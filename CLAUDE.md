# Election Night Results Scraper — Project Brief

Baseline context for this project. Treat this file as the source of truth for how
the scraper and results template are supposed to behave.

## Goal

A 13 county California election night results pipeline for Bay City News. The
scraper mirrors exactly what each county registrar posts and interprets nothing.
It adds live vote tallies and turnout to static content that is already produced,
then renders into a results template for embedding on our sites.

## The 13 counties, grouped by results platform

Write one parser per platform and reuse it across the counties on that platform.

- Clarity / ENR (results.enr.clarityelections.com): Contra Costa, Marin, Santa Clara, Sonoma
- LiveVoterTurnout: San Mateo, San Joaquin
- County custom pages: Alameda, Mendocino, Monterey, Napa, San Francisco, Santa Cruz, Solano

## Which link to use, testing now versus election night

Counties that have a zero report: Contra Costa, Marin, San Mateo, Solano, Sonoma.
For these, the zero report URL is the live results portal. It shows zeros now and
fills in with real numbers on the night.

- Testing now: use the zero report if the county has one, since that is this exact
  election's format. Otherwise use the past election link.
- Election night: scrape the zero report URL for counties that have one. Scrape the
  placeholder URL for counties that do not.

Note on Santa Clara: its past results are on Clarity, but its live placeholder is the
county's own site. Confirm which platform the live page actually uses before the night.

All county URLs (zero report, placeholder, past election) live in the county links CSV.

## Scraper scope and rules

- Local races only. Static content (race names, candidates, professions, measure
  descriptions) is already produced and does not change.
- On the night the scraper adds only vote tallies and turnout data.
- It interprets NOTHING. No pass or fail on measures, no thresholds, no winner or
  leading calls on candidates. Show options, votes and percentages exactly as the
  county posts them. Rows display in vote order, which only reflects the numbers.
- If a county's own page prints a status string, that text may be mirrored verbatim,
  but the scraper never derives it.

## Template (the render target)

- Namespaced under `.lnm-results-widget` so it embeds cleanly in WordPress / Newspack
  and ActiveCampaign without colliding with the page.
- No interpretation: no winner, leading, passing or failing styling.
- Turnout strip renders only the stats a county actually provides and stays balanced
  at two, three or four stats. Omit the bar if a county gives no turnout data.
- Per contest data (contest, each option or candidate, votes, percent) is universal
  across counties, so contest rows are constant. Only the turnout strip varies.
- Provenance always renders: source link, last updated timestamp, and precincts when
  the county reports them.

## Render contract

The template expects each contest resolved to structured fields, not concatenated
strings. For every option or candidate provide the label or name, the votes, and the
percent as separate values, so rendering is a direct mapping. Percent comes from the
county if posted, otherwise computed from votes within that contest. Never mix the two
in one contest.

## Election night access

- Prefer official data feeds. For the four Clarity counties pull the structured Reports
  files (XML, CSV, XLS) rather than scraping the rendered page. The OpenElections
  `clarify` Python library discovers those file URLs. Check the LiveVoterTurnout and
  custom county pages for an export or download link too. Pulling a static file sidesteps
  the election night bot defenses entirely and is lighter on the registrar's server.
- For pages with no feed, behave like one polite visitor. Use a real browser engine for
  the JavaScript pages so challenges and clearance cookies resolve, set normal headers,
  process one county at a time, poll slowly (every few minutes, not seconds) with
  exponential backoff on 429, 403 and 503, and cache the last good pull so a slow or
  blocked fetch shows the last numbers with a timestamp instead of going blank.
- Email the registrars ahead of time for feed access or whitelisting. That beats
  fighting a firewall live, and the county's own data file is also the cleanest provenance.
