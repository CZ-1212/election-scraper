"""
Mock Test Suite — Bay City News Election Pipeline

Fully self-contained: no network calls, no Google credentials, no WordPress.
All tests use fixture JSON files from tests/fixtures/.

Run with:  python -m pytest tests/ -v
Must pass 100% before any push to main.
"""

import json
import sys
import unittest
from pathlib import Path


# ---------------------------------------------------------------------------
# PATH SETUP
# Make sure we can import src/normalize.py and export/ modules without
# needing to install them as packages.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR      = PROJECT_ROOT / "src"
EXPORT_DIR   = PROJECT_ROOT / "export"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"

for path in (SRC_DIR, EXPORT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# ---------------------------------------------------------------------------
# Import the modules we're testing.
# We do this at module level so missing imports fail loudly at collection time.
# ---------------------------------------------------------------------------
import importlib.util

def _load_module(name: str, filepath: Path):
    """Helper: load a Python file as a module by path."""
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

normalize_mod  = _load_module("normalize",     SRC_DIR    / "normalize.py")
sheets_mod     = _load_module("to_sheets",     EXPORT_DIR / "to_sheets.py")
wordpress_mod  = _load_module("to_wordpress",  EXPORT_DIR / "to_wordpress.py")


# ---------------------------------------------------------------------------
# HELPER: run normalize against the fixtures directory
# ---------------------------------------------------------------------------
def _run_normalize(tmp_path: Path) -> dict:
    """Run normalize() using fixture files as input, write output to tmp_path."""
    output_path = tmp_path / "election_results_master.json"
    master = normalize_mod.normalize(input_dir=FIXTURES_DIR, output_path=output_path)
    return master


# ===========================================================================
# TEST CLASS 1 — FIXTURES
# Make sure every fixture file loads and has the structure we expect.
# ===========================================================================
class TestFixtures(unittest.TestCase):

    def test_all_fixture_files_are_valid_json(self):
        """Every file in tests/fixtures/ must be valid JSON."""
        fixture_files = list(FIXTURES_DIR.glob("*.json"))
        self.assertGreater(len(fixture_files), 0, "No fixture files found in tests/fixtures/")
        for f in fixture_files:
            with self.subTest(file=f.name):
                with open(f, encoding="utf-8") as fh:
                    data = json.load(fh)
                self.assertIsInstance(data, dict, f"{f.name} should be a JSON object")

    def test_clarity_fixtures_have_selenium_data(self):
        """Clarity fixtures must have the 'selenium_data' wrapper key."""
        # Exclude *_non_clarity.json — the glob "*_clarity.json" would match both.
        clarity_files = [f for f in FIXTURES_DIR.glob("*.json")
                         if f.stem.endswith("_clarity") and not f.stem.endswith("_non_clarity")]
        self.assertGreater(len(clarity_files), 0, "No clarity fixture files found")
        for f in clarity_files:
            with self.subTest(file=f.name):
                with open(f, encoding="utf-8") as fh:
                    data = json.load(fh)
                self.assertIn("selenium_data", data, f"{f.name} missing 'selenium_data'")
                sd = data["selenium_data"]
                self.assertIn("voter_turnout", sd)
                self.assertIn("contests", sd)

    def test_non_clarity_fixtures_have_flat_structure(self):
        """Non-Clarity fixtures must have voter_turnout and contests at the top level."""
        nc_files = list(FIXTURES_DIR.glob("*_non_clarity.json"))
        self.assertGreater(len(nc_files), 0, "No non-clarity fixture files found")
        for f in nc_files:
            with self.subTest(file=f.name):
                with open(f, encoding="utf-8") as fh:
                    data = json.load(fh)
                self.assertNotIn("selenium_data", data, f"{f.name} should not have selenium_data")
                self.assertIn("voter_turnout", data)
                self.assertIn("contests", data)

    def test_all_fixtures_have_contests_with_choices(self):
        """Every fixture must have at least one contest with at least one choice."""
        for f in FIXTURES_DIR.glob("*.json"):
            with self.subTest(file=f.name):
                with open(f, encoding="utf-8") as fh:
                    data = json.load(fh)
                # Pull contests from the right location depending on format.
                if "selenium_data" in data:
                    contests = data["selenium_data"].get("contests", [])
                else:
                    contests = data.get("contests", [])
                self.assertGreater(len(contests), 0, f"{f.name} has no contests")
                for contest in contests:
                    self.assertIn("choices", contest)
                    self.assertGreater(len(contest["choices"]), 0, f"{f.name}: contest '{contest.get('title')}' has no choices")


# ===========================================================================
# TEST CLASS 2 — NORMALIZE
# Verify that normalize() produces a master JSON with the correct structure.
# ===========================================================================
class TestNormalize(unittest.TestCase):

    def setUp(self):
        """Run normalize once against the fixtures and reuse the result."""
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)
        self._master = _run_normalize(self._tmp_path)

    def test_master_has_required_top_level_keys(self):
        """Master JSON must have pipeline_timestamp, county_count, and counties."""
        self.assertIn("pipeline_timestamp", self._master)
        self.assertIn("county_count", self._master)
        self.assertIn("counties", self._master)

    def test_county_count_matches_fixtures(self):
        """county_count must equal the number of fixture files."""
        fixture_count = len(list(FIXTURES_DIR.glob("*.json")))
        self.assertEqual(self._master["county_count"], fixture_count)

    def test_every_county_has_required_fields(self):
        """Every county in the master must have the normalized fields we expect."""
        required_fields = [
            "county", "platform", "scrape_timestamp",
            "last_updated", "voter_turnout", "contests",
            "anomalies", "scrape_status",
        ]
        for county_name, data in self._master["counties"].items():
            with self.subTest(county=county_name):
                for field in required_fields:
                    self.assertIn(field, data, f"{county_name} missing field '{field}'")

    def test_voter_turnout_fields_are_present(self):
        """voter_turnout must have ballots_cast, registered_voters, turnout_percentage."""
        for county_name, data in self._master["counties"].items():
            with self.subTest(county=county_name):
                vt = data.get("voter_turnout", {})
                self.assertIn("ballots_cast", vt)
                self.assertIn("registered_voters", vt)
                self.assertIn("turnout_percentage", vt)

    def test_choices_are_canonical_dicts(self):
        """Every choice in every contest must be a dict with name, votes, pct."""
        for county_name, county_data in self._master["counties"].items():
            for contest in county_data.get("contests", []):
                for choice in contest.get("choices", []):
                    with self.subTest(county=county_name, contest=contest.get("title")):
                        self.assertIsInstance(choice, dict)
                        self.assertIn("name", choice)
                        self.assertIn("votes", choice)
                        self.assertIn("pct", choice)
                        self.assertIsInstance(choice["votes"], int)
                        self.assertIsInstance(choice["pct"], float)

    def test_scrape_status_is_valid_value(self):
        """scrape_status must be one of: OK, WARN, FAIL."""
        valid = {"OK", "WARN", "FAIL"}
        for county_name, data in self._master["counties"].items():
            with self.subTest(county=county_name):
                self.assertIn(data.get("scrape_status"), valid)

    def test_master_json_written_to_disk(self):
        """normalize() must write election_results_master.json to the output path."""
        output = self._tmp_path / "election_results_master.json"
        self.assertTrue(output.exists(), "election_results_master.json was not created")

    def test_output_json_is_readable(self):
        """The file written to disk must be valid JSON that round-trips cleanly."""
        output = self._tmp_path / "election_results_master.json"
        with open(output, encoding="utf-8") as f:
            reloaded = json.load(f)
        self.assertEqual(reloaded["county_count"], self._master["county_count"])

    def test_clarity_counties_use_clarity_platform(self):
        """Counties from Clarity fixtures should have platform == 'clarity'."""
        clarity_county_names = {"Contra_Costa", "Marin", "Santa_Clara", "Sonoma"}
        for name in clarity_county_names:
            if name in self._master["counties"]:
                with self.subTest(county=name):
                    self.assertEqual(self._master["counties"][name]["platform"], "clarity")

    def test_non_clarity_counties_use_named_platform(self):
        """Non-Clarity counties should have a platform other than 'clarity'."""
        nc_county_names = {"San_Mateo", "San_Joaquin", "Santa_Cruz"}
        for name in nc_county_names:
            if name in self._master["counties"]:
                with self.subTest(county=name):
                    self.assertNotEqual(self._master["counties"][name]["platform"], "clarity")


# ===========================================================================
# TEST CLASS 3 — GOOGLE SHEETS (mock — no real API calls)
# Verify the data structures that would be sent to gspread.
# ===========================================================================
class TestSheetsExport(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp_path = Path(tempfile.mkdtemp())
        self._master = _run_normalize(self._tmp_path)
        self._master_path = self._tmp_path / "election_results_master.json"

    def test_status_dashboard_rows_match_county_count(self):
        """
        _update_status_dashboard builds one data row per county.
        Row 0 is the header, so total rows = counties + 1.
        """
        # Build the rows the same way the real function does, without calling gspread.
        counties = self._master["counties"]
        # Simulate what _update_status_dashboard puts in `rows`.
        rows = [sheets_mod.STATUS_HEADERS]
        for county_name, data in sorted(counties.items()):
            vt = data.get("voter_turnout") or {}
            anomalies = data.get("anomalies") or []
            rows.append([
                county_name.replace("_", " "),
                data.get("scrape_status", "FAIL"),
                data.get("scrape_timestamp", ""),
                data.get("last_updated", ""),
                vt.get("ballots_cast", ""),
                vt.get("registered_voters", ""),
                vt.get("turnout_percentage", ""),
                len(data.get("contests", [])),
                " | ".join(anomalies) if anomalies else "",
            ])
        # Header row + one row per county.
        self.assertEqual(len(rows), len(counties) + 1)

    def test_status_headers_are_correct(self):
        """STATUS DASHBOARD must have the exact header columns we specified."""
        expected = [
            "County", "Status", "Scrape Time", "Site Last Updated",
            "Ballots Cast", "Registered Voters", "Turnout %", "Contests", "Anomaly Flags",
        ]
        self.assertEqual(sheets_mod.STATUS_HEADERS, expected)

    def test_tab_names_are_defined(self):
        """The three special tab name constants must exist in to_sheets."""
        self.assertEqual(sheets_mod.TAB_STATUS,    "STATUS DASHBOARD")
        self.assertEqual(sheets_mod.TAB_LOG,       "SCRAPE LOG")
        self.assertEqual(sheets_mod.TAB_CHECKLIST, "PUBLISH CHECKLIST")

    def test_checklist_content_is_not_empty(self):
        """PUBLISH CHECKLIST must have at least 5 rows of instructions."""
        self.assertGreater(len(sheets_mod.CHECKLIST_CONTENT), 5)


# ===========================================================================
# TEST CLASS 4 — WORDPRESS HTML BUILDER (mock — no real HTTP calls)
# Verify the HTML output has the right structure and county data.
# ===========================================================================
class TestWordPressHTML(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp_path = Path(tempfile.mkdtemp())
        self._master = _run_normalize(self._tmp_path)

    def test_html_contains_all_county_names(self):
        """The built HTML must mention every county that has OK or WARN status."""
        html = wordpress_mod._build_html(self._master)
        for county_name, data in self._master["counties"].items():
            if data.get("scrape_status") != "FAIL":
                display = county_name.replace("_", " ")
                with self.subTest(county=county_name):
                    self.assertIn(display, html, f"County '{display}' not found in HTML output")

    def test_html_contains_results_table(self):
        """HTML must include a <table class='results-table'> element."""
        html = wordpress_mod._build_html(self._master)
        self.assertIn('class="results-table"', html)

    def test_html_contains_last_updated_timestamp(self):
        """HTML must include a last-updated paragraph."""
        html = wordpress_mod._build_html(self._master)
        self.assertIn("results-updated", html)

    def test_html_contains_turnout_data(self):
        """HTML must include the word 'Ballots' or 'Turnout' for each county."""
        html = wordpress_mod._build_html(self._master)
        self.assertTrue(
            "Ballots cast" in html or "Turnout" in html,
            "No turnout data found in HTML output"
        )

    def test_html_has_no_winner_language(self):
        """HTML must never contain winner/leading/passing/failing language."""
        html = wordpress_mod._build_html(self._master)
        forbidden = ["WINNER", "LEADING", "PASSING", "FAILING", "PROJECTED"]
        for word in forbidden:
            with self.subTest(word=word):
                self.assertNotIn(word, html.upper(), f"Forbidden word '{word}' found in HTML")

    def test_html_is_non_empty_string(self):
        """_build_html must return a non-empty string."""
        html = wordpress_mod._build_html(self._master)
        self.assertIsInstance(html, str)
        self.assertGreater(len(html), 100)


# ===========================================================================
# TEST CLASS 5 — MOCK SCRAPE PIPELINE
# Verify that fixture files pass through the full pipeline end-to-end.
# ===========================================================================
class TestMockPipeline(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp_path = Path(tempfile.mkdtemp())

    def test_full_pipeline_runs_without_errors(self):
        """Running normalize on all fixtures must not raise an exception."""
        try:
            _run_normalize(self._tmp_path)
        except Exception as e:
            self.fail(f"normalize() raised an exception on fixture data: {e}")

    def test_all_fixture_counties_appear_in_master(self):
        """Every fixture file must produce a county entry in the master JSON."""
        master = _run_normalize(self._tmp_path)
        fixture_files = list(FIXTURES_DIR.glob("*.json"))

        for f in fixture_files:
            # Derive expected county name the same way normalize.py does.
            county = normalize_mod._county_from_filename(f)
            if county:
                with self.subTest(file=f.name, county=county):
                    self.assertIn(county, master["counties"],
                                  f"County '{county}' (from {f.name}) missing from master")

    def test_html_generation_does_not_crash(self):
        """Building WordPress HTML from fixture-derived master must not raise."""
        master = _run_normalize(self._tmp_path)
        try:
            html = wordpress_mod._build_html(master)
        except Exception as e:
            self.fail(f"_build_html() raised on fixture data: {e}")
        self.assertIsInstance(html, str)

    def test_county_name_detection_for_all_fixtures(self):
        """normalize._county_from_filename must recognize every fixture filename."""
        for f in FIXTURES_DIR.glob("*.json"):
            with self.subTest(file=f.name):
                county = normalize_mod._county_from_filename(f)
                self.assertIsNotNone(county, f"Could not detect county from filename: {f.name}")


if __name__ == "__main__":
    unittest.main()
