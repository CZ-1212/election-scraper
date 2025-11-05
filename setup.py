"""Setup script for California Election Data Scraper"""

from setuptools import setup, find_packages
from pathlib import Path

# Read the README file
this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text()

setup(
    name="california-election-scraper",
    version="1.0.0",
    author="Election Scraper Project",
    description="A robust Python-based election night scraper for California counties",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/election-scraper",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: Other/Proprietary License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.8",
    install_requires=[
        "selenium>=4.15.2",
        "requests>=2.31.0",
        "beautifulsoup4>=4.12.2",
        "schedule>=1.2.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "black>=23.0.0",
            "flake8>=6.0.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "scrape-clarity=src.test_clarity_only:main",
            "scrape-non-clarity=src.scrape_3_working:main",
        ],
    },
)

