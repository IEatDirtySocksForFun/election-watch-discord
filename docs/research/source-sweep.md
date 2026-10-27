# Source sweep: AP/DDHQ routes, API keys, fallback results sources

Research pass of 2026-10-27 for the 2026-11-03 general. **Read the caveat first.**

> **Verification caveat.** This pass ran in a sandbox whose network policy denied every
> news, election-vendor and state-election host (apnews.com, interactives.ap.org,
> results.decisiondeskhq.com, politics.api.cnn.io, results.texas-election.com,
> results.enr.clarityelections.com, app.enhancedvoting.com, api.open.fec.gov,
> v3.openstates.org, api.elections.kalshi.com, ...). Nothing below was fetched live from
> here. Statements come from (a) the real fixtures other packages already captured in
> `tests/fixtures/`, (b) web-search results (cited inline), and (c) knowledge of prior
> cycles. Each row carries a confidence tag:
>
> - **[fixture]** a real response is in the repo and a parser is tested against it
> - **[doc]** provider documentation or an official notice says so (cited)
> - **[prior]** true in 2022/2024 to the best of our knowledge; re-check before Nov 3
>
> No new adapter was written from guessed payloads. Where a route needs a real capture
> before code can be written, it says so.

## 1. AP and DDHQ: no-cost routes

### AP (Associated Press)

| Route | Works without paying? | Allowed use? | Status |
|-------|----------------------|--------------|--------|
| AP Elections API v3 (`api.ap.org/v3/elections`) | No. Key issued by AP sales/customer support; no public price list or self-serve trial [doc: [AP developer portal summary](https://scriptagc.wasmer.app/https_developer_ap_org), [Elex docs](https://elex.readthedocs.io/)] | Yes, under contract | Adapter `ap` exists, keyed on `AP_API_KEY` |
| Knight Election Hub (MuckRock) | Free AP results graphics + VoteCast for qualifying newsrooms (2024: INN members, or local newsrooms ≤ $5M revenue; "qualifying swing-state newsrooms" in one listing) [doc: [Knight Election Hub](https://accounts.muckrock.com/election-hub/), [Newspack](https://newspack.com/knight-foundation-election-hub/)] | Only for the newsroom that qualifies, and it is **embeds/graphics**, not a data API | Not usable by a general-purpose open-source bot. A qualifying newsroom running this bot should ask AP (contact listed on the hub: eparziale@ap.org) whether the deal includes API access |
| apnews.com results pages / `interactives.ap.org` JSON | Technically public JSON behind AP's own interactives [prior] | **No.** AP licenses this data to members; its site terms prohibit redistribution. A bot reposting it is redistribution | Not implemented. Could not be captured here (host denied) |
| AP data via member outlets (NYT, Politico, WaPo, USA Today, local papers' embeds) | Public JSON behind each outlet's pages [prior] | Same licensing problem, one step removed; the calls are AP's | Not implemented (no capture possible here). If a maintainer wants it anyway it must be `scrape=True`, off by default, and attributed "AP via <outlet>" |
| Elex / python-elections | Open-source clients, but they need an AP key [doc: [elex](https://elex.readthedocs.io/)] | n/a | Same as API |

**Verdict:** there is no legitimate no-cost route to AP data for this bot. The
outlets with their own decision desks (CBS, CNN, NBC, Fox, ABC — all already in
`electionwatch/sources/calls/`) are the free substitute for race calls, and
`cbs_results` (new, see §3) is a free substitute for per-race vote counts.

### DDHQ (Decision Desk HQ)

| Route | Works without paying? | Allowed use? | Status |
|-------|----------------------|--------------|--------|
| DDHQ results API (ddhq.io) | No. Sold to publishers, financial firms, researchers; no public pricing or self-serve key [doc: [ddhq.io](https://ddhq.io), [Cloudflare case study](https://www.cloudflare.com/case-studies/decision-desk-hq)] | Under contract | Skeleton `ddhq` keyed on `DDHQ_API_KEY`; endpoint/auth are client-specific |
| DDHQ embeddable widgets via Knight Election Hub | Free for qualifying newsrooms (non-INN, or races AP doesn't cover) [doc: [Knight hub listing](https://accounts.muckrock.com/election-hub/rechGjksx7cCd7V2z)] | Only for the qualifying newsroom; widgets, not data | Contact info@decisiondeskhq.com |
| DDHQ academic partnerships | DDHQ has run university desks (Georgetown McCourt, 2024) [doc: [Georgetown](https://mccourt.georgetown.edu/news/decision-desk-hq-learning-lab/)] | Per agreement | Ask DDHQ; plausible route for a university-hosted deployment |
| The Hill / NewsNation election hub (`elections20xx.thehill.com`, static pages on `elections2024-ssg.ddhq.io`) | Public pages; Nexstar continues with DDHQ through 2026 [doc: [The Desk](https://thedesk.net/2025/03/newsnation-the-hill-to-continue-using-decision-desk-hq/), [The Hill 2024 hub](https://elections2024.thehill.com/national)] | Site scrape of licensed data; no stated permission | Not implemented: needs a real capture of the 2026 hub's data files (host denied here). If implemented: `scrape=True`, off by default, ≥ 60 s polling |
| DDHQ Polling API (`polling-api-docs.decisiondeskhq.com`) | Docs public; access terms not stated [doc: [docs](https://polling-api-docs.decisiondeskhq.com/reference)] | Ask DDHQ | Relevant to the polls package, not calls |

**Verdict:** DDHQ also has no public free API. The only "free" paths are newsroom
programs and an academic partnership; both need a human to apply.

`ap_public.py` / `ddhq_public.py` were **not** written: the only technically free
routes are scrapes of licensed data that also could not be captured from this
sandbox, and the brief forbids untested parsers.

## 2. API keys a human can sign up for

Nothing here was signed up for (no credentials in the sandbox). Costs/limits are as
published at the cited source; confirm on the provider's page.

| Source | Data | Cost | Limits | Sign-up steps | Wired? |
|--------|------|------|--------|---------------|--------|
| **AP Elections API** | Results + calls, all races | Paid, quote only | Per contract | Contact AP Elections sales via developer.ap.org ("Contact us"), ask for 2026 general access + test data; key arrives by email | `AP_API_KEY` (existing `ap` adapter) |
| **DDHQ API** | Results + calls | Paid, quote only | Per contract | Email info@decisiondeskhq.com / form on ddhq.io; mention newsroom/academic status | `DDHQ_API_KEY` (existing `ddhq` skeleton) |
| **Texas SOS election-night media feed** | Official TX results, XML, ~5 min ahead of public site | **$250** per election (2022 terms) [doc: [Texas Press Assn. PDF](https://www.texaspress.com/sites/texaspress.com/files/file_uploads/SOSGeneralMediaFeed.pdf), [TAB](https://www.tab.org/news-and-events/news/key-election-night-tools-for-journalists)] | n/a | Call SOS Elections Division 1-800-252-8683; request the General Election media feed form; deadline was Sept 30 in past cycles, so ask about late enrolment | Not wired (needs the feed spec; would be a new `results/` adapter, owned by the results package) |
| **Kalshi** | Prediction-market prices | Free for read | Public market-data endpoints need no key; trading needs RSA-signed key [doc: [Codex](https://www.codex.io/blog/kalshi-api), docs.kalshi.com] | Not needed for this bot. For higher limits: kalshi.com account → KYC → Settings → API keys | Existing `kalshi` adapter, keyless [fixture] |
| **Polymarket (Gamma API)** | Market prices | Free | Keyless public read; rate limits published at docs.polymarket.com | Not needed | Existing `polymarket` adapter, keyless [fixture] |
| **OpenFEC** (api.open.fec.gov) | Candidate list, IDs, party, incumbency, finance | Free | 1,000 req/h with key (7,200/h on request); `DEMO_KEY` 30/h, 50/day [doc: [api.data.gov manual](https://api.data.gov/docs/developer-manual/), [open_fec](https://rubydoc.info/gems/open_fec)] | https://api.data.gov/signup/ → name + email → key emailed instantly | No adapter consumes it yet (candidate rosters would help name matching). Suggested var: `FEC_API_KEY` |
| **Open States / Plural** (v3.openstates.org) | State legislators, districts | Free tier | Daily/minute quota shown on account page (not confirmed) | Register at open.pluralpolicy.com (formerly openstates.org/accounts/profile) → request API key → send as `X-API-KEY` [doc: [Plural help](https://help.pluralpolicy.com/how-to-access-the-open-states-api)] | No adapter yet. Suggested var: `OPENSTATES_API_KEY` |
| **Google Civic Information API** | Elections list, voter info, OCD divisions. **Representatives endpoint shut down April 2025** [doc: [Google notice](https://groups.google.com/g/google-civicinfo-api/c/9fwFn-dhktA/m/ftUoL83mAwAJ)] | Free | 25,000 req/day default | console.cloud.google.com → new project → enable "Google Civic Information API" → Credentials → API key → restrict to that API | No adapter (no results/calls; useful only for polling-place lookups). Suggested var: `GOOGLE_CIVIC_API_KEY` |
| **Democracy Works Elections API** | Election dates, deadlines, ballot info | Paid / by request [doc: [democracy.works/data](https://www.democracy.works/data)] | Per contract | "Request pricing" form on data.democracy.works | No |
| **VoteHub** | Polls (public) | Free, keyless (per `docs/sources-signals.md`: polls only, no results endpoint) | — | None | Polls package |
| **civicAPI** (civicapi.org) | Claims live results/polls, keyless [doc: [freepublicapis](https://freepublicapis.com/civicapi)] | Free | Unknown | None | Not evaluated (could not reach); treat as unverified |
| **Clarity / Enhanced Voting / state feeds** | Official results | Free, keyless | Be polite (≥ 60 s) | None | `clarity`, `enhanced_voting`, state adapters |
| **ProPublica Congress API** | — | **Discontinued**; no new keys [doc: [ProPublica](https://projects.propublica.org/api-docs/congress-api/)] | — | — | No |

No new env var was added to `.env.example`: none of the free keys above feeds an
adapter that exists today, and the poll adapters (which might use them) belong to
another package. Suggested names are listed so whoever writes those adapters uses the
same ones.

## 3. National fallbacks for results

| Outlet | Adapter | Calls | Per-race votes | Coverage | Confidence |
|--------|---------|-------|----------------|----------|-----------|
| CBS `races2` | `cbs` / **`cbs_results` (new)** | yes | **yes**: `vote`, `votePct`, `pctIn`, `totalVote`, `lastUpdated` | Sen/Gov/House, all states | [fixture] 2024 archive. Live 2026 path assumed to be the same as the archive path with `2026`; confirm on Nov 3 |
| CNN `politics.api.cnn.io/results/race/<code>.json` | `cnn` | yes | likely (candidate vote fields were trimmed from our fixture) | Sen/Gov/House | [fixture] calls only; re-capture an untrimmed race to add results |
| NBC firecracker | `nbc` | yes | likely | national files per office | [fixture] calls only |
| Fox `feeds-elections.foxnews.com` | `fox` | yes | yes in full file (`countyVotes` trimmed) | per state per office, county-level | [fixture] calls only |
| ABC widget | `abc` | yes | likely in `__abcnews__` | per state per chamber | [fixture] calls only |
| NYT, Politico, WaPo, USA Today | — | AP's calls | yes | all | [prior]; AP-licensed data, not implemented (see §1) |
| The Hill | — | DDHQ's calls | yes | all + some local | [prior]; DDHQ-licensed data |

`cbs_results` (in `electionwatch/sources/calls/cbs_results.py`) is a `ResultsSource`
with `states` = all 50 + DC, `scrape=True`, `enabled_by_default=False`. It sets
`expected_vote_pct` from CBS' `pctIn` and never sets `winner` (CBS' flag is a
projection; the call comes through `cbs`). Enable with `EW_ENABLED_SOURCES=cbs_results`.
It does not cover territories' delegates, state legislature or down-ballot.

## 4. Per-state fallbacks

Abbreviations: **NAT** = national JSON chain `cbs_results` → `cnn`/`nbc`/`fox` calls
(federal + governor only). **CL** = Clarity/Scytl ENR (`results.enr.clarityelections.com`,
adapter `clarity`, add feeds via `CLARITY_FEEDS`). **EV** = Enhanced Voting CDN export
(`enhanced_voting`, via `ENHANCED_VOTING_FEEDS`). Vendor attributions are **[prior]**
unless marked; vendors change between cycles, so confirm each state's 2026 results page
before election night.

| State | Official statewide (2026) | County / vendor fallback | Recommendation |
|-------|---------------------------|--------------------------|----------------|
| AL | SOS ENR, HTML | Jefferson, Madison, Mobile county sites (HTML) | NAT |
| AK | Division of Elections, HTML/PDF; RCV tabulation later | none useful | NAT; RCV races resolve days later, calls will lag |
| AZ | EV (`2026NovGen`, per results package) | Maricopa (Maricopa's own ENR), Pima | `enhanced_voting`; NAT if export slug differs |
| AR | CL statewide | CL counties | `clarity` with an AR statewide entry; NAT |
| CA | SOS ENR (electionresults.sos.ca.gov; SOS publishes API docs for vendors [doc: [CA SOS API doc](https://cms.cdn.sos.ca.gov/media/3-api-documentation-v20.pdf)]) | LA County (results.lavote.gov), CL in several counties | NAT on night; CA counts for weeks, so `expected_vote_pct` matters more than precincts |
| CO | CL statewide | CL counties | `clarity` statewide entry |
| CT | SOS ENR (HTML) | towns report individually | NAT |
| DE | Dept. of Elections ENR (HTML) | — | NAT |
| DC | DCBOE ENR | — | NAT (delegate is not in CBS House data; DC races need DCBOE) |
| FL | `fl_sos` [fixture] | CL/other in many counties | `fl_sos`; NAT |
| GA | `ga_sos` / EV [fixture] | EV counties | `ga_sos`, then `enhanced_voting`, then NAT |
| HI | Office of Elections, PDF/HTML summary reports | — | NAT |
| ID | SOS ENR (HTML) | Ada County | NAT |
| IL | No statewide ENR on election night | CL in many counties (`clarity` built-in: McLean [fixture]); Cook County Clerk and Chicago BOE own sites | NAT for federal; CL feeds for counties |
| IN | Indiana ENR (state-run) | — | NAT |
| IA | CL statewide [prior] | CL counties | `clarity` statewide entry; NAT |
| KS | SOS ENR (HTML) | Johnson, Sedgwick county sites | NAT |
| KY | CL statewide [prior] | CL counties | `clarity` statewide entry |
| LA | SOS voter portal (graphical ENR, JSON behind the SPA) | — | NAT; note LA's Nov ballot is a jungle primary for many offices (runoffs Dec) |
| ME | SOS posts files after the night; RCV | — | NAT |
| MD | SBE ENR (HTML + CSV downloads) | — | NAT |
| MA | No real-time state feed; electionstats.state.ma.us after | — | NAT |
| MI | MVIC (Cloudflare-protected per results package notes) | Oakland, Kent, Wayne and other counties publish ENR (several on CL) | NAT; CL county feeds for big counties. Do not bypass the Cloudflare check |
| MN | SOS ENR media text files (electionresults.sos.mn.gov), delimited, well-documented | — | Good candidate for a results adapter (results package); NAT meanwhile |
| MS | SOS ENR (HTML) | — | NAT |
| MO | SOS ENR (HTML/XML) | St. Louis County, KC | NAT |
| MT | SOS ENR (HTML) | — | NAT |
| NE | SOS ENR (HTML) | Douglas, Lancaster | NAT (NE-02 is the key race) |
| NV | silverstateelection.nv.gov (HTML; no JSON found per results package) | Clark, Washoe county sites | NAT; Clark County dumps are what move the race |
| NH | No state ENR on night; towns report to SOS/press | — | NAT (calls come from desks) |
| NJ | Counties report; state posts later | CL in several counties | NAT |
| NM | SOS ENR (electionresults.sos.nm.gov) | — | NAT |
| NY | NYS BOE ENR (nyenr.elections.ny.gov, HTML) | NYC BOE (enr.boenyc.gov) | NAT; NYC BOE for city races |
| NC | `nc_sbe` [fixture] | — | `nc_sbe`; NAT |
| ND | SOS ENR (HTML) | — | NAT |
| OH | liveresults.ohiosos.gov (format unverified per results package) | CL in several counties | NAT |
| OK | OK State Election Board ENR | — | NAT |
| OR | SOS ENR; vote-by-mail, slow count | county sites (Multnomah) | NAT; expect late swings |
| PA | `pa_dos` [fixture] | CL in many counties (e.g. Allegheny) | `pa_dos`; NAT |
| RI | BOE ENR | — | NAT |
| SC | CL statewide [prior] | CL counties | `clarity` statewide entry |
| SD | SOS ENR (HTML) | — | NAT |
| TN | SOS ENR (HTML) | county sites | NAT |
| **TX** | `tx_sos` (results.texas-election.com; Cloudflare challenge from some IPs) | Harris (harrisvotes.com), Dallas, Tarrant, Bexar, Travis, Collin, Denton county sites; vendors vary and could not be verified | **See below** |
| UT | SOS ENR (electionresults.utah.gov); slow mail count | Salt Lake, Utah counties | NAT |
| VT | SOS ENR (HTML) | — | NAT |
| VA | ELECT ENR with JSON downloads | — | Good candidate for a results adapter (results package); NAT meanwhile |
| WA | SOS results with CSV media files (results.vote.wa.gov) | King County | Good candidate for a results adapter; NAT meanwhile; slow mail count |
| WV | `clarity` statewide [fixture, 2026 primary] | CL counties | `clarity`; NAT |
| WI | No statewide ENR on night; WEC ArcGIS later | Milwaukee, Dane county sites | NAT; county sites for Milwaukee "central count" dumps |
| WY | SOS ENR (HTML) | — | NAT |
| PR | CEE (Comisión Estatal de Elecciones) results site | — | CEE only; the Resident Commissioner is not in CBS House data. Desks rarely cover PR |
| GU | Guam Election Commission (PDF/HTML) | — | GEC only; delegate race |
| VI | VI Election System (vivote.gov) | — | Official only; delegate race |
| AS | American Samoa Election Office | — | Official only; delegate race (nonpartisan) |
| MP | CNMI Commission (votecnmi.gov.mp) | — | Official only; delegate race |

Territories are not in `racekeys.STATES`, so no adapter can key them today; adding them
is a contract change (`racekeys.py`) for the core package.

### Texas on election night

`results.texas-election.com` serves a Cloudflare challenge to some IP ranges
(datacenter IPs especially). **Do not try to solve or bypass it.** Legitimate options,
best first:

1. **Run the bot from a residential/office connection** (or any host that isn't
   challenged): the `tx_sos` adapter works unchanged where the site serves JSON.
2. **TX SOS paid media feed** ($250 in past cycles; XML, ~5 minutes ahead of the public
   site). Call 1-800-252-8683. This is the only official machine feed, and the right
   answer for anyone running the bot for a newsroom.
3. **`cbs_results`** (new): statewide TX SEN/GOV and all 38 House seats with votes and
   expected-vote %, keyless. Enable with `EW_ENABLED_SOURCES=cbs_results`. Does not
   cover AG/LTGOV/RRC/legislature.
4. **County sites** for Harris/Dallas/Tarrant/Bexar/Travis (≈ 45% of the statewide
   vote). Their 2026 ENR vendors/URLs could not be confirmed from here; check each
   county clerk/elections page the week before and add any Clarity feeds through
   `CLARITY_FEEDS`.
5. Race calls for TX come from the existing `cbs`/`cnn`/`nbc`/`fox`/`abc` adapters.

## 5. What a human should do before Nov 3

1. From an unrestricted machine, capture one untrimmed 2026 (or 2024) CNN race JSON and
   one Fox state file; that unlocks `cnn_results` / `fox_results` the same way as CBS.
2. On election morning, confirm the CBS live path:
   `https://api-election.cbsnews.com/archive/api/public/races2/2026/G?filter.officeCode=S&filter.state=TX`.
   If CBS serves live data elsewhere, change `CBSSource.BASE` (shared by `cbs_results`).
3. If running for a newsroom: apply to AP/DDHQ via the Knight Election Hub, and buy the
   TX SOS media feed.
4. Get `FEC_API_KEY` / `OPENSTATES_API_KEY` only when an adapter that uses them exists.
