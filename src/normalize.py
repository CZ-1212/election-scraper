#!/usr/bin/env python3
"""
Normalization Layer — Bay City News Election Pipeline

Reads raw county JSON files saved by the scrapers and merges them into one
master JSON file at data/processed/election_results_master.json.

This module handles two different raw formats:
  - Clarity format:     {"scrape_timestamp": ..., "selenium_data": {...}}
  - Non-Clarity format: {"county_name": ..., "voter_turnout": ..., "contests": [...]}

Both are normalized to the same master structure so downstream code (Sheets,
WordPress) never has to care which platform a county used.
"""

import json
import re
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# COUNTY NAME HELPERS
# ---------------------------------------------------------------------------

# These are the county names we expect to see, used to figure out which
# county a file belongs to when the county name isn't stored inside the JSON.
KNOWN_COUNTIES = [
    "Contra_Costa", "Marin", "Santa_Clara", "Sonoma",  # Clarity counties
    "San_Mateo", "San_Joaquin", "Santa_Cruz",            # Non-Clarity counties
    "Alameda", "Mendocino", "Monterey", "Napa",
    "San_Francisco", "Solano",
]

# Map lowercase/stripped versions to canonical names for flexible matching.
_COUNTY_LOWER = {c.lower().replace("_", ""): c for c in KNOWN_COUNTIES}


def _county_from_filename(filepath: Path) -> str | None:
    """
    Try to extract the county name from a filename.

    The scrapers produce names like:
      San_Mateo_working_20251105_200100_abc123.json
      Contra_Costa_clarity_fixed_20251105_200100_abc123.json
      Contra_Costa_clarity.json  (fixtures)
      San_Mateo_non_clarity.json (fixtures)

    We strip the known suffixes and match against the county list.
    """
    stem = filepath.stem  # filename without extension

    # Remove known suffix patterns so we're left with just the county name.
    # Order matters: strip _non_clarity before _clarity, otherwise "San_Mateo_non_clarity"
    # would have its trailing "_clarity" stripped first, leaving "San_Mateo_non".
    stem = re.sub(r"_working.*$", "", stem)
    stem = re.sub(r"_clarity_fixed.*$", "", stem)
    stem = re.sub(r"_non_clarity$", "", stem)
    stem = re.sub(r"_clarity$", "", stem)

    # Check exact match first (e.g. "Contra_Costa")
    for county in KNOWN_COUNTIES:
        if stem == county:
            return county

    # Fallback: strip underscores/spaces and compare case-insensitively
    normalized = stem.lower().replace("_", "").replace(" ", "")
    return _COUNTY_LOWER.get(normalized)


# ---------------------------------------------------------------------------
# FORMAT DETECTION
# ---------------------------------------------------------------------------

def _is_clarity_format(raw: dict) -> bool:
    """
    Return True if the raw JSON looks like Clarity scraper output.
    Clarity output has a 'selenium_data' key wrapping the actual results.
    """
    return "selenium_data" in raw


# ---------------------------------------------------------------------------
# PARSING HELPERS
# ---------------------------------------------------------------------------

def _parse_choice_string(raw_str: str) -> dict | None:
    """
    Parse a raw choice string like "Yes\n70.67% 197,522" into a canonical dict.

    Some older Clarity samples store choices as strings rather than dicts.
    This handles both the "pct% votes" and "votes pct%" orderings.
    Returns {"name": str, "votes": int, "pct": float} or None if unparseable.
    """
    tokens = re.sub(r"\s+", " ", raw_str.strip()).split()

    if len(tokens) < 3:
        return None

    last = tokens[-1]
    second_last = tokens[-2]

    if re.match(r"^[\d,]+$", last):
        votes_raw, pct_raw, name_tokens = last, second_last.rstrip("%"), tokens[:-2]
    elif re.match(r"^\d[\d.]*%?$", last) and "%" in last:
        pct_raw, votes_raw, name_tokens = last.rstrip("%"), second_last, tokens[:-2]
    else:
        return None

    try:
        votes = int(votes_raw.replace(",", ""))
        pct = float(pct_raw)
    except ValueError:
        return None

    name = " ".join(name_tokens).strip()
    return {"name": name, "votes": votes, "pct": pct} if name else None


def _normalize_choices(raw_choices: list) -> list[dict]:
    """
    Convert a choices list to canonical dicts.

    Scrapers may produce either:
      - Strings: "Yes\n70.67% 197,522"   (older Clarity samples)
      - Dicts:   {"name": "Yes", "votes": 197522, "pct": 70.67}  (normalized scrapers)

    We also drop "Vote Cast" summary rows — those are totals, not choices.
    """
    result = []
    for choice in raw_choices:
        if isinstance(choice, dict):
            name = str(choice.get("name", "")).strip()
            # Skip the "Vote Cast" summary row that Clarity includes.
            if re.match(r"^vote\s*cast", name, re.I):
                continue
            try:
                result.append({
                    "name": name,
                    "votes": int(choice["votes"]),
                    "pct": float(choice["pct"]),
                })
            except (KeyError, ValueError, TypeError):
                # Choice dict is malformed — skip it but flag it below.
                continue
        elif isinstance(choice, str):
            # String format — skip "Vote Cast" rows first.
            if re.match(r"^vote\s*cast", choice.strip(), re.I):
                continue
            parsed = _parse_choice_string(choice)
            if parsed:
                result.append(parsed)

    return result


# ---------------------------------------------------------------------------
# NORMALIZATION — convert each raw format to master structure
# ---------------------------------------------------------------------------

def _normalize_clarity(raw: dict, county: str) -> dict:
    """
    Extract and normalize data from a Clarity-format JSON.
    Clarity data lives inside the 'selenium_data' key.
    """
    sd = raw.get("selenium_data") or {}
    vt = sd.get("voter_turnout") or {}
    contests_raw = sd.get("contests") or []
    last_updated = str(sd.get("last_updated") or "").strip()
    scrape_timestamp = str(raw.get("scrape_timestamp") or sd.get("timestamp") or "").strip()

    contests = []
    for c in contests_raw:
        title = str(c.get("title") or "").strip()
        precincts = str(c.get("precincts_reporting") or "").strip()
        choices = _normalize_choices(c.get("choices") or [])
        if title:
            contests.append({
                "title": title,
                "precincts_reporting": precincts,
                "choices": choices,
            })

    return {
        "county": county,
        "platform": "clarity",
        "scrape_timestamp": scrape_timestamp,
        "last_updated": last_updated,
        "voter_turnout": {
            "ballots_cast": vt.get("ballots_cast") or 0,
            "registered_voters": vt.get("registered_voters") or 0,
            "turnout_percentage": vt.get("turnout_percentage") or 0.0,
        },
        "contests": contests,
    }


def _normalize_non_clarity(raw: dict, county: str) -> dict:
    """
    Extract and normalize data from a non-Clarity-format JSON.
    Non-Clarity data is flat at the top level (no 'selenium_data' wrapper).
    """
    vt = raw.get("voter_turnout") or {}
    contests_raw = raw.get("contests") or []
    last_updated = str(raw.get("last_updated") or "").strip()
    scrape_timestamp = str(raw.get("scrape_timestamp") or "").strip()
    platform = str(raw.get("platform") or "non-clarity").strip()

    contests = []
    for c in contests_raw:
        title = str(c.get("title") or "").strip()
        precincts = str(c.get("precincts_reporting") or "").strip()
        choices = _normalize_choices(c.get("choices") or [])
        if title:
            contests.append({
                "title": title,
                "precincts_reporting": precincts,
                "choices": choices,
            })

    return {
        "county": county,
        "platform": platform,
        "scrape_timestamp": scrape_timestamp,
        "last_updated": last_updated,
        "voter_turnout": {
            "ballots_cast": vt.get("ballots_cast") or 0,
            "registered_voters": vt.get("registered_voters") or 0,
            "turnout_percentage": vt.get("turnout_percentage") or 0.0,
            # Some counties provide additional turnout fields (precincts, mail, etc.).
            **{k: v for k, v in vt.items() if k not in ("ballots_cast", "registered_voters", "turnout_percentage")},
        },
        "contests": contests,
    }


# ---------------------------------------------------------------------------
# ANOMALY DETECTION
# Flags unusual conditions without making any judgement about election results.
# These flags help editors quickly spot data quality issues before publishing.
# ---------------------------------------------------------------------------

def _detect_anomalies(county_data: dict) -> list[str]:
    """
    Return a list of anomaly strings for a single county's normalized data.
    An anomaly means something might be wrong with the data — not the election.
    """
    anomalies = []

    vt = county_data.get("voter_turnout") or {}
    ballots = vt.get("ballots_cast") or 0
    contests = county_data.get("contests") or []

    if ballots == 0:
        anomalies.append("ZERO_BALLOTS: ballots_cast is 0 — results may not be posted yet")

    if not contests:
        anomalies.append("NO_CONTESTS: contests list is empty — check the county website")

    # Flag if turnout percentage seems impossibly high (over 100%)
    turnout_pct = float(vt.get("turnout_percentage") or 0)
    if 0 < turnout_pct > 100:
        anomalies.append(f"TURNOUT_OVER_100: turnout_percentage is {turnout_pct}%")

    # Flag contests where every choice has zero votes (possible zero report)
    for contest in contests:
        all_zero = all(c.get("votes", 0) == 0 for c in contest.get("choices", []))
        if contest.get("choices") and all_zero:
            anomalies.append(f"ZERO_VOTES: all choices in '{contest['title']}' have 0 votes")
            break  # one flag per county is enough to prompt a check

    # Flag if voter turnout data is entirely missing
    if not any([vt.get("ballots_cast"), vt.get("registered_voters"), vt.get("turnout_percentage")]):
        anomalies.append("MISSING_TURNOUT: no turnout data available for this county")

    return anomalies


def _scrape_status(county_data: dict, anomalies: list[str]) -> str:
    """
    Return a simple status string: OK, WARN, or FAIL.

      OK   — data looks complete
      WARN — data arrived but has anomalies (zero ballots, missing turnout, etc.)
      FAIL — no data at all (county_data is empty or marked as failed)
    """
    if not county_data or county_data.get("scrape_failed"):
        return "FAIL"
    if anomalies:
        return "WARN"
    return "OK"


# ---------------------------------------------------------------------------
# MAIN NORMALIZE FUNCTION
# ---------------------------------------------------------------------------

def _parse_all_counties_csv(csv_path: Path) -> dict[str, dict]:
    """
    Parse the all_counties.csv produced by src/run_all.py into per-county dicts.

    The file uses a grouped format — each county gets a header block, a turnout
    row, a contest sub-header, then one row per choice:

        County,ballots_cast,registered_voters,turnout_percentage,Last-update
        Contra_Costa,413212,730646,56.6,2026-06-01 ...
        contest_title,choice_name,votes,vote_percentage
        PROPOSITION 50,Yes,250000,60.5
        PROPOSITION 50,No,163212,39.5
        County,ballots_cast,...    ← next county block starts here
    """
    import csv as _csv

    counties: dict[str, dict] = {}
    current_county: str | None = None
    expect_turnout = False
    expect_contest_rows = False
    current_contests: dict[str, dict] = {}

    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in _csv.reader(f):
            if not row or not any(row):
                continue

            first = row[0].strip()

            # County header row — signals start of a new county block.
            if first == "County" and len(row) >= 4 and row[1].strip() == "ballots_cast":
                if current_county:
                    counties[current_county]["contests"] = list(current_contests.values())
                current_county = None
                current_contests = {}
                expect_turnout = True
                expect_contest_rows = False
                continue

            # Contest sub-header row.
            if first == "contest_title":
                expect_contest_rows = True
                expect_turnout = False
                continue

            # Turnout data row — the row immediately after the County header.
            if expect_turnout and current_county is None and first:
                county_name = first.replace(" ", "_")
                try:
                    ballots = int(str(row[1]).replace(",", "")) if len(row) > 1 and row[1] else 0
                    registered = int(str(row[2]).replace(",", "")) if len(row) > 2 and row[2] else 0
                    turnout_pct = float(row[3]) if len(row) > 3 and row[3] else 0.0
                    last_updated = row[4].strip() if len(row) > 4 else ""
                except (ValueError, IndexError):
                    ballots, registered, turnout_pct, last_updated = 0, 0, 0.0, ""

                current_county = county_name
                counties[current_county] = {
                    "county": county_name,
                    "platform": "custom",
                    "scrape_timestamp": "",
                    "last_updated": last_updated,
                    "voter_turnout": {
                        "ballots_cast": ballots,
                        "registered_voters": registered,
                        "turnout_percentage": turnout_pct,
                    },
                    "contests": [],
                }
                expect_turnout = False
                continue

            # Contest choice row.
            if expect_contest_rows and current_county and first:
                contest_title = first
                choice_name = row[1].strip() if len(row) > 1 else ""
                try:
                    votes = int(str(row[2]).replace(",", "")) if len(row) > 2 and row[2] else 0
                    pct = float(row[3]) if len(row) > 3 and row[3] else 0.0
                except (ValueError, IndexError):
                    votes, pct = 0, 0.0

                if contest_title not in current_contests:
                    current_contests[contest_title] = {
                        "title": contest_title,
                        "precincts_reporting": "",
                        "choices": [],
                    }
                if choice_name:
                    current_contests[contest_title]["choices"].append({
                        "name": choice_name,
                        "votes": votes,
                        "pct": pct,
                    })

    # Save the last county block.
    if current_county:
        counties[current_county]["contests"] = list(current_contests.values())

    return counties


def normalize(input_dir: Path, output_path: Path) -> dict:
    """
    Read county data from input_dir and write the merged master JSON to output_path.

    Reads individual county JSON files (saved by scrape_3_working.py and
    test_clarity_only.py) if present. If none are found, falls back to parsing
    all_counties.csv (saved by src/run_all.py when scraping all 13 counties).

    Returns the master dict so callers can inspect it without re-reading the file.
    """
    input_dir = Path(input_dir)
    output_path = Path(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[normalize] Scanning {input_dir} for county data...")

    # Collect individual county JSON files first (fastest, most precise).
    county_files: dict[str, Path] = {}

    for filepath in sorted(input_dir.glob("*.json")):
        if filepath.name == "election_results_master.json":
            continue
        if any(x in filepath.name for x in ["working_summary", "all_counties"]):
            continue

        county = _county_from_filename(filepath)
        if county is None:
            print(f"[normalize] Skipping unrecognized file: {filepath.name}")
            continue

        if county not in county_files:
            county_files[county] = filepath
        else:
            existing = county_files[county]
            if filepath.stat().st_mtime > existing.stat().st_mtime:
                county_files[county] = filepath

    # If all_counties.csv exists and covers more counties than the individual JSON files,
    # use it — it comes from src/run_all.py which scrapes all 13 counties at once
    # with the correct URLs from county_links.csv.
    csv_candidates = sorted(
        input_dir.glob("all_counties*.csv"),
        key=lambda p: p.stat().st_mtime, reverse=True
    )
    if csv_candidates:
        csv_counties = _parse_all_counties_csv(csv_candidates[0])
        if len(csv_counties) > len(county_files):
            print(f"[normalize] all_counties.csv has {len(csv_counties)} counties vs "
                  f"{len(county_files)} JSON files — using {csv_candidates[0].name}")
            county_files = {}  # discard JSON files, use CSV instead

    if not county_files:
        csv_candidates = sorted(input_dir.glob("all_counties*.csv"),
                                key=lambda p: p.stat().st_mtime, reverse=True)
        if csv_candidates:
            all_csv = csv_candidates[0]
            print(f"[normalize] No county JSON files found — reading from {all_csv.name}")
            parsed = _parse_all_counties_csv(all_csv)
            if not parsed:
                raise ValueError(f"all_counties.csv at {all_csv} contained no county data.")
            # Attach anomalies and status then write master JSON.
            master_counties = {}
            for county, data in parsed.items():
                anomalies = _detect_anomalies(data)
                data["anomalies"] = anomalies
                data["scrape_status"] = _scrape_status(data, anomalies)
                master_counties[county] = data
            master = {
                "pipeline_timestamp": datetime.now().isoformat(),
                "county_count": len(master_counties),
                "counties": master_counties,
            }
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(master, f, indent=2, ensure_ascii=False)
            ok_count = sum(1 for d in master_counties.values() if d.get("scrape_status") == "OK")
            warn_count = sum(1 for d in master_counties.values() if d.get("scrape_status") == "WARN")
            fail_count = sum(1 for d in master_counties.values() if d.get("scrape_status") == "FAIL")
            print(f"[normalize] Master JSON written to: {output_path}")
            print(f"[normalize] Summary: {ok_count} OK / {warn_count} WARN / {fail_count} FAIL")
            return master
        else:
            raise ValueError(f"No county JSON files or all_counties.csv found in {input_dir}")

    print(f"[normalize] Found files for {len(county_files)} counties: {', '.join(sorted(county_files.keys()))}")

    # Normalize each county's data.
    master_counties: dict = {}

    for county, filepath in sorted(county_files.items()):
        print(f"[normalize] Processing {county} from {filepath.name}...")

        try:
            with open(filepath, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            print(f"[normalize] ERROR reading {filepath}: {e}")
            master_counties[county] = {
                "county": county,
                "scrape_failed": True,
                "error": str(e),
                "scrape_status": "FAIL",
                "anomalies": [f"READ_ERROR: {e}"],
            }
            continue

        # Pick the right normalizer based on JSON structure.
        if _is_clarity_format(raw):
            county_data = _normalize_clarity(raw, county)
        else:
            county_data = _normalize_non_clarity(raw, county)

        # Detect anomalies and attach status.
        anomalies = _detect_anomalies(county_data)
        county_data["anomalies"] = anomalies
        county_data["scrape_status"] = _scrape_status(county_data, anomalies)

        if anomalies:
            for flag in anomalies:
                print(f"[normalize] ANOMALY [{county}]: {flag}")

        master_counties[county] = county_data

    # Build the master structure.
    master = {
        "pipeline_timestamp": datetime.now().isoformat(),
        "county_count": len(master_counties),
        "counties": master_counties,
    }

    # Write to disk.
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(master, f, indent=2, ensure_ascii=False)

    print(f"[normalize] Master JSON written to: {output_path}")

    # Print a quick summary to the terminal.
    ok_count = sum(1 for d in master_counties.values() if d.get("scrape_status") == "OK")
    warn_count = sum(1 for d in master_counties.values() if d.get("scrape_status") == "WARN")
    fail_count = sum(1 for d in master_counties.values() if d.get("scrape_status") == "FAIL")
    print(f"[normalize] Summary: {ok_count} OK / {warn_count} WARN / {fail_count} FAIL")

    return master


# ---------------------------------------------------------------------------
# COMMAND-LINE USAGE (for running normalize.py directly)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Normalize raw county JSON files into master JSON.")
    parser.add_argument(
        "--input-dir",
        default=str(Path(__file__).resolve().parent.parent / "data"),
        help="Directory containing raw county JSON files (default: data/)",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parent.parent / "data" / "processed" / "election_results_master.json"),
        help="Output path for master JSON (default: data/processed/election_results_master.json)",
    )
    args = parser.parse_args()

    normalize(input_dir=Path(args.input_dir), output_path=Path(args.output))
