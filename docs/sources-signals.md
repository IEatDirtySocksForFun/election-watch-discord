# Signals package data sources

This package covers race calls, prediction-market odds, and exit polls for the
2026 U.S. midterms.

## Odds

| Market     | Method                                | Key needed | Verified |
| ---------- | ------------------------------------- | ---------- | -------- |
| `kalshi`   | Kalshi public market-data API (`/events/{series}-26`) | None | Yes (2026-10-27) |
| `polymarket` | Gamma API event discovery + CLOB-derived `outcomePrices` | None | Yes (2026-10-27) |

Both adapters map markets to `RaceKey` and emit `OddsSnapshot` rows with implied
probability (0-1). Kalshi uses series-ticker discovery with a small override
table (e.g. Alaska governor maps to `KXGOVAK`). Polymarket paginates active
events and filters titles.

The poller fetches odds for watched races (and races users asked about with `/odds`) each
minute, within the hosts' request budgets (Polymarket publishes 300 requests / 10 s for
`/markets`; Kalshi publishes only keyed tiers, so the bot assumes 10/s and uses half). Every
snapshot is stored; `/odds` reads the newest per outcome from MongoDB, and a race the refresh
found no market for says so instead of "queued".

## Odds swings

`electionwatch.swings.detect_swing(history, pts, window)` is a pure function
that returns the largest percentage-point move between any snapshot inside the
window and the latest snapshot.

## Race calls

| Outlet  | Method                                         | Key needed    | Verified |
| ------- | ---------------------------------------------- | ------------- | -------- |
| `ap`    | AP Elections API v3 `/elections`               | `AP_API_KEY`  | Schema only |
| `ddhq`  | DDHQ API v4 (endpoint/auth client-specific)    | `DDHQ_API_KEY`| Skeleton |
| `votehub`| Polling API only (`/polls`); no results endpoint | None        | No results API (2026-10-27) |
| `cnn`   | CNN politics JSON feed (`politics.api.cnn.io`) | None          | Yes (2024 fixture) |
| `nbc`   | NBC firecracker results JSON (`/firecracker/api/v2/national-results/...`) | None | Yes (2024 fixture) |
| `cbs`   | CBS election archive API (`races2/<year>/G?filter.officeCode=&filter.state=`) | None | Yes (2024 fixture) |
| `fox`   | Fox per-state feeds (`feeds-elections.foxnews.com/archive/.../<Office>/<ST>/...`) | None | Yes (2024 fixture) |
| `abc`   | ABC state-results widget HTML; data in `window['__abcnews__']` | None | Yes (2024 fixture) |
| `nyt`   | NYT results feed (none found)                  | None          | No (interactive page 403) |

Per-race vote counts: `cbs_results` (in `calls/cbs_results.py`) is a `ResultsSource`
over the same CBS `races2` JSON, a keyless national fallback for Senate/Governor/House
in every state (off by default). See `docs/research/source-sweep.md` for the AP/DDHQ
route research, API keys and per-state fallbacks.

All outlets other than AP/DDHQ are `scrape=True` and `enabled_by_default=False`.
Their parsers are tested against trimmed real 2024 responses saved in
`tests/fixtures/<outlet>/`; the URLs and capture dates are recorded in each
fixture directory's `README.txt`.

Call-time caveats: CNN uses `raceEmbargo.callsRelease` (falling back to
`voteTimestamp`); CBS and ABC use the race's `lastUpdated`; NBC uses the feed's
`lastModified`; Fox House feeds use `last_updated`, while Fox Senate/Governor
feeds expose no timestamp and fall back to observation time.

## Exit polls

| Outlet | Method                                              | Key needed | Verified |
| ------ | --------------------------------------------------- | ---------- | -------- |
| `cnn_exit_polls`  | Per-state/race page URL + optional `.json` sidecar | None | URL yes (2024) |
| `cbs_exit_polls`  | Per-state/race page URL + optional `.json` sidecar | None | URL yes (2024) |
| `nbc_exit_polls`  | National exit-poll page URL                        | None       | URL yes (2024) |
| `fox_exit_polls`  | National voter-analysis page URL                   | None       | URL yes (2024) |

NBC and Fox do not publish per-state exit-poll pages (those URLs 404), so all
races share the national page. State-level and other unsupported races return
no URL.

When a JSON sidecar is returned, the adapter builds a 1-3 line summary from the
top demographic splits without any LLM/AI summarisation. No outlet currently
serves such a sidecar, so summaries are normally `None`.

## Results fallbacks

National `ResultsSource` adapters that re-use the call-outlet feeds above for
per-candidate vote counts. All are `scrape=True` and `enabled_by_default=False`.

| Source | Base feed (2024) | Coverage | Votes / pct | Reporting | Verified |
| ------ | ---------------- | -------- | ----------- | --------- | -------- |
| `cbs_results` | `api-election.cbsnews.com/archive/api/public/races2/2024/G` | All states; Senate, Governor, House | `vote` / `votePct` | `pctIn` (expected vote %) | Yes (2026-10-27) |
| `nbc_results` | `nbcnews.com/firecracker/api/v2/national-results/2024-elections/{office}-results` | All states; Senate, Governor, House | `vote` / `percentVote` | `percentIn` | Yes (2026-10-27) |
| `fox_results` | `feeds-elections.foxnews.com/archive/politics/elections/2024/3/2024_Generals/{Office}/{ST}/state_level_results/file.json` | All states; Senate, Governor, House | `votes.count` / `votes.percentage` | `stateTotals.precinctsReporting` / `expectedPercentage` | Yes (2026-10-27) |
| `abc_results` | `abcnews.go.com/widgets/generalstateresults?chamber={chamber}&stateAbbrev={st}&year=2024&type=general` | All states; Senate, Governor, House | `rawVotes` / `rawPercent` | `reporting.value` | Yes (2026-10-27) |
| `cnn_results` | `politics.api.cnn.io/results/race/2024-{code}.json` | All states; Senate, Governor, House | `voteNum` / `votePercentNum` | `percentReporting` | Yes (2026-10-27) |

CBS, NBC and CNN 2026 archive URLs return 404/empty as of 2026-10-27, so the
fallbacks currently target the 2024 cycle. Fox has 2026 placeholder files with
zero votes; ABC's 2026 widget is not yet populated. The `winner` flag is always
kept `False` because outlet projections belong in the call adapters, not official
results.
