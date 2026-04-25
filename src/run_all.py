#!/usr/bin/env python3
"""Run all 7 county scrapers in parallel (4 Clarity + 3 non-Clarity)."""

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

# All counties
CLARITY_SITES = {
    'Contra_Costa': 'https://results.enr.clarityelections.com/CA/Contra_Costa/124407/web.345435/#/summary',
    'Marin':        'https://results.enr.clarityelections.com/CA/Marin/124182/web.345435/#/summary',
    'Santa_Clara':  'https://results.enr.clarityelections.com/CA/Santa_Clara/125157/web.345435/#/summary',
    'Sonoma':       'https://results.enr.clarityelections.com/CA/Sonoma/124354/web.345435/#/summary',
}

NON_CLARITY_SITES = {
    'San_Mateo':   {'url': 'https://www.livevoterturnout.com/ENR/sanmateocaenr/18/en/gWJEq_Index_18.html',  'scraper_class': LiveVoterTurnoutScraper},
    'San_Joaquin': {'url': 'https://www.livevoterturnout.com/ENR/sanjoaquincaenr/19/en/Index_19.html',       'scraper_class': LiveVoterTurnoutScraper},
    'Santa_Cruz':  {'url': 'https://www2.santacruzcountyca.gov/ElectionSites/ElectionResults/Results',       'scraper_class': SantaCruzScraper},
    'Alameda':     {'url': 'https://alamedacountyca.gov/rovresults/258/',                                    'scraper_class': AlamedaScraper},
    'Mendocino':   {'url': 'https://www.mendocinocounty.gov/government/assessor-county-clerk-recorder-elections/current-election-results', 'scraper_class': MendocinoScraper},
    'Monterey':    {'url': 'https://www.countyofmonterey.gov/government/departments-a-h/elections/election-results', 'scraper_class': MontereyCountyScraper},
    'Napa':        {'url': 'https://www.napacounty.gov/402/Election-Results',                                        'scraper_class': NapaScraper},
    'San_Francisco': {'url': 'https://sfelections.org/results/20251104w/index.html',                                'scraper_class': SFElectionsScraper},
    'Solano':        {'url': 'https://content.solanocounty.gov/sites/default/files/2026-01/Official_Summary_Results_-_SIGNED.pdf', 'scraper_class': SolanoScraper},
}


def parse_choice(choice):
    r"""Parse a choice (string or dict) into (name, votes, pct) strings.

    Handles the formats produced by each scraper:
      - dict with 'name'/'votes'/'percentage' keys
      - "Name | votes | pct%"          (Santa Cruz pipe-separated)
      - "Name\npct% votes"              (Clarity: e.g. "Yes\n71.25% 294,137")
      - "Name\nvotes pct%"              (LiveVoterTurnout: e.g. "Yes\n294,137 71.25%")
    """
    if isinstance(choice, dict):
        return choice.get('name', ''), choice.get('votes', ''), choice.get('percentage', '')

    s = str(choice).strip()

    # Format: "Name | votes | pct" (Santa Cruz)
    if ' | ' in s:
        parts = [p.strip() for p in s.split(' | ')]
        name = parts[0] if len(parts) > 0 else s
        votes = parts[1] if len(parts) > 1 else ''
        pct = parts[2] if len(parts) > 2 else ''
        return name, votes, pct

    # Multi-line: first line is name, second line has votes/pct in some order
    lines = [l.strip() for l in s.split('\n') if l.strip()]
    if len(lines) >= 2:
        name = lines[0]
        rest = lines[1]
        # "71.25% 294,137" — pct first then votes (Clarity format)
        m = re.match(r'^(\d+\.?\d*)%\s*([\d,]+)$', rest)
        if m:
            return name, m.group(2), m.group(1) + '%'
        # "294,137 71.25%" — votes first then pct
        m = re.match(r'^([\d,]+)\s+(\d+\.?\d*)%$', rest)
        if m:
            return name, m.group(1), m.group(2) + '%'
        # "294,137 (71.25%)" — votes with pct in parens
        m = re.match(r'^([\d,]+)\s+\((\d+\.?\d*)%\)$', rest)
        if m:
            return name, m.group(1), m.group(2) + '%'
        return name, rest, ''

    # Single line ending in a number (e.g. "Vote Cast 412,838") — split label from count
    m = re.match(r'^(.+?)\s+([\d,]+)$', s)
    if m:
        return m.group(1), m.group(2), ''

    return s, '', ''


def _minify_html(html_str: str) -> str:
    """Collapse all whitespace/newlines into a single unbroken line."""
    return re.sub(r"\s+", " ", html_str).strip()


def _escape_html_text(value) -> str:
    """HTML-escape ``value`` for safe insertion into the rendered template."""
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


def _votes_to_int(votes_str) -> int:
    """Convert a votes string (possibly with commas) to int, 0 on failure."""
    try:
        return int(str(votes_str or "").replace(",", "").strip())
    except (ValueError, TypeError):
        return 0


def _fmt_pct(pct_str, votes_str=None, total_votes=0) -> str:
    """Normalize a percentage string to have a % suffix.

    If pct_str is empty and votes/total are provided, compute from those.
    """
    s = str(pct_str or "").strip()
    if s:
        return s if s.endswith("%") else s + "%"
    if votes_str is not None and total_votes > 0:
        v = _votes_to_int(votes_str)
        return f"{v / total_votes * 100:.2f}%"
    return "0%"


def _is_ballot_measure(parsed_choices: list) -> bool:
    """Return True if all choices are yes/no responses (ballot measure)."""
    names = {name.strip().lower() for name, _, _ in parsed_choices}
    return bool(names) and names <= {"yes", "no"}


_CHECK_SVG = '<svg viewBox="0 0 12 12"><polyline points="2,6 5,9 10,3"/></svg>'


def _normalize_lookup_key(s: str) -> str:
    """Lowercase ``s`` and strip all whitespace for fuzzy lookups."""
    return re.sub(r"\s+", "", str(s or "").strip()).lower()


def _lookup_measure_info(title: str, measure_desc_lookup: dict) -> dict:
    """Find measure info dict for a contest title using fuzzy prefix matching."""
    if not measure_desc_lookup:
        return {}

    raw = str(title or "").strip()
    candidates = [raw]

    # Split on dash/em-dash separators: "Measure A - County..." → "Measure A"
    parts = re.split(r"\s*[–—\-:]\s*", raw, maxsplit=1)
    if parts and parts[0].strip() != raw:
        candidates.append(parts[0].strip())

    words = raw.split()
    if len(words) >= 2:
        candidates.append(" ".join(words[:2]))
    if words:
        candidates.append(words[0])

    # Also extract any "Proposition/Measure + token" substring from the title,
    # e.g. "State Proposition 50 - ..." → "Proposition 50"
    m = re.search(r"\b((?:Proposition|Measure|Prop)\s+\S+)", raw, re.I)
    if m:
        candidates.append(m.group(1).strip())

    seen = set()
    for c in candidates:
        key = _normalize_lookup_key(c)
        if key in seen:
            continue
        seen.add(key)
        if key in measure_desc_lookup:
            info = measure_desc_lookup[key]
            print(f"[HTML] Matched measure: '{title}' -> key='{key}'")
            return info if isinstance(info, dict) else {"description": info, "jurisdiction": ""}

    print(f"[HTML] No measure match: '{title}'")
    return {}


def _render_measure_block(title: str, parsed_choices: list, measure_desc_lookup: dict) -> str:
    """Render a ballot-measure race-box using the measure-block template format."""
    info = _lookup_measure_info(title, measure_desc_lookup)
    description = info.get("description", "")
    jurisdiction = info.get("jurisdiction", "")

    # Rank choices by vote count descending.
    total_votes = sum(_votes_to_int(v) for _, v, _ in parsed_choices)
    ranked = sorted(parsed_choices, key=lambda x: _votes_to_int(x[1]), reverse=True)
    winner_name, winner_votes, winner_pct = ranked[0]

    # Status: failing when NO is the leading choice.
    is_failing = winner_name.strip().upper() == "NO"
    badge_class = " failing" if is_failing else ""
    status_word = "failing" if is_failing else "passing"
    status_svg_color = "#9e3d0a" if is_failing else "#4a6020"
    status_svg = (
        f'<svg width="10" height="10" viewBox="0 0 12 12" '
        f'style="stroke:{status_svg_color};fill:none;stroke-width:2.5;stroke-linecap:round;">'
        f'<polyline points="2,6 5,9 10,3"/></svg>'
    )

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

    # Build result rows (winner first, then trailing choices).
    rows = []
    for i, (name, votes, pct) in enumerate(ranked):
        pct_fmt = _fmt_pct(pct, votes, total_votes)
        votes_fmt = _format_int_commas(votes)
        if i == 0:
            rows.append(
                '<div class="measure-result-row leading">'
                '<div class="response-cell">'
                f'<div class="response-check">{_CHECK_SVG}</div>'
                f'<span class="response-label">{_escape_html_text(name)}</span>'
                "</div>"
                '<div class="bar-cell-measure">'
                '<div class="bar-track-measure">'
                f'<div class="bar-fill-leading" style="width:{pct_fmt}"></div>'
                "</div></div>"
                f'<div class="pct-cell-measure pct-leading">{pct_fmt}</div>'
                f'<div class="total-cell">{votes_fmt}</div>'
                "</div>"
            )
        else:
            rows.append(
                '<div class="measure-result-row">'
                '<div class="response-cell">'
                '<div class="response-spacer"></div>'
                f'<span class="response-label">{_escape_html_text(name)}</span>'
                "</div>"
                '<div class="bar-cell-measure">'
                '<div class="bar-track-measure">'
                f'<div class="bar-fill-trailing" style="width:{pct_fmt}"></div>'
                "</div></div>"
                f'<div class="pct-cell-measure pct-trailing">{pct_fmt}</div>'
                f'<div class="total-cell">{votes_fmt}</div>'
                "</div>"
            )

    return (
        '<div class="race-box">'
        f'<div class="race-title">{_escape_html_text(race_title)}</div>'
        '<div class="measure-block">'
        f'<div class="measure-name">{_escape_html_text(measure_name)}</div>'
        + description_html
        + '<div class="measure-status-row">'
        f'<span class="measure-status-badge{badge_class}">{status_svg} {_escape_html_text(measure_name)} is {status_word}</span>'
        f'<span class="measure-votes-counted">{_escape_html_text(total_votes_str)}</span>'
        "</div>"
        '<div class="measure-table">'
        '<div class="measure-table-head" style="grid-template-columns: 100px 1fr 60px 90px;">'
        "<span>Response</span><span>% Votes</span>"
        '<span class="right">Pct</span><span class="right">Votes</span>'
        "</div>"
        + "".join(rows)
        + "</div></div></div>"
    )


def _render_candidate_block(title: str, parsed_choices: list) -> str:
    """Render a candidate race-box using the candidate-row template format."""
    if not parsed_choices:
        return ""

    total_votes = sum(_votes_to_int(v) for _, v, _ in parsed_choices)
    ranked = sorted(parsed_choices, key=lambda x: _votes_to_int(x[1]), reverse=True)
    winner_name = ranked[0][0]

    rows = []
    for name, votes, pct in ranked:
        is_winner = name == winner_name
        pct_fmt = _fmt_pct(pct, votes, total_votes)
        votes_fmt = _format_int_commas(votes)

        # No party data in scraped output — default to Independent styling.
        party_letter = "I"
        party_class = "party-i"
        bar_class = "bar-i"
        pct_class = "pct-i"

        winner_row_class = " winner" if is_winner else ""
        winner_check = ' <span class="winner-check-inline">&#x2713;</span>' if is_winner else ""
        leading_div = '<div class="candidate-leading">Leading</div>' if is_winner else ""

        rows.append(
            f'<div class="candidate-row{winner_row_class}">'
            '<div class="candidate-name-cell">'
            f'<div class="candidate-party {party_class}">{party_letter}</div>'
            "<div class=\"candidate-name-text\">"
            f'<div class="candidate-name">{_escape_html_text(name)}{winner_check}</div>'
            + leading_div
            + "</div></div>"
            '<div class="candidate-bar-cell">'
            '<div class="candidate-bar-track">'
            f'<div class="candidate-bar-fill {bar_class}" style="width:{pct_fmt}"></div>'
            "</div></div>"
            f'<div class="candidate-pct {pct_class}">{pct_fmt}</div>'
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


def _render_county_html(template_str: str, result: dict, measure_desc_lookup: dict | None = None) -> str:
    """Render completed/minified HTML for a single county using the template CSS."""
    if not result:
        return ""

    if "selenium_data" in result:
        vt = (result.get("selenium_data") or {}).get("voter_turnout", {}) or {}
        contests = (result.get("selenium_data") or {}).get("contests", []) or []
    else:
        vt = result.get("voter_turnout", {}) or {}
        contests = result.get("contests", []) or []

    measure_desc_lookup = measure_desc_lookup or {}

    # Reuse the <head> / CSS from the template; replace only the <body> content.
    head_match = re.search(r"^(.*?<body>)", template_str, re.S)
    if not head_match:
        raise ValueError("Could not find <body> tag in election_results_template.html")
    head_html = head_match.group(1)

    # Precincts: voter_turnout field (non-Clarity) or first contest's precincts_reporting (Clarity)
    precincts_raw = vt.get("precincts_reported") or vt.get("precincts_reporting") or ""
    if not precincts_raw and contests:
        precincts_raw = contests[0].get("precincts_reporting") or ""

    # Turnout bar
    turnout_pct = str(vt.get("turnout_percentage", "") or "")
    if turnout_pct and not turnout_pct.endswith("%"):
        turnout_pct += "%"
    turnout_html = (
        '<div class="turnout-bar">'
        '<div class="turnout-stat">'
        '<div class="turnout-stat-label">Voter Turnout</div>'
        f'<div class="turnout-stat-value green">{_escape_html_text(turnout_pct)}</div>'
        "</div>"
        '<div class="turnout-stat">'
        '<div class="turnout-stat-label">Ballots Cast</div>'
        f'<div class="turnout-stat-value">{_escape_html_text(_format_int_commas(vt.get("ballots_cast", "")))}</div>'
        "</div>"
        '<div class="turnout-stat">'
        '<div class="turnout-stat-label">Registered Voters</div>'
        f'<div class="turnout-stat-value">{_escape_html_text(_format_int_commas(vt.get("registered_voters", "")))}</div>'
        "</div>"
        '<div class="turnout-stat">'
        '<div class="turnout-stat-label">Precincts Reporting</div>'
        f'<div class="turnout-stat-value">{_escape_html_text(_clean_precincts(precincts_raw))}</div>'
        "</div>"
        "</div>"
    )

    # Contest blocks
    contest_blocks = []
    for contest in contests:
        choices_raw = contest.get("choices", []) or []
        parsed = []
        for choice in choices_raw:
            name, votes, pct = parse_choice(choice)
            # Skip "Vote Cast" summary rows some scrapers append.
            if re.match(r"^vote\s*cast", name.strip(), re.I):
                continue
            parsed.append((name, votes, pct))

        if not parsed:
            continue

        title = contest.get("title", "")
        if _is_ballot_measure(parsed):
            block = _render_measure_block(title, parsed, measure_desc_lookup)
        else:
            block = _render_candidate_block(title, parsed)
        contest_blocks.append(block)

    search_bar = (
        '<div class="search-wrap">'
        '<input class="search-input" type="text" placeholder="Search for measures, candidates, contests\u2026" id="electionSearch">'
        '</div>'
        '<div class="no-results" id="noResults">No results found.</div>'
    )
    search_script = (
        '<script>(function(){'
        'var input=document.getElementById("electionSearch");'
        'var noResults=document.getElementById("noResults");'
        'if(!input)return;'
        'input.addEventListener("input",function(){'
        'var q=this.value.trim().toLowerCase();'
        'var boxes=document.querySelectorAll(".race-box");'
        'var visible=0;'
        'boxes.forEach(function(box){'
        'var show=!q||box.textContent.toLowerCase().indexOf(q)!==-1;'
        'box.style.display=show?"":"none";'
        'if(show)visible++;'
        '});'
        'noResults.style.display=(q&&visible===0)?"block":"none";'
        '});'
        '})();</script>'
    )
    html_out = head_html + search_bar + turnout_html + "".join(contest_blocks) + search_script + "</body></html>"
    return _minify_html(html_out)


def _load_measure_descriptions_lookup(path: Path) -> dict:
    """Load measure descriptions keyed by normalized measure_title.

    Returns a dict of {normalized_key: {'description': str, 'jurisdiction': str}}.
    """
    if not path.exists():
        print(f"[HTML] measure_descriptions.csv not found at '{path}'. Leaving description/jurisdiction blank.")
        return {}

    def _normalize_colname(s: str) -> str:
        return re.sub(r"[\s_]+", "", str(s or "").strip().lower())

    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return {}

        field_map = {_normalize_colname(fn): fn for fn in reader.fieldnames if fn}

        title_col = None
        desc_col = None
        juris_col = None

        for candidate in ["measure_title", "measuretitle", "title"]:
            if _normalize_colname(candidate) in field_map:
                title_col = field_map[_normalize_colname(candidate)]
                break
        for candidate in ["description", "measure_description", "measuredescription", "contest_description"]:
            if _normalize_colname(candidate) in field_map:
                desc_col = field_map[_normalize_colname(candidate)]
                break
        for candidate in ["jurisdiction"]:
            if _normalize_colname(candidate) in field_map:
                juris_col = field_map[_normalize_colname(candidate)]
                break

        if not title_col or not desc_col:
            print(
                f"[HTML] measure_descriptions.csv missing expected columns. "
                f"Detected title_col='{title_col}', desc_col='{desc_col}'. Leaving blank."
            )
            return {}

        lookup = {}
        for row in reader:
            raw_title = (row.get(title_col) or "").strip()
            if not raw_title:
                continue
            key = _normalize_lookup_key(raw_title)
            lookup[key] = {
                "description": (row.get(desc_col) or "").strip(),
                "jurisdiction": (row.get(juris_col) or "").strip() if juris_col else "",
            }
        print(
            f"[HTML] Loaded measure descriptions: {len(lookup)} entries "
            f"(title_col='{title_col}', desc_col='{desc_col}', juris_col='{juris_col}')."
        )
        return lookup


def scrape_clarity(county, url):
    """Run a single Clarity scraper job and return its tuple result."""
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
    """Run a single non-Clarity scraper job and return its tuple result.

    Retries once with a fresh scraper if the first result looks empty.
    """
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
    """Run all county scrapers in parallel and write the combined CSV exports."""
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
                    name, votes, pct = parse_choice(choice)
                    writer.writerow([contest.get('title', ''), name, votes, pct])
    print(f"\n💾 All county data saved to: {combined_file.name}")
    print("=" * 70)

    # Second export: one row per county HTML (minified), plus election_date.
    measure_lookup = _load_measure_descriptions_lookup(Path("election_data") / "measure_descriptions.csv")
    print(f"[HTML] measure_descriptions loaded at startup: {len(measure_lookup)}")
    template_str = (Path("template") / "election_results_template.html").read_text(encoding="utf-8")
    election_date = _determine_election_date(county_data)

    html_export_file = Path("data") / f"election_results_html_{election_date}.csv"
    with open(html_export_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["county", "election_date", "html_output"])

        for county in sorted(county_data.keys()):
            data = county_data[county]
            if not data:
                continue

            county_display_name = county.replace("_", " ")
            html_output = _render_county_html(template_str, data, measure_lookup)
            writer.writerow([county_display_name, election_date, html_output])

    print(f"\n💾 HTML outputs saved to: {html_export_file.name}")
    print("=" * 70)


if __name__ == "__main__":
    main()
