#!/usr/bin/env python3
"""
Marin County ballot preview scraper for the June 2, 2026 Primary Election.

Uses Playwright to bypass Cloudflare, then parses the local candidate filing
status page at marincounty.gov.

First run: populates local_races.csv (contested) and uncontested_races.csv.
Subsequent runs: reads directly from those CSVs — no re-scraping.

Candidate data source:
  https://www.marincounty.gov/departments/elections/
  june-2-2026-statewide-direct-primary-election/
  information-and-about-candidates-060226/
  status-local-candidates-who-have-taken-papers-office-060226
"""

import re
import html as _html
import time
from collections import defaultdict
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from june2_utils import (
    load_measure_descriptions,
    load_all_professions,
    save_candidate_professions,
    fill_missing_professions,
    load_local_race_filter,
    is_local_races_populated,
    load_local_races_for_html,
    populate_local_races,
    load_uncontested_races,
    is_uncontested_populated,
    populate_uncontested,
    render_uncontested_dropdown,
    _norm_race,
    SHARED_STYLE_BLOCK,
    SHARED_SCRIPT,
)

_ROOT = Path(__file__).parent.parent
OUTPUT_FILE = _ROOT / "data" / "marin_ballot_preview_2026-06-02.html"

COUNTY = "Marin"
CANDIDATES_URL = (
    "https://www.marincounty.gov/departments/elections/"
    "june-2-2026-statewide-direct-primary-election/"
    "information-and-about-candidates-060226/"
    "status-local-candidates-who-have-taken-papers-office-060226"
)

ELECTION_TITLE = "Marin County — June 2, 2026 Primary"

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


def _esc(value) -> str:
    return _html.escape(str(value or ""), quote=False)


def _party_badge(party: str) -> tuple[str, str]:
    key = (party or "").strip().lower()
    for k, v in PARTY_ABBREV.items():
        if k in key:
            return v
    return ("N", "party-n")


def fetch_html(url: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page.goto(url, wait_until="networkidle", timeout=30000)
        time.sleep(2)
        html = page.content()
        browser.close()
    return html


def _parse_name(raw: str) -> str:
    """Convert 'Last, First' → 'First Last', strip trailing **."""
    name = raw.rstrip("*").strip()
    if ", " in name:
        last, first = name.split(", ", 1)
        return f"{first} {last}"
    return name


def scrape_all_races(html: str) -> list[dict]:
    """
    Parse the Marin filing-status page.
    Returns ALL races (both contested and uncontested) as:
    [{'race': str, 'candidates': [{'name', 'party', 'designation'}]}]
    """
    soup = BeautifulSoup(html, "html.parser")
    races = []
    race_name_counts: dict[str, int] = defaultdict(int)

    for h3 in soup.find_all("h3"):
        race_name = h3.get_text(strip=True)
        if not race_name or race_name.lower() in ("table of contents",):
            continue

        table_divs = []
        node = h3.find_next_sibling()
        while node and node.name != "h3":
            if node.name == "div" and "widetablediv" in (node.get("class") or []):
                tbl = node.find("table")
                if tbl:
                    table_divs.append(tbl)
            node = node.find_next_sibling()

        if len(table_divs) < 2:
            continue
        candidate_table = table_divs[1]

        candidates = []
        for row in candidate_table.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            name_cell = cells[0]
            strong = name_cell.find("strong")
            if not strong:
                continue

            raw_name = strong.get_text(strip=True)
            is_incumbent = "*" in raw_name
            name = _parse_name(raw_name)

            br = name_cell.find("br")
            occupation = ""
            if br and br.next_sibling:
                occupation = str(br.next_sibling).strip()
            if not occupation or occupation.lower() == "to be determined":
                occupation = "Incumbent" if is_incumbent else "N/A"

            candidates.append({
                "name": name,
                "party": "",
                "designation": occupation,
            })

        if not candidates:
            continue

        race_name_counts[race_name] += 1
        count = race_name_counts[race_name]
        display_name = race_name if count == 1 else f"{race_name} (Office {count})"
        races.append({"race": display_name, "candidates": candidates})

    # Retroactively rename first occurrence of duplicated race names
    name_totals = {k: v for k, v in race_name_counts.items() if v > 1}
    for i, race in enumerate(races):
        base = re.sub(r" \(Office \d+\)$", "", race["race"])
        if base in name_totals and race["race"] == base:
            races[i]["race"] = f"{base} (Office 1)"

    return races


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
        designation = c.get("designation", "N/A")
        desig_html = f'<div class="preview-candidate-designation">{_esc(designation)}</div>'
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
        '<div class="preview-candidate-list">' + "".join(rows) + "</div></div>"
    )


def _parse_measure_short_label(full_label: str) -> str:
    """Extract 'Measure B' from 'Measure B SMART sales tax'."""
    m = re.match(r"^((?:Bond\s+)?(?:Measure|Proposition)\s+\S+)", full_label, re.I)
    return m.group(1).strip() if m else full_label


def _render_measures_block(measures: list[dict]) -> str:
    rows = []
    for m in measures:
        label = _parse_measure_short_label(m["label"])
        badge_inner = (
            "Bond<br>" + _esc(label[5:])
            if label.lower().startswith("bond ")
            else _esc(label)
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
        blocks.append(_render_measures_block(measures))
    body = banner + search_bar + uncontested_html + "".join(blocks)
    return SHARED_STYLE_BLOCK + '\n<div class="lnm-ballot-widget">\n' + body + '\n</div>\n' + SHARED_SCRIPT



def main():
    race_filter, _ = load_local_race_filter(COUNTY)
    norm_filter = {_norm_race(r) for r in race_filter}

    uncontested_entries = load_uncontested_races(COUNTY)
    norm_uncontested = {_norm_race(u["race"]) for u in uncontested_entries}

    needs_scrape = not is_local_races_populated(COUNTY) or not is_uncontested_populated(COUNTY)

    scraped_races: list[dict] = []
    if needs_scrape:
        print(f"Fetching Marin candidate page via Playwright...\n  {CANDIDATES_URL}")
        html = fetch_html(CANDIDATES_URL)
        print(f"  {len(html):,} bytes loaded")
        scraped_races = scrape_all_races(html)
        total_scraped = sum(len(r["candidates"]) for r in scraped_races)
        print(f"  {len(scraped_races)} races, {total_scraped} candidates found")

        # Save to cross-county professions CSV
        save_candidate_professions(scraped_races, COUNTY)
        all_professions = load_all_professions()
        fill_missing_professions(scraped_races, all_professions)

    # ---- Populate local_races.csv (contested races) ----
    if not is_local_races_populated(COUNTY) and scraped_races:
        local_contested = [r for r in scraped_races if _norm_race(r["race"]) in norm_filter]
        total = sum(len(r["candidates"]) for r in local_contested)
        print(f"\nContested races to save: {len(local_contested)} races, {total} candidates")
        for r in local_contested:
            for c in r["candidates"]:
                print(f"  {r['race']} | {c['name']} | {c['designation']}")

        answer = input("\nSave contested candidates to local_races.csv? [y/N]: ").strip().lower()
        if answer == "y":
            added = populate_local_races(local_contested, COUNTY, race_filter, dry_run=False)
            print(f"  Saved {added} candidate rows.")

    # ---- Populate uncontested_races.csv ----
    if any(not e["candidate_name"] for e in uncontested_entries) and scraped_races:
        # Collect uncontested candidate data from scraped races
        # Match by: scraped race name starts with or equals the CSV race name
        uncontested_data: list[dict] = []
        for scraped in scraped_races:
            sn = _norm_race(scraped["race"])
            for csv_race_name in {u["race"] for u in uncontested_entries}:
                cn = _norm_race(csv_race_name)
                if sn == cn or sn.startswith(cn + " ") or cn.startswith(sn + " "):
                    if len(scraped["candidates"]) >= 1:
                        c = scraped["candidates"][0]
                        uncontested_data.append({
                            "race": scraped["race"],  # use scraped name (may have Office N)
                            "candidate_name": c["name"],
                            "profession": c["designation"],
                        })
                    break

        if uncontested_data:
            print(f"\nUncontested candidates to save ({len(uncontested_data)} entries):")
            for u in uncontested_data:
                print(f"  {u['race']} | {u['candidate_name']} | {u['profession']}")

            answer = input("\nSave to uncontested_races.csv? [y/N]: ").strip().lower()
            if answer == "y":
                updated = populate_uncontested(uncontested_data, COUNTY, dry_run=False)
                print(f"  Updated {updated} rows.")

    # ---- Load rendering data ----
    uncontested = load_uncontested_races(COUNTY)

    if is_local_races_populated(COUNTY):
        csv_races, csv_measures = load_local_races_for_html(COUNTY)
        print(f"\nLoaded {len(csv_races)} contested races, {len(csv_measures)} measures from CSV.")
    elif scraped_races:
        csv_races = [r for r in scraped_races if _norm_race(r["race"]) in norm_filter]
        csv_measures = []
        print(f"\nUsing {len(csv_races)} contested races from live scrape.")
    else:
        csv_races = []
        csv_measures = []

    measures = csv_measures
    print(f"  {len(measures)} measures loaded")
    print(f"  {len(uncontested)} uncontested races")

    template_path = _ROOT / "template" / "election_results_template.html"
    template_str = template_path.read_text(encoding="utf-8")

    print("\nRendering HTML...")
    html_out = render_html(csv_races, measures, uncontested)

    OUTPUT_FILE.parent.mkdir(exist_ok=True)
    OUTPUT_FILE.write_text(html_out, encoding="utf-8")
    total = sum(len(r["candidates"]) for r in csv_races)
    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"  Contested races: {len(csv_races)}")
    print(f"  Candidates:      {total}")
    print(f"  Measures:        {len(measures)}")
    print(f"  Uncontested:     {len(uncontested)}")


if __name__ == "__main__":
    main()
