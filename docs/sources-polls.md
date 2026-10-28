Poll sources: what is verified, how it is fetched, what it costs
==============================================================

Code: `electionwatch/sources/polls/` (contract: `PollSource` in
`electionwatch/sources/base.py`, model: `Poll` / `PollResult` in
`electionwatch/models.py`).

Every adapter is pure parsing + fetch:

  parse_<source>_polls(payload, ...) -> list[Poll]   (offline, tested against fixtures)
  <Source>().fetch_polls(keys, client)               (httpx.AsyncClient is passed in)

All three are tested against trimmed **real** responses in
`tests/fixtures/<adapter>/` (URL and fetch date in each `README.txt`). Tests
never touch the network. No LLM / model API is involved anywhere: "partisan",
"sponsor" and party codes are parsed, never inferred by a model.

Available sources
-----------------

1. votehub  -- `electionwatch/sources/polls/votehub.py`
   Method: JSON API, `GET https://api.votehub.com/polls`. The endpoint returns
   the whole database as one array (~2.9 MB, no auth, no pagination). Query
   parameters (`poll_type`, `state`, ...) are accepted but ignored, so the
   adapter downloads once per fetch cycle and filters locally.
   Races: US Senate (`us-senator`), Governor (`governor`) and US House
   (`us-representative`, district from `subject` = "2026 IL-04"; single-district
   states such as "2026 AK-01" map to the at-large key `AK-AL`).
   Fields: pollster, start/end date, sample size, population (LV/RV/A),
   sponsor(s) (joined), partisan flag, poll URL, per-candidate percentages.
   Verified: 2026-10-27 (fixture `tests/fixtures/votehub_polls/`).
   `scrape = False`, `enabled_by_default = True`.
   Limits: VoteHub publishes no party per answer, so `PollResult.party` is
   `None` for candidate polls. Party-word answers (generic ballot "Dem"/"Rep")
   are normalised with `normalize_party`. `internal` is always `false` upstream
   (it exports partisanship through `partisan` instead).

2. 270towin  -- `electionwatch/sources/polls/two70win.py`
   Method: the poll list on "Latest 2026 <office> Election Polls" is injected by
   jQuery from `https://www.270towin.com/polls/php/get-polls-list-v2.php`
   (`election_year=2026`, `election_types[]=S|G|H`,
   `election_subtypes[]=PE&PR&GE&GR`, `limit=40`). The response is a
   server-rendered HTML table fragment; parsed with selectolax.
   Races: US Senate, Governor, US House (state + `/district-N`, at-large states
   have no district segment).
   Fields: date (single column), poll type, pollster, sample + population from
   the metadata line, candidate surname + percentage, party from the row's
   `fill-D` / `fill-R` / `fill-I` class, and the poll's article URL.
   Verified: 2026-10-27 (fixtures `tests/fixtures/two70win/`).
   `scrape = True`, `enabled_by_default = False`.
   Limits: the endpoint rejects any `limit` other than 40 and any partial
   `election_subtypes` list (404), so each office is one request returning the
   40 newest general-election polls nationwide -- a state with no recent
   national-level coverage can be missing. Candidates are surnames as printed
   ("Collins *" for the incumbent). 270toWin shows no sponsor / partisan marker
   in this fragment. The single date column is used as `end_date` with
   `start_date = None`; note that Wikipedia's editors document 270toWin dates as
   *publication* dates, so cross-source de-duplication should be tolerant of a
   few days of drift.

3. wikipedia  -- `electionwatch/sources/polls/wikipedia.py`
   Method: MediaWiki API, `action=parse` with `prop=text|wikitext&redirects=1`,
   one call per race article. HTML supplies the tables (selectolax); the
   wikitext supplies a candidate -> party map built from each party's primary
   section ("Republican primary", "Democratic primary", "Libertarian
   convention", "Independents", ...).
   Races: 2026 Senate (`2026 United States Senate election in <State>`, special
   variants), 2026 Governor (`2026 <State> gubernatorial election`). State
   legislative / other statewide / House articles are not covered.
   Fields: pollster (+ "(R)"/"(D)" marker -> `partisan`, so internals and
   partisan-sponsored polls are kept and flagged), fieldwork window, sample and
   population, candidate name + percentage, party from the column header
   ("Ken Paxton (R)") or the primary-section map, article URL.
   Verified: 2026-10-27 (fixture `tests/fixtures/wikipedia/`).
   `scrape = True`, `enabled_by_default = False`.
   Limits: only tables under the article's level-2 "General election" heading
   are read (primary/runoff polls are skipped), aggregate-poll and predictions
   tables are rejected by their headers, and rows that share a `rowspan`
   pollster group -- the "with leaners" sub-samples -- are skipped because only
   the group's first row has one cell per column. Bipartisan poll pairs
   ("Beacon Research (D)/Shaw & Co. Research (R)") are deliberately **not**
   flagged partisan; a sponsor is not recorded separately, because Wikipedia
   lists the pollster in that cell.

Considered and not used (checked 2026-10-27)
--------------------------------------------

- RealClearPolling (`/polls/senate/general/2026/<state>`): returns HTTP 403
  behind a Cloudflare challenge to non-browser clients. Not bypassed, so no
  adapter.
- Race to the WH (`/senate/26polls`, `/allpolls`): the poll tables are an
  embedded Infogram iframe rendered by JavaScript; the HTML contains no poll
  rows and there is no public JSON endpoint. Not scraped.
- Decision Desk HQ polls (`polls.decisiondeskhq.com`): HTTP 429 from this
  network, and no documented public API. Not scraped.
- FiftyPlusOne (`fiftyplusone.news/polls/senate/general/<state>`): pages load,
  but the data is rendered client-side; no adapter parses it yet.
- FiveThirtyEight: the project is frozen (no 2026 polling data).

Housekeeping
------------

- `pollster` and `end_date` are kept exactly as the source prints them so the
  bot can de-duplicate across sources; de-duplication itself is out of scope
  here. `source` is always the adapter name.
- Nothing reads an API key; `env` is empty for all three adapters.
- HTML sources send a descriptive User-Agent with a project URL, as Wikimedia requires.
- The poller refreshes polls for watched races, and races users asked about, every 30 minutes
  (longer if a host's request budget needs it) and stores them in MongoDB; `/polls` reads only
  the stored polls.
- The `YEAR = 2026` constants in `two70win.py` and `wikipedia.py` pin the
  adapters to the Nov 3 2026 midterms; change them together with the template's
  election year.
