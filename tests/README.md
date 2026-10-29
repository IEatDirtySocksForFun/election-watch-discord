# Tests

Everything here runs offline against saved responses and fakes. Three commands cover the
everyday checks:

```bash
make install      # a .venv with the package and its dev dependencies
make test         # the whole unit suite: no network, no MongoDB (~20 s)
make sim          # one election night of slash-command changes on bundled data (~15 s)
```

No make available? See [Windows, without make](#windows-without-make), or run
`.venv/bin/python -m pytest -q` from the repository root.

> **Note:** CI (`.github/workflows/ci.yml`) runs `pip install -e .[dev]` and `pytest -q` on
> Python 3.11 and 3.12, with a MongoDB 7.0 service and `EW_TEST_MONGO_URI` set, so the
> real-server cases run there too.

- [What's in the suite](#whats-in-the-suite)
- [Fixtures](#fixtures)
- [Discord replies: ephemeral, and inside the limits](#discord-replies-ephemeral-and-inside-the-limits)
- [Command simulations](#command-simulations)
- [MongoDB store suite](#mongodb-store-suite)
- [Live checks](#live-checks)
- [Windows, without make](#windows-without-make)
- [Adding a test for a new source adapter](#adding-a-test-for-a-new-source-adapter)
- [What "no network in tests" means](#what-no-network-in-tests-means)

---

## What's in the suite

Tests are grouped by area, one file per area or source:

- **Contracts and helpers**: `test_core_contracts.py` (race keys, results, post scheduling),
  `test_typed_keys.py` (forgiving race and state spellings such as `pa 08` or `Penn. 8`),
  `test_parties.py`, `test_faq.py`, `test_swings.py`.
- **Results adapters**: `test_results_<source>.py`, one per state, county or national feed
  (`pa`, `ga`, `tx`, `clarity`, `enhanced_voting`, `ap`, `cnn`, ...), plus
  `test_results_common.py` and `test_results_fetch.py` for the shared fetch and parse paths.
- **Race calls**: `test_calls_*.py` (AP, CNN, Fox, NBC, ABC, CBS, civicAPI), with
  `test_calls_skeletons.py` covering DDHQ, VoteHub and NYT.
- **Polls, betting markets, exit polls, early vote**: `test_polls_*.py`, `test_odds_kalshi.py`,
  `test_odds_polymarket.py`, `test_odds_candidates.py` (candidate names in `/bettingmarkets`),
  `test_exitpolls.py`, `test_early_vote_source.py`, `test_early_vote_counties.py` (the county
  party split and the one-county view), with the civicAPI fake in
  `fake_civicapi_early_vote.py`.
- **The bot itself**: `test_bot_commands.py`, `test_bot_logic.py`, `test_bot_navigator.py`,
  `test_bot_menu.py` (`/menu` and the posted channel menu), `test_bot_poster.py`,
  `test_bot_config.py`, `test_bot_command_data.py`, `test_racelists.py`, `test_poller.py`, and
  the matrices `test_commands_admin_matrix.py`, `test_commands_data_matrix.py`,
  `test_commands_early_vote.py`, `test_navigator_matrix.py`.
- **Discord payloads**: `test_interaction_payloads.py`, with the fakes in
  `fake_interaction_admin.py` and `fake_interaction_data.py`.
- **Request budgets, cooldowns and live mode**: `test_ratelimit.py`, `test_live.py`.
- **Storage**: `test_mongo_store.py` (memory, fake pymongo and, when a server is available, real
  MongoDB).
- **Replay and tools**: `test_replay.py`, `test_replay_tools.py`, `test_command_sim.py`.

The tests load a separate watch list, `tests/fixtures/races.yaml`, through `EW_RACES_FILE` (set
in `conftest.py`), so editing the shipped `config/races.yaml` never breaks the suite.
`test_bot_config.py` still checks that the shipped file loads.

Run everything, one file, or one test:

```bash
python -m pytest -q
python -m pytest -q tests/test_poller.py
python -m pytest -q tests/test_calls_ap.py -k house
python -m pytest -q tests/test_mongo_store.py::test_server_selection_fails_fast_unless_the_uri_says_otherwise
python -m pytest -q -x            # stop at the first failure
```

Async tests are plain `async def test_...` functions; pytest-asyncio runs those in auto mode
(see `pyproject.toml`).

---

## Fixtures

`tests/fixtures/<adapter>/` holds trimmed copies of real responses. Every directory has a
`README.txt` with the origin (usually the URL), the fetch date, and what was trimmed;
`tests/fixtures/kalshi/README.txt` and `tests/fixtures/co_sos/co/README.txt` are good examples.
When a response is hand-made rather than real, the README says so.

`tests/fixtures/replay/` is the same idea for the replay tools: two trimmed MEDSL/OpenElections
CSVs, two trimmed NYT time-series files and a four-snapshot replay timeline.

> **Warning:** Keep new fixtures small (a handful of races, not a whole state) and never put a
> token, API key or cookie in one.

---

## Discord replies: ephemeral, and inside the limits

- `test_interaction_payloads.py` feeds gateway-shaped `INTERACTION_CREATE` payloads through the
  real discord.py parsing and command dispatch, then inspects the JSON body Discord would
  receive: the response type and the ephemeral flag. That proves ephemeral delivery at payload
  level, which a fake interaction object alone cannot.
- `fake_interaction_admin.py` and `fake_interaction_data.py` validate every reply against
  Discord's limits: 2000 characters of text, at most 10 embeds, embed title/description/field/
  footer totals, at most 25 fields, at most 5 view rows and 25 select options, and exactly one
  acknowledgement per interaction. `/faq`, `/settings` and `/limits` answer with embeds too,
  so the embed limits are checked for those replies as well.
- The command simulations (`limit_problems()` in `replay/command_sim.py`) apply the same checks
  to every reply recorded during a simulated night.

---

## Command simulations

`replay/command_sim.py` runs a replay night at full speed through the real poller and the real
slash-command callbacks, with fake interactions for an admin, a plain member, a delegated admin
role and a bystander. At scripted times it changes settings and checks the effect on the posts.

```bash
python -m replay.command_sim --list                 # every scenario
python -m replay.command_sim --quick                # bundled night, 12 scenarios, ~15 s
python -m replay.command_sim --quick --scenarios posting,nonadmin
```

`--quick` needs no downloaded history: it builds a small hand-made night in memory (five Senate
races, a snapshot every two minutes from 8 PM to 1 AM ET) and runs twelve scenarios chosen to
work on it, everything except the MongoDB, mongod, `watch` and `latency` ones. That is also what
`make sim` runs.

The full matrix uses real timelines from `tools/fetch_history.py` and takes much longer:

```bash
make sim-full YEARS=2022,2024
python -m replay.command_sim --years 2024 --races all --scenarios latency,latencymongo \
    --mongo-uri mongodb://127.0.0.1:27017 --mongod-bin "$(command -v mongod)"
```

Results land in `replay/out/sims/<stamp>/` (git-ignored): a PASS/FAIL matrix, a summary JSON and
one JSONL per scenario with every post and command.

---

## MongoDB store suite

`tests/test_mongo_store.py` runs the same store behaviour three ways: `MemoryStore`, `MongoStore`
against a fake pymongo client, and `MongoStore` against a real server. The admin-command matrix
also runs its mongo cases against a local server when one is reachable.

```bash
docker run -d -p 27017:27017 mongo:8.0     # one throwaway server
make test-mongo                            # the whole suite, mongo cases included
EW_TEST_MONGO_URI=mongodb://other-host:27017 make test-mongo
```

`make test-mongo` sets `EW_TEST_MONGO_URI` to `mongodb://127.0.0.1:27017` unless the variable is
already set, pings the server first and stops with a reminder if nothing answers. With no
server, the real-server cases skip and `make test` stays quick (when a local server happens to
be running, the admin matrix quietly uses it too). Each run uses random throwaway databases
(`ew_test_…`, `ew_admin_test_…`) and drops each one afterwards.

---

## Live checks

These need the network (and the second and third need a bot token), so these are tools, not
pytest cases:

```bash
python tools/health_check.py                    # every results adapter, every state: OK/EMPTY/...
EW_ENV_FILES=.env python tools/live_command_check.py \
    --guild 100000000000000001 --channel 100000000000000002
python -m replay.discord_live --year 2022 --races "PA SEN" --guild <guild-id> --channel <channel-id>
```

`health_check.py` asks each adapter for a few races and prints what came back.
`live_command_check.py` syncs the commands to a test server and posts every embed, view and text
the commands can produce, so Discord itself validates the payloads. `replay.discord_live` posts a
whole replayed night into a channel.

---

## Windows, without make

Command Prompt, from the repository root:

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest -q
set EW_TEST_MONGO_URI=mongodb://127.0.0.1:27017
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m replay.command_sim --quick
.venv\Scripts\python -m replay.command_sim --years 2022,2024 --races top
.venv\Scripts\python tools\health_check.py
```

PowerShell: same commands with `.venv\Scripts\python.exe`, and
`$env:EW_TEST_MONGO_URI="mongodb://127.0.0.1:27017"` instead of `set`.

---

## Adding a test for a new source adapter

1. Save a trimmed real response in `tests/fixtures/<adapter>/`. Trim long lists down to a few
   races, but keep the awkward parts: nulls, empty strings, odd party spellings, an error page.
   XML and CSV are fine too.
2. Write `tests/fixtures/<adapter>/README.txt`: the origin of the response (the URL), the fetch
   date, and what was trimmed. That file is the provenance record, so be exact. For a hand-made
   payload, say so instead.
3. Add `tests/test_results_<adapter>.py` (or `test_calls_<adapter>.py`) that calls the adapter's
   parse function on the fixture with a fixed `fetched_at`. Check race keys (including House and
   state-legislature keys), titles, candidates, votes, percentages and `total_votes`, plus the sad
   paths: empty payload, garbage payload, missing fields. Parsing must return `[]`, not raise.
4. To cover the HTTP layer, mock the URL with `respx` (see `test_ratelimit.py`) or pass a mock
   httpx client. Check the URL the adapter builds, the User-Agent, and that a 403/404 or an HTML
   error page comes back as `SourceUnavailable`.
5. If the adapter needs a key, assert its `env` tuple. Scrapers must have `scrape is True` and
   `enabled_by_default is False`, so no scraper gets turned on by accident.
6. Run it: `python -m pytest -q tests/test_results_<adapter>.py`.

---

## What "no network in tests" means

`pytest` never talks to the outside world. Every HTTP call the code under test makes is answered
from a fixture, by `respx` or by a fake client; Discord is never logged into; MongoDB is only used
when `EW_TEST_MONGO_URI` points at a server (or one answers on 127.0.0.1).

That is why adapters are tested through parse functions and saved bodies instead of live
requests: the suite must give the same answer on a plane, on CI, and in 2027 after a state
redesigns its website. When a test needs a real response that no fixture has, fetch it once,
trim it, save it with its README, and test against that.

---

## See also

- [docs/testing.md](../docs/testing.md): historical data, replays and live checks in depth
- [AGENTS.md](../AGENTS.md): contribution rules
