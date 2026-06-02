#!/usr/bin/env python3
"""
Multi-Platform Election Data Scraper
Supports multiple election data platforms:
- Clarity Elections (existing)
- LiveVoterTurnout.com (San Mateo, San Joaquin)
- SF Elections (San Francisco)
- Santa Cruz County
"""

import time
import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from bs4 import BeautifulSoup
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# Security: Allowed domains for URL validation
ALLOWED_DOMAINS = [
    'results.enr.clarityelections.com',
    'clarityelections.com',
    'livevoterturnout.com',
    'sfelections.org',
    'www.sf.gov',               # SF Elections moved to sf.gov for June 2026
    'sf.gov',
    'santacruzcountyca.gov',
    'votescount.santacruzcountyca.gov',
    'alamedacountyca.gov',
    'acvote.alamedacountyca.gov',
    'mendocinocounty.gov',
    'co.mendocino.ca.us',
    'countyofmonterey.gov',
    'napacounty.gov',
    'content.solanocounty.gov',
    'solanocounty.gov',
    'rovservices.sccgov.org',   # Santa Clara switched from Clarity to county site
    'sccgov.org',
    'www.sjgov.org',            # San Joaquin moved off LiveVoterTurnout
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
        'www.livevoterturnout.com': 2.0,
        'sfelections.org': 2.0,
        'santacruzcountyca.gov': 1.5,
        'www2.santacruzcountyca.gov': 1.5,
        'alamedacountyca.gov': 2.0,
        'mendocinocounty.gov': 2.0,
        'www.mendocinocounty.gov': 2.0,
        'co.mendocino.ca.us': 2.0,
        'www.co.mendocino.ca.us': 2.0,
        'countyofmonterey.gov': 2.0,
        'www.countyofmonterey.gov': 2.0,
        'napacounty.gov': 2.0,
        'www.napacounty.gov': 2.0,
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


class BaseScraper:
    """Base scraper class with common functionality"""
    
    def __init__(self, url, county_name, reuse_driver=None):
        # Security: Validate URL before use
        self.url = validate_url(url)
        self.county_name = county_name
        self.driver = reuse_driver
        self.owns_driver = reuse_driver is None
        self.session = self._create_session()
        
        # Security: Initialize rate limiter for this domain
        parsed_url = urlparse(self.url)
        self.rate_limiter = RateLimiter(parsed_url.netloc)
        
    def _create_session(self):
        """Create HTTP session with connection pooling and retry strategy"""
        session = requests.Session()
        
        # Security: Explicitly enforce SSL certificate verification
        session.verify = True
        
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=20
        )
        
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive'
        })
        
        return session
    
    def setup_driver(self):
        """Initialize Selenium WebDriver with PROVEN stealth configuration"""
        options = webdriver.ChromeOptions()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        # Use exact proven user agent from fixed scraper
        options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36')
        options.add_argument('--disable-extensions')
        options.add_argument('--disable-gpu')
        options.add_argument('--disable-plugins')  # Added from fixed scraper
        options.add_argument('--disable-images')   # Added from fixed scraper - faster
        options.add_argument('--disable-software-rasterizer')
        options.add_argument('--disable-background-networking')
        options.add_argument('--disable-default-apps')
        options.add_argument('--disable-sync')
        options.add_argument('--disable-translate')
        options.add_argument('--no-first-run')
        options.add_argument('--blink-settings=imagesEnabled=false')
        options.add_argument('--window-size=1920,1080')
        options.page_load_strategy = 'eager'

        self.driver = webdriver.Chrome(options=options)
        
        # PROVEN stealth script from fixed_multi_platform_scraper.py
        stealth_script = """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        window.chrome = {runtime: {}};
        """
        self.driver.execute_script(stealth_script)
        
        self.driver.implicitly_wait(0)
        self.driver.set_page_load_timeout(30)
        
    def close_driver(self):
        """Close the WebDriver only if we own it"""
        if self.driver and self.owns_driver:
            self.driver.quit()
            self.driver = None

    def _fetch_with_retry(self, url, headers=None, timeout=20, verify=True, max_attempts=3, backoff=5):
        """Fetch a URL via requests.get with retry on 5xx / connection / timeout errors.

        Retries up to *max_attempts* times, sleeping *backoff* seconds between attempts.
        Raises the last exception if all attempts fail.
        """
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            try:
                self.rate_limiter.wait()
                resp = self.session.get(url, headers=headers, timeout=timeout, verify=verify)
                resp.raise_for_status()
                return resp
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status >= 500:
                    last_exc = e
                    if attempt < max_attempts:
                        print(f"[{self.county_name}] HTTP {status} on attempt {attempt}/{max_attempts}, retrying in {backoff}s...")
                        time.sleep(backoff)
                    continue
                raise  # 4xx errors — don't retry
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                last_exc = e
                if attempt < max_attempts:
                    print(f"[{self.county_name}] {type(e).__name__} on attempt {attempt}/{max_attempts}, retrying in {backoff}s...")
                    time.sleep(backoff)
                continue
        raise last_exc

    def scrape(self):
        """Main scraping method - to be implemented by subclasses"""
        raise NotImplementedError


class LiveVoterTurnoutScraper(BaseScraper):
    """Scraper for LiveVoterTurnout.com sites (San Mateo, San Joaquin)"""

    def scrape(self):
        """Scrape LiveVoterTurnout.com site"""
        data = None
        try:
            # Security: Rate limit requests
            self.rate_limiter.wait()

            if not self.driver:
                self.setup_driver()

            print(f"[{self.county_name}] Loading LiveVoterTurnout.com page...")
            self.driver.get(self.url)

            # Wait for page to load with explicit waits instead of sleep
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                WebDriverWait(self.driver, 10).until(
                    lambda d: len(d.find_element(By.TAG_NAME, "body").text) > 100
                )
            except TimeoutException:
                pass  # Continue with whatever loaded

            data = {
                'timestamp': datetime.now().isoformat(),
                'url': self.url,
                'county_name': self.county_name,
                'page_title': self.driver.title,
                'last_updated': None,
                'voter_turnout': {},
                'contests': []
            }
            
            # Extract last updated time
            try:
                # Try to find "Website Updated:" text
                updated_elem = self.driver.find_element(By.XPATH, "//*[contains(text(), 'Website Updated:')]")
                data['last_updated'] = updated_elem.text.replace('Website Updated:', '').strip()
            except NoSuchElementException:
                try:
                    # Alternative: look for any date/time pattern
                    page_text = self.driver.find_element(By.TAG_NAME, 'body').text
                    match = re.search(r'(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)', page_text)
                    if match:
                        data['last_updated'] = match.group(1)
                except:
                    pass
            
            # Extract voter turnout
            print(f"[{self.county_name}] Extracting voter turnout data...")
            try:
                # Get the full page text for more flexible parsing
                page_text = self.driver.find_element(By.TAG_NAME, 'body').text
                
                # Extract ballots counted
                ballots_match = re.search(r'Ballots Counted[:\s]*(\d+(?:,\d+)*)', page_text)
                if ballots_match:
                    data['voter_turnout']['ballots_cast'] = int(ballots_match.group(1).replace(',', ''))
                
                # Extract registered voters
                registered_match = re.search(r'Registered Voters[:\s]*(\d+(?:,\d+)*)', page_text)
                if registered_match:
                    data['voter_turnout']['registered_voters'] = int(registered_match.group(1).replace(',', ''))
                
                # Extract turnout percentage (look for the first large percentage in voter turnout context)
                # Look for percentage near "VOTER TURNOUT" or at the start of the page
                turnout_section_match = re.search(r'VOTER TURNOUT[^0-9]*(\d+\.?\d*)%', page_text, re.IGNORECASE)
                if turnout_section_match:
                    data['voter_turnout']['turnout_percentage'] = float(turnout_section_match.group(1))
                else:
                    # Fallback: look for any percentage in the first 500 chars (where turnout usually is)
                    early_percent_match = re.search(r'(\d+\.?\d*)%', page_text[:500])
                    if early_percent_match:
                        data['voter_turnout']['turnout_percentage'] = float(early_percent_match.group(1))
                
                # Calculate percentage if missing
                if 'ballots_cast' in data['voter_turnout'] and 'registered_voters' in data['voter_turnout']:
                    if 'turnout_percentage' not in data['voter_turnout'] and data['voter_turnout']['registered_voters'] > 0:
                        data['voter_turnout']['turnout_percentage'] = round(
                            (data['voter_turnout']['ballots_cast'] / data['voter_turnout']['registered_voters']) * 100, 2
                        )
                
                print(f"[{self.county_name}] ✓ Voter turnout: {data['voter_turnout']}")
            except Exception as e:
                print(f"[{self.county_name}] ⚠ Error extracting voter turnout: {e}")
            
            # Extract contests
            print(f"[{self.county_name}] Extracting contests...")

            try:
                # Parse the loaded page with BeautifulSoup to extract proper contest names.
                # LiveVoterTurnout uses a dropdown-per-contest structure:
                #   <a class="heading align-center">
                #     <span class="sr-only">Contest: Governor, VOTE FOR 1</span>
                #   </a>
                #   <div class="content"> <table> ... </table> </div>
                # The visible h3 text only says the section name ("State Contests") —
                # the actual contest name is in the sr-only span.
                page_soup = BeautifulSoup(self.driver.page_source, 'html.parser')

                for heading_a in page_soup.find_all("a", class_="heading"):
                    sr = heading_a.find("span", class_="sr-only")
                    if not sr:
                        continue

                    # Strip "Contest: " prefix and ", VOTE FOR X" suffix from the label.
                    raw_title = sr.get_text(strip=True)
                    title = re.sub(r'^Contest:\s*', '', raw_title)
                    title = re.sub(r',?\s*VOTE FOR\s+\d+\s*$', '', title, flags=re.I).strip()
                    if not title:
                        continue

                    # Candidates live in the sibling <div class="content"> table.
                    content_div = heading_a.find_next_sibling("div")
                    table = content_div.find("table") if content_div else None

                    choices = []
                    if table:
                        for row in table.find_all("tr"):
                            cells = row.find_all(["td", "th"])
                            if len(cells) < 5:
                                continue
                            # Name cell (index 1): two spans — take the visible one (aria-hidden).
                            name_cell = cells[1]
                            visible = name_cell.find("span", attrs={"aria-hidden": "true"})
                            name = visible.get_text(strip=True) if visible else name_cell.get_text(strip=True)
                            if not name or name.lower() in ("candidate name", "candidate"):
                                continue
                            try:
                                votes = int(cells[3].get_text(strip=True).replace(",", ""))
                                pct   = float(cells[4].get_text(strip=True).rstrip("%"))
                            except (ValueError, IndexError):
                                continue
                            choices.append({"name": name, "votes": votes, "pct": pct})

                    data['contests'].append({
                        "title":               title,
                        "precincts_reporting": "",
                        "choices":             choices,
                    })

                print(f"[{self.county_name}] ✓ Extracted {len(data['contests'])} contests")

            except Exception as e:
                print(f"[{self.county_name}] ⚠ Error extracting contests: {e}")
            
            return data

        except Exception as e:
            print(f"[{self.county_name}] ✗ Error during scraping: {e}")
            print(f"[{self.county_name}] Error type: {type(e).__name__}")
            import traceback
            print(f"[{self.county_name}] Traceback: {traceback.format_exc()}")
            return data  # return whatever partial data was collected before the failure
        finally:
            self.close_driver()


class SFElectionsScraper(BaseScraper):
    """Scraper for SF Elections (sfelections.org).
    Discovers the latest certified summary.xml from the detail page and parses
    the SSRS XML report directly — no Selenium needed.

    The election ID (e.g. '20260602w') is derived from self.url so this
    scraper automatically uses whichever election the county_links.csv points to.
    """

    BASE_URL = 'https://www.sfelections.org'

    def _election_id_from_url(self):
        """Extract the election ID (e.g. '20260602w') from the passed-in URL."""
        m = re.search(r'/results/(\w+)/', self.url)
        return m.group(1) if m else '20260602w'

    def scrape(self):
        self.rate_limiter.wait()
        partial_data = None
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

            # Derive the election ID from the URL passed in via county_links.csv.
            # This makes the scraper work for any election without code changes —
            # just update the URL in the Google Sheet links tab.
            election_id = self._election_id_from_url()
            election_date = election_id.rstrip('w')  # '20260602w' → '20260602'
            detail_url = f'https://sfelections.org/results/{election_id}/detail.html'

            print(f"[{self.county_name}] Election ID: {election_id}")
            print(f"[{self.county_name}] Finding latest certified XML from {detail_url}...")
            r = self._fetch_with_retry(detail_url, headers=headers, timeout=20, verify=True)
            soup = BeautifulSoup(r.text, 'html.parser')

            xml_url = None
            # Links are like /results/20260602/data/20260603/summary.xml — pick the latest date folder
            candidates = []
            for a in soup.find_all('a', href=True):
                href = a['href']
                m = re.search(rf'/results/{election_date}/data/(\d+)/summary\.xml', href)
                if m:
                    candidates.append((m.group(1), href))
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                latest_href = candidates[0][1]
                xml_url = latest_href if latest_href.startswith('http') else self.BASE_URL + latest_href

            if not xml_url:
                print(f"[{self.county_name}] ✗ Could not find summary.xml URL on {detail_url}")
                print(f"[{self.county_name}]   Results may not be posted yet for election {election_id}.")
                return None

            print(f"[{self.county_name}] Fetching XML: {xml_url}")
            r = self._fetch_with_retry(xml_url, headers=headers, timeout=20, verify=True)
            partial_data = self._parse_ssrs_xml(r.text)
            return partial_data

        except Exception as e:
            print(f"[{self.county_name}] ✗ Error: {e}")
            import traceback
            print(traceback.format_exc())
            return partial_data

    def _parse_ssrs_xml(self, xml_text):
        import math, warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            soup = BeautifulSoup(xml_text, 'html.parser')

        data = {
            'timestamp': datetime.now().isoformat(),
            'url': self.url,
            'county_name': self.county_name,
            'page_title': '',
            'last_updated': None,
            'voter_turnout': {},
            'contests': [],
        }

        # Title (html.parser lowercases tag/attr names)
        r = soup.find('report', attrs={'name': 'Title'})
        if r:
            data['page_title'] = r.get('textbox8', '').replace('\n', ' ').strip()

        # --- Voter turnout (contest-level candidate aggregates) ---
        print(f"[{self.county_name}] Extracting voter turnout data...")
        try:
            # Precincts
            details = soup.find('details', attrs={'reported': True})
            if details:
                m = re.match(r'Precincts Reported: (\d+) of (\d+)', details['reported'])
                if m:
                    rep, tot = int(m.group(1)), int(m.group(2))
                    data['voter_turnout']['precincts_reported'] = f"{rep} / {tot} ({rep/tot*100:.2f}%)"

            # Turnout totals from the electorgroupid2 'Total' row
            eg = soup.find('electorgroupid2', attrs={'electorgroupid2': 'Total'})
            if eg:
                data['voter_turnout']['ballots_cast'] = int(eg.get('ballots2', 0))
                data['voter_turnout']['registered_voters'] = int(eg.get('textbox32', 0))
                turnout_pct_raw = float(eg.get('textbox6', 0))
                data['voter_turnout']['turnout_percentage'] = round(turnout_pct_raw * 100, 2)

            # Election Day / VBM totals from details1 counting-group rows
            for d1 in soup.find_all('details1'):
                grp = d1.get('countinggroup1', '')
                ballots = int(d1.get('ballots1', 0))
                if 'Election Day' in grp or 'Polling' in grp:
                    pass  # election_day not in uniform output
                elif 'Vote by Mail' in grp or 'Mail' in grp:
                    pass  # vote_by_mail not in uniform output

            print(f"[{self.county_name}] ✓ Voter turnout: {data['voter_turnout']}")
        except Exception as e:
            print(f"[{self.county_name}] ⚠ Error extracting voter turnout: {e}")

        # --- Contests ---
        print(f"[{self.county_name}] Extracting contests...")
        try:
            for contest_group in soup.find_all('contestidgroup'):
                title = contest_group.get('contestid', '').strip()
                if not title:
                    continue
                contest = {'title': title, 'choices': []}
                contest_total = 0
                for ch in contest_group.find_all('chgroup'):
                    t = ch.find('textbox13')
                    if t:
                        contest_total += int(t.get('vot8', 0))
                for ch in contest_group.find_all('chgroup'):
                    name_tag = ch.find('candidatenametextbox4')
                    if not name_tag:
                        continue
                    cname = name_tag.get('candidatenametextbox4', '').strip()
                    t = ch.find('textbox13')
                    if t:
                        votes = int(t.get('vot8', 0))
                        pct = round(votes / contest_total * 100, 2) if contest_total else 0
                        contest['choices'].append({
                            'name': cname,
                            'votes': votes,   # int
                            'pct': pct,       # float
                        })
                if contest['choices']:
                    data['contests'].append(contest)

            print(f"[{self.county_name}] ✓ Extracted {len(data['contests'])} contests")
        except Exception as e:
            print(f"[{self.county_name}] ⚠ Error extracting contests: {e}")

        return data


class SantaCruzScraper(BaseScraper):
    """Scraper for Santa Cruz County elections site"""
    
    def _parse_soup(self, soup):
        """Parse BeautifulSoup object directly using HTML structure (used by requests path)."""
        data = {
            'timestamp': datetime.now().isoformat(),
            'url': self.url,
            'county_name': self.county_name,
            'page_title': soup.title.string if soup.title else '',
            'last_updated': None,
            'voter_turnout': {},
            'contests': []
        }

        # Use flat text for turnout and timestamp (these regex patterns still work)
        page_text = soup.get_text()
        timestamp_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)', page_text)
        if timestamp_match:
            data['last_updated'] = timestamp_match.group(1)

        print(f"[{self.county_name}] Extracting voter turnout data...")
        try:
            total_match = re.search(r'Total Votes[:\s]*(\d+(?:,\d+)*)\s*\(([^)]+)\)', page_text)
            if total_match:
                data['voter_turnout']['ballots_cast'] = int(total_match.group(1).replace(',', ''))
                turnout_pct = total_match.group(2)
                if '%' in turnout_pct:
                    data['voter_turnout']['turnout_percentage'] = float(turnout_pct.replace('%', ''))

            registered_match = re.search(r'Total Registered Voters[:\s]*(\d+(?:,\d+)*)', page_text)
            if registered_match:
                data['voter_turnout']['registered_voters'] = int(registered_match.group(1).replace(',', ''))

            print(f"[{self.county_name}] ✓ Voter turnout: {data['voter_turnout']}")
        except Exception as e:
            print(f"[{self.county_name}] ⚠ Error extracting voter turnout: {e}")

        # Parse contests directly from HTML structure (NameRace_N / Race_N divs)
        print(f"[{self.county_name}] Extracting contests...")
        try:
            race_num = 1
            while True:
                title_div = soup.find(id=f'NameRace_{race_num}')
                results_div = soup.find(id=f'Race_{race_num}')
                if not title_div or not results_div:
                    break

                # Extract title from the <a> tag (strip icon elements first)
                title_link = title_div.find('a')
                for icon in (title_link or title_div).find_all('i'):
                    icon.decompose()
                title = (title_link or title_div).get_text(strip=True)

                contest_data = {
                    'title': title,
                    'choices': [],
                    'undervotes': None,
                    'overvotes': None,
                    'total_votes': None
                }

                # Candidate rows: col[0]=name, col[1]=party, col[2]=votes (percentage)
                for row in results_div.select('.table-candidate .content-row'):
                    cols = row.select('.elec-num')
                    if len(cols) >= 3:
                        candidate = cols[0].get_text(strip=True)
                        votes_text = cols[2].get_text(strip=True)
                        if candidate and candidate.lower() not in ['candidate', 'write in candidate', 'write in', '']:
                            votes_match = re.match(r'^(\d+(?:,\d+)*)\s*\(([^)]+%)\)$', votes_text)
                            if votes_match:
                                try:
                                    contest_data['choices'].append({
                                        'name': candidate,
                                        'votes': int(votes_match.group(1).replace(',', '')),
                                        'pct': float(votes_match.group(2).rstrip('%')),
                                    })
                                except (ValueError, TypeError) as e:
                                    print(f"[PARSE ERROR] [{self.county_name}] {contest_data.get('title', '?')!r}: {e}")

                # Total votes from footer
                footer = results_div.select_one('.mtable-footer')
                if footer:
                    footer_cols = footer.select('.elec-num')
                    if len(footer_cols) >= 2:
                        contest_data['total_votes'] = footer_cols[1].get_text(strip=True)

                # Undervotes / overvotes
                for row in results_div.select('.table-votes .content-row'):
                    cols = row.select('.elec-num')
                    if len(cols) >= 2:
                        label = cols[0].get_text(strip=True).lower()
                        value = cols[1].get_text(strip=True)
                        if 'under' in label:
                            contest_data['undervotes'] = value
                        elif 'over' in label:
                            contest_data['overvotes'] = value

                if contest_data['choices']:
                    data['contests'].append(contest_data)
                race_num += 1

            print(f"[{self.county_name}] ✓ Extracted {len(data['contests'])} contests")
        except Exception as e:
            print(f"[{self.county_name}] ⚠ Error extracting contests: {e}")

        return data

    def _parse_page_text(self, page_text, page_title=None):
        """Parse page text to extract election data (used by Selenium fallback path)"""
        data = {
            'timestamp': datetime.now().isoformat(),
            'url': self.url,
            'county_name': self.county_name,
            'page_title': page_title or '',
            'last_updated': None,
            'voter_turnout': {},
            'contests': []
        }

        # Extract last updated time from page
        timestamp_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)', page_text)
        if timestamp_match:
            data['last_updated'] = timestamp_match.group(1)

        # Extract voter turnout from "Registration and Turn out" section
        print(f"[{self.county_name}] Extracting voter turnout data...")
        try:
            section_text = page_text

            # Extract Total Votes
            total_match = re.search(r'Total Votes[:\s]*(\d+(?:,\d+)*)\s*\(([^)]+)\)', section_text)
            if total_match:
                data['voter_turnout']['ballots_cast'] = int(total_match.group(1).replace(',', ''))
                turnout_pct = total_match.group(2)
                if '%' in turnout_pct:
                    data['voter_turnout']['turnout_percentage'] = float(turnout_pct.replace('%', ''))

            # Extract Total Registered Voters
            registered_match = re.search(r'Total Registered Voters[:\s]*(\d+(?:,\d+)*)', section_text)
            if registered_match:
                data['voter_turnout']['registered_voters'] = int(registered_match.group(1).replace(',', ''))

            print(f"[{self.county_name}] ✓ Voter turnout: {data['voter_turnout']}")
        except Exception as e:
            print(f"[{self.county_name}] ⚠ Error extracting voter turnout: {e}")

        # Extract contests
        print(f"[{self.county_name}] Extracting contests...")
        try:
            # Find contest titles - pattern like "50 - Congressional Redistricting (Vote for 1)"
            # or "B - Workforce Housing Act (Vote for 1)"
            contest_pattern = r'([A-Z0-9]+)\s*-\s*([^(]+?)\s*(?:-\s*[^(]+?)?\s*\(Vote for \d+\)'
            contest_matches = re.finditer(contest_pattern, page_text)

            contests_found = []
            for match in contest_matches:
                contest_number = match.group(1)
                full_title = match.group(0)

                # Skip if this is a registration section
                if 'registration' in full_title.lower():
                    continue

                contests_found.append({
                    'title': full_title,
                    'match_start': match.start(),
                    'match_end': match.end()
                })

            # For each contest, extract the choices that follow
            for i, contest_info in enumerate(contests_found):
                contest_data = {
                    'title': contest_info['title'],
                    'choices': [],
                    'undervotes': None,
                    'overvotes': None,
                    'total_votes': None
                }

                # Get text between this contest and the next
                start_pos = contest_info['match_end']
                if i < len(contests_found) - 1:
                    end_pos = contests_found[i+1]['match_start']
                    contest_section = page_text[start_pos:end_pos]
                else:
                    contest_section = page_text[start_pos:start_pos+800]

                # Find the "Total Votes:" marker to know where this contest ends
                total_votes_pos = contest_section.find("Total Votes:")
                if total_votes_pos > 0:
                    contest_section = contest_section[:total_votes_pos+100]

                # Extract choices - candidate name and votes are on SEPARATE lines
                lines = contest_section.split('\n')

                seen_candidates = set()
                j = 0
                while j < len(lines):
                    line = lines[j].strip()

                    # Check if this line is a votes line: "57514 (77.77%)"
                    votes_match = re.match(r'^(\d+(?:,\d+)*)\s*\(([^)]+%)\)$', line)
                    if votes_match and j > 0:
                        candidate = lines[j-1].strip()

                        if candidate.lower() not in ['candidate', 'party', 'total', 'write in candidate', 'write in', ''] and candidate:
                            if candidate not in seen_candidates:
                                seen_candidates.add(candidate)
                                try:
                                    contest_data['choices'].append({
                                        'name': candidate,
                                        'votes': int(votes_match.group(1).replace(',', '')),
                                        'pct': float(votes_match.group(2).rstrip('%')),
                                    })
                                except (ValueError, TypeError) as e:
                                    print(f"[PARSE ERROR] [{self.county_name}] {contest_data.get('title', '?')!r}: {e}")

                    j += 1

                # Extract total votes
                total_match = re.search(r'Total Votes:\s*(\d+(?:,\d+)*)', contest_section[:500])
                if total_match:
                    contest_data['total_votes'] = total_match.group(1)

                # Extract undervotes
                under_match = re.search(r'Undervotes\s+(\d+(?:,\d+)*)', contest_section[:500])
                if under_match:
                    contest_data['undervotes'] = under_match.group(1)

                # Extract overvotes
                over_match = re.search(r'Overvotes\s+(\d+(?:,\d+)*)', contest_section[:500])
                if over_match:
                    contest_data['overvotes'] = over_match.group(1)

                if contest_data['choices']:
                    data['contests'].append(contest_data)

            print(f"[{self.county_name}] ✓ Extracted {len(data['contests'])} contests")

        except Exception as e:
            print(f"[{self.county_name}] ⚠ Error extracting contests: {e}")

        return data

    def scrape(self):
        """Scrape Santa Cruz County site - tries requests first, falls back to Selenium"""
        # Security: Rate limit requests
        self.rate_limiter.wait()

        # Try requests + BeautifulSoup first (avoids launching Chrome)
        try:
            print(f"[{self.county_name}] Trying requests-based fetch (no Selenium)...")
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            }
            response = self._fetch_with_retry(self.url, headers=headers, timeout=15, verify=True)

            if "Total Registered Voters" in response.text:
                print(f"[{self.county_name}] ✓ Requests fetch successful, parsing with BeautifulSoup...")
                soup = BeautifulSoup(response.text, 'html.parser')
                return self._parse_soup(soup)
            else:
                print(f"[{self.county_name}] Requests fetch did not contain expected data, falling back to Selenium...")
        except Exception as e:
            print(f"[{self.county_name}] Requests fetch failed ({e}), falling back to Selenium...")

        # Fall back to Selenium
        partial_data = None
        try:
            if not self.driver:
                self.setup_driver()

            print(f"[{self.county_name}] Loading Santa Cruz County page with Selenium...")
            self.driver.get(self.url)

            # Wait for page to load with explicit waits instead of sleep
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                WebDriverWait(self.driver, 10).until(
                    lambda d: len(d.find_element(By.TAG_NAME, "body").text) > 100
                )
            except TimeoutException:
                pass

            page_text = self.driver.find_element(By.TAG_NAME, 'body').text
            page_title = self.driver.title
            partial_data = self._parse_page_text(page_text, page_title)
            return partial_data

        except Exception as e:
            print(f"[{self.county_name}] ✗ Error during scraping: {e}")
            print(f"[{self.county_name}] Error type: {type(e).__name__}")
            import traceback
            print(f"[{self.county_name}] Traceback: {traceback.format_exc()}")
            return partial_data
        finally:
            self.close_driver()


class MendocinoScraper(BaseScraper):
    """Scraper for Mendocino County elections.
    The county page embeds an iframe at co.mendocino.ca.us which serves
    a Microsoft SSRS HTML report — fully static, no WAF, fetched directly.
    """

    IFRAME_URL = 'http://www.co.mendocino.ca.us/acr/cgi-bin/currentFR.pl'

    def scrape(self):
        self.rate_limiter.wait()
        partial_data = None
        try:
            print(f"[{self.county_name}] Fetching Mendocino results (iframe source)...")
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            }
            response = self._fetch_with_retry(self.IFRAME_URL, headers=headers, timeout=20, verify=False)
            soup = BeautifulSoup(response.text, 'html.parser')
            partial_data = self._parse(soup)
            return partial_data
        except Exception as e:
            print(f"[{self.county_name}] ✗ Error: {e}")
            import traceback
            print(traceback.format_exc())
            return partial_data

    def _meta(self, soup, name):
        tag = soup.find('meta', attrs={'name': name})
        return tag['content'] if tag else None

    def _parse(self, soup):
        data = {
            'timestamp': datetime.now().isoformat(),
            'url': self.url,
            'county_name': self.county_name,
            'page_title': self._meta(soup, 'ElectionTitle') or '',
            'last_updated': self._meta(soup, 'ReportGeneratedDate'),
            'voter_turnout': {},
            'contests': [],
        }

        # --- Voter turnout from meta tags ---
        print(f"[{self.county_name}] Extracting voter turnout data...")
        try:
            registered = self._meta(soup, 'TotalRegisteredVoters')
            ballots = self._meta(soup, 'TotalTabulatedBallots')
            precincts_total = self._meta(soup, 'TotalPrecintsSplits')
            precincts_reporting = self._meta(soup, 'TotalNumberReportingByPrecincts')

            if registered:
                data['voter_turnout']['registered_voters'] = int(registered.replace(',', ''))
            if ballots:
                data['voter_turnout']['ballots_cast'] = int(ballots.replace(',', ''))
            if registered and ballots:
                reg = int(registered.replace(',', ''))
                cast = int(ballots.replace(',', ''))
                if reg > 0:
                    data['voter_turnout']['turnout_percentage'] = round(cast / reg * 100, 2)
            if precincts_reporting and precincts_total:
                data['voter_turnout']['precincts_reported'] = f"{precincts_reporting} of {precincts_total}"

            print(f"[{self.county_name}] ✓ Voter turnout: {data['voter_turnout']}")
        except Exception as e:
            print(f"[{self.county_name}] ⚠ Error extracting voter turnout: {e}")

        # --- Contests from HTML tables ---
        # The SSRS report renders nested tables. Each contest appears as:
        #   - A row with 1 cell containing the contest title (all-caps)
        #   - Header row (Choice, Party, Election Day, Absentee, Total)
        #   - Choice rows with 11 cells: [name, '', '', ed_votes, ed_pct, '', mail_votes, mail_pct, '', total_votes, total_pct]
        print(f"[{self.county_name}] Extracting contests...")
        try:
            SKIP_LABELS = {'Cast Votes:', 'Undervotes:', 'Overvotes:', 'Choice', 'Party',
                           'Total', 'Election Day Voting', 'Absentee Voting', ''}
            current_title = None
            current_choices = []

            def flush(title, choices, out):
                if title and choices:
                    out.append({'title': title, 'choices': choices})

            for table in soup.find_all('table'):
                for row in table.find_all('tr'):
                    cells = [td.get_text(strip=True) for td in row.find_all(['td', 'th'])]
                    non_empty = [c for c in cells if c]

                    # Contest title: single non-empty cell, all-caps, not a known header
                    if (len(non_empty) == 1
                            and non_empty[0].isupper()
                            and non_empty[0] not in {'FINAL OFFICIAL REPORT - WEB',
                                                     'MENDOCINO COUNTY, CALIFORNIA',
                                                     'STATEWIDE SPECIAL ELECTION',
                                                     'OFFICIAL RESULTS'}):
                        flush(current_title, current_choices, data['contests'])
                        current_title = non_empty[0]
                        current_choices = []
                        continue

                    # Choice row: 11 cells, first cell is choice name
                    if (len(cells) == 11
                            and cells[0] not in SKIP_LABELS
                            and cells[0]
                            and cells[9]):  # total votes present
                        try:
                            current_choices.append({
                                'name': cells[0],
                                'votes': int(cells[9].replace(',', '')),
                                'pct': float(cells[10].rstrip('%')),
                            })
                        except (ValueError, TypeError) as e:
                            print(f"[PARSE ERROR] [{self.county_name}] {current_title!r}: {e} for cells {cells!r}")

            flush(current_title, current_choices, data['contests'])

            # Deduplicate by title (SSRS renders same tables multiple times)
            seen_titles = set()
            unique = []
            for c in data['contests']:
                if c['title'] not in seen_titles:
                    seen_titles.add(c['title'])
                    unique.append(c)
            data['contests'] = unique

            print(f"[{self.county_name}] ✓ Extracted {len(data['contests'])} contests")
        except Exception as e:
            print(f"[{self.county_name}] ⚠ Error extracting contests: {e}")

        return data


class AlamedaScraper(BaseScraper):
    """Scraper for Alameda County custom election results site (requests + BeautifulSoup)."""

    def scrape(self):
        self.rate_limiter.wait()
        partial_data = None
        try:
            print(f"[{self.county_name}] Fetching Alameda County results page...")
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            }
            response = self._fetch_with_retry(self.url, headers=headers, timeout=20, verify=True)
            soup = BeautifulSoup(response.text, 'html.parser')
            partial_data = self._parse(soup)
            return partial_data
        except Exception as e:
            print(f"[{self.county_name}] ✗ Error: {e}")
            import traceback
            print(traceback.format_exc())
            return partial_data

    def _parse(self, soup):
        data = {
            'timestamp': datetime.now().isoformat(),
            'url': self.url,
            'county_name': self.county_name,
            'page_title': soup.title.string.strip() if soup.title else '',
            'last_updated': None,
            'voter_turnout': {},
            'contests': [],
        }

        # --- Voter turnout ---
        print(f"[{self.county_name}] Extracting voter turnout data...")
        try:
            # Total registration from "Total Registration:965876" label
            reg_label = soup.find(id='totalRegLabel')
            if reg_label:
                m = re.search(r'Total Registration:\s*([\d,]+)', reg_label.get_text())
                if m:
                    data['voter_turnout']['registered_voters'] = int(m.group(1).replace(',', ''))

            # Precinct reporting
            reported_div = soup.select_one('#panelDtl0 .reported')
            if reported_div:
                data['voter_turnout']['precincts_reported'] = reported_div.get_text(strip=True)

            # Turnout table rows inside panelDtl0
            turnout_table = soup.select_one('#panelDtl0 table')
            if turnout_table:
                for row in turnout_table.select('tbody tr'):
                    cells = row.find_all(['th', 'td'])
                    if len(cells) < 3:
                        continue
                    label = cells[0].get_text(strip=True).lower()
                    votes_text = cells[1].get_text(strip=True).replace(',', '')
                    pct_text = cells[2].get_text(strip=True).replace('%', '').replace(' ', '')
                    try:
                        votes = int(votes_text)
                        pct = float(pct_text)
                    except ValueError:
                        continue
                    if 'total ballots' in label:
                        data['voter_turnout']['ballots_cast'] = votes
                        data['voter_turnout']['turnout_percentage'] = pct

            print(f"[{self.county_name}] ✓ Voter turnout: {data['voter_turnout']}")
        except Exception as e:
            print(f"[{self.county_name}] ⚠ Error extracting voter turnout: {e}")

        # --- Contests ---
        print(f"[{self.county_name}] Extracting contests...")
        try:
            # Propositions table: #panelDtlProps — columns: Title, YES Votes, YES Pct, NO Votes, NO Pct
            props_panel = soup.select_one('#panelDtlProps table tbody')
            if props_panel:
                for row in props_panel.select('tr'):
                    cells = row.find_all(['th', 'td'])
                    if len(cells) < 5:
                        continue
                    title = cells[0].get_text(strip=True)
                    yes_votes = cells[1].get_text(strip=True).replace(',', '').strip()
                    yes_pct = cells[2].get_text(strip=True).replace(' ', '')
                    no_votes = cells[3].get_text(strip=True).replace(',', '').strip()
                    no_pct = cells[4].get_text(strip=True).replace(' ', '')
                    if not title:
                        continue
                    try:
                        data['contests'].append({
                            'title': title,
                            'choices': [
                                {'name': 'Yes', 'votes': int(yes_votes), 'pct': float(yes_pct.rstrip('%'))},
                                {'name': 'No',  'votes': int(no_votes),  'pct': float(no_pct.rstrip('%'))},
                            ]
                        })
                    except (ValueError, TypeError) as e:
                        print(f"[PARSE ERROR] [{self.county_name}] {title!r}: {e}")

            # Individual measure/race panels: each has class "panelDtl" with an h3 sibling for the title
            for panel_btn in soup.select('button.list-group-item'):
                h3 = panel_btn.find('h3', class_='racePanelHeading')
                if not h3:
                    continue
                title = h3.get_text(strip=True)
                # Skip the Registration & Turnout and Propositions panels already handled
                if title in ('Registration & Turnout', 'Propositions'):
                    continue

                # Find the associated detail div via data-target
                target_id = panel_btn.get('data-target', '').lstrip('#')
                if not target_id:
                    continue
                detail_div = soup.find(id=target_id)
                if not detail_div:
                    continue

                contest = {'title': title, 'choices': []}

                # Precinct reporting & passing threshold
                reported = detail_div.select_one('.reported')
                if reported:
                    contest['precincts_reported'] = reported.get_text(strip=True)
                passing = detail_div.select_one('.passing')
                if passing:
                    contest['passing_requirement'] = passing.get_text(strip=True)

                # Candidate/choice rows
                for row in detail_div.select('table tbody tr'):
                    cells = row.find_all(['th', 'td'])
                    if len(cells) < 3:
                        continue
                    name = cells[0].get_text(strip=True)
                    votes_raw = cells[1].get_text(strip=True).replace(',', '').strip()
                    pct_raw = cells[2].get_text(strip=True).replace(' ', '')
                    if name and votes_raw:
                        try:
                            contest['choices'].append({
                                'name': name,
                                'votes': int(votes_raw),
                                'pct': float(pct_raw.rstrip('%')),
                            })
                        except (ValueError, TypeError) as e:
                            print(f"[PARSE ERROR] [{self.county_name}] {title!r}: {e}")

                if contest['choices']:
                    data['contests'].append(contest)

            print(f"[{self.county_name}] ✓ Extracted {len(data['contests'])} contests")
        except Exception as e:
            print(f"[{self.county_name}] ⚠ Error extracting contests: {e}")

        return data


class NapaScraper(BaseScraper):
    """Scraper for Napa County elections.
    The results page links to a PDF Summary Report; we fetch and parse it with pdfplumber.
    The PDF URL is discovered by scraping the election results page for the Nov 4 2025 election.
    """

    # Known stable PDF URL for the Nov 4, 2025 Statewide Special Election summary
    SUMMARY_PDF_URL = 'https://www.napacounty.gov/DocumentCenter/View/39913/'

    def scrape(self):
        self.rate_limiter.wait()
        partial_data = None
        try:
            import pdfplumber, io as _io
            print(f"[{self.county_name}] Fetching Napa County summary PDF...")
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
            r = self._fetch_with_retry(self.SUMMARY_PDF_URL, headers=headers, timeout=20, verify=True)

            with pdfplumber.open(_io.BytesIO(r.content)) as pdf:
                text = '\n'.join(page.extract_text() or '' for page in pdf.pages)

            partial_data = self._parse(text)
            return partial_data
        except Exception as e:
            print(f"[{self.county_name}] ✗ Error: {e}")
            import traceback
            print(traceback.format_exc())
            return partial_data

    def _parse(self, text):
        data = {
            'timestamp': datetime.now().isoformat(),
            'url': self.url,
            'county_name': self.county_name,
            'page_title': '',
            'last_updated': None,
            'voter_turnout': {},
            'contests': [],
        }

        # --- Title ---
        m = re.search(r'(Statewide Special Election.*?)\n', text)
        if m:
            data['page_title'] = m.group(1).strip()

        # --- Voter turnout ---
        print(f"[{self.county_name}] Extracting voter turnout data...")
        try:
            # "Total  52,409  86,390  60.67%"
            m = re.search(r'Total\s+([\d,]+)\s+([\d,]+)\s+([\d.]+)%', text)
            if m:
                data['voter_turnout']['ballots_cast'] = int(m.group(1).replace(',', ''))
                data['voter_turnout']['registered_voters'] = int(m.group(2).replace(',', ''))
                data['voter_turnout']['turnout_percentage'] = float(m.group(3))

            # Precincts
            m = re.search(r'Precincts Reported:\s*([\d]+ of [\d]+ \([^)]+\))', text)
            if m:
                data['voter_turnout']['precincts_reported'] = m.group(1)

            print(f"[{self.county_name}] ✓ Voter turnout: {data['voter_turnout']}")
        except Exception as e:
            print(f"[{self.county_name}] ⚠ Error extracting voter turnout: {e}")

        # --- Contests ---
        print(f"[{self.county_name}] Extracting contests...")
        try:
            # Contest block pattern: title, then candidate rows "NAME  [party]  votes  pct%"
            # Split by "Total Votes" to isolate each contest
            contest_blocks = re.split(r'Total Votes\s+[\d,]+', text)

            # Find contest titles: lines like "State Proposition 50 (Vote for 1)"
            title_pattern = re.compile(
                r'((?:State\s+)?(?:Proposition|Measure|Assessment|Director|Board|District)\s+[^\n]+?\(Vote for\s+\d+\))',
                re.IGNORECASE
            )

            for block in contest_blocks:
                title_m = title_pattern.search(block)
                if not title_m:
                    continue
                title = title_m.group(1).strip()
                contest = {'title': title, 'choices': []}

                # Choice rows after "Candidate  Party  Total"
                choice_section = block[title_m.end():]
                # Pattern: choice name (YES/NO or candidate), votes, pct
                # Exclude header/summary rows
                skip = {'Candidate', 'Total', 'Times Cast', 'Precincts Reported', 'Voters Cast'}
                for cm in re.finditer(r'^([A-Z][A-Z0-9 /\-\.]+?)\s+([\d,]+)\s+([\d.]+)%', choice_section, re.MULTILINE):
                    name = cm.group(1).strip()
                    if name in skip or re.match(r'^\d', name):
                        continue
                    try:
                        contest['choices'].append({
                            'name': name,
                            'votes': int(cm.group(2).replace(',', '')),
                            'pct': float(cm.group(3)),
                        })
                    except (ValueError, TypeError) as e:
                        print(f"[PARSE ERROR] [{self.county_name}] {title!r}: {e}")

                if contest['choices']:
                    data['contests'].append(contest)

            print(f"[{self.county_name}] ✓ Extracted {len(data['contests'])} contests")
        except Exception as e:
            print(f"[{self.county_name}] ⚠ Error extracting contests: {e}")

        return data


class SolanoScraper(BaseScraper):
    """Scraper for Solano County elections.
    Results are published as a PDF or HTML summary file. The URL comes from
    county_links.csv (synced from the Google Sheet) so it can be updated
    without touching code when Solano publishes a new results page.
    """

    def scrape(self):
        self.rate_limiter.wait()
        partial_data = None
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
            fetch_url = self.url  # use the URL from county_links.csv, not a hardcoded value
            print(f"[{self.county_name}] Fetching Solano County results from {fetch_url[:80]}...")
            r = self._fetch_with_retry(fetch_url, headers=headers, timeout=20, verify=True)

            if fetch_url.lower().endswith('.pdf'):
                # PDF path — extract text with pdfplumber
                import pdfplumber, io as _io
                with pdfplumber.open(_io.BytesIO(r.content)) as pdf:
                    text = '\n'.join(page.extract_text() or '' for page in pdf.pages)
                partial_data = self._parse(text)
            else:
                # HTML path — strip tags and parse the plain text
                from bs4 import BeautifulSoup as _BS
                text = _BS(r.text, 'html.parser').get_text(separator='\n')
                partial_data = self._parse(text)

            return partial_data
        except Exception as e:
            print(f"[{self.county_name}] ✗ Error: {e}")
            import traceback
            print(traceback.format_exc())
            return partial_data

    def _parse(self, text):
        data = {
            'timestamp': datetime.now().isoformat(),
            'url': self.url,
            'county_name': self.county_name,
            'page_title': 'Statewide Special Election - November 4, 2025',
            'last_updated': None,
            'voter_turnout': {},
            'contests': [],
        }

        # --- Voter turnout ---
        # "145294 of 277461 = 52.37%"
        print(f"[{self.county_name}] Extracting voter turnout data...")
        try:
            m = re.search(r'([\d,]+) of ([\d,]+) = ([\d.]+)%', text)
            if m:
                data['voter_turnout']['ballots_cast'] = int(m.group(1).replace(',', ''))
                data['voter_turnout']['registered_voters'] = int(m.group(2).replace(',', ''))
                data['voter_turnout']['turnout_percentage'] = float(m.group(3))

            # Precincts: "159 of 159 = 100.00%"
            m2 = re.search(r'Precincts Reporting\s+(\d+) of (\d+) = ([\d.]+)%', text)
            if m2:
                data['voter_turnout']['precincts_reported'] = f"{m2.group(1)} / {m2.group(2)} ({m2.group(3)}%)"

            print(f"[{self.county_name}] ✓ Voter turnout: {data['voter_turnout']}")
        except Exception as e:
            print(f"[{self.county_name}] ⚠ Error extracting voter turnout: {e}")

        # --- Contests ---
        # Format: contest title line, then header, then:
        #   "YES  9,821  51.14%  80,903  65.42%  1,646  74.58%  92,370  63.67%"
        # Columns: Choice [Party] | ED votes pct | VBM votes pct | Prov votes pct | Total votes pct
        print(f"[{self.county_name}] Extracting contests...")
        try:
            # Find contest titles (all-caps lines like "STATE PROPOSITION 50")
            contest_title_re = re.compile(r'^(STATE\s+PROPOSITION\s+\d+[^\n]*|MEASURE\s+[A-Z][^\n]*)', re.MULTILINE | re.IGNORECASE)
            # Choice rows: name, then pairs of (votes pct%) repeated, last pair is total
            # YES  9,821  51.14%  80,903  65.42%  1,646  74.58%  92,370  63.67%
            choice_re = re.compile(
                r'^(YES|NO|[A-Z][A-Z ]+?)\s+([\d,]+)\s+[\d.]+%\s+[\d,]+\s+[\d.]+%\s+[\d,]+\s+[\d.]+%\s+([\d,]+)\s+([\d.]+)%',
                re.MULTILINE
            )

            titles = list(contest_title_re.finditer(text))
            for i, tm in enumerate(titles):
                end = titles[i+1].start() if i+1 < len(titles) else len(text)
                block = text[tm.start():end]
                contest = {'title': tm.group(1).strip(), 'choices': []}

                for cm in choice_re.finditer(block):
                    try:
                        contest['choices'].append({
                            'name': cm.group(1).strip(),
                            'votes': int(cm.group(3).replace(',', '')),
                            'pct': float(cm.group(4)),
                        })
                    except (ValueError, TypeError) as e:
                        print(f"[PARSE ERROR] [{self.county_name}] {contest['title']!r}: {e}")
                if contest['choices']:
                    data['contests'].append(contest)

            print(f"[{self.county_name}] ✓ Extracted {len(data['contests'])} contests")
        except Exception as e:
            print(f"[{self.county_name}] ⚠ Error extracting contests: {e}")

        return data


class MontereyCountyScraper(BaseScraper):
    """Scraper for Monterey County elections.
    The page is CloudFront-protected but renders election data via JS from an XML file
    served at a /home/showdocument?id=XXXXX URL embedded in the page's JS.
    Uses Selenium to load the page, extracts the XML URL, fetches XML via browser JS,
    then parses it.
    """

    def scrape(self):
        self.rate_limiter.wait()
        partial_data = None
        try:
            if not self.driver:
                self.setup_driver()

            print(f"[{self.county_name}] Loading Monterey County election results page...")
            self.driver.get(self.url)

            # Wait for the page's JS to populate the `election` object
            try:
                WebDriverWait(self.driver, 20).until(
                    lambda d: d.execute_script("return typeof election !== 'undefined' && election !== null && election.contests && election.contests.length > 0")
                )
            except TimeoutException:
                print(f"[{self.county_name}] ⚠ Timed out waiting for election data, proceeding anyway")

            # Extract the fully-built election object directly from JS context
            import json as _json
            raw = self.driver.execute_script("return JSON.stringify(election)")
            if not raw:
                print(f"[{self.county_name}] ✗ election object not found in page")
                return None

            election = _json.loads(raw)
            partial_data = self._parse_election(election)
            return partial_data

        except Exception as e:
            print(f"[{self.county_name}] ✗ Error: {e}")
            import traceback
            print(traceback.format_exc())
            return partial_data
        finally:
            self.close_driver()

    def _parse_election(self, election):
        data = {
            'timestamp': datetime.now().isoformat(),
            'url': self.url,
            'county_name': self.county_name,
            'page_title': election.get('fullTitle', '').replace('\n', ' ').strip(),
            'last_updated': election.get('publishedDate'),
            'voter_turnout': {},
            'contests': [],
        }

        # --- Voter turnout (contest-level aggregates match what the page displays) ---
        print(f"[{self.county_name}] Extracting voter turnout data...")
        try:
            # Use the first contest's data — it aggregates candidate votes for VBM/PP/total
            # which is what the page displays, matching the official Final Report numbers
            contests_js = election.get('contests', [])
            reporting = election.get('reporting', {})
            turnout_js = election.get('turnout', {})

            if contests_js:
                c = contests_js[0]
                data['voter_turnout']['ballots_cast'] = c.get('totalVotes')
                data['voter_turnout']['registered_voters'] = c.get('registeredToVote')
                pct = c.get('turnoutPercent', 0)
                data['voter_turnout']['turnout_percentage'] = round(pct, 2)

            # Precincts from reporting object
            precincts = reporting.get('precinctsReported', '')
            if precincts:
                # Convert "171 of 171 (1)" → "171 / 171 (100.00%)"
                m = re.match(r'(\d+) of (\d+)', precincts)
                if m:
                    rep, tot = int(m.group(1)), int(m.group(2))
                    pct_str = f"{rep/tot*100:.2f}%" if tot else "0%"
                    data['voter_turnout']['precincts_reported'] = f"{rep} / {tot} ({pct_str})"

            print(f"[{self.county_name}] ✓ Voter turnout: {data['voter_turnout']}")
        except Exception as e:
            print(f"[{self.county_name}] ⚠ Error extracting voter turnout: {e}")

        # --- Contests ---
        print(f"[{self.county_name}] Extracting contests...")
        try:
            for c in election.get('contests', []):
                contest = {'title': c.get('name', '').strip(), 'choices': []}
                total_votes = c.get('totalVotes', 0) or 1  # avoid div/0
                for cand in c.get('candidates', []):
                    votes = cand.get('totalVotes', 0)
                    contest['choices'].append({
                        'name': cand.get('name', '').strip(),
                        'votes': int(votes),
                        'pct': round(votes / total_votes * 100, 2),
                    })
                for wi in c.get('writeIns', []):
                    votes = wi.get('totalVotes', 0)
                    contest['choices'].append({
                        'name': wi.get('name', '').strip(),
                        'votes': int(votes),
                        'pct': round(votes / total_votes * 100, 2),
                    })
                if contest['choices']:
                    data['contests'].append(contest)

            print(f"[{self.county_name}] ✓ Extracted {len(data['contests'])} contests")
        except Exception as e:
            print(f"[{self.county_name}] ⚠ Error extracting contests: {e}")

        return data


# Factory function to create the appropriate scraper
def create_scraper(url, county_name, reuse_driver=None):
    """Create the appropriate scraper based on URL"""
    if 'livevoterturnout.com' in url:
        return LiveVoterTurnoutScraper(url, county_name, reuse_driver)
    elif 'sfelections.org' in url:
        return SFElectionsScraper(url, county_name, reuse_driver)
    elif 'santacruzcountyca.gov' in url:
        return SantaCruzScraper(url, county_name, reuse_driver)
    elif 'alamedacountyca.gov' in url:
        return AlamedaScraper(url, county_name, reuse_driver)
    elif 'mendocinocounty.gov' in url or 'co.mendocino.ca.us' in url:
        return MendocinoScraper(url, county_name, reuse_driver)
    elif 'countyofmonterey.gov' in url:
        return MontereyCountyScraper(url, county_name, reuse_driver)
    elif 'napacounty.gov' in url:
        return NapaScraper(url, county_name, reuse_driver)
    elif 'clarityelections.com' in url:
        # Import and use the existing ClarityScraper
        from clarity_scraper import ClarityScraper
        return ClarityScraper(url, reuse_driver, save_files=False)
    else:
        raise ValueError(f"Unknown site type for URL: {url}")


if __name__ == "__main__":
    # Quick test
    print("Multi-Platform Scraper - Quick Test")
    print("="*60)
    
    # Test with San Mateo
    test_url = "https://www.livevoterturnout.com/ENR/sanmateocaenr/18/en/gWJEq_Index_18.html"
    test_county = "San_Mateo"
    
    scraper = create_scraper(test_url, test_county)
    result = scraper.scrape()
    
    if result:
        print("\n✓ Test successful!")
        print(f"Voter turnout: {result.get('voter_turnout')}")
        print(f"Contests found: {len(result.get('contests', []))}")
    else:
        print("\n✗ Test failed")

