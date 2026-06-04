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

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_PACIFIC = ZoneInfo("America/Los_Angeles")

# Path to the static local_races roster CSV — candidate parties, professions,
# and measure descriptions that don't change between scrape runs.
_LOCAL_RACES_CSV = Path(__file__).resolve().parent.parent / "election_data" / "local_races - Sheet1.csv"

# Regex to detect ballot measure rows (vs. candidate rows) in the roster.
_MEASURE_PAT = re.compile(r"\b(measure|proposition|prop|bond|recall|initiative)\b", re.I)


def _load_local_races(csv_path: Path) -> tuple[dict, dict]:
    """
    Load the static local_races CSV into two lookup dicts:

      candidate_lookup  — keyed by (county, race_title, candidate_name) → {party, profession}
      measure_lookup    — keyed by (county, race_title)                  → {description, jurisdiction}

    Keys are lowercased and whitespace-collapsed for fuzzy matching.
    """
    def _key(*parts):
        return re.sub(r"\s+", " ", " ".join(str(p or "").strip().lower() for p in parts)).strip()

    candidates: dict = {}
    measures: dict   = {}

    if not csv_path.exists():
        print(f"[normalize] NOTE: {csv_path.name} not found — party/description data will be blank.")
        return candidates, measures

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            race    = (row.get("Race/Measure name") or "").strip()
            name    = (row.get("Candidate Name/Measure Juristiction") or "").strip()
            desc    = (row.get("Profession/Description") or "").strip()
            party   = (row.get("Party") or "").strip()
            county  = (row.get("County") or "").strip().replace(" County", "").strip()

            if _MEASURE_PAT.search(race):
                # Ballot measure — store description and jurisdiction.
                measures[_key(county, race)] = {
                    "description":  desc,
                    "jurisdiction": name,
                }
                # Also index by first two words and short prefix for flexible matching.
                short = " ".join(race.split()[:2])
                measures.setdefault(_key(county, short), {"description": desc, "jurisdiction": name})
            else:
                # Candidate — store party and profession.
                if name:
                    candidates[_key(county, race, name)] = {
                        "party":      party,
                        "profession": desc,
                    }

    print(f"[normalize] Loaded roster: {len(candidates)} candidates, {len(measures)} measures.")
    return candidates, measures


# Load once at module level so every normalize() call shares the same data.
_CANDIDATE_LOOKUP, _MEASURE_LOOKUP = _load_local_races(_LOCAL_RACES_CSV)



# Party abbreviations that Clarity embeds at the start of candidate names,
# e.g. "DEM BETTY T. YEE" or "NPP MARGARET TROWE".
_PARTY_PREFIXES = {
    "DEM": "Democrat",
    "REP": "Republican",
    "NPP": "No Party Preference",
    "LIB": "Libertarian",
    "GRN": "Green",
    "PFR": "Peace and Freedom",
    "PF":  "Peace and Freedom",   # Napa PDF uses PF, SoS uses P&F
    "AI":  "American Independent",
    "IND": "Independent",
}


def _split_party_prefix(name: str) -> tuple[str, str]:
    """
    If the candidate name starts with a known party abbreviation (e.g. 'DEM'),
    return (party_full_name, clean_name_without_prefix).
    Otherwise return ('', original_name).
    """
    parts = name.strip().split(None, 1)  # split on first whitespace only
    if len(parts) == 2 and parts[0].upper() in _PARTY_PREFIXES:
        return _PARTY_PREFIXES[parts[0].upper()], parts[1].strip()
    return "", name.strip()


def _strip_party_suffix(name: str) -> tuple[str, str]:
    """
    If the candidate name ends with a known party abbreviation (e.g. 'DEM'),
    return (party_full_name, clean_name_without_suffix).
    Otherwise return ('', original_name).

    Handles the Napa PDF format where party follows the name:
      'SALLY J. LIEBER DEM' → ('Democrat', 'SALLY J. LIEBER')
    """
    parts = name.strip().rsplit(None, 1)  # split on last whitespace only
    if len(parts) == 2 and parts[1].upper().rstrip(".") in _PARTY_PREFIXES:
        return _PARTY_PREFIXES[parts[1].upper().rstrip(".")], parts[0].strip()
    return "", name.strip()


# Words that should stay uppercase inside a Title-Cased name.
_KEEP_UPPER = {
    "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII", "XIII",
}


def _normalize_name(name: str) -> str:
    """
    Convert an all-uppercase candidate name to Title Case.

    Only modifies names that are entirely uppercase — names from counties
    that already post proper casing (Alameda, Marin, Santa Clara, Santa Cruz)
    are left completely untouched.

    Handles:
      - Quoted nicknames:   MICHAEL "MIKE" SILVA  → Michael "Mike" Silva
      - Hyphenated names:   SMITH-JONES           → Smith-Jones
      - Apostrophes:        O'BRIEN               → O'Brien
      - Periods:            TONY K. THURMOND      → Tony K. Thurmond
      - Roman numerals:     JOHN DOE III          → John Doe III  (kept upper)
      - Write-in:           WRITE-IN              → Write-In
    """
    if not name:
        return name
    # Leave names that already have mixed case exactly as the county posted them.
    if name != name.upper():
        return name
    # Apply Python's title() as the base — correctly handles apostrophes,
    # hyphens, and quoted substrings.
    result = name.title()
    # Re-uppercase any Roman numerals that title() lowercased.
    fixed = []
    for word in result.split():
        bare = word.strip('"\'.,()')
        if bare.upper() in _KEEP_UPPER:
            fixed.append(word.upper())
        else:
            fixed.append(word)
    return " ".join(fixed)


def _enrich_choices(choices: list, county: str, race_title: str) -> list:
    """
    Add party, profession, and normalized display name to each candidate choice.

    Processing order:
    1. Strip trailing party suffix  (Napa: 'SALLY J. LIEBER DEM')
    2. Strip leading party prefix   (Clarity: 'DEM BETTY T. YEE')
    3. Normalize name to Title Case (only if currently all-uppercase)
    4. Roster lookup for party / profession using the clean name
    """
    def _key(*parts):
        return re.sub(r"\s+", " ", " ".join(str(p or "").strip().lower() for p in parts)).strip()

    county_clean = county.replace("_", " ")
    enriched = []
    for choice in choices:
        raw_name = choice.get("name", "")

        # 1. Strip trailing party suffix (Napa format).
        suffix_party, name_no_suffix = _strip_party_suffix(raw_name)

        # 2. Strip leading party prefix (Clarity format).
        prefix_party, clean_name = _split_party_prefix(name_no_suffix)

        party_from_name = prefix_party or suffix_party

        # 3. Normalize to Title Case (no-op for counties already using proper case).
        display_name = _normalize_name(clean_name)

        # 4. Roster lookup — uses lowercase key so case doesn't matter.
        info = (
            _CANDIDATE_LOOKUP.get(_key(county_clean, race_title, clean_name))
            or _CANDIDATE_LOOKUP.get(_key(county_clean, race_title, raw_name))
            or {}
        )

        enriched.append({
            **choice,
            "name":       display_name,
            "party":      party_from_name or info.get("party", ""),
            "profession": info.get("profession", ""),
        })
    return enriched


def _measure_info(county: str, race_title: str) -> dict:
    """
    Look up description and jurisdiction for a ballot measure.
    Tries progressively shorter versions of the title to handle cases where
    the scraper returns a longer/different form than the local_races CSV.
    """
    def _key(*parts):
        return re.sub(r"\s+", " ", " ".join(str(p or "").strip().lower() for p in parts)).strip()

    county_clean = county.replace("_", " ")
    words = race_title.split()

    # Try: full title, first 4 words, first 3 words, first 2 words, first word only.
    candidates = [
        race_title,
        " ".join(words[:4]),
        " ".join(words[:3]),
        " ".join(words[:2]),
        words[0] if words else "",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        info = _MEASURE_LOOKUP.get(_key(county_clean, candidate))
        if info:
            return info
    return {}


def _is_measure(race_title: str) -> bool:
    """Return True if the race title looks like a ballot measure."""
    return bool(_MEASURE_PAT.search(race_title))


def _enrich_contests(contests: list, county: str) -> list:
    """
    Add party, profession, is_measure, and measure description/jurisdiction
    to every contest and choice. Called after parsing regardless of whether
    data came from a JSON file or all_counties.csv.
    """
    enriched = []
    for contest in contests:
        title      = contest.get("title", "")
        choices    = contest.get("choices") or []
        is_measure = _is_measure(title)
        measure    = _measure_info(county, title) if is_measure else {}

        enriched_choices = (
            choices if is_measure
            else _enrich_choices(choices, county, title)
        )

        enriched.append({
            **contest,
            "is_measure":           is_measure,
            "measure_description":  measure.get("description", ""),
            "measure_jurisdiction": measure.get("jurisdiction", ""),
            "choices":              enriched_choices,
        })
    return enriched


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
        if not title:
            continue

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
        if not title:
            continue

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


def _finalize_county(data: dict, county: str) -> dict:
    """
    Enrich a county's contests and attach anomalies + scrape_status.

    Shared by every code path (individual JSON, all_counties.csv gap-fill, and
    the no-JSON fallback) so a county is finalized identically no matter which
    source it came from.
    """
    data["contests"] = _enrich_contests(data.get("contests") or [], county)
    anomalies = _detect_anomalies(data)
    data["anomalies"] = anomalies
    data["scrape_status"] = _scrape_status(data, anomalies)
    return data


def _latest_all_counties_csv(input_dir: Path) -> Path | None:
    """Return the newest all_counties*.csv in input_dir, or None if there isn't one."""
    candidates = sorted(
        input_dir.glob("all_counties*.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


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

    # Collect individual county JSON files — keep only the newest file per county.
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
            if filepath.stat().st_mtime > county_files[county].stat().st_mtime:
                county_files[county] = filepath  # always keep the newest

    # If there are no individual JSON files at all, the all_counties.csv is the
    # only source of fresh data — bail out early if it's also missing.
    all_csv = _latest_all_counties_csv(input_dir)
    if not county_files and all_csv is None:
        raise ValueError(f"No county JSON files or all_counties.csv found in {input_dir}")

    if county_files:
        print(f"[normalize] Found files for {len(county_files)} counties: {', '.join(sorted(county_files.keys()))}")

    # Parse the all_counties.csv (the complete all-13-county snapshot that
    # src/run_all.py writes each scrape) up front so we can compare its freshness
    # against any individual JSON files county-by-county.
    csv_parsed: dict[str, dict] = {}
    csv_mtime = 0.0
    if all_csv is not None:
        csv_mtime = all_csv.stat().st_mtime
        try:
            csv_parsed = _parse_all_counties_csv(all_csv)
            print(f"[normalize] Read {len(csv_parsed)} counties from {all_csv.name}")
        except Exception as e:
            print(f"[normalize] Warning: could not parse {all_csv.name}: {e}")
            csv_parsed = {}

    # Decide, per county, which source is freshest.  Both the individual JSON
    # files and the all_counties.csv come from scrapers, but either one can be a
    # stale leftover from an earlier run, so we pick by modification time rather
    # than blanket-preferring one format.  This keeps the complete data (the CSV
    # carries every contest) instead of letting a thin/old JSON override it.
    all_county_names = set(county_files) | set(csv_parsed)
    master_counties: dict = {}

    for county in sorted(all_county_names):
        json_path = county_files.get(county)
        json_mtime = json_path.stat().st_mtime if json_path else -1.0
        has_csv = county in csv_parsed

        use_json = json_path is not None and (not has_csv or json_mtime >= csv_mtime)

        if use_json:
            try:
                with open(json_path, encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception as e:
                print(f"[normalize] ERROR reading {json_path}: {e}")
                use_json = False
            else:
                county_data = (
                    _normalize_clarity(raw, county) if _is_clarity_format(raw)
                    else _normalize_non_clarity(raw, county)
                )
                print(f"[normalize] {county}: using JSON {json_path.name}")

        if not use_json:
            # Fall back to the CSV snapshot for this county.
            county_data = dict(csv_parsed[county])
            print(f"[normalize] {county}: using {all_csv.name}")

        county_data = _finalize_county(county_data, county)
        for flag in county_data["anomalies"]:
            print(f"[normalize] ANOMALY [{county}]: {flag}")
        master_counties[county] = county_data

    # Carry forward any county that had no JSON file this run by pulling its
    # last known data from the existing master JSON.  This keeps all 13 counties
    # on the dashboard even when a single scraper fails or a county hasn't posted
    # results yet.  Carried-forward counties are flagged with scrape_status=STALE
    # so editors can see at a glance that the data isn't fresh.
    if output_path.exists():
        try:
            with open(output_path, encoding="utf-8") as f:
                prev_master = json.load(f)
            prev_counties = prev_master.get("counties", {})
            carried = []
            for county in KNOWN_COUNTIES:
                if county not in master_counties and county in prev_counties:
                    stale = dict(prev_counties[county])
                    stale["scrape_status"] = "STALE"
                    stale.setdefault("anomalies", [])
                    if "STALE: no new data this run" not in stale["anomalies"]:
                        stale["anomalies"] = ["STALE: no new data this run"] + stale["anomalies"]
                    master_counties[county] = stale
                    carried.append(county)
            if carried:
                print(f"[normalize] Carried forward (no new scrape data): {', '.join(carried)}")
        except Exception as e:
            print(f"[normalize] Warning: could not load previous master JSON for carry-forward: {e}")

    # Build the master structure.
    master = {
        "pipeline_timestamp": datetime.now(tz=_PACIFIC).isoformat(),
        "county_count": len(master_counties),
        "counties": master_counties,
    }

    # Write to disk.
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(master, f, indent=2, ensure_ascii=False)

    print(f"[normalize] Master JSON written to: {output_path}")

    # Print a quick summary to the terminal.
    ok_count    = sum(1 for d in master_counties.values() if d.get("scrape_status") == "OK")
    warn_count  = sum(1 for d in master_counties.values() if d.get("scrape_status") == "WARN")
    fail_count  = sum(1 for d in master_counties.values() if d.get("scrape_status") == "FAIL")
    stale_count = sum(1 for d in master_counties.values() if d.get("scrape_status") == "STALE")
    print(f"[normalize] Summary: {ok_count} OK / {warn_count} WARN / {fail_count} FAIL / {stale_count} STALE")

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
