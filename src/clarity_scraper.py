#!/usr/bin/env python3
"""
Clarity Elections Web Scraper
Scrapes election results from Clarity Elections websites every 30 minutes starting at 8:01 PM
"""

import time
import json
import os
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
import schedule
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from scraper_utils import _parse_choice_str

# Configuration
TARGET_URL = "https://results.enr.clarityelections.com/CA/Marin/124182/web.345435/#/summary"
DATA_DIR = Path("election_data")
START_TIME = "20:01"  # 8:01 PM
INTERVAL_MINUTES = 30

# Create data directory if it doesn't exist
DATA_DIR.mkdir(exist_ok=True)


# Security: Allowed domains for URL validation
ALLOWED_DOMAINS = [
    'results.enr.clarityelections.com',
    'clarityelections.com',
    'livevoterturnout.com',
    'sfelections.org',
    'www.sf.gov',
    'sf.gov',
    'santacruzcountyca.gov',
    'votescount.santacruzcountyca.gov',
    'rovservices.sccgov.org',   # Santa Clara may switch to county site for June 2
    'sccgov.org',
    'www.sjgov.org',
    'sjgov.org',
]

# Security: Allowed file extensions
ALLOWED_FILE_EXTENSIONS = {'xml', 'csv', 'xls', 'json'}


class RateLimiter:
    """Rate limiter to prevent overwhelming election servers"""
    
    # Per-domain rate limits (seconds between requests)
    DOMAIN_LIMITS = {
        'results.enr.clarityelections.com': 3.0,
        'clarityelections.com': 3.0,
        'livevoterturnout.com': 2.0,
        'sfelections.org': 2.0,
        'santacruzcountyca.gov': 1.5,
    }
    
    def __init__(self, domain=None):
        self.domain = domain
        self.min_interval = self.DOMAIN_LIMITS.get(domain, 2.0)
        self.last_request = 0
    
    def wait(self):
        """Enforce minimum delay between requests"""
        elapsed = time.time() - self.last_request
        if elapsed < self.min_interval:
            wait_time = self.min_interval - elapsed
            time.sleep(wait_time)
        self.last_request = time.time()


def validate_url(url):
    """
    Validate URL against whitelist of allowed domains.
    Prevents SSRF attacks when code is shared publicly.
    """
    try:
        parsed = urlparse(url)
        
        # Check scheme
        if parsed.scheme not in ['http', 'https']:
            raise ValueError(f"Invalid URL scheme '{parsed.scheme}'. Only http and https allowed.")
        
        # Check for localhost/internal IPs (prevent accidents)
        if parsed.netloc in ['localhost', '127.0.0.1', '0.0.0.0', '::1']:
            raise ValueError(f"Internal URLs not allowed: {parsed.netloc}")
        
        # Check domain against whitelist (flexible matching for subdomains)
        is_allowed = False
        for allowed_domain in ALLOWED_DOMAINS:
            if parsed.netloc == allowed_domain or parsed.netloc.endswith('.' + allowed_domain):
                is_allowed = True
                break
        
        if not is_allowed:
            raise ValueError(
                f"Domain '{parsed.netloc}' not in allowed list. "
                f"Allowed domains: {', '.join(ALLOWED_DOMAINS)}"
            )
        
        return url
    except Exception as e:
        raise ValueError(f"URL validation failed: {e}")


def validate_and_secure_filepath(base_dir, filename, extension):
    """
    Validate file path to prevent path traversal attacks.
    Returns a secure filepath with random suffix.
    """
    # Validate extension
    if extension not in ALLOWED_FILE_EXTENSIONS:
        raise ValueError(f"File extension '{extension}' not allowed. Allowed: {ALLOWED_FILE_EXTENSIONS}")
    
    # Create secure filename with random suffix
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    random_suffix = secrets.token_hex(8)
    secure_filename = f"{filename}_{timestamp}_{random_suffix}.{extension}"
    
    # Construct full path
    filepath = Path(base_dir) / secure_filename
    filepath = filepath.resolve()
    
    # Verify path is within base directory (prevent path traversal)
    base_dir_resolved = Path(base_dir).resolve()
    if not filepath.is_relative_to(base_dir_resolved):
        raise ValueError(f"Path traversal attempt detected: {filepath}")
    
    return filepath


class ClarityScraper:
    """Scraper for Clarity Elections websites"""
    
    def __init__(self, url, reuse_driver=None, save_files=True):
        # Security: Validate URL before use
        self.url = validate_url(url)
        self.base_url = self._extract_base_url(self.url)
        self.driver = reuse_driver  # Allow driver reuse
        self.owns_driver = reuse_driver is None  # Track if we should close driver
        self.session = self._create_session()  # HTTP session with connection pooling
        self.save_files = save_files  # Control whether to save files

        # Security: Initialize rate limiter for this domain
        parsed_url = urlparse(self.url)
        self.rate_limiter = RateLimiter(parsed_url.netloc)

        # Derive a display label for error messages from the URL path (e.g. "Marin").
        m = re.search(r"/CA/([^/]+)/", self.url)
        self._county_label = m.group(1) if m else "Clarity"
        
    def _extract_base_url(self, url):
        """Extract base URL from the full URL"""
        # Remove hash portion and trailing slashes
        return url.split('#')[0].rstrip('/')
    
    def _create_session(self):
        """Create HTTP session with connection pooling and retry strategy"""
        session = requests.Session()
        
        # Security: Explicitly enforce SSL certificate verification
        session.verify = True
        
        # Configure retry strategy for resilience
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        
        # Configure HTTP adapter with connection pooling
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=20
        )
        
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        
        # Set default headers for better performance
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive'
        })
        
        return session
    
    def setup_driver(self):
        """Initialize Selenium WebDriver with anti-detection options"""
        options = webdriver.ChromeOptions()
        options.add_argument('--headless')  # Run in background
        options.add_argument('--no-sandbox')  # Required for stability
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-blink-features=AutomationControlled')
        
        # Anti-detection measures
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.7444.60 Safari/537.36')
        
        # Security: Disable unnecessary features
        options.add_argument('--disable-extensions')
        options.add_argument('--disable-gpu')
        options.add_argument('--disable-popup-blocking')
        options.add_argument('--window-size=1920,1080')

        # Performance flags
        options.add_argument('--disable-images')
        options.add_argument('--blink-settings=imagesEnabled=false')
        options.add_argument('--disable-plugins')
        options.add_argument('--disable-software-rasterizer')
        options.add_argument('--disable-background-networking')
        options.add_argument('--disable-default-apps')
        options.add_argument('--disable-sync')
        options.add_argument('--disable-translate')
        options.add_argument('--no-first-run')

        # Eager page load strategy - don't wait for all resources
        options.page_load_strategy = 'eager'

        self.driver = webdriver.Chrome(options=options)
        
        # Execute script to remove webdriver detection
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        self.driver.implicitly_wait(0)  # Set to 0 to prevent implicit wait hangs
        self.driver.set_page_load_timeout(20)  # Reduced from 30s to 20s
        
    def _smart_wait_for_content(self):
        """Wait for JS-rendered contest/turnout content to appear (max 30s).

        We deliberately exclude generic tags like h1/h2/h3 because every SPA
        shell renders those immediately — before any election data is injected
        — causing a false-positive that lets the scraper proceed too early.
        The selectors here are specific enough that a match means real data
        is present.
        """
        CONTENT_SELECTORS = [
            "[class*='contest']",
            "[class*='turnout']",
            "[class*='result']",
            "[class*='summary']",
            "table",
        ]
        try:
            WebDriverWait(self.driver, 30).until(
                lambda d: any(
                    d.find_elements(By.CSS_SELECTOR, sel)
                    for sel in CONTENT_SELECTORS
                )
            )
            print("Content detected")
        except TimeoutException:
            print("Content wait timeout after 30s — continuing with available content")

    def _extract_last_updated(self):
        """Optimized extraction of last updated timestamp"""
        try:
            # Priority ordered selectors for efficiency
            selectors = [
                "//div[contains(., 'Last updated')]",
                "//*[contains(text(), '2025') and (contains(text(), 'AM') or contains(text(), 'PM'))]",
                "//time",
                "//*[contains(@class, 'updated') or contains(@class, 'timestamp')]"
            ]
            
            for selector in selectors:
                try:
                    elem = self.driver.find_element(By.XPATH, selector)
                    text = elem.text.strip()
                    
                    # Quick validation
                    if text and (('updated' in text.lower()) or ('AM' in text or 'PM' in text)):
                        # Extract timestamp if in large text block
                        if 'Last updated' in text and len(text) > 100:
                            match = re.search(r'(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+\w+\s+\d+,\s+\d{4},\s+\d{1,2}:\d{2}:\d{2}\s+(AM|PM)', text)
                            if match:
                                return match.group(0)
                        return text
                except NoSuchElementException:
                    continue
            
            return None
        except Exception as e:
            print(f"⚠ Error finding last updated: {e}")
            return None

    def _extract_voter_turnout_optimized(self):
        """Optimized voter turnout extraction with broader pattern matching"""
        try:
            turnout_data = {}
            
            # Try multiple selectors for turnout section
            turnout_selectors = [
                "//div[contains(., 'VOTER TURNOUT')]",
                "//section[contains(., 'Voter Turnout')]",
                "//div[contains(., 'Voter Turnout')]",
                "//div[contains(., 'TURNOUT')]",
                "//*[contains(@class, 'turnout')]",
                "//div[contains(., 'Ballots Cast')]//ancestor::div[1]",
            ]
            
            turnout_section = None
            section_text = ""
            
            for selector in turnout_selectors:
                try:
                    turnout_section = self.driver.find_element(By.XPATH, selector)
                    section_text = turnout_section.text
                    break
                except NoSuchElementException:
                    continue
            
            # If no specific section found, try full page text
            if not turnout_section:
                try:
                    section_text = self.driver.find_element(By.TAG_NAME, 'body').text
                except:
                    pass
            
            if section_text:
                # Extract ballots cast (broader patterns)
                ballots_patterns = [
                    r'Ballots Cast[:\s]*(\d+(?:,\d+)*)',
                    r'Total Ballots Cast[:\s]*(\d+(?:,\d+)*)',
                    r'Ballots Counted[:\s]*(\d+(?:,\d+)*)',
                ]
                for pattern in ballots_patterns:
                    ballots_match = re.search(pattern, section_text, re.IGNORECASE)
                    if ballots_match:
                        turnout_data['ballots_cast'] = int(ballots_match.group(1).replace(',', ''))
                        break
                
                # Extract registered voters (broader patterns)
                registered_patterns = [
                    r'Registered Voters[:\s]*(\d+(?:,\d+)*)',
                    r'Total Registered Voters[:\s]*(\d+(?:,\d+)*)',
                    r'Voters Registered[:\s]*(\d+(?:,\d+)*)',
                ]
                for pattern in registered_patterns:
                    registered_match = re.search(pattern, section_text, re.IGNORECASE)
                    if registered_match:
                        turnout_data['registered_voters'] = int(registered_match.group(1).replace(',', ''))
                        break
                
                # Extract precincts — require the count to appear on a line with "Precincts Reporting"
                # to avoid matching contest pagination like "0 of 6 contests".
                precincts_patterns = [
                    r'(\d+(?:,\d+)?)\s+of\s+(\d+(?:,\d+)?)\s+Precincts\s+Report',
                    r'Precincts\s+Report\w*[:\s]+(\d+(?:,\d+)?)\s+of\s+(\d+(?:,\d+)?)',
                ]
                for pattern in precincts_patterns:
                    m = re.search(pattern, section_text, re.IGNORECASE)
                    if m:
                        turnout_data['precincts_reported'] = f"{m.group(1)} of {m.group(2)} Precincts Reported"
                        break

            # If text parsing didn't find X of Y, use Selenium to find the donut chart
            # container near the "Precincts Reporting" label and extract the X/Y count
            # which appears as a sibling/child element (e.g. "930/930" below the label).
            if 'precincts_reported' not in turnout_data:
                try:
                    label_elems = self.driver.find_elements(
                        By.XPATH, "//*[contains(text(), 'Precincts Reporting')]"
                    )
                    for label_elem in label_elems:
                        # Walk up to parent, then search all text within it for X/Y fraction
                        for level in range(1, 4):
                            try:
                                ancestor = label_elem.find_element(
                                    By.XPATH, ".//" + "/..".join([""] * (level + 1))
                                )
                            except Exception:
                                # Simpler traversal: use JavaScript to get ancestor
                                try:
                                    ancestor = self.driver.execute_script(
                                        "var e = arguments[0]; for(var i=0;i<%d;i++) e=e.parentElement; return e;" % level,
                                        label_elem
                                    )
                                except Exception:
                                    break
                            if ancestor is None:
                                break
                            container_text = ancestor.text.strip()
                            # Look for X/Y pattern (slash or "of")
                            m = re.search(r'(\d[\d,]*)\s*[/]\s*(\d[\d,]*)', container_text)
                            if m:
                                a, b = m.group(1).replace(',', ''), m.group(2).replace(',', '')
                                turnout_data['precincts_reported'] = f"{a} of {b} Precincts Reported"
                                break
                            m = re.search(r'(\d[\d,]+)\s+of\s+(\d[\d,]+)', container_text)
                            if m:
                                turnout_data['precincts_reported'] = f"{m.group(1)} of {m.group(2)} Precincts Reported"
                                break
                        if 'precincts_reported' in turnout_data:
                            break
                except Exception:
                    pass

            # Always calculate turnout from ballots/registered — more reliable than scraping
            # the "100%" precincts-reporting figure that Clarity sites display prominently.
            if 'ballots_cast' in turnout_data and 'registered_voters' in turnout_data:
                if turnout_data['registered_voters'] > 0:
                    turnout_data['turnout_percentage'] = round(
                        (turnout_data['ballots_cast'] / turnout_data['registered_voters']) * 100, 1
                    )
            
            return turnout_data
            
        except Exception as e:
            print(f"⚠ Error extracting voter turnout: {e}")
            return {}

    def close_driver(self):
        """Close the WebDriver only if we own it"""
        if self.driver and self.owns_driver:
            self.driver.quit()
            self.driver = None
    
    def check_for_json_data(self):
        """
        Attempt to find JSON data endpoints by analyzing network requests
        Clarity sites often load data from JSON APIs
        """
        try:
            # Common Clarity Elections JSON endpoints
            potential_endpoints = [
                f"{self.base_url}/json/en/summary.json",
                f"{self.base_url}/json/summary.json",
                f"{self.base_url}/en/summary.json",
            ]
            
            for endpoint in potential_endpoints:
                try:
                    # Security: Rate limit requests
                    self.rate_limiter.wait()
                    
                    # Security: Explicit SSL verification
                    response = self.session.get(
                        endpoint,
                        headers={'Referer': self.url},
                        timeout=10,
                        verify=True
                    )
                    if response.status_code == 200:
                        return response.json()
                except:
                    continue
                    
        except Exception as e:
            print(f"Error checking JSON endpoints: {e}")
        
        return None
    
    # CSS selectors used to identify individual contest blocks on Clarity SPAs.
    # Clarity renders each contest as a Bootstrap card: <div class="card contest">.
    # We try precise selectors first and fall back to the broad net.
    _CONTEST_CARD_SELECTOR = "[class*='contest']"
    _CONTEST_PRECISE_SELECTORS = [
        ".card.contest",
        ".contest.col-12",
        "[class*='contest-container']",
    ]

    # Title selectors tried in priority order inside each contest card.
    _TITLE_SELECTORS = [
        ".contest-title",
        "[class*='contest-name']",
        "[class*='contest-header']",
        "h2",
        "h3",
        "h4",
        "[class*='title']",
        "[class*='name']",
    ]

    def _find_contest_elements(self):
        """Return contest card elements from the current page.

        Clarity renders each contest as a Bootstrap card-level div, e.g.:
          <div class="card contest"> or <div class="contest mb-3 col-12">

        We try precise card-level selectors first.  If none match we fall back
        to the broad [class*='contest'] net and filter out elements whose text
        is clearly not a full contest card (empty, just a number, etc.).
        """
        # Try precise selectors first — these target the card level directly
        # and avoid picking up sub-elements like contest-name or btn-contest.
        for sel in self._CONTEST_PRECISE_SELECTORS:
            elems = self.driver.find_elements(By.CSS_SELECTOR, sel)
            elems = [e for e in elems if e.text.strip() and len(e.text.strip()) > 10]
            if elems:
                return elems

        # Fallback: broad selector, keep only elements that look like full cards
        # (have substantial text containing vote data).
        all_elems = self.driver.find_elements(By.CSS_SELECTOR, self._CONTEST_CARD_SELECTOR)
        result = []
        for elem in all_elems:
            try:
                txt = elem.text.strip()
                if not txt or len(txt) < 10:
                    continue
                # Must contain something that looks like vote data
                if re.search(r'\d+\.?\d*%', txt):
                    result.append(elem)
            except Exception:
                continue
        return result

    def scrape_with_selenium(self):
        """Scrape data using Selenium to handle JavaScript rendering.

        An overall 60-second hard cap is enforced so a single county can
        never block the process indefinitely.
        """
        # Hard cap: the entire Selenium session must finish within 60 seconds.
        SELENIUM_HARD_TIMEOUT = 60
        deadline = time.time() + SELENIUM_HARD_TIMEOUT

        def _timed_out():
            return time.time() > deadline

        try:
            if not self.driver:
                self.setup_driver()
            print(f"Loading page: {self.url}")

            try:
                self.driver.get(self.url)

                # Check if request was blocked by CloudFront
                if "ERROR" in self.driver.title or "request could not be satisfied" in self.driver.title.lower():
                    print("Page blocked by CloudFront (403 error)")
                    print("  Attempting retry with brief delay...")

                    time.sleep(5)
                    self.driver.refresh()
                    time.sleep(3)

                    if "ERROR" in self.driver.title or "request could not be satisfied" in self.driver.page_source.lower():
                        print("Still blocked by CloudFront after retry")
                        data = {
                            'timestamp': datetime.now().isoformat(),
                            'url': self.url,
                            'page_title': self.driver.title,
                            'cloudfront_blocked': True,
                            'voter_turnout': {},
                            'contests': [],
                            'error': 'CloudFront 403 block - high traffic protection'
                        }
                        return data
                    else:
                        print("Retry successful after CloudFront block")

                print("Page loaded successfully")

            except Exception as e:
                print(f"Page load issue: {e}")
                # Continue — page may be partially loaded (eager strategy).

            if _timed_out():
                print("Hard timeout reached after page load — aborting")
                return None

            # Wait for JS-rendered content.
            print("Waiting for JavaScript to execute...")
            self._smart_wait_for_content()
            print("Wait complete")

            if _timed_out():
                print("Hard timeout reached after content wait — aborting")
                return None

            # Quick recon — log element count before extraction.
            contest_elements = self.driver.find_elements(By.CSS_SELECTOR, self._CONTEST_CARD_SELECTOR)
            print(f"Raw [class*='contest'] element count: {len(contest_elements)}")
            if not contest_elements:
                print("No contest elements found — page may not have results yet")

            # Build result skeleton.
            data = {
                'timestamp': datetime.now().isoformat(),
                'url': self.url,
                'page_title': self.driver.title,
                'voter_turnout': {},
                'contests': []
            }

            data['last_updated'] = self._extract_last_updated()

            print("Looking for voter turnout data...")
            data['voter_turnout'] = self._extract_voter_turnout_optimized()
            if data['voter_turnout']:
                print(f"Extracted voter turnout data: {data['voter_turnout']}")
            else:
                print("No voter turnout data extracted")

            if _timed_out():
                print("Hard timeout reached before contest extraction — returning partial data")
                return data

            # Extract contest information.
            skip_patterns = [
                r'^STATE$',
                r'^COUNTY$',
                r'^LOCAL$',
                r'^FEDERAL$',
                r'^showing',
                r'^\s*$',
            ]

            try:
                contests = self._find_contest_elements()
                print(f"Leaf contest elements after filtering: {len(contests)}")

                for contest in contests:
                    if _timed_out():
                        print("Hard timeout hit during contest extraction — stopping early")
                        break

                    contest_data = {
                        'title': None,
                        'precincts_reporting': None,
                        'choices': []
                    }

                    # Attempt title extraction using priority-ordered selectors.
                    for title_sel in self._TITLE_SELECTORS:
                        try:
                            title_elem = contest.find_element(By.CSS_SELECTOR, title_sel)
                            candidate = title_elem.text.strip()
                            if candidate:
                                contest_data['title'] = candidate
                                break
                        except NoSuchElementException:
                            continue

                    # If CSS selectors found nothing, fall back to the first
                    # non-empty line of the contest element's own text.
                    if not contest_data['title']:
                        try:
                            lines = [l.strip() for l in contest.text.split('\n') if l.strip()]
                            if lines:
                                contest_data['title'] = lines[0]
                        except Exception:
                            pass

                    # Skip navigation / section-header pseudo-contests.
                    if contest_data['title']:
                        should_skip = any(
                            re.match(p, contest_data['title'], re.IGNORECASE)
                            for p in skip_patterns
                        )
                        if should_skip:
                            continue

                    # Precincts reporting.
                    try:
                        precincts_selectors = [
                            ".//*[contains(text(), 'Precincts Reporting')]",
                            ".//div[contains(., 'Precincts Reporting')]",
                            ".//span[contains(., 'Precincts Reporting')]",
                        ]

                        precincts_text = None
                        for selector in precincts_selectors:
                            try:
                                precincts_elem = contest.find_element(By.XPATH, selector)
                                full_text = precincts_elem.text.strip()

                                if 'Precincts Reporting' in full_text and '%' in full_text:
                                    precincts_text = full_text
                                    break
                                elif 'Precincts Reporting' in full_text:
                                    try:
                                        parent = precincts_elem.find_element(By.XPATH, "./..")
                                        parent_text = parent.text.strip()
                                        if '%' in parent_text:
                                            lines = parent_text.split('\n')
                                            for line in lines:
                                                if 'Precincts Reporting' in line and '%' in line:
                                                    precincts_text = line.strip()
                                                    break
                                            if not precincts_text:
                                                match = re.search(r'Precincts Reporting\s*(\d+%)', parent_text)
                                                if match:
                                                    precincts_text = f"Precincts Reporting {match.group(1)}"
                                    except Exception:
                                        pass

                                    if not precincts_text:
                                        precincts_text = full_text
                                    break
                            except NoSuchElementException:
                                continue

                        if precincts_text and 'Precincts Reporting' in precincts_text:
                            formatted_text = re.sub(
                                r'Precincts Reporting(\d+%)',
                                r'Precincts Reporting \1',
                                precincts_text
                            )
                            contest_data['precincts_reporting'] = formatted_text
                        else:
                            contest_data['precincts_reporting'] = precincts_text

                    except Exception:
                        contest_data['precincts_reporting'] = None

                    # Choices / candidates.
                    # Try CSS selectors first; fall back to parsing the contest's full text.
                    # Both paths parse directly into the canonical dict shape:
                    #   {'name': str, 'votes': int, 'pct': float}
                    try:
                        rows = contest.find_elements(By.CSS_SELECTOR, "tr, .choice, [class*='choice'], [class*='candidate']")
                        seen_choices = set()
                        for row in rows:
                            try:
                                choice_text = row.text.strip()
                                if (choice_text
                                        and choice_text not in seen_choices
                                        and not choice_text.startswith('Candidate')
                                        and not choice_text.startswith('Choice')
                                        and choice_text != 'Percentage Votes'):
                                    seen_choices.add(choice_text)
                                    choice = _parse_choice_str(
                                        choice_text, self._county_label,
                                        contest_data.get('title', '')
                                    )
                                    if choice is not None:
                                        contest_data['choices'].append(choice)
                            except Exception:
                                pass
                    except NoSuchElementException:
                        pass

                    # Text-based fallback: parse the contest element's full text.
                    # Clarity renders each choice as "Name\nXX.XX% X,XXX" or
                    # "Name\nX,XXX XX.XX%" within the card text.
                    if not contest_data['choices']:
                        try:
                            full_text = contest.text
                            lines = [l.strip() for l in full_text.split('\n') if l.strip()]
                            noise = {'Percentage', 'Votes', 'Percentage Votes', 'Map', 'Chart',
                                     'Precincts Reporting', contest_data['title']}
                            i = 0
                            while i < len(lines):
                                line = lines[i]
                                if (line in noise
                                        or 'Precincts' in line
                                        or 'Vote For' in line
                                        or 'Vote Cast' in line
                                        or re.match(r'^\(?\d+\)?$', line)):
                                    i += 1
                                    continue
                                next_line = lines[i + 1] if i + 1 < len(lines) else ''
                                m1 = re.search(r'(\d+\.?\d*)%\s*([\d,]+)', next_line)
                                m2 = re.search(r'([\d,]+)\s+(\d+\.?\d*)%', next_line)
                                if m1 or m2:
                                    # Build the canonical string, then parse it.
                                    raw = (
                                        f"{line} {m1.group(1)}% {m1.group(2)}"
                                        if m1 else
                                        f"{line} {m2.group(1)} {m2.group(2)}%"
                                    )
                                    choice = _parse_choice_str(
                                        raw, self._county_label,
                                        contest_data.get('title', '')
                                    )
                                    if choice is not None:
                                        contest_data['choices'].append(choice)
                                    i += 2
                                else:
                                    i += 1
                        except Exception:
                            pass

                    if contest_data['title']:
                        data['contests'].append(contest_data)

                if data['contests']:
                    original_count = len(data['contests'])
                    data['contests'] = self._deduplicate_contests(data['contests'])
                    print(f"Extracted {original_count} contests, deduplicated to {len(data['contests'])} unique contests")
                else:
                    print("0 contests extracted after filtering")

            except Exception as e:
                print(f"Error extracting contests: {e}")

            # HTML snapshot removed to reduce file size.

            # Check reports section using the same driver.
            if not _timed_out():
                try:
                    reports_url = self.url.replace('#/summary', '#/reports')
                    print(f"Checking reports section: {reports_url}")
                    self.driver.get(reports_url)
                    # Wait up to 5s for body — always present, so this is just
                    # a guard against a completely failed navigation.
                    WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((By.TAG_NAME, "body"))
                    )

                    download_links = self.driver.find_elements(
                        By.XPATH,
                        "//a[contains(@href, '.xml') or contains(@href, '.csv') or contains(@href, '.xls')]"
                    )

                    reports = []
                    for link in download_links:
                        href = link.get_attribute('href')
                        reports.append({
                            'text': link.text,
                            'url': href,
                            'type': href.split('.')[-1] if href else 'unknown'
                        })

                    data['reports'] = reports
                    if reports:
                        print(f"Found {len(reports)} downloadable reports")
                    else:
                        print("No downloadable reports found")
                except Exception as e:
                    print(f"Reports check failed (non-fatal): {e}")
                    data['reports'] = []
            else:
                print("Hard timeout reached — skipping reports section")
                data['reports'] = []

            return data

        except Exception as e:
            print(f"Error during Selenium scraping: {e}")
            return None
        finally:
            self.close_driver()
    
    def _deduplicate_contests(self, contests):
        """Remove duplicate contests based on title"""
        seen_titles = set()
        unique_contests = []
        
        for contest in contests:
            title = contest.get('title')
            if title and title not in seen_titles:
                seen_titles.add(title)
                unique_contests.append(contest)
        
        return unique_contests
    
    def check_reports_section(self):
        """
        Check if there are downloadable reports (XML, CSV, XLS)
        These are often the best way to get structured data
        """
        try:
            if not self.driver:
                self.setup_driver()
            reports_url = self.url.replace('#/summary', '#/reports')
            print(f"Checking reports section: {reports_url}")
            try:
                self.driver.get(reports_url)
                time.sleep(5)
            except Exception as e:
                print(f"⚠ Reports page load issue: {e}")
                return []
            
            # Look for download links
            download_links = self.driver.find_elements(By.XPATH, "//a[contains(@href, '.xml') or contains(@href, '.csv') or contains(@href, '.xls')]")
            
            reports = []
            for link in download_links:
                reports.append({
                    'text': link.text,
                    'url': link.get_attribute('href'),
                    'type': link.get_attribute('href').split('.')[-1]
                })
            
            return reports
            
        except Exception as e:
            print(f"Error checking reports section: {e}")
            return []
        finally:
            self.close_driver()
    
    def download_report(self, report_url, report_type):
        """Download a structured data report file"""
        try:
            # Security: Rate limit requests
            self.rate_limiter.wait()
            
            # Security: Explicit SSL verification
            response = self.session.get(
                report_url,
                headers={'Referer': self.url},
                timeout=30,
                verify=True
            )
            
            if response.status_code == 200:
                # Security: Use secure filepath with validation
                filename = validate_and_secure_filepath(DATA_DIR, "report", report_type)
                
                with open(filename, 'wb') as f:
                    f.write(response.content)
                
                print(f"Downloaded report: {filename}")
                return filename
                
        except Exception as e:
            print(f"Error downloading report: {e}")
        
        return None
    
    def scrape(self):
        """Main scraping method"""
        print(f"\n{'='*60}")
        print(f"Starting scrape at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")
        
        results = {
            'scrape_timestamp': datetime.now().isoformat(),
            'json_data': None,
            'selenium_data': None,
            'reports': []
        }
        
        # Try 1: Check for direct JSON endpoints
        print("\n[1] Checking for JSON data endpoints...")
        json_data = self.check_for_json_data()
        if json_data:
            print("JSON data found - skipping Selenium (JSON API is sufficient)")
            results['json_data'] = json_data

            # Save results to JSON file only if enabled
            if self.save_files:
                output_file = validate_and_secure_filepath(DATA_DIR, "scrape", "json")

                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(results, f, indent=2, ensure_ascii=False)

                print(f"\nResults saved to: {output_file}")
                print(f"{'='*60}\n")

            return results
        else:
            print("No JSON endpoints found")

        # Try 2: Scrape with Selenium (also checks reports section).
        # Retry once with a fresh driver if the first attempt returns 0 contests,
        # since this can happen when the SPA hasn't fully bootstrapped yet.
        print("\n[2] Scraping with Selenium...")
        selenium_data = self.scrape_with_selenium()

        if selenium_data is not None and len(selenium_data.get('contests', [])) == 0:
            print("Got 0 contests on first attempt — retrying once with a fresh driver...")
            # Ensure the previous driver is fully closed before creating a new one.
            self.close_driver()
            self.driver = None
            self.owns_driver = True
            time.sleep(3)  # Brief pause before retry.
            selenium_data = self.scrape_with_selenium()
            if selenium_data is not None:
                print(f"Retry result: {len(selenium_data.get('contests', []))} contests")
            else:
                print("Retry failed — no data returned")

        if selenium_data:
            print(f"Scraped {len(selenium_data.get('contests', []))} contests")
            results['selenium_data'] = selenium_data

            # Get reports from selenium_data (merged into scrape_with_selenium)
            reports = selenium_data.get('reports', [])
            if reports:
                print(f"Found {len(reports)} reports")
                results['reports'] = reports

                # Download the first XML or CSV report
                for report in reports:
                    if report['type'] in ['xml', 'csv']:
                        self.download_report(report['url'], report['type'])
                        break
            else:
                print("No reports found")
        else:
            print("Selenium scraping failed")
        
        # Save results to JSON file only if enabled
        if self.save_files:
            # Security: Use secure filepath with validation
            output_file = validate_and_secure_filepath(DATA_DIR, "scrape", "json")
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            
            print(f"\n✓ Results saved to: {output_file}")
            print(f"{'='*60}\n")
        
        return results


def scrape_job():
    """Job to be scheduled"""
    scraper = ClarityScraper(TARGET_URL)
    scraper.scrape()


def wait_until_start_time():
    """Start immediately - no waiting for specific time"""
    print("Starting immediately...")
    return


def main():
    """Main function to run the scraper on schedule"""
    print("="*60)
    print("CLARITY ELECTIONS SCRAPER")
    print("="*60)
    print(f"Target URL: {TARGET_URL}")
    print(f"Starts: IMMEDIATELY")
    print(f"Interval: Every {INTERVAL_MINUTES} minutes")
    print(f"Data Directory: {DATA_DIR.absolute()}")
    print("="*60)
    
    # Start immediately
    wait_until_start_time()
    
    # Run initial scrape
    print("\nRunning initial scrape...")
    scrape_job()
    
    # Schedule subsequent runs
    schedule.every(INTERVAL_MINUTES).minutes.do(scrape_job)
    
    print(f"\n✓ Scheduled to run every {INTERVAL_MINUTES} minutes")
    print("Press Ctrl+C to stop\n")
    
    # Keep running
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nScraper stopped by user")
    except Exception as e:
        print(f"\n\nError: {e}")
        raise

