# State coverage plan: election night 2026-11-03

This guide covers states where the bot has no complete official feed. It compares the
available routes, records the evidence and explains what the adapters do. The checks were run
on **2026-10-27** with the bot's own User-Agent, one GET at a time and at least 2 seconds
between requests. **Nothing here gets past a bot check.** A challenge, WAF block or IP block
is a closed door; use another official publisher or civicAPI instead.

For the underlying feed notes, read
[`docs/results/counties.md`](results/counties.md), [`alt-a.md`](results/alt-a.md),
[`alt-b.md`](results/alt-b.md), [`alt-c.md`](results/alt-c.md) and
[`states-*.md`](results/).

## Summary: expected coverage

"Share" is a county's share of the state's **2024 US Senate vote**. AL uses 2022 instead,
because it had no 2024 Senate race. Denominators: TX 11,291,854 and TN 3,007,608 (from the CBS
2024 fixtures in the repo, `logs/health/final-2024.json`); AL 2022 ≈ 1.41 M. County numerators
were read from the counties' own 2024 (AL: 2022) files.

| State      | Initial official feed                                              | Current official feed                                                                                                                                                             | Ready for 2026 at the check                                                                                                                                       | National fallback              |
| ---------- | ------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| **TX**     | 14 Clarity counties = **17.9%** (tx_sos 403 Cloudflare)            | + Harris (own JSON) 13.7%, Tarrant 7.3%, Bexar 6.6%, Fort Bend 3.2% = **48.6%** verified; Dallas (≈8%) and Williamson (≈3.6%) are picked up when they list the general → **≈60%** | 13 county feeds answer for 2026 now = **40.5%**; Travis, Bell, Midland, Gregg and Comal (8.1%) list their 2026 primaries and should list the general before Nov 3 | fox_results OK (Sen/Gov/House) |
| **TN**     | Shelby 10.6% (2026 export appears on the night)                    | unchanged; no other county publishes an open feed                                                                                                                                 | 0% until Shelby's export appears                                                                                                                                  | fox_results OK                 |
| **AL**     | none                                                               | **Madison County 8.5%** (`al_counties`, new)                                                                                                                                      | 0% until Madison lists the general (its 2026 primary runoff is listed)                                                                                            | fox_results OK                 |
| KY         | none (SBE live-results host blocks this IP: "acceptable use" page) | unchanged; **sanctioned route: ask SBE**, which publishes a documented XML feed                                                                                                   | —                                                                                                                                                                 | fox_results OK                 |
| NV         | none (Imperva; Clark is Blazor/SignalR, Washoe PDF)                | unchanged; **ask SOS for media-site access**                                                                                                                                      | —                                                                                                                                                                 | fox_results OK                 |
| OK         | none (results host 403 for every UA)                               | unchanged; **ask State Election Board**                                                                                                                                           | —                                                                                                                                                                 | fox_results OK                 |
| MA, ME, NH | none (no live state feed; towns report)                            | unchanged                                                                                                                                                                         | —                                                                                                                                                                 | fox_results OK                 |
| WY         | none (certified XLSX/PDF only)                                     | unchanged                                                                                                                                                                         | —                                                                                                                                                                 | fox_results OK                 |
| MS         | certified only (`ms_sos`)                                          | unchanged                                                                                                                                                                         | —                                                                                                                                                                 | fox_results OK                 |
| MI         | Ottawa (+3 configured)                                             | unchanged                                                                                                                                                                         | per `mi_county`                                                                                                                                                   | fox_results OK                 |
| WI         | Dane (opt-in)                                                      | unchanged                                                                                                                                                                         | per `wi_dane`                                                                                                                                                     | fox_results OK                 |
| MO         | St. Louis Co.                                                      | unchanged (Jackson, St. Charles: no Clarity directory)                                                                                                                            | export appears on the night                                                                                                                                       | fox_results OK                 |
| NY         | NYC (opt-in)                                                       | unchanged (Monroe, Erie, Westchester: no Clarity directory)                                                                                                                       | per `ny_nycboe`                                                                                                                                                   | fox_results OK                 |
| NJ         | 14 Clarity counties                                                | unchanged                                                                                                                                                                         | per `nj_counties`                                                                                                                                                 | fox_results OK                 |

The poller prefers the first complete result and uses a partial county tally only when no
complete source answers (`poller.best_result`). With a national fallback enabled, these states
get a statewide Senate/Governor/House picture plus official county detail for every down-ballot
race the counties publish.

## How to turn the fallback on for just these states

National fallbacks read news sites' public JSON. They set `scrape = True` and
`enabled_by_default = False`, so turning one on is your decision. **Fox's election-feed
`robots.txt` disallows automated access. Keep `fox_results` off unless you have reviewed the
current terms and deliberately chosen to use it.** `EW_SOURCE_STATES` narrows an enabled
adapter to a list of states:

```sh
EW_ENABLED_SOURCES=fox_results
EW_SOURCE_STATES=fox_results=AL,KY,MA,ME,MI,MO,MS,NH,NJ,NV,NY,OK,TN,TX,WI,WY
```

Fox then serves only those 16 states. Everywhere else the official feeds are the only results
source.

### How good is the fallback?

| Adapter       | 2026 today                                                 | 2024 replay                                                                      | Races                           | Speed / detail                                                                                               | Notes                                                          |
| ------------- | ---------------------------------------------------------- | -------------------------------------------------------------------------------- | ------------------------------- | ------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------- |
| `fox_results` | **OK in all 16 states**: 2026 candidates listed at 0 votes | OK; but TX-01 read 0 votes and NY/WI House rows were missing in the 2024 archive | Senate, Governor, US House only | state level; Fox's file also has `county_level_results` for statewide races, which the adapter does not read | the only fallback that already serves 2026 files. Recommended. |
| `cbs_results` | EMPTY (2026 path not live yet)                             | OK, full statewide totals                                                        | Sen/Gov/House                   | `pctIn` = expected vote                                                                                      | confirm the 2026 path on election morning (source-sweep §5)    |
| `abc_results` | EMPTY                                                      | OK                                                                               | Sen/Gov/House                   | expected vote %                                                                                              |                                                                |
| `cnn_results` | HTTP 406 to this host                                      | 406                                                                              | —                               | —                                                                                                            | CNN blocks this IP; do not retry in bursts                     |
| `nbc_results` | HTTP 403                                                   | 403                                                                              | —                               | —                                                                                                            | NBC blocks this IP; do not retry in bursts                     |

None of the fallbacks has down-ballot races (statewide row offices, legislature, local). Those
come only from the official state and county feeds above. All of them carry AP- or
outlet-desk-sourced numbers, updated every few minutes on the night. Their terms are the
outlets' site terms, which do not grant redistribution: hence opt-in.

---

## Texas

**Problem:** `https://results.texas-election.com/static/data/version.json` → **403**, Cloudflare
"Attention Required" page (2026-10-27 21:35Z). Closed door.

### Ranked options

| #   | Option                                                                                                                             | Evidence (2026-10-27)                                                                      | Effort                                                    | Risk                                                                              | Terms                                                                 | Recommendation                      |
| --- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | --------------------------------------------------------- | --------------------------------------------------------------------------------- | --------------------------------------------------------------------- | ----------------------------------- |
| 1   | **County feeds, extended** (built)                                                                                                 | see county table below                                                                     | done                                                      | counties may rename contests on the night; one dead county never hides the others | public county results; no terms restrict reading them                 | **On by default (`tx_counties`)**   |
| 2   | **TX SOS media feed** (XML over SFTP; $250 per election in 2022; reservation deadline was in September)                            | Texas Press Assn. notice `texaspress.com/.../SOSGeneralMediaFeed.pdf`; TAB notice for 2023 | email/phone + payment; then a new parser for the XML spec | the 2026 deadline has probably passed; ask about late enrolment                   | subscriber agreement; ask whether an open-source bot may redistribute | not pursued                         |
| 3   | **Ask SOS to allowlist the bot's User-Agent** on results.texas-election.com, or to publish the static JSON on an unchallenged host | same 403                                                                                   | one email                                                 | none                                                                              | —                                                                     | not pursued                         |
| 4   | Run the bot from a host that the site does not challenge (`tx_sos` works unchanged where the site serves JSON)                     | the challenge depends on the IP range                                                      | deployment choice                                         | none, provided the host is your normal network (no proxy or IP rotation)          | —                                                                     | your choice; see the research guide |
| 5   | `fox_results` via `EW_SOURCE_STATES`                                                                                               | OK for TX SEN/GOV/TX-01 in 2026                                                            | config                                                    | outlet terms                                                                      | opt-in                                                                | **Enable for TX**                   |

### Texas counties by 2024 Senate vote

| County                                                                                | 2024 Senate votes                                               | Share  | Cumulative | Vendor / URL                                                                                                                                                      | Status                                                          |
| ------------------------------------------------------------------------------------- | --------------------------------------------------------------- | ------ | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| Harris                                                                                | 1,548,914                                                       | 13.72% | 13.7%      | own JSON: `appfiles.harrisvotes.com/harrisvotes/prd/Data/<MMYY>/resultsbyballot.json` (1124 = 2024, 1126 = 2026; 200 OK, both)                                    | **added** (`_harris`, merged in `tx_counties`)                  |
| Dallas                                                                                | ≈ 8% (not measured)                                             | —      | —          | Clarity, `TX/Dallas/elections.json` lists the 2026 runoffs (126485, 126270); dallascountyvotes.org answers **202 with an empty body** (AWS WAF challenge, closed) | already configured; **discovered once listed**                  |
| Tarrant                                                                               | 821,473                                                         | 7.27%  | 21.0%      | Clarity 122489 (2024) / **127297** (2026, zero-vote file live), ids linked from tarrantcountytx.gov; elections.json empty                                         | **pinned**                                                      |
| Bexar                                                                                 | 740,804 (from the ES&S HTML report `home.bexar.org/el45a.html`) | 6.56%  | 27.6%      | 2024: ES&S HTML; **2026: Clarity 127291** (linked from elections.bexar.gov; elections.json `[]`)                                                                  | **added + pinned**                                              |
| Travis                                                                                | 578,885                                                         | 5.13%  | 32.7%      | Clarity; 2026 primaries listed                                                                                                                                    | configured, discovered                                          |
| Collin                                                                                | ≈ 4.6% (520,196 ballots)                                        | —      | —          | own PDFs plus a post-canvass precinct CSV at an unpredictable Sitefinity URL; `TX/Collin` is 404 on Clarity                                                       | not feasible (PDF)                                              |
| Denton                                                                                | 448,459                                                         | 3.97%  | 36.7%      | Clarity 127398                                                                                                                                                    | pinned                                                          |
| Fort Bend                                                                             | 356,419                                                         | 3.16%  | 39.8%      | Clarity 122596 / **127480** (linked from fortbendcountytx.gov)                                                                                                    | **pinned**; name fix for "District 7 District 7"                |
| Williamson                                                                            | ≈ 3.6%                                                          | —      | —          | Clarity: `TX/Williamson/elections.json` lists the 2026 runoff (127277); wilcotx.gov answers 403 to our UA                                                         | **added**, discovered                                           |
| El Paso                                                                               | 245,124                                                         | 2.17%  | 42.0%      | Clarity                                                                                                                                                           | pinned                                                          |
| Montgomery                                                                            | ≈ 3.3%                                                          | —      | —          | `TX/Montgomery` 404 on Clarity; county results page not found                                                                                                     | not feasible yet                                                |
| Hidalgo                                                                               | ≈ 1.9%                                                          | —      | —          | Clarity until 2020 only; now Hart HTML cumulative report on CivicPlus                                                                                             | not feasible (HTML report at unpredictable DocumentCenter URLs) |
| Galveston, Bell, Comal, Kaufman, Rockwall, Midland, Gregg, Bastrop, Hood, Rusk, Lamar | 745,832 together                                                | 6.61%  | **48.6%**  | Clarity                                                                                                                                                           | configured (Rockwall lists the 2026 general as 127294)          |
| Brazoria, Hays                                                                        | —                                                               | —      | —          | `TX/<county>` 404 on Clarity                                                                                                                                      | not feasible yet                                                |

**Total verified: 48.6%** of the 2024 Texas Senate vote (5,485,910 of 11,291,854), up from
17.9%. Dallas and Williamson, already configured, would take it to about 60% once they list the
general. A single live poll on 2026-10-27 merged **13 counties** for `TX SEN`, all at 0 votes.

What to watch on the night: `tx_counties` merges whichever counties answer. Each title says
`(partial: N counties)`. A reachable `tx_sos` or an enabled national fallback takes precedence
for TX SEN/GOV/House.

---

## Tennessee

**Problem:** `elections.tn.gov` and `sos.tn.gov` answer **403 CloudFront** (states-4.md,
alt-c.md; not re-probed). The state's own results site is the only statewide publisher.

| #   | Option                                                                                                               | Evidence                                                                                                                                               | Effort    | Recommendation                                                                  |
| --- | -------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | --------- | ------------------------------------------------------------------------------- |
| 1   | Shelby County Enhanced Voting export (`tn_counties`, existing)                                                       | 2024 export verified; `export-General11032026.json` 404 until the night                                                                                | done      | keep                                                                            |
| 2   | **Ask the TN Division of Elections** to allowlist the bot or name a media feed                                       | 403 everywhere on the domain                                                                                                                           | one email | not pursued                                                                     |
| 3   | Other counties on Enhanced Voting                                                                                    | `app.enhancedvoting.com/cdn/results/{davidson,knox,hamilton,rutherford,williamson}-county-tn/export-General11052024.json` → **404** (all five, 22:24Z) | —         | none publish there                                                              |
| 4   | Davidson (PDF), Knox (SPA with a per-session token), Hamilton (signed PDFs; `elect2.hamiltontn.gov/results/...` 404) | county notes                                                                                                                                           | —         | not feasible without a PDF parser or token replay. Token replay is out of scope |
| 5   | Third-party mirror `tnfirefly.com` (republishes SOS results)                                                         | web search only; not probed                                                                                                                            | —         | **not recommended**: a scrape of a scrape, with unknown terms                   |
| 6   | `fox_results`                                                                                                        | 2026 OK                                                                                                                                                | config    | **Enable for TN**                                                               |

Coverage: Shelby **10.6%** (319,479 Senate votes in 2024). Its 2026 export appears on the night.

---

## Alabama

**Problem:** the SOS app (`www2.alabamavotes.gov/electionnight/...`) serves the same empty
shell for every election code. The only machine-readable statewide data is certified `.xls`
(states-2.md).

| #   | Option                                        | Evidence (2026-10-27)                                                                                                  | Recommendation                                                         |
| --- | --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| 1   | **Madison County Clarity**                    | `AL/Madison/elections.json` 200, lists `126524 2026 Primary Runoff Election`; 2022 general `116245/313343` summary 200 | **built: `al_counties`**, on by default, discovers the general by date |
| 2   | Jefferson, Mobile, Baldwin, Shelby on Clarity | elections.json 200 but newest entry is the **2012** general                                                            | not usable                                                             |
| 3   | Ask the SOS for a media/results file          | —                                                                                                                      | not pursued                                                            |
| 4   | `fox_results`                                 | 2026 OK                                                                                                                | **Enable for AL**                                                      |

Coverage: Madison **≈ 8.5%** (120,500 of ≈ 1.41 M Senate votes in 2022).

---

## Kentucky

`vrsws.sos.ky.gov/liveresults/...` now answers **403 "KY SBE Acceptable Use Policy"**. The page
says the State Board's firewall "identified website scraping activity originating from your
IP" and offers a contact form (reference ID `0.f8c83017.1793139994.4f125b6a`, 22:26Z). This IP
is therefore flagged. **Do not probe it again.** The SBE documents an XML data feed
(`/liveresults/data`, "Current Results ... updated every two minutes"; _Data Website Guide_,
found by web search). That feed is the right source, but only with the Board's permission.
Kentucky counties' Clarity paths answered 403 earlier (alt-b.md).

Not pursued: the bot uses civicAPI, then `fox_results`.

## Nevada

The statewide site is Imperva, Clark County is Blazor/SignalR, and Washoe publishes PDFs
(alt-a.md). The SOS runs "a secure, closed website" that gives a small number of media outlets
results at poll close (_Nevada Independent_, 2026 primary). `NV/Washoe` is 404 on Clarity. The bot uses civicAPI, then
`fox_results`.

## Oklahoma

`results.okelections.gov` / `.us` answer 403 to every request (states-4.md, alt-c.md). The
board's own pages (oklahoma.gov) say its results "Export" tab offers zipped county, race and
precinct files. Those sit behind the same host. Tulsa's Clarity listing ends in 2012.
The bot uses civicAPI, then `fox_results`.

## Massachusetts, Maine, New Hampshire

No live state feed exists (MA: PD43+ is certified-only; ME and NH towns report by phone/PDF;
NH's SOS is CloudFront 403). Use civicAPI or an enabled national fallback.

## Wyoming

Certified XLSX/PDF only. `WY/Laramie` is 404 on Clarity. Use `fox_results`.

## Mississippi

`ms_sos` reads the certified recap after the election (2026 page 404 until then). No
election-night feed exists. Use `fox_results` on the night.

## Partial states: MI, WI, MO, NY, NJ

The existing county adapters cover these states. Checks on a few more counties found:

| State | Checked                               | Result                                     |
| ----- | ------------------------------------- | ------------------------------------------ |
| MO    | Jackson, St. Charles on Clarity       | 404 (other vendors: livevoterturnout HTML) |
| NY    | Monroe, Erie, Westchester on Clarity  | 404                                        |
| MI    | Wayne (ES&S postback app), Kent (403) | unchanged from alt-a.md                    |
| WI    | Milwaukee (Cloudflare)                | unchanged                                  |
| NJ    | Bergen, Camden                        | 404 on Clarity (alt-b.md)                  |

For all five, a complete statewide source takes precedence over the county tally. With
`EW_SOURCE_STATES=fox_results=...`, the statewide Senate/Governor/House picture can come from
Fox, while county detail and down-ballot races come from the official county feeds.

## Third-party aggregators

| Source                                                | Results?                                                                                                                                        | Terms                                                                                                                                                                                                                                                     | Verdict                                                                                                                                                                                                                                         |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| VoteHub                                               | polls API only (`votehub.com/polls/api/`); no results endpoint found                                                                            | public, keyless                                                                                                                                                                                                                                           | already used for polls                                                                                                                                                                                                                          |
| OpenElections                                         | certified, after the fact                                                                                                                       | open data                                                                                                                                                                                                                                                 | no live value                                                                                                                                                                                                                                   |
| Decision Desk HQ                                      | paid API; free widgets only for qualifying newsrooms                                                                                            | contract                                                                                                                                                                                                                                                  | paid option; see source-sweep.md                                                                                                                                                                                                                |
| AP                                                    | paid; public pages are licensed data                                                                                                            | contract                                                                                                                                                                                                                                                  | no free route                                                                                                                                                                                                                                   |
| Google Civic API                                      | no results                                                                                                                                      | free                                                                                                                                                                                                                                                      | n/a                                                                                                                                                                                                                                             |
| civicAPI.org                                          | **yes**: live results for every state + DC down to county and local races, plus its own race calls (`/api/v2/race/search`, `/api/v2/race/{id}`) | keyless; free for commercial and non-commercial use with attribution. Its FAQ says the data is aggregated from Secretaries of State and state/county boards (local media only for some small or foreign elections); calls come from its own decision desk | **adopted** (`civicapi`, on by default, ranked after official feeds; test data dropped). Verified 2026-10-27: 2024 TX SEN/TX-28 match the official totals; every 2026 federal race is currently `test_data: true` (fake winners) and is dropped |
| Newspapers' JSON (NYT, WaPo, Houston Chronicle, KPRC) | AP-licensed data                                                                                                                                | site terms                                                                                                                                                                                                                                                | not recommended (redistribution of AP data)                                                                                                                                                                                                     |
| tnfirefly.com                                         | mirror of TN SOS                                                                                                                                | unknown                                                                                                                                                                                                                                                   | not recommended                                                                                                                                                                                                                                 |

## Anything else found

- Harris County also publishes per-precinct JSON (`Data/<code>/pv-<contestId>.json`) and a
  precinct list (`pr.json`). Neither is used; county totals are enough for the bot.
- Bexar's 2024 ES&S HTML summary (`home.bexar.org/el45a.html`) is parseable, but it is moot:
  Bexar moved to Clarity for 2026.
- Collin publishes a full precinct CSV after the canvass, not on the night.
- When running from a source checkout, put it on `PYTHONPATH` so `tools/health_check.py`
  imports that copy of `electionwatch`:

  `PYTHONPATH=$PWD python tools/health_check.py`

## Election-morning checklist

1. `PYTHONPATH=$PWD python tools/health_check.py --only tx_counties,al_counties,tn_counties`.
   TX should report 13+ counties. AL becomes OK once Madison lists the general.
2. If Dallas, Travis or Williamson still list no general by evening, find the id on the
   county's results page and pin it:
   `TX_COUNTY_FEEDS='[{"county":"Dallas","election_id":"<id>","date":"2026-11-03"}]'`.
   Note that `TX_COUNTY_FEEDS` replaces the whole Clarity list, so include every county you
   want.
3. Harris switches to `Data/1126/` automatically. `TX_HARRIS_CODE` overrides it.
4. Decide on `EW_ENABLED_SOURCES=fox_results` + `EW_SOURCE_STATES` (above).
