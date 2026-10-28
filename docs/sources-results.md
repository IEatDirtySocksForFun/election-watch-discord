# Results sources

Official state election-night feeds, one `ResultsSource` adapter per feed family. Every
adapter exposes a pure `parse_*(payload, ..., fetched_at=...) -> list[RaceResult]` plus
`fetch()` / `list_races()`, and registers itself in `electionwatch.sources.base.REGISTRY`
under `results`.

| State | Adapter | Feed | Status |
|-------|---------|------|--------|
| TX | `tx_sos` | https://results.texas-election.com/static/data/ | verified fixture (2024 general); 2026 ID not published yet, discovery implemented |
| GA | `ga_sos` | https://results.sos.ga.gov/cdn/results/Georgia/export-<slug>.json | verified fixture (2020 general); 2024 export live, 2026 404 on 2026-10-27 |
| NC | `nc_sbe` | https://er.ncsbe.gov/enr/<YYYYMMDD>/data/ | verified fixture (2024 general); live 2026 general feed returns a 0-vote Senate contest |
| PA | `pa_dos` | https://www.electionreturns.pa.gov/api/ElectionReturn/ | verified fixture (2024 general); 2026 election id 117 published, no results yet |
| IL (McLean Co.) | `clarity` | https://results.enr.clarityelections.com/IL/McLean/127349/ | verified fixture (2026 general sample upload) |
| WV | `clarity` | https://results.enr.clarityelections.com/WV/126209/ | verified fixture (2026 primary, non-partisan contests) |
| AZ | `enhanced_voting` | https://app.enhancedvoting.com/cdn/results/AZ/export-2026NovGen.json | configured for 2026; URL not published yet (Arizona switches to Enhanced Voting for 2026) |
| GA | `enhanced_voting` | https://app.enhancedvoting.com/cdn/results/Georgia/export-2024NovGen.json | verified fixture (2024 general); same format as GA SOS |
| FL | `fl_sos` | https://results.elections.myflorida.com/ResultsExtract.Asp | verified fixture (2024 general tab-delimited extract) |
| MN | `mn_sos` | https://electionresultsfiles.sos.mn.gov/`<YYYYMMDD>`/*.txt | **verified fixture** — 2026 general text exports; live 2026-10-27 returns 871 contests (statewide) |
| MI | `mi_county` | https://results.enr.clarityelections.com/MI/<County>/ | **verified fixture** — Ottawa 2026 general; Oakland/Macomb/Genesee configured, index empty until published; merged, ` (partial: N counties)` |
| WI | `wi_dane` | https://elections.countyofdane.com/Election-Result | **verified fixture** — Dane County 2026 general page (0 votes); HTML scraper, opt-in, ` (partial: N counties)` |
| TN (Shelby Co.) | `tn_counties` | https://app.enhancedvoting.com/cdn/results/shelby-county-tn/export-<slug>.json | **verified fixture** — Shelby 2024 general (142 precincts); 2026 export 404 on 2026-10-27; `TN_COUNTY_FEEDS` pins another county/slug |
| TX (counties) | `tx_counties` | https://results.enr.clarityelections.com/TX/<County>/ | **verified fixtures** — 17 configured, 2024 for 13 counties, 2026 for Denton/Galveston/Kaufman; per-county 2024 shares in docs/results/counties.md |
| NV, NH, ME | — | — | blocked / PDF-only; see docs/results/alt-a.md |

"Verified fixture" means `parse_*` is tested against a trimmed real response saved under
`tests/fixtures/<adapter>/` (each README.txt records the URL and fetch date). Nothing in
the tests touches the network.

## Enhanced Voting / Scytl — `enhanced_voting`

Many states and counties publish through Enhanced Voting's CDN export:

    https://app.enhancedvoting.com/cdn/results/<State>/export-<slug>.json

The export is the same JSON shape as the Georgia SOS export used by `ga_sos`,
so this adapter reuses `parse_ga` and remaps keys to the requested state.

- `ELECTIONS`: `2024-11-05` = Georgia (`2024NovGen`, verified); `2026-11-03` =
  Arizona (`2026NovGen`, expected from Arizona's announced switch to Enhanced
  Voting; the URL should be verified once azsos.gov publishes the 2026 general
  results page).
- Contest names can be hyphenated (`US House of Representatives - District 1`)
  or unhyphenated (`US House District 1`); both map to canonical RaceKeys.
- No precinct counts or winner flags are present in the export.

## Florida — `fl_sos`

Florida publishes a tab-delimited extract via a POST to `ResultsExtract.Asp`:

    ElectionDate=11/5/2024&OfficialResults=Y&PartyRaces=N&DataMode=

Rows are one candidate per county. The adapter aggregates county rows to
produce statewide (or district-wide) totals.

- Race codes: `PRE` -> LOCAL, `USS` -> `FL SEN`, `USR` + Juris1num -> `FL-nn`,
  `STS` + Juris1num -> `FL SD-nn`, `STR` + Juris1num -> `FL HD-nn`, `Axx` and
  judicial retention codes -> LOCAL.
- Candidate names are built from `CanNameLast`/`CanNameFirst`/`CanNameMiddle`;
  president rows encode the ticket as VP-last / president-last and are rendered
  as "PRESIDENT / VP".
- Precincts reporting/total are summed across counties for each race.

## More per-state notes

Statewide and county adapters have their own notes under [docs/results/](results/).
Start with [states-1.md](results/states-1.md) through [states-4.md](results/states-4.md), then
see the alternate-source and county notes for states whose main site is blocked or incomplete.

## Environment

- `ELECTION_DATE` — ISO date of the tracked election, default `2026-11-03`
  (`electionwatch/sources/results/_common.py`). Every adapter uses it to pick the election.
- `CLARITY_FEEDS` — JSON list overriding the built-in Clarity feeds, e.g.
  `[{"state":"WI","jurisdiction":"Dane","election_id":"123456"}]`. Entries need `state`
  and `election_id`; `jurisdiction` is optional (omit for a statewide feed) and `date`
  limits an entry to one election date. Without a matching feed for the requested state,
  the Clarity adapter raises `SourceUnavailable` instead of guessing URLs.
- `ENHANCED_VOTING_FEEDS` — JSON list overriding the built-in Enhanced Voting
  feeds, e.g. `[{"state":"AZ","slug":"2026NovGen"}]`. Entries need `state` and
  `slug`; `date` limits an entry to one election date.
- `TX_COUNTY_FEEDS` / `NJ_COUNTY_FEEDS` — JSON list pinning a county election id, e.g.
  `[{"county":"Harris","election_id":"123456","date":"2026-11-03"}]`.
- `TN_COUNTY_FEEDS` — JSON list pinning a county's Enhanced Voting export slug, e.g.
  `[{"county":"Shelby","slug":"General11032026","date":"2026-11-03"}]`.

No adapter needs an API key. All of them are plain HTTP GETs; the poller owns the
User-Agent, timeout and retry policy.

## Texas — `tx_sos`

Static JSON SPA. `static/data/version.json` gives the constants version
(`ElectionConstants_<ver>.json`, currently `404`), which maps years to election IDs.
For each election: `election/<id>/version.json` gives the data version, then
`election/<id>/<ver>/{Home,Federal,StateWide,Districted,StateWideQ,Race}.json`.

- `ELECTIONS`: `2024-11-05` pinned to id 49664 / version 1012; `2026-11-03` is `None` and
  resolved at runtime (constants currently stop at 2024, so TX reports itself
  unavailable with the URL to re-check).
- RaceKey: `U. S. SENATOR` -> `TX SEN`, `U. S. REPRESENTATIVE DISTRICT n` -> `TX-nn`,
  statewide executives -> `TX GOV/LTGOV/AG/COMP/AGCOM/LANDCOM/RRC`, `STATE SENATOR,
  DISTRICT n` -> `TX SD-nn`, `STATE REPRESENTATIVE DISTRICT n` -> `TX HD-nn` (both with
  `special` for `- UNEXPIRED TERM`), everything else -> `TX LOCAL <slug>`.
- Precincts and last-updated time come from `Home.json` and are applied to every race.
  Texas publishes no winner flags in these files.

## Georgia — `ga_sos`

One export per general election: `cdn/results/Georgia/export-<YYYY>NovGen.json`.
`ELECTIONS` maps 2020/2022/2024 to their slugs and `2026-11-03` to `2026NovGen`; the
2026 file is not published yet (HTTP 404), and the adapter says so rather than returning
partial data.

- `results.ballotItems[].ballotOptions[]` carry `name`, `politicalParty` and `voteCount`.
  Candidate names look like `David A. Perdue (I) (Rep)`; the party suffix and the
  incumbent marker are stripped (`party` comes from `politicalParty`).
- `US Senate (Perdue)` -> `GA SEN`, `US Senate (Loeffler) - Special` -> `GA SEN S`,
  `US House District n` -> `GA-nn`, `State Senate District n` -> `GA SD-nn`,
  `State House District n` -> `GA HD-nn`. The bilingual variant
  `State Senate Dist 5/Senador Estatal Dist 5` is reduced to its English half.
- The export has no precinct counts and no winner flags. Ballot measures arrive with
  `YES`/`NO` choices and become LOCAL races.

## North Carolina — `nc_sbe`

File-based ENR. `enr/elections.txt` lists elections and `enr/<YYYYMMDD>/data/` holds
`contest.txt`, `results_0.txt` and `county.txt` (all JSON arrays without the outer
brackets, one object per line).

- Results rows are one per candidate: `cid` 0 = statewide, `cnm` contest name (often with
  `(VOTE FOR 1)`), `bnm` candidate, `pty`, `vct` votes, `pct` as a 0–1 fraction,
  `prt`/`ptl` precincts reporting/total.
- `county.txt` row `cid` 0 supplies registered voters (`rtl`), turnout (`bpt`) and the
  `imp` timestamp (parsed as America/New_York).
- `US SENATE` -> `NC SEN`, `US HOUSE OF REPRESENTATIVES DISTRICT 01` -> `NC-01`,
  statewide executives -> `NC GOV/LTGOV/AG/AUD/AGCOM/INSCOM/SOS/TREAS/SUPT`,
  `NC STATE SENATE DISTRICT nn` -> `NC SD-nn`, `NC HOUSE OF REPRESENTATIVES DISTRICT nnn`
  -> `NC HD-nn`, everything else (including constitutional amendments) -> LOCAL.

## Pennsylvania — `pa_dos`

Angular app over `/api/ElectionReturn/`. Responses are JSON strings containing JSON, so
`_decode()` may parse twice. `GetLatestElection` gives the current election,
`GetOfficeData` one office at a time; `isactive` is tried as 1 then 0.

- Office ids: 1 President, 2 US Senate, 3 Governor, 5 Attorney General, 6 Auditor
  General, 7 State Treasurer, 11 US House, 12 state Senate, 13 state House.
- Districts come from the group label (`1st Congressional District`, `121st Legislative
  District`) or `DistrictId`.
- `ELECTIONS`: `2024-11-05` = id 105, `2026-11-03` = id 117 (from `GetLatestElection` on
  2026-10-27). Discovery by date is implemented for future ids.
- The API publishes no precinct counts, no winner flags and an empty
  `GetUpdatedTimeStamp` for archived elections, so those fields stay `None`. County
  breakdowns (`GetCountyBreak`, `GetCountyData`) need one request per county and are not
  polled.

## Clarity / Scytl — `clarity`

Shared vendor feed used by many counties. Per feed: `current_ver.txt` (data version),
then `/<version>/json/en/summary.json` and `electionsettings.json`. summary.json is
usually a JSON array of contests; `{"Contests": [...]} ` is also accepted.

- Contest fields: `C` name, `CH` choices, `P` party per choice, `V` votes, `PCT`
  percentages, `W` winner flags, `TP`/`PR` precincts total/reporting, `T` total votes.
  `electionsettings.websiteupdatedat` (e.g. `10/26/2026 11:42:44 AM CDT`) becomes
  `source_updated_at` with the zone abbreviation mapped to an IANA zone.
- Names cover both abbreviations and long forms: `U.S. SENATOR` -> `SEN`,
  `REP. IN CONGRESS D16` / `U.S. HOUSE OF REPRESENTATIVES, 1st Congressional District`
  -> `HOUSE`, `GOV. AND LT. GOV` -> `GOV`, `ATT. GEN` -> `AG`, `SEC. OF STATE` -> `SOS`,
  `COMPTROLLER` -> `COMP`, `TREASURER` -> `TREAS`, `STATE SENATOR D44` -> `SD`,
  `REP. IN THE G.A. D87` -> `HD`; everything else becomes LOCAL.
- Primary feeds split an office into `... - REP` / `... - DEM` contests; those are
  skipped because the bot tracks the general election.
- Built-in feed: IL/McLean 2026 general (`127349`); a county feed only supplies that county's
  local races, never the statewide IL SEN/GOV/House totals. Add others with `CLARITY_FEEDS`.

## Adding a state

1. Find the feed for the 2024 general and the 2026 primary; confirm the 2026 general URL
   pattern.
2. Write `electionwatch/sources/results/<st>_<agency>.py`: a `race_key()` mapper, a pure
   `parse_*(...)`, an `ELECTIONS` dict keyed by ISO date (fill `2026-11-03` when the id is
   published, otherwise discover it), `fetch()` and `list_races()`, decorated with
   `@register`.
3. Save a trimmed real response under `tests/fixtures/<adapter>/` with a README.txt
   (URLs + fetch date) and add `tests/test_results_<st>.py`.
4. Add the row to the table above.

Run the adapter tests with:

    python -m pytest -q tests/test_results_*.py
