"""Tests for small pure helpers on ClarityScraper."""

from src.clarity_scraper import ClarityScraper

from .conftest import make_scraper


VALID_URL = "https://results.enr.clarityelections.com/CA/Marin/124182/web.345435/#/summary"


def test_extract_base_url_strips_fragment():
    scraper = make_scraper(ClarityScraper, VALID_URL)
    assert scraper._extract_base_url(VALID_URL) == (
        "https://results.enr.clarityelections.com/CA/Marin/124182/web.345435"
    )


def test_extract_base_url_strips_trailing_slash():
    scraper = make_scraper(ClarityScraper, VALID_URL)
    assert scraper._extract_base_url("https://clarityelections.com/x/") == (
        "https://clarityelections.com/x"
    )


def test_deduplicate_contests_keeps_first_occurrence():
    scraper = make_scraper(ClarityScraper, VALID_URL)
    contests = [
        {"title": "Proposition 50", "choices": ["a"]},
        {"title": "Measure A", "choices": ["b"]},
        {"title": "Proposition 50", "choices": ["duplicate"]},
    ]
    out = scraper._deduplicate_contests(contests)
    assert [c["title"] for c in out] == ["Proposition 50", "Measure A"]
    assert out[0]["choices"] == ["a"]


def test_deduplicate_contests_drops_titleless_entries():
    scraper = make_scraper(ClarityScraper, VALID_URL)
    contests = [
        {"title": None, "choices": []},
        {"title": "", "choices": []},
        {"title": "Real", "choices": []},
    ]
    out = scraper._deduplicate_contests(contests)
    assert [c["title"] for c in out] == ["Real"]
