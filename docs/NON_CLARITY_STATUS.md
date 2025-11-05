# Non-Clarity Sites Scraping Status

## Summary
**3 out of 4 non-Clarity election sites are fully operational** ✅

## Working Sites (3/4)

### 1. San Mateo County ✅
- **Platform**: LiveVoterTurnout.com
- **URL**: https://www.livevoterturnout.com/ENR/sanmateocaenr/18/en/gWJEq_Index_18.html
- **Status**: Fully operational
- **Data Extracted**:
  - Voter Turnout: 174,939 / 446,692 (39.2%)
  - Contests: 1 (Proposition 50 with 3 choices)
  - Last Updated: Timestamp available

### 2. San Joaquin County ✅
- **Platform**: LiveVoterTurnout.com
- **URL**: https://www.livevoterturnout.com/ENR/sanjoaquincaenr/19/en/Index_19.html
- **Status**: Fully operational
- **Data Extracted**:
  - Voter Turnout: 107,920 / 403,835 (26.7%)
  - Contests: 1 (Proposition 50 with 3 choices)
  - Last Updated: Timestamp available

### 3. Santa Cruz County ✅
- **Platform**: Santa Cruz County (custom)
- **URL**: https://www2.santacruzcountyca.gov/ElectionSites/ElectionResults/Results
- **Status**: Fully operational (FIXED!)
- **Data Extracted**:
  - Voter Turnout: 
    - In Person: 6,090
    - Vote by Mail: 71,256
    - Total: 77,346 / 173,331 (44.62%)
  - Contests: 3
    1. 50 - Congressional Redistricting (2 choices: Yes/No)
    2. B - Workforce Housing And Climate Protection Act (2 choices: Yes/No)
    3. C - Workforce Housing Affordability Act (2 choices: Yes/No)
  - Last Updated: 11/4/2025 11:00:00 PM
- **Technical Details**:
  - Data format: Plain text (no HTML panels/tables)
  - Parsing method: Regex pattern matching on raw text
  - Challenge: Candidate names and votes on separate lines

## Non-Working Site (1/4)

### 4. San Francisco ❌
- **Platform**: SF Elections (sfelections.org)
- **URL**: https://sfelections.org/results/20251104w/index.html
- **Status**: Not operational with Selenium
- **Issue**: JavaScript content framework incompatible with Selenium/Chrome automation
- **Symptoms**:
  - Only 513 characters load (navigation menu only)
  - No election data appears in page body
  - Timeout after 60+ seconds of waiting
- **Verified**: 
  - ✅ Content DOES load in Browserbase cloud browser
  - ✅ Content DOES load in manual browser
  - ❌ Content does NOT load in Selenium (headless or non-headless)
- **Expected Data** (visible in Browserbase):
  - Number of Ballots Cast: 174,760
  - Voter registration total: 531,310
  - Current voter turnout: 32.89%
  - Precincts reporting: 0 of 108 (0.00%)
  - Contests: PROPOSITION 50 and others

## Technical Implementation Details

### Santa Cruz Scraper Fixes Applied:
1. **Voter Turnout Extraction**:
   - Removed dependency on HTML panel structure
   - Parse directly from full page text using regex
   - Patterns: `Total In Person:`, `Total Vote by Mail:`, `Total Votes:`, `Total Registered Voters:`

2. **Contest Extraction**:
   - Pattern matching for contest titles: `[A-Z0-9]+ - [Title] (Vote for \d+)`
   - Handles both numbered contests (50) and lettered measures (B, C)
   - Two-line parsing: candidate name on one line, votes/percentage on next line
   - Filters out menu items with no actual vote data

3. **Data Cleaning**:
   - Skip header words: "Candidate", "Party", "Total"
   - Deduplicate candidates to avoid double-counting
   - Extract undervotes, overvotes, and total votes per contest

### San Francisco Technical Limitations:
The SF Elections website uses a JavaScript framework (likely React or Angular) that:
1. Loads content dynamically after initial page render
2. Requires specific browser capabilities that Selenium doesn't fully emulate
3. May have bot detection that blocks automated browsers
4. Works perfectly in Browserbase cloud browsers (Chromium-based)

## Recommendations for San Francisco

### Option 1: Accept Limitation (Recommended)
- Document that SF Elections is incompatible with Selenium
- Focus on the 3 working counties (San Mateo, San Joaquin, Santa Cruz)
- Manually check SF Elections if needed

### Option 2: Use Browserbase API (Requires Setup)
- Integrate Browserbase MCP tools into the scraper
- Use cloud browser specifically for SF Elections
- Requires Browserbase account and API setup

### Option 3: Alternative Data Source
- Check if SF Elections provides a data API or RSS feed
- Look for JSON/XML data endpoints
- Use direct API calls instead of web scraping

### Option 4: Different Automation Tool
- Try Playwright instead of Selenium (better JS support)
- Use Puppeteer (Node.js-based)
- Requires rewriting scraper in different framework

## Running the Scraper

### Test Non-Clarity Sites Only:
```bash
python3 scrape_non_clarity.py
```

### Test Individual Counties:
- San Mateo: `SanMateo_non_clarity_[timestamp].json`
- San Joaquin: `SanJoaquin_non_clarity_[timestamp].json`
- Santa Cruz: `SantaCruz_non_clarity_[timestamp].json`
- San Francisco: Returns empty data with warning

### Output Location:
All results saved to: `election_data_multi/`

## Performance

- **San Mateo**: ~30 seconds
- **San Joaquin**: ~30 seconds
- **San Francisco**: ~87 seconds (fails)
- **Santa Cruz**: ~6-8 seconds
- **Total**: ~2-3 minutes for all 4 counties

## Success Rate

**Overall**: 75% (3 out of 4 counties)
- Voter Turnout Data: 3/4 (75%)
- Contest Data: 3/4 (75%)
- Last Updated Timestamp: 3/4 (75%)

## Next Steps

1. ✅ San Mateo - No action needed
2. ✅ San Joaquin - No action needed  
3. ✅ Santa Cruz - No action needed
4. ⚠️ San Francisco - Requires alternative solution (see recommendations)

---

**Last Updated**: November 4, 2025
**Scrapers**: `multi_platform_scraper.py`, `scrape_non_clarity.py`
**Data Directory**: `election_data_multi/`

