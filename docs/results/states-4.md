# Results adapters: NY, OK, OR, RI, SC, SD, TN, UT, VT, WA, WV and WY

The status below reflects live checks on 2026-10-27. "Verified fixture" means a trimmed real
response is saved under `tests/fixtures/` and parsed by `tests/test_results_<st>.py` without
network access.

| State | Adapter   | Feed                                                                                                                    | Status                                                                                    |
| ----- | --------- | ----------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| NY    | —         | `https://nyenr.elections.ny.gov/`, `https://results.elections.ny.gov/`                                                  | no statewide adapter — see NY notes                                                       |
| OK    | —         | `https://results.okelections.gov/2024/`, `https://results.okelections.us/OKER/`                                         | no statewide adapter — see OK notes                                                       |
| OR    | `or_sos`  | `orresultswebservices.azureedge.us/ResultsAjax.svc/GetMapData`                                                          | statewide adapter added through the alternate endpoint in [alt-c.md](alt-c.md)            |
| RI    | `ri_boe`  | `https://rigov.s3.amazonaws.com/election/results/<YYYY>/general_election/{statewide,ga_senator,ga_representative}.json` | verified fixture (2024 general)                                                           |
| SC    | `sc_sec`  | `https://results.enr.clarityelections.com/SC/<id>/<ver>/json/en/summary.json`                                           | verified fixture (2024 general); 2026 id discovered from `/SC/elections.json`             |
| SD    | `sd_sos`  | `electionresults.sd.gov/resultsCSV.aspx`                                                                                | statewide adapter added through the CSV endpoint in [alt-c.md](alt-c.md)                  |
| TN    | —         | `https://elections.tn.gov/`, `https://sos.tn.gov/elections/results`                                                     | no statewide adapter — see TN notes                                                       |
| UT    | `ut_lgov` | `https://app.enhancedvoting.com/cdn/results/Utah/export-general<MMDDYYYY>.json`                                         | verified fixture (2024 general); 2026 export not posted yet                               |
| VT    | `vt_sos`  | `https://static.electionresults.vermont.gov/elections/<guid>{,-f-,-s-,-sn-,-h-}<stamp>.json`                            | verified fixture (2024 general); 2026 guid discovered by date                             |
| WA    | `wa_sos`  | `https://results.vote.wa.gov/results/<YYYYMMDD>/export/<YYYYMMDD>_AllState.xml`                                         | verified fixture (2024 general); 2026 export not posted yet                               |
| WV    | `clarity` | `https://results.enr.clarityelections.com/WV/122766/<ver>/json/en/summary.json` (2024 general)                          | verified fixture (2024 general); 2026 general id not published, `CLARITY_FEEDS` overrides |
| WY    | —         | `https://sos.wyo.gov/Elections/Docs/<year>/...Results.aspx`                                                             | no election-night adapter — see WY notes                                                  |

Race keys follow the shared vocabulary: `XX SEN`, `XX-01` (U.S. House), `XX GOV` and the
other statewide abbreviations, `XX SD-01` / `XX HD-01` for the legislature, and
`XX LOCAL <slug>` for everything else (ballot questions, judicial retention, county offices).
Percentages are only used when the source publishes them; otherwise they are computed from
the vote totals. Precinct counts are only reported when the source reports them for the
contest.

## Adapter notes

### RI — `ri_boe` (verified)

RI.gov's results page links three JSON documents per election, one per contest group
(statewide, state Senate, state House), plus per-town files that the adapter does not fetch.
Contest objects carry `precinct_count`, `precincts_reporting`, `total_votes` and candidate
`name`/`party_code`/`votes`. The general-assembly files repeat the party code as a name
prefix (`"DEM Jacob Bissaillon"`), which is stripped only when it matches `party_code`.
The path is date-derived (`<YYYY>/general_election`), so the 2026 documents are picked up
automatically: on 2026-10-27 S3 answered 403 for the 2026 key and the RI.gov page 404ed, and
the adapter reports itself unavailable ("not published yet") until election night.

### SC — `sc_sec` (verified)

South Carolina runs the Clarity ENR platform under its own host (`www.enr-scvotes.org/SC/`)
but the data files are the standard versioned Clarity JSON on the shared CDN. The summary
flavour differs from `clarity.py`'s: contest names contain inconsistent double spaces, choice
names repeat the party code (`"REP Nancy Mace"`), `NON` stands for "no party" (write-ins,
school board, referenda) and `PR == 0` means "reporting not published" rather than zero. Those
three things — plus 2026 election-id discovery from `/SC/elections.json`, which the vendor
adapter does not do — are why this state has its own module instead of a `clarity` entry.
`/SC/elections.json` was empty on 2026-10-27, so no 2026 id is pinned; when the state lists
the election there the adapter picks it up by date.

### UT — `ut_lgov` (verified)

Enhanced Voting CDN export, ~29 MB for the 2024 general. Ballot items of type `contest`
carry `ballotOptions` with `name`/`politicalParty`/`voteCount`; retention questions and ballot
measures are mapped to `UT LOCAL`. The 2026 export (`export-general11032026.json`) was not
posted on 2026-10-27 although the results page is live, so the URL is date-derived and the
adapter reports unavailable until the file exists.

### VT — `vt_sos` (verified)

Vermont publishes a static JSON app: an election index, a metadata document per election whose
`path` fields name the four contest documents (federal, statewide, Senate, House), and those
documents with per-town rows the parser sums by candidate id. Write-ins are lumped into a
single "Write-In" candidate. Named legislative districts ("ADD 1", "ADD RUT") become slugs
(`VT SD-add-1`, `VT HD-add-rut`); the shared `racekeys.parse` only accepts numeric districts,
so those keys must be constructed as `RaceKey("VT", Level.STATE, "HD", "add-1")` rather than
parsed from a string. The 2026 guid is already listed in `elections.json` and is discovered by
date. County and town local races live in the `-cty`/`-t` documents, which are not fetched.

### WA — `wa_sos` (verified)

`<date>_AllState.xml` (also split per office group) gives one row per
candidate with `RaceName`, `Party`, `Votes`, `VotePercent`, `Winner` and `IsBallotMeasure`.
Party labels are ballot-style (`(Prefers Democratic Party)`), normalised before
`norm_party`. District numbers come from `RaceName`; ballot measures have no party and are
mapped to `WA LOCAL`. The 2026 export and results page both 404ed on 2026-10-27, so the URL is
date-derived.

### WV — `clarity` (verified)

West Virginia is a plain Clarity state: 2024 general election id `122766` was added to
`clarity.ELECTIONS` (append-only) and `WV` was already in the adapter's `states`. The 2026
primary feed (`126209`) is live but its contests carry ` - REP` / ` - DEM` suffixes and the
general-election id is not published yet; set it with `CLARITY_FEEDS` when the state opens the
ENR site, e.g.
`CLARITY_FEEDS='[{"state":"WV","election_id":"<id>","date":"2026-11-03"}]'`.

## Blocked or unavailable original feeds

OR and SD later gained adapters through alternate official endpoints; see [alt-c.md](alt-c.md).
The notes below explain why the original feeds were not used.

### NY — not possible

`https://nyenr.elections.ny.gov/` and `https://results.elections.ny.gov/contest/5591` both
answer **403** to a plain `httpx`/`curl` request with a descriptive User-Agent — the state's
ENR front end sits behind a bot filter. The underlying data host is reachable
(`https://ny.elstats.civera.com/api/download_contest/5591.json` and
`.../5591_table.csv?split_party=true` return 200), but it only serves _per contest id_, has no
index endpoint (`/api/elections`, `/api/contests`, `/api/contest/5591` all 404) and the ids are
per election, so there is no way to discover the 2026 contests without scraping the blocked
site. No bypass was attempted.

### OK — not possible

`https://results.okelections.gov/2024/`, `https://results.okelections.us/OKER/` and the
`/OKER/api/...` paths the SPA calls all answer **403 Forbidden** to a plain request (with or
without a custom User-Agent), i.e. a WAF/Akamai-style block on the results host. No bypass
attempted.

### OR — original page not used

`https://results.oregonvotes.gov/` now answers **302** to `https://sos.oregon.gov/`, and the
SOS site has no results feed (`/elections/Pages/results.aspx` 404, the historical-data page
links HTML/pages only). No JSON/CSV/XML endpoint for the statewide results was found, so a
scraper would have to parse an unknown front end that is not yet live for 2026.

### SD — original page not used

`https://electionresults.sd.gov/` is a JavaScript SPA (`ResultsSW.aspx`, `ResultsList.aspx`,
`ResultsExport.aspx` all render a shell with zero `<table>` elements; the export page only
shows "Preparing Export..."). The data calls are not visible in the served bundle
(`/Scripts/script.bundle.js` is a generic ASP.NET/jQuery/chart bundle), `ResultsWS.asmx`
redirects to the home page, and `sdresws.azurewebsites.us` answers 404 for `/api/*` and
`/results`. The SPA's XHR calls were not reverse-engineered, so this route did not produce an
adapter.

### TN — not possible

`https://elections.tn.gov/` and `https://elections.tn.gov/results-by-county` resolve to a
CloudFront distribution that answers **403** to a plain request, and `sos.tn.gov/elections/results`
is a link hub for PDFs. No machine-readable statewide feed reachable without a browser
fingerprint; no bypass attempted.

### WY — not possible

The Secretary of State results pages (`https://sos.wyo.gov/Elections/Docs/<year>/...Results.aspx`)
are link hubs to per-county and per-office **PDFs** (no tables, no JSON/CSV/XML). Wyoming has
no statewide machine-readable results feed.
