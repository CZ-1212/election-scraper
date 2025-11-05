"""
California Election Data Scraper

A robust Python-based election night scraper for California counties.
Supports both Clarity Elections and non-Clarity platforms.
"""

__version__ = "1.0.0"
__author__ = "Election Scraper Project"

from .clarity_scraper import ClarityScraper
from .multi_platform_scraper import (
    BaseScraper,
    LiveVoterTurnoutScraper,
    SantaCruzScraper
)

__all__ = [
    'ClarityScraper',
    'BaseScraper',
    'LiveVoterTurnoutScraper',
    'SantaCruzScraper'
]

