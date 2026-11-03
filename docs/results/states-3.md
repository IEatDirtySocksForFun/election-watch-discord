# Results adapters: KS, KY, LA, MA, MD, MO, MS, MT, ND, NJ and NM

Every adapter below registers itself through `@register` in
`electionwatch.sources.results.<module>`.

Seven states have an implemented adapter (KS, LA, MD, MS, MT, ND and NM). The other four have
no usable statewide adapter; the notes below explain why.

- [Summary](#summary)
- [Notes per state](#notes-per-state)
- [Not possible / not implemented](#not-possible--not-implemented)
- [Tests](#tests)
- [See also](#see-also)

---

## Summary

| State | Adapter        | Feed                                                                                                                          | Status                                                                                                                                                          |
| ----- | -------------- | ----------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| KS    | `ks_sos` (new) | `https://www.sos.ks.gov/ent/{national,statewide,ks-house,ks-boe,judicial,constitutional}.html`                                | HTML parser, `scrape=True`, enabled. Verified against the live 2026 primary pages (2024 general HTML is not published).                                         |
| KY    | —              | `https://vrsws.sos.ky.gov/liveresults/`                                                                                       | Not possible: HTTP 403 "KY SBE Acceptable Use Policy" access gate.                                                                                              |
| LA    | `la_sos` (new) | `https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob=<YYYYMMDD>/{RacesCandidates,Votes}_Multiparish.htm` | Verified fixture (2024 general with counts, 2026 general race list).                                                                                            |
| MA    | —              | `https://electionstats.state.ma.us/` (PD43+)                                                                                  | Not implemented: no election-night feed at all; certified per-election CSVs only. See below.                                                                    |
| MD    | `md_sbe` (new) | `https://elections.maryland.gov/elections/archive/<YYYY>/election_data/PG<YY>_{Congressional,Legislative}BreakDown.csv`       | Verified fixture (2024 general). Certified files, published after the count.                                                                                    |
| MO    | —              | `https://enr.sos.mo.gov/`                                                                                                     | Not possible: Cloudflare bot challenge (`cf-mitigated: challenge`).                                                                                             |
| MS    | `ms_sos` (new) | `https://www.sos.ms.gov/elections/electionresults/<YYYY>ElectionRecapSheets.csv`                                              | Verified fixture (2024 general). Certified recap, published after the count.                                                                                    |
| MT    | `mt_sos`       | `electionresults.mt.gov/resultsSW.aspx`                                                                                       | Statewide adapter added through the alternate endpoint in [alt-c.md](alt-c.md); the main site is Cloudflare-blocked.                                            |
| ND    | `nd_enr` (new) | `https://api.resultsnd.sos.nd.gov/Contest/GetContestResults?cId=north-dakota&electionID=348&contestType={SW,LEG}`             | JSON API, enabled. Verified against the live 2026 general (0 of 358 precincts, 0 votes) and the finished 2026 primary count. `nd_sos` (2024 archive) is opt-in. |
| NJ    | —              | `https://www.nj.gov/state/elections/election-results.shtml`                                                                   | Not possible: PDF only (statewide and per-county PDFs), no CSV/JSON/XLSX.                                                                                       |
| NM    | `nm_sos` (new) | `https://electionresults.sos.nm.gov/resultsSW.aspx?text=All&type={FED,LGX}&map=CTY`                                           | HTML parser, `scrape=True`, enabled. Structure verified on the live 2026 general pages (0 of 2204 precincts at capture); no archived 2024 page exists.          |

All adapters map US Senate → `XX SEN`, US House → `XX-DD` (at-large → `XX-AL`), Governor and
statewide executive offices → `XX GOV`, `XX AG`, ... , state legislature → `XX SD-DD` /
`XX HD-DD`, and everything else (ballot measures, judges, school boards, county races) to
`XX LOCAL <slug>` with a stable slug.

---

## Notes per state

### KS — `ks_sos`

- One server-rendered page per race category; the category list is read from the page
  navigation (county pages and the "favourites" page are skipped), so a renamed tab does not
  need a code change.
- The site serves **one election at a time**; `ELECTION_DATE` cannot select an election. On
  2026-10-27 the site showed the 2026-08-04 primary, and on 2026-11-02 it still did, ballot
  questions with real counts included. A page whose "Last Update" is 45 days or more before
  `ELECTION_DATE` (`STALE_DAYS`) belongs to an earlier election and is skipped; when every page
  is skipped the adapter reports the results as not published yet. A page without the stamp
  is read as before.
- Primary pages split each office into `(D)` / `(R)` halves; those are skipped exactly like
  the Clarity feeds. General-election headings carry no party suffix and are parsed
  normally (`tests/test_results_ks.py::test_general_election_headings_parse` runs the real
  primary blocks with the suffix removed).
- Candidates have no party on the page (the party is the contest suffix), so `party` is
  `None` for general-election contests. "Last Update" (`#time-stamp`, America/Chicago) is
  converted to UTC.

### LA — `la_sos`

- The "Graphical Election Results" app is a keyless JSON feed; `.htm` blob names return
  JSON. `ElectionDates.htm` maps the tracked date to its `YYYYMMDD` folder, so 2026-11-03
  resolves without a hard-coded id.
- `Votes_Multiparish.htm` carries precincts, registered voters and the `Outcome`
  ("Elected") flag; `RacesCandidates_Multiparish.htm` carries titles and candidates.
- Party comes from a `(REP)`-style suffix, or, for presidential electors, from the trailing
  party name (`... Republican`). `NOPTY` → `NPA`, `LBT` → `LIB`.
- The media API (`/api/MediaRequests/...?access=...&secret=...`) needs per-outlet
  credentials and is not used.

### MD — `md_sbe`

- Two breakdown CSVs per election; rows with `County = 00` are the statewide contests, the
  rest are county rows (including the legislative districts). The trailing
  `Congressional District N` / `Legislative District NX` columns are a geographic
  partition, so summing a row's columns gives that contest's total, and for a district
  contest the total of its own district.
- Published with the certified results, **not** on election night; there is no JSON feed
  and the election-night HTML pages could not be located on the current site (the old
  `.../results/gen_results/index.html` paths now return the 404 page).
- The 2026 files (`PG26_*`) are not published yet; the adapter discovers the files by following
  the `*BreakDown.csv` links on `/elections/archive/2026/election_data`.
- Maryland held no state legislative election in 2024, so the legislative-district path is
  unit-tested via `race_key` but not against a fixture row.

### MS — `ms_sos`

- One CSV of counties' recap sheets; `State Total` is the statewide figure repeated on every
  county row, so the parser uses it rather than summing counties.
- Presidential slates arrive as elector prose and are reduced to `President / Vice
President`.
- Certified recapitulation, published after the count; no precinct counts, no winner
  column.
- Discovery, not a hard-coded file name: the adapter follows the `*ElectionRecapSheets.csv`
  link on `/elections/electionresults_aspx/elections_results_<YYYY>_general.aspx`. That 2026
  page is not published yet (HTTP 404 on 2026-10-27), so `fetch()` raises `SourceUnavailable`
  until it appears.

### ND — `nd_enr` (2026 general)

- For the 2026 general election the state moved the live count to a new Election Night
  Reporting site, linked from `sos.nd.gov/elections/election-results`. It is a single-page
  app over a JSON API on `api.resultsnd.sos.nd.gov`; the adapter reads that API:
  `Election/GetElectionList?cid=north-dakota` (election ids), `Contest/GetContestSearchList`
  (contest names, parties, write-in choices) and `Contest/GetContestResults` once per contest
  type (`SW` statewide, `LEG` legislative districts).
- The results payload carries choice ids only, so the two payloads are joined on
  `contestID` / `choiceID`. `lastUpdated` (UTC, seven fractional digits) becomes
  `source_updated_at`; the human page is `resultsnd.sos.nd.gov/#election=<id>`.
- Winner comes from `isWinner`; precincts from `precinctsReporting` / `totalPrecincts`. Before
  the count starts every contest answers 0 votes and 0 of 358 precincts, which is the state of
  the general fixture. At-large House, row offices, legislative districts (including
  `State Representative Unexpired 2-Year Term District 20` → `ND HD-20 S`) and measures are
  mapped as before.
- Election id `348` is the 2026 general and is pinned in `ELECTIONS`; any other date is
  discovered from `GetElectionList` by date.
- Party ids are `REP`, `DEM`, `IND`, `LIB`, `GRE`, `CON`, `NON`, `OTH`; a measure's Yes/No
  choices carry no party.
- The old ASP.NET pages (`nd_sos`, `results.sos.nd.gov/resultsSW.aspx`) still serve the 2024
  general election and are now an archive: `enabled_by_default = False`, and `fetch()` reads
  the page heading and raises `SourceUnavailable` when it names another election than
  `ELECTION_DATE`, so a stale count is never published as the current one. With
  `ELECTION_DATE=2024-11-05` it still parses the archive.
- `nd_sos` also skips any page whose hidden `hidElectionDate` field names another date than
  `ELECTION_DATE` (added for the same 2024 pages before `nd_enr` existed).

### NM — `nm_sos`

- Same ASP.NET family, NM markup: candidate names in `<h1>`, party in `<h2>`, votes in
  `div.display-results-box-f`, no percentages and **no winner marker**.
- `type=FED` + `type=LGX` (legislative). The 2026 general pages were live with
  "Precincts Fully: 0 / 2204" at capture, so the format is verified but non-zero counts
  have not been observed through this adapter yet.
- No archived 2024 page in this markup (`Results.aspx?ID=...` returns an empty shell).

---

## Not possible / not implemented

### KY — blocked by an access gate

`https://vrsws.sos.ky.gov/liveresults/` and `/liveresults/Default.aspx` answer
**HTTP 403** with a page titled "KY SBE Acceptable Use Policy" (an IP/acceptable-use
gate in front of the live-results app). The informational page
`https://elect.ky.gov/results/2020-2029/Pages/2024.aspx` is reachable but only links the
gated app and PDFs. Bypassing the gate is out of scope by rule, so no adapter is written.

### MA — no election-night feed exists

The Secretary of the Commonwealth states that Massachusetts does not publish results on
election night; PD43+ (`https://electionstats.state.ma.us/`) publishes **certified**
results only. It does offer clean per-election CSVs, verified for 2024:

```text
GET https://electionstats.state.ma.us/elections/download/165300/
    -> Content-Disposition: attachment; filename=PD43+__2024_President_General_Election.csv
City/Town,,,"Harris/ Walz","Trump/ Vance",...
,,,Democratic,Republican,...
Abington,,,"4,714","4,639",... ,"9,675"
```

Not implemented because the office → election-id mapping needs scraping ~260 contest tables
whose markup carries no office headings (`/elections/search/date:2024-11-05/` is a 900 kB
page of result tables), and because a certified-only feed would report the _previous_
election's numbers on election night. Dated TODO: if a per-office search URL appears, add
`ma_sos` on top of the download endpoint above.

### MO — Cloudflare bot challenge

`https://enr.sos.mo.gov/` returns **HTTP 403** with `cf-mitigated: challenge`,
`server: cloudflare`, `<title>Just a moment...</title>`. No JSON/CSV endpoint is reachable
without solving the challenge, which is out of scope.

### MT — original site blocked

`https://electionresults.mt.gov/` returns **HTTP 403** with
`cf-mitigated: challenge`. The `mt_sos` adapter uses the alternate official endpoint described
in [alt-c.md](alt-c.md) instead.

### NJ — PDF only

`https://www.nj.gov/state/elections/election-results.shtml` links statewide and per-county
result PDFs; the page contains no `.csv`, `.xlsx` or `.json` links. There is no PDF parser,
and the per-county PDFs are re-typeset during the night, so NJ is left unadapted rather than
scraped unreliably.

---

## Tests

```bash
pytest -q \
    tests/test_results_la.py tests/test_results_ms.py tests/test_results_ks.py \
    tests/test_results_nd_enr.py tests/test_results_nd.py tests/test_results_md.py \
    tests/test_results_nm.py
```

Fixtures live in `tests/fixtures/<adapter>/<ST>/` with a `README.txt` recording the URLs,
the capture date and exactly what was trimmed.

---

## See also

- [../sources-results.md](../sources-results.md): results adapters at a glance
- [../state-coverage-plan.md](../state-coverage-plan.md): election-night coverage plan
