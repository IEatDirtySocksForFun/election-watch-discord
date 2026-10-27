Dane County, Wisconsin — Clerk's unofficial results page

Recorded 2026-10-27 with a plain GET and a descriptive User-Agent
("election-watch-discord/0.1"). No cookies, no login, no bot challenge.

    https://elections.countyofdane.com/Election-Result

The saved file is the real page trimmed to four contest blocks
(race0002 "BALLOTS CAST - TOTAL", race0004 governor, race0008 US House 2,
race0010 state senate 15) inside a minimal <html><body> wrapper. Everything else
(selection list, disclaimers, scripts) is dropped.

The page was already showing the 2026 general with every contest at 0 votes and
"0 of 282 Precincts Reporting", so the numbers in the fixture are the real zeros.
Vote/percentage parsing is additionally covered by mid-count markup inline in
tests/test_results_wi.py, using the same table shape as this page.

Wisconsin reports per county, not per state: elections.wi.gov (the WEC site) answers
Cloudflare's 403 interstitial to non-browser clients, so this is the reachable official
feed for the state's second-largest county.
