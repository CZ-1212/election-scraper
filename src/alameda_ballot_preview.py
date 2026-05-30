#!/usr/bin/env python3
"""
Alameda County ballot preview scraper for the June 2, 2026 Primary Election.

Scrapes:
  - Candidate list: https://alamedacountyca.gov/rov_app/candidatelist?electionid=259
  - Measures page:  https://acvote.alamedacountyca.gov/election-information/elections?id=259

First run: populates local_races.csv and (if applicable) uncontested_races.csv.
Subsequent runs: reads directly from those CSVs — no re-scraping of candidates.
"""

import re
import html as _html
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from june2_utils import (
    load_measure_descriptions,
    save_candidate_professions,
    load_local_race_filter,
    is_local_races_populated,
    load_local_races_for_html,
    populate_local_races,
    load_uncontested_races,
    is_uncontested_populated,
    render_uncontested_dropdown,
    _norm_race,
    SHARED_STYLE_BLOCK,
    SHARED_SCRIPT,
)

CANDIDATE_URL = "https://alamedacountyca.gov/rov_app/candidatelist?electionid=259"
MEASURES_URL = "https://acvote.alamedacountyca.gov/election-information/elections?id=259"
_ROOT = Path(__file__).parent.parent
OUTPUT_FILE = _ROOT / "data" / "alameda_ballot_preview_2026-06-02.html"

COUNTY = "Alameda"
ELECTION_TITLE = "Alameda County — June 2, 2026 Primary"

PARTY_ABBREV = {
    "democratic": ("D", "party-d"),
    "republican": ("R", "party-r"),
    "no party preference": ("N", "party-n"),
    "non-partisan": ("N", "party-n"),
    "non partisan": ("N", "party-n"),
    "nonpartisan": ("N", "party-n"),
    "green": ("G", "party-g"),
    "libertarian": ("L", "party-l"),
    "american independent": ("A", "party-a"),
    "peace and freedom": ("P", "party-p"),
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _esc(value) -> str:
    return _html.escape(str(value or ""), quote=False)


def _party_badge(party_raw: str) -> tuple[str, str]:
    key = (party_raw or "").strip().lower()
    for k, v in PARTY_ABBREV.items():
        if k in key:
            return v
    return ("NA", "party-na")


def fetch(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


# ---------------------------------------------------------------------------
# Candidate scraping
# ---------------------------------------------------------------------------

def scrape_candidates(soup: BeautifulSoup) -> list[dict]:
    """
    Returns all contested races from the Alameda ROV filing page.
    {'race': str, 'candidates': [{'name', 'party', 'designation'}]}

    Only "Filing Completed" candidates in "On Ballot" races are included.
    """
    races_map: dict[str, list] = {}
    race_order: list[str] = []

    current_race: str | None = None
    current_on_ballot: bool = False

    for div in soup.find_all("div", class_="Info_big"):
        caption = div.find("caption")
        if caption:
            h2 = caption.find("h2", class_="candidate-list-table-header-div")
            if h2:
                header_span = h2.find("span", class_="candidate-list-table-header")
                if header_span:
                    on_ballot = header_span.find(
                        "span", string=re.compile(r"on ballot", re.I)
                    )
                    current_on_ballot = on_ballot is not None
                    full_text = header_span.get_text(separator=" ", strip=True)
                    current_race = re.split(
                        r"\s*-\s*on ballot", full_text, flags=re.I
                    )[0].strip()

        if not current_on_ballot or not current_race:
            continue

        table = div.find("table")
        if not table:
            continue

        for row in table.find_all("tr"):
            if row.find("th"):
                continue
            cells = row.find_all("td")
            if len(cells) < 4:
                continue

            status = cells[3].get_text(strip=True)
            if status.lower() != "filing completed":
                continue

            name_span = cells[0].find("span", class_="candidateName")
            name = name_span.get_text(strip=True) if name_span else ""

            party = ""
            br = cells[0].find("br")
            if br and br.next_sibling:
                party = str(br.next_sibling).strip()

            designation = cells[1].get_text(strip=True)

            if not name:
                continue

            if current_race not in races_map:
                races_map[current_race] = []
                race_order.append(current_race)
            races_map[current_race].append({
                "name": name,
                "party": party,
                "designation": designation,
            })

    return [{"race": r, "candidates": races_map[r]} for r in race_order]


# ---------------------------------------------------------------------------
# Measures scraping
# ---------------------------------------------------------------------------

def scrape_measures(soup: BeautifulSoup) -> list[str]:
    measures = []
    for panel in soup.find_all("div", class_="panel"):
        h3 = panel.find("h3", class_="panel-title")
        if not h3 or "measures" not in h3.get_text(strip=True).lower():
            continue
        body = panel.find("div", class_="panel-body")
        if not body:
            continue
        for a in body.find_all("a"):
            txt = a.get_text(separator=" ", strip=True)
            if txt:
                measures.append(txt)
        break
    return measures


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def _render_candidate_race(race: dict) -> str:
    rows = []
    for c in race["candidates"]:
        letter, css_class = _party_badge(c["party"])
        party_label = c["party"] or "Party Designation Not Available"
        desig_html = (
            f'<div class="preview-candidate-designation">{_esc(c["designation"])}</div>'
            if c["designation"]
            else ""
        )
        rows.append(
            '<div class="preview-candidate">'
            f'<div class="candidate-party {css_class}" data-party="{_esc(party_label)}">'
            f"{_esc(letter)}</div>"
            '<div class="preview-candidate-info">'
            f'<div class="preview-candidate-name">{_esc(c["name"])}</div>'
            + desig_html
            + "</div></div>"
        )
    return (
        '<div class="race-box">'
        f'<div class="race-title">{_esc(race["race"])}</div>'
        '<div class="preview-candidate-list">'
        + "".join(rows)
        + "</div></div>"
    )


def _parse_measure_label_title(measure_text: str) -> tuple[str, str]:
    m = re.match(
        r"^((?:Bond\s+)?(?:Measure|Proposition)\s+\S+)\s*[-–—]\s*(.*)",
        measure_text,
        re.I,
    )
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return measure_text, ""


def _render_measures_block(measures_raw: list[str], descriptions: dict[str, dict] | None = None) -> str:
    rows = []
    for measure_text in measures_raw:
        label, _ = _parse_measure_label_title(measure_text)
        entry = (descriptions or {}).get(label.lower(), {})
        jurisdiction = entry.get("jurisdiction", "")
        desc = entry.get("description", "")
        jurisdiction_html = (
            f'<div class="preview-measure-jurisdiction">{_esc(jurisdiction)}</div>'
            if jurisdiction else ""
        )
        desc_html = (
            f'<div class="preview-measure-description">{_esc(desc)}</div>' if desc else ""
        )
        badge_inner = (
            "Bond<br>" + _esc(label[5:])
            if label.lower().startswith("bond ")
            else _esc(label)
        )
        rows.append(
            '<div class="preview-measure">'
            f'<div class="preview-measure-badge">{badge_inner}</div>'
            '<div class="preview-measure-body">'
            + jurisdiction_html
            + desc_html
            + "</div></div>"
        )
    return (
        '<div class="race-box">'
        '<div class="race-title">Measures on the Ballot</div>'
        '<div class="preview-measure-list">'
        + "".join(rows)
        + "</div></div>"
    )


def _render_csv_measures_block(measures: list[dict]) -> str:
    """Render measures sourced directly from local_races.csv."""
    rows = []
    for m in measures:
        # Parse "Measure A - Full Title" → badge shows only "Measure A"
        short_label, _ = _parse_measure_label_title(m["label"])
        badge_inner = (
            "Bond<br>" + _esc(short_label[5:])
            if short_label.lower().startswith("bond ")
            else _esc(short_label)
        )
        jurisdiction_html = (
            f'<div class="preview-measure-jurisdiction">{_esc(m["jurisdiction"])}</div>'
            if m.get("jurisdiction") else ""
        )
        desc_html = (
            f'<div class="preview-measure-description">{_esc(m["description"])}</div>'
            if m.get("description") else ""
        )
        rows.append(
            '<div class="preview-measure">'
            f'<div class="preview-measure-badge">{badge_inner}</div>'
            '<div class="preview-measure-body">' + jurisdiction_html + desc_html + "</div></div>"
        )
    return (
        '<div class="race-box">'
        '<div class="race-title">Measures on the Ballot</div>'
        '<div class="preview-measure-list">' + "".join(rows) + "</div></div>"
    )


def render_html(races: list[dict], measures: list[dict], uncontested: list[dict]) -> str:
    banner = (
        '<div class="preview-banner">'
        f'<div class="preview-banner-title">{_esc(ELECTION_TITLE)}</div>'
        '<div class="preview-banner-sub">Ballot Preview — Not Election Results</div>'
        "</div>"
    )
    search_bar = (
        '<div class="search-wrap">'
        '<input class="search-input" type="text" autocomplete="off" '
        'placeholder="Search races, candidates, measures…" id="electionSearch">'
        "</div>"
        '<div class="no-results" id="noResults">No results found.</div>'
    )
    uncontested_html = render_uncontested_dropdown(uncontested)
    blocks = [_render_candidate_race(r) for r in races]
    if measures:
        blocks.append(_render_csv_measures_block(measures))
    body = banner + search_bar + uncontested_html + "".join(blocks)
    return SHARED_STYLE_BLOCK + '\n<div class="lnm-ballot-widget">\n' + body + '\n</div>\n' + SHARED_SCRIPT


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    race_filter, _ = load_local_race_filter(COUNTY)
    norm_filter = {_norm_race(r) for r in race_filter}

    # ---- First-run: scrape + preview + confirm ----
    if not is_local_races_populated(COUNTY):
        print(f"First run detected for {COUNTY}. Fetching candidate list...")
        candidate_soup = fetch(CANDIDATE_URL)
        all_races = scrape_candidates(candidate_soup)

        # Filter to only local races defined in local_races.csv
        local_races = [r for r in all_races if _norm_race(r["race"]) in norm_filter]
        total = sum(len(r["candidates"]) for r in local_races)

        print(f"\nFound {len(local_races)} local races, {total} candidates:")
        for r in local_races:
            print(f"  {r['race']}: {len(r['candidates'])} candidates")
            for c in r["candidates"]:
                print(f"    - {c['name']} ({c['designation'] or 'N/A'})")

        answer = input("\nSave candidates to local_races.csv? [y/N]: ").strip().lower()
        if answer == "y":
            added = populate_local_races(local_races, COUNTY, race_filter, dry_run=False)
            print(f"  Saved {added} candidate rows.")
        else:
            print("  Skipped. Re-run to save.")

    # ---- Uncontested: populate if first run ----
    if not is_uncontested_populated(COUNTY):
        uncontested_entries = load_uncontested_races(COUNTY)
        if uncontested_entries:
            # Alameda uncontested candidates aren't yet scraped — user will add manually
            print(f"\n{len(uncontested_entries)} uncontested race(s) in CSV have no candidate data.")
            print("Add candidate names to uncontested_races.csv manually, then re-run.")

    # ---- Load data for rendering ----
    uncontested = load_uncontested_races(COUNTY)

    if is_local_races_populated(COUNTY):
        # Use CSV data (locked candidate names + professions)
        csv_races, csv_measures = load_local_races_for_html(COUNTY)
        print(f"\nLoaded {len(csv_races)} local races, {len(csv_measures)} measures from CSV.")
        races = csv_races
        csv_measures_for_render = csv_measures
        measures_raw = []
        descriptions = None
    else:
        # Fallback: scrape fresh (first run path where user declined to save)
        print(f"\nFetching candidate list from:\n  {CANDIDATE_URL}")
        candidate_soup = fetch(CANDIDATE_URL)
        all_races = scrape_candidates(candidate_soup)
        races = [r for r in all_races if _norm_race(r["race"]) in norm_filter] if norm_filter else all_races
        # Also add statewide races (not in local_races filter)
        statewide = [r for r in all_races if _norm_race(r["race"]) not in norm_filter]
        races = statewide + races

        print(f"\nFetching measures page from:\n  {MEASURES_URL}")
        measures_soup = fetch(MEASURES_URL)
        measures_raw = scrape_measures(measures_soup)
        descriptions = load_measure_descriptions(COUNTY)
        csv_measures_for_render = None

        print(f"\nSaving candidate professions to shared CSV...")
        added = save_candidate_professions(races, COUNTY)
        print(f"  Added {added} new profession rows")

    total = sum(len(r["candidates"]) for r in races)
    print(f"\nRendering HTML: {len(races)} races, {total} candidates, "
          f"{len(uncontested)} uncontested")

    html_out = render_html(races, csv_measures_for_render or [], uncontested)

    OUTPUT_FILE.parent.mkdir(exist_ok=True)
    OUTPUT_FILE.write_text(html_out, encoding="utf-8")
    print(f"\nSaved: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
