# Alternate official results: TX, NY, NJ, MA, KY and MO

Second source for the six states whose statewide official results site is blocked, PDF-only or
certified-only (see `states-*.md`). Status is what was verified against live responses on
2026-10-27; "verified fixture" means a trimmed real response is committed under
`tests/fixtures/<adapter>/` (README there records the URL and capture date) and parsed by
`tests/test_results_<adapter>.py` with no network.

| State | Adapter                              | Feed                                                                                                                                                                                    | Coverage                                                                                                                                                                                             | Status                                                                                        |
| ----- | ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| TX    | `tx_counties`                        | `https://results.enr.clarityelections.com/TX/<County>/elections.json` → `/<EID>/<ver>/json/en/summary.json`                                                                             | 17 counties configured; 14 published a 2024 Senate contest = 2,018,216 counted Senate votes = **17.87%** of Texas's 2024 Senate vote (11,291,854). Per-county table in `docs/results/counties.md`    | verified fixtures (2024 general for 13 counties; 2026 general for Denton, Galveston, Kaufman) |
| NJ    | `nj_counties`                        | `https://results.enr.clarityelections.com/NJ/<County>/…` (same vendor layout)                                                                                                           | 14 counties configured, all answering; 2024 verified for Mercer + Cumberland = 208,807 Senate votes; 8 counties have pinned 2024 ids                                                                 | verified fixture (2024 general Mercer + Cumberland; 2026 general Cumberland)                  |
| NY    | `ny_nycboe` (scrape, off by default) | `https://vote.nyc/page/election-results-summary[-<year>]` → `https://www.vote.nyc/sites/default/files/pdf/election_results/<YYYY>/<YYYYMMDD><Stage>/<code><County> <Contest> Recap.csv` | New York City = 5 counties (New York, Bronx, Kings, Queens, Richmond); 2024 Senate citywide total 2,680,944 votes; 840 recap files listed for the 2024 general, downloaded only for subscribed races | verified fixture (2024 general Senate citywide, CD-10 Manhattan + Brooklyn)                   |
| MO    | `mo_stlouis`                         | `https://extcontent.stlouisco.com/BOE/eResults/el<YYMMDD>/CSV.csv` (per-precinct CSV, cp1252)                                                                                           | St. Louis County only: 2024 Senate 502,494 votes, 965 precincts, 89 contests                                                                                                                         | verified fixture (2024 general)                                                               |
| MA    | —                                    | `https://www.sec.state.ma.us/...` (Imperva), `https://electionstats.state.ma.us` (PD43+)                                                                                                | none                                                                                                                                                                                                 | not possible — see MA notes                                                                   |
| KY    | —                                    | `https://vrsws.sos.ky.gov/liveresults/` (WAF), `https://elect.ky.gov/Results/` (archive pages only)                                                                                     | none                                                                                                                                                                                                 | not possible — see KY notes                                                                   |

Percentages are only quoted where a verified statewide denominator exists in the repo (the
CBS 2024 fixtures). Elsewhere the numbers are the votes the adapter actually reproduces; the
adapters are county feeds, so every result carries the `" (partial: N counties)"` title suffix
the bot uses to flag partial coverage.

---

## TX — `tx_counties` (county Clarity ENR feeds)

`results.texas-election.com` is intermittently behind a Cloudflare challenge. The large
counties publish the same Clarity ENR files on the vendor CDN and were all reachable with a
plain GET:

```text
/TX/Denton/122426/355989/json/en/summary.json      2024 general (Denton)
/TX/El_Paso/122595/355580/json/en/summary.json     2024 general (El Paso)
/TX/Denton/127398/382120/json/en/summary.json      2026 general (zero votes, pre-election)
```

Seventeen counties are configured: Bastrop, Bell, Comal, Dallas, Denton, El Paso, Fort Bend,
Galveston, Gregg, Hood, Kaufman, Lamar, Midland, Rockwall, Rusk, Tarrant and Travis. Fourteen of
those counties published a 2024 Senate contest (2,018,216 counted votes = 17.87% of the statewide Senate
vote);
the per-county 2024 numbers and shares are in `docs/results/counties.md`. The remaining large
Texas counties (Harris, Bexar, Collin, Hidalgo, Williamson, Montgomery, Brazoria, Nueces,
Cameron, Lubbock, Webb, McLennan, Hays, Smith, Jefferson) have no directory on this CDN.

Discovery is per county from `elections.json` (`EID`, `ElectionName`, `Date`); verified 2024
ids are pinned because Dallas, Tarrant and Fort Bend publish no `elections.json` at all (the three
are still listed and skipped, and `TX_COUNTY_FEEDS` pins any of the three). One dead county cannot
break the fetch: failures are counted and only an all-county failure raises.

Name handling: the counties disagree on wording ("United States Representative, District 4"
vs "US Representative, District 16", "Chief Justice, 8th Court of Appeals District" vs
"Chief Justice, 8th Crt of App Dist"). County abbreviations are expanded, US Senate/House,
the statewide executives, the legislature and the statewide courts map to shared keys, and
everything else becomes `TX LOCAL <county>-<race>` — so two counties' "Sheriff" never collide
while a statewide appeals-court race still merges.

---

## NJ — `nj_counties` (county Clarity ENR feeds)

New Jersey publishes result PDFs per county and no machine-readable statewide file; 14 counties
run Clarity ENR. The state-level `NJ/elections.json` is a 404, the county listing is not.
Election ids are discovered per county (`2026 GENERAL ELECTION` is already listed by
Cumberland as EID 127562) with the verified 2024 ids pinned. Camden and Bergen answer 404 on
the CDN and are not listed; `NJ_COUNTY_FEEDS` pins either county if it moves back.

Three House naming styles appear in the feeds — `House of Representatives CD 6`,
`U.S. House of Representatives 12th Congressional District`, `HOUSE OF REPRESENTATIVES - 2ND
CONGRESSIONAL` — all parsed to `NJ-HOUSE-<district>`. A bare `United States House of
Representatives` (no district, as Cumberland publishes in 2026) is kept county-local instead
of pretending it is a district race. Sheriff, county commissioners, mayors, school-board
seats and referenda are `NJ LOCAL <county>-<race>`.

---

## NY — `ny_nycboe` (New York City Board of Elections recap CSVs)

`nyenr.elections.ny.gov/` and `results.elections.ny.gov` answer 403 to a plain request; the
state data host `ny.elstats.civera.com` serves only per-contest ids with no index endpoint, so
the state feed cannot be discovered without the blocked front end (documented in `states-4.md`).
The city's own recap files are reachable and are the official statement of votes:

```text
https://vote.nyc/page/election-results-summary-2024          index (one row per file)
.../2024/20241105General Election/00000800000Citywide United States Senator Citywide Recap.csv
.../2024/20241105General Election/00101900010New York Representative in Congress 10th Congressional District Recap.csv
.../2024/20241105General Election/00301900010Kings  Representative in Congress 10th Congressional District Recap.csv
```

Each file is one contest in one county ("Citywide" for the aggregate). Votes are read from the
`Total for <contest> - <County>` block, skipping the machine categories (`Public Counter`,
`Absentee / Military`, `Affidavit`, `Federal`, ...). The index is parsed first and only the
files matching the subscribed race keys are downloaded, so a subscription for `NY SEN` pulls
one 2 MB file instead of the 840 files listed for the 2024 general; a citywide file replaces
the borough files it already sums. Only general-election files are accepted (primary and
crossover files are per party and would collide on race keys). Finding the files means
parsing the HTML index, so the adapter sets `scrape = True` and `enabled_by_default = False`
per AGENTS.md; an operator switches it on.

Not covered: upstate New York (57 counties) — no machine-readable publisher was found.

---

## MO — `mo_stlouis` (St. Louis County per-precinct CSV)

`enr.sos.mo.gov` answers a Cloudflare challenge and `sos.mo.gov/enrweb/` redirects into it, so
the statewide ENR is not usable. St. Louis County publishes a full per-precinct export per
election (`el{YYMMDD}`), 9.4 MB / 104,636 rows for 2024-11-05, cp1252:

```text
https://extcontent.stlouisco.com/BOE/eResults/el241105/CSV.csv
```

Rows are (precinct, contest, choice, votes); votes are summed per choice and the precincts
reporting a contest are counted. Statewide offices, US House/Senate, the legislature and
statewide ballot measures map to shared keys; `ST. LOUIS COUNTY - PROPOSITION A` and the
judicial retention contests (titled with the judge's name, YES/NO choices) become county-local.
The export has no precinct total, so `precincts_total` and the expected-vote percentage stay
empty. Jackson, St. Charles, Greene, Clay and Boone run other vendors
(`livevoterturnout.com` HTML) and are not implemented.

---

## MA — not possible

`https://www.sec.state.ma.us/ele/eleidx.htm` returns an Imperva/Incapsula interstitial (212
bytes, `_Incapsula_Resource` script, `ROBOTS NOINDEX`) to a plain request with a descriptive
User-Agent. The Secretary of State's own results archive, `https://electionstats.state.ma.us`
("PD43+"), _is_ reachable (933,632 bytes for a 2024-general search) but serves only HTML:
`.../stage:General/download/csv` and `.../format:csv` both return the same HTML page, and the
page contains no CSV/JSON/XLS link at all. Massachusetts does not administer elections at
county level (city/town clerks do), so there is no county-feed alternative either. A scraper of
PD43+ is possible in principle but is certified-results HTML, not election-night data, so no
statewide adapter is enabled.

---

## KY — not possible

`https://vrsws.sos.ky.gov/liveresults/` (the State Board of Elections' live results) answers
**403 Forbidden** (nginx WAF) for `/`, `/data`, `/data/currentresults.xml`, `/data/elections.xml`,
`/data/contests.xml` and `/data/candidates.xml`. `https://apps.sos.ky.gov/electionresults/`
also answers 403. `https://elect.ky.gov/Results/Pages/default.aspx` is reachable (106 KB) but
is the historical archive — it only links to per-year pages (1973 … present), with no live feed
and no CSV/JSON export. Kentucky's Clarity CDN paths answer 403 for every county tried
(Jefferson, Fayette, Kenton), so there is no county-feed alternative. No bypass attempted.

---

## See also

- [../sources-results.md](../sources-results.md): results adapters at a glance
- [../state-coverage-plan.md](../state-coverage-plan.md): election-night coverage plan
