#!/usr/bin/env python3
"""
Contra Costa County ballot preview scraper for the June 2, 2026 Primary Election.

Uses the SOE REST API at contracostavote.gov/ce/mobile/seam/resource/rest/election
Election ID: 65

First run: populates local_races.csv.
Subsequent runs: reads directly from local_races.csv — no re-scraping of candidates.
"""

import html as _html
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

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
    render_uncontested_dropdown,
    _norm_race,
    SHARED_STYLE_BLOCK,
    SHARED_SCRIPT,
)

_ROOT = Path(__file__).parent.parent
OUTPUT_FILE = _ROOT / "data" / "contra_costa_ballot_preview_2026-06-02.html"

COUNTY = "Contra Costa"
ELECTION_TITLE = "Contra Costa County — June 2, 2026 Primary"
ELECTION_ID = 65
API_BASE = "https://www.contracostavote.gov/ce/mobile/seam/resource/rest/election"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.contracostavote.gov/election/june-2-2026-statewide-direct-primary-election/",
    "Accept-Encoding": "gzip, deflate, br",
}

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


def _strip_html(text: str) -> str:
    return BeautifulSoup(text or "", "html.parser").get_text(strip=True)


def _party_badge(affiliation: str) -> tuple[str, str]:
    key = (affiliation or "").strip().lower()
    for k, v in PARTY_ABBREV.items():
        if k in key:
            return v
    return ("NA", "party-na")


def fetch_json(endpoint: str, params: dict) -> dict:
    params["lang"] = "en-US"
    params["callback"] = "cb"
    resp = requests.get(
        f"{API_BASE}/{endpoint}", params=params, headers=HEADERS, timeout=30
    )
    resp.raise_for_status()
    raw = resp.text.strip()
    raw = re.sub(r"^cb\(|\)\s*$", "", raw)
    return json.loads(raw)


def scrape_job_titles(url: str) -> dict[str, str]:
    """
    Fetch a candidate-statement page and return {normalized_name: job_title}.
    """
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    titles: dict[str, str] = {}

    for td in soup.find_all("td"):
        strong = td.find("strong")
        if not strong:
            continue
        name = strong.get_text(strip=True)
        name_lower = name.lower()

        job_title = "N/A"
        for span in td.find_all("span"):
            if span.find_parent("strong") is not None:
                continue
            text = span.get_text(strip=True)
            if not text:
                continue
            if text.lower() == name_lower:
                continue
            if text.lower().startswith(("party preference:", "no party preference")):
                continue
            if text.lower().startswith(("http", "www.")):
                continue
            if len(text) > 80:
                continue
            job_title = text
            break

        if name:
            titles[name.lower()] = job_title

    return titles


def _extract_href(raw_name: str) -> str | None:
    m = re.search(r'href=["\']([^"\']+)["\']', raw_name)
    return m.group(1) if m else None


def scrape_races() -> list[dict]:
    """Fetch all races and their candidates from the API."""
    print("  Fetching election overview...")
    election = fetch_json("getElection", {"eid": ELECTION_ID})

    offices_by_level = election.get("offices", {})
    all_offices = []
    for _level, groups in offices_by_level.items():
        for _group, office_list in groups.items():
            for o in office_list:
                if o.get("hasCand"):
                    all_offices.append({
                        "id": o["id"],
                        "name": _strip_html(o["name"]),
                        "statement_url": _extract_href(o.get("name", "")),
                    })

    races = []
    for i, office in enumerate(all_offices):
        print(f"  Fetching candidates for: {office['name']}")
        data = fetch_json("getOfficeCandidates", {"eoid": office["id"]})

        job_titles: dict[str, str] = {}
        if office["statement_url"]:
            try:
                print(f"    Scraping job titles from: {office['statement_url']}")
                job_titles = scrape_job_titles(office["statement_url"])
                time.sleep(0.3)
            except Exception as e:
                print(f"    Warning: could not scrape job titles: {e}")

        candidates = []
        for c in data.get("candidates", []):
            affiliation = c.get("party", {}).get("affiliationTxt", "")
            name = c.get("candidateDisplayName", "")
            designation = job_titles.get(name.lower(), "N/A")
            candidates.append({
                "name": name,
                "party": affiliation,
                "designation": designation,
            })
        if candidates:
            races.append({"race": office["name"], "candidates": candidates})
        if i < len(all_offices) - 1:
            time.sleep(0.3)

    return races


def scrape_measures(descriptions: dict[str, dict]) -> list[dict]:
    """Fetch all measures from the API, merging with CSV descriptions."""
    print("  Fetching election measures...")
    election = fetch_json("getElection", {"eid": ELECTION_ID})

    questions_by_cat = election.get("questions", {})
    measures = []
    for _cat, question_list in questions_by_cat.items():
        for q in question_list:
            label = _strip_html(q.get("title", ""))
            qid = q.get("id")

            csv_entry = descriptions.get(label.lower(), {})
            jurisdiction = csv_entry.get("jurisdiction", "")
            description = csv_entry.get("description", "")

            if not description and qid:
                print(f"    Fetching question text for: {label}")
                qdata = fetch_json("getQuestion", {"qid": qid})
                description = qdata.get("question", "")
                time.sleep(0.2)

            measures.append({
                "label": label,
                "jurisdiction": jurisdiction,
                "description": description,
            })

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


def _render_measures_block(measures: list[dict]) -> str:
    rows = []
    for m in measures:
        label = m["label"]
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    race_filter, _ = load_local_race_filter(COUNTY)
    norm_filter = {_norm_race(r) for r in race_filter}

    # ---- First-run: scrape + preview + confirm ----
    if not is_local_races_populated(COUNTY):
        print(f"First run detected for {COUNTY}. Fetching from API...")
        all_races = scrape_races()

        # Cross-county profession fill
        all_professions = load_all_professions()
        fill_missing_professions(all_races, all_professions)
        save_candidate_professions(all_races, COUNTY)

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

    # ---- Load data for rendering ----
    uncontested = load_uncontested_races(COUNTY)
    descriptions = load_measure_descriptions(COUNTY)

    if is_local_races_populated(COUNTY):
        csv_races, _ = load_local_races_for_html(COUNTY)
        print(f"\nLoaded {len(csv_races)} local races from CSV.")

        print("\nScraping measures from API...")
        measures = scrape_measures(descriptions)
        total = sum(len(r["candidates"]) for r in csv_races)
    else:
        print("\nFetching all races from API for HTML render...")
        all_races = scrape_races()
        all_professions = load_all_professions()
        fill_missing_professions(all_races, all_professions)
        csv_races = [r for r in all_races if _norm_race(r["race"]) in norm_filter] if norm_filter else all_races
        measures = scrape_measures(descriptions)
        total = sum(len(r["candidates"]) for r in csv_races)

    print(f"\nRendering HTML: {len(csv_races)} races, {total} candidates, "
          f"{len(measures)} measures, {len(uncontested)} uncontested")

    template_path = _ROOT / "template" / "election_results_template.html"
    template_str = template_path.read_text(encoding="utf-8")

    html_out = render_html(csv_races, measures, uncontested)

    OUTPUT_FILE.parent.mkdir(exist_ok=True)
    OUTPUT_FILE.write_text(html_out, encoding="utf-8")
    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"  Races:      {len(csv_races)}")
    print(f"  Candidates: {total}")
    print(f"  Measures:   {len(measures)}")
    print(f"  Uncontested:{len(uncontested)}")


if __name__ == "__main__":
    main()
