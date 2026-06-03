#!/usr/bin/env python3
"""
Test Report — Bay City News Election Pipeline

Reads the normalized master JSON and produces a clean CSV showing every
race and measure result with candidate/option names, votes, percentages,
descriptions, and total ballots cast.

Uncontested races are included but flagged — they can be filtered out.
Candidate professions and measure descriptions come from the static
local_races CSV so editors can spot gaps in that data.

Usage:
    python scripts/test_report.py
    python scripts/test_report.py --output results/june2_test.csv
    python scripts/test_report.py --skip-uncontested
"""

import argparse
import csv
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

MASTER_JSON     = PROJECT_ROOT / "data" / "processed" / "election_results_master.json"
LOCAL_RACES_CSV = PROJECT_ROOT / "election_data" / "local_races - Sheet1.csv"
UNCONTESTED_CSV = PROJECT_ROOT / "election_data" / "uncontested_races - Sheet1.csv"
DEFAULT_OUTPUT  = PROJECT_ROOT / "data" / "processed" / "test_report.csv"


# ---------------------------------------------------------------------------
# LOAD STATIC REFERENCE DATA
# ---------------------------------------------------------------------------

def _normalize_key(*parts) -> str:
    """Lowercase + strip whitespace — joins all parts for fuzzy matching."""
    combined = " ".join(str(x or "").strip() for x in parts)
    return re.sub(r"\s+", " ", combined.lower().strip())


def load_local_races() -> dict:
    """
    Load candidate professions and measure descriptions from local_races CSV.
    Returns a lookup keyed by (county, race_title, candidate_or_measure_jurisdiction).
    """
    lookup = {}
    if not LOCAL_RACES_CSV.exists():
        print(f"[report] WARNING: {LOCAL_RACES_CSV.name} not found — descriptions will be blank.")
        return lookup

    with open(LOCAL_RACES_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            race    = (row.get("Race/Measure name") or "").strip()
            name    = (row.get("Candidate Name/Measure Juristiction") or "").strip()
            desc    = (row.get("Profession/Description") or "").strip()
            party   = (row.get("Party") or "").strip()
            county  = (row.get("County") or "").strip()
            key = _normalize_key(county, race, name)
            lookup[key] = {"description": desc, "party": party}

    print(f"[report] Loaded {len(lookup)} entries from local_races CSV.")
    return lookup


def load_uncontested() -> set:
    """
    Load the set of uncontested race names so the report can flag them.
    Returns a set of normalized (county, race_title) keys.
    """
    uncontested = set()
    if not UNCONTESTED_CSV.exists():
        return uncontested

    with open(UNCONTESTED_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            race   = (row.get("Race") or "").strip()
            county = (row.get("County") or "").strip()
            if race and county:
                uncontested.add(_normalize_key(county, race))

    print(f"[report] Loaded {len(uncontested)} uncontested races.")
    return uncontested


# ---------------------------------------------------------------------------
# BUILD REPORT
# ---------------------------------------------------------------------------

REPORT_COLUMNS = [
    "County",
    "Race / Measure",
    "Race Type",            # Candidate or Measure
    "Measure Jurisdiction", # e.g. "City of Oakland"
    "Measure Description",  # full ballot text from local_races CSV
    "Uncontested",
    "Precincts Reporting",
    "Choice / Candidate",
    "Party",
    "Profession",
    "Votes",
    "Percent",
    "Total Ballots Cast",
    "Registered Voters",
    "Turnout %",
    "Last Updated",
    "Scrape Status",
    "Anomalies",
]


def build_report(master: dict, local_races: dict, uncontested: set,
                 skip_uncontested: bool = False) -> list[dict]:
    """
    Flatten the master JSON into one row per choice/candidate, joined with
    description and party data from the static local_races CSV.
    """
    rows = []
    counties = master.get("counties") or {}

    for county_key, county_data in sorted(counties.items()):
        county_display = county_key.replace("_", " ")
        vt = county_data.get("voter_turnout") or {}
        ballots_cast    = vt.get("ballots_cast", "")
        registered      = vt.get("registered_voters", "")
        turnout_pct     = vt.get("turnout_percentage", "")
        last_updated    = county_data.get("last_updated", "")
        scrape_status   = county_data.get("scrape_status", "")
        anomalies       = " | ".join(county_data.get("anomalies") or [])

        for contest in county_data.get("contests") or []:
            race_title    = contest.get("title", "")
            precincts     = contest.get("precincts_reporting", "")
            choices       = contest.get("choices") or []
            is_measure    = contest.get("is_measure", False)
            measure_desc  = contest.get("measure_description", "")
            measure_juris = contest.get("measure_jurisdiction", "")
            race_type     = "Measure" if is_measure else "Candidate"

            # Check if this race is uncontested.
            is_uncontested = _normalize_key(county_display, race_title) in uncontested
            if skip_uncontested and is_uncontested:
                continue

            if not choices:
                rows.append({
                    "County":               county_display,
                    "Race / Measure":       race_title,
                    "Race Type":            race_type,
                    "Measure Jurisdiction": measure_juris,
                    "Measure Description":  measure_desc,
                    "Uncontested":          "Yes" if is_uncontested else "",
                    "Precincts Reporting":  precincts,
                    "Choice / Candidate":   "(no choices scraped)",
                    "Party":                "",
                    "Profession":           "",
                    "Votes":                "",
                    "Percent":              "",
                    "Total Ballots Cast":   ballots_cast,
                    "Registered Voters":    registered,
                    "Turnout %":            turnout_pct,
                    "Last Updated":         last_updated,
                    "Scrape Status":        scrape_status,
                    "Anomalies":            anomalies,
                })
                continue

            for choice in choices:
                name       = choice.get("name", "")
                party      = choice.get("party", "")
                profession = choice.get("profession", "")
                votes      = choice.get("votes", "")
                pct        = choice.get("pct", "")
                votes_fmt  = f"{int(votes):,}" if votes != "" else ""
                pct_fmt    = f"{float(pct):.2f}%" if pct != "" else ""

                rows.append({
                    "County":               county_display,
                    "Race / Measure":       race_title,
                    "Race Type":            race_type,
                    "Measure Jurisdiction": measure_juris,
                    "Measure Description":  measure_desc,
                    "Uncontested":          "Yes" if is_uncontested else "",
                    "Precincts Reporting":  precincts,
                    "Choice / Candidate":   name,
                    "Party":                party,
                    "Profession":           profession,
                    "Votes":                votes_fmt,
                    "Percent":              pct_fmt,
                    "Total Ballots Cast":   f"{int(ballots_cast):,}" if ballots_cast else "",
                    "Registered Voters":    f"{int(registered):,}" if registered else "",
                    "Turnout %":            f"{float(turnout_pct):.1f}%" if turnout_pct else "",
                    "Last Updated":         last_updated,
                    "Scrape Status":        scrape_status,
                    "Anomalies":            anomalies,
                })

    return rows


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate a formatted test report from scraped election data.")
    parser.add_argument("--master-json", default=str(MASTER_JSON),
                        help="Path to election_results_master.json")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help="Output CSV path (default: data/processed/test_report.csv)")
    parser.add_argument("--skip-uncontested", action="store_true",
                        help="Omit races listed in uncontested_races CSV from the report.")
    args = parser.parse_args()

    master_path = Path(args.master_json)
    output_path = Path(args.output)

    if not master_path.exists():
        print(f"[report] ERROR: {master_path} not found.")
        print("[report] Run the pipeline first: python run_all.py --mock --no-sheets")
        return

    with open(master_path, encoding="utf-8") as f:
        master = json.load(f)

    local_races  = load_local_races()
    uncontested  = load_uncontested()

    print(f"[report] Building report from {master.get('county_count', 0)} counties...")
    rows = build_report(master, local_races, uncontested, args.skip_uncontested)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[report] ✓ Report written: {output_path}")
    print(f"[report]   {len(rows)} rows | {master.get('county_count', 0)} counties")

    # Print a quick terminal preview — first 20 rows, key columns only.
    print()
    print(f"{'County':<15} {'Race':<40} {'Choice':<25} {'Votes':>10} {'Pct':>8}")
    print("-" * 103)
    for row in rows[:30]:
        print(
            f"{row['County']:<15} "
            f"{row['Race / Measure'][:40]:<40} "
            f"{row['Choice / Candidate'][:25]:<25} "
            f"{row['Votes']:>10} "
            f"{row['Percent']:>8}"
        )
    if len(rows) > 30:
        print(f"  ... and {len(rows) - 30} more rows in the CSV.")


if __name__ == "__main__":
    main()
