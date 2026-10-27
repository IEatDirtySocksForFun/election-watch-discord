# election-watch-discord

A template Discord bot that follows US election night for your server: it posts results for the
races you mark as important, race calls from the outlets you pick, turnout, exit-poll links and
prediction-market swings, and gives every member a private race navigator. It is built for the
**November 3, 2026 midterms** and meant to be forked.

The bot is plain deterministic code. **No LLMs in the product**: no OpenAI, Anthropic, DeepSeek,
Kimi, Ollama or any other model API, at runtime or as a dependency. Summaries (exit polls etc.)
come from parsing, not from a model.

## What it posts

- **Results** for each important race, on a timer, only when the numbers changed:
  - admin never set timing: every 5 minutes, if results changed;
  - admin set an interval: every N minutes, if results changed;
  - admin chose "every update": as soon as results change.
- **Race calls** (AP, NBC, CNN, FOX, DDHQ, VoteHub, ... you choose), immediately.
- **Turnout** inside the results post, when the source publishes it.
- **Exit polls**: once per race and outlet.
- **Odds swings** (Kalshi, Polymarket): off by default; alerts when an implied probability moves
  by at least the threshold within the look-back window (default 50 points in 60 minutes).

## Set up your own copy

1. **Create the app** at <https://discord.com/developers/applications> -> New Application -> Bot ->
   Reset Token (that is your `DISCORD_TOKEN`). No privileged intents are needed: the bot only uses
   slash commands and the default `Guilds` intent.
2. **Invite it**: OAuth2 -> URL Generator, scopes `bot` and `applications.commands`, bot
   permissions **View Channels, Send Messages, Embed Links, Read Message History**. Open the URL
   and pick your server.
3. **Install**: Python 3.11+.
   ```sh
   git clone <your fork> && cd election-watch-discord
   python -m venv .venv && . .venv/bin/activate
   pip install -e .[dev]
   ```
4. **Configure** (`cp .env.example .env`, then edit). Both `KEY=value` and `KEY: value` lines work;
   set `EW_ENV_FILES=a.env,b.env` to read other files. Real environment variables win.

   | variable | |
   |---|---|
   | `DISCORD_TOKEN` | bot token (required) |
   | `MONGODB_URI` | `mongodb://127.0.0.1:27017` or your Atlas `mongodb+srv://...` URI |
   | `MONGODB_DB` | database name, default `electionwatch` |
   | `DEV_GUILD_ID` | optional: register slash commands instantly in one test server |
   | `AP_API_KEY`, `DDHQ_API_KEY` | optional paid sources; adapters without a key are skipped |

   Never commit `.env`; `.gitignore` already excludes it.
5. **MongoDB**: either local (`docker run -d -p 27017:27017 mongo:7`, or your distro's package)
   or a free MongoDB Atlas cluster (create a database user, allow your server's IP under Network
   Access, copy the `mongodb+srv://` connection string into `MONGODB_URI`). Collections and
   indexes are created on first start.
6. **Check and run**:
   ```sh
   python -m electionwatch --check   # validates env, Mongo and config/races.yaml, then exits
   python -m electionwatch           # starts the bot
   ```
7. In Discord: `/setup channel:#election-night`, then `/watch add` the races you care about.

## Race keys (`config/races.yaml`)

`config/races.yaml` is the default watch list for new servers; admins override it per server with
`/watch`. Keys:

| key | meaning |
|---|---|
| `NC-01`, `NC-1`, `AK-AL` | US House district (`AL` = at-large) |
| `TX SEN` | US Senate; `OH SEN S` = special election |
| `GA GOV`, `AZ AG` | statewide office: `GOV LTGOV AG SOS TREAS AUD COMP SUPT AGCOM INSCOM LANDCOM RRC` |
| `TX SD-05`, `TX HD-121` | state senate / state house district |
| `TX LOCAL harris-county-judge` | local race, free-form slug the results adapter understands |

Long names work too: `Texas Senate`, `North Carolina Congressional District 1`, `Georgia Governor`.

```yaml
election_date: 2026-11-03
important:
  - key: TX SEN
    label: Texas Senate
  - key: NC-01
```

## Slash commands

Admin commands need **Manage Server**, or the admin role if one is configured. The navigator,
`/faq` and `/settings` replies are ephemeral (only you see them).

| command | who | what |
|---|---|---|
| `/setup channel:<channel>` | admin | where the bot posts |
| `/watch add <race>` / `/watch remove <race>` / `/watch list` | admin | important races (autocomplete) |
| `/posting mode:<default\|interval\|immediate> [minutes]` | admin | results timing (see above) |
| `/calls outlets:<...> on\|off` | admin | which outlets' calls are posted |
| `/odds on\|off [threshold] [window]` | admin | prediction-market swing alerts |
| `/turnout on\|off` | admin | turnout lines in results posts |
| `/exitpolls on\|off` | admin | exit-poll posts |
| `/pause`, `/resume` | admin | stop / restart all posting |
| `/settings` | admin | show this server's settings |
| `/races` | everyone | private navigator: Congressional / Senate / State / Local tabs, state picker, race picker; remembers your last state and tab |
| `/favorite add <race>` / `/favorite remove <race>` | everyone | pin races in your navigator |
| `/faq [state]` | everyone | when polls close, what is counted first, mail deadlines, typical call times |

## Adding a source adapter

One module per adapter under `electionwatch/sources/<kind>/`, registered with `@register`; see
`electionwatch/sources/base.py` for the contract and [docs/customizing.md](docs/customizing.md)
for a worked example. Short version: keep parsing a pure function, test it against a trimmed
real response in `tests/fixtures/<adapter>/` (with a README.txt giving URL and date), never raise
on a single bad race, and read API keys from the environment.

**Scrapers are off by default.** Adapters that scrape sites without an API set `scrape = True`
and `enabled_by_default = False`, send a descriptive User-Agent and poll at most once a minute.
Before turning one on, check that site's terms of use and robots.txt; you are responsible for how
your copy of the bot uses other people's websites.

## Testing with past elections (replay)

```sh
pytest -q                                                    # unit tests, no network
python tools/fetch_history.py --states PA,GA --years 2020,2024   # ~/Downloads/election-test-data
python tools/build_timelines.py --years 2020,2024            # synthetic nights where no real one exists
python -m replay.run --year 2024 --races "PA SEN,PA-01" --speed max
```

`replay.run` drives the real poller minute by minute with a fake clock, an in-memory store and a
recording poster (no Discord, no Mongo), adds a scripted race call and a forced odds swing per
race, and writes every post with its simulated time to `replay/out/<run>.md` plus a summary
(posts per race, interval rule respected, no duplicates). Details: [docs/testing.md](docs/testing.md).

## License

MIT.
