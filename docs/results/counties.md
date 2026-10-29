# County feeds — Texas and the states whose statewide site is out of reach

Several states' statewide results sites are behind a bot check or publish nothing the bot can
read. For those, the bot uses official county feeds instead and labels every result
`(partial: N counties)` so no county tally is mistaken for a state total.

County adapters follow the same ground rules:

- plain GET with the bot's own User-Agent (`election-watch-discord/0.1 (open-source election
results bot)`) — no browser User-Agent, no UA rotation, no headless browser;
- no attempt to defeat a WAF challenge: a 403/CloudFront block is recorded here and the county
  is skipped;
- every adapter is a pure `parse_*` function plus a thin `fetch`, with a trimmed real response
  in `tests/fixtures/<adapter>/` (URL + capture date in its `README.txt`) and no network in
  tests.

Checks below were run on **2026-10-27**.

> **Newer additions:** Tarrant, Fort Bend and Bexar are Clarity counties. The election ids are
> linked from the counties' own pages and are now pinned. Dallas and Williamson list the 2026
> elections. Harris publishes its own JSON, now merged by `tx_counties`. Alabama's Madison
> County has its own adapter, `al_counties`. See `docs/state-coverage-plan.md` for the current
> coverage estimates; older statements below describe the initial county set.

- [How the shared pieces fit](#how-the-shared-pieces-fit)
- [Texas — `tx_counties`](#texas--tx_counties)
- [Tennessee — `tn_counties`](#tennessee--tn_counties)
- [States checked, nothing usable with a plain GET](#states-checked-nothing-usable-with-a-plain-get)
- [Re-running these checks](#re-running-these-checks)
- [See also](#see-also)

---

## How the shared pieces fit

| Module                                                           | Role                                                                                                                                                                                                                    |
| ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sources/results/_county.py`                                     | Clarity ENR county helper: `elections.json` discovery, `current_ver.txt`, `summary.json`, `parse_contests`, `merge` (sums the same race across counties, rewrites the title `(partial: N counties)`, recomputes shares) |
| `sources/results/_ev_county.py`                                  | Enhanced Voting county export parser (same JSON shape as Georgia's SOS export)                                                                                                                                          |
| `tx_counties`                                                    | Texas: 17 configured Clarity counties                                                                                                                                                                                   |
| `tn_counties`                                                    | Tennessee: Shelby County Enhanced Voting export                                                                                                                                                                         |
| `nj_counties`, `mi_county`, `wi_dane`, `mo_stlouis`, `ny_nycboe` | pre-existing county adapters, unchanged                                                                                                                                                                                 |

Races every configured county publishes (US Senate, governor, the statewide courts) merge into
one result. A race that only one county publishes keeps its own county title. Purely local
races are namespaced with the county slug (`TX LOCAL galveston-sheriff`,
`TN LOCAL shelby-collierville-mayor`) so two counties' "Sheriff" cannot collide.

---

## Texas — `tx_counties`

Discovery: `https://results.enr.clarityelections.com/TX/<County>/elections.json` lists the
elections a county publishes; the id for the tracked date is picked from it (or pinned in
`ELECTIONS` / `TX_COUNTY_FEEDS`). Results come from
`.../TX/<County>/<EID>/<ver>/json/en/summary.json`.

The 2024 general (ids 1224xx/1228xx) was fetched for every configured county, which is also how
the "2024 share" column was computed: county US Senate votes ÷ the 11,291,854 Senate votes cast
statewide in 2024 (the figure already recorded in `docs/results/alt-b.md`).

| County    | Clarity ids 2024 / 2026 | 2024 Senate votes | Share of 2024 statewide Senate vote | Verified                                         |
| --------- | ----------------------- | ----------------- | ----------------------------------- | ------------------------------------------------ |
| Denton    | 122426 / 127398         | 448,459           | 3.97%                               | fixture (2024 + 2026)                            |
| El Paso   | 122595 / 127348         | 245,124           | 2.17%                               | fixture (2024)                                   |
| Galveston | 122842 / 127478         | 158,465           | 1.40%                               | fixture (2024 + 2026)                            |
| Bell      | 122428 / —              | 129,464           | 1.15%                               | fixture (2024)                                   |
| Comal     | 122856 / —              | 103,324           | 0.92%                               | fixture (2024)                                   |
| Kaufman   | 122824 / 127485         | 68,862            | 0.61%                               | fixture (2026); 2024 total read live, no fixture |
| Rockwall  | 122491 / —              | 62,404            | 0.55%                               | fixture (2024)                                   |
| Midland   | 122496 / —              | 58,210            | 0.52%                               | fixture (2024)                                   |
| Gregg     | 122313 / —              | 46,505            | 0.41%                               | fixture (2024)                                   |
| Bastrop   | 122753 / 127476         | 39,472            | 0.35%                               | fixture (2024)                                   |
| Hood      | 122858 / 127400         | 36,474            | 0.32%                               | fixture (2024)                                   |
| Rusk      | 122760 / 127574         | 21,532            | 0.19%                               | fixture (2024)                                   |
| Lamar     | 122497 / 127484         | 21,120            | 0.19%                               | fixture (2024)                                   |
| Travis    | 122432 / —              | 578,885           | 5.13%                               | fixture (2024)                                   |
| Dallas    | —                       | —                 | —                                   | configured, `elections.json` lists nothing       |
| Tarrant   | —                       | —                 | —                                   | configured, `elections.json` lists nothing       |
| Fort Bend | —                       | —                 | —                                   | configured, no directory on the CDN              |

The fourteen counties that published a Senate contest in 2024 cover **2,018,300 Senate votes =
17.87%** of Texas's 2024 statewide Senate vote; a live fetch of the same fourteen merges to
2,018,216 votes after the 84 rejected write-in ballots are dropped. Thirteen of the fourteen
have a 2024 fixture — Kaufman's 2024 export was read live (only its 2026 export is kept as a
fixture, because the 2026 general is the one tests need).

Largest counties **not** configured — no Clarity directory on the vendor host, or a directory
with no election row for the tracked date: Harris, Bexar, Collin, Hidalgo, Williamson,
Montgomery, Brazoria, Nueces, Cameron, Lubbock, Webb, McLennan, Hays, Smith, Jefferson. Each runs a
separate system (Harris: HarrisVotes; Bexar/Collin/Montgomery: different vendors), which would
need separate adapters, not Clarity.

Feed quirks the mapping absorbs (`race_key` in `tx_counties.py`):

- `United States Representative, District 4` / `US Representative, Dist 16` /
  `U. S. Representative District 25` / `State Representative,  District 73` (double space);
- `Comm General Land Office` for the Land Commissioner, `Pl 2 Unexp` /
  `Place 2 - Unexpired Term` for an unexpired Supreme Court term;
- `Ct of Crim App`, `State BoE, Dist 10`, `Crt of App Dist` style abbreviations, and Hood
  County's own typo `Superme Court`;
- `REP Ted Cruz` party prefixes (Lamar, Rockwall, Rusk, Bastrop) and Travis's parenthesised
  `(R) Ted Cruz`: stripped only when the prefix repeats the party column, so a title such as
  `Rep. John Lewis` is untouched;
- presidential tickets written three ways — `Donald J. Trump/JD Vance` (most),
  `Donald J. Trump JD Vance` (Comal drops the slash) and `Donald J. Trump` (Travis omits the
  running mate). All three are folded onto the presidential name so the per-county tallies add up
  (live 2024: Trump 1,031,964 / Harris 969,950 across 14 counties);
- `Rejected write-ins` and `Unassigned write-ins` tally rows are dropped; a counted write-in
  candidate (`Chad Hagg`, 93 votes in Hood) is kept.

Pin or add a county without touching code:

```bash
TX_COUNTY_FEEDS='[{"county":"Harris","election_id":"123456","date":"2026-11-03"}]'
```

---

## Tennessee — `tn_counties`

| Source                                     | Why not                                                                                      | Status                        |
| ------------------------------------------ | -------------------------------------------------------------------------------------------- | ----------------------------- |
| `sos.tn.gov/elections/results` (statewide) | answers **403** to the bot User-Agent (CloudFront block)                                     | not used; no bypass attempted |
| Davidson County                            | publishes a PDF precinct report, no JSON                                                     | skipped (PDF-only)            |
| Knox County (`elections.knoxcounty.org`)   | single-page app; its result feed needs a per-session token that a plain GET cannot reproduce | skipped                       |

Shelby County (Memphis, the state's largest) publishes a machine-readable export:

`https://app.enhancedvoting.com/cdn/results/shelby-county-tn/export-General11052024.json`

| County | Source                                                                                                            | 2024 verified                                    | 2024 vote share                                                  | Verified              |
| ------ | ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------- | --------------------- |
| Shelby | Enhanced Voting export, 142 precincts, President/Vice + Senate + US House 8/9 + TN Senate/House + municipal races | 328,159 presidential votes; 319,479 Senate votes | n/a — the statewide total could not be verified (sos.tn.gov 403) | fixture: 2024 general |

The 2026 export (`export-General11032026.json`) answered 404 on 2026-10-27; Enhanced Voting
counties publish on election night, so the slug is configured and `tn_counties.fetch` raises
`SourceUnavailable` until it appears. Pin another county or slug with:

```bash
TN_COUNTY_FEEDS='[{"county":"Shelby","slug":"General11032026","date":"2026-11-03"}]'
```

---

## States checked, nothing usable with a plain GET

| State | Counties checked                  | What is published                                                                                                                           | Verdict                                            |
| ----- | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| NV    | Clark, Washoe                     | Clark's `ElectionResults_NV/EnrMain` is an SPA shell with no data in the HTML and no documented JSON endpoint; Washoe publishes PDF reports | skipped                                            |
| KY    | Jefferson, Fayette                | county clerk CMS pages (`jeffersoncountyclerk.org`, `fayettekyclerk.gov`) with PDF/HTML summaries only                                      | skipped (PDF-only)                                 |
| OK    | Oklahoma, Tulsa                   | state app `results.okelections.gov` is an Angular bundle calling `enrapi/GetResults/` with a session token; no plain-GET JSON               | skipped                                            |
| MO    | Jackson, Kansas City board        | PDF reports; St. Louis County's CSV export is already covered by `mo_stlouis`                                                               | skipped                                            |
| MA    | Boston                            | PDF/HTML city reports                                                                                                                       | skipped (PDF-only)                                 |
| NH    | Manchester, Nashua                | PDF city reports                                                                                                                            | skipped (PDF-only)                                 |
| ME    | Portland                          | PDF city reports                                                                                                                            | skipped (PDF-only)                                 |
| AL    | Jefferson, Madison, Mobile        | `alabamavotes.gov` not reachable from the bot host; county sites publish PDFs                                                               | skipped                                            |
| WY    | Laramie, Natrona                  | PDF county canvasses                                                                                                                        | skipped (PDF-only)                                 |
| MI    | Wayne, Kent (beyond Ottawa)       | Wayne runs an ES&S `ResultsSW.aspx` app; Kent's results host answers 403                                                                    | see `mi_county` (Oakland, Macomb, Genesee, Ottawa) |
| WI    | Milwaukee, Waukesha (beyond Dane) | Milwaukee answers 403; Waukesha publishes PDFs                                                                                              | see `wi_dane`                                      |

A county is only added to an adapter when a plain GET with the bot User-Agent returns the
results document; PDFs would need a PDF parser dependency (`AGENTS.md`: no new dependencies
without saying why) and the states above are better served by a statewide adapter if a state site ever
exposes one.

---

## Re-running these checks

```bash
UA='election-watch-discord/0.1 (open-source election results bot)'

# Texas: does a county publish anything?
curl -s --compressed -A "$UA" "https://results.enr.clarityelections.com/TX/Galveston/elections.json"

# Texas: one county's 2024 general results
curl -s --compressed -A "$UA" \
  "https://results.enr.clarityelections.com/TX/Galveston/122842/356002/json/en/summary.json"

# Tennessee: Shelby's 2024 export (2.2 MB)
curl -s --compressed -A "$UA" \
  "https://app.enhancedvoting.com/cdn/results/shelby-county-tn/export-General11052024.json"

# Tests (no network)
pytest -q tests/test_results_tx_counties.py \
  tests/test_results_tn_counties.py
```

---

## See also

- [../sources-results.md](../sources-results.md): results adapters at a glance
- [../state-coverage-plan.md](../state-coverage-plan.md): election-night coverage plan
