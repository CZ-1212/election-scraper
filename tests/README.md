# Testing guide

This suite is small on purpose. It exists to catch the failure modes that
matter for an election-night scraper, not to chase coverage numbers.

## Running the suite

```bash
pip install -e ".[dev]"
pytest tests/
```

Tests do not touch the network and do not launch Chrome. The full suite
runs in well under a second; if a new test makes that materially slower,
something is wrong with the test, not the suite.

## What we test, and why

The scrapers have three classes of code:

1. **Pure parsing logic** — turns HTML / PDF text / XML into a JSON
   payload (`_parse`, `_parse_page_text`, `_parse_election`, helpers
   like `_extract_base_url`, `_deduplicate_contests`).
2. **Security boundary** — `validate_url`, `validate_and_secure_filepath`,
   the `ALLOWED_DOMAINS` whitelists.
3. **Network / browser orchestration** — Selenium driver setup,
   CloudFront detection, retry loops, rate limiting.

Tests cover **(1) and (2) only**. Group (3) is integration territory
and is exercised manually against live county sites; mocking Selenium
in detail produces tests that pass while the real thing breaks.

`test_pydocstyle.py` is a fourth, lighter check: it shells out to
`pydocstyle` against `src/` and `tests/` and fails if any docstring
drifts out of conformance. The convention is `pep257`; per-test
docstring requirements (D101–D103) are relaxed for the `tests/`
tree via `tests/.pydocstyle`, since pytest function names are
already descriptive.

The bar for a unit test here: it should fail loudly the next time a
county changes its HTML, and it should never fail for a reason
unrelated to the code under test.

## The fixture-first pattern

`tests/fixtures/` holds minimal hand-written reproductions of each
upstream format — not full real captures. A good fixture is the
shortest input that exercises every quirk the parser has to tolerate.
For example, `mendocino_minimal.html` deliberately includes:

- The `<meta>` tags the turnout numbers come from.
- A duplicated `STATE PROPOSITION 50` table, because SSRS renders the
  same contest twice and the parser must dedupe.
- Header rows the parser must skip (`Choice`, `Party`, `Cast Votes:`).
- An all-caps banner row (`MENDOCINO COUNTY, CALIFORNIA`) that looks
  like a contest title but isn't.

When a county changes its upstream output, update the fixture to
match the new shape and add a regression test for whatever broke.
Don't replace the whole fixture with a fresh real capture — that
defeats the point of keeping it minimal and readable.

## Why we bypass `__init__`

`BaseScraper.__init__` calls `validate_url(url)` and constructs a
`requests.Session`. Neither is interesting for parser tests, and the
session creation drags in `urllib3` retry config we don't want to
exercise here. Tests build instances via the `make_scraper()` helper
in `conftest.py`:

```python
scraper = make_scraper(MendocinoScraper, url, county_name="Mendocino")
data = scraper._parse(soup)
```

That uses `cls.__new__(cls)` and binds only the attributes the
parser actually reads. If a parser starts depending on something
else — say `self.session` — add it to `make_scraper()` rather than
spreading per-test setup.

## Cross-module drift checks

`clarity_scraper.py` and `multi_platform_scraper.py` each define
their own `validate_url`, `validate_and_secure_filepath`,
`ALLOWED_DOMAINS`, and `ALLOWED_FILE_EXTENSIONS`. These two copies
have drifted in the past. The tests in `test_security.py` are
parametrized across both modules so a fix applied to one and not
the other fails the suite. There is also an explicit subset check:
clarity's whitelist must stay a subset of the multi-platform one.

If you find yourself disabling a drift test, fix the divergence
instead — or, better, extract the shared code into one place.

## The JSON output schema is the contract

`test_output_schema.py` validates every file in `data/samples/`
against an explicit JSON Schema for the Clarity output envelope.
Downstream consumers — CMS imports, results pages, dashboards —
rely on this shape. The rule:

- **Adding** a documented field: update the schema, add a test for it.
- **Removing** a documented field: it's a breaking change. Bump
  whatever version downstream consumers track and call it out.
- **Adding** an undocumented field: fine, the schema sets
  `additionalProperties: true` on purpose so scrapers can carry
  county-specific extras.

## Adding a test for a new county

1. Capture a tiny representative slice of the upstream output in
   `tests/fixtures/<county>_<format>.{html,txt,xml}`. Keep it under
   ~100 lines if you can; trim aggressively.
2. Add a `_<county>_data` helper in `test_parsers.py` that builds
   a scraper via `make_scraper()` and runs the parser against the
   fixture.
3. Write assertions for: voter turnout numbers, page title, contest
   titles, and at least one choice's votes + percentage. Those four
   are the fields downstream consumers actually publish.
4. If the parser has a quirk-handling branch (dedupe, skip rows,
   multi-column layout), add a test that exercises it directly so
   regressions are obvious from the failure name.
