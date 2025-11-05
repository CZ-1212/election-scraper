# California Election Data Scraper

A robust Python-based election night scraper designed to extract results from multiple California county election websites, supporting both Clarity Elections and non-Clarity platforms. Optimized for high-load election night conditions with comprehensive data extraction.

## Quick Start

### Scrape Non-Clarity Counties (Fast - ~1-2 minutes)
```bash
python3 src/scrape_3_working.py
```
Scrapes: San Mateo, San Joaquin, Santa Cruz

### Scrape Clarity Counties (Comprehensive - ~5-6 minutes)
```bash
python3 src/test_clarity_only.py
```
Scrapes: Contra Costa, Marin, Santa Clara, Sonoma

## Project Structure

```
Election-Scraper/
├── src/                          # Source code
│   ├── clarity_scraper.py        # Clarity Elections scraper module
│   ├── multi_platform_scraper.py # Multi-platform scraper base
│   ├── scrape_3_working.py       # Non-Clarity production scraper
│   └── test_clarity_only.py      # Clarity production scraper
├── docs/                         # Documentation
│   ├── NON_CLARITY_STATUS.md     # Technical details for non-Clarity sites
│   └── PROJECT_STRUCTURE.md      # Detailed project structure
├── data/                         # Data directory
│   ├── samples/                  # Sample output files (committed)
│   └── .gitkeep                  # Keep directory in git
├── requirements.txt              # Python dependencies
├── LICENSE                       # MIT License
└── README.md                     # This file
```

## About Clarity Elections Sites

Clarity Elections websites (hosted at `results.enr.clarityelections.com`) are:
- **JavaScript-heavy Single Page Applications (SPAs)** - They use AngularJS or React to dynamically render content
- **CloudFront-protected** - Direct HTTP requests are often blocked by AWS CloudFront CDN
- **API-driven** - Data is loaded through JSON/XML endpoints after page load
- **Report-enabled** - Most sites provide downloadable structured data files (XML, CSV, XLS) in a "Reports" section

### How They Hide HTML Behind JavaScript

1. **Client-Side Rendering**: The initial HTML is minimal; all content is rendered by JavaScript after page load
2. **Asynchronous Data Loading**: Election results are fetched from API endpoints via AJAX after the page initializes
3. **Dynamic DOM Updates**: The page structure updates continuously as new data arrives
4. **Hash-based Routing**: URLs use hash fragments (e.g., `#/summary`) for navigation without page reloads

This scraper handles all these challenges using Selenium WebDriver to execute JavaScript and wait for dynamic content.

## Features

- **Sequential Processing**: Processes counties one at a time to handle election night server load
- **Robust Retry Logic**: 3-attempt retry mechanism with progressive backoff delays
- **Election Night Optimized**: Extended timeouts and delays designed for high-traffic conditions
- **Multi-Method Data Extraction**:
  1. Direct JSON API endpoint discovery
  2. Selenium-based scraping for JavaScript-rendered content
  3. Automatic detection and download of structured reports (XML/CSV)
- **Comprehensive Voter Turnout Data**: Extracts ballots cast, registered voters, and turnout percentages
- **Data Persistence**: Saves individual county results and summary reports as timestamped JSON files
- **Headless Operation**: Runs in background without opening browser windows

## Installation

### Prerequisites

1. **Python 3.8 or higher**
2. **Google Chrome browser** (required for Selenium ChromeDriver)
3. **ChromeDriver** - Selenium will attempt to auto-download, or install manually:

```bash
# macOS (using Homebrew)
brew install chromedriver

# Or download from: https://chromedriver.chromium.org/
```

### Setup

```bash
# 1. Clone or download this project
cd /path/to/Election\ Project

# 2. Install Python dependencies
pip install -r requirements.txt

# If you're using a virtual environment (recommended):
python3 -m venv venv
source venv/bin/activate  # On macOS/Linux
pip install -r requirements.txt
```

## Configuration

### Clarity Counties
The Clarity scraper is pre-configured with 4 California counties. Edit the `CLARITY_SITES` dictionary in `src/test_clarity_only.py` to add or modify counties:

```python
COUNTIES = {
    'Contra_Costa': 'https://results.enr.clarityelections.com/CA/Contra_Costa/124407/web.345435/#/summary',
    'Marin': 'https://results.enr.clarityelections.com/CA/Marin/124182/web.345435/#/summary',
    'Santa_Clara': 'https://results.enr.clarityelections.com/CA/Santa_Clara/125157/web.345435/#/summary',
    'Sonoma': 'https://results.enr.clarityelections.com/CA/Sonoma/124354/web.345435/#/summary'
}
```

## Usage

### Run the Non-Clarity Scraper (Fast)

```bash
python3 src/scrape_3_working.py
```

This will scrape San Mateo, San Joaquin, and Santa Cruz counties (~1-2 minutes total).

### Run the Clarity Scraper (Comprehensive)

```bash
python3 src/test_clarity_only.py
```

This will scrape Contra Costa, Marin, Santa Clara, and Sonoma counties (~5-6 minutes total).

### Output

All scraped data is saved to the `data/` directory:

```
data/
├── samples/                      # Sample output files (version controlled)
│   ├── Contra_Costa_clarity_fixed.json
│   ├── Marin_clarity_fixed.json
│   ├── Santa_Clara_clarity_fixed.json
│   ├── Sonoma_clarity_fixed.json
│   └── [County]_working_[timestamp].json
└── [Your actual scraped data - ignored by git]
```

### Sample County Output Structure

```json
{
  "county_name": "Marin",
  "county_url": "https://results.enr.clarityelections.com/CA/Marin/124182/web.345435/#/summary",
  "scrape_duration": 45.2,
  "attempt_number": 1,
  "scrape_timestamp": "2025-11-05T20:01:00.123456",
  "selenium_data": {
    "timestamp": "2025-11-05T20:01:05.789012",
    "last_updated": "Thursday, November 5 2025, 8:00:00 PM",
    "voter_turnout": {
      "ballots_cast": 152847,
      "registered_voters": 186234,
      "turnout_percentage": 82.1
    },
    "contests": [
      {
        "title": "Proposition 50",
        "precincts_reporting": "85% (127 of 150)",
        "choices": ["Yes 54.2% 82,879", "No 45.8% 69,968"]
      }
    ]
  }
}
```

### Sample Summary Output Structure

```json
{
  "election_night_scrape": true,
  "scrape_timestamp": "2025-11-05T20:04:00.000000",
  "total_duration_minutes": 4.2,
  "successful_counties": 4,
  "failed_counties": 0,
  "success_rate": 1.0,
  "detailed_results": [...]
}
```

## Alternative: Single County Scraping

The `clarity_scraper.py` module can be imported and used for single-county scraping with scheduling:

```python
from src.clarity_scraper import ClarityScraper

scraper = ClarityScraper(url, save_files=True)
result = scraper.scrape()
```

See `src/clarity_scraper.py` for scheduling and configuration options.

## Running in Background (Optional)

To run the scraper in the background and keep it running even after closing the terminal:

### Using nohup (macOS/Linux)

```bash
nohup python clarity_scraper.py > scraper.log 2>&1 &
```

### Using screen (macOS/Linux)

```bash
screen -S election-scraper
python clarity_scraper.py
# Press Ctrl+A then D to detach
# To reattach: screen -r election-scraper
```

## Troubleshooting

### ChromeDriver Issues

If you get errors about ChromeDriver:

```bash
# Check Chrome version
google-chrome --version  # Linux
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --version  # macOS

# Download matching ChromeDriver from:
# https://chromedriver.chromium.org/downloads
```

### CloudFront Blocking

If you get 403 errors, the scraper will automatically fall back to Selenium-based scraping which uses a real browser and bypasses CloudFront restrictions.

### No Data Found

- Check that the URL is correct and the election results are published
- The site may show "Zero Report" before 8:00 PM on election night
- Verify you can access the site manually in a browser

## Advanced Usage

### Custom Data Processing

To process the scraped data:

```python
import json
from pathlib import Path

data_dir = Path("data")
for file in sorted(data_dir.glob("*.json")):
    with open(file) as f:
        data = json.load(f)
        # Process data here
        vt = data.get('voter_turnout', {})
        print(f"County: {data.get('county_name')}")
        print(f"Turnout: {vt.get('turnout_percentage')}%")
        
        contests = data.get('contests', [])
        for contest in contests:
            print(f"  {contest['title']}: {contest.get('choices', [])}")
```

### Adding New Counties

To add new counties, edit the appropriate scraper file:
- **Clarity sites**: Edit `CLARITY_SITES` in `src/test_clarity_only.py`
- **Non-Clarity sites**: Edit `WORKING_SITES` in `src/scrape_3_working.py`

## Technical Details

### Scraping Strategy

1. **JSON API Discovery**: Attempts common Clarity Elections JSON endpoint patterns
2. **Selenium Scraping**: Uses Chrome WebDriver to render JavaScript and extract DOM content
3. **Reports Download**: Checks for downloadable structured data files (preferred method)

### Why Selenium?

Traditional scraping tools (like `requests` + `BeautifulSoup`) don't work because:
- Content doesn't exist in initial HTML
- JavaScript must execute to fetch and render data
- CloudFront blocks simple HTTP requests

Selenium solves this by:
- Running a real Chrome browser
- Executing all JavaScript
- Waiting for dynamic content to load
- Bypassing bot detection

## Counties Supported

### Clarity Elections Platform (4 counties)
- ✅ Contra Costa County
- ✅ Marin County
- ✅ Santa Clara County
- ✅ Sonoma County

### Non-Clarity Platforms (3 counties)
- ✅ San Mateo County (LiveVoterTurnout)
- ✅ San Joaquin County (LiveVoterTurnout)
- ✅ Santa Cruz County (Custom platform)

**Note**: San Francisco County is not currently supported due to JavaScript framework incompatibility. See `docs/NON_CLARITY_STATUS.md` for technical details.

## Contributing

Contributions are welcome! To add support for new counties:
1. Identify the election results platform
2. Create a new scraper class in `src/multi_platform_scraper.py`
3. Add the county to the appropriate production scraper
4. Test thoroughly and submit a pull request

## License

**Custom Non-Commercial License** - see LICENSE file for full details.

**Key Restrictions:**
- ✅ Free for personal, educational, and research use
- ❌ **No resale or commercial use** without permission
- ✅ Modifications allowed but must keep same restrictions
- ✅ Attribution required

This is an educational/research tool. Use responsibly and respect the terms of service of the websites you scrape.

## Documentation

- [Project Structure](docs/PROJECT_STRUCTURE.md) - Detailed codebase overview
- [Non-Clarity Technical Details](docs/NON_CLARITY_STATUS.md) - Technical implementation details

## Support

For issues or questions, check:
- [Selenium Documentation](https://selenium-python.readthedocs.io/)
- [Clarity Elections Open Data Project](https://github.com/openelections/clarify)

