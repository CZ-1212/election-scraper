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

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Load .env before anything else so credentials are in os.environ.
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import requests


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
# COMMAND-LINE USAGE (for running to_wordpress.py directly)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Publish election results to WordPress.")
    parser.add_argument(
        "--master-json",
        default=str(Path(__file__).resolve().parent.parent / "data" / "processed" / "election_results_master.json"),
        help="Path to election_results_master.json",
    )
    parser.add_argument(
        "--counties",
        default=None,
        help="Comma-separated list of county names to publish (e.g. 'Marin,Santa_Clara'). "
             "If omitted, all counties with OK/WARN status are published.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Print the HTML that would be posted to WordPress — posts nothing.",
    )
    args = parser.parse_args()

    county_list = [c.strip() for c in args.counties.split(",")] if args.counties else None

    if args.preview:
        preview_html(master_json_path=Path(args.master_json), include_counties=county_list)
    else:
        publish_to_wordpress(master_json_path=Path(args.master_json), include_counties=county_list)
