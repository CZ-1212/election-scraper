#!/usr/bin/env python3
"""
WordPress Publisher — Bay City News Election Pipeline

Reads the normalized master JSON (data/processed/election_results_master.json),
builds a clean HTML results table, and pushes it to the BCN/LNM WordPress site
via the WordPress REST API.

THIS SCRIPT NEVER RUNS AUTOMATICALLY.
It only runs when explicitly triggered:
  - Locally:         python run_all.py --push-wp
  - GitHub Actions:  the "Publish to WordPress" workflow (workflow_dispatch only)

You can publish all counties or a specific subset:
  - Locally:         python run_all.py --push-wp --counties "Marin,Santa_Clara"
  - Via workflow:    Enter county names in the "counties" workflow input field.

Before posting, it prints the target URL and asks the editor to type YES to confirm.
Any other input aborts the publish immediately — nothing is posted.

In GitHub Actions (non-interactive), set WP_CI_CONFIRMED=YES in the environment
to skip the interactive prompt (the workflow already requires a human confirmation step).

All credentials come from .env. No credentials are ever hardcoded here.
"""

import difflib
import hashlib
import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Load .env before anything else so credentials are in os.environ.
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import requests
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# CONFIGURATION — all values from .env
# ---------------------------------------------------------------------------

def _get_env(key: str) -> str:
    """Read a required environment variable. Exit clearly if it's missing."""
    val = os.environ.get(key, "").strip()
    if not val:
        print(f"[wordpress] ERROR: {key} is not set in .env. Cannot publish.")
        sys.exit(1)
    return val


# ---------------------------------------------------------------------------
# HTML BUILDER
# Produces a clean, readable HTML results table for embedding on the website.
# No winner / leading / passing / failing labels — just the raw numbers.
# ---------------------------------------------------------------------------

def _build_html(master: dict, include_counties: list[str] | None = None) -> str:
    """
    Build an HTML string from the master JSON.

    include_counties — optional list of county names to include (e.g. ["Marin", "San_Mateo"]).
                       If None or empty, all counties with OK/WARN status are included.

    Returns a single HTML string ready to paste into WordPress.
    """
    counties = master.get("counties") or {}
    run_timestamp = master.get("pipeline_timestamp") or ""

    # Format the timestamp for display — e.g. "June 2, 2026, 8:45 PM"
    try:
        dt = datetime.fromisoformat(run_timestamp)
        display_time = dt.strftime("%B %-d, %Y, %-I:%M %p")
    except (ValueError, TypeError):
        display_time = run_timestamp

    # Build a normalized set of requested counties for fast lookup.
    # Normalize by lowercasing and stripping spaces/underscores so callers
    # can write "Contra Costa" or "Contra_Costa" and both match.
    if include_counties:
        requested = {c.lower().replace(" ", "_").replace("-", "_") for c in include_counties}
    else:
        requested = None  # None means "all"

    parts = []
    parts.append('<div class="election-results">')
    parts.append(f'<p class="results-updated">Results last updated: {display_time}</p>')

    included_count = 0

    for county_name, data in sorted(counties.items()):
        # Filter by requested county list if one was provided.
        if requested is not None:
            normalized_name = county_name.lower().replace(" ", "_")
            if normalized_name not in requested:
                continue

        # Skip counties that failed to scrape — don't show partial/empty data.
        if data.get("scrape_status") == "FAIL":
            print(f"[wordpress] Skipping {county_name}: scrape_status is FAIL")
            continue

        display_county = county_name.replace("_", " ")
        vt = data.get("voter_turnout") or {}
        contests = data.get("contests") or []
        last_updated = data.get("last_updated") or ""

        parts.append('<section class="county-results">')
        parts.append(f'<h2>{display_county} County</h2>')

        # Turnout strip — only show stats that have a value.
        turnout_items = []
        if vt.get("ballots_cast"):
            turnout_items.append(f"Ballots cast: {int(vt['ballots_cast']):,}")
        if vt.get("registered_voters"):
            turnout_items.append(f"Registered voters: {int(vt['registered_voters']):,}")
        if vt.get("turnout_percentage"):
            turnout_items.append(f"Turnout: {vt['turnout_percentage']}%")

        if turnout_items:
            parts.append('<p class="turnout-summary">' + " &nbsp;|&nbsp; ".join(turnout_items) + "</p>")

        if last_updated:
            parts.append(f'<p class="source-updated">County site last updated: {last_updated}</p>')

        # Contest tables — one table per contest, choices in scraped order.
        for contest in contests:
            title = contest.get("title", "")
            precincts = contest.get("precincts_reporting", "")
            choices = contest.get("choices") or []

            parts.append('<div class="contest">')
            parts.append(f'<h3>{title}</h3>')
            if precincts:
                parts.append(f'<p class="precincts">{precincts}</p>')

            if choices:
                parts.append('<table class="results-table">')
                parts.append('<thead><tr><th>Choice</th><th>Votes</th><th>Percent</th></tr></thead>')
                parts.append('<tbody>')
                for choice in choices:
                    name = choice.get("name", "")
                    votes = choice.get("votes", "")
                    pct = choice.get("pct", "")
                    votes_fmt = f"{int(votes):,}" if votes != "" else ""
                    pct_fmt = f"{float(pct):.2f}%" if pct != "" else ""
                    parts.append(f'<tr><td>{name}</td><td>{votes_fmt}</td><td>{pct_fmt}</td></tr>')
                parts.append('</tbody></table>')

            parts.append('</div>')  # .contest

        parts.append('</section>')  # .county-results
        included_count += 1

    parts.append('</div>')  # .election-results

    print(f"[wordpress] HTML built: {included_count} counties included.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# WORDPRESS REST API
# Updates the page identified by WP_PAGE_ID with the new HTML content.
# Uses HTTP Basic Auth with an Application Password (not your login password).
# ---------------------------------------------------------------------------

def _push_to_wp(site_url: str, username: str, app_password: str, page_id: str, html: str) -> None:
    """
    Send the HTML to WordPress via the REST API.
    Updates the existing page at WP_PAGE_ID.
    Raises an exception (with the full error message) if the API call fails.
    """
    api_url = f"{site_url.rstrip('/')}/wp-json/wp/v2/pages/{page_id}"

    # WordPress REST API uses HTTP Basic Auth with an Application Password.
    # Application Passwords are created in WP Admin → Users → Profile → Application Passwords.
    auth = (username, app_password)

    payload = {
        "content": html,
        "status": "publish",  # keep the page published (not draft)
    }

    response = requests.post(api_url, json=payload, auth=auth, timeout=30)

    if response.status_code in (200, 201):
        print(f"[wordpress] Page updated successfully (HTTP {response.status_code}).")
    else:
        # Include the full API response body so the error is actionable.
        raise RuntimeError(
            f"WordPress API returned HTTP {response.status_code}.\n"
            f"URL: {api_url}\n"
            f"Response body: {response.text[:1000]}"
        )


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

def publish_to_wordpress(master_json_path: Path, include_counties: list[str] | None = None) -> None:
    """
    Build the HTML from master JSON and push to WordPress.

    include_counties — optional list of county names to publish.
                       If None, all counties with OK/WARN status are included.

    Always asks for a typed YES confirmation before posting anything,
    unless WP_CI_CONFIRMED=YES is set in the environment (GitHub Actions only).

    Called by run_all.py as Step 4 of the pipeline (only when --push-wp is set).
    """
    master_json_path = Path(master_json_path)

    # Read credentials from environment (.env file).
    site_url     = _get_env("WP_SITE_URL")
    username     = _get_env("WP_USERNAME")
    app_password = _get_env("WP_APP_PASSWORD")
    page_id      = _get_env("WP_PAGE_ID")

    # Load the normalized data.
    with open(master_json_path, encoding="utf-8") as f:
        master = json.load(f)

    counties = master.get("counties") or {}
    all_ok = [c for c, d in counties.items() if d.get("scrape_status") != "FAIL"]

    # Determine which counties will actually be published.
    if include_counties:
        publishing = [c for c in include_counties if c in all_ok]
        skipped = [c for c in include_counties if c not in all_ok]
        if skipped:
            print(f"[wordpress] NOTE: These requested counties will be skipped (FAIL status or not found): {skipped}")
    else:
        publishing = all_ok

    print()
    print("=" * 60)
    print("WORDPRESS PUBLISH — CONFIRMATION REQUIRED")
    print("=" * 60)
    print(f"  Target site:       {site_url}")
    print(f"  Page ID:           {page_id}")
    print(f"  Counties to post:  {len(publishing)} of {len(counties)}")
    if include_counties:
        print(f"  Selected:          {', '.join(include_counties)}")
    print()
    print("  Have you reviewed the Google Sheet STATUS DASHBOARD?")
    print("  Make sure all selected counties show OK or acceptable WARN status.")
    print()

    # Check for CI confirmation environment variable (set by GitHub Actions publish workflow).
    # This skips the interactive prompt when running non-interactively.
    ci_confirmed = os.environ.get("WP_CI_CONFIRMED", "").strip().upper()

    if ci_confirmed == "YES":
        print("[wordpress] CI confirmation received via WP_CI_CONFIRMED=YES. Skipping interactive prompt.")
    else:
        # The editor must type YES exactly (case-insensitive) to proceed.
        # Anything else — including an accidental Enter — aborts the publish.
        try:
            answer = input('  Type YES to confirm and publish, or anything else to abort: ').strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[wordpress] No input received. Aborting.")
            return

        if answer.upper() != "YES":
            print(f"[wordpress] Publish aborted (you typed: '{answer}'). Nothing was posted.")
            return

    print("[wordpress] Confirmation received. Building HTML...")
    html = _build_html(master, include_counties=include_counties)

    print(f"[wordpress] Posting to {site_url} (page ID: {page_id})...")
    try:
        _push_to_wp(site_url, username, app_password, page_id, html)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[wordpress] Publish complete at {timestamp}.")

        # Log the push to the scraper log file so there's a paper trail.
        log_file = Path(__file__).resolve().parent.parent / "logs" / "scraper.log"
        log_file.parent.mkdir(exist_ok=True)
        county_list = ", ".join(publishing) if publishing else "all"
        with open(log_file, "a", encoding="utf-8") as lf:
            lf.write(
                f"{timestamp}  INFO     WordPress publish: pushed to {site_url} "
                f"(page {page_id}) — counties: {county_list}\n"
            )

    except Exception as e:
        # Print the full error so editors know exactly what went wrong.
        print(f"[wordpress] ERROR: Publish failed.")
        print(f"[wordpress] Details: {e}")
        raise


# ---------------------------------------------------------------------------
# PREVIEW MODE — build and print HTML without posting anything
# ---------------------------------------------------------------------------

def preview_html(master_json_path: Path, include_counties: list[str] | None = None) -> None:
    """
    Build the WordPress HTML from the master JSON and print it to the terminal.
    Posts absolutely nothing. Safe to run at any time.

    Use this to verify the HTML looks correct before doing a real publish.
    """
    master_json_path = Path(master_json_path)

    with open(master_json_path, encoding="utf-8") as f:
        master = json.load(f)

    counties = master.get("counties") or {}
    site_url = os.environ.get("WP_SITE_URL", "(WP_SITE_URL not set)")
    page_id = os.environ.get("WP_PAGE_ID", "(WP_PAGE_ID not set)")

    print()
    print("=" * 60)
    print("WORDPRESS HTML PREVIEW — nothing will be posted")
    print("=" * 60)
    print(f"  Would post to: {site_url}  (page ID: {page_id})")

    if include_counties:
        print(f"  Counties selected: {', '.join(include_counties)}")
    else:
        ok = [c for c, d in counties.items() if d.get("scrape_status") != "FAIL"]
        print(f"  Counties included: {len(ok)} of {len(counties)} (all OK/WARN)")

    print("=" * 60)

    html = _build_html(master, include_counties=include_counties)

    print()
    print("--- HTML OUTPUT START ---")
    print(html)
    print("--- HTML OUTPUT END ---")
    print()
    print(f"Total HTML length: {len(html):,} characters")
    print("Preview complete. Nothing was posted.")


# ---------------------------------------------------------------------------
# RESULTS INJECTION
# Fetches a WordPress ballot-preview page, injects live vote tallies into
# matching .race-box elements, and pushes the updated HTML back.
# Only pushes when vote tallies have actually changed since the last push.
# ---------------------------------------------------------------------------

_INJECT_STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "processed" / "wp_inject_state.json"


def _contests_hash(county_data: dict) -> str:
    """16-char hex hash of all vote tallies for a county. Changes only when votes change."""
    contests = county_data.get("contests") or []
    snapshot = [
        (c.get("title"), [(ch.get("name"), ch.get("votes"), ch.get("pct"))
                          for ch in (c.get("choices") or [])])
        for c in contests
    ]
    return hashlib.sha256(json.dumps(snapshot).encode()).hexdigest()[:16]


def _load_inject_state() -> dict:
    if _INJECT_STATE_FILE.exists():
        with open(_INJECT_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_inject_state(state: dict) -> None:
    _INJECT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_INJECT_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

# Results CSS extends the existing .preview-candidate rows in place.
# Only the vote bar / pct / count lines are new — everything else inherits
# the widget styles already on the page.
_RESULTS_CSS = """\
<style>
.lnm-ballot-widget .results-candidate-list { padding: 4px 0; }
.lnm-ballot-widget .results-vote-wrap { margin-top: 5px; }
.lnm-ballot-widget .results-bar-wrap { background: #e8e8e8; border-radius: 3px; height: 8px; overflow: hidden; margin-bottom: 4px; }
.lnm-ballot-widget .results-bar { height: 100%; border-radius: 3px; }
.lnm-ballot-widget .results-pct { font-weight: 700; font-size: 0.82em; color: #1a3668; margin-right: 6px; }
.lnm-ballot-widget .results-votes { font-size: 0.78em; color: #888; }
.lnm-ballot-widget .results-precincts { font-size: 0.75em; color: #999; padding: 4px 14px; text-align: right; font-style: italic; }
</style>"""


def _div_end(s: str, open_pos: int) -> int:
    """Return the index just after the closing </div> for the <div> at open_pos."""
    depth = 0
    i = open_pos
    n = len(s)
    while i < n:
        lt = s.find("<", i)
        if lt < 0:
            break
        i = lt
        chunk = s[i : i + 20].lower()
        if chunk.startswith("<div") and (len(chunk) < 5 or chunk[4] in " \t\n\r/>"):
            depth += 1
            i += 4
        elif chunk.startswith("</div>"):
            depth -= 1
            if depth == 0:
                return i + 6
            i += 6
        else:
            i += 1
    return -1


def _replace_candidate_list(raw: str, race_title: str, new_div: str) -> tuple[str, bool]:
    """
    Find the race-box whose race-title matches race_title and replace its
    preview-candidate-list (or results-candidate-list) div with new_div.
    Operates entirely on the raw HTML string — no BeautifulSoup serialisation,
    so WordPress block markers, attribute order, and entity encoding are
    preserved byte-for-byte.
    Returns (modified_html, success).
    """
    # Locate the race-title div text in the raw HTML (HTML-escaped).
    title_escaped = html.escape(race_title, quote=False)
    title_pat = re.compile(
        r'class=["\']race-title["\'][^>]*>\s*' + re.escape(title_escaped) + r'\s*<',
        re.IGNORECASE,
    )
    m = title_pat.search(raw)
    if not m:
        return raw, False

    # Find the race-box opening <div> that contains this title.
    rb_pos = raw.rfind('class="race-box"', 0, m.start())
    if rb_pos < 0:
        return raw, False
    box_start = raw.rfind("<div", 0, rb_pos + 1)
    if box_start < 0:
        return raw, False

    # Within this race-box, find the candidate-list div.
    box_section = raw[box_start:]
    lst = re.search(
        r'<div\s+class="(?:preview-candidate-list|results-candidate-list)"',
        box_section,
    )
    if not lst:
        return raw, False

    list_start = box_start + lst.start()
    list_end = _div_end(raw, list_start)
    if list_end < 0:
        return raw, False

    return raw[:list_start] + new_div + raw[list_end:], True


def _norm_contest(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    s = re.sub(r"[,\-/]", " ", s.strip().lower())
    return re.sub(r"\s+", " ", s).strip()


def _best_contest_match(race_title: str, contest_lookup: dict) -> dict | None:
    """
    Return the contest dict whose title best matches race_title, or None.

    Exact match is tried first (after normalising both sides).  Falls back to
    difflib fuzzy matching with a 0.60 similarity cutoff so that mismatches
    like "County Assessor" → "ASSESSOR" or
    "City of Richmond, Council District 3" →
    "CITY OF RICHMOND  MEMBER  CITY COUNCIL  DISTRICT 3" still resolve.
    """
    norm = _norm_contest(race_title)
    # Build a lookup keyed by the same normalisation applied to contest titles.
    clean_lookup = {_norm_contest(k): v for k, v in contest_lookup.items()}

    if norm in clean_lookup:
        return clean_lookup[norm]

    matches = difflib.get_close_matches(norm, clean_lookup.keys(), n=1, cutoff=0.60)
    if matches:
        return clean_lookup[matches[0]]

    return None


def _vote_bar_html(pct, votes) -> str:
    """Inline vote bar fragment inserted inside .preview-candidate-info."""
    votes_fmt = f"{int(votes):,} votes" if votes is not None and votes != "" else "— votes"
    pct_fmt = f"{float(pct):.1f}%" if pct is not None and pct != "" else "—"
    bar_width = f"{float(pct):.1f}%" if pct is not None and pct != "" else "0%"
    return (
        '<div class="results-vote-wrap">'
        '<div class="results-bar-wrap">'
        f'<div class="results-bar" style="width:{bar_width};background:#1a3668"></div>'
        "</div>"
        f'<span class="results-pct">{pct_fmt}</span>'
        f'<span class="results-votes">{votes_fmt}</span>'
        "</div>"
    )


def _build_results_div(contest: dict, existing_candidate_list=None) -> str:
    """
    Build a .results-candidate-list div in vote order.

    existing_candidate_list — a BeautifulSoup tag for the current
        .preview-candidate-list or .results-candidate-list.  When provided,
        existing .preview-candidate rows (with party badges and names) are
        matched by candidate name and augmented with vote bars, preserving
        the widget's visual style.  Falls back to plain rows when no match.
    """
    choices = contest.get("choices") or []
    precincts = contest.get("precincts_reporting", "")

    # Build name → existing row lookup when we have prior HTML to work from.
    name_to_row: dict = {}
    if existing_candidate_list is not None:
        for row in existing_candidate_list.find_all(class_="preview-candidate"):
            name_tag = row.find(class_="preview-candidate-name")
            if name_tag:
                key = _norm_contest(name_tag.get_text(strip=True))
                name_to_row[key] = row

    rows = []
    for choice in choices:
        name = choice.get("name") or ""
        votes = choice.get("votes")
        pct = choice.get("pct")
        bar = _vote_bar_html(pct, votes)

        norm_name = _norm_contest(name)
        existing_row = name_to_row.get(norm_name)

        # Fuzzy fallback for minor name differences (e.g. "VINCE ROBB" vs "Vince Robb").
        if existing_row is None:
            for key, row in name_to_row.items():
                if norm_name in key or key in norm_name:
                    existing_row = row
                    break

        if existing_row is not None:
            # Clone row, strip any previous vote-wrap, add fresh one.
            row_copy = BeautifulSoup(str(existing_row), "html.parser")
            for old in row_copy.find_all(class_="results-vote-wrap"):
                old.decompose()
            for old in row_copy.find_all(class_="preview-candidate-designation"):
                old.decompose()
            info = row_copy.find(class_="preview-candidate-info")
            if info:
                info.append(BeautifulSoup(bar, "html.parser"))
            rows.append(str(row_copy))
        else:
            # No matching existing row — build a plain one.
            rows.append(
                '<div class="preview-candidate">'
                '<div class="preview-candidate-info">'
                f'<div class="preview-candidate-name">{html.escape(name)}</div>'
                + bar
                + "</div></div>"
            )

    precincts_html = (
        f'<div class="results-precincts">{html.escape(str(precincts))}</div>'
        if precincts else ""
    )

    return (
        '<div class="results-candidate-list">'
        + precincts_html
        + "".join(rows)
        + "</div>"
    )


def inject_results_into_page(
    county_key: str,
    master_json: dict,
    wp_page_id: int | str,
    source_html_path: "str | Path | None" = None,
    force: bool = False,
) -> None:
    """
    Fetch the live WordPress ballot-preview page for county_key, inject vote
    tallies from master_json into every matching .race-box, then push the
    updated HTML back to WordPress.

    Skips the push if vote tallies haven't changed since the last successful
    push (detected via a hash stored in data/processed/wp_inject_state.json).
    Pass force=True to push regardless.

    Race boxes with no matching contest in master_json are left untouched.
    CSS for the new result classes is prepended once on the first injection.

    source_html_path — optional path to a local ballot-preview HTML file.
        Used when the WP page is empty (e.g. first run / test pages). If the
        WP page already has content, this parameter is ignored.
    """
    # -- 0. Skip early if tallies haven't changed since the last push. --
    county_norm = county_key.lower().replace(" ", "_").replace("-", "_")
    county_data = next(
        (v for k, v in master_json.get("counties", {}).items()
         if k.lower().replace(" ", "_") == county_norm),
        None,
    )
    if county_data is None:
        print(f"[inject] County '{county_key}' not found in master JSON. Aborting.")
        return

    current_hash = _contests_hash(county_data)
    state = _load_inject_state()
    if not force and state.get(county_norm, {}).get("last_hash") == current_hash:
        print(f"[inject] {county_key}: no vote changes since last push — skipping.")
        return "skipped"

    site_url     = _get_env("WP_SITE_URL")
    username     = _get_env("WP_USERNAME")
    app_password = _get_env("WP_APP_PASSWORD")
    auth = (username, app_password)

    # 1. Fetch the page's stored content (context=edit returns the raw HTML we pushed).
    api_url = f"{site_url.rstrip('/')}/wp-json/wp/v2/pages/{wp_page_id}"
    print(f"[inject] Fetching page {wp_page_id} from {site_url}...")
    resp = requests.get(api_url, params={"context": "edit"}, auth=auth, timeout=30)
    resp.raise_for_status()
    raw_html = resp.json().get("content", {}).get("raw") or ""

    if not raw_html:
        if source_html_path:
            print(f"[inject] Page {wp_page_id} is empty — loading source HTML from {source_html_path}")
            raw_html = Path(source_html_path).read_text(encoding="utf-8")
        else:
            print(
                f"[inject] Page {wp_page_id} is empty and no source_html_path was provided.\n"
                f"[inject] Publish the ballot preview to this page first, or pass source_html_path."
            )
            return

    # 2. Build contest lookup (county_data already resolved in step 0).
    contests = county_data.get("contests") or []
    if not contests:
        print(f"[inject] No contests in master JSON for '{county_key}'. Nothing to inject.")
        return

    contest_lookup = {c["title"]: c for c in contests}
    print(f"[inject] {len(contest_lookup)} contests available for matching.")

    # 3. Parse the WP page (read-only) to find race boxes and badge data.
    #    All writes go back to raw_html via _replace_candidate_list so that
    #    BeautifulSoup's HTML normaliser never touches the rest of the page.
    soup = BeautifulSoup(raw_html, "html.parser")

    # Build party-badge lookup from the ballot-preview source file when provided.
    preview_race_boxes: dict = {}
    if source_html_path:
        preview_soup = BeautifulSoup(
            Path(source_html_path).read_text(encoding="utf-8"), "html.parser"
        )
        for rb in preview_soup.find_all(class_="race-box"):
            t = rb.find(class_="race-title")
            if t:
                preview_race_boxes[_norm_contest(t.get_text(strip=True))] = rb

    updated_html = raw_html  # will be modified in-place via string surgery
    replaced = 0

    for race_box in soup.find_all(class_="race-box"):
        title_tag = race_box.find(class_="race-title")
        if not title_tag:
            continue
        title_text = title_tag.get_text(strip=True)
        contest = _best_contest_match(title_text, contest_lookup)
        if contest is None:
            continue

        candidate_list = (
            race_box.find(class_="preview-candidate-list")
            or race_box.find(class_="results-candidate-list")
        )
        if candidate_list is None:
            continue

        # Prefer the source-file's preview rows for badge data; fall back to
        # whatever the WP page currently has.
        has_badges = bool(candidate_list.find(class_="candidate-party"))
        badge_source = candidate_list
        if not has_badges and preview_race_boxes:
            preview_rb = preview_race_boxes.get(_norm_contest(title_text))
            if preview_rb is not None:
                badge_source = (
                    preview_rb.find(class_="preview-candidate-list")
                    or candidate_list
                )

        new_div = _build_results_div(contest, existing_candidate_list=badge_source)
        updated_html, ok = _replace_candidate_list(updated_html, title_text, new_div)
        if ok:
            replaced += 1
            print(f"[inject]   Injected: {title_text}")

    if replaced == 0:
        print("[inject] No race boxes matched any contest. Nothing updated.")
        return

    # 4. Update banner sub-line — raw string replacement, no BS serialisation.
    pipeline_ts = master_json.get("pipeline_timestamp") or ""
    try:
        dt = datetime.fromisoformat(pipeline_ts)
        display_time = dt.strftime("%B %-d, %Y, %-I:%M %p")
    except (ValueError, TypeError):
        display_time = pipeline_ts or "unknown"

    updated_html = re.sub(
        r'(<div[^>]*class="preview-banner-sub"[^>]*>)[^<]*(</div>)',
        rf'\g<1>Results last updated: {re.escape(display_time)}\g<2>',
        updated_html,
    )

    # 5. Prepend results CSS once (sentinel guards against double-injection).
    sentinel = ".lnm-ballot-widget .results-candidate-list"
    if sentinel not in updated_html:
        updated_html = re.sub(
            r"<style>\s*\.results-candidate-list.*?</style>\s*",
            "",
            updated_html,
            flags=re.DOTALL,
        )
        updated_html = _RESULTS_CSS + "\n" + updated_html

    # 6. Push the updated HTML back to WordPress.
    print(f"[inject] Pushing {replaced} updated race boxes to page {wp_page_id}...")
    push_resp = requests.post(
        api_url,
        json={"content": updated_html, "status": "publish"},
        auth=auth,
        timeout=30,
    )
    if push_resp.status_code in (200, 201):
        print(f"[inject] Page {wp_page_id} updated — {replaced} races injected.")
        state[county_norm] = {
            "last_hash": current_hash,
            "last_inject": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "wp_page_id": str(wp_page_id),
        }
        _save_inject_state(state)
        return "pushed"
    else:
        raise RuntimeError(
            f"WordPress API returned HTTP {push_resp.status_code}.\n"
            f"URL: {api_url}\n"
            f"Response body: {push_resp.text[:1000]}"
        )


# ---------------------------------------------------------------------------
# INJECT ALL COUNTIES
# Reads WP_PAGE_IDS from .env (a JSON dict mapping county_key → page_id) and
# calls inject_results_into_page for every county. Skips counties whose tallies
# haven't changed since the last successful push.
# ---------------------------------------------------------------------------

# Some .env files use short keys (e.g. "sf") that don't match the master JSON
# county names.  Map any known aliases to their canonical master-JSON key.
_COUNTY_KEY_ALIASES = {
    "sf": "san_francisco",
}


def inject_all_counties(master_json_path: Path, force: bool = False) -> None:
    """
    Inject live vote tallies into every county ballot-preview page listed in
    WP_PAGE_IDS.  Skips any county whose tallies haven't changed since the
    last push (unless force=True).
    """
    page_ids_raw = _get_env("WP_PAGE_IDS")
    try:
        page_ids: dict = json.loads(page_ids_raw)
    except json.JSONDecodeError:
        print("[inject_all] ERROR: WP_PAGE_IDS is not valid JSON. Check your .env.")
        sys.exit(1)

    master_json_path = Path(master_json_path)
    with open(master_json_path, encoding="utf-8") as f:
        master = json.load(f)

    pushed, skipped, no_match, errors = [], [], [], []
    for raw_key, page_id in page_ids.items():
        county_key = _COUNTY_KEY_ALIASES.get(raw_key, raw_key)
        print(f"\n[inject_all] ── {county_key} (page {page_id}) ──")
        try:
            result = inject_results_into_page(county_key, master, int(page_id), force=force)
            if result == "pushed":
                pushed.append(county_key)
            elif result == "skipped":
                skipped.append(county_key)
            else:
                no_match.append(county_key)
        except Exception as exc:
            print(f"[inject_all] ERROR — {county_key}: {exc}")
            errors.append(f"{county_key}: {exc}")

    print("\n" + "=" * 50)
    print(f"INJECT SUMMARY  ({len(page_ids)} counties)")
    print(f"  Pushed       : {len(pushed)}   {pushed}")
    print(f"  Skipped      : {len(skipped)}   (votes unchanged since last push)")
    print(f"  No match     : {len(no_match)}   (page empty or race titles don't match)")
    print(f"  Errors       : {len(errors)}")
    for e in errors:
        print(f"    {e}")
    print("=" * 50)

    if errors:
        sys.exit(1)


# ---------------------------------------------------------------------------
# COMMAND-LINE USAGE (for running to_wordpress.py directly)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    _default_master = str(
        Path(__file__).resolve().parent.parent / "data" / "processed" / "election_results_master.json"
    )

    parser = argparse.ArgumentParser(description="Publish election results to WordPress.")
    parser.add_argument("--master-json", default=_default_master,
                        help="Path to election_results_master.json")
    parser.add_argument("--counties", default=None,
                        help="Comma-separated county names (publish/inject only). "
                             "Omit for all counties.")
    parser.add_argument("--preview", action="store_true",
                        help="Print the HTML that would be posted — posts nothing.")
    parser.add_argument("--inject-wp", action="store_true",
                        help="Inject live tallies into county ballot-preview pages. "
                             "Skips pages whose votes haven't changed since the last push.")
    parser.add_argument("--force", action="store_true",
                        help="Force injection even when tallies haven't changed.")
    args = parser.parse_args()

    county_list = [c.strip() for c in args.counties.split(",")] if args.counties else None

    if args.inject_wp:
        inject_all_counties(master_json_path=Path(args.master_json), force=args.force)
    elif args.preview:
        preview_html(master_json_path=Path(args.master_json), include_counties=county_list)
    else:
        publish_to_wordpress(master_json_path=Path(args.master_json), include_counties=county_list)
