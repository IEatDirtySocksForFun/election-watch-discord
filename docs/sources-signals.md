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

## Odds swings

`electionwatch.swings.detect_swing(history, pts, window)` is a pure function
that returns the largest percentage-point move between any snapshot inside the
window and the latest snapshot.

## Race calls

| Outlet  | Method                                         | Key needed    | Verified |
| ------- | ---------------------------------------------- | ------------- | -------- |
| `ap`    | AP Elections API v3 `/elections`               | `AP_API_KEY`  | Schema only |
| `ddhq`  | DDHQ API v4 (endpoint/auth client-specific)    | `DDHQ_API_KEY`| Skeleton    |
| `votehub`| Scrape results page                           | None          | Skeleton    |
| `cnn`   | CNN politics JSON feed (`politics.api.cnn.io`) | None          | Yes (2024 fixture) |
| `cbs`   | CBS results JSON feed (assumed)                | None          | No          |
| `fox`   | Fox results JSON feed (assumed)                | None          | No          |
| `nbc`   | NBC results JSON feed (assumed)                | None          | No          |
| `abc`   | ABC results JSON feed (assumed)                | None          | No          |
| `nyt`   | NYT results JSON feed (assumed)                | None          | No          |

All network outlets except CNN are skeleton adapters: they expose a parse
function against a simple 2024 fixture and are `scrape=True`,
`enabled_by_default=False`. CNN's feed structure was verified against live 2024
race endpoints.

## Exit polls

| Outlet | Method                                              | Key needed | Verified |
| ------ | --------------------------------------------------- | ---------- | -------- |
| `cnn_exit_polls`  | Page URL + optional `.json` sidecar        | None       | URL unverified |
| `nbc_exit_polls`  | Page URL + optional `.json` sidecar        | None       | URL unverified |
| `cbs_exit_polls`  | Page URL + optional `.json` sidecar        | None       | URL unverified |
| `fox_exit_polls`  | Page URL + optional `.json` sidecar        | None       | URL unverified |

When a JSON sidecar is returned, the adapter builds a 1-3 line summary from the
top demographic splits without any LLM/AI summarisation.
