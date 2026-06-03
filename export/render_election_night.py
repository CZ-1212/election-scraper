#!/usr/bin/env python3
"""
Election Night HTML Renderer — Bay City News pipeline.

Takes the scraped + normalized master JSON (live vote tallies) and merges it with
the STATIC local-race reference data, then renders one WordPress-ready HTML block
per county using the ELECTION NIGHT template (turnout bar + candidate grid +
ballot-measure table).

Pipeline for each county:
    scrape local tallies (master JSON)
      -> filter to LOCAL races only (whitelist = local_races CSV)
      -> join static context (race definitions, candidate professions, measure descriptions)
      -> render into template/election_results_template.html styling

Scope rules (see CLAUDE.md):
  * LOCAL races only. State/federal races (Governor, US Rep, State Assembly, etc.)
    are out of scope and are dropped — they go to AP wire, not this widget.
  * The local-race list (election_data/local_races - Sheet1.csv) defines WHAT
    renders. The scrape supplies ONLY the live vote numbers. Professions and
    measure descriptions are static context.
  * Interprets nothing — no winner/leading/pass/fail. Rows render in the order the
    county posted them (vote order, as already sorted in the master JSON).

Outputs:
  * data/processed/election_night_by_county.csv — one row per county with the full
    generated HTML in the `html` column, plus a merge-report column.
  * output/election_night/<County>.html — same HTML as a standalone file per county.

This module ONLY uses the election night template. It never touches the ballot
preview template.
"""

import csv
import html as _html
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_PACIFIC = ZoneInfo("America/Los_Angeles")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = PROJECT_ROOT / "template" / "election_results_template.html"
LOCAL_RACES_CSV = PROJECT_ROOT / "election_data" / "local_races - Sheet1.csv"
MASTER_JSON = PROJECT_ROOT / "data" / "processed" / "election_results_master.json"
COUNTY_LINKS_CSV = PROJECT_ROOT / "election_data" / "county_links.csv"

# Reuse the ballot-preview uncontested helpers so the same CSV drives both surfaces.
import sys
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from june2_utils import load_uncontested_races, render_uncontested_dropdown, UNCONTESTED_CSS  # noqa: E402

# Primary deliverable: two columns only — county name + the entire HTML code.
OUT_CSV = PROJECT_ROOT / "data" / "processed" / "election_night_by_county.csv"
# Separate diagnostic report (rendered/unmatched counts), kept out of the main CSV.
OUT_REPORT_CSV = PROJECT_ROOT / "data" / "processed" / "election_night_merge_report.csv"
OUT_HTML_DIR = PROJECT_ROOT / "output" / "election_night"

# Canonical 13 counties (underscore form as used in the master JSON).
KNOWN_COUNTIES = [
    "Alameda", "Contra_Costa", "Marin", "Mendocino", "Monterey", "Napa",
    "San_Francisco", "San_Joaquin", "San_Mateo", "Santa_Clara", "Santa_Cruz",
    "Solano", "Sonoma",
]


# ---------------------------------------------------------------------------
# NAME NORMALIZATION + MATCHING
# The scraped contest titles and the static race names rarely match byte-for-byte
# (case, punctuation, "(Vote for 1)" tails, "- Majority vote required" suffixes,
# "County X" vs "X").  We match defensively so a single naming difference never
# silently drops a local race, and we report every local race that fails to match.
# ---------------------------------------------------------------------------

# Tokens that carry no identifying weight when comparing candidate-race names.
# 'county' is noise ('County Assessor' == 'ASSESSOR'); 'state' is deliberately
# KEPT so state races stay distinct from same-named local races.
_STOPWORDS = {
    "of", "the", "for", "and", "a", "an", "to", "member", "vote", "votes",
    "required", "no", "office", "county",
}

# Trailing qualifier phrases the county appends to measure/contest titles.
_QUALIFIER_TAIL = re.compile(
    r"\s*[-–]\s*(majority|2\s*/\s*3.*|55%.*|2/3rds.*|.*vote required).*$",
    re.I,
)


def _strip_qualifiers(title: str) -> str:
    """Remove '(Vote for 1)' and '- Majority vote required' style tails."""
    t = re.sub(r"\(.*?\)", " ", title)          # (Vote for 1)
    t = _QUALIFIER_TAIL.sub(" ", t)             # - Majority vote required
    return t


def _norm_tokens(title: str) -> list[str]:
    """Lowercase, strip punctuation + qualifiers, drop stopwords → token list."""
    t = _strip_qualifiers(title).lower()
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return [tok for tok in t.split() if tok not in _STOPWORDS]


def _numbers(title: str) -> set[str]:
    """District / office / seat numbers in a title (used as a hard guard)."""
    return set(re.findall(r"\d+", _strip_qualifiers(title)))


def _measure_letter(title: str) -> str | None:
    """
    Identify a ballot measure by its letter (or proposition number), which is the
    only stable key across the scrape and the static CSV within a single county.
    'MEASURE K - OAKLEY...'  -> 'k'
    'Bond Measure B - ...'   -> 'b'
    'Proposition 50'         -> 'prop50'
    """
    low = title.lower()
    m = re.search(r"\b(?:bond\s+)?measure\s+([a-z]{1,2})\b", low)
    if m:
        return m.group(1)
    m = re.search(r"\bproposition\s+(\d+)\b", low)
    if m:
        return "prop" + m.group(1)
    return None


def _looks_like_measure(title: str) -> bool:
    return bool(re.search(r"\b(measure|proposition|prop|bond)\b", title, re.I))


def _candidate_match_score(a: str, b: str) -> float:
    """
    Similarity in [0,1] between two candidate-race titles.

    Returns 0 when both titles carry district/office numbers that disagree — that
    is a hard mismatch (District 3 must never match District 4).  Otherwise it is
    the token overlap divided by the smaller token set, so 'County Assessor' vs
    'ASSESSOR' scores 1.0 while still rewarding fuller overlaps.
    """
    na, nb = _numbers(a), _numbers(b)
    if na and nb and na.isdisjoint(nb):
        return 0.0

    ta, tb = set(_norm_tokens(a)), set(_norm_tokens(b))
    if not ta or not tb:
        return 0.0
    # Jaccard (intersection / union), not overlap/min: a short scraped title that
    # is merely a subset of the local name (e.g. 'CONTROLLER' inside 'County
    # Auditor-Controller') must NOT score a perfect match.
    score = len(ta & tb) / len(ta | tb)

    # Containment bonus: if the LOCAL race name (a) is fully contained in the
    # scraped title (b) — e.g. 'District Attorney' ⊂ 'District Attorney, Short
    # Term' — that's a strong signal and worth lifting above the threshold,
    # without symmetrically rewarding short scraped titles that are subsets of
    # longer local names.
    if ta and ta.issubset(tb):
        score = max(score, 0.75)

    # Require the distinguishing number to be present on both sides when either
    # side has one, so 'Council District 3' can't match a numberless 'Council'.
    if (na or nb) and not (na & nb):
        score *= 0.5
    return score


# ---------------------------------------------------------------------------
# STATIC DATA — local race definitions, professions, measure descriptions
# ---------------------------------------------------------------------------

def load_local_races(csv_path: Path = LOCAL_RACES_CSV) -> dict[str, list[dict]]:
    """
    Load the local-race roster into per-county race definitions.

    Returns {county_display_name: [race_def, ...]} where each race_def is:
        {
          "name": str,                  # static race/measure name
          "is_measure": bool,
          "measure_letter": str|None,   # join key for measures
          "description": str,           # measures only
          "jurisdiction": str,          # measures only
          "candidates": {clean_name: {"profession": str, "party": str}},
        }
    """
    races: dict[str, dict[str, dict]] = defaultdict(dict)

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("Race/Measure name") or "").strip()
            who = (row.get("Candidate Name/Measure Juristiction") or "").strip()
            desc = (row.get("Profession/Description") or "").strip()
            party = (row.get("Party") or "").strip()
            county = (row.get("County") or "").replace(" County", "").strip()
            if not name or not county:
                continue

            bucket = races[county]
            if name not in bucket:
                is_measure = _looks_like_measure(name)
                bucket[name] = {
                    "name": name,
                    "is_measure": is_measure,
                    "measure_letter": _measure_letter(name) if is_measure else None,
                    "description": "",
                    "jurisdiction": "",
                    "candidates": {},
                }
            rd = bucket[name]

            if rd["is_measure"]:
                # For a measure row, 'who' is the jurisdiction and 'desc' the text.
                if desc and not rd["description"]:
                    rd["description"] = desc
                if who and not rd["jurisdiction"]:
                    rd["jurisdiction"] = who
            elif who:
                rd["candidates"][who.strip().lower()] = {
                    "profession": desc,
                    "party": party,
                }

    return {county: list(bucket.values()) for county, bucket in races.items()}


# ---------------------------------------------------------------------------
# MERGE — match each LOCAL race to a scraped contest
# ---------------------------------------------------------------------------

def match_local_races(local_defs: list[dict], scraped_contests: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    For each LOCAL race definition, find its scraped contest (live tallies).

    Returns (merged, unmatched):
      merged    — [{"def": race_def, "contest": scraped_contest_or_None,
                    "match_score": float, "matched_title": str}]  (one per local race)
      unmatched — the subset of merged where no scraped contest was found.

    The local-race list drives WHAT we render; a local race with no scraped match
    still renders (as "results not yet available") and is reported, never dropped.
    """
    # Index scraped contests by measure letter for O(1) measure matching.
    scraped_by_letter: dict[str, dict] = {}
    for c in scraped_contests:
        letter = _measure_letter(c.get("title", ""))
        if letter and letter not in scraped_by_letter:
            scraped_by_letter[letter] = c

    used_ids: set[int] = set()
    merged: list[dict] = []

    for rd in local_defs:
        best, best_score, best_title = None, 0.0, ""

        if rd["is_measure"] and rd["measure_letter"]:
            cand = scraped_by_letter.get(rd["measure_letter"])
            if cand is not None and id(cand) not in used_ids:
                best, best_score, best_title = cand, 1.0, cand.get("title", "")
        else:
            for c in scraped_contests:
                if id(c) in used_ids or _looks_like_measure(c.get("title", "")):
                    continue
                score = _candidate_match_score(rd["name"], c.get("title", ""))
                if score > best_score:
                    best, best_score, best_title = c, score, c.get("title", "")

        # Accept candidate matches at/above threshold; measures already gated by letter.
        matched = best if (rd["is_measure"] and best) or best_score >= 0.6 else None
        if matched is not None:
            used_ids.add(id(matched))

        merged.append({
            "def": rd,
            "contest": matched,
            "match_score": round(best_score, 3) if matched else 0.0,
            "matched_title": best_title if matched else "",
        })

    unmatched = [row for row in merged if row["contest"] is None]
    return merged, unmatched


# ---------------------------------------------------------------------------
# RENDERING — election night template
# ---------------------------------------------------------------------------

def _esc(s) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def _fmt_int(n) -> str:
    try:
        return f"{int(n):,}"
    except (ValueError, TypeError):
        return "0"


def _party_class_and_label(party: str) -> tuple[str, str, str]:
    """Map a party string to (css_suffix, circle_initial, tooltip_label)."""
    p = (party or "").strip().lower()
    if p.startswith("dem"):
        return "d", "D", "Democrat"
    if p.startswith("rep"):
        return "r", "R", "Republican"
    if "no party" in p or p in {"npp", "decline"}:
        return "n", "N", "No Party Preference"
    if p.startswith("ind") or "independent" in p:
        return "i", "I", "Independent"
    if p:  # any other named party (Green, Libertarian, Non Partisan, etc.)
        if "non" in p:
            return "n", "N", "Non-Partisan"
        return "i", p[:1].upper(), party.strip()
    return "n", "N", "Non-Partisan"


def _extract_template_css() -> str:
    """Pull the <style>…</style> block verbatim from the election night template."""
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    m = re.search(r"<style>(.*?)</style>", text, re.S)
    return m.group(1) if m else ""


# Cache the CSS once so the same styling is reused for every county.
_TEMPLATE_CSS = _extract_template_css()


def _render_turnout(vt: dict) -> str:
    """
    Render the turnout strip from whatever stats the county actually provides.
    Omits the bar entirely if there is no turnout data (per template contract).
    """
    stats = []
    pct = vt.get("turnout_percentage")
    ballots = vt.get("ballots_cast")
    registered = vt.get("registered_voters")
    if pct:
        stats.append(("Voter Turnout", f"{float(pct):.2f}%"))
    if ballots:
        stats.append(("Ballots Cast", _fmt_int(ballots)))
    if registered:
        stats.append(("Registered Voters", _fmt_int(registered)))

    if not stats:
        return ""

    cells = "\n".join(
        f'    <div class="turnout-stat">\n'
        f'      <div class="turnout-stat-label">{_esc(label)}</div>\n'
        f'      <div class="turnout-stat-value">{_esc(value)}</div>\n'
        f'    </div>'
        for label, value in stats
    )
    return f'  <div class="turnout-bar">\n{cells}\n  </div>'


def _render_provenance(county_data: dict, source_url: str) -> str:
    county = county_data.get("county", "").replace("_", " ")
    updated = county_data.get("last_updated") or county_data.get("scrape_timestamp") or ""
    src = _esc(source_url) if source_url else "#"
    parts = [f'Source: <a href="{src}">{_esc(county)} Registrar of Voters</a>']
    if updated:
        parts.append(f"Updated: {_esc(updated)}")
    return f'  <div class="lnm-provenance">{" &middot; ".join(parts)}</div>'


def _bar_width(pct) -> str:
    try:
        return f"{max(0.0, min(100.0, float(pct))):.1f}"
    except (ValueError, TypeError):
        return "0.0"


def _render_candidate_race(race_def: dict, contest: dict) -> str:
    title = race_def["name"]
    choices = contest.get("choices") or []
    rows = []
    static_cands = race_def.get("candidates", {})

    for ch in choices:
        name = ch.get("name", "")
        if re.match(r"^\s*write[\s-]*in\s*$", name, re.I):
            continue  # skip the Write-in summary row
        # Profession/party: prefer what the merge already attached, else the CSV.
        static = static_cands.get(name.strip().lower(), {})
        profession = ch.get("profession") or static.get("profession") or ""
        if profession.strip().upper() in {"N/A", "NA"}:
            profession = ""
        party = ch.get("party") or static.get("party") or ""
        cls, initial, label = _party_class_and_label(party)

        prof_html = (
            f'\n          <div class="candidate-profession">{_esc(profession)}</div>'
            if profession else ""
        )
        rows.append(
            f'    <div class="candidate-row">\n'
            f'      <div class="candidate-name-cell">\n'
            f'        <div class="candidate-party party-{cls}" data-party="{_esc(label)}">{_esc(initial)}</div>\n'
            f'        <div class="candidate-name-text">\n'
            f'          <div class="candidate-name">{_esc(name)}</div>{prof_html}\n'
            f'        </div>\n'
            f'      </div>\n'
            f'      <div class="candidate-bar-cell">\n'
            f'        <div class="candidate-bar-track"><div class="candidate-bar-fill bar-n" style="width:{_bar_width(ch.get("pct"))}%"></div></div>\n'
            f'      </div>\n'
            f'      <div class="candidate-pct pct-n">{float(ch.get("pct") or 0):.1f}%</div>\n'
            f'      <div class="candidate-votes">{_fmt_int(ch.get("votes"))}</div>\n'
            f'    </div>'
        )

    if not rows:
        body = '    <div class="contest-unavailable">Results not yet available.</div>'
    else:
        head = (
            '    <div class="candidate-table-head">\n'
            '      <span>Candidate</span>\n'
            '      <span>% Votes</span>\n'
            '      <span class="right">Pct</span>\n'
            '      <span class="right">Votes</span>\n'
            '    </div>'
        )
        body = head + "\n" + "\n".join(rows)

    return (
        '  <div class="race-box">\n'
        f'    <div class="race-title">{_esc(title)}</div>\n'
        f'{body}\n'
        '  </div>'
    )


def _render_measure(race_def: dict, contest: dict | None) -> str:
    name = race_def["name"]
    # Split a "Measure B - Jurisdiction" name into headline + jurisdiction title.
    mletter = race_def.get("measure_letter") or ""
    headline = f"Measure {mletter.upper()}" if mletter and not mletter.startswith("prop") else name
    box_title = race_def.get("jurisdiction") or name
    description = race_def.get("description", "")

    choices = (contest.get("choices") if contest else []) or []
    total_votes = sum(int(c.get("votes") or 0) for c in choices)

    rows = []
    for ch in choices:
        label = ch.get("name", "")
        rows.append(
            '        <div class="measure-result-row">\n'
            '          <div class="response-cell">\n'
            f'            <span class="response-label">{_esc(label.upper())}</span>\n'
            '          </div>\n'
            '          <div class="bar-cell-measure">\n'
            f'            <div class="bar-track-measure"><div class="bar-fill" style="width:{_bar_width(ch.get("pct"))}%"></div></div>\n'
            '          </div>\n'
            f'          <div class="pct-cell-measure">{float(ch.get("pct") or 0):.2f}%</div>\n'
            f'          <div class="total-cell">{_fmt_int(ch.get("votes"))}</div>\n'
            '        </div>'
        )

    desc_html = f'      <div class="measure-desc">{_esc(description)}</div>\n' if description else ""

    if rows:
        table = (
            '      <div class="measure-table">\n'
            '        <div class="measure-table-head" style="grid-template-columns: 100px 1fr 60px 90px;">\n'
            '          <span>Response</span>\n'
            '          <span>% Votes</span>\n'
            '          <span class="right">Pct</span>\n'
            '          <span class="right">Votes</span>\n'
            '        </div>\n'
            + "\n".join(rows) + "\n"
            '      </div>'
        )
        votes_counted = f'      <div class="measure-votes-counted">{_fmt_int(total_votes)} votes counted</div>\n'
    else:
        table = '      <div class="contest-unavailable">Results not yet available.</div>'
        votes_counted = ""

    return (
        '  <div class="race-box">\n'
        f'    <div class="race-title">{_esc(box_title)}</div>\n'
        '    <div class="measure-block">\n'
        f'      <div class="measure-name">{_esc(headline)}</div>\n'
        f'{desc_html}'
        f'{votes_counted}'
        f'{table}\n'
        '    </div>\n'
        '  </div>'
    )


_SEARCH_BAR = (
    '  <div class="search-wrap">\n'
    '    <input class="search-input" type="text" placeholder="Search for measures, candidates, contests…" id="electionSearch">\n'
    '  </div>\n'
    '  <div class="no-results" id="noResults">No results found.</div>'
)

_SEARCH_SCRIPT = """<script>
  (function() {
    // Uncontested dropdown toggle.
    var _ut = document.getElementById('uncontestedToggle');
    var _ub = document.getElementById('uncontestedBody');
    var _uc = document.getElementById('uncontestedChevron');
    if (_ut && _ub && _uc) {
      _ut.addEventListener('click', function() {
        _ub.classList.toggle('open');
        _uc.classList.toggle('open');
      });
    }
    // Race search filter.
    var input = document.getElementById('electionSearch');
    var noResults = document.getElementById('noResults');
    if (!input) return;
    input.addEventListener('input', function() {
      var q = this.value.trim().toLowerCase();
      var container = input.closest('.lnm-results-widget') || document;
      var boxes = container.querySelectorAll('.race-box');
      var visible = 0;
      boxes.forEach(function(box) {
        var show = !q || box.textContent.toLowerCase().indexOf(q) !== -1;
        box.style.display = show ? '' : 'none';
        if (show) visible++;
      });
      noResults.style.display = (q && visible === 0) ? 'block' : 'none';
    });
  })();
</script>"""


def render_county_html(county_data: dict, local_defs: list[dict], source_url: str,
                       standalone: bool = True) -> tuple[str, list[dict]]:
    """
    Render one county's election night widget HTML.

    Returns (html, unmatched) where unmatched is the list of LOCAL races that had
    no scraped contest to populate.  `standalone=True` wraps the widget in a full
    HTML document (with <style>); False returns just the embeddable widget block
    for pasting into WordPress.
    """
    contests = county_data.get("contests") or []
    merged, unmatched = match_local_races(local_defs, contests)

    blocks = []
    for row in merged:
        rd, contest = row["def"], row["contest"]
        if rd["is_measure"]:
            blocks.append(_render_measure(rd, contest))
        else:
            if contest is None:
                blocks.append(
                    '  <div class="race-box">\n'
                    f'    <div class="race-title">{_esc(rd["name"])}</div>\n'
                    '    <div class="contest-unavailable">Results not yet available.</div>\n'
                    '  </div>'
                )
            else:
                blocks.append(_render_candidate_race(rd, contest))

    turnout = _render_turnout(county_data.get("voter_turnout") or {})
    provenance = _render_provenance(county_data, source_url)

    # Banner: county name + election title (always rendered, top of widget).
    county_display = county_data.get("county", "").replace("_", " ")
    banner = (
        '  <div class="lnm-county-banner">\n'
        f'    <div class="lnm-county-name">{_esc(county_display)} County</div>\n'
        '    <div class="lnm-election-title">June 2, 2026 Statewide Direct Primary</div>\n'
        '  </div>'
    )

    # Uncontested races: same CSV that drives the ballot-preview dropdown.
    uncontested_html = render_uncontested_dropdown(load_uncontested_races(county_display))

    widget_parts = [banner, _SEARCH_BAR]
    if turnout:
        widget_parts.append(turnout)
    widget_parts.append(provenance)
    if uncontested_html:
        widget_parts.append(uncontested_html)
    widget_parts.extend(blocks)
    widget = '<div class="lnm-results-widget">\n\n' + "\n\n".join(widget_parts) + "\n\n</div>"

    if not standalone:
        return widget + "\n" + _SEARCH_SCRIPT, unmatched

    county_name = county_data.get("county", "").replace("_", " ")
    doc = (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        '  <meta charset="UTF-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"  <title>{_esc(county_name)} — Election Night Results</title>\n"
        f"  <style>{_TEMPLATE_CSS}</style>\n"
        "</head>\n<body>\n\n"
        f"{widget}\n\n"
        f"{_SEARCH_SCRIPT}\n"
        "</body>\n</html>\n"
    )
    return doc, unmatched


# ---------------------------------------------------------------------------
# DRIVER
# ---------------------------------------------------------------------------

def _load_source_urls(csv_path: Path = COUNTY_LINKS_CSV) -> dict[str, str]:
    """Map county → results URL for provenance, from county_links.csv."""
    urls: dict[str, str] = {}
    if not csv_path.exists():
        return urls
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            county = (row.get("county") or row.get("County") or "").strip()
            url = (row.get("test_url") or row.get("live_url") or row.get("url") or "").strip()
            if county:
                urls[county.replace(" ", "_")] = url
    return urls


def main(master_path: Path = MASTER_JSON, standalone: bool = True) -> None:
    master = json.loads(Path(master_path).read_text(encoding="utf-8"))
    counties = master.get("counties", {})
    local_by_county = load_local_races()
    source_urls = _load_source_urls()

    OUT_HTML_DIR.mkdir(parents=True, exist_ok=True)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    print(f"[render] Using template: {TEMPLATE_PATH.relative_to(PROJECT_ROOT)}")
    print(f"[render] Generated: {datetime.now(tz=_PACIFIC).strftime('%Y-%m-%d %H:%M %Z')}\n")

    for county in KNOWN_COUNTIES:
        cdata = counties.get(county)
        display = county.replace("_", " ")
        # County display name used in the local-races CSV (spaces, no underscores).
        local_defs = local_by_county.get(display, [])

        if cdata is None:
            print(f"[render] {display:14} — NOT in master JSON, skipping")
            rows.append({
                "county": display, "local_races_total": len(local_defs),
                "rendered": 0, "unmatched": len(local_defs),
                "unmatched_races": "; ".join(d["name"] for d in local_defs),
                "source_url": source_urls.get(county, ""), "html": "",
            })
            continue

        url = source_urls.get(county, "")
        html_doc, unmatched = render_county_html(cdata, local_defs, url, standalone=standalone)
        rendered = len(local_defs) - len(unmatched)

        # Write the per-county standalone HTML file too.
        (OUT_HTML_DIR / f"{county}.html").write_text(html_doc, encoding="utf-8")

        unmatched_names = "; ".join(r["def"]["name"] for r in unmatched)
        print(f"[render] {display:14} — {rendered}/{len(local_defs)} local races rendered"
              + (f"  | UNMATCHED: {unmatched_names}" if unmatched else ""))

        rows.append({
            "county": display,
            "local_races_total": len(local_defs),
            "rendered": rendered,
            "unmatched": len(unmatched),
            "unmatched_races": unmatched_names,
            "source_url": url,
            "html": html_doc,
        })

    # Main CSV — exactly two columns: county name, then the entire HTML code.
    # The HTML is flattened to a single line per row so the file is one clean row
    # per county (no cell sprawling across many lines in a text/CSV viewer).
    # Collapsing inter-tag whitespace is safe — the markup renders identically.
    def _one_line(html_doc: str) -> str:
        return re.sub(r"\s*\n\s*", " ", html_doc).strip()

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["county", "html"])
        for r in rows:
            writer.writerow([r["county"], _one_line(r["html"])])

    # Diagnostic report (separate file) — match counts + any unmatched local races.
    with open(OUT_REPORT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "county", "local_races_total", "rendered", "unmatched",
            "unmatched_races", "source_url",
        ])
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r[k] for k in writer.fieldnames})

    total_rendered = sum(r["rendered"] for r in rows)
    total_local = sum(r["local_races_total"] for r in rows)
    print(f"\n[render] CSV written: {OUT_CSV.relative_to(PROJECT_ROOT)}  (columns: county, html)")
    print(f"[render] Merge report: {OUT_REPORT_CSV.relative_to(PROJECT_ROOT)}")
    print(f"[render] Per-county HTML: {OUT_HTML_DIR.relative_to(PROJECT_ROOT)}/")
    print(f"[render] Totals: {total_rendered}/{total_local} local races rendered across {len(rows)} counties")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Render election night HTML per county.")
    ap.add_argument("--master", default=str(MASTER_JSON), help="Path to master JSON.")
    ap.add_argument("--embed", action="store_true",
                    help="Emit embeddable widget blocks (no <html>/<style> wrapper) for WordPress.")
    args = ap.parse_args()

    main(master_path=Path(args.master), standalone=not args.embed)
