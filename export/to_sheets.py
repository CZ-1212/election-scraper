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

# Load environment variables from .env before anything else.
# python-dotenv reads KEY=VALUE pairs from .env and puts them in os.environ.
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import gspread
from google.oauth2.service_account import Credentials


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
    "Status",          # OK / WARN / FAIL
    "Scrape Time",
    "Site Last Updated",
    "Ballots Cast",
    "Registered Voters",
    "Turnout %",
    "Contests",
    "Anomaly Flags",
]

def _update_status_dashboard(ws: gspread.Worksheet, counties: dict) -> None:
    """
    Rewrite the STATUS DASHBOARD tab with one data row per county.
    Clears the existing content first so stale county rows don't linger.
    """
    rows = [STATUS_HEADERS]

    for county_name, data in sorted(counties.items()):
        vt = data.get("voter_turnout") or {}
        anomalies = data.get("anomalies") or []

        rows.append([
            county_name.replace("_", " "),
            data.get("scrape_status", "FAIL"),
            data.get("scrape_timestamp", ""),
            data.get("last_updated", ""),
            vt.get("ballots_cast", ""),
            vt.get("registered_voters", ""),
            vt.get("turnout_percentage", ""),
            len(data.get("contests", [])),
            " | ".join(anomalies) if anomalies else "",
        ])

    ws.clear()
    ws.update(rows, value_input_option="USER_ENTERED")
    print(f"[sheets] STATUS DASHBOARD updated: {len(rows) - 1} counties.")


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
    "Contests",
    "Ballots Cast",
    "Notes",
]

def _append_to_scrape_log(ws: gspread.Worksheet, counties: dict, run_timestamp: str) -> None:
    """
    Append one row per county to the SCRAPE LOG tab.
    Rows are added below whatever is already there — we never overwrite the log.
    """
    # Check if the sheet is empty (no headers yet) and add them if needed.
    existing = ws.get_all_values()
    if not existing or existing[0] != LOG_HEADERS:
        ws.append_row(LOG_HEADERS, value_input_option="USER_ENTERED")

    for county_name, data in sorted(counties.items()):
        vt = data.get("voter_turnout") or {}
        anomalies = data.get("anomalies") or []

        ws.append_row([
            run_timestamp,
            county_name.replace("_", " "),
            data.get("scrape_status", "FAIL"),
            len(data.get("contests", [])),
            vt.get("ballots_cast", ""),
            " | ".join(anomalies) if anomalies else "OK",
        ], value_input_option="USER_ENTERED")

    # Blank row to visually separate runs in the log.
    ws.append_row(["---", "", "", "", "", ""])
    print(f"[sheets] SCRAPE LOG appended: {len(counties)} counties logged.")


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

def _update_statewide_races(ws: gspread.Worksheet, counties: dict) -> None:
    """
    Build a cross-county view of every race that appears in 3 or more counties.

    Layout:
      Row 1: Race title (bold-ish in Sheets since it spans the row)
      Row 2: Headers — Candidate | Party | Total Votes | Total % | [County1] | [County2] ...
      Rows 3+: One row per candidate with their total and per-county votes
      Blank row between races

    This lets editors instantly see how a race is going across the whole
    Bay Area without having to flip through 13 individual county tabs.
    """
    # Step 1: collect all counties reporting each race.
    # Normalize race titles so "GOVERNOR" and "Governor" match.
    def _norm(s):
        return re.sub(r'\s+', ' ', s.strip().lower())

    # race_data[normalized_title] = {county: {candidate_name: {votes, pct, party}}}
    race_data: dict = defaultdict(lambda: defaultdict(dict))
    canonical_title: dict = {}  # normalized → first seen display title

    sorted_counties = sorted(counties.keys())

    for county in sorted_counties:
        data = counties[county]
        for contest in data.get("contests") or []:
            title = contest.get("title", "").strip()
            if not title:
                continue
            norm = _norm(title)
            if norm not in canonical_title:
                canonical_title[norm] = title
            for choice in contest.get("choices") or []:
                name = choice.get("name", "").strip()
                if not name:
                    continue
                race_data[norm][county][name] = {
                    "votes": choice.get("votes", 0) or 0,
                    "pct":   choice.get("pct",   0.0) or 0.0,
                    "party": choice.get("party", "") or "",
                }

    # Step 2: keep only races in 3+ counties (statewide / regional).
    statewide_races = [
        (norm, canonical_title[norm], race_data[norm])
        for norm in race_data
        if len(race_data[norm]) >= 3
    ]
    # Sort by number of reporting counties descending, then alphabetically.
    statewide_races.sort(key=lambda x: (-len(x[2]), x[1]))

    # Step 3: build the rows list to write in one batch.
    rows = [["STATEWIDE RACES — last updated: " + datetime.now().strftime("%Y-%m-%d %H:%M")]]
    rows.append([""])  # blank spacer

    for norm, display_title, by_county in statewide_races:
        reporting_counties = sorted(by_county.keys())

        # Collect every unique candidate name across all counties.
        all_candidates: dict = {}  # name → party (take first non-empty)
        for county in reporting_counties:
            for name, info in by_county[county].items():
                if name not in all_candidates:
                    all_candidates[name] = info.get("party", "")
                elif not all_candidates[name] and info.get("party"):
                    all_candidates[name] = info["party"]

        # Compute total votes per candidate across all reporting counties.
        totals: dict = {}
        for name in all_candidates:
            totals[name] = sum(
                by_county[county].get(name, {}).get("votes", 0)
                for county in reporting_counties
            )

        grand_total = sum(totals.values())

        # Sort candidates by total votes descending.
        ranked = sorted(all_candidates.keys(), key=lambda n: -totals[n])

        # Header row for this race.
        rows.append([f"▸ {display_title}  ({len(reporting_counties)} counties reporting)"])
        header = ["Candidate", "Party", "Total Votes", "Total %"] + reporting_counties
        rows.append(header)

        for name in ranked:
            total_v = totals[name]
            total_pct = round(total_v / grand_total * 100, 2) if grand_total > 0 else 0.0
            per_county = [
                by_county[county].get(name, {}).get("votes", "")
                for county in reporting_counties
            ]
            rows.append([
                name,
                all_candidates[name],
                total_v,
                f"{total_pct:.2f}%",
            ] + per_county)

        rows.append([""])  # blank row between races

    ws.clear()
    # Write in chunks — Sheets API has a 10MB limit per request
    chunk = 500
    for i in range(0, len(rows), chunk):
        ws.append_rows(rows[i:i+chunk], value_input_option="USER_ENTERED")

    print(f"[sheets] STATEWIDE RACES tab updated: {len(statewide_races)} races.")


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
    run_timestamp = master.get("pipeline_timestamp") or datetime.now().isoformat()

    if not counties:
        print("[sheets] WARNING: No county data found in master JSON. Nothing to push.")
        return

    print(f"[sheets] Connecting to Google Sheet (ID: {sheet_id[:8]}...)")
    spreadsheet = _connect(service_account_path, sheet_id)
    print(f"[sheets] Connected: '{spreadsheet.title}'")

    # --- STATUS DASHBOARD tab ---
    ws_status = _get_or_create_tab(spreadsheet, TAB_STATUS)
    _update_status_dashboard(ws_status, counties)

    # --- One tab per county ---
    for county_name, county_data in sorted(counties.items()):
        tab_name = county_name.replace("_", " ")
        ws_county = _get_or_create_tab(spreadsheet, tab_name)
        _update_county_tab(ws_county, county_data)
        print(f"[sheets] Updated tab: '{tab_name}'")

    # --- STATEWIDE RACES tab ---
    ws_statewide = _get_or_create_tab(spreadsheet, "STATEWIDE RACES")
    _update_statewide_races(ws_statewide, counties)

    # --- SCRAPE LOG tab (append only) ---
    ws_log = _get_or_create_tab(spreadsheet, TAB_LOG)
    _append_to_scrape_log(ws_log, counties, run_timestamp)

    # --- PUBLISH CHECKLIST tab (write once, never overwrite) ---
    ws_checklist = _get_or_create_tab(spreadsheet, TAB_CHECKLIST)
    _ensure_publish_checklist(ws_checklist)

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
