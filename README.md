# election-watch-discord

**A self-hosted Discord bot for US election night.** It posts live results for the races your
server cares about, race calls from the outlets you trust, turnout, and prediction-market swings.
Every member also gets a private race browser for odds, polls and a per-state voting FAQ. It is
built for the **November 3, 2026 midterms** and is meant to be forked and customized.

```
[#election-night]                                      Texas Senate
🔵 James Talarico (DEM)   4,104,331 · 50.6%
🔴 Ken Paxton (REP)       3,962,208 · 48.8%
🟡 Ted Brown (LIB)           49,770 · 0.6%
72.4% expected vote · 6,912/8,031 precincts
Turnout: 57.1% · 8,116,309/14,214,001 ballots
Source: tx_sos · Nov 04 03:41 UTC
```
<sub>Example post format (illustrative numbers).</sub>

- **No AI inside.** The bot is plain, deterministic Python. It makes no calls to OpenAI,
  Anthropic, DeepSeek, Kimi, Ollama or any other model, at runtime or as a dependency.
- **Your data stays yours.** It runs on your machine with your Discord token and your MongoDB.
- **Tested on real election nights.** A replay sandbox reruns 2014–2024 results minute by minute
  through the real bot, in Discord or offline.

---

## Contents
[Features](#features) · [Quick start](#quick-start) · [Recommended election-night setup](#recommended-election-night-setup) ·
[Coverage](#coverage-by-state) · [Limitations](#limitations-read-this) · [Slash commands](#slash-commands) ·
[Race keys](#race-keys) · [Party markers](#party-markers) · [Testing](#testing-with-past-elections) ·
[Extending](#adding-a-data-source) · [How it works](#how-it-works)

## Features

| | |
|---|---|
| **Results** | Official state and county election-night feeds for 40 states + DC, backed by [civicAPI](https://civicapi.org) for every state (on by default), plus optional national fallbacks from CBS, NBC, Fox, ABC and CNN. Shows the top 5 candidates, party markers, expected vote, precincts and turnout. |
| **Posting rule** | Per server: every 5 minutes if numbers changed (default), every *N* minutes if changed, or on every change. An update is never re-posted when it would look identical. |
| **Race calls** | From the outlets you choose: AP, DDHQ, CNN, NBC, CBS, Fox, ABC, and civicAPI's own decision desk (free, opt-in). Posted the moment they appear, with retractions. |
| **Odds** | Kalshi and Polymarket for every watched race. `/odds` shows them privately; optional alerts fire on big swings (default: 50 points in 60 minutes, adjustable). |
| **Polls** | `/polls` shows the latest 5 public polls for a race, internal and partisan polls tagged, merged from VoteHub, 270toWin and Wikipedia. |
| **Early vote** | `/earlyvote [state] [breakdown:party·method·age·county]` privately shows ballots cast before Election Day (mail returned + in person, requests, party, age, top counties, and the same point in the last comparable election) for the 46 states [civicAPI](https://civicapi.org/early-vote) tracks; no state = every state. Optional daily summary for watched states (`/earlyvote_posts on`, off by default). Data from civicAPI, credited with a link in every embed. |
| **Race browser** | `/races` is a private browser with Congressional, Senate, State and Local tabs and a state picker. Each race has Odds and Polls buttons, and it remembers your last view. |
| **Voting FAQ** | `/faq <state>` covers poll-closing times, what each state counts first and last, mail deadlines, recount thresholds and typical call times. All 50 states + DC are covered, each with official sources. |
| **Per-server settings** | Stored in MongoDB: channel, watched races, posting mode, outlets, alert thresholds and an admin role. |

## Quick start

**You need:** Python 3.11+, a Discord application, and MongoDB (local, or a free Atlas cluster).

1. **Create the bot.** Go to <https://discord.com/developers/applications>, choose *New Application*,
   then *Bot* → *Reset Token*. The token is your `DISCORD_TOKEN`. No privileged intents are needed.
2. **Invite it.** Under *OAuth2 → URL Generator*, pick the scopes `bot` and `applications.commands`
   and these permissions: **View Channels, Send Messages, Embed Links, Read Message History**.
   Open the generated URL and pick your server.
3. **Install.**
   ```sh
   git clone https://github.com/<you>/election-watch-discord && cd election-watch-discord
   python -m venv .venv && . .venv/bin/activate
   pip install -e .
   cp .env.example .env        # then fill it in (next step)
   ```
4. **Configure `.env`.** Both `KEY=value` and `KEY: value` work, and `EW_ENV_FILES=a.env,b.env` reads
   several files. Real environment variables take precedence over the files.

   | variable | required | meaning |
   |---|---|---|
   | `DISCORD_TOKEN` | yes | bot token |
   | `MONGODB_URI` | yes | `mongodb://127.0.0.1:27017` or an Atlas `mongodb+srv://…` URI |
   | `MONGODB_DB` | | database name (default `electionwatch`) |
   | `DEV_GUILD_ID` | | register slash commands instantly in one test server |
   | `ELECTION_DATE` | | election to follow (default `2026-11-03`) |
   | `EW_ENABLED_SOURCES` | | turn on optional sources (see below) |
   | `EW_DISABLED_SOURCES` | | turn off default sources |
   | `EW_SOURCE_STATES` | | limit a source to some states: `fox_results=TX,TN;cbs_results=AL` |
   | `AP_API_KEY`, `DDHQ_API_KEY` | | paid race-call feeds; the adapters are skipped without a key |

5. **Run.**
   ```sh
   python -m electionwatch --check   # validates the env, MongoDB and config/races.yaml, then exits
   python -m electionwatch           # starts the bot
   ```
6. **In Discord:** `/setup channel:#election-night`, then `/watch add` each race you care about.
   The default watch list is `config/races.yaml` (it ships with Texas Senate).

## Recommended election-night setup

Out of the box, the bot only reads official state feeds and APIs that are free to use.
Most people will want the national fallbacks and outlet race calls too. They read news sites'
public JSON, so they are **off by default**. Enabling them is your decision; check each site's
terms first. For example:

```sh
EW_ENABLED_SOURCES=cbs_results,fox_results,nbc,cbs,fox,abc,cnn_exit_polls,270towin,wikipedia
```

Without that line:
- **Results:** every state still has results through civicAPI (see below); the outlet
  fallbacks are a second opinion if civicAPI is down.
- **Race calls:** none are posted (AP and DDHQ need a paid key). civicAPI's free calls are
  opt-in: `/calls outlets:CIVICAPI enabled:on`.

CNN's call feed (`cnn`) is left out above: it asks once per race per minute and blocks quickly.
Fallback sources are only asked about races that no official feed answered completely.
- **Exit polls:** no exit-poll links are posted.

## Coverage by state

Status as of 2026-10-27, checked with `tools/health_check.py` against the 2024 general and the
2026 feeds.

| Coverage | States |
|---|---|
| **Official statewide feed** (the state's own election-night data) | AR, AZ, CA, CO, CT, DC, DE, FL, GA, HI, IA, ID, IL, IN, KS, LA, MD, MN, MT, NC, ND, NM, OH, OR, PA, RI, SC, SD, UT, VA, VT, WA, WV |
| **Official feed, opt-in scraper** | NE (`ne_sos`) |
| **Official county feeds only** (partial state) | TX (Harris, Tarrant, Bexar, Travis, Fort Bend + 15 more Clarity counties: about 49% of the 2024 vote; backup for the state site), MI (Ottawa), WI (Dane, opt-in), NJ (14 counties), MO (St. Louis Co.), NY (NYC, opt-in), TN (Shelby), AL (Madison) |
| **No official feed reachable**: civicAPI + national fallbacks | KY, MA, ME, MS*, NH, NV, OK, WY |
| **civicAPI** (free API, no key, on by default; every state, federal + statewide + legislature + local) | all 50 + DC; fills every gap above |
| **Known problems right now** | AK: the state site's TLS certificate chain fails verification. TX: results.texas-election.com sometimes returns a Cloudflare challenge. |

\* MS publishes certified results only, days after the election.

[civicAPI](https://civicapi.org) is a free, documented results API (no key; attribution
required, which the bot gives by linking every civicAPI result to its race page and naming the
source in the footer). It covers **every state + DC** down to county level, including
down-ballot races, so it fills the states above without an official feed. It ranks after every
official feed (`fallback = True`), so a working state feed still wins. civicAPI publishes test
data before election night (fake winners flagged `test_data`); the adapter drops it, and drops
any votes reported before the polls open. Its race calls are available as the `CIVICAPI`
outlet (`/calls outlets:CIVICAPI enabled:on`); they are off by default because no real race in
its archive carries a call time yet, so its decision desk could not be checked. Its 2024 archive
covers 14 states, so the replay tests don't use it.

Accuracy, checked 2026-10-27 against official feeds (full report in
[docs/civicapi-accuracy.md](docs/civicapi-accuracy.md)): VA 2025 statewide and all 100 House of
Delegates seats match within a few write-in votes (100 of 103 exact); NC 2024 matches in 184 of
191 races (a few real errors, e.g. one House district off by 10,000 votes); NJ 2025 Governor is
exact, but its Assembly races were left at election-night numbers (2–11% low). Winners agreed in
every reliable comparison. Treat civicAPI as election-night numbers, not certified totals.

With the national fallbacks on as well, every state + DC has a second source for Senate, Governor and US House.
A complete statewide result always wins over a `(partial: N counties)` county tally for the same
race. To use a fallback only where it is needed, narrow it with `EW_SOURCE_STATES`, e.g.
`EW_SOURCE_STATES=fox_results=AL,KY,MA,ME,MI,MO,MS,NH,NJ,NV,NY,OK,TN,TX,WI,WY`. The plan
and evidence for the states without a complete feed are in
[docs/state-coverage-plan.md](docs/state-coverage-plan.md).
Down-ballot races (state legislature, local) come from official state and county feeds and civicAPI.
Per-state details are in [docs/sources-results.md](docs/sources-results.md) and
[docs/results/](docs/results/).

## Limitations (read this)

- **2026 feeds aren't live yet.** Most states publish their 2026 election-night URLs or IDs
  only on, or just before, election day. The adapters discover them automatically where the
  state allows. Some need a value set by hand (`CLARITY_FEEDS`, `MT_EID`, `SD_EID`, the CA media
  base URL). Run `python tools/health_check.py` on election morning. Its civicAPI rows say
  TEST while civicAPI still serves pre-election test data; they must turn OK once returns arrive.
- **Feeds change without notice.** Adapters are tested against saved real responses. A state
  can still rename a field, move a URL or add a bot check on the night itself. One failing
  source never stops the others.
- **No source bypasses bot checks.** Sites behind Cloudflare, Imperva, Radware or a usage
  agreement (MA, KY, NV, NH, MN's main site, …) are not scraped. That is why some states rely on
  the national fallbacks.
- **AP and DDHQ cost money.** Their adapters are written to the documented APIs, but with no
  key they stay off. There is no free legitimate route to their calls; see
  [docs/research/source-sweep.md](docs/research/source-sweep.md).
- **Outlet call times are approximate.** NBC, CBS, Fox and ABC don't publish the moment of a call,
  so their posts use the feed's last-updated time.
- **Exit polls are links.** No outlet publishes exit-poll data in a form the bot can summarize, so
  those posts link to the outlet's page.
- **Turnout appears only where the feed publishes registered voters** (e.g. NC, GA, FL).
- **Territories:** race keys exist (`PR-AL`, `GU GOV`, …), but no territory has a results feed yet.
- **Multi-seat races:** posts show the top 5 candidates. In a race with more seats than that,
  the winners below 5th place aren't shown.
- **Discord rate limits:** one channel accepts about 5 messages every 5 seconds. A server
  watching dozens of races in "every update" mode will see posts queue up at peak. The default
  5-minute mode, or a few channels/servers, avoids that.
- **civicAPI is a third party.** It aggregates official returns but is not itself official, and
  it is one service: if it goes down on the night, the states without an official feed fall
  back to the outlet fallbacks (if enabled) or show nothing.
- **It's not an official source.** Numbers are only as good as the feeds. Calls come from the
  outlets that made them, not from the bot.

## Slash commands

Admin commands need **Manage Server**, or the admin role set in `/setup`. Replies marked
*private* are ephemeral: only the person who asked sees them.

| command | who | what |
|---|---|---|
| `/setup channel:<#channel> [admin_role]` | admin | where the bot posts, and who may configure it |
| `/watch add·remove·list <race>` | admin | the server's important races (with autocomplete) |
| `/posting mode:<default·interval·immediate> [minutes]` | admin | results timing |
| `/calls outlets:<…> on·off` | admin | which outlets' calls are posted |
| `/oddsalerts on·off [threshold] [window]` | admin | Kalshi / Polymarket swing alerts |
| `/turnout on·off`, `/exitpolls on·off` | admin | extra lines and posts |
| `/earlyvote_posts on·off` | admin | daily early-vote summary for watched states (off by default) |
| `/pause`, `/resume` | admin | stop or restart all posting |
| `/refresh [what:all·results·odds·polls·earlyvote]` | admin | fetch fresh data now instead of waiting for the next refresh (once per 2 refresh intervals per server; the hourly request allowance still applies) |
| `/limits [user_cooldown] [user_hourly] [refresh_cooldown]` | everyone sees · admin sets | refresh rates, request budgets and this server's command limits (`-1` = back to default) |
| `/settings` | everyone | this server's settings (private) |
| `/races` | everyone | private race browser with Odds and Polls buttons |
| `/odds [race]` | everyone | private Kalshi + Polymarket probabilities with 1 h / 24 h moves (read from the database; see [rate limits](docs/customizing.md#refresh-rates-and-limits)) |
| `/polls race:<race> [count] [internals]` | everyone | private latest polls, internals tagged |
| `/favorite add·remove <race>` | everyone | pin races in your browser |
| `/faq [state]` | everyone | private per-state election FAQ |
| `/earlyvote [state] [breakdown:party·method·age·county]` | everyone | private early-vote numbers from [civicAPI](https://civicapi.org/early-vote) (attribution: each embed links civicapi.org and names it in the footer) |

## Race keys

`config/races.yaml` holds the default watch list for new servers.

| key | race |
|---|---|
| `NC-01`, `AK-AL` | US House district (`AL` = at-large; single-seat states always use `AL`) |
| `TX SEN`, `OH SEN S` | US Senate; `S` = special election (2026: watch `OH SEN S` and `FL SEN S`) |
| `GA GOV`, `AZ AG` | statewide office: `GOV LTGOV AG SOS TREAS AUD COMP SUPT AGCOM INSCOM LANDCOM RRC` |
| `TX SD-05`, `TX HD-121` | state senate / state house district |
| `TX LOCAL harris-county-judge` | local race (slug as the results adapter publishes it) |
| `PR-AL`, `GU GOV` | territories |

Plain names work too: `Texas Senate`, `North Carolina Congressional District 1`.

## Party markers

🔵 Democratic · 🔴 Republican · 🟡 Libertarian · 🟢 Green (and state affiliates) · 📜 Constitution ·
each socialist party its own marker (✊ 🌹 ☭ 🚩 …) · white-nationalist / neo-Nazi parties
documented as such by reliable sources ⛓️ 🔒 🚔 · ⚪ independent or no party. 93 parties
are covered, including state parties and the abbreviations feeds print (e.g. `DFL`, `LBT`,
`WOR`, `DEMOCRATIC P`). The full table, with sources, is in [docs/parties.md](docs/parties.md).
Edit `electionwatch/data/parties.yaml` to change any marker.

## Testing with past elections

```sh
pytest -q                                          # 700+ unit tests, no network
python tools/health_check.py                       # which sources answer right now
python tools/fetch_history.py --years 2022,2024    # results by state -> ~/Downloads/election-test-data
python tools/build_timelines.py --years 2022       # synthesize election nights from county results
python -m replay.run --year 2022 --races "PA SEN,PA-07" --speed max      # offline, recorded posts
python -m replay.discord_live --year 2022 --races "$(…)" --post top-per-state \
    --speed 10 --guild <id> --channel <id> --thread "2022 replay"         # into a real channel
python -m replay.report_html --year 2022 --races "$(…)" --out night.html  # one page per run
```

The replay drives the real poller minute by minute with a fake clock. Races with NYT
election-night series use the real timeline; the rest are synthesized from certified county
results in a plausible reporting order. Race calls and odds in replays are scripted test
data. Details are in [docs/testing.md](docs/testing.md).

## Adding a data source

Write one module per adapter under `electionwatch/sources/<results|calls|odds|polls|exitpolls>/`
and register it with `@register`. The rules (contract in
[`sources/base.py`](electionwatch/sources/base.py), walkthrough in
[docs/customizing.md](docs/customizing.md)):

- Parsing is a pure function, tested against a trimmed real response in `tests/fixtures/<adapter>/`
  (with a README giving the URL and date).
- Never raise on one bad race. Raise `SourceUnavailable` when the whole feed is down.
- API keys come from the environment. Scrapers set `scrape = True, enabled_by_default = False`,
  send a descriptive User-Agent and poll at most once a minute.

## How it works

```
 every 60 s ─► Poller.tick()
                ├─ results adapters (concurrently) ─► MongoDB: latest + history, change detection
                ├─ call / exit-poll adapters        ─► new calls only
                └─ odds adapters                    ─► swing detection
                          │
            per server: posting rule, outlets, thresholds
                          ▼
              DiscordPoster ─► embeds in #election-night
 every 60 s ─► the same tick refreshes odds and polls; early vote has its own hourly refresh
                each source only when its request budget allows (half its published limit)
 slash commands ─► MongoDB only: odds, polls, early vote and results are never fetched for a user
```

Commands never call a data source: `/odds`, `/polls` and `/earlyvote` read what the background
refresh stored, so users repeating a command cannot hammer an API. A race nobody watches is
queued by the first request and fetched by the next refresh. How often each source is asked is
computed from its host's published rate limit; every number is configurable
([docs/customizing.md](docs/customizing.md#refresh-rates-and-limits)).

## License

MIT. Data belongs to its publishers. You are responsible for following the terms of any site
you enable.
