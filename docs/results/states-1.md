# Results adapters: MI, WI, NV, OH, NH, ME, IA, NE, MN, VA and CO

Each adapter registers in `electionwatch.sources.base.REGISTRY` under `results`, exposes a pure
`parse_*(payload, ..., fetched_at=...)` and is tested against a trimmed real response in
`tests/fixtures/<adapter>/<st>/` (each README.txt records the URLs and the fetch date).
Tests never touch the network.

| State | Adapter    | Feed                                                                     | Status                                                                                                                                                                       |
| ----- | ---------- | ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OH    | `oh_sos`   | https://liveresults.ohiosos.gov/Api/v1/Contest/                          | **verified fixture** — 2026 general (pre-election, 0 votes); contest ids published                                                                                           |
| VA    | `va_elect` | https://enr.elections.virginia.gov/results/public/api/elections/virginia | **verified fixture** — 2025 general and 2026 general; election discovered from the listing                                                                                   |
| NE    | `ne_sos`   | https://electionresults.nebraska.gov/resultsSW.aspx?type=SW\|CG\|LD\|... | **verified fixture** — 2026 general (pre-election, 0 votes); HTML scraper, opt-in                                                                                            |
| CO    | `co_sos`   | https://results.enr.clarityelections.com/CO/                             | **verified fixture** — 2024 general; 2026 id not published yet, discovery implemented                                                                                        |
| IA    | `ia_sos`   | https://results.enr.clarityelections.com/IA/                             | **verified fixture** — 2024 general; 2026 id not published yet, discovery implemented                                                                                        |
| MI    | —          | https://mielections.us/election/results/                                 | **not possible** — unreachable: every request times out after 45 s (curl exit 28) on both schemes; the SOS's mvic.sos.state.mi.us answers 403 with a Cloudflare interstitial |
| WI    | —          | https://elections.wi.gov/                                                | **not possible** — Cloudflare interstitial (403 "Just a moment...") for automated clients, and the WEC publishes county-reported results, not a statewide JSON export        |
| NV    | —          | https://silverstateelection.nv.gov/                                      | **not possible** — Imperva Incapsula bot challenge: HTTP 200 with a 212-byte body, `x-iinfo` + `incap_ses_*` cookies, no result markup                                       |
| NH    | —          | https://www.sos.nh.gov/                                                  | **not possible** — 403 "Access Denied" (CloudFront WAF) for a descriptive UA and for a browser UA alike                                                                      |
| ME    | —          | https://www.maine.gov/sos/elections-voting/election-results-data         | **not possible** — the page only links per-contest Excel workbooks and RCV PDFs published after the count; no live feed                                                      |
| MN    | —          | https://electionresults.sos.mn.gov/                                      | **not possible** — Radware Bot Manager challenge (`__uzdbm_*` / `SSJSConnectorObj`), no markup served                                                                        |

Checked 2026-10-27 with a descriptive User-Agent
(`Mozilla/5.0 (compatible; election-watch-discord/0.1; +https://github.com/election-watch-discord)`).
No bot challenge, captcha or login was bypassed, faked or replayed.

---

## Ohio — `oh_sos`

The live-results site is an SPA over a JSON API:

```text
GET https://liveresults.ohiosos.gov/Api/v1/Contest/getContestList
GET https://liveresults.ohiosos.gov/Api/v1/Contest/GetContestDetailsObject
        ?contestName=<parent contest>&contestObjectId=<grouping>&resultsLevel=&countyId=
```

`getContestList` returns parent contests (`Governor and Lieutenant Governor`,
`Representative to Congress`, `Statewide Issues`, ...) each with `contestGroupings`
(`displayName`, `contestName`, `contestObjectId`). One details call per grouping returns
`contestList`, where a district contest is split into one row per county, so `parse_oh`
sums rows per candidate and per precinct count.

- Keys: `Governor and Lieutenant Governor` -> `OH GOV`, `Attorney General` -> `OH AG`,
  `Secretary of State` -> `OH SOS`, `Treasurer of State` -> `OH TREAS`,
  `Auditor of State` -> `OH AUD`, `U.S. Senator` -> `OH SEN`, `Representative to
Congress - District nn` -> `OH-nn`, `State Senator - District nn` -> `OH SD-nn`,
  `State Representative - District nn` -> `OH HD-nn`; `Statewide Issues` and
  `Justice of the Supreme Court` become LOCAL slugs.
- Names are split on `" - "` before matching, so `U.S. Senator - Unexpired Term Ending
01/03/2029` (the 2026 race) still maps to `OH SEN`, flagged special.
- Party codes are the site's own (`DEM`/`REP`/`LIB`/`WRI`, `OP` for "other party").
- No `ELECTIONS` map: the site serves one election at a time and the 2026 general is
  already live.
- The API rejects generic client User-Agents with HTTP 403 (a bare `python-httpx` UA gets
  the 1.2 MB SPA shell back), so it relies on the bot's client UA
  (`election-watch-discord/0.1 ...`), which is accepted.

---

## Virginia — `va_elect`

```text
https://enr.elections.virginia.gov/results/public/api/elections/virginia
.../api/elections/virginia/<publicElectionId>/data
```

The listing gives `publicElectionId`, `electionDate` and a localized `name`; the data
endpoint returns `ballotItems` (`name`, `contestType`, `voteTotal`, `reportingStatus` with
`reportingUnits`/`totalUnits`/`asOf`, `summaryResults.ballotOptions`). Election ids are
irregular (`2024NovemberGeneral`, `2026-November-General`), so the id is discovered by
date from the listing rather than constructed; `ELECTIONS` only pins verified ids.

- Keys: `Governor` -> `VA GOV`, `Lieutenant Governor` -> `VA LTGOV`, `Attorney General`
  -> `VA AG`, `Member, House of Delegates (nnth District)` -> `VA HD-nn`, `Member, U.S.
House of Representatives (nnth District)` -> `VA-nn`, `U.S. Senate` -> `VA SEN`; local
  offices, constitutional amendments and ballot questions -> LOCAL slugs.
- `reportingStatus.asOf` becomes `source_updated_at`; the authoritative timestamp lives in
  a `statistics` block that is not polled.

---

## Nebraska — `ne_sos`

No JSON feed: results are server-rendered HTML, one page per contest family
(`resultsSW.aspx?type=SW|CG|LD|SE|CC|...&map=CTY|DIST`), one
`div.wrapper-inside.wrapper-border` block per contest, one `div.section.group` per
candidate. `scrape = True` and `enabled_by_default = False` (AGENTS.md: scrapers are
opt-in), and `fetch()` pulls only the families a watched race can live on — `SW` for the
US Senate and statewide executive offices, `CG` for US House, `LD` for the unicameral
Legislature, and the local families only when a LOCAL race is watched.

- Keys: US Senate -> `NE SEN`; Governor and the five statewide executives -> `NE GOV`,
  `NE SOS`, `NE TREAS`, `NE AG`, `NE AUD`; `Representative in Congress ... District nn`
  -> `NE-nn`; `Member of the Legislature - District nn` -> `NE SD-nn` (non-partisan,
  unicameral); everything else (State Board of Education, regents, judges, county boards,
  NRDs, PPDs) -> LOCAL slugs.
- `Precincts Fully: n / m` becomes `precincts_reporting`/`precincts_total`; partially
  reported precincts are deliberately not counted as reporting.
- The pages carry no last-updated stamp, so `source_updated_at` stays `None`.

---

## Colorado and Iowa — `co_sos`, `ia_sos`

Both publish through Clarity ENR, so the transport matches `clarity.py`
(`current_ver.txt`, then `<version>/json/en/summary.json`, plus `electionsettings.json`
for `websiteupdatedat`), but the contest names miss the vendor adapter's patterns:

```text
CO: Representative to the 119th United States Congress - District 1
CO: State Senator - District 2          CO: State Representative - District 1
IA: United States Representative District 1
IA: State Senator District 2            IA: State Representative District 1
```

Adding the two states to `clarity.ELECTIONS` alone would file those races as LOCAL. Each state therefore
has its own small module and `race_key()` mapper. Both reuse `clarity.parse_settings` for
`source_updated_at` and discover the election id from `<ST>/elections.json` when the date is
not pinned.

- `co_sos`: `ELECTIONS["2024-11-05"] = "122598"`; regents, State Board of Education,
  district attorneys, judicial retention and amendments/propositions -> LOCAL slugs.
- `ia_sos`: `ELECTIONS["2024-11-05"] = "122322"`; `Secretary of Agriculture` ->
  `IA AGCOM`; judges, constitutional amendments and school/city measures -> LOCAL slugs.
- The 2026 general is not published for either state yet; the date-matched discovery in
  `<ST>/elections.json` is the path it will take, and is covered by tests using the 2025
  elections that are published today.

Run the adapter tests:

```bash
pytest -q tests/test_results_*.py
```

---

## Live smoke check (2026-10-27)

`fetch()` was run against the live sites with the bot's own User-Agent, outside the test
suite:

| Adapter    | Election     | Result                                                                                                                            |
| ---------- | ------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| `oh_sos`   | 2026 general | `OH GOV` 0 votes, 8834 precincts; `OH-01` 0 votes, 784 precincts; State Issue 3 YES/NO (all 0 — counting has not started)         |
| `va_elect` | 2026 general | `VA-07` (Vindman DEM / Ollivant REP, 0 votes); Virginia has no 2026 governor or House of Delegates race, so those keys are absent |
| `ne_sos`   | 2026 general | `NE SEN`, `NE GOV`, `NE-02`, 0 votes, 1322 / 334 precincts                                                                        |
| `co_sos`   | 2024 general | `CO-01` DeGette 264,606 (76.56%), 3/3 precincts; `CO SD-02` Frizell 63,181                                                        |
| `ia_sos`   | 2024 general | `IA-01` Miller-Meeks 206,955 (49.98%); `IA HD-01` Scholten 4,615, 1/1 precincts                                                   |

---

## See also

- [../sources-results.md](../sources-results.md): results adapters at a glance
- [../state-coverage-plan.md](../state-coverage-plan.md): election-night coverage plan
