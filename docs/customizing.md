# Customizing a fork

How to change the defaults, the refresh schedule, the command data mode and the data sources
of a copy of election-watch-discord.

- [Watch list and defaults](#watch-list-and-defaults)
- [Refresh rates and limits](#refresh-rates-and-limits)
  - [Database or live commands](#database-or-live-commands)
- [Adding a results adapter](#adding-a-results-adapter)
- [Trying a change against a past election](#trying-a-change-against-a-past-election)
- [Rules worth keeping](#rules-worth-keeping)

---

## Watch list and defaults

- `config/races.yaml`: the default important races for new servers (key format in the README).
  Each server changes its own list with `/watch`. The file uses the strict key format
  (`PA-08`, `TX SEN`); looser spellings such as `pa 08` work only in slash commands.
- `electionwatch/settings.py`: defaults for new servers (posting interval, call outlets,
  betting-market alert threshold and window). These are shared contracts: change values, not
  field names, because existing servers store settings under those names.
- Betting-market alert categories (`ODDS_CATEGORIES`, mapped from a race key by
  `odds_category`): `senate`, `house`, `statewide`, `state-senate`, `state-house`, `mayor` and
  `local`. Servers store overrides by category id (`/bettingalerts category:...`), so add new
  ids rather than renaming old ones.

---

## Refresh rates and limits

The bot asks each data source on a schedule and stores the answers in MongoDB.
`/bettingmarkets`, `/polls`, `/earlyvote` and `/races` read only the database, unless
[live commands](#database-or-live-commands) are switched on. The schedule is computed so the
bot uses at most half of each host's published rate limit per hour
(`electionwatch/ratelimit.py`):

```text
allowance per hour = host's limit per hour / EW_RATE_SAFETY        (default 2)
demand             = sum over the sources on the host of (requests per refresh / floor)
stretch            = demand / (90% of the allowance), at least 1
refresh interval   = floor for the source's kind x the largest stretch of its hosts
```

Requests are counted per host as each one happens, so a bigger watch list slows sources down rather
than sending more requests. Sources sharing a host (the county adapters on Clarity; civicAPI
results, calls and early vote) are slowed by the same factor, so the host is used evenly through
the hour instead of being spent early. A refresh also only starts while its hosts have room in
the last hour, counting the requests of refreshes already running.

Floors (the fastest each kind is refreshed):

- results, calls, exit polls and betting markets: 60 s
- polls: 30 min
- early vote: 1 h
- race lists (for `/races` and autocomplete): 1 h per state, at most 3 states per source per
  refresh

Published limits used by default (researched 2026-10-28):

| host                                       | limit      | note                                                                    |
| ------------------------------------------ | ---------- | ----------------------------------------------------------------------- |
| gamma-api.polymarket.com                   | 300 / 10 s | Polymarket docs (`/markets`)                                            |
| api.elections.kalshi.com                   | 10 / s     | only a keyed tier (20/s) is published; half of it                       |
| en.wikipedia.org                           | 200 / min  | needs a User-Agent with contact info (the bot sends one)                |
| api.ap.org                                 | 10 / s     | typical key quota; check the key's quota with AP                        |
| results.enr.clarityelections.com           | 4 / s      | nothing published; static files on a CDN shared by many county adapters |
| civicapi.org, api.votehub.com, other hosts | 1 / s      | nothing published                                                       |
| CNN, NBC, Fox, CBS, ABC feeds, 270toWin    | 10 / min   | undocumented; CNN and NBC block bursts                                  |

User commands follow the same rule: a refresh is the most that can change between two looks, so
a user can repeat a command for the same race or state once every **2 refresh intervals** of
the data behind it (betting markets: 2 min), and a server can `/refresh` once every 2 intervals
of what it refreshes.

| setting                                                     | where          | meaning                                                                                                                                                                                                                                                                |
| ----------------------------------------------------------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `EW_RATE_SAFETY=2`                                          | env            | use at most 1/N of each published limit                                                                                                                                                                                                                                |
| `EW_RATE_LIMITS=civicapi.org=600/60,...`                    | env            | custom limits, `host=requests/seconds`                                                                                                                                                                                                                                 |
| `EW_INTERVALS=odds=120,polls=1800,polymarket=300`           | env            | fixed intervals in seconds, by kind (`results`, `calls`, `exitpolls`, `odds`, `polls`, `earlyvote`, `racelists`), source (`polymarket`) or source and kind (`civicapi:calls`); the most specific wins; replaces the computed value, the hourly allowance still applies |
| `EW_USER_COOLDOWN`, `EW_USER_HOURLY`, `EW_REFRESH_COOLDOWN` | env            | operator defaults for every server (seconds; commands per user per hour, 0 = no cap)                                                                                                                                                                                   |
| `EW_DEMAND_MAX=200`, `EW_DEMAND_HOURS=24`                   | env            | races users asked about that no server watches: how many are refreshed, and for how long after the last request                                                                                                                                                        |
| `/limits user_cooldown user_hourly refresh_cooldown`        | Discord, admin | this server's values; `-1` returns to the default                                                                                                                                                                                                                      |

`/limits` with no options replies with an embed of the current intervals, each source's
requests per refresh and the usage of its hosts against the allowance.

### Database or live commands

By default (`EW_COMMAND_DATA=db`), commands never call a source: each one shows what the
background refresh stored, and a race no server watches is queued for the next refresh ("try
again in a few minutes").

With `EW_COMMAND_DATA=live`, `/bettingmarkets`, `/polls`, `/earlyvote` and the `/races`
browser (state race lists and the chosen race's result) ask the source directly when the stored
answer is older than `EW_LIVE_MAX_AGE`, then save the answer to the database as a refresh
would. Live mode stays polite:

- An answer younger than `EW_LIVE_MAX_AGE` seconds (default 60) is reused, so many people asking
  about one race share a single fetch.
- Every request counts against the same hourly allowance as the background refresh. When a site
  has no room left, is failing, or is slow, the command answers from the database instead.
- Requests to one source go one at a time, spaced by an even share of the source's allowance,
  and no command waits more than `EW_LIVE_TIMEOUT` seconds (default 10) for one.
- Race, state and county autocomplete always reads the database (Discord allows under
  3 seconds).
- The per-user cooldown becomes 2 x `EW_LIVE_MAX_AGE` when that is shorter than 2 refreshes.

| setting           | default | meaning                                                      |
| ----------------- | ------- | ------------------------------------------------------------ |
| `EW_COMMAND_DATA` | `db`    | `db`: commands read the database; `live`: commands may fetch |
| `EW_LIVE_MAX_AGE` | `60`    | seconds a stored answer counts as fresh in live mode         |
| `EW_LIVE_TIMEOUT` | `10`    | seconds a live fetch (or the wait for one) may take          |

> **Note:** `/limits` shows which mode the bot runs in.

---

## Adding a results adapter

Create `electionwatch/sources/results/<name>.py`:

```python
from electionwatch.models import CandidateResult, RaceResult
from electionwatch.racekeys import parse
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

URL = "https://results.example.gov/api/2026-general.json"


def parse_results(payload: dict, fetched_at) -> list[RaceResult]:
    """Pure: payload -> results. Tested against tests/fixtures/example_sos/."""
    out = []
    for race in payload["races"]:
        try:
            key = parse(race["key"])
        except ValueError:
            continue  # never fail the batch for one odd race
        out.append(
            RaceResult(
                key=key,
                source="example_sos",
                fetched_at=fetched_at,
                url=race.get("url"),
                candidates=[CandidateResult(c["name"], c.get("party"), int(c["votes"])) for c in race["candidates"]],
                precincts_reporting=race.get("reporting"),
                precincts_total=race.get("precincts"),
            ).finalize()
        )
    return out


@register
class ExampleSos(ResultsSource):
    name = "example_sos"
    states = frozenset({"EX"})
    # scrape = True; enabled_by_default = False   <- for HTML scrapers of sites without an API
    # env = ("EXAMPLE_API_KEY",)                   <- for keyed APIs; skipped when unset

    async def fetch(self, keys, client):
        r = await client.get(URL)
        if r.status_code >= 500:
            raise SourceUnavailable(f"{self.name}: HTTP {r.status_code}")
        from datetime import datetime, timezone

        wanted = {str(k) for k in keys}
        return [x for x in parse_results(r.json(), datetime.now(timezone.utc)) if str(x.key) in wanted]
```

Then save a trimmed real response to `tests/fixtures/example_sos/` with a `README.txt` (URL +
date fetched) and test `parse_results` against it. Call, odds and exit-poll adapters work the same
way with `CallSource.fetch_calls`, `OddsSource.fetch_odds` and `ExitPollSource.fetch_exit_polls`.
Adapters are found automatically (`electionwatch.sources.base.load_all`).

---

## Trying a change against a past election

Run the new adapter's parser over a saved response, or replay a whole night through the poller:
see [testing.md](testing.md). A replay is the quickest way to see how a change to post formatting
or timing looks across a real election night.

---

## Rules worth keeping

- No LLM or AI calls in the product, and no model SDKs as dependencies.
- No secrets in the repo; everything sensitive comes from the environment.
- Scrapers off by default, descriptive User-Agent.
- Sources are called on budget: by the background refresh, and by commands only in live mode.
- No source gets past a bot check; a blocked host is a closed door.

---

## See also

- [testing.md](testing.md): tests, replays and command simulations
- [sources-results.md](sources-results.md): results adapters and the feeds behind each one
- [sources-signals.md](sources-signals.md): race calls, betting markets and exit polls
- [sources-polls.md](sources-polls.md): poll sources
- [../AGENTS.md](../AGENTS.md): contribution rules
