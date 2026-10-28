# Alternate official results: MI, WI, MN, NV, NH and ME

The statewide sites for these six states were blocked, PDF-only or certified-only when checked
in `docs/results/states-1.md`. These notes cover the next best options: another official
statewide endpoint, official county feeds merged into a partial-state result, or a clear
explanation when nothing is reachable. No bot check, captcha, login or agreement was bypassed,
faked or replayed. Every feed below answered a plain GET with a descriptive User-Agent
(`election-watch-discord/0.1`).

| State | Adapter     | Source                                                                         | Coverage                                                                                                                                                                                                                                  | Verified                                                                                                                                              |
| ----- | ----------- | ------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| MN    | `mn_sos`    | https://electionresultsfiles.sos.mn.gov/`<YYYYMMDD>`/*.txt                     | **statewide** — all 87 counties, 871 contests: US Senate, US House, Governor, State Auditor, 67 state senate + 134 state house, judicial, county races & questions                                                                        | **yes** — trimmed real 2026-11-03 files in `tests/fixtures/mn_sos/`, live fetch 2026-10-27 returned the 871 contests                                  |
| MI    | `mi_county` | Clarity county feeds, `https://results.enr.clarityelections.com/MI/<County>/…` | **partial: 1 county live** (Ottawa, 178,507 of Michigan's 5,664,186 votes in the 2024 presidential = 3.2 %); Oakland/Macomb/Genesee configured in the adapter, their `elections.json` is empty until the county turns the 2026 general on | **yes** — real Ottawa 2026 general summary/elections.json in `tests/fixtures/mi_county/`, live discovery + fetch 2026-10-27                           |
| WI    | `wi_dane`   | https://elections.countyofdane.com/Election-Result                             | **partial: 1 county** (Dane, 364,840 of Wisconsin's 3,415,213 votes in 2024 = 10.7 %); whole county ballot incl. assembly/senate districts and county offices                                                                             | **yes** — trimmed real page in `tests/fixtures/wi_dane/`, live fetch 2026-10-27 returned 33 contests (all at 0 votes: the county has not counted yet) |
| NV    | —           | —                                                                              | 0                                                                                                                                                                                                                                         | **not possible** — see below                                                                                                                          |
| NH    | —           | —                                                                              | 0                                                                                                                                                                                                                                         | **not possible** — see below                                                                                                                          |
| ME    | —           | —                                                                              | 0                                                                                                                                                                                                                                         | **not possible** — see below                                                                                                                          |

2024 vote shares are the presidential vote totals per county against the state total
(NYT/NPR county tables for Wisconsin, Wikipedia's Michigan county table), used only to
size the county feeds — they are not part of the runtime.

Until a state's adapter is switched on, the national `cbs_results` fallback still covers
Senate / Governor / House for all six.

## Minnesota — `mn_sos` (statewide, verified)

`electionresults.sos.mn.gov` answers Radware's `__uzdbm_*` challenge, but the SOS
publishes the same numbers as semicolon-delimited text on a _different, ungated_ host —
the file host the results site itself reads:

    https://electionresultsfiles.sos.mn.gov/20261103/ussenate.txt
    .../ushouse.txt  .../stsenate.txt  .../LegislativeByDistrict.txt
    .../governor.txt .../auditor.txt   .../judicial.txt
    .../cntyRaceQuestions.txt .../local.txt .../SDRaceQuestions.txt
    .../Cntytbl.txt (county code -> name) .../PartyTbl.txt (party code -> name)

(`electionresultsfiles.sos.state.mn.us` 302s to the same host.)

One row per candidate per contest, 16 fields:

    MN;;;0104;U.S. Representative District 1;1;0201;Rachel Bohman;;;DFL;749;749;156375;41.41;377601
     0 1 2     3    4                          5  6    7            8 9  10  11  12 13      14    15
     0 state, 1 county code, 3 contest code, 4 contest name, 5 district, 7 candidate,
     10 party, 11/12 precincts reporting/total, 13 votes, 14 percent, 15 contest total

- Keys: `U.S. Senator` -> `MN SEN`; `U.S. Representative District n` -> `MN-nn`;
  `State Senator District n` -> `MN SD-nn`; `State Representative District nB` ->
  `MN HD-nB`; `Governor & Lt Governor` -> `MN GOV`; `State Auditor` -> `MN AUD`;
  everything else (judicial, county boards, soil & water, school boards, ballot
  questions) -> `MN LOCAL <slug>`, with the county name prefixed for county offices.
- Party codes are the SOS's own (`DFL` -> `DEM`, `R`, `IND`, `LIB`, `WI` write-in);
  write-in rows are dropped.
- The export carries no winner flag, so `winner` stays `False`; percentages come from
  the feed, and `RaceResult.finalize()` recomputes what is missing.
- Files are Windows-1252 with a stray non-UTF-8 byte (`decode_bytes()` falls back from
  UTF-8 to cp1252).
- Tolerance for a late-starting election: a missing office file is skipped, and only a
  date with no files at all raises `SourceUnavailable`.
- **Gap:** the SOS publishes `governor.txt` and `auditor.txt` only. There is no
  attorney-general or secretary-of-state export (`attorneygeneral.txt`,
  `secretaryofstate.txt`, `ag.txt`, `sos.txt` are all 404), so `MN AG` and `MN SOS` stay
  on `cbs_results`. `local.txt` / `SDRaceQuestions.txt` (township and school district
  contests, thousands of keys) are deliberately not fetched by default; add them to
  `FILES` if a deployment wants them.

## Michigan — `mi_county` (partial, verified for one county)

`mielections.us` still times out (curl exit 28) and `mvic.sos.state.mi.us` still answers
Cloudflare's 403 interstitial, so the state itself is out. The counties run their own
reporting systems, and the largest ones on the Clarity/Scytl platform serve the standard
vendor feeds:

    https://results.enr.clarityelections.com/MI/<County>/elections.json
    .../MI/<County>/<EID>/current_ver.txt
    .../MI/<County>/<EID>/<version>/json/en/summary.json
    .../MI/<County>/<EID>/<version>/json/en/electionsettings.json   (websiteupdatedat)

`elections.json` is the discovery hook — it lists the jurisdiction's configured
elections (`EID`, name, `Date`), so the election id is found by date instead of being
hard-coded. `MI_CLARITY_FEEDS` (JSON list of `{"county", "election_id", "date"}`)
pins ids when a county does not list them. Every fetched county is merged into one
partial picture: votes summed per candidate, precinct counts summed, percentages
recomputed, title suffixed ` (partial: N counties)`, `url` set to the state results page.

- Parsing is the shared `clarity.parse_clarity`; this adapter only re-keys the names to
  Michigan ones: `Governor and Lieutenant Governor` -> `MI GOV`, `Secretary of State` ->
  `MI SOS`, `United States Senator` -> `MI SEN`, `Representative in Congress 2nd
District` -> `MI-02`, `State Senator 30th District` -> `MI SD-30`, `Representative in
State Legislature 43rd District` -> `MI HD-43`; county offices and the like stay
  `MI LOCAL <slug>`, so state-legislative and county races are covered.
- Live on 2026-10-27: Ottawa discovered `EID 127391`, version `381490`, 68 contests on
  the ballot, 0 votes (`websiteupdatedat` 10/19/2026). Oakland, Macomb and Genesee return
  an empty `elections.json` for the 2026 general today; they are first in `COUNTIES` and
  will be picked up automatically once the county configures the election.
- A county that has not published is skipped (its error is collected and reported only
  if _no_ county produced anything, which then raises `SourceUnavailable`).
- **Not covered:** Wayne County (Detroit, the largest) runs an ES&S `ResultsSW.aspx` app
  on `waynecountyresults.azurewebsites.us` with a JS pager and no per-contest URL, so it
  is not parseable without driving the page's postbacks.

## Wisconsin — `wi_dane` (partial, scrape, off by default)

`elections.wi.gov` still answers Cloudflare's 403 interstitial, and Wisconsin has no
statewide election-night reporting system: municipal clerks report to the 72 county
clerks, who post the unofficial count themselves. Dane County (Madison) server-renders
one HTML page per contest that a plain GET returns:

    https://elections.countyofdane.com/Election-Result

    <div class="row" id="race0004"><h4>Governor / Lieutenant Governor</h4>
      … Precincts Reporting (0 of 282) …
      <tr><td><strong>REP Tom Tiffany /David Varnam</strong> <em>(REP)</em></td>
          <td><div class="progress">…</div>0.0%</td><td class="text-right">0</td></tr>

- No API and no JSON are offered, so `scrape = True`, `enabled_by_default = False`; the
  adapter adds no cache of its own, so the operator's poll interval (whole minutes, ≥ 1)
  is what keeps the request rate at or below one a minute.
- Keys: `Governor / Lieutenant Governor` -> `WI GOV`, `Attorney General` -> `WI AG`,
  `Secretary of State` -> `WI SOS`, `State Treasurer` -> `WI TREAS`, `Representative in
Congress District n` -> `WI-nn`, `State Senator District n` -> `WI SD-nn`,
  `Representative to the Assembly District n` -> `WI HD-nn`, everything else (sheriff,
  clerk of circuit court, municipal judges, referenda) -> `WI LOCAL <slug>`.
- `BALLOTS CAST - TOTAL` blocks and `write-in:` rows are dropped; the party is printed
  twice (`REP … (REP)`) and is de-duplicated; `(PNP)`-style third parties are kept as
  published (normalised to `PNP`).
- Live on 2026-10-27: 33 contests listed, all at 0 votes, `0 of 282` precincts — the
  county has the 2026 general staged but has not counted.
- **Coverage:** one county (10.7 % of the 2024 presidential vote). Milwaukee (13.5 %)
  is Cloudflare-blocked on `county.milwaukee.gov`, Waukesha publishes PDF only (and the
  bot has no PDF parser), and no Wisconsin jurisdiction is a Clarity client — every
  `WI/<County>/elections.json` probe returned the vendor's error page. Adding another
  county means a new parse function, not a config row.

## Nevada — write-up

- `silverstateelection.nv.gov` — Imperva Incapsula challenge (HTTP 200, `x-iinfo` +
  `incap_ses_*`, no markup). Its `/library/2026/` path is a real page but only lists
  documents.
- `www.nvsos.gov/…/election-results` — a 926-byte link farm, no results data.
- Clark County (`elections.clarkcountynv.gov/ElectionResults_NV`) is Blazor Server: the
  page is a 2.5 KB shell and every number arrives over a SignalR websocket in a binary
  protocol. Driving that is a browser emulation, not a feed.
- Washoe County links to `electionresults.washoecounty.gov`, which today answers HTTP
  503 with a static "Washoe County Election Results" shell (no table, no data endpoint)
  and, alongside it, per-contest PDFs (`WASHOE2026PRIMARYSummaryNN.pdf`) — PDF only, and
  the bot deliberately has no PDF dependency.
- Nothing reachable; NV stays on `cbs_results` (Senate/Gov/House).

## New Hampshire — write-up

- `www.sos.nh.gov` answers CloudFront WAF 403 "Access Denied" for a descriptive UA and
  for a browser UA alike (`/elections-results` included).
- `electionresults.sos.nh.gov` does not resolve at all (curl exit 6), and NH reports
  town-by-town rather than through a state ENR application.
- No reachable feed; NH stays on `cbs_results`.

## Maine — write-up

- `www.maine.gov/sos/elections-voting/election-results-data` answers 403 to non-browser
  clients, and the page is a post-count document index: per-contest Excel workbooks plus
  the RCV round tabulations (PDF) published after the count. Maine has no live ENR feed
  at the state level — the towns report through the Secretary of State's canvass.
- No reachable feed; ME stays on `cbs_results`.

### If a ME / NH / NV county feed turns up later

`clarity.Clarity.states` already declares `NV`, `ME` and `NH` (the vendor runs some
counties and towns there), but `clarity.ELECTIONS` has no entry for them. A county that
starts publishing on the Clarity host needs only a config row — either a
`2026-11-03` entry in `clarity.ELECTIONS` (`{"state": "ME", "jurisdiction": "<Town>",
"election_id": "<id>"}`) or the same object in `CLARITY_FEEDS` — no new module.

## Adapter defaults and request costs

- `mn_sos` and `mi_county` are on by default and set `scrape = False`. `wi_dane` sets
  `scrape = True` and `enabled_by_default = False`.
- `mn_sos` fetches up to 8 text files per poll. `mi_county` fetches one `elections.json`
  plus 3 requests per configured county. The poller's request budget sets the actual interval.
- No extra dependencies are needed. Tests use fixtures and never touch the network:

  `pytest -q tests/test_results_mn.py tests/test_results_mi.py tests/test_results_wi.py`
