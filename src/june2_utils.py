"""
Shared utilities for June 2, 2026 ballot preview scrapers.
Handles cross-county candidate profession lookup, CSV management,
and uncontested-races dropdown rendering.
"""

import csv
import html as _html
import re
from pathlib import Path

_ROOT = Path(__file__).parent.parent
DESCRIPTIONS_CSV = _ROOT / "election_data" / "June 2 Measure Descriptions - Sheet2.csv"
FIELDNAMES = ["measure_title", "jurisdiction", "description", "County"]

LOCAL_RACES_CSV = _ROOT / "election_data" / "local_races - Sheet1.csv"
UNCONTESTED_CSV = _ROOT / "election_data" / "uncontested_races - Sheet1.csv"
# Note: column name matches the (misspelled) header in the CSV exactly
LOCAL_RACES_FIELDNAMES = ["Race/Measure name", "Candidate Name/Measure Juristiction", "Profession/Description", "Party", "County"]
UNCONTESTED_FIELDNAMES = ["Race", "Candidate Name", "Profession", "Party", "County"]


# ---------------------------------------------------------------------------
# CSS for uncontested dropdown — inject into EXTRA_CSS in each scraper
# ---------------------------------------------------------------------------

PARTY_BADGE_CSS = """
    .candidate-party {
      width: 30px; height: 30px;
      font-size: 11px;
      position: relative;
      cursor: default;
    }
    .candidate-party::after {
      content: attr(data-party);
      position: absolute;
      bottom: calc(100% + 5px);
      left: 0;
      background: rgba(26,54,104,0.92);
      color: white;
      font-size: 11px;
      font-weight: 400;
      white-space: nowrap;
      padding: 4px 9px;
      border-radius: 4px;
      pointer-events: none;
      opacity: 0;
      transition: opacity 0.15s ease;
      z-index: 20;
    }
    .candidate-party:hover::after { opacity: 1; }
    .party-na { background: #e8e8e8; color: #666; font-size: 8px; }
    .party-i  { background: #e8e8e8; color: #666; }
"""

UNCONTESTED_CSS = """
    .uncontested-wrap {
      margin-bottom: 16px;
      border-radius: 5px;
      overflow: hidden;
      border: 1px solid #1a3668;
    }
    .uncontested-toggle {
      background-color: #1a3668;
      color: white;
      padding: 10px 16px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      cursor: pointer;
      user-select: none;
    }
    .uncontested-toggle-label { font-size: 0.92em; font-weight: 700; letter-spacing: 0.04em; }
    .uncontested-toggle-count {
      font-size: 0.82em; color: #accf00; margin-left: 8px;
      font-weight: 400; text-transform: none; letter-spacing: 0;
    }
    .uncontested-chevron { transition: transform 0.2s ease; flex-shrink: 0; }
    .uncontested-chevron.open { transform: rotate(180deg); }
    .uncontested-body { display: none; background: white; }
    .uncontested-body.open { display: block; }
    .uncontested-row {
      display: flex; align-items: center; justify-content: space-between;
      padding: 10px 16px; border-bottom: 1px solid #f0e68c; gap: 12px;
      background: #fffde7;
    }
    .uncontested-row:last-child { border-bottom: none; }
    .uncontested-race-name { font-size: 0.88em; font-weight: 700; color: #1a3668; flex: 1; }
    .uncontested-candidate { font-size: 0.85em; color: #444; flex: 1; }
    .uncontested-profession { font-size: 0.78em; color: #888; flex: 1; font-style: italic; }
    .uncontested-badge {
      font-size: 0.72em; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase;
      background: #f2f7d9; color: #4a6020; border: 1px solid #c8e050;
      padding: 2px 8px; border-radius: 4px; flex-shrink: 0;
    }
"""

# ---------------------------------------------------------------------------
# Shared embed CSS + JS for the new lnm-ballot-widget format
# ---------------------------------------------------------------------------

SHARED_STYLE_BLOCK = """<style>
  .lnm-ballot-widget, .lnm-ballot-widget *, .lnm-ballot-widget *::before, .lnm-ballot-widget *::after {
    box-sizing: border-box;
  }
  .lnm-ballot-widget {
    font-family: Arial, sans-serif;
    margin: 0;
  }
  .lnm-ballot-widget .race-box {
    background-color: white;
    border: 1px solid #ddd;
    border-radius: 5px;
    margin-bottom: 20px;
    overflow: hidden;
  }
  .lnm-ballot-widget .race-title {
    background-color: #1a3668;
    color: white;
    padding: 10px 14px;
    font-weight: bold;
    font-size: 1em;
  }
  .lnm-ballot-widget .search-wrap { margin-bottom: 16px; position: relative; }
  .lnm-ballot-widget .search-input {
    width: 100%;
    padding: 10px 16px;
    font-size: 15px;
    border: 1px solid #ccc;
    border-radius: 6px;
    background: white;
    outline: none;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }
  .lnm-ballot-widget .search-input:focus {
    border-color: #1a3668;
    box-shadow: 0 0 0 2px rgba(26,54,104,0.15);
  }
  .lnm-ballot-widget .no-results {
    display: none;
    text-align: center;
    color: #888;
    font-size: 14px;
    padding: 24px 0;
  }
  .lnm-ballot-widget .preview-banner {
    background: #1a3668;
    color: white;
    text-align: center;
    padding: 14px 16px;
    border-radius: 5px;
    margin-bottom: 20px;
  }
  .lnm-ballot-widget .preview-banner-title {
    font-size: 1.15em;
    font-weight: 700;
    letter-spacing: 0.04em;
  }
  .lnm-ballot-widget .preview-banner-sub {
    font-size: 0.85em;
    color: #accf00;
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .lnm-ballot-widget .preview-candidate-list { padding: 4px 0; }
  .lnm-ballot-widget .preview-candidate {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 10px 14px;
    border-top: 1px solid #eee;
  }
  .lnm-ballot-widget .preview-candidate:first-child { border-top: none; }
  .lnm-ballot-widget .preview-candidate-info { display: flex; flex-direction: column; }
  .lnm-ballot-widget .preview-candidate-name {
    font-weight: 700;
    font-size: 0.92em;
    color: #333;
  }
  .lnm-ballot-widget .preview-candidate-designation {
    font-size: 0.82em;
    color: #666;
    margin-top: 2px;
  }
  .lnm-ballot-widget .candidate-party {
    width: 30px; height: 30px;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 11px;
    font-weight: 700;
    flex-shrink: 0;
    position: relative;
    cursor: default;
  }
  .lnm-ballot-widget .candidate-party::after {
    content: attr(data-party);
    position: absolute;
    bottom: calc(100% + 5px);
    left: 0;
    background: rgba(26,54,104,0.92);
    color: white;
    font-size: 11px;
    font-weight: 400;
    white-space: nowrap;
    padding: 4px 9px;
    border-radius: 4px;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.15s ease;
    z-index: 20;
  }
  .lnm-ballot-widget .candidate-party:hover::after { opacity: 1; }
  .lnm-ballot-widget .party-d { background: #dceef4; color: #1a6e8a; }
  .lnm-ballot-widget .party-r { background: #faeee7; color: #b85c2a; }
  .lnm-ballot-widget .party-i { background: #e8e8e8; color: #666; }
  .lnm-ballot-widget .party-na { background: #e8e8e8; color: #666; font-size: 8px; }
  .lnm-ballot-widget .party-n { background: #e8f0e8; color: #3a6e3a; }
  .lnm-ballot-widget .party-g { background: #e8f0e8; color: #2e7d32; }
  .lnm-ballot-widget .party-l { background: #fff3e0; color: #e65100; }
  .lnm-ballot-widget .party-a { background: #fce4ec; color: #880e4f; }
  .lnm-ballot-widget .party-p { background: #f3e5f5; color: #6a1b9a; }
  .lnm-ballot-widget .preview-measure-list { padding: 4px 0; }
  .lnm-ballot-widget .preview-measure {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 10px 14px;
    border-top: 1px solid #eee;
  }
  .lnm-ballot-widget .preview-measure:first-child { border-top: none; }
  .lnm-ballot-widget .preview-measure-badge {
    font-size: 0.78em;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    background: #1a3668;
    color: #accf00;
    padding: 3px 8px;
    border-radius: 4px;
    flex-shrink: 0;
    width: 130px;
    box-sizing: border-box;
    text-align: center;
    margin-top: 1px;
  }
  .lnm-ballot-widget .preview-measure-body { display: flex; flex-direction: column; gap: 4px; }
  .lnm-ballot-widget .preview-measure-jurisdiction {
    font-size: 0.92em;
    font-weight: 700;
    color: #1a3668;
  }
  .lnm-ballot-widget .preview-measure-description {
    font-size: 0.82em;
    color: #555;
    line-height: 1.5;
  }
  .lnm-ballot-widget .uncontested-wrap {
    margin-bottom: 16px;
    border-radius: 5px;
    overflow: hidden;
    border: 1px solid #1a3668;
  }
  .lnm-ballot-widget .uncontested-toggle {
    background-color: #1a3668;
    color: white;
    padding: 10px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    cursor: pointer;
    user-select: none;
  }
  .lnm-ballot-widget .uncontested-toggle-label { font-size: 0.92em; font-weight: 700; letter-spacing: 0.04em; }
  .lnm-ballot-widget .uncontested-toggle-count {
    font-size: 0.82em; color: #accf00; margin-left: 8px;
    font-weight: 400; text-transform: none; letter-spacing: 0;
  }
  .lnm-ballot-widget .uncontested-chevron { transition: transform 0.2s ease; flex-shrink: 0; }
  .lnm-ballot-widget .uncontested-chevron.open { transform: rotate(180deg); }
  .lnm-ballot-widget .uncontested-body { display: none; background: white; }
  .lnm-ballot-widget .uncontested-body.open { display: block; }
  .lnm-ballot-widget .uncontested-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 16px; border-bottom: 1px solid #f0e68c; gap: 12px;
    background: #fffde7;
  }
  .lnm-ballot-widget .uncontested-row:last-child { border-bottom: none; }
  .lnm-ballot-widget .uncontested-race-name { font-size: 0.88em; font-weight: 700; color: #1a3668; flex: 1; }
  .lnm-ballot-widget .uncontested-candidate { font-size: 0.85em; color: #444; flex: 1; }
  .lnm-ballot-widget .uncontested-profession { font-size: 0.78em; color: #888; flex: 1; font-style: italic; }
  .lnm-ballot-widget .uncontested-badge {
    font-size: 0.72em; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase;
    background: #f2f7d9; color: #4a6020; border: 1px solid #c8e050;
    padding: 2px 8px; border-radius: 4px; flex-shrink: 0;
  }
</style>"""

SHARED_SCRIPT = """<script>
document.addEventListener('DOMContentLoaded', function() {
  var _ut = document.getElementById("uncontestedToggle");
  var _ub = document.getElementById("uncontestedBody");
  var _uc = document.getElementById("uncontestedChevron");
  if (_ut) {
    _ut.addEventListener("click", function() {
      _ub.classList.toggle("open");
      _uc.classList.toggle("open");
    });
  }
  var input = document.getElementById("electionSearch");
  var noResults = document.getElementById("noResults");
  if (!input) return;
  input.value = '';
  function runFilter() {
    var q = input.value.trim().toLowerCase();
    var boxes = document.querySelectorAll(".lnm-ballot-widget .race-box");
    var visible = 0;
    boxes.forEach(function(box) {
      var show = !q || box.textContent.toLowerCase().indexOf(q) !== -1;
      box.style.display = show ? "" : "none";
      if (show) visible++;
    });
    if (noResults) noResults.style.display = (q && visible === 0) ? "block" : "none";
  }
  input.addEventListener("input", runFilter);
});
</script>"""

# JS snippet to wire up the toggle — combine with each scraper's search JS
UNCONTESTED_TOGGLE_JS = (
    'var _ut=document.getElementById("uncontestedToggle");'
    'var _ub=document.getElementById("uncontestedBody");'
    'var _uc=document.getElementById("uncontestedChevron");'
    'if(_ut){_ut.addEventListener("click",function(){'
    '_ub.classList.toggle("open");_uc.classList.toggle("open");});}'
)


# ---------------------------------------------------------------------------
# String helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase, collapse whitespace."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _norm_race(text: str) -> str:
    """Aggressive normalization for race-name fuzzy matching (strips commas/periods)."""
    t = (text or "").lower().strip()
    t = re.sub(r"[,.]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def _county_match(csv_value: str, query: str) -> bool:
    """
    Compare county names, ignoring a trailing ' County' suffix on either side.
    Handles CSVs that store 'Alameda County' when the scraper passes 'Alameda'.
    """
    a = re.sub(r"\s+county$", "", csv_value.strip().lower())
    b = re.sub(r"\s+county$", "", query.strip().lower())
    return a == b


def _is_measure_name(name: str) -> bool:
    """Return True if the name looks like a ballot measure title."""
    n = _norm_race(name)
    return n.startswith(("measure", "bond measure", "bond ", "proposition"))


# ---------------------------------------------------------------------------
# Existing June-2 descriptions CSV (cross-county candidate professions)
# ---------------------------------------------------------------------------

def load_measure_descriptions(county: str) -> dict[str, dict]:
    """Return {normalized_label: {description, jurisdiction}} for the given county."""
    data: dict[str, dict] = {}
    csv_path = DESCRIPTIONS_CSV.resolve()
    if not csv_path.exists():
        return data
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("County", "").strip().lower() != county.lower():
                continue
            label = row.get("measure_title", "").strip()
            if label:
                data[label.lower()] = {
                    "description": row.get("description", "").strip(),
                    "jurisdiction": row.get("jurisdiction", "").strip(),
                }
    return data


def load_all_professions() -> dict[tuple, str]:
    """Return {(normalized_name, normalized_race): profession} across ALL counties."""
    data: dict[tuple, str] = {}
    csv_path = DESCRIPTIONS_CSV.resolve()
    if not csv_path.exists():
        return data
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = row.get("measure_title", "").strip()
            race = row.get("jurisdiction", "").strip()
            desc = row.get("description", "").strip()
            if not name or not race or not desc:
                continue
            if desc.lower().startswith(("shall ", "to ", "whether ")):
                continue
            key = (_normalize(name), _normalize(race))
            if key not in data:
                data[key] = desc
    return data


def save_candidate_professions(races: list[dict], county: str) -> int:
    """
    Append non-N/A candidate professions to the shared CSV.
    Skips rows already present (same name + race + county).
    Returns count of rows added.
    """
    csv_path = DESCRIPTIONS_CSV.resolve()

    existing_keys: set[tuple] = set()
    existing_rows: list[dict] = []

    if csv_path.exists():
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing_rows.append(row)
                existing_keys.add((
                    _normalize(row.get("measure_title", "")),
                    _normalize(row.get("jurisdiction", "")),
                    _normalize(row.get("County", "")),
                ))

    new_rows: list[dict] = []
    for race in races:
        race_name = race["race"]
        for c in race["candidates"]:
            designation = (c.get("designation") or "").strip()
            if not designation or designation == "N/A":
                continue
            key = (_normalize(c["name"]), _normalize(race_name), _normalize(county))
            if key not in existing_keys:
                new_rows.append({
                    "measure_title": c["name"],
                    "jurisdiction": race_name,
                    "description": designation,
                    "County": county,
                })
                existing_keys.add(key)

    if new_rows:
        all_rows = existing_rows + new_rows
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_rows)

    return len(new_rows)


def fill_missing_professions(races: list[dict], all_professions: dict[tuple, str]) -> int:
    """
    For any candidate with designation 'N/A', look up in the cross-county CSV.
    Mutates the races list in place. Returns count of professions filled.
    """
    filled = 0
    for race in races:
        race_name = _normalize(race["race"])
        for c in race["candidates"]:
            if c.get("designation") == "N/A":
                key = (_normalize(c["name"]), race_name)
                if key in all_professions:
                    c["designation"] = all_professions[key]
                    filled += 1
    return filled


# ---------------------------------------------------------------------------
# local_races.csv — contested races / measures manifest
# ---------------------------------------------------------------------------

def _norm_row(row: dict) -> dict:
    """Strip trailing/leading whitespace from all keys (handles CSV headers with accidental spaces)."""
    return {k.strip(): v for k, v in row.items()}


def load_local_race_filter(county: str) -> tuple[list[str], list[str]]:
    """
    Return (race_names, measure_names) for the given county from local_races.csv.
    race_names  — distinct contested-race names to include
    measure_names — measure labels to include
    """
    race_names: list[str] = []
    measure_names: list[str] = []
    if not LOCAL_RACES_CSV.exists():
        return race_names, measure_names
    seen_races: set[str] = set()
    seen_measures: set[str] = set()
    with open(LOCAL_RACES_CSV, newline="", encoding="utf-8") as f:
        for row in (_norm_row(r) for r in csv.DictReader(f)):
            if not _county_match(row.get("County", ""), county):
                continue
            name = row.get("Race/Measure name", "").strip()
            if not name:
                continue
            if _is_measure_name(name):
                k = _norm_race(name)
                if k not in seen_measures:
                    measure_names.append(name)
                    seen_measures.add(k)
            else:
                k = _norm_race(name)
                if k not in seen_races:
                    race_names.append(name)
                    seen_races.add(k)
    return race_names, measure_names


def is_local_races_populated(county: str) -> bool:
    """Return True if local_races.csv has any candidate rows for this county."""
    if not LOCAL_RACES_CSV.exists():
        return False
    with open(LOCAL_RACES_CSV, newline="", encoding="utf-8") as f:
        for row in (_norm_row(r) for r in csv.DictReader(f)):
            if not _county_match(row.get("County", ""), county):
                continue
            name = row.get("Race/Measure name", "").strip()
            candidate = row.get("Candidate Name/Measure Juristiction", "").strip()
            if not _is_measure_name(name) and candidate:
                return True
    return False


def load_local_races_for_html(county: str) -> tuple[list[dict], list[dict]]:
    """
    Read local_races.csv for this county and return (races, measures).

    races:    [{race: str, candidates: [{name, designation, party}]}]
    measures: [{label: str, jurisdiction: str, description: str}]
    """
    race_groups: dict[str, list] = {}
    race_order: list[str] = []
    measures: list[dict] = []

    if not LOCAL_RACES_CSV.exists():
        return [], []

    with open(LOCAL_RACES_CSV, newline="", encoding="utf-8") as f:
        for row in (_norm_row(r) for r in csv.DictReader(f)):
            if not _county_match(row.get("County", ""), county):
                continue
            name = row.get("Race/Measure name", "").strip()
            col2 = row.get("Candidate Name/Measure Juristiction", "").strip()
            col3 = row.get("Profession/Description", "").strip()

            if _is_measure_name(name):
                measures.append({"label": name, "jurisdiction": col2, "description": col3})
            elif col2:
                if name not in race_groups:
                    race_groups[name] = []
                    race_order.append(name)
                race_groups[name].append({
                    "name": col2,
                    "designation": col3,
                    "party": row.get("Party", "").strip(),
                })

    races = [{"race": r, "candidates": race_groups[r]} for r in race_order]
    return races, measures


def populate_local_races(
    scraped_races: list[dict],
    county: str,
    race_filter: list[str],
    dry_run: bool = True,
) -> int:
    """
    Add candidate rows from scraped_races to local_races.csv.
    Only races whose normalized name matches something in race_filter are written.
    In dry_run mode, prints what would be written without saving.
    Returns count of rows added.
    """
    norm_filter = {_norm_race(r) for r in race_filter}

    existing_rows: list[dict] = []
    existing_keys: set[tuple] = set()

    if LOCAL_RACES_CSV.exists():
        with open(LOCAL_RACES_CSV, newline="", encoding="utf-8") as f:
            for row in (_norm_row(r) for r in csv.DictReader(f)):
                existing_rows.append(row)
                race = row.get("Race/Measure name", "").strip()
                candidate = row.get("Candidate Name/Measure Juristiction", "").strip()
                if candidate and not _is_measure_name(race):
                    existing_keys.add((
                        _normalize(race),
                        _normalize(candidate),
                        _normalize(row.get("County", "")),
                    ))

    new_rows: list[dict] = []
    for race in scraped_races:
        race_name = race["race"]
        if _norm_race(race_name) not in norm_filter:
            continue
        for c in race["candidates"]:
            key = (_normalize(race_name), _normalize(c["name"]), _normalize(county))
            if key not in existing_keys:
                new_rows.append({
                    "Race/Measure name": race_name,
                    "Candidate Name/Measure Juristiction": c["name"],
                    "Profession/Description": c.get("designation", ""),
                    "Party": c.get("party", ""),
                    "County": county,
                })
                existing_keys.add(key)

    if dry_run:
        print(f"\n  [preview] {len(new_rows)} rows to add to local_races.csv ({county}):")
        for r in new_rows:
            print(f"    {r['Race/Measure name']} | {r['Candidate Name/Measure Juristiction']} | {r['Profession/Description']}")
        return len(new_rows)

    if not new_rows:
        return 0

    # Remove empty placeholder rows for races we are now populating
    races_added = {_normalize(r["Race/Measure name"]) for r in new_rows}
    filtered_existing = []
    for row in existing_rows:
        r = _norm_row(row)
        if (
            _county_match(r.get("County", ""), county)
            and not _is_measure_name(r.get("Race/Measure name", ""))
            and not r.get("Candidate Name/Measure Juristiction", "").strip()
            and _normalize(r.get("Race/Measure name", "")) in races_added
        ):
            continue  # drop empty placeholder
        filtered_existing.append(r)  # store normalised row so keys match fieldnames

    all_rows = filtered_existing + new_rows
    with open(LOCAL_RACES_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOCAL_RACES_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    return len(new_rows)


# ---------------------------------------------------------------------------
# uncontested_races.csv
# ---------------------------------------------------------------------------

def load_uncontested_races(county: str) -> list[dict]:
    """Return [{race, candidate_name, profession}] from uncontested_races.csv."""
    result: list[dict] = []
    if not UNCONTESTED_CSV.exists():
        return result
    with open(UNCONTESTED_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not _county_match(row.get("County", ""), county):
                continue
            result.append({
                "race": row.get("Race", "").strip(),
                "candidate_name": row.get("Candidate Name", "").strip(),
                "profession": row.get("Profession", "").strip(),
                "party": row.get("Party", "").strip(),
            })
    return result


def is_uncontested_populated(county: str) -> bool:
    """Return True if uncontested_races.csv has candidate data for this county."""
    return any(e["candidate_name"] for e in load_uncontested_races(county))


def populate_uncontested(
    data: list[dict],
    county: str,
    dry_run: bool = True,
) -> int:
    """
    Write candidate names/professions into the uncontested_races.csv rows for county.
    data: [{race, candidate_name, profession}]
    Matches by race name (handles duplicate names by position).
    Returns count of rows updated.
    """
    if not UNCONTESTED_CSV.exists():
        return 0

    all_rows: list[dict] = []
    with open(UNCONTESTED_CSV, newline="", encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))

    # Build lookup: norm_race -> ordered list of (candidate_name, profession)
    by_race: dict[str, list[tuple]] = {}
    for d in data:
        k = _norm_race(d["race"])
        if k not in by_race:
            by_race[k] = []
        by_race[k].append((d["candidate_name"], d["profession"], d.get("party", "")))

    used: dict[str, int] = {}
    updated = 0

    for row in all_rows:
        if not _county_match(row.get("County", ""), county):
            continue

        race = row.get("Race", "").strip()
        norm = _norm_race(race)

        # Find all keys that could match this CSV race name, then pick
        # the first one that still has unused candidates.
        # This handles duplicates like "Superior Court Judge" x5 vs
        # "Superior Court Judge (Office 1/2/3/4/5)".
        matching_keys = []
        if norm in by_race:
            matching_keys = [norm]
        else:
            for k in by_race:
                if norm.startswith(k + " ") or k.startswith(norm + " "):
                    matching_keys.append(k)

        matched_key = None
        for mk in matching_keys:
            if used.get(mk, 0) < len(by_race[mk]):
                matched_key = mk
                break

        if matched_key is None:
            continue

        # Always consume the slot so positional distribution stays correct
        # even when some rows are already filled.
        idx = used.get(matched_key, 0)
        used[matched_key] = idx + 1

        if row.get("Candidate Name", "").strip():
            continue  # already populated — slot consumed but no write needed

        candidates = by_race[matched_key]
        name, prof, party = candidates[idx]
        if dry_run:
            print(f"    {race} | {name} | {prof} | {party}")
        else:
            row["Candidate Name"] = name
            row["Profession"] = prof
            row["Party"] = party
        updated += 1

    if not dry_run and updated > 0:
        with open(UNCONTESTED_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=UNCONTESTED_FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_rows)

    return updated


# ---------------------------------------------------------------------------
# Uncontested dropdown HTML renderer
# ---------------------------------------------------------------------------

def render_uncontested_dropdown(uncontested: list[dict]) -> str:
    """
    Generate the collapsed uncontested-races dropdown block.
    uncontested: [{race, candidate_name, profession}]
    Returns empty string if the list is empty.
    """
    if not uncontested:
        return ""

    count = len(uncontested)
    count_label = f"{count} race{'s' if count != 1 else ''} with no opposition"

    rows_html: list[str] = []
    for u in uncontested:
        candidate_html = (
            f'<div class="uncontested-candidate">{_html.escape(u["candidate_name"])}</div>'
            if u["candidate_name"]
            else '<div class="uncontested-candidate">—</div>'
        )
        profession_html = (
            f'<div class="uncontested-profession">{_html.escape(u["profession"])}</div>'
            if u.get("profession")
            else '<div class="uncontested-profession"></div>'
        )
        rows_html.append(
            '<div class="uncontested-row">'
            f'<div class="uncontested-race-name">{_html.escape(u["race"])}</div>'
            + candidate_html
            + profession_html
            + '<div class="uncontested-badge">Uncontested</div>'
            "</div>"
        )

    # Use native <details>/<summary> so the toggle works without JavaScript —
    # WordPress and other CMSes strip <script> tags, which broke the old JS toggle.
    return (
        '<details class="uncontested-wrap">'
        '<summary class="uncontested-toggle">'
        "<span>"
        '<span class="uncontested-toggle-label">Uncontested Races</span>'
        f'<span class="uncontested-toggle-count">{_html.escape(count_label)}</span>'
        "</span>"
        '<svg class="uncontested-chevron" width="16" height="16" '
        'viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M4 6L8 10L12 6" stroke="white" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        "</svg>"
        "</summary>"
        '<div class="uncontested-body">'
        + "".join(rows_html)
        + "</div></details>"
    )
