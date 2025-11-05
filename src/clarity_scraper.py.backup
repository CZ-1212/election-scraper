#!/usr/bin/env python3
"""
Clarity Elections Web Scraper
Scrapes election results from Clarity Elections websites every 30 minutes starting at 8:01 PM
"""

import time
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
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

# Configuration
TARGET_URL = "https://results.enr.clarityelections.com/CA/Marin/124182/web.345435/#/summary"
DATA_DIR = Path("election_data")
START_TIME = "20:01"  # 8:01 PM
INTERVAL_MINUTES = 30

# Create data directory if it doesn't exist
DATA_DIR.mkdir(exist_ok=True)


class ClarityScraper:
    """Scraper for Clarity Elections websites"""
    
    def __init__(self, url, reuse_driver=None, save_files=True):
        self.url = url
        self.base_url = self._extract_base_url(url)
        self.driver = reuse_driver  # Allow driver reuse
        self.owns_driver = reuse_driver is None  # Track if we should close driver
        self.session = self._create_session()  # HTTP session with connection pooling
        self.save_files = save_files  # Control whether to save files
        
    def _extract_base_url(self, url):
        """Extract base URL from the full URL"""
        # Remove hash portion and trailing slashes
        return url.split('#')[0].rstrip('/')
    
    def _create_session(self):
        """Create HTTP session with connection pooling and retry strategy"""
        session = requests.Session()
        
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
        
        self.driver = webdriver.Chrome(options=options)
        
        # Execute script to remove webdriver detection
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        self.driver.implicitly_wait(5)  # Reduced from 10s to 5s
        self.driver.set_page_load_timeout(20)  # Reduced from 30s to 20s
        
    def _smart_wait_for_content(self):
        """Adaptive wait for page content to load"""
        max_wait = 8  # Reduced from 15s to 8s
        check_interval = 1  # Check every second
        waited = 0
        
        while waited < max_wait:
            # Check if content is loading by looking for key elements
            try:
                # Look for any meaningful content indicators
                content_indicators = [
                    ".contest", "[class*='contest']",  # Contest elements
                    "[class*='turnout']", "[class*='result']",  # Results elements
                    "h1", "h2", "h3",  # Headers that indicate content
                ]
                
                for selector in content_indicators:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements and any(el.text.strip() for el in elements):
                        print(f"✓ Content detected after {waited}s")
                        return
                
                time.sleep(check_interval)
                waited += check_interval
                
            except Exception:
                time.sleep(check_interval)
                waited += check_interval
        
        print(f"⚠ Content wait timeout after {max_wait}s")

    def _extract_last_updated(self):
        """Optimized extraction of last updated timestamp"""
        try:
            # Priority ordered selectors for efficiency
            selectors = [
                "//div[contains(., 'Last updated')]",
                "//*[contains(text(), '2025') and (contains(text(), 'AM') or contains(text(), 'PM'))]",
                "//time",
                "//*[@class*='updated' or @class*='timestamp']"
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
                
                # Extract percentage (look for turnout percentage specifically)
                percent_patterns = [
                    r'Turnout[:\s]*(\d+\.?\d*)%',
                    r'Voter Turnout[:\s]*(\d+\.?\d*)%',
                    r'(\d+\.?\d*)%',  # Fallback: any percentage in section
                ]
                for pattern in percent_patterns:
                    percent_match = re.search(pattern, section_text, re.IGNORECASE)
                    if percent_match:
                        turnout_data['turnout_percentage'] = float(percent_match.group(1))
                        break
            
            # Calculate percentage if missing
            if 'ballots_cast' in turnout_data and 'registered_voters' in turnout_data and 'turnout_percentage' not in turnout_data:
                if turnout_data['registered_voters'] > 0:
                    turnout_data['turnout_percentage'] = round((turnout_data['ballots_cast'] / turnout_data['registered_voters']) * 100, 2)
            
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
                    response = self.session.get(
                        endpoint,
                        headers={'Referer': self.url},
                        timeout=10
                    )
                    if response.status_code == 200:
                        return response.json()
                except:
                    continue
                    
        except Exception as e:
            print(f"Error checking JSON endpoints: {e}")
        
        return None
    
    def scrape_with_selenium(self):
        """Scrape data using Selenium to handle JavaScript rendering"""
        try:
            if not self.driver:
                self.setup_driver()
            print(f"Loading page: {self.url}")
            
            try:
                self.driver.get(self.url)
                
                # Check if request was blocked by CloudFront
                if "ERROR" in self.driver.title or "request could not be satisfied" in self.driver.title.lower():
                    print("⚠ Request blocked by CloudFront (403 error)")
                    print("  This is common on election night due to high traffic")
                    print("  Attempting enhanced retry with longer delays...")
                    
                    # Try with longer wait and refresh
                    time.sleep(30)  # Increased from 10s to 30s
                    self.driver.refresh()
                    time.sleep(15)  # Increased from 5s to 15s
                    
                    # Check again
                    if "ERROR" in self.driver.title or "request could not be satisfied" in self.driver.page_source.lower():
                        print("✗ Still blocked by CloudFront after retry")
                        print("  Returning partial data (voter turnout only, no contests)")
                        
                        # Try to extract at least voter turnout from cached/partial load
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
                        print("✓ Retry successful after CloudFront block")
                
                print("✓ Page loaded successfully")
                        
            except Exception as e:
                print(f"⚠ Page load issue: {e}")
                # Continue anyway - page might be partially loaded
            
            # Smart wait for JavaScript to execute - adaptive timing
            print("Waiting for JavaScript to execute (adaptive timing)...")
            self._smart_wait_for_content()
            print("✓ Wait complete")
            
            # Look for contest elements (don't wait - just check)
            print("Looking for contest elements...")
            contest_elements = self.driver.find_elements(By.CSS_SELECTOR, ".contest, [class*='contest']")
            if contest_elements:
                print(f"✓ Found {len(contest_elements)} contest elements")
            else:
                print("⚠ No contest elements found - page may not have results yet")
            
            # Extract page data
            data = {
                'timestamp': datetime.now().isoformat(),
                'url': self.url,
                'page_title': self.driver.title,
                'voter_turnout': {},
                'contests': []
            }
            
            # Optimized last updated time extraction
            data['last_updated'] = self._extract_last_updated()
            
            # Optimized voter turnout extraction
            print("Looking for voter turnout data...")
            data['voter_turnout'] = self._extract_voter_turnout_optimized()
            if data['voter_turnout']:
                print(f"✓ Extracted voter turnout data: {data['voter_turnout']}")
            else:
                print("⚠ No voter turnout data extracted")
            
            # Extract contest information
            # Patterns to skip (headers, instructions, etc.)
            skip_patterns = [
                r'^STATE$',
                r'^COUNTY$',
                r'^LOCAL$',
                r'^FEDERAL$',
                r'^showing',
                r'^\s*$'  # Empty
            ]
            
            try:
                contests = self.driver.find_elements(By.CSS_SELECTOR, ".contest, [class*='contest']")
                
                for contest in contests:
                    contest_data = {
                        'title': None,
                        'precincts_reporting': None,
                        'choices': []
                    }
                    
                    # Get contest title
                    try:
                        title_elem = contest.find_element(By.CSS_SELECTOR, "h2, h3, .contest-title, [class*='title']")
                        contest_data['title'] = title_elem.text
                    except NoSuchElementException:
                        pass
                    
                    # Skip non-contest headers
                    if contest_data['title']:
                        should_skip = False
                        for pattern in skip_patterns:
                            if re.match(pattern, contest_data['title'], re.IGNORECASE):
                                should_skip = True
                                break
                        
                        if should_skip:
                            continue
                    
                    # Get precincts reporting with percentage
                    try:
                        # Try multiple approaches to get precincts reporting with percentage
                        precincts_selectors = [
                            ".//*[contains(text(), 'Precincts Reporting')]",
                            ".//div[contains(., 'Precincts Reporting')]",
                            ".//span[contains(., 'Precincts Reporting')]"
                        ]
                        
                        precincts_text = None
                        for selector in precincts_selectors:
                            try:
                                precincts_elem = contest.find_element(By.XPATH, selector)
                                full_text = precincts_elem.text.strip()
                                
                                # Check if we got the full text with percentage
                                if 'Precincts Reporting' in full_text and '%' in full_text:
                                    precincts_text = full_text
                                    break
                                elif 'Precincts Reporting' in full_text:
                                    # Look for percentage in nearby elements or parent
                                    try:
                                        parent = precincts_elem.find_element(By.XPATH, "./..")
                                        parent_text = parent.text.strip()
                                        if '%' in parent_text:
                                            # Extract the line containing both
                                            lines = parent_text.split('\n')
                                            for line in lines:
                                                if 'Precincts Reporting' in line and '%' in line:
                                                    precincts_text = line.strip()
                                                    break
                                            if not precincts_text:
                                                # Try to find percentage pattern near "Precincts Reporting"
                                                match = re.search(r'Precincts Reporting\s*(\d+%)', parent_text)
                                                if match:
                                                    precincts_text = f"Precincts Reporting {match.group(1)}"
                                    except:
                                        pass
                                    
                                    if not precincts_text:
                                        precincts_text = full_text  # Fallback to original text
                                    break
                            except NoSuchElementException:
                                continue
                        
                        # Format the precincts reporting text nicely
                        if precincts_text and 'Precincts Reporting' in precincts_text:
                            # Add space before percentage if missing
                            formatted_text = re.sub(r'Precincts Reporting(\d+%)', r'Precincts Reporting \1', precincts_text)
                            contest_data['precincts_reporting'] = formatted_text
                        else:
                            contest_data['precincts_reporting'] = precincts_text
                            
                    except Exception as e:
                        contest_data['precincts_reporting'] = None
                    
                    # Get choices/candidates
                    try:
                        rows = contest.find_elements(By.CSS_SELECTOR, "tr, .choice, [class*='choice']")
                        seen_choices = set()
                        for row in rows:
                            try:
                                choice_text = row.text.strip()
                                # Filter out empty, header, or duplicate choices
                                if (choice_text and len(choice_text) > 0 and 
                                    choice_text not in seen_choices and
                                    not choice_text.startswith('Candidate') and
                                    not choice_text.startswith('Choice') and
                                    choice_text != 'Percentage Votes'):
                                    contest_data['choices'].append(choice_text)
                                    seen_choices.add(choice_text)
                            except:
                                pass
                    except NoSuchElementException:
                        pass
                    
                    if contest_data['title']:
                        data['contests'].append(contest_data)
                
                # Deduplicate contests
                if data['contests']:
                    original_count = len(data['contests'])
                    data['contests'] = self._deduplicate_contests(data['contests'])
                    print(f"✓ Extracted {original_count} contests, deduplicated to {len(data['contests'])} unique contests")
                        
            except Exception as e:
                print(f"Error extracting contests: {e}")
            
            # HTML snapshot removed to reduce file size
            
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
            response = self.session.get(
                report_url,
                headers={'Referer': self.url},
                timeout=30
            )
            
            if response.status_code == 200:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = DATA_DIR / f"report_{timestamp}.{report_type}"
                
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
            print("✓ Found JSON data!")
            results['json_data'] = json_data
        else:
            print("✗ No JSON endpoints found")
        
        # Try 2: Scrape with Selenium
        print("\n[2] Scraping with Selenium...")
        selenium_data = self.scrape_with_selenium()
        if selenium_data:
            print(f"✓ Scraped {len(selenium_data.get('contests', []))} contests")
            results['selenium_data'] = selenium_data
        else:
            print("✗ Selenium scraping failed")
        
        # Try 3: Check for downloadable reports
        print("\n[3] Checking for downloadable reports...")
        reports = self.check_reports_section()
        if reports:
            print(f"✓ Found {len(reports)} reports")
            results['reports'] = reports
            
            # Download the first XML or CSV report
            for report in reports:
                if report['type'] in ['xml', 'csv']:
                    self.download_report(report['url'], report['type'])
                    break
        else:
            print("✗ No reports found")
        
        # Save results to JSON file only if enabled
        if self.save_files:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = DATA_DIR / f"scrape_{timestamp}.json"
            
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

