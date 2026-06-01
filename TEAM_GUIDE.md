# How the Election Night Pipeline Works — and Why We Built It This Way

*Written for Bay City News / Local News Matters team members who want to understand what this system does, how it was built, and the thinking behind every decision. No coding knowledge required.*

---

## The Problem We Were Solving

On election night, Bay City News needs to publish live results from 13 different California county registrar websites — all at the same time, updating every 15 minutes, for hours.

Before this pipeline existed, someone would have had to:
- Manually visit 13 different county websites
- Copy the numbers by hand
- Paste them into a WordPress page
- Repeat every 15 minutes all night

That's not just exhausting — it's error-prone. One mistyped number in a race result is a serious journalism mistake.

The goal of this system is to do that mechanical work automatically and accurately, while keeping humans in control of anything that actually goes live on our website.

---

## The Ground Rules We Set From the Start

Before writing a single line of code, we agreed on a few non-negotiable rules:

**1. The pipeline never interprets results.**
It shows exactly what the county posted — votes, percentages, in the order the county listed them. It never calls a winner, says something is "leading," or marks a measure as passing or failing. That's the editor's job.

**2. The website never updates automatically.**
The scraper runs automatically every 15 minutes and updates a private Google Sheet. But nothing ever goes to the live BCN/LNM website unless a human editor reviews the data and manually presses a button.

**3. All passwords and credentials stay secret.**
Every API key, password, and account credential lives in a single hidden file (`.env`) that is never uploaded to GitHub. Anyone who checks out this code cannot see our credentials.

**4. If one county fails, the others still work.**
If Marin's website is down at 9 PM on election night, we still publish the other 12 counties. The pipeline keeps going and just flags Marin as a failure.

---

## The 13 Counties and Why They're Split Into Two Groups

Not all county election websites work the same way. We identified two types:

**Clarity counties (4):** Contra Costa, Marin, Santa Clara, Sonoma
These counties use a platform called Clarity Elections. Their websites are built with JavaScript, meaning the page looks empty when you first load it and then fills in with data a second later. A regular web scraper can't see that data — it's like trying to read a book before the ink has dried. So we use a tool that runs a real Chrome browser in the background, waits for the page to load, and then reads the numbers.

**Non-Clarity counties (9):** San Mateo, San Joaquin, Santa Cruz, Alameda, Mendocino, Monterey, Napa, San Francisco, Solano
These counties use simpler pages or a platform called LiveVoterTurnout. Their data is more directly accessible, so we can read it without running a full browser. This makes them faster to scrape (about 1–2 minutes vs. 5–6 minutes for Clarity counties).

The reason we care about this distinction: Clarity counties require Chrome to be installed on whatever computer is running the scraper. That's relevant for both local testing and for the automated GitHub system.

---

## What We Built, Piece by Piece

### 1. The Scrapers (already existed — we did not touch these)

`src/scrape_3_working.py` — handles the non-Clarity counties
`src/test_clarity_only.py` — handles the Clarity counties

These were already built and tested before this project. A key constraint we set: **do not modify these files.** They work. Changing them could break something right before election night. So everything we built works around them.

### 2. `run_all.py` — The Conductor (we built this)

Think of this as the stage manager. It doesn't do any scraping itself — it just tells the right people to go on stage at the right time, in the right order.

The pipeline order is always:
1. **Scrape** — visit the county websites
2. **Combine** — merge all county files into one master file
3. **Google Sheet** — push the results to the team dashboard
4. **WordPress** — (only if an editor explicitly requests it)

We gave it several "modes" so we can test each step safely:

- `--mock` — use fake sample data instead of hitting real county websites. Good for testing the pipeline without waiting 6 minutes for scrapers to run.
- `--test-sheets` — connect to the Google Sheet and confirm credentials work, but write nothing.
- `--preview-wp` — show what the WordPress page would look like, but post nothing.
- `--dry-run` — use whatever data is already on disk, skip the scraping step.
- `--no-sheets` — skip the Google Sheet update.
- `--push-wp` — enable the WordPress publish (still requires a typed YES confirmation).
- `--counties "Marin,San_Mateo"` — publish only specific counties to WordPress.

The venv guard at the top of the file checks that you're running inside the project's virtual environment before doing anything. If someone runs it with the wrong Python, they get a clear error message explaining exactly how to fix it.

### 3. `src/normalize.py` — The Translator (we built this)

The two scraper groups produce data in different formats:
- Clarity output wraps everything inside a key called `selenium_data`
- Non-Clarity output is flat (no wrapper)

If we fed those two formats directly to the Google Sheet and WordPress publisher, we'd need to handle both formats everywhere — messy and error-prone.

The normalize step reads both formats and converts them to one consistent structure. After normalization, every county looks the same. All downstream code (Sheets, WordPress) only has to deal with one format.

It also flags anomalies automatically:
- **ZERO_BALLOTS** — if a county shows zero votes cast, it probably means results aren't posted yet
- **NO_CONTESTS** — if no races appear, something may have gone wrong
- **MISSING_TURNOUT** — if turnout data is completely absent
- **ZERO_VOTES** — if every choice in a contest shows 0 votes

These flags appear in the Google Sheet so editors can spot data quality issues before publishing.

### 4. `export/to_sheets.py` — The Google Sheet Dashboard (we built this)

This connects to the Google Sheet using a service account — essentially a robot Google account that has permission to edit the sheet. The credentials for that account live in a key file on the computer.

The sheet has four tabs:

**STATUS DASHBOARD** — the first thing editors look at. One row per county showing whether the scrape worked (OK / WARN / FAIL), the last time the county's own website updated, how many ballots have been counted, and any anomaly flags.

**[County Name] tabs** — one tab per county with the full breakdown of every race and every candidate or measure option with votes and percentages.

**SCRAPE LOG** — a running history of every pipeline run. Never overwritten — only new rows are added. If something goes wrong at 10 PM, you can scroll back and see what the data looked like at 9:45 PM.

**PUBLISH CHECKLIST** — static instructions for editors. This tab is written once and never touched by the pipeline again, so editors can add their own notes to it.

### 5. `export/to_wordpress.py` — The Publisher (we built this)

This is the most carefully protected piece of the system. It can push a formatted HTML results page to the BCN/LNM WordPress site using the WordPress REST API (a standard way for software to talk to WordPress without logging in through a browser).

We added several layers of protection to make sure nothing ever goes live accidentally:

- The WordPress step never runs unless `--push-wp` is explicitly typed
- When it does run, it prints exactly where it's about to post and asks the editor to type `YES` — any other response aborts immediately
- In GitHub Actions (the automated system), a human still has to trigger the workflow manually and type a confirmation message in the form before it runs
- Editors can specify which counties to publish — if one county has bad data, you can publish the other 12 and leave that one out

### 6. `tests/test_mock.py` and `tests/fixtures/` — The Safety Net (we built this)

Before trusting any of this on election night, we need to be able to test it without hitting real websites, real Google Sheets, or real WordPress.

The fixtures are fake county data files — one per county — that look exactly like what the real scrapers would produce. The test suite runs the entire pipeline (combine → build Sheets data → build WordPress HTML) using those fake files and checks that everything comes out correctly.

28 tests. All must pass before any code change goes to GitHub.

Importantly: these tests require no credentials, no internet connection, and no county websites. Anyone can run `python -m pytest tests/ -v` on any machine and see whether the code works.

### 7. `.github/workflows/scrape.yml` — The Automatic Scraper (we built this)

This file tells GitHub to run the pipeline automatically every 15 minutes during election night. GitHub has servers that run code on a schedule — we're using that to avoid needing to leave a laptop running all night.

Each step has a `continue-on-error` flag, meaning if one county's scraper fails, the pipeline keeps going and still updates the Sheet with whatever it got. At the end, it prints a clear summary of every step's outcome so the log is never silent.

After every successful run, the normalized master data file is committed back to the GitHub repository. This is important because the publish workflow reads that file — it means the publisher always uses the most recently scraped and verified data.

### 8. `.github/workflows/publish.yml` — The Manual Publish Button (we built this)

This workflow has no schedule. It only runs when someone goes to GitHub and manually triggers it.

It requires the editor to type `"I have reviewed the Google Sheet and data is ready to publish"` exactly. Not a checkbox — a typed sentence. That friction is intentional: it forces a moment of conscious decision before anything goes live.

It also accepts an optional county filter. If, at 9 PM on election night, San Francisco's data looks wrong but all other counties look good, an editor can type `Contra_Costa,Marin,Santa_Clara,Sonoma,San_Mateo,San_Joaquin,Santa_Cruz,Alameda,Mendocino,Monterey,Napa,Solano` in the county field and publish everything except San Francisco.

---

## The Test Sequence We Ran

Before trusting this on a real election, we ran through every step in order with increasingly real data:

**1. `python run_all.py --mock --no-sheets`**
Used fake data, skipped the Google Sheet entirely. Just confirmed the combine step works.

**2. `python run_all.py --test-sheets`**
Connected to the real Google Sheet using real credentials. Read the sheet title and tab list. Wrote nothing. Confirmed: credentials work, sheet is accessible.

**3. `python run_all.py --mock`** *(next step)*
Uses fake data but pushes it to the real Google Sheet. Lets us see what the dashboard will look like on election night without scraping any live websites.

**4. `python run_all.py --mock --preview-wp`** *(next step)*
Builds the WordPress HTML from fake data and prints it to the terminal. Lets us read through the formatted output before anything touches the live site.

**5. GitHub Actions test run** *(after adding secrets)*
Manually trigger the scrape workflow from GitHub to confirm the automated system works end to end.

**6. First real scrape** *(election night or a pre-night rehearsal)*
Run the real pipeline against live county websites for the first time, review the Sheet, and confirm the numbers match what the county websites show.

---

## Things We Decided Not to Do (and Why)

**We don't auto-publish to WordPress.** The data is automatically collected and organized, but a human always makes the final call on what goes on the website. Election results are high-stakes journalism — automation handles the mechanical work, editors handle the judgment calls.

**We don't call winners or leading candidates.** The code shows numbers exactly as counties post them. We never add "LEADING" or "WINNER" labels, never compute thresholds for measures passing, never make any interpretation. That's editorial work, not pipeline work.

**We don't modify the original scrapers.** `scrape_3_working.py` and `test_clarity_only.py` were tested and working before this project. We built around them rather than risk breaking something that works.

**We don't store credentials in code.** Every password, API key, and account credential lives only in `.env` (on local machines) or GitHub Secrets (on the automated system). Neither is ever committed to GitHub. The `.env.example` file shows the shape of the credentials without any real values.

---

## Who Does What on Election Night

**The automated system (GitHub Actions):**
- Scrapes all 13 county websites every 15 minutes starting whenever the first results appear
- Combines all county data into the master file
- Updates the Google Sheet dashboard
- Commits the updated data to the repository

**The editors:**
- Monitor the STATUS DASHBOARD tab in the Google Sheet
- Spot-check numbers against county registrar websites
- Decide when the data is ready to publish
- Go to GitHub Actions → "Publish to WordPress" → type the confirmation → click the button

**Nobody:**
- Manually copies vote numbers
- Stays up all night refreshing 13 different county websites
- Pushes anything to the website without reviewing it first

---

## If Something Goes Wrong

**A county shows FAIL in the Google Sheet:**
The pipeline couldn't reach that county's website. Check the SCRAPE LOG tab to see the error message. The county's website may be temporarily down or slow. The pipeline will try again on the next 15-minute cycle.

**The Google Sheet isn't updating:**
Check the GitHub Actions tab in the repository. Look at the most recent "Scrape Election Results" run. Each step has a color indicator — red means it failed. Click on the failed step to read the error.

**WordPress publish fails:**
The publish workflow prints the full error message in the GitHub Actions log. Common causes: the WordPress Application Password expired, the page ID is wrong, or the site is temporarily unreachable.

**You need to publish without one county:**
In the "Publish to WordPress" workflow, enter the other 12 counties in the county field, separated by commas. That county will be excluded from the page.

---

*Last updated: June 2026. Built by the BCN/LNM data team for the June 2, 2026 California primary.*
