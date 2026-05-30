#!/usr/bin/env python3
"""
Run all 7 county scrapers in parallel (4 Clarity + 3 non-Clarity).
"""

import csv
import re
import time
from datetime import datetime
import html as _html
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Hard timeout (seconds) for each non-Clarity scraper call.
NON_CLARITY_TIMEOUT = 120

# Retry delay (seconds) before a single re-attempt when a non-Clarity scraper
# returns None, ballots_cast == 0, or an empty contests list.
NON_CLARITY_RETRY_DELAY = 7

from clarity_scraper import ClarityScraper, validate_and_secure_filepath
from multi_platform_scraper import LiveVoterTurnoutScraper, SantaCruzScraper, AlamedaScraper, MendocinoScraper, MontereyCountyScraper, NapaScraper, SFElectionsScraper, SolanoScraper

# "test"  → scrape test_url column  (zero report or past-election link)
# "live"  → scrape live_url column   (election-night endpoint)
MODE = "test"

# Maps county name (as used in county_links.csv) to its non-Clarity scraper class.
_COUNTY_TO_SCRAPER_CLASS: dict = {
    "San_Mateo":     LiveVoterTurnoutScraper,
    "San_Joaquin":   LiveVoterTurnoutScraper,
    "Santa_Cruz":    SantaCruzScraper,
    "Alameda":       AlamedaScraper,
    "Mendocino":     MendocinoScraper,
    "Monterey":      MontereyCountyScraper,
    "Napa":          NapaScraper,
    "San_Francisco": SFElectionsScraper,
    "Solano":        SolanoScraper,
}


def _load_county_links(csv_path: Path, mode: str) -> tuple[dict, dict, dict]:
    """Read county_links.csv and return (clarity_sites, non_clarity_sites, source_labels).

    mode selects which URL column to use: 'test' → test_url, 'live' → live_url.
    Counties whose selected URL column is blank are skipped with a warning.
    """
    url_col = "test_url" if mode == "test" else "live_url"
    clarity: dict = {}
    non_clarity: dict = {}
    source_labels: dict = {}

    if not csv_path.exists():
        print(f"[CONFIG] county_links.csv not found at {csv_path} — no counties loaded")
        return clarity, non_clarity, source_labels

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            county = (row.get("county") or "").strip()
            platform = (row.get("platform") or "").strip().lower()
            url = (row.get(url_col) or "").strip()
            label = (row.get("source_label") or "").strip()

            if not county:
                continue
            source_labels[county] = label

            if not url:
                print(f"[CONFIG] {county}: {url_col} is blank in county_links.csv — skipping")
                continue

            if platform == "clarity":
                clarity[county] = url
            else:
                scraper_cls = _COUNTY_TO_SCRAPER_CLASS.get(county)
                if scraper_cls is None:
                    print(f"[CONFIG] {county}: no scraper class mapped — skipping")
                    continue
                non_clarity[county] = {"url": url, "scraper_class": scraper_cls}

    return clarity, non_clarity, source_labels


_LINKS_CSV = Path(__file__).parent.parent / "election_data" / "county_links.csv"
CLARITY_SITES, NON_CLARITY_SITES, COUNTY_SOURCE_LABELS = _load_county_links(_LINKS_CSV, MODE)


def _minify_html(html_str: str) -> str:
    """Collapse all whitespace/newlines into a single unbroken line."""
    return re.sub(r"\s+", " ", html_str).strip()


def _escape_html_text(value) -> str:
    if value is None:
        return ""
    return _html.escape(str(value), quote=False)


def _format_int_commas(value) -> str:
    """Format an integer-like value with comma separators.

    Accepts ints or numeric strings that may already include commas.
    Returns '' if the value can't be parsed.
    """
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    try:
        n = int(s.replace(",", ""))
        return f"{n:,}"
    except Exception:
        return s


def _clean_precincts(raw) -> str:
    """Normalize precinct strings from various scraper formats.

    Handles:
      '539 of 539 Precincts Reported from Vote Centers (100.00%)' → '539 of 539 Precincts Reported'
      '205 of 205 (100.00%)'                                      → '205 of 205 Precincts Reported'
      '159 / 159 (100.00%)'                                       → '159 of 159 Precincts Reported'
      'Precincts Reporting 100%'                                   → '100% Precincts Reported'
      'Precincts Reporting'                                        → ''
    """
    s = str(raw or "").strip()
    if not s:
        return s
    # X of Y (or X / Y) format
    m = re.match(r"(\d[\d,]*)\s*(?:of|/)\s*(\d[\d,]*)", s)
    if m:
        return f"{m.group(1)} of {m.group(2)} Precincts Reported"
    # "Precincts Reporting 100%" format
    m = re.search(r"Precincts Reporting\s+(\d+(?:\.\d+)?%)", s, re.I)
    if m:
        return f"{m.group(1)} Precincts Reported"
    # Bare "Precincts Reporting" with no data — omit
    if re.match(r"^precincts reporting$", s, re.I):
        return ""
    return s


def _parse_election_date_from_raw(raw: str | None) -> str | None:
    """Try to extract an election day as YYYY-MM-DD from common strings."""
    if not raw:
        return None
    s = str(raw).strip()

    # ISO-like (e.g. 2025-11-04 or 2025/11/04)
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"

    # Month-name (e.g. Tuesday, November 4, 2025, 10:45:43 PM)
    month_map = {
        "January": 1,
        "February": 2,
        "March": 3,
        "April": 4,
        "May": 5,
        "June": 6,
        "July": 7,
        "August": 8,
        "September": 9,
        "October": 10,
        "November": 11,
        "December": 12,
    }
    m = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s*(\d{4})",
        s,
    )
    if m:
        month = month_map.get(m.group(1))
        day = int(m.group(2))
        year = int(m.group(3))
        if month:
            return f"{year:04d}-{month:02d}-{day:02d}"

    # scrape_timestamp ISO datetime (e.g. 2025-11-05T10:32:57...)
    m = re.match(r"^(\d{4}-\d{2}-\d{2})T", s)
    if m:
        return m.group(1)

    return None


def _determine_election_date(county_data: dict) -> str:
    """Parse election_date from the first county result we can; else today."""
    for _, data in county_data.items():
        if not data:
            continue
        if isinstance(data, dict) and "selenium_data" in data:
            raw = (data.get("selenium_data") or {}).get("last_updated")
        else:
            raw = data.get("last_updated") if isinstance(data, dict) else None

        parsed = _parse_election_date_from_raw(raw)
        if parsed:
            return parsed

    return datetime.now().strftime("%Y-%m-%d")


def _is_ballot_measure(parsed_choices: list) -> bool:
    """Return True if all choices are yes/no responses (ballot measure)."""
    names = {choice["name"].strip().lower() for choice in parsed_choices}
    return bool(names) and names <= {"yes", "no"}


def _normalize_lookup_key(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "").strip()).lower()


def _normalize_county(s: str) -> str:
    """Strip 'county', underscores, spaces and lowercase so 'Alameda County' and 'Alameda' both map to 'alameda'."""
    s = re.sub(r"\bcounty\b", "", str(s or "").lower(), flags=re.I)
    return re.sub(r"[\s_]+", "", s).strip()


_PARTY_ABBREV = {
    "democratic": ("D", "party-d"),
    "republican": ("R", "party-r"),
    "green": ("G", "party-n"),
    "libertarian": ("L", "party-n"),
    "american independent": ("AI", "party-n"),
    "peace and freedom": ("PF", "party-n"),
}


def _party_badge(party: str) -> tuple[str, str]:
    """Map a party string to (badge_letter, css_class). Non-partisan and empty both return N/party-n."""
    key = (party or "").strip().lower()
    for k, v in _PARTY_ABBREV.items():
        if k in key:
            return v
    return ("N", "party-n")


def _lookup_measure_info(title: str, measure_desc_lookup: dict, county: str = "") -> dict:
    """Find measure info dict for a contest title, scoped to county.

    County is included in every key so measures that share a letter across
    counties never collide (e.g. 'alameda|measurea' vs 'santaclara|measurea').
    """
    if not measure_desc_lookup:
        return {}

    county_key = _normalize_county(county)
    raw = str(title or "").strip()
    candidates = [raw]

    parts = re.split(r"\s*[–—\-:]\s*", raw, maxsplit=1)
    if parts and parts[0].strip() != raw:
        candidates.append(parts[0].strip())

    words = raw.split()
    if len(words) >= 2:
        candidates.append(" ".join(words[:2]))
    if words:
        candidates.append(words[0])

    m = re.search(r"\b((?:Proposition|Measure|Prop|Bond\s+Measure)\s+\S+)", raw, re.I)
    if m:
        candidates.append(m.group(1).strip())

    seen = set()
    for c in candidates:
        key = f"{county_key}|{_normalize_lookup_key(c)}"
        if key in seen:
            continue
        seen.add(key)
        if key in measure_desc_lookup:
            info = measure_desc_lookup[key]
            print(f"[HTML] Matched measure: '{title}' (county={county}) -> key='{key}'")
            return info if isinstance(info, dict) else {"description": info, "jurisdiction": ""}

    print(f"[HTML] No measure match: '{title}' (county={county})")
    return {}


def _render_unavailable_contest(title: str) -> str:
    """Render a contest box whose choices failed to parse — no rows, just a notice."""
    return (
        '<div class="race-box">'
        f'<div class="race-title">{_escape_html_text(title)}</div>'
        '<div class="contest-unavailable">Results unavailable for this contest.</div>'
        "</div>"
    )


def _render_measure_block(title: str, parsed_choices: list, measure_desc_lookup: dict, county: str = "") -> str:
    """Render a ballot-measure race-box. No pass/fail interpretation."""
    info = _lookup_measure_info(title, measure_desc_lookup, county)
    description = info.get("description", "")
    jurisdiction = info.get("jurisdiction", "")

    total_votes = sum(choice["votes"] for choice in parsed_choices)

    # Short measure identifier for the measure-name div.
    m = re.match(r"^((?:Measure|Proposition|Prop)\s+\S+)", title, re.I)
    measure_name = m.group(1).strip() if m else title
    # race-title: use jurisdiction from lookup, else fall back to full title.
    race_title = jurisdiction if jurisdiction else title

    total_votes_str = f"{total_votes:,} votes counted" if total_votes else ""
    description_html = (
        f'<div class="measure-desc">{_escape_html_text(description)}</div>'
        if description
        else ""
    )
    votes_counted_html = (
        f'<div class="measure-votes-counted">{_escape_html_text(total_votes_str)}</div>'
        if total_votes_str
        else ""
    )

    # Choices in scraped order — no sorting, no interpretation.
    rows = []
    for choice in parsed_choices:
        name = choice["name"]
        pct_fmt = f"{choice['pct']:.2f}%"
        votes_fmt = f"{choice['votes']:,}"
        rows.append(
            '<div class="measure-result-row">'
            '<div class="response-cell">'
            f'<span class="response-label">{_escape_html_text(name)}</span>'
            "</div>"
            '<div class="bar-cell-measure">'
            '<div class="bar-track-measure">'
            f'<div class="bar-fill" style="width:{pct_fmt}"></div>'
            "</div></div>"
            f'<div class="pct-cell-measure">{pct_fmt}</div>'
            f'<div class="total-cell">{votes_fmt}</div>'
            "</div>"
        )

    return (
        '<div class="race-box">'
        f'<div class="race-title">{_escape_html_text(race_title)}</div>'
        '<div class="measure-block">'
        f'<div class="measure-name">{_escape_html_text(measure_name)}</div>'
        + description_html
        + votes_counted_html
        + '<div class="measure-table">'
        '<div class="measure-table-head" style="grid-template-columns: 100px 1fr 60px 90px;">'
        "<span>Response</span><span>% Votes</span>"
        '<span class="right">Pct</span><span class="right">Votes</span>'
        "</div>"
        + "".join(rows)
        + "</div></div></div>"
    )


def _render_candidate_block(title: str, parsed_choices: list, candidate_roster: dict | None = None, county: str = "") -> str:
    """Render a candidate race-box. No winner/leading interpretation."""
    if not parsed_choices:
        return ""

    roster = candidate_roster or {}

    rows = []
    for choice in parsed_choices:
        name = choice["name"]
        pct_fmt = f"{choice['pct']:.2f}%"
        votes_fmt = f"{choice['votes']:,}"

        info = _lookup_candidate_info(county, title, name, roster)
        profession = info.get("profession", "")
        party_str = info.get("party", "")
        letter, party_css = _party_badge(party_str)
        party_label = party_str if party_str else "Non-Partisan"

        profession_html = (
            f'<div class="candidate-profession">{_escape_html_text(profession)}</div>'
            if profession else ""
        )

        rows.append(
            '<div class="candidate-row">'
            '<div class="candidate-name-cell">'
            f'<div class="candidate-party {party_css}" data-party="{_escape_html_text(party_label)}">'
            f"{_escape_html_text(letter)}</div>"
            '<div class="candidate-name-text">'
            f'<div class="candidate-name">{_escape_html_text(name)}</div>'
            + profession_html
            + "</div></div>"
            '<div class="candidate-bar-cell">'
            '<div class="candidate-bar-track">'
            f'<div class="candidate-bar-fill bar-n" style="width:{pct_fmt}"></div>'
            "</div></div>"
            f'<div class="candidate-pct pct-n">{pct_fmt}</div>'
            f'<div class="candidate-votes">{votes_fmt}</div>'
            "</div>"
        )

    return (
        '<div class="race-box">'
        f'<div class="race-title">{_escape_html_text(title)}</div>'
        '<div class="candidate-table-head">'
        "<span>Candidate</span><span>% Votes</span>"
        '<span class="right">Pct</span><span class="right">Votes</span>'
        "</div>"
        + "".join(rows)
        + "</div>"
    )


def _render_county_html(
    template_str: str,
    result: dict,
    measure_desc_lookup: dict | None = None,
    county_name: str = "",
    candidate_roster: dict | None = None,
    source_url: str = "",
    source_label: str = "",
) -> str:
    """Render a self-contained embed: scoped <style> block + <div class='lnm-results-widget'>."""
    if not result:
        return ""

    if "selenium_data" in result:
        sd = result.get("selenium_data") or {}
        vt = sd.get("voter_turnout", {}) or {}
        contests = sd.get("contests", []) or []
        last_updated = str(sd.get("last_updated", "") or "").strip()
    else:
        vt = result.get("voter_turnout", {}) or {}
        contests = result.get("contests", []) or []
        last_updated = str(result.get("last_updated", "") or "").strip()

    measure_desc_lookup = measure_desc_lookup or {}

    # Extract the scoped <style> block from the template.
    style_match = re.search(r"(<style>.*?</style>)", template_str, re.S | re.I)
    if not style_match:
        raise ValueError("Could not find <style> block in election_results_template.html")
    style_block = style_match.group(1)

    # Precincts go to provenance only (not the turnout strip).
    precincts_raw = vt.get("precincts_reported") or vt.get("precincts_reporting") or ""
    if not precincts_raw and contests:
        precincts_raw = contests[0].get("precincts_reporting") or ""
    precincts_str = _clean_precincts(precincts_raw)

    # Turnout bar \u2014 only render stats that have a value; omit bar if none do.
    turnout_stats = []
    turnout_pct = str(vt.get("turnout_percentage", "") or "").strip()
    if turnout_pct:
        if not turnout_pct.endswith("%"):
            turnout_pct += "%"
        turnout_stats.append(("Voter Turnout", turnout_pct))
    ballots = _format_int_commas(vt.get("ballots_cast", ""))
    if ballots:
        turnout_stats.append(("Ballots Cast", ballots))
    registered = _format_int_commas(vt.get("registered_voters", ""))
    if registered:
        turnout_stats.append(("Registered Voters", registered))

    if turnout_stats:
        stat_divs = "".join(
            '<div class="turnout-stat">'
            f'<div class="turnout-stat-label">{_escape_html_text(label)}</div>'
            f'<div class="turnout-stat-value">{_escape_html_text(value)}</div>'
            "</div>"
            for label, value in turnout_stats
        )
        turnout_html = f'<div class="turnout-bar">{stat_divs}</div>'
    else:
        turnout_html = ""

    # Provenance block \u2014 source link, timestamp, precincts when available.
    provenance_parts = []
    if source_url:
        link_label = source_label if source_label else county_name.replace("_", " ") + " Registrar of Voters"
        provenance_parts.append(
            f'Source: <a href="{_escape_html_text(source_url)}" target="_blank" rel="noopener">'
            f"{_escape_html_text(link_label)}</a>"
        )
    if last_updated:
        provenance_parts.append(f"Updated: {_escape_html_text(last_updated)}")
    if precincts_str:
        provenance_parts.append(_escape_html_text(precincts_str))

    provenance_html = (
        '<div class="lnm-provenance">' + " &middot; ".join(provenance_parts) + "</div>"
        if provenance_parts else ""
    )

    # Contest blocks — choices are now canonical dicts {name, votes: int, pct: float}.
    # A single bad choice fails the entire contest: render an unavailability notice
    # instead of partial rows, which would be misleading (e.g. a measure showing only Yes).
    contest_blocks = []
    for contest in contests:
        choices_raw = contest.get("choices", []) or []
        title = contest.get("title", "") or ""
        parsed = []
        parse_failed = False

        for choice in choices_raw:
            if not isinstance(choice, dict) or "name" not in choice:
                print(
                    f"[PARSE ERROR] [{county_name}] {title!r}: "
                    f"choice is not a canonical dict: {choice!r}"
                )
                parse_failed = True
                continue  # keep logging remaining failures before deciding
            if not isinstance(choice.get("votes"), int) or not isinstance(choice.get("pct"), (int, float)):
                print(
                    f"[PARSE ERROR] [{county_name}] {title!r}: "
                    f"choice missing int votes or float pct: {choice!r}"
                )
                parse_failed = True
                continue
            if re.match(r"^vote\s*cast", str(choice["name"]).strip(), re.I):
                continue
            parsed.append(choice)

        if parse_failed:
            # One or more choices could not be parsed — render nothing rather than partial rows.
            contest_blocks.append(_render_unavailable_contest(title))
            continue

        if not parsed:
            continue

        if _is_ballot_measure(parsed):
            block = _render_measure_block(title, parsed, measure_desc_lookup, county_name)
        else:
            block = _render_candidate_block(title, parsed, candidate_roster, county_name)
        contest_blocks.append(block)

    search_bar = (
        '<div class="search-wrap">'
        '<input class="search-input" type="text" placeholder="Search for measures, candidates, contests\u2026" id="electionSearch">'
        "</div>"
        '<div class="no-results" id="noResults">No results found.</div>'
    )
    search_script = (
        "<script>(function(){"
        'var input=document.getElementById("electionSearch");'
        'var noResults=document.getElementById("noResults");'
        "if(!input)return;"
        'input.addEventListener("input",function(){'
        "var q=this.value.trim().toLowerCase();"
        'var container=input.closest(".lnm-results-widget")||document;'
        'var boxes=container.querySelectorAll(".race-box");'
        "var visible=0;"
        "boxes.forEach(function(box){"
        "var show=!q||box.textContent.toLowerCase().indexOf(q)!==-1;"
        'box.style.display=show?"":"none";'
        "if(show)visible++;"
        "});"
        'noResults.style.display=(q&&visible===0)?"block":"none";'
        "});"
        "})();</script>"
    )

    widget_body = search_bar + turnout_html + provenance_html + "".join(contest_blocks) + search_script
    html_out = style_block + '\n<div class="lnm-results-widget">\n' + widget_body + "\n</div>"
    return _minify_html(html_out)


_MEASURE_ROW_PAT = re.compile(r"\b(measure|proposition|prop|bond|recall)\b", re.I)


def _load_measure_descriptions_lookup(path: Path) -> dict:
    """Load measure descriptions from local_races - Sheet1.csv.

    Only rows whose 'Race/Measure name' contains a ballot-measure keyword are
    loaded; candidate rows are skipped automatically.

    Key format: "{normalized_county}|{normalized_title_variant}" so that
    measures sharing a letter across counties never collide.  Multiple key
    variants per row let the scraper's short or long title form both match.
    """
    if not path.exists():
        print(f"[HTML] {path.name} not found. Leaving measure descriptions blank.")
        return {}

    lookup: dict = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            race = (row.get("Race/Measure name") or "").strip()
            if not _MEASURE_ROW_PAT.search(race):
                continue

            juris = (row.get("Candidate Name/Measure Juristiction") or "").strip()
            desc = (row.get("Profession/Description") or "").strip()
            county_raw = (row.get("County") or "").strip()
            county_key = _normalize_county(county_raw)
            info = {"description": desc, "jurisdiction": juris}

            # Collect every title variant the scraper might produce for this row.
            variants: list[str] = [race]

            parts = re.split(r"\s*[–—\-:]\s*", race, maxsplit=1)
            if parts and parts[0].strip() != race:
                variants.append(parts[0].strip())

            words = race.split()
            if len(words) >= 2:
                variants.append(" ".join(words[:2]))

            m = re.search(r"\b((?:Proposition|Measure|Prop|Bond\s+Measure)\s+\S+)", race, re.I)
            if m:
                variants.append(m.group(1).strip())

            for variant in variants:
                full_key = f"{county_key}|{_normalize_lookup_key(variant)}"
                if full_key not in lookup:
                    lookup[full_key] = info

    print(f"[HTML] Loaded measure descriptions: {len(lookup)} entries from {path.name}")
    return lookup


def _load_candidate_roster_lookup(path: Path) -> dict:
    """Load candidate professions and parties from local_races - Sheet1.csv.

    Skips measure rows (race name matches _MEASURE_ROW_PAT).
    Key: "{normalized_county}|{normalized_race}|{normalized_candidate_name}"
    Value: {'profession': str, 'party': str}
    """
    if not path.exists():
        print(f"[HTML] {path.name} not found. Leaving candidate professions blank.")
        return {}

    lookup: dict = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            race = (row.get("Race/Measure name") or "").strip()
            if _MEASURE_ROW_PAT.search(race):
                continue

            candidate = (row.get("Candidate Name/Measure Juristiction") or "").strip()
            if not candidate:
                continue

            profession = (row.get("Profession/Description") or "").strip()
            party = (row.get("Party") or "").strip()
            county_raw = (row.get("County") or "").strip()

            key = f"{_normalize_county(county_raw)}|{_normalize_lookup_key(race)}|{_normalize_lookup_key(candidate)}"
            lookup[key] = {"profession": profession, "party": party}

    print(f"[HTML] Loaded candidate roster: {len(lookup)} entries from {path.name}")
    return lookup


def _lookup_candidate_info(county: str, race_title: str, candidate_name: str, roster: dict) -> dict:
    """Look up a candidate in the roster by county + race + name.

    Returns {'profession': str, 'party': str} or {} if no match.
    Scoped to county so there is no cross-county fallthrough.
    """
    if not roster:
        return {}
    key = f"{_normalize_county(county)}|{_normalize_lookup_key(race_title)}|{_normalize_lookup_key(candidate_name)}"
    return roster.get(key, {})


def scrape_clarity(county, url):
    start = time.time()
    try:
        scraper = ClarityScraper(url, reuse_driver=None, save_files=False)
        result = scraper.scrape()
        duration = time.time() - start
        return county, 'clarity', result, duration, None
    except Exception as e:
        return county, 'clarity', None, time.time() - start, str(e)


def _result_looks_empty(result):
    """Return True if a scraper result looks like it has no useful data."""
    if result is None:
        return True
    vt = result.get('voter_turnout', {})
    ballots = vt.get('ballots_cast', 0) or 0
    contests = result.get('contests', [])
    return ballots == 0 or contests == []


def scrape_non_clarity(county, info):
    start = time.time()
    try:
        scraper = info['scraper_class'](info['url'], county)
        result = scraper.scrape()

        # Retry once if the result looks empty (transient failure / page not yet loaded)
        if _result_looks_empty(result):
            print(f"[{county}] Result looks empty (ballots_cast=0 or no contests) — retrying in {NON_CLARITY_RETRY_DELAY}s...")
            time.sleep(NON_CLARITY_RETRY_DELAY)
            scraper2 = info['scraper_class'](info['url'], county)
            retry_result = scraper2.scrape()
            if not _result_looks_empty(retry_result):
                result = retry_result
            else:
                print(f"[{county}] Retry also returned empty result, keeping first result.")

        duration = time.time() - start
        return county, 'non-clarity', result, duration, None
    except Exception as e:
        return county, 'non-clarity', None, time.time() - start, str(e)


def main():
    print("=" * 70)
    print("CALIFORNIA ELECTION SCRAPER — ALL 13 COUNTIES")
    print("=" * 70)
    print("Clarity (4):     Contra Costa, Marin, Santa Clara, Sonoma")
    print("Non-Clarity (9): San Mateo, San Joaquin, Santa Cruz, Alameda, Mendocino, Monterey, Napa, San Francisco, Solano")
    print("=" * 70)

    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)

    overall_start = time.time()
    results = []
    county_data = {}  # Accumulates all county results for the combined file

    # Submit all counties concurrently.
    # Non-Clarity scrapers get a hard per-scraper timeout (NON_CLARITY_TIMEOUT).
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        for county, url in CLARITY_SITES.items():
            futures[executor.submit(scrape_clarity, county, url)] = (county, 'clarity')
        for county, info in NON_CLARITY_SITES.items():
            futures[executor.submit(scrape_non_clarity, county, info)] = (county, 'non-clarity')

        for future in as_completed(futures):
            county, platform = futures[future]
            try:
                county, platform, result, duration, error = future.result(timeout=NON_CLARITY_TIMEOUT if platform == 'non-clarity' else None)
            except TimeoutError:
                duration = NON_CLARITY_TIMEOUT
                error = f"Scraper exceeded {NON_CLARITY_TIMEOUT}s hard timeout"
                result = None
                print(f"\n{'=' * 70}")
                print(f"[TIMEOUT] {county} ({platform}) — exceeded {NON_CLARITY_TIMEOUT}s")
                results.append({'county': county, 'platform': platform, 'success': False, 'duration': duration, 'error': error})
                county_data[county] = {'success': False, 'error': error}
                continue
            duration_str = f"{int(duration)}s" if duration < 60 else f"{int(duration//60)}m {int(duration%60)}s"

            print(f"\n{'=' * 70}")
            print(f"{'✅' if not error else '❌'} {county} ({platform}) — {duration_str}")
            print(f"{'=' * 70}")

            if error:
                print(f"  ERROR: {error}")
                results.append({'county': county, 'platform': platform, 'success': False, 'duration': duration, 'error': error})
                county_data[county] = {'success': False, 'error': error}
                continue

            if platform == 'clarity':
                selenium_data = (result.get('selenium_data') or {}) if result else {}
                vt = selenium_data.get('voter_turnout', {})
                contests = selenium_data.get('contests', [])
            else:
                vt = result.get('voter_turnout', {}) if result else {}
                contests = result.get('contests', []) if result else []

            # Zero-result detection: warn clearly rather than silently succeeding
            ballots_cast = vt.get('ballots_cast', 0) or 0
            if ballots_cast == 0:
                print(f"  [{county}] WARNING: ballots_cast is 0 — results may not be posted yet")
            if not contests:
                print(f"  [{county}] WARNING: contests list is empty — results may not be posted yet")

            if vt:
                print(f"  Ballots Cast: {vt.get('ballots_cast', 0):,}")
                print(f"  Registered:   {vt.get('registered_voters', 0):,}")
                print(f"  Turnout:      {vt.get('turnout_percentage', 0)}%")
            print(f"  Contests: {len(contests)}")
            for i, c in enumerate(contests[:3], 1):
                print(f"    {i}. {c.get('title', '')[:65]} ({len(c.get('choices', []))} choices)")

            county_data[county] = result

            results.append({
                'county': county,
                'platform': platform,
                'success': True,
                'duration': duration,
                'turnout_pct': vt.get('turnout_percentage'),
                'contest_count': len(contests),
            })

    total_time = time.time() - overall_start
    successful = sum(1 for r in results if r['success'])
    total = len(results)

    print("\n" + "=" * 70)
    print("FINAL SUMMARY — ALL 7 COUNTIES")
    print("=" * 70)
    print(f"✅ Successful: {successful}/{total} ({successful/total*100:.0f}%)")
    print(f"⏱️  Total Time: {int(total_time//60)}m {int(total_time%60)}s")
    print()
    for r in sorted(results, key=lambda x: x['county']):
        status = "✅" if r['success'] else "❌"
        dur = f"{int(r['duration'])}s" if r['duration'] < 60 else f"{int(r['duration']//60)}m {int(r['duration']%60)}s"
        if r['success']:
            pct = r.get('turnout_pct') if r.get('turnout_pct') is not None else '?'
            print(f"  {status} {r['county']:15} | Turnout: {str(pct):>5}% | Contests: {r.get('contest_count', 0)} | {dur}")
        else:
            print(f"  {status} {r['county']:15} | FAILED | {dur}")

    # Save all county data as CSV — grouped by county, with a header block per county
    combined_file = validate_and_secure_filepath(data_dir, "all_counties", "csv")
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(combined_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        for county in sorted(county_data.keys()):
            data = county_data[county]
            if not data or data.get('success') is False:
                continue
            if 'selenium_data' in data:
                selenium_data = data.get('selenium_data') or {}
                vt = selenium_data.get('voter_turnout', {})
                contests = selenium_data.get('contests', [])
            else:
                vt = data.get('voter_turnout', {})
                contests = data.get('contests', [])
            # County-level header + data
            writer.writerow(['County', 'ballots_cast', 'registered_voters', 'turnout_percentage', 'Last-update'])
            writer.writerow([county, vt.get('ballots_cast', ''), vt.get('registered_voters', ''),
                             vt.get('turnout_percentage', ''), timestamp])
            # Contest sub-header + rows
            writer.writerow(['contest_title', 'choice_name', 'votes', 'vote_percentage'])
            for contest in contests:
                for choice in contest.get('choices', []):
                    writer.writerow([
                        contest.get('title', ''),
                        choice.get('name', '') if isinstance(choice, dict) else '',
                        choice.get('votes', '') if isinstance(choice, dict) else '',
                        choice.get('pct', '')  if isinstance(choice, dict) else '',
                    ])
    print(f"\n💾 All county data saved to: {combined_file.name}")
    print("=" * 70)

    # Second export: one row per county HTML (minified), plus election_date.
    roster_path = Path("election_data") / "local_races - Sheet1.csv"
    measure_lookup = _load_measure_descriptions_lookup(roster_path)
    candidate_roster = _load_candidate_roster_lookup(roster_path)
    template_str = (Path("template") / "election_results_template.html").read_text(encoding="utf-8")
    election_date = _determine_election_date(county_data)

    # Build URL map for provenance links.
    county_urls: dict = {}
    for c, url in CLARITY_SITES.items():
        county_urls[c] = url
    for c, info in NON_CLARITY_SITES.items():
        county_urls[c] = info["url"]

    html_export_file = Path("data") / f"election_results_html_{election_date}.csv"
    with open(html_export_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["county", "election_date", "html_output"])

        for county in sorted(county_data.keys()):
            data = county_data[county]
            if not data:
                continue

            county_display_name = county.replace("_", " ")
            source_url = county_urls.get(county, "")
            source_label = COUNTY_SOURCE_LABELS.get(county, "")
            html_output = _render_county_html(template_str, data, measure_lookup, county, candidate_roster, source_url, source_label)
            writer.writerow([county_display_name, election_date, html_output])

    print(f"\n💾 HTML outputs saved to: {html_export_file.name}")
    print("=" * 70)


if __name__ == "__main__":
    main()
