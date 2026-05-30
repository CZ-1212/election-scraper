#!/usr/bin/env python3
"""
San Francisco ballot preview for the June 2, 2026 Primary Election.

Candidate data: https://www.sf.gov/reports--candidates-june-2-2026-statewide-direct-primary-election
Measure data:   https://www.sf.gov/qualified-measures

All data stored in local_races.csv / uncontested_races.csv.
Run to regenerate HTML from CSVs.
"""

import re
import html as _html
from pathlib import Path

from june2_utils import (
    load_local_races_for_html,
    load_uncontested_races,
    render_uncontested_dropdown,
    SHARED_STYLE_BLOCK,
    SHARED_SCRIPT,
)

_ROOT = Path(__file__).parent.parent
OUTPUT_FILE = _ROOT / "data" / "san_francisco_ballot_preview_2026-06-02.html"

COUNTY = "San Francisco"
ELECTION_TITLE = "San Francisco — June 2, 2026 Primary"

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
    return ("NA", "party-na")



def _parse_measure_short_label(full_label: str) -> str:
    m = re.match(r"^((?:Bond\s+)?(?:Measure|Proposition)\s+\S+)", full_label, re.I)
    return m.group(1).strip() if m else full_label


def _render_candidate_race(race: dict) -> str:
    rows = []
    for c in race["candidates"]:
        letter, css_class = _party_badge(c.get("party", ""))
        party_label = c.get("party") or "Party Designation Not Available"
        desig_html = (
            f'<div class="preview-candidate-designation">{_esc(c["designation"])}</div>'
            if c.get("designation") else ""
        )
        rows.append(
            '<div class="preview-candidate">'
            f'<div class="candidate-party {css_class}" data-party="{_esc(party_label)}">'
            f"{_esc(letter)}</div>"
            '<div class="preview-candidate-info">'
            f'<div class="preview-candidate-name">{_esc(c["name"])}</div>'
            + desig_html + "</div></div>"
        )
    return (
        '<div class="race-box">'
        f'<div class="race-title">{_esc(race["race"])}</div>'
        '<div class="preview-candidate-list">' + "".join(rows) + "</div></div>"
    )


def _render_measures_block(measures: list[dict]) -> str:
    rows = []
    for m in measures:
        label = _parse_measure_short_label(m["label"])
        badge_inner = (
            "Bond<br>" + _esc(label[5:])
            if label.lower().startswith("bond ") else _esc(label)
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
    races, measures = load_local_races_for_html(COUNTY)
    uncontested = load_uncontested_races(COUNTY)

    print(f"Loaded {len(races)} races, {len(measures)} measures, {len(uncontested)} uncontested")

    template_path = _ROOT / "template" / "election_results_template.html"
    template_str = template_path.read_text(encoding="utf-8")

    html_out = render_html(races, measures, uncontested)
    OUTPUT_FILE.parent.mkdir(exist_ok=True)
    OUTPUT_FILE.write_text(html_out, encoding="utf-8")

    print(f"Saved: {OUTPUT_FILE}")
    print(f"  Races:       {len(races)}")
    print(f"  Candidates:  {sum(len(r['candidates']) for r in races)}")
    print(f"  Measures:    {len(measures)}")
    print(f"  Uncontested: {len(uncontested)}")


if __name__ == "__main__":
    main()
