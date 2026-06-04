#!/usr/bin/env python3
"""
Google Sheets Live Dashboard — Bay City News Election Pipeline

Reads the normalized master JSON (data/processed/election_results_master.json)
and pushes it to the Google Sheet that editors use as their live dashboard.

The sheet has four tabs:
  STATUS DASHBOARD  — one row per county showing status, turnout, and anomaly flags
  [County Name]     — one tab per county with full contest / choice breakdowns
  SCRAPE LOG        — one new row block appended every pipeline run
  PUBLISH CHECKLIST — static instructions for editors; never overwritten by this script

All credentials come from the .env file. No credentials are ever hardcoded here.
"""

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests as _requests

# Load environment variables from .env before anything else.
# python-dotenv reads KEY=VALUE pairs from .env and puts them in os.environ.
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import gspread
from google.oauth2.service_account import Credentials
from zoneinfo import ZoneInfo

_PACIFIC = ZoneInfo("America/Los_Angeles")


def _now_pacific() -> str:
    """Current time as a Pacific-timezone string for display."""
    from datetime import timezone
    return datetime.now(tz=timezone.utc).astimezone(_PACIFIC).strftime("%Y-%m-%d %H:%M PT")


# ---------------------------------------------------------------------------
# CALIFORNIA SOS API — statewide results for the 8 statewide offices
# Base: https://api.sos.ca.gov/returns/<race-slug>
# No auth required. Returns JSON: {raceTitle, Reporting, ReportingTime, candidates:[]}
# ---------------------------------------------------------------------------

_SOS_ENDPOINTS = {
    "governor":                             "https://api.sos.ca.gov/returns/governor",
    "lieutenant governor":                  "https://api.sos.ca.gov/returns/lieutenant-governor",
    "secretary of state":                   "https://api.sos.ca.gov/returns/secretary-of-state",
    "controller":                           "https://api.sos.ca.gov/returns/controller",
    "treasurer":                            "https://api.sos.ca.gov/returns/treasurer",
    "attorney general":                     "https://api.sos.ca.gov/returns/attorney-general",
    "insurance commissioner":               "https://api.sos.ca.gov/returns/insurance-commissioner",
    "superintendent of public instruction": "https://api.sos.ca.gov/returns/superintendent-of-public-instruction",
}


def _fetch_sos_statewide() -> dict:
    """
    Pull live statewide results from the CA SoS Election Night API for all
    8 statewide offices.  Returns a dict keyed by the same normalized race
    name used in _STATEWIDE_OFFICES:

      {
        "governor": {
          "reporting": "100% (19,788 of 19,788) precincts reporting",
          "updated":   "June 4, 2026, 10:23 a.m.",
          "candidates": {
            "xavier becerra": {"name": "Xavier Becerra", "party": "Dem",
                               "votes": 1322704, "pct": 25.6},
            ...
          }
        },
        ...
      }

    If a race cannot be fetched (network error, API down), that key is
    present but empty so callers can handle it gracefully.
    """
    results = {}
    headers = {"User-Agent": "Mozilla/5.0 BayCityNews-ElectionPipeline/1.0"}

    for race_key, url in _SOS_ENDPOINTS.items():
        results[race_key] = {}
        try:
            resp = _requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            # Named endpoint → object; query endpoint → array. Handle both.
            if isinstance(data, list):
                data = data[0] if data else {}
            if not data:
                continue

            candidates = {}
            for c in data.get("candidates") or []:
                raw_name = (c.get("Name") or "").strip()
                if not raw_name:
                    continue
                norm_name = _norm_candidate_name(raw_name)
                votes_str = re.sub(r"[,\s]", "", str(c.get("Votes") or "0"))
                try:
                    votes = int(votes_str)
                except ValueError:
                    votes = 0
                try:
                    pct = float(c.get("Percent") or 0)
                except ValueError:
                    pct = 0.0
                candidates[norm_name] = {
                    "name":  raw_name,
                    "party": c.get("Party") or "",
                    "votes": votes,
                    "pct":   pct,
                }

            results[race_key] = {
                "reporting":  data.get("Reporting", ""),
                "updated":    data.get("ReportingTime", ""),
                "candidates": candidates,
            }
            print(f"[sos_api]  {race_key:40s} {data.get('Reporting','')}")

        except Exception as exc:
            print(f"[sos_api]  WARNING — could not fetch {race_key}: {exc}")

    return results


# ---------------------------------------------------------------------------
# CONFIGURATION — all values come from .env, never hardcoded
# ---------------------------------------------------------------------------

def _get_env(key: str) -> str:
    """Read a required environment variable. Exit clearly if it's missing."""
    val = os.environ.get(key, "").strip()
    if not val:
        print(f"[sheets] ERROR: {key} is not set in .env. Cannot update Google Sheet.")
        sys.exit(1)
    return val


# Google Sheets OAuth scopes — we need spreadsheets (read/write) and Drive (to find the sheet).
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Tab names — consistent across the whole pipeline.
TAB_STATUS   = "STATUS DASHBOARD"
TAB_LOG      = "SCRAPE LOG"
TAB_CHECKLIST = "PUBLISH CHECKLIST"

# Stores ballot counts from the previous run so we can show deltas to reporters.
_VOTE_STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "processed" / "vote_count_state.json"

# A single interval that adds more than this share of registered voters is flagged
# as a spike — likely a large batch drop from the registrar.
_SPIKE_THRESHOLD_PCT = 5.0  # 5 % of registered voters in one 15-min cycle


def _load_vote_state() -> dict:
    if _VOTE_STATE_FILE.exists():
        with open(_VOTE_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_vote_state(state: dict) -> None:
    _VOTE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_VOTE_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# GOOGLE SHEETS CONNECTION
# ---------------------------------------------------------------------------

def _connect(service_account_path: str, sheet_id: str) -> gspread.Spreadsheet:
    """
    Authenticate with Google and return the target spreadsheet object.
    Uses a service account JSON key file. The path comes from .env.
    """
    creds = Credentials.from_service_account_file(service_account_path, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id)


def _get_or_create_tab(spreadsheet: gspread.Spreadsheet, title: str) -> gspread.Worksheet:
    """
    Return the worksheet with the given title, creating it if it doesn't exist yet.
    This means the script is safe to run on a brand-new empty sheet.
    """
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        print(f"[sheets] Tab '{title}' not found — creating it.")
        return spreadsheet.add_worksheet(title=title, rows=500, cols=26)


# ---------------------------------------------------------------------------
# TAB: STATUS DASHBOARD
# One row per county showing the key health metrics editors need at a glance.
# ---------------------------------------------------------------------------

STATUS_HEADERS = [
    "County",
    "Status",
    "Last Scraped",
    "Site Last Updated",
    "Contests",
]


def _update_status_dashboard(ws: gspread.Worksheet, counties: dict) -> None:
    """Rewrite the STATUS DASHBOARD tab — one row per county, last-scraped time only."""
    rows = [STATUS_HEADERS]
    for county_name, data in sorted(counties.items()):
        rows.append([
            county_name.replace("_", " "),
            data.get("scrape_status", "FAIL"),
            data.get("scrape_timestamp", ""),
            data.get("last_updated", ""),
            len(data.get("contests", [])),
        ])
    ws.clear()
    ws.update(rows, value_input_option="USER_ENTERED")
    print(f"[sheets] STATUS DASHBOARD updated: {len(rows) - 1} counties.")


def _build_vote_state(counties: dict) -> dict:
    """Build the vote-count state dict used by the SCRAPE LOG for delta tracking."""
    return {
        county_name: {
            "ballots_cast": (data.get("voter_turnout") or {}).get("ballots_cast") or 0,
            "timestamp": data.get("scrape_timestamp", ""),
        }
        for county_name, data in counties.items()
    }


# ---------------------------------------------------------------------------
# TAB: [County Name]
# One tab per county, listing every contest and choice with votes and percent.
# ---------------------------------------------------------------------------

COUNTY_TAB_HEADERS = [
    "Contest",
    "Precincts Reporting",
    "Choice",
    "Votes",
    "Percent",
]

def _update_county_tab(ws: gspread.Worksheet, county_data: dict) -> None:
    """
    Rewrite a single county's tab with all its contest and choice rows.
    Each contest gets a header row, then one row per choice.
    """
    rows = [COUNTY_TAB_HEADERS]

    for contest in county_data.get("contests") or []:
        title = contest.get("title", "")
        precincts = contest.get("precincts_reporting", "")

        for choice in contest.get("choices") or []:
            rows.append([
                title,
                precincts,
                choice.get("name", ""),
                choice.get("votes", ""),
                choice.get("pct", ""),
            ])

        # Blank separator row between contests so they're easy to scan.
        rows.append(["", "", "", "", ""])

    ws.clear()
    ws.update(rows, value_input_option="USER_ENTERED")


# ---------------------------------------------------------------------------
# TAB: SCRAPE LOG
# Appends a new block of rows every time the pipeline runs.
# This preserves the history of every scrape so editors can spot changes.
# ---------------------------------------------------------------------------

LOG_HEADERS = [
    "Run Timestamp",
    "County",
    "Status",
    "Ballots Cast",
    "Δ Since Last",
    "⚠ Spike?",
    "Contests",
    "Notes",
]


def _append_to_scrape_log(
    ws: gspread.Worksheet,
    counties: dict,
    run_timestamp: str,
    prev_state: dict,
) -> None:
    """
    Append one row per county to the SCRAPE LOG tab.
    Rows are added below whatever is already there — we never overwrite the log.
    """
    existing = ws.get_all_values()
    if not existing or existing[0] != LOG_HEADERS:
        ws.update([LOG_HEADERS], value_input_option="USER_ENTERED")
        existing = [LOG_HEADERS]

    rows = []
    spikes = []
    for county_name, data in sorted(counties.items()):
        vt = data.get("voter_turnout") or {}
        anomalies = data.get("anomalies") or []
        current_ballots = vt.get("ballots_cast") or 0
        registered = vt.get("registered_voters") or 0

        prev = prev_state.get(county_name, {})
        prev_ballots = prev.get("ballots_cast", None)
        if prev_ballots is None:
            delta_str = "—"
            spike = ""
        else:
            delta = current_ballots - prev_ballots
            delta_str = f"+{delta:,}" if delta >= 0 else f"{delta:,}"
            if registered > 0 and (delta / registered * 100) >= _SPIKE_THRESHOLD_PCT:
                spike = "⚠ SPIKE"
                spikes.append(f"{county_name.replace('_',' ')} (+{delta:,})")
            else:
                spike = ""

        rows.append([
            run_timestamp,
            county_name.replace("_", " "),
            data.get("scrape_status", "FAIL"),
            current_ballots if current_ballots else "",
            delta_str,
            spike,
            len(data.get("contests", [])),
            " | ".join(anomalies) if anomalies else "OK",
        ])

    # Summary row for this run — makes it easy to scan the log for big moments.
    spike_summary = "SPIKES: " + ", ".join(spikes) if spikes else "no spikes"
    rows.append(["--- END RUN", "", "", "", "", spike_summary, "", ""])

    ws.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"[sheets] SCRAPE LOG appended: {len(counties)} counties. {spike_summary}")


# ---------------------------------------------------------------------------
# TAB: PUBLISH CHECKLIST
# Static instructions for editors. We only write this once (first run).
# After that we leave it completely alone so editors can annotate it freely.
# ---------------------------------------------------------------------------

CHECKLIST_CONTENT = [
    ["ELECTION RESULTS — PUBLISH CHECKLIST"],
    [""],
    ["Before publishing to WordPress, editors should do these steps:"],
    [""],
    ["1. Open the STATUS DASHBOARD tab in this spreadsheet."],
    ["   - Every county should show Status = OK or WARN."],
    ["   - FAIL means the scraper could not reach that county's website."],
    ["   - Check the Anomaly Flags column. WARN is usually zero ballots (pre-results)."],
    [""],
    ["2. Spot-check the numbers. Pick 2-3 counties and verify their"],
    ["   Ballots Cast and Turnout % against the county registrar's website."],
    [""],
    ["3. Make sure the SCRAPE LOG shows a recent run timestamp."],
    ["   If the last run is more than 20 minutes old, something may be wrong."],
    [""],
    ["4. When you are satisfied the data is accurate:"],
    ["   - Go to GitHub → Actions tab → 'Publish to WordPress' workflow."],
    ["   - Click 'Run workflow'."],
    ["   - Type the confirmation message exactly as shown and click the green button."],
    [""],
    ["5. After publishing, verify the live page on the BCN/LNM website."],
    [""],
    ["IMPORTANT: Do NOT push to WordPress if any county shows Status = FAIL."],
    ["           Contact the data team first."],
]

def _ensure_publish_checklist(ws: gspread.Worksheet) -> None:
    """
    Write the PUBLISH CHECKLIST content only if the tab is currently empty.
    If there's already content (editors may have added notes), leave it alone.
    """
    existing = ws.get_all_values()
    if existing:
        print(f"[sheets] PUBLISH CHECKLIST already has content — leaving it untouched.")
        return
    ws.update(CHECKLIST_CONTENT, value_input_option="USER_ENTERED")
    print(f"[sheets] PUBLISH CHECKLIST initialized.")


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

def _party_color(party: str) -> dict:
    """Return a Sheets RGB backgroundColor dict for a party affiliation."""
    p = (party or "").lower()
    if "democrat" in p:
        return {"red": 0.78, "green": 0.88, "blue": 1.0}    # light blue
    if "republican" in p:
        return {"red": 1.0,  "green": 0.80, "blue": 0.80}   # light red
    return {"red": 0.93, "green": 0.93, "blue": 0.93}        # light gray


def _norm_race_title(s: str) -> str:
    """
    Normalize a race title for cross-county grouping.
    Strips case, '(Vote for N)' suffix, and a leading 'State ' prefix
    that some counties prepend (e.g. 'State Treasurer' vs 'Treasurer',
    'State Superintendent…' vs 'Superintendent…').
    Does NOT strip 'State' from 'State Board of Equalization'.
    """
    s = re.sub(r'\s+', ' ', s.strip().lower())
    s = re.sub(r'\s*\(vote\s+for\s+\d+\)\s*$', '', s).strip()
    # Strip leading "state " only when it's followed by a known word that
    # some counties omit the prefix on.
    s = re.sub(r'^state (treasurer|superintendent|controller|senator)\b', r'\1', s)
    return s


def _norm_candidate_name(name: str) -> str:
    """
    Lowercase key for de-duplicating the same candidate across counties.
    Strips periods from middle initials so 'Shirley N. Weber' and
    'Shirley N Weber' (different county formatting) collapse to one entry.
    """
    s = re.sub(r'\s+', ' ', name.strip().lower())
    s = re.sub(r'\b([a-z])\.\s*', r'\1 ', s).strip()  # "N." → "N"
    return re.sub(r'\s+', ' ', s)


def _display_candidate_name(name: str) -> str:
    """Title-case a candidate name if it is all-uppercase; leave mixed case alone."""
    if not name or name != name.upper():
        return name
    return name.title()


def _update_statewide_races(ws: gspread.Worksheet, counties: dict) -> None:
    """
    Cross-county view of every race appearing in 3+ counties.

    Layout per race:
      ▸ RACE TITLE  (N counties reporting)
      Candidate | Party | Total Votes | Total % | County … County
      [top 3 candidates, one row each, coloured by party]
      All Others (N candidates) | | votes | %  | …
      [blank row]

    Fixes applied vs. old version:
      - Race titles normalised with _norm_race_title() so 'GOVERNOR',
        'Governor', and 'Governor (Vote for 1)' all collapse to 'governor'.
      - Candidate names de-duplicated case-insensitively so 'XAVIER BECERRA'
        and 'Xavier Becerra' count as one person.
      - Only top-3 candidates shown; the rest are summed into 'All Others'.
      - Party colours applied via Sheets batchUpdate (blue/red/gray).
    """
    # ── Step 1: collect race data, normalising titles and candidate names ───
    # race_data[norm_title][county][norm_name] = {votes, pct, party, display_name}
    race_data: dict = defaultdict(lambda: defaultdict(dict))
    canonical_title: dict = {}   # norm_title → first seen display title

    sorted_counties = sorted(counties.keys())

    for county in sorted_counties:
        data = counties[county]
        for contest in data.get("contests") or []:
            title = contest.get("title", "").strip()
            if not title:
                continue
            norm = _norm_race_title(title)
            if norm not in canonical_title:
                # Prefer Title-Case display title over ALL-CAPS.
                existing = canonical_title.get(norm, "")
                if not existing or title == title.title():
                    canonical_title[norm] = title.title() if title == title.upper() else title
            for choice in contest.get("choices") or []:
                raw_name = choice.get("name", "").strip()
                if not raw_name:
                    continue
                norm_name = _norm_candidate_name(raw_name)
                existing = race_data[norm][county].get(norm_name, {})
                race_data[norm][county][norm_name] = {
                    "votes":        choice.get("votes", 0) or 0,
                    "pct":          choice.get("pct", 0.0) or 0.0,
                    "party":        choice.get("party", "") or existing.get("party", ""),
                    "display_name": _display_candidate_name(raw_name),
                }

    # ── Step 2: filter to the 8 true California statewide offices ───────────
    # Only these races are the same contest across every county.  Anything
    # else that happens to share a name (e.g. "County Superintendent of
    # Schools", "Auditor-Controller", "Supervisor 3rd District") is a
    # county-level race and must be excluded even if it appears in 3+ counties.
    _STATEWIDE_OFFICES = {
        "governor",
        "lieutenant governor",
        "secretary of state",
        "controller",
        "treasurer",
        "attorney general",
        "insurance commissioner",
        "superintendent of public instruction",
    }

    statewide_races = [
        (norm, canonical_title[norm], race_data[norm])
        for norm in race_data
        if norm in _STATEWIDE_OFFICES
    ]
    statewide_races.sort(key=lambda x: (-len(x[2]), x[1]))

    # ── Step 3: fetch CA SoS statewide numbers for comparison ───────────────
    print("[sheets] Fetching CA SoS statewide results for comparison...")
    sos_data = _fetch_sos_statewide()

    # ── Step 4: build rows + collect formatting instructions ─────────────────
    sos_updated = next(
        (v.get("updated") for v in sos_data.values() if v.get("updated")), ""
    )
    rows = [[
        f"STATEWIDE RACES — Bay Area results vs. California statewide  |  "
        f"Bay Area last scraped: {_now_pacific()}  |  "
        f"CA SoS last updated: {sos_updated}"
    ]]
    rows.append([""])
    current_row = 2   # 0-indexed; rows 0-1 are the header block above

    format_requests = []
    sheet_id = ws.id

    for norm, display_title, by_county in statewide_races:
        reporting_counties = sorted(by_county.keys())
        county_headers = [c.replace("_", " ") for c in reporting_counties]
        sos_race = sos_data.get(norm, {})
        sos_cands = sos_race.get("candidates", {})
        sos_reporting = sos_race.get("reporting", "CA data unavailable")

        # Aggregate Bay Area candidates (case-insensitive de-dup).
        all_candidates: dict = {}   # norm_name → {display_name, party}
        for county in reporting_counties:
            for norm_name, info in by_county[county].items():
                if norm_name not in all_candidates:
                    all_candidates[norm_name] = {
                        "display_name": info["display_name"],
                        "party":        info["party"],
                    }
                elif not all_candidates[norm_name]["party"] and info["party"]:
                    all_candidates[norm_name]["party"] = info["party"]

        # Fill in party from SoS data when our scraper didn't capture it.
        for norm_name, info in all_candidates.items():
            if not info["party"] and norm_name in sos_cands:
                info["party"] = sos_cands[norm_name].get("party", "")

        bay_totals = {
            n: sum(by_county[c].get(n, {}).get("votes", 0) for c in reporting_counties)
            for n in all_candidates
        }
        bay_grand = sum(bay_totals.values())
        ranked = sorted(all_candidates.keys(), key=lambda n: -bay_totals[n])

        top3 = ranked[:3]
        rest = ranked[3:]

        # Title row — includes SoS reporting status
        rows.append([
            f"▸ {display_title}  "
            f"({len(reporting_counties)} of 13 Bay Area counties)  |  "
            f"CA: {sos_reporting}"
        ])
        current_row += 1

        # Header row
        rows.append([
            "Candidate", "Party",
            "Bay Area Votes", "Bay Area %",
            "CA Statewide Votes", "CA Statewide %", "Bay Area vs. CA",
        ] + county_headers)
        current_row += 1

        # Top-3 candidate rows
        for norm_name in top3:
            info    = all_candidates[norm_name]
            party   = info["party"]
            bay_v   = bay_totals[norm_name]
            bay_pct = round(bay_v / bay_grand * 100, 2) if bay_grand > 0 else 0.0

            # SoS match — try exact norm_name, then fuzzy (last name match).
            sos_c = sos_cands.get(norm_name)
            if sos_c is None:
                last = norm_name.split()[-1]
                sos_c = next((v for k, v in sos_cands.items()
                              if k.split()[-1] == last), None)

            if sos_c:
                ca_v   = sos_c["votes"]
                ca_pct = sos_c["pct"]
                diff   = f"{bay_pct - ca_pct:+.1f}pp"
                # Prefer SoS party abbreviation expansion
                if not party:
                    party = sos_c.get("party", "")
            else:
                ca_v, ca_pct, diff = "—", "—", "—"

            per_county = [
                by_county[c].get(norm_name, {}).get("votes", "")
                for c in reporting_counties
            ]
            rows.append([
                info["display_name"], party,
                bay_v, f"{bay_pct:.2f}%",
                ca_v,  f"{ca_pct:.1f}%" if ca_pct != "—" else "—",
                diff,
            ] + per_county)

            color = _party_color(party)
            format_requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId":          sheet_id,
                        "startRowIndex":    current_row,
                        "endRowIndex":      current_row + 1,
                        "startColumnIndex": 0,
                        "endColumnIndex":   7 + len(reporting_counties),
                    },
                    "cell":   {"userEnteredFormat": {"backgroundColor": color}},
                    "fields": "userEnteredFormat.backgroundColor",
                }
            })
            current_row += 1

        # "All Others" summary row
        if rest:
            others_v   = sum(bay_totals[n] for n in rest)
            others_pct = round(others_v / bay_grand * 100, 2) if bay_grand > 0 else 0.0
            others_per = [
                sum(by_county[c].get(n, {}).get("votes", 0) for n in rest) or ""
                for c in reporting_counties
            ]
            rows.append([
                f"All Others ({len(rest)} candidates)", "",
                others_v, f"{others_pct:.2f}%",
                "", "", "",
            ] + others_per)
            current_row += 1

        rows.append([""])   # blank spacer between races
        current_row += 1

    # ── Step 4: write all rows in one shot ───────────────────────────────────
    ws.clear()
    chunk = 500
    if rows:
        ws.update(rows[:chunk], "A1", value_input_option="USER_ENTERED")
        for i in range(chunk, len(rows), chunk):
            ws.append_rows(rows[i:i + chunk], value_input_option="USER_ENTERED")

    # ── Step 5: apply party colours in a single batch API call ───────────────
    if format_requests:
        ws.spreadsheet.batch_update({"requests": format_requests})

    print(f"[sheets] STATEWIDE RACES tab updated: {len(statewide_races)} races.")


def _update_party_breakdown(ws: gspread.Worksheet, counties: dict) -> None:
    """
    New tab: PARTY BREAKDOWN
    For each statewide race, shows Democrat / Republican / Other vote totals
    and percentages broken down by county — one block per race.
    """
    sorted_counties = sorted(counties.keys())

    # Re-use the same race grouping logic.
    race_data: dict = defaultdict(lambda: defaultdict(dict))
    canonical_title: dict = {}

    for county in sorted_counties:
        for contest in (counties[county].get("contests") or []):
            title = contest.get("title", "").strip()
            if not title:
                continue
            norm = _norm_race_title(title)
            if norm not in canonical_title:
                canonical_title[norm] = title.title() if title == title.upper() else title
            for choice in contest.get("choices") or []:
                raw_name = choice.get("name", "").strip()
                norm_name = _norm_candidate_name(raw_name)
                race_data[norm][county][norm_name] = {
                    "votes": choice.get("votes", 0) or 0,
                    "party": choice.get("party", "") or "",
                }

    _STATEWIDE_OFFICES = {
        "governor", "lieutenant governor", "secretary of state",
        "controller", "treasurer", "attorney general",
        "insurance commissioner", "superintendent of public instruction",
    }
    statewide = [
        (norm, canonical_title[norm], race_data[norm])
        for norm in race_data
        if norm in _STATEWIDE_OFFICES
    ]
    statewide.sort(key=lambda x: (-len(x[2]), x[1]))

    rows = [["PARTY BREAKDOWN BY COUNTY — last updated: " + _now_pacific()]]
    rows.append(["How each county's votes split by party for every statewide race."])
    rows.append([""])

    for norm, display_title, by_county in statewide:
        reporting_counties = sorted(by_county.keys())

        rows.append([f"▸ {display_title}"])
        rows.append(["County", "Dem Votes", "Rep Votes", "Other Votes",
                     "Dem %", "Rep %", "Other %"])

        for county in reporting_counties:
            dem = rep = other = 0
            for norm_name, info in by_county[county].items():
                v = info["votes"]
                p = (info["party"] or "").lower()
                if "democrat" in p:
                    dem += v
                elif "republican" in p:
                    rep += v
                else:
                    other += v
            total = dem + rep + other
            if total == 0:
                continue

            def pct(n):
                return f"{n / total * 100:.1f}%" if total > 0 else "—"

            rows.append([
                county.replace("_", " "),
                dem, rep, other,
                pct(dem), pct(rep), pct(other),
            ])

        rows.append([""])   # spacer

    ws.clear()
    if rows:
        ws.update("A1", rows[:500], value_input_option="USER_ENTERED")
        for i in range(500, len(rows), 500):
            ws.append_rows(rows[i:i + 500], value_input_option="USER_ENTERED")

    print(f"[sheets] PARTY BREAKDOWN tab updated: {len(statewide)} races.")


def _update_glossary(ws: gspread.Worksheet) -> None:
    """
    Write (or overwrite) a GLOSSARY & SOURCES tab with column definitions,
    data sources, API references, and pipeline documentation.
    This tab is always fully rewritten so it stays current with the code.
    """
    rows = [
        # ── Header ──────────────────────────────────────────────────────────
        ["BAY CITY NEWS — ELECTION RESULTS PIPELINE: GLOSSARY & SOURCES"],
        ["June 2, 2026 California Statewide Direct Primary"],
        [f"Last updated: {_now_pacific()}"],
        [""],

        # ── Tab guide ───────────────────────────────────────────────────────
        ["SHEET TABS — WHAT EACH ONE SHOWS"],
        ["Tab", "Contents", "Updates"],
        ["STATUS DASHBOARD",   "One row per county: scrape status, last scraped time, last site update, contest count.",  "Every pipeline run"],
        ["[County Name] ×13",  "Full contest and choice breakdown for that county (votes and % per candidate/measure).", "Every pipeline run"],
        ["STATEWIDE RACES",    "8 state offices. Bay Area totals vs. CA statewide (SoS API). Party colours: blue=Dem, red=Rep, gray=Other. Top 3 + All Others per race.", "Every pipeline run"],
        ["PARTY BREAKDOWN",    "For each statewide race: how each county's votes split Democrat / Republican / Other.",   "Every pipeline run"],
        ["SCRAPE LOG",         "Append-only history of every pipeline run — ballots cast, delta since last run, spike flag.", "Every pipeline run (appended)"],
        ["PUBLISH CHECKLIST",  "Editor instructions for reviewing data before publishing to WordPress.",                   "Written once, never overwritten"],
        ["GLOSSARY & SOURCES", "This tab. Column definitions, sources, API references.",                                  "Every pipeline run"],
        [""],

        # ── Column definitions ───────────────────────────────────────────────
        ["COLUMN DEFINITIONS"],
        ["Column", "Definition"],
        ["Status",           "OK = scrape succeeded with data. WARN = succeeded but zero ballots or anomaly detected. FAIL = scrape could not reach the county website."],
        ["Last Scraped",     "Timestamp when the pipeline last successfully pulled data from this county's website (Pacific Time)."],
        ["Site Last Updated","Timestamp the county's own website shows for when their results file was last updated."],
        ["Contests",         "Number of races parsed from this county's results page."],
        ["Bay Area Votes",   "Sum of votes for this candidate across all 13 Bay Area counties in our pipeline."],
        ["Bay Area %",       "Bay Area votes for this candidate ÷ total Bay Area votes in this race × 100."],
        ["CA Statewide Votes","Total votes statewide per the CA Secretary of State API (all 58 counties)."],
        ["CA Statewide %",   "Statewide percentage per the CA SoS API."],
        ["Bay Area vs. CA",  "Bay Area % minus CA Statewide %. Positive = Bay Area voted more for this candidate than the state average. Negative = less."],
        ["Δ Since Last",     "Change in ballots cast since the previous pipeline run (SCRAPE LOG only)."],
        ["⚠ Spike?",         "Flagged if a single 15-min cycle added more than 5% of registered voters — likely a large batch drop from the registrar."],
        [""],

        # ── Data sources ─────────────────────────────────────────────────────
        ["DATA SOURCES — 13 BAY AREA COUNTIES"],
        ["County",          "Platform",          "Live Results URL",                                                                                          "Source Label"],
        ["Alameda",         "Custom HTML",        "https://alamedacountyca.gov/rovresults/259/",                                                              "Alameda County Registrar of Voters"],
        ["Contra Costa",    "Clarity Elections",  "https://results.enr.clarityelections.com/CA/Contra_Costa/126374/web.345435/#/summary",                    "Contra Costa County Elections Division"],
        ["Marin",           "Clarity Elections",  "https://results.enr.clarityelections.com/CA/Marin/126360/web.345435/#/summary",                           "Marin County Elections"],
        ["Mendocino",       "Custom HTML",        "https://www.mendocinocounty.gov/government/assessor-county-clerk-recorder-elections/current-election-results", "Mendocino County Elections"],
        ["Monterey",        "Custom HTML",        "https://www.countyofmonterey.gov/government/departments-a-h/elections/election-results",                  "Monterey County Elections Department"],
        ["Napa",            "PDF (auto-discover)","https://www.napacounty.gov/402/Election-Results",                                                         "Napa County Elections — scraper auto-finds latest PDF"],
        ["San Francisco",   "Custom XML/PDF",     "https://www.sfelections.org/results/20260602w/index.html",                                                "San Francisco Department of Elections"],
        ["San Joaquin",     "LiveVoterTurnout",   "https://www.livevoterturnout.com/ENR/sanjoaquincaenr/21/en/Index_21.html",                                "San Joaquin County Registrar of Voters"],
        ["San Mateo",       "LiveVoterTurnout",   "https://www.livevoterturnout.com/ENR/sanmateocaenr/19/en/t7vnu_Index_19.html",                            "San Mateo County Elections"],
        ["Santa Clara",     "Clarity Elections",  "https://results.enr.clarityelections.com/CA/Santa_Clara/126487/web.345435/#/summary",                    "Santa Clara County Registrar of Voters"],
        ["Santa Cruz",      "Custom HTML",        "https://www2.santacruzcountyca.gov/ElectionSites/ElectionResults/Results",                               "Santa Cruz County Elections Department"],
        ["Solano",          "Custom HTML",        "https://content.solanocounty.gov/sites/default/files/2026-04/HTML_Cumulative_Results-2026_Statewide_Direct_Primary_-_TW-4-20-2026_16-38-04_PM.html", "Solano County Elections Division"],
        ["Sonoma",          "Clarity Elections",  "https://results.enr.clarityelections.com/CA/Sonoma/126199/web.345435/#/summary",                         "Sonoma County Registrar of Voters"],
        [""],

        # ── Statewide API ────────────────────────────────────────────────────
        ["CA SECRETARY OF STATE API — STATEWIDE RESULTS"],
        ["Race",                            "API Endpoint"],
        ["Governor",                        "https://api.sos.ca.gov/returns/governor"],
        ["Lieutenant Governor",             "https://api.sos.ca.gov/returns/lieutenant-governor"],
        ["Secretary of State",              "https://api.sos.ca.gov/returns/secretary-of-state"],
        ["Controller",                      "https://api.sos.ca.gov/returns/controller"],
        ["Treasurer",                       "https://api.sos.ca.gov/returns/treasurer"],
        ["Attorney General",                "https://api.sos.ca.gov/returns/attorney-general"],
        ["Insurance Commissioner",          "https://api.sos.ca.gov/returns/insurance-commissioner"],
        ["Superintendent of Public Instruction", "https://api.sos.ca.gov/returns/superintendent-of-public-instruction"],
        ["",                                "No authentication required. Returns JSON. Documented at https://www.sos.ca.gov/media/"],
        [""],

        # ── Pipeline notes ───────────────────────────────────────────────────
        ["PIPELINE NOTES"],
        ["Item",            "Detail"],
        ["Update frequency","Automatic every 15 minutes via GitHub Actions on election night."],
        ["Manual publish",  "Run: python export/to_wordpress.py --push-rendered  — requires typed YES confirmation."],
        ["Test push",       "Run: python export/to_wordpress.py --push-rendered --test-page 183978 --counties Marin"],
        ["Name casing",     "All-uppercase candidate names are normalized to Title Case during the normalize step. Counties that already post proper casing are untouched."],
        ["Party matching",  "Party affiliation comes from county data where available; back-filled from CA SoS API where county scraper did not capture it."],
        ["Napa PDF",        "Napa posts a new PDF every few hours on election night. The scraper auto-discovers the latest one from napacounty.gov/402/Election-Results."],
        ["Statewide threshold", "A race must appear in all 13 counties to qualify for the STATEWIDE RACES tab. County-level races with shared names (e.g. Auditor-Controller) are excluded by an explicit whitelist."],
        ["GitHub repo",     "https://github.com/alariosjx/election-scraper"],
        ["Contact",         "andres@baycitynews.com"],
    ]

    ws.clear()
    ws.update(rows, value_input_option="USER_ENTERED")
    print(f"[sheets] GLOSSARY & SOURCES tab updated ({len(rows)} rows).")


def update_sheets(master_json_path: Path) -> None:
    """
    Read the master JSON and push all data to the Google Sheet.
    Called by run_all.py as Step 3 of the pipeline.
    """
    master_json_path = Path(master_json_path)

    # Read credentials and sheet ID from environment (.env file).
    service_account_path = _get_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = _get_env("GOOGLE_SHEET_ID")

    # Load the normalized data.
    with open(master_json_path, encoding="utf-8") as f:
        master = json.load(f)

    counties = master.get("counties") or {}
    run_timestamp = master.get("pipeline_timestamp") or _now_pacific()

    if not counties:
        print("[sheets] WARNING: No county data found in master JSON. Nothing to push.")
        return

    print(f"[sheets] Connecting to Google Sheet (ID: {sheet_id[:8]}...)")
    spreadsheet = _connect(service_account_path, sheet_id)
    print(f"[sheets] Connected: '{spreadsheet.title}'")

    # Load previous ballot counts for delta calculation.
    prev_state = _load_vote_state()

    # --- STATUS DASHBOARD tab ---
    ws_status = _get_or_create_tab(spreadsheet, TAB_STATUS)
    _update_status_dashboard(ws_status, counties)

    # Save updated ballot counts so the next run can compute deltas for SCRAPE LOG.
    _save_vote_state(_build_vote_state(counties))

    # --- One tab per county ---
    for county_name, county_data in sorted(counties.items()):
        tab_name = county_name.replace("_", " ")
        ws_county = _get_or_create_tab(spreadsheet, tab_name)
        _update_county_tab(ws_county, county_data)
        print(f"[sheets] Updated tab: '{tab_name}'")

    # --- STATEWIDE RACES tab ---
    ws_statewide = _get_or_create_tab(spreadsheet, "STATEWIDE RACES")
    _update_statewide_races(ws_statewide, counties)

    # --- PARTY BREAKDOWN tab ---
    ws_party = _get_or_create_tab(spreadsheet, "PARTY BREAKDOWN")
    _update_party_breakdown(ws_party, counties)

    # --- SCRAPE LOG tab (append only) ---
    ws_log = _get_or_create_tab(spreadsheet, TAB_LOG)
    _append_to_scrape_log(ws_log, counties, run_timestamp, prev_state)

    # --- PUBLISH CHECKLIST tab (write once, never overwrite) ---
    ws_checklist = _get_or_create_tab(spreadsheet, TAB_CHECKLIST)
    _ensure_publish_checklist(ws_checklist)

    # --- GLOSSARY & SOURCES tab (always rewritten) ---
    ws_glossary = _get_or_create_tab(spreadsheet, "GLOSSARY & SOURCES")
    _update_glossary(ws_glossary)

    print(f"[sheets] All tabs updated successfully.")


# ---------------------------------------------------------------------------
# COUNTY LINKS SYNC
# Reads the "links" tab from the Google Sheet and writes county_links.csv.
# The team maintains URLs in the spreadsheet — the pipeline syncs them down
# before every scrape so nobody has to edit CSV files by hand.
# ---------------------------------------------------------------------------

# Which Clarity / LiveVoterTurnout counties use which platform.
# Everything not listed here is treated as "custom".
_PLATFORM_MAP = {
    "Contra_Costa": "clarity",
    "Marin":        "clarity",
    "Santa_Clara":  "clarity",
    "Sonoma":       "clarity",
    "San_Mateo":    "livevoterturnout",
    "San_Joaquin":  "livevoterturnout",
}

# Static source labels — these never change so we keep them here rather than
# adding another column to the spreadsheet.
_SOURCE_LABELS = {
    "Alameda":       "Alameda County Registrar of Voters",
    "Contra_Costa":  "Contra Costa County Elections Division",
    "Marin":         "Marin County Elections",
    "Mendocino":     "Mendocino County Elections",
    "Monterey":      "Monterey County Elections Department",
    "Napa":          "Napa County Elections",
    "San_Francisco": "San Francisco Department of Elections",
    "San_Joaquin":   "San Joaquin County Registrar of Voters",
    "San_Mateo":     "San Mateo County Elections",
    "Santa_Clara":   "Santa Clara County Registrar of Voters",
    "Santa_Cruz":    "Santa Cruz County Elections Department",
    "Solano":        "Solano County Elections Division",
    "Sonoma":        "Sonoma County Registrar of Voters",
}


def sync_county_links(output_csv_path: Path) -> bool:
    """
    Pull county URLs from the 'links' tab of the Google Sheet and write
    them to county_links.csv so the scrapers always use up-to-date URLs.

    Column mapping (Google Sheet → CSV):
      test_url  = Zero Report  (if set) else Past Election
      live_url  = Zero Report  (if set) else Placeholder

    Returns True on success, False if the sync fails (caller can fall back
    to whatever is already in the CSV).
    """
    service_account_path = _get_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = _get_env("GOOGLE_SHEET_ID")

    print("[links:sync] Pulling county URLs from Google Sheet 'links' tab...")

    try:
        spreadsheet = _connect(service_account_path, sheet_id)
        ws = spreadsheet.worksheet("links")
        rows = ws.get_all_records()
    except Exception as e:
        print(f"[links:sync] WARNING: Could not read 'links' tab — {e}")
        print("[links:sync] Falling back to existing county_links.csv.")
        return False

    output_csv_path = Path(output_csv_path)
    output_csv_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with open(output_csv_path, "w", newline="", encoding="utf-8") as f:
        import csv as _csv
        writer = _csv.DictWriter(f, fieldnames=["county", "platform", "test_url", "live_url", "source_label"])
        writer.writeheader()

        for row in rows:
            # Normalize county name: "Contra Costa" → "Contra_Costa"
            county_raw = str(row.get("County") or "").strip()
            if not county_raw:
                continue
            county = county_raw.replace(" ", "_")

            zero_report   = str(row.get("Zero Report")  or "").strip()
            placeholder   = str(row.get("Placeholder")  or "").strip()
            past_election = str(row.get("Past Election") or "").strip()

            # test_url: zero report if available, otherwise past election
            test_url = zero_report or past_election

            # live_url: zero report if available, otherwise placeholder
            live_url = zero_report or placeholder

            writer.writerow({
                "county":       county,
                "platform":     _PLATFORM_MAP.get(county, "custom"),
                "test_url":     test_url,
                "live_url":     live_url,
                "source_label": _SOURCE_LABELS.get(county, ""),
            })
            written += 1
            print(f"[links:sync]   {county}: test={test_url[:60]}...")

    print(f"[links:sync] ✓ Wrote {written} counties to {output_csv_path.name}")
    return True


# ---------------------------------------------------------------------------
# CONNECTION TEST — verify credentials and sheet access without writing anything
# ---------------------------------------------------------------------------

def test_connection() -> bool:
    """
    Connect to the Google Sheet and read its metadata.
    Writes absolutely nothing — safe to run at any time.

    Prints the sheet title, number of tabs, and lists all existing tab names.
    Returns True if the connection succeeded, False if it failed.
    """
    service_account_path = _get_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = _get_env("GOOGLE_SHEET_ID")

    print(f"[sheets:test] Connecting to Google Sheet...")
    print(f"[sheets:test]   Service account: {service_account_path}")
    print(f"[sheets:test]   Sheet ID:        {sheet_id[:8]}...")

    try:
        spreadsheet = _connect(service_account_path, sheet_id)
        worksheets = spreadsheet.worksheets()
        tab_names = [ws.title for ws in worksheets]

        print(f"[sheets:test] ✓ Connected successfully.")
        print(f"[sheets:test]   Sheet title: '{spreadsheet.title}'")
        print(f"[sheets:test]   Tabs ({len(tab_names)}): {', '.join(tab_names) if tab_names else '(empty)'}")
        print(f"[sheets:test] Connection test passed — credentials are valid, sheet is accessible.")
        return True

    except Exception as e:
        print(f"[sheets:test] ✗ Connection FAILED: {e}")
        print(f"[sheets:test] Common causes:")
        print(f"[sheets:test]   - Key file path is wrong (check GOOGLE_SERVICE_ACCOUNT_JSON in .env)")
        print(f"[sheets:test]   - Service account not shared on the Google Sheet as Editor")
        print(f"[sheets:test]   - GOOGLE_SHEET_ID is wrong")
        return False


# ---------------------------------------------------------------------------
# COMMAND-LINE USAGE (for running to_sheets.py directly)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Push normalized election data to Google Sheets.")
    parser.add_argument(
        "--master-json",
        default=str(Path(__file__).resolve().parent.parent / "data" / "processed" / "election_results_master.json"),
        help="Path to election_results_master.json (default: data/processed/election_results_master.json)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test Google Sheets connection only — reads sheet metadata, writes nothing.",
    )
    args = parser.parse_args()

    if args.test:
        ok = test_connection()
        sys.exit(0 if ok else 1)
    else:
        update_sheets(master_json_path=Path(args.master_json))
