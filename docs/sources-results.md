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
| AZ, MI, WI, NV, OH, FL, NH, ME, IA, NE | — | — | not implemented in this pass; no verified feed format yet |

"Verified fixture" means `parse_*` is tested against a trimmed real response saved under
`tests/fixtures/<adapter>/` (each README.txt records the URL and fetch date). Nothing in
the tests touches the network.

## Environment

- `ELECTION_DATE` — ISO date of the tracked election, default `2026-11-03`
  (`electionwatch/sources/results/_common.py`). Every adapter uses it to pick the election.
- `CLARITY_FEEDS` — JSON list overriding the built-in Clarity feeds, e.g.
  `[{"state":"WI","jurisdiction":"Dane","election_id":"123456"}]`. Entries need `state`
  and `election_id`; `jurisdiction` is optional (omit for a statewide feed) and `date`
  limits an entry to one election date. Without a matching feed for the requested state,
  the Clarity adapter raises `SourceUnavailable` instead of guessing URLs.

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
- Built-in feed: IL/McLean 2026 general (`127349`). Add others with `CLARITY_FEEDS`.

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

Run the package tests with:

    python -m pytest -q tests/test_results_*.py
