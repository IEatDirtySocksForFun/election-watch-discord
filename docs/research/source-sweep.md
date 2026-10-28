# Source research: paid feeds and national fallbacks

Research snapshot dated 2026-10-27 for the 2026-11-03 general election. For the current
adapter list, see [docs/sources-results.md](../sources-results.md) and
[docs/sources-signals.md](../sources-signals.md).

Evidence labels used below:

- **[fixture]**: a real response is saved in `tests/fixtures/` and parsed by the tests.
- **[doc]**: provider documentation or an official notice says so.
- **[prior]**: observed in an earlier election cycle; check it again before relying on it.

A parser needs a real fixture. An interesting endpoint alone is not enough to ship an adapter.

## AP and DDHQ

### Associated Press

| Route                                                              | Public access                                                             | What it means for the bot                                            |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| AP Elections API v3 (`api.ap.org/v3/elections`)                    | Paid key, under contract [doc: [Elex docs](https://elex.readthedocs.io/)] | Use the existing `ap` adapter and set `AP_API_KEY`.                  |
| [Knight Election Hub](https://accounts.muckrock.com/election-hub/) | Free results graphics for qualifying newsrooms                            | Newsroom program, not a general-purpose data API.                    |
| AP results pages and member-outlet embeds                          | Public pages, licensed data [prior]                                       | Not implemented. Public access does not grant redistribution rights. |
| Elex / python-elections                                            | Open-source clients that still need an AP key                             | Another client for the same paid API.                                |

There is no free public AP results API. The outlet adapters already in the bot are the
alternative for race calls, and the national results adapters below are the alternative for
vote counts.

### Decision Desk HQ

| Route                                        | Public access                                                  | What it means for the bot                                                                |
| -------------------------------------------- | -------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| [DDHQ API](https://ddhq.io)                  | Paid, with client-specific endpoint and authentication details | The `ddhq` adapter is a skeleton and needs `DDHQ_API_KEY` plus the client's API details. |
| DDHQ widgets through the Knight Election Hub | Free for some qualifying newsrooms                             | Widgets, not a data API.                                                                 |
| DDHQ academic partnerships                   | By agreement                                                   | A possible route for an eligible institution, not a public API.                          |
| The Hill / NewsNation election hub           | Public pages containing licensed DDHQ data [prior]             | Not implemented. It would need a real capture and permission to redistribute the data.   |
| DDHQ polling API                             | Public documentation; access terms need checking               | Relevant to polls, not race calls.                                                       |

DDHQ has no public free results API either. Do not treat a widget or a news site's JSON as
permission to republish the underlying data.

## API keys and public data

Only enable a keyed source after reviewing its price, quota and redistribution terms.

| Source                              | Data                                         | Access                                                                                                                               | Bot support                                                |
| ----------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------- |
| AP Elections API                    | Results and calls                            | Paid key                                                                                                                             | `ap`, using `AP_API_KEY`                                   |
| DDHQ API                            | Results and calls                            | Paid key and client-specific setup                                                                                                   | `ddhq` skeleton, using `DDHQ_API_KEY`                      |
| Texas SOS election-night media feed | Official Texas results                       | Paid media feed; see the [public notice](https://www.texaspress.com/sites/texaspress.com/files/file_uploads/SOSGeneralMediaFeed.pdf) | No adapter; the feed specification is needed first         |
| Kalshi                              | Prediction-market prices                     | Keyless public market-data reads                                                                                                     | `kalshi` [fixture]                                         |
| Polymarket Gamma API                | Market prices                                | Keyless public reads                                                                                                                 | `polymarket` [fixture]                                     |
| VoteHub                             | Polls                                        | Free, keyless                                                                                                                        | `votehub` polls adapter                                    |
| civicAPI                            | Results and race calls                       | Free, keyless, attribution required                                                                                                  | `civicapi`, on by default for results and opt in for calls |
| OpenFEC                             | Candidate and finance data                   | Free key                                                                                                                             | No adapter                                                 |
| Open States / Plural                | Legislators and districts                    | Free tier                                                                                                                            | No adapter                                                 |
| Google Civic Information API        | Elections and voter information, not results | Free key                                                                                                                             | No adapter                                                 |
| Democracy Works Elections API       | Dates, deadlines and ballot information      | Paid or by request                                                                                                                   | No adapter                                                 |

There is no reason to sign up for a key until an adapter uses it.

## National results fallbacks

These adapters reuse outlet feeds for Senate, Governor and US House vote counts. They are
`scrape=True` and `enabled_by_default=False`. Outlet projections stay in the call adapters;
the results adapters do not mark winners themselves.

| Outlet | Results adapter | Coverage and fields                                        | Verification |
| ------ | --------------- | ---------------------------------------------------------- | ------------ |
| CBS    | `cbs_results`   | All states; votes, vote share and expected vote            | 2024 fixture |
| NBC    | `nbc_results`   | All states; votes, vote share and reporting percentage     | 2024 fixture |
| Fox    | `fox_results`   | All states; votes, vote share, precincts and expected vote | 2024 fixture |
| ABC    | `abc_results`   | All states; votes, vote share and reporting percentage     | 2024 fixture |
| CNN    | `cnn_results`   | All states; votes, vote share and reporting percentage     | 2024 fixture |

Enable one with `EW_ENABLED_SOURCES`, for example:

```sh
EW_ENABLED_SOURCES=cbs_results
```

Use `EW_SOURCE_STATES` to limit it to states that need a fallback. The 2026 URLs may not serve
live data until election night, so check them on election morning.

**Fox is a special case.** Its election-feed `robots.txt` disallows automated access. That is
why both `fox` and `fox_results` are opt in. Review the current rules before choosing to enable
them.

## Per-state research

The current state-by-state notes live in:

- [docs/sources-results.md](../sources-results.md) for the main results adapters and feed
  overrides.
- [docs/results/](../results/) for detailed statewide and county feed notes.
- [docs/state-coverage-plan.md](../state-coverage-plan.md) for states without a complete
  official feed.

Do not bypass a challenge, captcha, WAF or IP block to reach a feed. Use another official
publisher, civicAPI or a deliberately enabled national fallback.

## Texas on election night

`results.texas-election.com` can present a Cloudflare challenge to some networks. Do not try to
solve or bypass it. The available options are:

1. Use `tx_sos` where the state site serves its JSON normally.
2. Use the official county feeds through `tx_counties`. Their titles say
   `(partial: N counties)`, and a complete statewide result takes precedence.
3. Use the paid Texas SOS media feed if its terms fit your deployment. This needs a new
   adapter and the feed specification.
4. Enable a national results fallback, optionally limited to Texas with `EW_SOURCE_STATES`.

## Before election day

1. Run `python tools/health_check.py` with the watch list and sources you plan to use.
2. Confirm that runtime-discovered election ids resolve. Several states publish them only
   shortly before counting starts.
3. Check the live 2026 URL for each enabled national fallback.
4. Keep race-call sources and scrapers off unless you have chosen their terms and request
   costs deliberately.
