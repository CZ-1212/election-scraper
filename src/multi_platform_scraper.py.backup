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
from datetime import datetime
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from bs4 import BeautifulSoup
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class BaseScraper:
    """Base scraper class with common functionality"""
    
    def __init__(self, url, county_name, reuse_driver=None):
        self.url = url
        self.county_name = county_name
        self.driver = reuse_driver
        self.owns_driver = reuse_driver is None
        self.session = self._create_session()
        
    def _create_session(self):
        """Create HTTP session with connection pooling and retry strategy"""
        session = requests.Session()
        
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
        options.add_argument('--window-size=1920,1080')
        
        self.driver = webdriver.Chrome(options=options)
        
        # PROVEN stealth script from fixed_multi_platform_scraper.py
        stealth_script = """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        window.chrome = {runtime: {}};
        """
        self.driver.execute_script(stealth_script)
        
        self.driver.implicitly_wait(3)
        self.driver.set_page_load_timeout(30)
        
    def close_driver(self):
        """Close the WebDriver only if we own it"""
        if self.driver and self.owns_driver:
            self.driver.quit()
            self.driver = None
    
    def scrape(self):
        """Main scraping method - to be implemented by subclasses"""
        raise NotImplementedError


class LiveVoterTurnoutScraper(BaseScraper):
    """Scraper for LiveVoterTurnout.com sites (San Mateo, San Joaquin)"""
    
    def scrape(self):
        """Scrape LiveVoterTurnout.com site"""
        try:
            if not self.driver:
                self.setup_driver()
            
            print(f"[{self.county_name}] Loading LiveVoterTurnout.com page...")
            self.driver.get(self.url)
            
            # Wait for page to load
            time.sleep(3)  # Reduced from 5s
            
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
            
            # Patterns to skip (headers, instructions, etc.)
            skip_patterns = [
                r'^SEMI-OFFICIAL',
                r'^Results include:',
                r'^Results do not include:',
                r'^VOTER TURNOUT',
                r'^Website Updated:',
                r'^Statewide Elections Results',
                r'^showing \d+ of \d+ contests',
                r'^\s*$'  # Empty
            ]
            
            try:
                # Find all contest elements (they're typically in divs with specific classes)
                contest_elements = self.driver.find_elements(By.XPATH, 
                    "//div[contains(@class, 'contest') or contains(@id, 'contest')] | " +
                    "//section[contains(@class, 'contest')] | " +
                    "//div[.//h2 | .//h3][.//table | .//div[contains(., 'Yes') or contains(., 'No')]]"
                )
                
                for contest in contest_elements:
                    try:
                        contest_data = {
                            'title': None,
                            'precincts_reporting': None,
                            'choices': []
                        }
                        
                        # Get contest title
                        try:
                            title_elem = contest.find_element(By.XPATH, ".//h2 | .//h3 | .//*[contains(@class, 'title')]")
                            contest_data['title'] = title_elem.text.strip()
                        except:
                            # Try to get title from parent or nearby elements
                            contest_text = contest.text.split('\n')
                            if contest_text:
                                contest_data['title'] = contest_text[0].strip()
                        
                        if not contest_data['title']:
                            continue
                        
                        # Skip non-contest headers
                        should_skip = False
                        for pattern in skip_patterns:
                            if re.match(pattern, contest_data['title'], re.IGNORECASE):
                                should_skip = True
                                break
                        
                        if should_skip:
                            continue
                        
                        # Get precincts reporting if available
                        try:
                            precincts_elem = contest.find_element(By.XPATH, ".//*[contains(text(), 'Precincts') or contains(text(), 'precinct')]")
                            contest_data['precincts_reporting'] = precincts_elem.text.strip()
                        except:
                            pass
                        
                        # Get candidate/choice results
                        try:
                            # Try to find table rows or choice divs
                            choice_elements = contest.find_elements(By.XPATH, 
                                ".//tr[td] | .//div[contains(@class, 'choice')] | " +
                                ".//div[contains(., 'YES') or contains(., 'NO') or contains(., 'Yes') or contains(., 'No')]"
                            )
                            
                            seen_choices = set()
                            for choice in choice_elements:
                                choice_text = choice.text.strip()
                                # Filter out empty, header, or duplicate rows
                                if (choice_text and len(choice_text) > 0 and 
                                    not choice_text.startswith('Candidate') and
                                    choice_text not in seen_choices):
                                    # Parse choice text to extract name, votes, percentage
                                    if choice_text and any(char.isdigit() for char in choice_text):
                                        contest_data['choices'].append(choice_text)
                                        seen_choices.add(choice_text)
                        except:
                            pass
                        
                        if contest_data['title'] and (contest_data['choices'] or contest_data['precincts_reporting']):
                            data['contests'].append(contest_data)
                    except Exception as e:
                        continue
                
                print(f"[{self.county_name}] ✓ Extracted {len(data['contests'])} contests")
                
            except Exception as e:
                print(f"[{self.county_name}] ⚠ Error extracting contests: {e}")
            
            return data
            
        except Exception as e:
            print(f"[{self.county_name}] ✗ Error during scraping: {e}")
            print(f"[{self.county_name}] Error type: {type(e).__name__}")
            import traceback
            print(f"[{self.county_name}] Traceback: {traceback.format_exc()}")
            return None
        finally:
            self.close_driver()


class SFElectionsScraper(BaseScraper):
    """Scraper for SF Elections website (sfelections.org)
    
    NOTE: SF Elections has JavaScript compatibility issues with Selenium.
    Content loads in Browserbase cloud browser but not in local Selenium.
    Using the proven stealth driver to try to improve loading.
    """
    
    def scrape(self):
        """Scrape SF Elections site"""
        try:
            if not self.driver:
                self.setup_driver()
            
            print(f"[{self.county_name}] Loading SF Elections page with stealth...")
            self.driver.get(self.url)
            
            # Use proven wait time from fixed_multi_platform_scraper.py
            print(f"[{self.county_name}] Waiting 8 seconds for content (proven time)...")
            time.sleep(8)
            
            # Check content length
            try:
                body = self.driver.find_element(By.TAG_NAME, 'body')
                body_text = body.text
                print(f"[{self.county_name}] Page has {len(body_text)} characters")
                
                if len(body_text) < 1000:
                    print(f"[{self.county_name}] ⚠️ Insufficient content - JavaScript may not be loading")
                    print(f"[{self.county_name}] (Known issue: SF Elections incompatible with Selenium)")
                else:
                    print(f"[{self.county_name}] ✅ Content loaded successfully!")
            except:
                print(f"[{self.county_name}] ⚠️ Could not read page content")
            
            data = {
                'timestamp': datetime.now().isoformat(),
                'url': self.url,
                'county_name': self.county_name,
                'page_title': self.driver.title,
                'last_updated': None,
                'voter_turnout': {},
                'contests': [],
                'precincts_reporting': None
            }
            
            # Extract last updated time
            try:
                updated_elem = self.driver.find_element(By.XPATH, "//*[contains(text(), 'Last Update:')]")
                data['last_updated'] = updated_elem.text.replace('Last Update:', '').strip()
            except:
                pass
            
            # Extract voter turnout
            print(f"[{self.county_name}] Extracting voter turnout data...")
            try:
                page_text = self.driver.find_element(By.TAG_NAME, 'body').text
                
                # Extract number of ballots cast - direct pattern matching
                ballots_match = re.search(r'Number of Ballots Cast[:\s]*(\d+(?:,\d+)*)', page_text, re.IGNORECASE)
                if ballots_match:
                    data['voter_turnout']['ballots_cast'] = int(ballots_match.group(1).replace(',', ''))
                
                # Extract voter registration total - direct pattern matching
                registration_match = re.search(r'Voter registration total[:\s]*(\d+(?:,\d+)*)', page_text, re.IGNORECASE)
                if registration_match:
                    data['voter_turnout']['registered_voters'] = int(registration_match.group(1).replace(',', ''))
                
                # Extract current voter turnout - direct pattern matching
                turnout_match = re.search(r'Current voter turnout[:\s]*(\d+\.?\d*)%', page_text, re.IGNORECASE)
                if turnout_match:
                    data['voter_turnout']['turnout_percentage'] = float(turnout_match.group(1))
                
                # Extract last update with better pattern
                last_update_match = re.search(r'Last Update[:\s]*([^\n]+?)(?=Next Update|Voter registration|$)', page_text, re.IGNORECASE)
                if last_update_match:
                    data['last_updated'] = last_update_match.group(1).strip()
                
                # Extract precincts reporting
                precincts_match = re.search(r'Precincts that have reported[^:]*:\s*(\d+)\s+of\s+(\d+)\s+\(([^)]+)\)', page_text)
                if precincts_match:
                    data['precincts_reporting'] = f"{precincts_match.group(1)} of {precincts_match.group(2)} ({precincts_match.group(3)})"
                
                print(f"[{self.county_name}] ✓ Voter turnout: {data['voter_turnout']}")
            except Exception as e:
                print(f"[{self.county_name}] ⚠ Error extracting voter turnout: {e}")
            
            # Extract contests
            print(f"[{self.county_name}] Extracting contests...")
            try:
                # Look for proposition/measure sections with broader search
                # SF Elections uses simple text structure
                contest_elements = self.driver.find_elements(By.XPATH, 
                    "//*[contains(text(), 'PROPOSITION') and (self::h2 or self::h3 or self::h4 or self::div)] | " +
                    "//*[contains(text(), 'MEASURE') and (self::h2 or self::h3 or self::h4)]"
                )
                
                for contest in contest_elements:
                    try:
                        contest_data = {
                            'title': None,
                            'description': None,
                            'choices': []
                        }
                        
                        # Get title
                        contest_text = contest.text
                        lines = contest_text.split('\n')
                        
                        # First line is typically the title
                        if lines:
                            contest_data['title'] = lines[0].strip()
                        
                        # Look for description or measure text
                        try:
                            desc_elem = contest.find_element(By.XPATH, ".//*[contains(text(), 'measure') or contains(@class, 'description')]")
                            contest_data['description'] = desc_elem.text.strip()
                        except:
                            # Use subsequent lines as description if available
                            if len(lines) > 1:
                                contest_data['description'] = ' '.join(lines[1:3])
                        
                        # Extract vote counts from table
                        try:
                            rows = contest.find_elements(By.XPATH, ".//tr[td]")
                            for row in rows:
                                row_text = row.text.strip()
                                if row_text and any(char.isdigit() for char in row_text):
                                    contest_data['choices'].append(row_text)
                        except:
                            pass
                        
                        if contest_data['title'] and (contest_data['choices'] or contest_data['description']):
                            data['contests'].append(contest_data)
                    except:
                        continue
                
                print(f"[{self.county_name}] ✓ Extracted {len(data['contests'])} contests")
                
            except Exception as e:
                print(f"[{self.county_name}] ⚠ Error extracting contests: {e}")
            
            return data
            
        except Exception as e:
            print(f"[{self.county_name}] ✗ Error during scraping: {e}")
            print(f"[{self.county_name}] Error type: {type(e).__name__}")
            import traceback
            print(f"[{self.county_name}] Traceback: {traceback.format_exc()}")
            return None
        finally:
            self.close_driver()


class SantaCruzScraper(BaseScraper):
    """Scraper for Santa Cruz County elections site"""
    
    def scrape(self):
        """Scrape Santa Cruz County site"""
        try:
            if not self.driver:
                self.setup_driver()
            
            print(f"[{self.county_name}] Loading Santa Cruz County page...")
            self.driver.get(self.url)
            
            # Wait for page to load
            time.sleep(3)  # Reduced from 5s
            
            data = {
                'timestamp': datetime.now().isoformat(),
                'url': self.url,
                'county_name': self.county_name,
                'page_title': self.driver.title,
                'last_updated': None,
                'voter_turnout': {},
                'contests': []
            }
            
            # Extract last updated time from page
            try:
                page_text = self.driver.find_element(By.TAG_NAME, 'body').text
                # Look for timestamp pattern like "11/4/2025 8:05:00 PM"
                timestamp_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)', page_text)
                if timestamp_match:
                    data['last_updated'] = timestamp_match.group(1)
            except:
                pass
            
            # Extract voter turnout from "Registration and Turn out" section
            print(f"[{self.county_name}] Extracting voter turnout data...")
            try:
                # Get full page text - Santa Cruz doesn't use panels, data is in plain text
                page_text = self.driver.find_element(By.TAG_NAME, 'body').text
                section_text = page_text  # Use full page text directly
                
                # Extract Total In Person
                in_person_match = re.search(r'Total In Person[:\s]*(\d+(?:,\d+)*)\s*\(([^)]+)\)', section_text)
                if in_person_match:
                    data['voter_turnout']['in_person'] = int(in_person_match.group(1).replace(',', ''))
                
                # Extract Total Vote by Mail
                mail_match = re.search(r'Total Vote by Mail[:\s]*(\d+(?:,\d+)*)\s*\(([^)]+)\)', section_text)
                if mail_match:
                    data['voter_turnout']['vote_by_mail'] = int(mail_match.group(1).replace(',', ''))
                
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
                # Santa Cruz doesn't use panels/tables - data is in plain text
                # Parse from page text directly
                page_text = self.driver.find_element(By.TAG_NAME, 'body').text
                
                # Find contest titles - pattern like "50 - Congressional Redistricting (Vote for 1)"
                # or "B - Workforce Housing Act (Vote for 1)"
                contest_pattern = r'([A-Z0-9]+)\s*-\s*([^(]+?)\s*(?:-\s*[^(]+?)?\s*\(Vote for \d+\)'
                contest_matches = re.finditer(contest_pattern, page_text)
                
                contests_found = []
                for match in contest_matches:
                    contest_number = match.group(1)
                    full_title = match.group(0)  # Use full matched text as title
                    
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
                        # End at the start of the next contest
                        end_pos = contests_found[i+1]['match_start']
                        contest_section = page_text[start_pos:end_pos]
                    else:
                        # Last contest - limit to reasonable section size
                        contest_section = page_text[start_pos:start_pos+800]
                    
                    # Find the "Total Votes:" marker to know where this contest ends
                    total_votes_pos = contest_section.find("Total Votes:")
                    if total_votes_pos > 0:
                        # Only look up to (and slightly after) the Total Votes line
                        contest_section = contest_section[:total_votes_pos+100]
                    
                    # Extract choices - candidate name and votes are on SEPARATE lines
                    # Format:
                    #   Line 1: "Yes"
                    #   Line 2: "57514 (77.77%)"
                    lines = contest_section.split('\n')
                    
                    seen_candidates = set()
                    i = 0
                    while i < len(lines):
                        line = lines[i].strip()
                        
                        # Check if this line is a votes line: "57514 (77.77%)"
                        votes_match = re.match(r'^(\d+(?:,\d+)*)\s*\(([^)]+%)\)$', line)
                        if votes_match and i > 0:
                            # This is a votes line, get the candidate from previous line
                            candidate = lines[i-1].strip()
                            
                            # Skip if it's a header word
                            if candidate.lower() not in ['candidate', 'party', 'total', 'write in candidate', 'write in', ''] and candidate:
                                votes = votes_match.group(1)
                                percentage = votes_match.group(2)
                                
                                # Skip if already seen
                                if candidate not in seen_candidates:
                                    seen_candidates.add(candidate)
                                    choice_str = f"{candidate} | {votes} | {percentage}"
                                    contest_data['choices'].append(choice_str)
                        
                        i += 1
                    
                    # Extract total votes - pattern like "Total Votes: 74070"
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
                    
                    # Only add contests that have actual choices (not just menu items)
                    if contest_data['choices']:
                        data['contests'].append(contest_data)
                
                print(f"[{self.county_name}] ✓ Extracted {len(data['contests'])} contests")
                
            except Exception as e:
                print(f"[{self.county_name}] ⚠ Error extracting contests: {e}")
            
            return data
            
        except Exception as e:
            print(f"[{self.county_name}] ✗ Error during scraping: {e}")
            print(f"[{self.county_name}] Error type: {type(e).__name__}")
            import traceback
            print(f"[{self.county_name}] Traceback: {traceback.format_exc()}")
            return None
        finally:
            self.close_driver()


# Factory function to create the appropriate scraper
def create_scraper(url, county_name, reuse_driver=None):
    """Create the appropriate scraper based on URL"""
    if 'livevoterturnout.com' in url:
        return LiveVoterTurnoutScraper(url, county_name, reuse_driver)
    elif 'sfelections.org' in url:
        return SFElectionsScraper(url, county_name, reuse_driver)
    elif 'santacruzcountyca.gov' in url:
        return SantaCruzScraper(url, county_name, reuse_driver)
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

