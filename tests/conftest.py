"""Shared pytest fixtures and path setup.

Adds the project root to sys.path so `from src.* import ...` works
without requiring an editable install.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAMPLES = ROOT / "data" / "samples"


@pytest.fixture
def fixtures_dir() -> Path:
    """Return the directory containing minimal hand-written parser fixtures."""
    return FIXTURES


@pytest.fixture
def samples_dir() -> Path:
    """Return the directory containing real-world JSON samples for schema tests."""
    return SAMPLES


def make_scraper(cls, url: str, county_name: str = "Test County"):
    """Build a scraper instance without running BaseScraper.__init__.

    BaseScraper.__init__ calls validate_url() and creates a requests
    Session — both unnecessary (and the latter slow) for parser tests.
    Tests only need the parsing methods, which read self.url and
    self.county_name.
    """
    inst = cls.__new__(cls)
    inst.url = url
    inst.county_name = county_name
    return inst
