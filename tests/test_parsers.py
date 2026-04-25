"""Fixture-driven tests for the per-county parsers.

These exercise the pure parsing logic — _parse() / _parse_page_text() —
without any network or Selenium activity. The fixtures in tests/fixtures/
are minimal hand-written reproductions of the real upstream formats:
SSRS HTML for Mendocino, PDF-extracted text for Napa and Solano. They
intentionally include the quirks the scrapers have to tolerate
(duplicate contests, all-caps title rows, "Cast Votes" summary rows).

If a county's upstream HTML structure changes and the parser breaks,
the corresponding fixture should be updated to match — this is the
regression net the project did not have before.
"""

from bs4 import BeautifulSoup

from src.multi_platform_scraper import (
    MendocinoScraper,
    NapaScraper,
    SolanoScraper,
)

from .conftest import make_scraper


# ---------------------------------------------------------------------------
# Mendocino — SSRS HTML
# ---------------------------------------------------------------------------


def _mendocino_data(fixtures_dir):
    html = (fixtures_dir / "mendocino_minimal.html").read_text()
    soup = BeautifulSoup(html, "html.parser")
    scraper = make_scraper(
        MendocinoScraper,
        "http://www.co.mendocino.ca.us/acr/cgi-bin/currentFR.pl",
        county_name="Mendocino",
    )
    return scraper._parse(soup)


def test_mendocino_extracts_voter_turnout(fixtures_dir):
    data = _mendocino_data(fixtures_dir)
    turnout = data["voter_turnout"]
    assert turnout["registered_voters"] == 51234
    assert turnout["ballots_cast"] == 22617
    assert turnout["turnout_percentage"] == 44.14
    assert turnout["precincts_reported"] == "80 of 80"


def test_mendocino_extracts_page_title_and_last_updated(fixtures_dir):
    data = _mendocino_data(fixtures_dir)
    assert data["page_title"] == "Statewide Special Election - November 4, 2025"
    assert data["last_updated"] == "11/05/2025 09:00 AM"
    assert data["county_name"] == "Mendocino"


def test_mendocino_dedupes_repeated_contest(fixtures_dir):
    data = _mendocino_data(fixtures_dir)
    titles = [c["title"] for c in data["contests"]]
    # Fixture has STATE PROPOSITION 50 listed twice; parser should dedupe.
    assert titles.count("STATE PROPOSITION 50") == 1
    assert "MEASURE A SCHOOL BOND" in titles


def test_mendocino_extracts_choice_totals(fixtures_dir):
    data = _mendocino_data(fixtures_dir)
    prop50 = next(c for c in data["contests"] if c["title"] == "STATE PROPOSITION 50")
    yes = next(ch for ch in prop50["choices"] if ch["name"] == "YES")
    no = next(ch for ch in prop50["choices"] if ch["name"] == "NO")
    assert yes["votes"] == "11000"
    assert yes["percentage"] == "61.10%"
    assert no["votes"] == "7000"


def test_mendocino_skips_summary_rows(fixtures_dir):
    """Cast Votes / Undervotes / Overvotes rows must not appear as choices."""
    data = _mendocino_data(fixtures_dir)
    for contest in data["contests"]:
        names = [c["name"] for c in contest["choices"]]
        for forbidden in ("Cast Votes:", "Undervotes:", "Overvotes:", "Choice"):
            assert forbidden not in names


# ---------------------------------------------------------------------------
# Napa — PDF-extracted text
# ---------------------------------------------------------------------------


def _napa_data(fixtures_dir):
    text = (fixtures_dir / "napa_summary.txt").read_text()
    scraper = make_scraper(
        NapaScraper,
        "https://www.napacounty.gov/DocumentCenter/View/39913/",
        county_name="Napa",
    )
    return scraper._parse(text)


def test_napa_extracts_voter_turnout(fixtures_dir):
    data = _napa_data(fixtures_dir)
    turnout = data["voter_turnout"]
    assert turnout["ballots_cast"] == 52409
    assert turnout["registered_voters"] == 86390
    assert turnout["turnout_percentage"] == 60.67
    assert turnout["precincts_reported"] == "65 of 65 (100.00%)"


def test_napa_extracts_page_title(fixtures_dir):
    data = _napa_data(fixtures_dir)
    assert "Statewide Special Election" in data["page_title"]


def test_napa_extracts_contests(fixtures_dir):
    data = _napa_data(fixtures_dir)
    titles = [c["title"] for c in data["contests"]]
    assert any("Proposition 50" in t for t in titles)


# ---------------------------------------------------------------------------
# Solano — PDF-extracted text
# ---------------------------------------------------------------------------


def _solano_data(fixtures_dir):
    text = (fixtures_dir / "solano_summary.txt").read_text()
    scraper = make_scraper(
        SolanoScraper,
        "https://content.solanocounty.gov/sites/default/files/2026-01/Official_Summary_Results_-_SIGNED.pdf",
        county_name="Solano",
    )
    return scraper._parse(text)


def test_solano_extracts_voter_turnout(fixtures_dir):
    data = _solano_data(fixtures_dir)
    turnout = data["voter_turnout"]
    assert turnout["ballots_cast"] == 145294
    assert turnout["registered_voters"] == 277461
    assert turnout["turnout_percentage"] == 52.37
    assert turnout["precincts_reported"] == "159 / 159 (100.00%)"


def test_solano_extracts_contests_with_choices(fixtures_dir):
    data = _solano_data(fixtures_dir)
    titles = [c["title"] for c in data["contests"]]
    assert any("PROPOSITION 50" in t.upper() for t in titles)

    prop50 = next(c for c in data["contests"] if "PROPOSITION 50" in c["title"].upper())
    names = {ch["name"] for ch in prop50["choices"]}
    assert "YES" in names and "NO" in names

    yes = next(ch for ch in prop50["choices"] if ch["name"] == "YES")
    assert yes["votes"] == "92370"
    assert yes["percentage"] == "63.67%"
