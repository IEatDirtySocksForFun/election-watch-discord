# Testing

Nothing here touches the network. [`tests/README.md`](../tests/README.md) has the quick start and
the per-area details (what each test file covers, how to add a test for a new adapter, the Windows
commands without make). This page is the longer story: what the tools do and how to run the bigger
checks.

## Quick start

```sh
make install      # .venv with the package and its dev dependencies
make test         # unit tests: no network, no MongoDB (~20 s)
make sim          # election-night command simulation on bundled data (~15 s)
```

Make is optional: `python -m pytest -q` and `python -m replay.command_sim --quick` do the same.
On Windows without make, tests/README.md lists the equivalent commands.

## What the unit tests cover

- **Adapters**, one test file per source, against trimmed real responses in
  `tests/fixtures/<adapter>/`. Each fixture directory has a README.txt with the source and the
  fetch date.
- **The bot's commands** and the `/races` navigator, through fake interactions: the admin matrix
  covers permissions and persisted settings, the data matrix covers every read-only command, and
  `tests/test_navigator_matrix.py` clicks every tab and state.
- **Discord payloads**: `tests/test_interaction_payloads.py` feeds gateway-shaped interaction
  payloads through the real discord.py dispatch and asserts on the JSON Discord would receive
  (response type and the ephemeral flag), so ephemeral replies are proven, not assumed.
- **Request budgets**: `tests/test_ratelimit.py`, against the budgets in
  `electionwatch/ratelimit.py`.
- **The store**: `tests/test_mongo_store.py` runs one behaviour suite against `MemoryStore`,
  `MongoStore` with a fake pymongo client, and a real MongoDB when one is configured.
- **Replay and tooling**: `tests/test_replay.py`, `tests/test_replay_tools.py` and
  `tests/test_command_sim.py` against `tests/fixtures/replay/`.

`make test` never needs MongoDB. To include the real-server cases:

```sh
docker run -d -p 27017:27017 mongo:8.0    # one throwaway server
make test-mongo
```

`make test-mongo` uses `EW_TEST_MONGO_URI` if you set it, otherwise `mongodb://127.0.0.1:27017`,
pings the server first and stops with a reminder if nothing answers. Without the variable, the
real-server cases skip. CI (`.github/workflows/ci.yml`) runs `pytest -q` on Python 3.11, 3.12 and
3.13 with no MongoDB.

## Historical data

`tools/fetch_history.py [DATA_DIR]` (default `~/Downloads/election-test-data`, outside the repo;
never commit it) downloads past results:

```
<data>/<year>/<ST>/<level>.csv       race_key,candidate,party,votes,county
<data>/<year>/manifest.json          per state: source + URL + race count per level, and notes
<data>/timelines/<year>/<KEY>.jsonl  real NYT election-night series
<data>/_cache/                       raw downloads; reruns reuse them
```

- `<year>`: `2018`, `2020`, `2022`, `2024`, `2024-primary`. `<level>`: `senate`, `house`,
  `governor` (statewide executive offices that have a race key), `state` (legislature), `local`
  (everything else: county and city offices, judges, other statewide offices, measures).
- Options: `--states PA,GA`, `--years 2020,2024`, `--force` (redo states already in the
  manifest), `--no-timelines`, `--delay` (seconds between requests, min 1). Set `GITHUB_TOKEN`
  to raise the GitHub API limit (60 requests/hour without it).
- Sources per (year, state, level), first with data wins:
  1. MIT MEDSL precinct returns (GitHub `MEDSL/<year>-elections-official`), summed to county.
     The 2020 files live on Harvard Dataverse behind a guestbook form, so the script cannot
     download them; the manifest notes where to drop the file if you download it by hand.
  2. MEDSL U.S. Senate statewide returns (Harvard Dataverse), county left empty.
  3. OpenElections county or precinct CSVs (`openelections/openelections-data-<st>`); for each
     election date the richest file per level is used. The only source for 2024 primaries.
- File names of race keys use `_` for spaces: `PA_SEN.jsonl`, `NC-01.jsonl`, `TX_SD-05.jsonl`.
- Races whose district cannot form a valid key (e.g. Vermont's named house districts) become
  `ST LOCAL <slug>` inside the level file they belong to.

Real election-night series: NYT published per-race time series for the 2020 race pages
(Senate, governor) and the 2024 results pages (U.S. Senate, governor). Those are written with
`"synthetic": false`; 2020 snapshots rebuild candidate votes from NYT's vote shares, so they can be
off by a vote or two. No verified public series was found for 2018 or 2022.

`tools/build_timelines.py [DATA_DIR] --years 2022 --levels senate,governor,house` fills in the
rest: every county releases early vote, election-day vote and mail in the state's count order
(from `electionwatch/faq/states.yaml` `count_order` if present) over about 6 hours after 8 PM ET,
with seeded jitter, ending exactly on the certified county totals. Lines say `"synthetic": true`.
Real files are never overwritten; `--force` rebuilds synthetic ones.

## Replay

```sh
python -m replay.run --year 2024 --races "PA SEN,PA-01" --speed max
python -m replay.run --year 2020 --mode interval --interval 15 --max-hours 8
```

- `ReplaySource` (registered as results source `replay`) serves the snapshot current at the
  `FakeClock` time. `ScriptedCallSource` calls each race for its final leader once 95% of the
  expected vote is in. `ScriptedOddsSource` turns the running margin into a toy implied
  probability and forces one 20-to-80-point swing an hour into each race's night.
- The run starts a minute before the first snapshot and ticks `Poller.tick()` once per simulated
  minute until 30 minutes after the last one (at most `--max-hours`, default 12).
  `--speed 60` plays one simulated minute per second; `max` runs flat out.
- Output: `replay/out/<year>-<races>.md` with every post and its simulated time, then a per-race
  table and the checks (results interval respected, no identical consecutive results posts, no
  repeated call posts). Exit code 1 if a check fails, 2 if the poller or the data is missing.
- Test it without data: `python -m pytest -q tests/test_replay.py` runs the tools against the
  four-snapshot fixture timeline in `tests/fixtures/replay/`.

## Command simulations

```sh
python -m replay.command_sim --quick          # bundled data, ~15 s, no history download
python -m replay.command_sim --list
python -m replay.command_sim --years 2014,2016,2018,2020,2022,2024 --races top
python -m replay.command_sim --years 2024 --races all --scenarios latency,latencymongo \
    --mongo-uri mongodb://127.0.0.1:27017 --mongod-bin "$(command -v mongod)"
```

A replay night runs through the real poller of a real (never started) `ElectionBot`; at scripted
times the real slash-command callbacks run with fake interactions (admins, members, a delegated
admin role). Seventeen scenarios check the effect on the posts: pause/resume, posting modes, watch
changes, call outlets, turnout and exit polls, odds alerts, a channel change, non-admins trying
every admin command, read-only commands under load (memory and MongoDB), two servers, MongoDB
errors and a mongod restart, a 30-minute results outage (the source's last answer stands in for
10 minutes, then fallbacks), the admin role, invalid input and posting changes while paused.
Request budgets and cooldowns run on the replay's clock. Output: `replay/out/sims/<stamp>/`.

`--quick` (what `make sim` runs) skips the history data entirely: it builds a small hand-made night
in memory (five Senate races, a snapshot every two minutes from 8 PM to 1 AM ET) and runs twelve
scenarios chosen to work on it (the MongoDB, mongod, `watch` and `latency` ones are left out).
`make sim-full YEARS=2022,2024` runs the full matrix on the timelines from
`tools/fetch_history.py`; the three MongoDB scenarios need a server (see the command above) and the
restart test needs a mongod binary.

## Live checks

- `python tools/health_check.py`: asks every results adapter for each state it covers and prints
  OK / EMPTY / UNAVAILABLE / ERROR / TIMEOUT (civicAPI: TEST until real returns arrive).
- `python tools/live_command_check.py --guild <guild-id> --channel <channel-id>`: syncs the
  commands to a test server and posts every embed, view and text the commands can produce into a
  thread, so Discord itself validates the payloads. Ephemeral delivery is proven at payload level
  by `tests/test_interaction_payloads.py`.
- `python -m replay.discord_live --year 2022 --races "PA SEN" --guild <guild-id> --channel <channel-id>`:
  a whole replay night posted into a real channel.
