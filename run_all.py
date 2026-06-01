#!/usr/bin/env python3
"""
Election Night Pipeline Orchestrator — Bay City News / Local News Matters

This is the main entry point for the election results pipeline. Run it from
the project root. It chains four steps in order:

  1. SCRAPE    — Hit the county election websites and save raw JSON to data/
  2. NORMALIZE — Merge all county JSON files into one master JSON
  3. SHEETS    — Push the master JSON to the Google Sheet live dashboard
  4. WORDPRESS — (Manual only) Push HTML results to the BCN/LNM WordPress site

Usage examples:
  python run_all.py                        # Full pipeline (scrape + normalize + sheets)
  python run_all.py --scraper clarity      # Only scrape Clarity counties
  python run_all.py --no-sheets            # Scrape and normalize, skip sheets
  python run_all.py --mock                 # Use fixture files instead of live scraping
  python run_all.py --dry-run              # Skip scraping, use existing normalized data
  python run_all.py --push-wp --dry-run    # Skip scraping, push existing data to WordPress
"""

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# VENV GUARD — stop immediately if we're not running inside ./venv
# This prevents "works on my machine" problems from missing dependencies.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
EXPECTED_VENV = PROJECT_ROOT / "venv"

# sys.prefix is the path to the Python environment currently in use.
# If it doesn't start with our expected venv path, we're running the wrong Python.
if not sys.prefix.startswith(str(EXPECTED_VENV)):
    print("ERROR: You must activate the project virtual environment before running this script.")
    print(f"\n  Run this first:")
    print(f"    source {EXPECTED_VENV}/bin/activate")
    print(f"\n  Then try again.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# LOGGING — write to both the terminal and logs/scraper.log
# ---------------------------------------------------------------------------
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "scraper.log"

# Set up a logger that writes the same messages to stdout and the log file.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),          # show in terminal
        logging.FileHandler(LOG_FILE, encoding="utf-8"),  # also save to file
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ARGUMENT PARSING
# ---------------------------------------------------------------------------
def parse_args():
    """Define and parse command-line flags."""
    parser = argparse.ArgumentParser(
        description="Bay City News election night pipeline: scrape → normalize → sheets → (optional) WordPress"
    )

    parser.add_argument(
        "--scraper",
        choices=["both", "clarity", "non-clarity"],
        default="both",
        help="Which scraper(s) to run. 'both' runs all counties. "
             "'clarity' runs only Contra Costa, Marin, Santa Clara, Sonoma. "
             "'non-clarity' runs only San Mateo, San Joaquin, Santa Cruz. "
             "Default: both",
    )

    parser.add_argument(
        "--no-sheets",
        action="store_true",
        help="Skip the Google Sheets update step. Useful when testing locally.",
    )

    parser.add_argument(
        "--push-wp",
        action="store_true",
        help="Push results to WordPress after normalizing. "
             "This will ask you for a typed confirmation before publishing. "
             "NEVER runs automatically — you must pass this flag explicitly.",
    )

    parser.add_argument(
        "--counties",
        default=None,
        help="Comma-separated list of county names to publish to WordPress "
             "(e.g. 'Marin,Santa_Clara'). Only used when --push-wp is set. "
             "If omitted, all counties with OK/WARN status are published.",
    )

    parser.add_argument(
        "--test-sheets",
        action="store_true",
        help="Test Google Sheets connection only — reads sheet metadata, writes nothing. "
             "Use this before running the full pipeline to confirm credentials work.",
    )

    parser.add_argument(
        "--preview-wp",
        action="store_true",
        help="Build and print the WordPress HTML without posting anything. "
             "Use this to check what the publish would look like before going live.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip the scraping step. Uses whatever data is already on disk. "
             "Pair with --push-wp to publish existing normalized data to WordPress "
             "without re-scraping the county websites.",
    )

    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use fixture JSON files from tests/fixtures/ instead of hitting live county websites. "
             "Useful for testing the pipeline without a network connection.",
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# STEP 1 — SCRAPE
# Call the existing scraper scripts as subprocesses. They save JSON files to
# data/ which normalize.py will pick up in the next step.
# ---------------------------------------------------------------------------
def run_scrape(args):
    """
    Visit each county election website and download the latest results.
    Saves one JSON file per county into the data/ folder.
    """
    log.info("=" * 60)
    log.info("STEP 1 of 4 — SCRAPING COUNTY WEBSITES")
    log.info("  Going to each county registrar's website and downloading their results.")
    log.info("=" * 60)

    src_dir = PROJECT_ROOT / "src"
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}:{existing_pythonpath}" if existing_pythonpath else str(src_dir)

    python_exe = sys.executable
    scraper_choice = args.scraper
    success = True

    # Non-Clarity counties use a simpler web scraper (no browser needed).
    if scraper_choice in ("both", "non-clarity"):
        log.info("  Visiting: San Mateo, San Joaquin, Santa Cruz...")
        log.info("  (These counties use a simpler page — takes about 1-2 minutes.)")
        result = subprocess.run(
            [python_exe, str(src_dir / "scrape_3_working.py")],
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        if result.returncode != 0:
            log.error("  ✗ San Mateo / San Joaquin / Santa Cruz scrape had an error.")
            log.error("    The pipeline will continue with whatever data was saved.")
            success = False
        else:
            log.info("  ✓ San Mateo, San Joaquin, Santa Cruz — downloaded successfully.")

    # Clarity counties run a full Chrome browser to load their JavaScript pages.
    if scraper_choice in ("both", "clarity"):
        log.info("  Visiting: Contra Costa, Marin, Santa Clara, Sonoma...")
        log.info("  (These counties require a browser — takes about 5-6 minutes.)")
        result = subprocess.run(
            [python_exe, str(src_dir / "test_clarity_only.py")],
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        if result.returncode != 0:
            log.error("  ✗ Clarity scrape had an error (Contra Costa / Marin / Santa Clara / Sonoma).")
            log.error("    The pipeline will continue with whatever data was saved.")
            success = False
        else:
            log.info("  ✓ Contra Costa, Marin, Santa Clara, Sonoma — downloaded successfully.")

    return success


# ---------------------------------------------------------------------------
# STEP 2 — NORMALIZE
# Import normalize.py and call it. It finds the most recent county JSON files
# in data/ (or tests/fixtures/ when --mock is active), merges them, and
# writes data/processed/election_results_master.json.
# ---------------------------------------------------------------------------
def run_normalize(args):
    """
    Take all the individual county files and combine them into one clean master file.
    This is the step that makes all the data consistent and flags any problems.
    """
    log.info("=" * 60)
    log.info("STEP 2 of 4 — COMBINING & CHECKING THE DATA")
    log.info("  Reading each county's raw data file and merging them into one master file.")
    log.info("  Any counties with zero votes or missing data will be flagged as warnings.")
    log.info("=" * 60)

    src_dir = PROJECT_ROOT / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    import importlib.util
    normalize_path = src_dir / "normalize.py"
    spec = importlib.util.spec_from_file_location("normalize", normalize_path)
    normalize_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(normalize_module)

    if args.mock:
        input_dir = PROJECT_ROOT / "tests" / "fixtures"
        log.info("  Using test fixture files from: %s", input_dir)
        log.info("  (These are fake sample files — no real county data is being used.)")
    else:
        input_dir = PROJECT_ROOT / "data"

    output_path = PROJECT_ROOT / "data" / "processed" / "election_results_master.json"

    try:
        normalize_module.normalize(input_dir=input_dir, output_path=output_path)
        log.info("  ✓ All county data combined. Master file saved to: data/processed/election_results_master.json")
        return True
    except Exception as e:
        log.error("  ✗ Failed to combine county data: %s", e)
        return False


# ---------------------------------------------------------------------------
# STEP 3 — GOOGLE SHEETS
# Push normalized data to the live dashboard Google Sheet.
# Skip if --no-sheets is set or if --dry-run is set without --push-wp.
# ---------------------------------------------------------------------------
def run_sheets():
    """
    Send the combined results to the Google Sheet so editors can review them
    before anything goes on the website.
    """
    log.info("=" * 60)
    log.info("STEP 3 of 4 — UPDATING THE GOOGLE SHEET DASHBOARD")
    log.info("  Sending all county results to the Google Sheet.")
    log.info("  Editors should review that sheet before publishing to the website.")
    log.info("=" * 60)

    # Add export/ to the Python path so to_sheets can be imported.
    export_dir = PROJECT_ROOT / "export"
    if str(export_dir) not in sys.path:
        sys.path.insert(0, str(export_dir))

    import importlib.util
    sheets_path = export_dir / "to_sheets.py"
    spec = importlib.util.spec_from_file_location("to_sheets", sheets_path)
    sheets_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sheets_module)

    master_json = PROJECT_ROOT / "data" / "processed" / "election_results_master.json"

    if not master_json.exists():
        log.error("Master JSON not found at %s — run normalize first.", master_json)
        return False

    try:
        sheets_module.update_sheets(master_json_path=master_json)
        log.info("  ✓ Google Sheet updated. Editors can now review it before publishing.")
        return True
    except Exception as e:
        log.error("  ✗ Google Sheet update failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# STEP 4 — WORDPRESS
# Only runs when --push-wp is explicitly passed. Always requires a human to
# type YES before publishing. This step never runs automatically.
# ---------------------------------------------------------------------------
def run_wordpress(args):
    """
    Publish the results to the live website.
    This only runs when an editor explicitly requests it — never automatically.
    The editor will be asked to type YES before anything is posted.
    """
    log.info("=" * 60)
    log.info("STEP 4 of 4 — PUBLISHING TO THE WEBSITE")
    log.info("  This will post the election results to the live BCN/LNM page.")
    log.info("  You will be asked to confirm before anything is published.")
    log.info("=" * 60)

    export_dir = PROJECT_ROOT / "export"
    if str(export_dir) not in sys.path:
        sys.path.insert(0, str(export_dir))

    import importlib.util
    wp_path = export_dir / "to_wordpress.py"
    spec = importlib.util.spec_from_file_location("to_wordpress", wp_path)
    wp_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wp_module)

    master_json = PROJECT_ROOT / "data" / "processed" / "election_results_master.json"

    if not master_json.exists():
        log.error("Master JSON not found at %s — nothing to publish.", master_json)
        return False

    # Parse --counties flag into a list if provided, otherwise pass None (= all counties).
    county_list = [c.strip() for c in args.counties.split(",")] if args.counties else None

    try:
        wp_module.publish_to_wordpress(master_json_path=master_json, include_counties=county_list)
        return True
    except Exception as e:
        log.error("WordPress publish failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# MAIN — chain the pipeline steps together
# ---------------------------------------------------------------------------
def main():
    args = parse_args()

    log.info("=" * 60)
    log.info("BAY CITY NEWS — ELECTION NIGHT PIPELINE")
    log.info("Starting at %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("-" * 60)
    log.info("What this pipeline does:")
    log.info("  1. Visits each county's election website and downloads the results.")
    log.info("  2. Combines all county data into one clean master file.")
    log.info("  3. Sends the results to the Google Sheet so editors can review them.")
    log.info("  4. (Only if requested) Publishes to the live BCN/LNM website.")
    log.info("-" * 60)

    # Print what mode we're running in so it's clear from the log.
    if args.mock:
        log.info("MODE: TESTING with fake sample data (no real county websites will be visited).")
    elif args.dry_run:
        log.info("MODE: DRY RUN — using data already on disk, no websites will be visited.")
    else:
        log.info("MODE: LIVE — will visit real county election websites.")

    if args.push_wp:
        log.info("WordPress publish: ENABLED — you will be asked to confirm before anything posts.")
    if args.preview_wp:
        log.info("WordPress preview: will show you the HTML without posting anything.")
    if args.test_sheets:
        log.info("Sheets test: will check the Google connection only — no data will be written.")
    log.info("=" * 60)

    # ---------------------------------------------------------------------------
    # SHORT-CIRCUIT: test and preview modes run a single task and stop.
    # They never touch live data or post anything anywhere.
    # ---------------------------------------------------------------------------

    # --test-sheets: just checks that we can reach the Google Sheet. Writes nothing.
    if args.test_sheets:
        log.info("Running Google Sheets connection test (no data will be written)...")
        export_dir = PROJECT_ROOT / "export"
        if str(export_dir) not in sys.path:
            sys.path.insert(0, str(export_dir))
        import importlib.util
        spec = importlib.util.spec_from_file_location("to_sheets", export_dir / "to_sheets.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ok = mod.test_connection()
        sys.exit(0 if ok else 1)

    # --preview-wp: builds the WordPress HTML and prints it. Posts nothing.
    if args.preview_wp:
        log.info("Building WordPress HTML preview (nothing will be posted)...")
        # We need a master JSON to build from — normalize from mock fixtures or existing data.
        ok = run_normalize(args)
        if not ok:
            log.error("Cannot build preview: normalize step failed.")
            sys.exit(1)
        export_dir = PROJECT_ROOT / "export"
        if str(export_dir) not in sys.path:
            sys.path.insert(0, str(export_dir))
        import importlib.util
        spec = importlib.util.spec_from_file_location("to_wordpress", export_dir / "to_wordpress.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        master_json = PROJECT_ROOT / "data" / "processed" / "election_results_master.json"
        county_list = [c.strip() for c in args.counties.split(",")] if args.counties else None
        mod.preview_html(master_json_path=master_json, include_counties=county_list)
        sys.exit(0)

    # ── STEP 0: SYNC COUNTY LINKS ────────────────────────────────────────────
    # Pull the latest county URLs from the Google Sheet before scraping.
    # If this fails (no internet, bad credentials), the existing CSV is used.
    if not args.mock and not args.dry_run:
        log.info("Step 0 — Syncing county URLs from the Google Sheet...")
        export_dir = PROJECT_ROOT / "export"
        if str(export_dir) not in sys.path:
            sys.path.insert(0, str(export_dir))
        import importlib.util
        spec = importlib.util.spec_from_file_location("to_sheets", export_dir / "to_sheets.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        csv_path = PROJECT_ROOT / "election_data" / "county_links.csv"
        ok = mod.sync_county_links(output_csv_path=csv_path)
        if ok:
            log.info("  ✓ county_links.csv updated from Google Sheet.")
        else:
            log.warning("  Could not sync county links — using existing county_links.csv.")

    # ── STEP 1: SCRAPE ───────────────────────────────────────────────────────
    if args.dry_run:
        log.info("Step 1 — Skipping website visits (dry-run mode: using data already on disk).")
    elif args.mock:
        log.info("Step 1 — Skipping website visits (test mode: using sample fixture files instead).")
    else:
        ok = run_scrape(args)
        if not ok:
            log.warning("Step 1 had some errors, but we'll keep going with whatever data was saved.")

    # ── STEP 2: NORMALIZE ────────────────────────────────────────────────────
    ok = run_normalize(args)
    if not ok:
        log.error("Step 2 failed — could not combine county data. Cannot continue.")
        log.error("Check that county data files exist in data/ (or tests/fixtures/ in mock mode).")
        sys.exit(1)

    # ── STEP 3: GOOGLE SHEETS ────────────────────────────────────────────────
    # Skip if editor passed --no-sheets, or if this is just a local dry-run check.
    sheets_skipped = args.no_sheets or (args.dry_run and not args.push_wp)
    if sheets_skipped:
        if args.no_sheets:
            log.info("Step 3 — Skipping Google Sheet update (--no-sheets was set).")
        else:
            log.info("Step 3 — Skipping Google Sheet update (dry-run without --push-wp).")
    else:
        run_sheets()

    # ── STEP 4: WORDPRESS ────────────────────────────────────────────────────
    # WordPress NEVER updates automatically. Only when --push-wp is explicitly passed.
    if args.push_wp:
        run_wordpress(args)
    else:
        log.info("Step 4 — WordPress publish skipped.")
        log.info("         (To publish to the website, re-run with --push-wp.)")

    log.info("=" * 60)
    log.info("Pipeline finished at %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("All done! Check the Google Sheet to review the results.")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
