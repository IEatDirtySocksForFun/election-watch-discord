# Alternate official results: MT, OK, OR, SD, TN, WY, MD and MS

These sources were checked on 2026-10-27 with a plain HTTP client and a descriptive User-Agent
(`election-watch-discord/0.1`). The checks used no cookies, JavaScript, browser fingerprints,
login or agreement pages.

## Summary

| State | Adapter | Source | Coverage | Verified |
|---|---|---|---|---|
| MT | `mt_sos` (new) | `electionresults.mt.gov/resultsSW.aspx?type=…&eid=…` (id discovered from `candidatefiling.mt.gov`) | statewide: US Senate/House, 6 statewide offices, state senate + house districts, PSC, supreme/district court, ballot issues | yes — 2024 general replayed live (89 races listed), 2026 general id resolves and is empty until election night |
| SD | `sd_sos` (new) | `electionresults.sd.gov/resultsCSV.aspx?text=All&type=SWR\|BQ\|LEG\|CTY\|MUN` | statewide: all 5 contest families (statewide, ballot questions, legislative, county, municipal) | yes — 2026 general test data live (122 of 671 precincts) |
| OR | `or_sos` (new) | `orresultswebservices.azureedge.us/ResultsAjax.svc/GetMapData?type=SWPAR\|FED\|HOUSE\|SENATE\|MEASURE` | statewide offices, US Senate/House, all 60 HD + 30 SD districts, statewide measures (no county/local races) | yes — 2026 general live, 83 races listed |
| MD | `md_sbe` (extended) | `elections.maryland.gov/elections/2026/general_results/index.html` + per-contest pages | statewide: Governor/Lt.Gov, Comptroller, AG, 8 CD, 47 SD, 71 HD (lettered), judges, questions | yes — 2026 general live, 141 races listed, "NR" everywhere until counting |
| OK | — | nothing reachable | none (cbs_results covers Senate/Gov/House only) | no |
| TN | — | nothing reachable | none | no |
| WY | — | certified XLSX zip only | none before certification | no |
| MS | — (existing adapter = certified files) | certified artifacts only | unchanged | no election-night feed found |

New dependencies: none. Shared contracts unchanged.

## MT — `electionwatch/sources/results/mt_sos.py`

* **The block, as found:** `https://electionresults.mt.gov/` answers **403** with
  `cf-mitigated: challenge` (Cloudflare) and the bare root 302s to sosmt.gov. `sosmt.gov`
  pages likewise redirect back.
* **What is open:** the per-election results pages answer **200** to a plain request, e.g.
  `…/resultsSW.aspx?type=FED&map=CTY&eid=450002785` (2024 general) and `type=STATE`,
  `SENATE`, `HOUSE`, `PSC`, `SCJ`, `JUD`, `BQ`. One page per contest group, the same
  ASP.NET "Results SW" markup the ND/NM adapters already parse.
* **Election ids:** discovered from the Secretary of State's candidate-filing site, whose
  election dropdown is the public list of the current cycle's elections:
  `https://candidatefiling.mt.gov/candidatefiling/CandidateList.aspx` (302 to the default
  election, then a `<select id="ddlElection">` such as
  `<option value="450002987">FEDERAL GENERAL 2026 (11/03/2026) (General)</option>`). That id
  opens `Default.aspx?eid=450002987` → "2026 General Election - November 3, 2026".
  `MT_EID` pins an id (e.g. `MT_EID=450002785` to replay 2024).
* **Guards:** the tracked date is matched against the candidate-filing date and against the
  date in the page header (unless pinned); a group with no contests is skipped; if no group
  publishes contests yet the adapter raises `SourceUnavailable` ("publishes no contests yet")
  instead of pretending the state has zero votes.
* **Format notes:** first row of every contest is a header row (no candidate name); no
  winner flag and no percentages on the page (computed); "Precincts Fully" + "Partially" over
  the same total; names upper case with occasional double spaces (collapsed, kept verbatim);
  "Non-Partisan" is recorded as `NP`.

## SD — `electionwatch/sources/results/sd_sos.py`

* **Source:** the media/press feed the state board publishes, plain CSV, no key:
  `…/resultsCSV.aspx?text=All&type=SWR|BQ|LEG|CTY|MUN&map=CTY|DIST`
  (`LEG` needs `map=DIST`). The media index `…/ResultsMedia.aspx` supplies election date,
  election type, ballots cast, registered voters, precinct reporting and the "last updated"
  stamp from its hidden fields.
* **Coverage:** all five families, i.e. the whole certified result set — statewide races,
  ballot questions, legislative districts, county races/questions, municipal races/questions.
* **Verification:** live on 2026-10-27 the site was in "Testing In Progress" mode
  (122 of 671 precincts), so the 2026 general CSV is already serving. Keys checked live:
  `SD SEN`, `SD-AL`, `SD GOV`, `SD SD-01`, `SD HD-01`.

## OR — `electionwatch/sources/results/or_sos.py`

* **The block, as found:** `results.oregonvotes.gov` is the site the press uses, but out of
  election season it 302s to a not-found page and it publishes no JSON itself.
* **What is open:** the JSON service the same front end calls, on the state's Azure CDN:
  `https://orresultswebservices.azureedge.us/ResultsAjax.svc/GetMapData?type=FED&category=SW&raceID=0&osn=0&county=0&party=0`.
  `type` ∈ `SWPAR`, `FED`, `HOUSE`, `SENATE`, `MEASURE` (`CTYALL` holds 2,300+ local
  races/measures in 2.4 MB, out of scope). No key, no cookies, `application/json`.
* **Format notes:** one row per candidate; all parties in one race; Oregon's fusion
  nominations appear in `PartyName` ("Democrat, Working Families"), the first name is the
  party recorded; `PartyCode` is the contest code ("NON") and is ignored; write-in rows have
  no party; the state's own `Winner` flag is carried through; `PrecinctsReporting` +
  `PrecinctsPartial` over `TotalPrecincts`. The payload has **no election date**, so the
  tracked date comes from configuration (the service only ever answers for the election the
  state is currently publishing).
* **Verified:** 2026 general live — Governor (4 candidates), US Senate, 6 US House districts,
  60 state house and 15 state senate districts; `list_races` returns 83 races; all counts 0
  before election night.

## MD — `electionwatch/sources/results/md_sbe.py` (extended, certified CSV path kept)

* **What existed:** the adapter read the post-election `*_CongressionalBreakDown.csv` /
  `*_LegislativeBreakDown.csv` (certified only).
* **Election-night feed found:** the board's own site publishes the running, unofficial count
  as static HTML under the election's year —
  `https://elections.maryland.gov/elections/2026/general_results/index.html`, an index of
  contest anchors (`gen_results_2026_<n>[_<district>].html`, questions
  `gen_qresults_2026_<n>.html`) whose pages carry
  "Unofficial … General Election Results for <contest>", "Last refreshed: …",
  "(N of 1979 election day precincts reported)" and a plain table
  `Name | Party | Early Voting | Election Day | Mail-In Ballot | Provisional | Total | Percentage`.
* **Behaviour now:** `fetch()` uses those live pages (index + one request per tracked
  contest); if the year's directory is not published (404) it falls back to the certified
  CSVs exactly as before. `list_races()` prefers the live index (141 contests for 2026).
* **Format notes:** before counting every cell is "NR" (not reported) → recorded as 0 votes
  with 0 precincts reported, not as a result; the winner is marked with
  `<img alt="Winner Selected">`; ballot-question rows put the option label in `<th>` and the
  numbers in `<td>`; primary pages hold one table per party, so their per-party percentages
  are dropped when tables are merged (the adapter targets the general-election pages).
  `race_key` gained a `Question N` branch (`MD LOCAL question-N`) and `_PARTIES` a
  "Working Class Party" entry — both append-only, the certified path is unchanged.
* **Verified:** live for 2026-11-03 — `MD GOV`, `MD-01`, `MD SD-27`, `MD HD-01A` fetched,
  141 contests listed. (Maryland has no US Senate race in 2026, so `MD SEN` correctly
  resolves to nothing.)

## OK — nothing reachable

* `results.okelections.us`, `results.okelections.gov` and `www.okelections.us` answer **403
  Forbidden** (nginx) for every path and every UA tried — root, `/OKER?elecDate=20241105`,
  `/OKER/api/elections`, with and without a UA.
* The state board's `oklahoma.gov/elections/...` pages are open (200) but only link to the
  blocked results host; the CSV/XML/PDF race-, county- and precinct-level exports live on
  that host's Export tab, i.e. behind the same block.
* County boards: `oklahomacounty.org` (the largest county, ~12% of the 2024 vote) is
  reachable but publishes no structured results feed (no results/CSV/JSON links on its
  Election Board page); `tulsacounty.org/283/Election-Board` 404s. A county-feed adapter
  would need Oklahoma, Tulsa, Cleveland, Canadian and Payne to approach half the state's
  vote, and each would need a parser for ad-hoc HTML or PDF.

## TN — nothing reachable

* Both `elections.tn.gov` and `sos.tn.gov` answer **403 CloudFront "Request blocked"** — the
  Secretary of State's whole domain, including the historical results pages and the
  per-precinct spreadsheets.
* No alternate official host was found; county election commissions (Shelby `shelbyvote.com`,
  Davidson, Knox) are reachable but publish results through their own one-off pages/systems.
  Covering most of Tennessee would need about 6 county feeds (Shelby, Davidson, Knox,
  Hamilton, Williamson and Rutherford), each with its own parser.

## WY — certified XLSX only

* `sos.wyo.gov` is reachable, but `Elections/ElectionResults.aspx` is a link hub; the
  official results are a zip of Excel workbooks published after certification, e.g.
  `https://sos.wyo.gov/Elections/Docs/2024/Results/General/2024_Wyoming_General_Results.zip`
  (referenced from `/Elections/Docs/2024/2024GeneralResults.aspx`).
* No election-night feed exists that we could reach, and reading XLSX would add a dependency
  (`openpyxl`) for a certified-only, three-electoral-vote state. Not done.

## MS — certified artifacts only (existing adapter unchanged)

* `sos.ms.gov` is reachable. The results stubs
  (`/elections/electionresults_aspx/elections_results_<year>_general.aspx`) only link to
  certified artifacts: `elections_results_<year>_certified.aspx`,
  `elections_results_<year>_county.aspx`, `Official Statewide Results.pdf`,
  `<year>ElectionRecapSheets.csv`. The 2026 stub 404s until after the election.
* No election-night reporting feed (ENR/clarity-style) is published by the Secretary of
  State, so the existing certified-file adapter remains the only option.

## Operational notes

* Polling: MT 8 requests/poll, SD 5 + 1 media page, OR 5, MD 1 index + 1 per tracked contest.
  All are fine at the bot's ≥60 s interval; MD is the only one that scales with the number of
  tracked contests.
* All four adapters raise `SourceUnavailable` (never fabricated zeros) when the tracked
  election has no published data yet.
* `scrape` is `True` for MT and MD (HTML), `False` for SD (CSV) and OR (JSON);
  `enabled_by_default` is `True` for all four because these are the states' own official
  sources, not third-party scrapes.
