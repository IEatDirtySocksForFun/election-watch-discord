# Results adapters: AK, AL, AR, ID, IL, IN and more

Each section lists the adapter, its feed and how it was verified. "Verified fixture" means
`tests/test_results_<st>.py` parses a trimmed real response. The fixture's README records the
source URL and fetch date. Some adapters were also checked against the live feeds.

Fixtures sit directly in `tests/fixtures/<adapter>/` because each of these adapters serves one
state.

---

## Summary

| State | Adapter   | Feed                                                                  | Status                                                                     |
| ----- | --------- | --------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| AK    | `ak_doe`  | Division of Elections precinct CSV                                    | verified fixture; live fetch blocked by the origin's TLS chain             |
| AL    | —         | no usable statewide feed                                              | not possible (see below)                                                   |
| AR    | `ar_sos`  | SOS election page → Tally-ENR `FullDataFile.json`                     | verified fixture (2024); 2026 UUID format has no contest names             |
| ID    | `id_sos`  | SOS results archive pages (embedded reactable JSON)                   | verified fixture + live run (2024 general)                                 |
| IL    | `il_sbe`  | SBE `ElectionVoteTotals.aspx` HTML                                    | verified fixture + live run (2026 primary); 2026 general not published yet |
| IN    | `in_sos`  | `enr.indianavoters.in.gov/archive/<election>/data/*.json`             | verified fixture + live run (2024 general)                                 |
| CA    | `ca_sos`  | `media.sos.ca.gov/media/<office>.json`                                | verified fixture                                                           |
| CT    | `ct_sots` | `ct.elstats.civera.com` search CSV                                    | verified fixture                                                           |
| DC    | `dc_boe`  | `electionresults.dcboe.org` XML export                                | verified fixture                                                           |
| DE    | `de_doe`  | `elections.delaware.gov/reports/GE<year>.csv`                         | verified fixture                                                           |
| HI    | `hi_soe`  | `files.hawaii.gov/elections/files/results/<year>/General/summary.txt` | verified fixture                                                           |

---

## AK — `electionwatch/sources/results/ak_doe.py`

- Feed: <https://www.elections.alaska.gov/results/24GENR/ENRbyPrecinct.csv> (one row
  per candidate per precinct, `Contest_title`, `candidate_name`, `Party_Code`,
  `total_votes`, `Pct_Id`, `Reporting_flag`, `Candidate_Type`). Summing the rows of
  a contest reproduces the Division's published statewide totals (checked for the
  2024 U.S. House race: Begich 159,550 / Peltola 152,828 / Howe 13,010 / Hafner 3,417).
- Fixture: six contests × the first eight precincts of `24GENR`.
- Status: verified fixture. The data is the RCV _first-choice_ count; the final
  round is in the Election Summary Report PDF, which the adapter does not read.
- Known issue (checked 2026-10-27): `elections.alaska.gov` serves an incomplete TLS
  chain (leaf issued by "GlobalSign GCC R46 OV TLS CA 2025", but the server sends the
  older "GlobalSign RSA OV SSL CA 2018" intermediate), so httpx verification fails
  with "unable to verify the first certificate". Browsers hide this with AIA
  fetching. Nothing in the adapter disables verification; the fetch starts working
  when the Division fixes its chain or the deployment trusts the missing intermediate.

---

## AL — not possible

- The unofficial-results app is
  <https://www2.alabamavotes.gov/electionnight/statewideResultsByContest.aspx?ecode=...>
  Its pages are a Drupal shell for **every** `ecode` tried (42,277 bytes, identical),
  its theme script `.../themes/custom/sos/js/main.js` is 0 bytes, and no JSON/AJAX
  endpoint could be found, so no results are reachable without executing the page's
  own JS (which is gone). `www2.sos.alabama.gov`, the older results host, no longer
  resolves; `results.enr.clarityelections.com/AL/` returns 404, so Alabama is not on
  the Clarity/Scytl ENR platform.
- The only machine-readable statewide artifact is a post-certification download,
  "2024-General Precinct Level Results.zip"
  (<https://www.sos.alabama.gov/sites/default/files/election-data/2024-12/2024-General%20Precinct%20Level%20Results.zip>):
  67 per-county legacy **.xls** (BIFF) files. Reading those needs `xlrd`, which the bot does
  not include, and the data is a certified snapshot rather than a feed usable on election
  night.
- Revisit if the SOS republishes a working results app or a CSV/JSON export.

---

## AR — `electionwatch/sources/results/ar_sos.py`

- Discovery: <https://www.sos.arkansas.gov/elections/research/election-results> lists
  every election as `https://arkansas.tally-enr.com/#election=<id>`; the adapter
  matches the tracked election's label ("2024 General Election", "2026 Preferential
  Primary", ...) and reads the id from the link. Labels per election day are in
  `ELECTIONS`; the page order is newest-first.
- Feed: `https://arkansas.tally-enr.com/results/arkansas/<id>/FullDataFile.json`
  (`ElectionInfo`, `Turnout`, `ContestData[]` with `ContestName`, `TotalVotes`,
  `PrecinctsReporting`/`TotalPrecincts`, `Candidates[]` with `Name`, `PartyName`,
  `TotalVotes`). The 2024 general file is 15.7 MB.
- Fixture: five contests from the 2024 file (President, US House 01, state Senate 01,
  state House 01, one Justice of the Peace race) plus the SOS link list and a sample
  of the newer format.
- Status: verified fixture, and a live fetch for 2024 returned the expected races.
- Caveat: elections from 2026 on are addressed by UUID and use a shape
  (`ElectionData` / `TurnoutData` / `ContestData[].ContestID` + `Choices`) that
  carries **no contest names**; the app resolves names through an authenticated API,
  so those files cannot be mapped to race keys. `parse_ar` raises `SourceUnavailable`
  explaining this instead of guessing. Dated TODO: re-check the 2026 feed once the
  general election is published.

---

## ID — `electionwatch/sources/results/id_sos.py`

- The live app (`results.voteidaho.gov`) is an Angular SPA whose API is
  per-jurisdiction (one per county plus a statewide `id` slug); the election document
  returns contest groups but not vote totals, and the bundle exposes no endpoint that
  returns the totals, so the app was not used.
- Feed: the SOS archive, `https://archive.voteidaho.gov/results/<year>/<kind>/`
  (`general` in November, `primary` in May), which carries the same numbers as
  reactable widgets: each contest is an `<h4>` heading followed by a
  `<script type="application/json">` block with `ContestantName`, `TotalVotes`,
  `Percent`. Party is in the candidate name's parentheses ("Russ Fulcher (REP)").
- Pages polled: `congressional.html` for US House, `legislative.html` for the
  legislature, and any other page the index lists when a statewide office is asked
  for; `county`/`measures`/`turnout`/`xml`/`close-races` are skipped. `fetch()` only
  downloads the pages the requested races live on (congressional ≈ 8 MB,
  legislative ≈ 38 MB — the archive is a finished-results snapshot, not a live feed).
- Idaho House districts elect two members from separate positions, so those races use
  district codes `01A` / `01B` (`ID HD-01A`).
- Fixture: the archive index plus the District 1 sections of the congressional and
  legislative pages.
- Status: verified fixture; a live run for 2024 general returned `ID-01` (Fulcher
  466,232) and `ID SD-01` (Woodward 30,859).

---

## IL — `electionwatch/sources/results/il_sbe.py`

- Feed: <https://www.elections.il.gov/electionoperations/ElectionVoteTotals.aspx>
  (server-rendered ASP.NET; `scrape = True`, official state site). The default page is
  the newest election's "Federal / Statewide" category (US Senate, Governor and the
  other statewide executives, every US House district as "1ST CONGRESS" ...); the
  category links on that page add the state Senate ("2ND SENATE" ...) and state House
  ("1ST REPRESENTATIVE" ...) pages.
- The percent column is "Percent of Party Vote" in a primary, so it is ignored; the
  share is computed from the votes.
- Discovery: the page defaults to the newest election and its election dropdown lists
  every election. The URL id of an _older_ election is an opaque ASP.NET token, so the
  adapter checks the selected election's name against `ELECTIONS` and reports itself
  unavailable when the tracked election is not the one on screen (2026 general is not
  published yet, checked 2026-10-27). `IL_ELECTION_ID` pins the opaque id when a past
  election has to be read.
- Fixture: 7 office blocks of the 2026 primary "Federal / Statewide" page plus the
  first two blocks of the "Senate" and "District All" pages.
- Status: verified fixture; a live run for 2026-03-17 returned `IL SEN`, `IL-01`,
  `IL SD-02` and `IL HD-01`.

---

## IN — `electionwatch/sources/results/in_sos.py`

- Feed: static Angular data files under `archive/2024General/data/` (no directory
  listing). https://enr.indianavoters.in.gov/archive/2024General/data/settings.json gives
  `VersionType` (e.g. `A`); `statewideElectionsC_A.json` holds the office categories and
  there is one `OffCatC_<category>_A.json` per category (`Root.StatewideSummary.Race` with
  `OFFICE_TITLE`, `Candidates.Candidate`, `VOTES`, `PARTY_NAME`).
- Categories read: 1006 US Senator, 1005 US Representative, 1007 Governor,
  1018 State Senator, 1039 State Representative.
- Status: verified fixture; a live run for 2024 returned `IN SEN` (Jim Banks).
- `source_updated_at` is the JSON file's own `WriteTime` (a build stamp), not a
  live update clock.

---

## See also

- [../sources-results.md](../sources-results.md): results adapters at a glance
- [../state-coverage-plan.md](../state-coverage-plan.md): election-night coverage plan
