# election-watch-discord

**A self-hosted Discord bot for US election night.** Point it at a channel and it posts live
results for the races your server cares about, race calls from the outlets you trust, turnout,
and big prediction-market swings. Everyone in the server also gets a private race browser with
odds, polls and a voting FAQ for every state. It's built for the **November 3, 2026 midterms**,
and it's meant to be forked and made your own.

```
[#election-night]                                      Texas Senate
🔵 James Talarico (DEM)   4,104,331 · 50.6%
🔴 Ken Paxton (REP)       3,962,208 · 48.8%
🟡 Ted Brown (LIB)           49,770 · 0.6%
72.4% expected vote · 6,912/8,031 precincts
Turnout: 57.1% · 8,116,309/14,214,001 ballots
Source: tx_sos · Nov 04 03:41 UTC
```

<sub>What a results post looks like (made-up numbers).</sub>

- **Local** Your Discord token, your database. Either pull bits and pieces or plug it into a bot.
- **Tested on real election nights.** A replay sandbox reruns the 2014–2024 nights minute by
  minute through the real bot. `make sim` runs a short one in about 15 seconds.

**Contents:** [What it does](#what-it-does) · [Get it running](#get-it-running) ·
[Election night checklist](#election-night-checklist) · [Commands](#commands) ·
[Where the numbers come from](#where-the-numbers-come-from) · [Good to know](#good-to-know) ·
[Privacy](#privacy) · [Troubleshooting](#troubleshooting) · [Race keys](#race-keys) ·
[Contributing](#contributing) · [How it works](#how-it-works) · [License](#license)

## What it does

- **Live results** for every race you watch, from official state and county feeds (40 states +
  DC) with [civicAPI](https://civicapi.org) filling in every state. Each post shows the top five
  candidates, party markers, expected vote, precincts and turnout.
- **Sensible posting.** By default a race is posted at most every 5 minutes, and only when its
  numbers changed. Admins can switch to every _N_ minutes or every single update.
- **Race calls** from the outlets your server picks: AP, DDHQ, CNN, NBC, CBS, Fox, ABC, or
  civicAPI's own decision desk. Retracted calls get their own post.
- **Odds and polls.** Kalshi and Polymarket odds for every watched race (with optional alerts on
  big swings), and the latest public polls with partisan internals tagged.
- **Early vote.** Ballots cast before Election Day for the 46 states civicAPI tracks: mail and in
  person, party, age, top counties, and how it compares with the last similar election.
- **Private race browser** (`/races`): tabs for House, Senate, state and local races, a state
  picker, favorites, and Odds and Polls buttons on every race. Only you see it.
- **Voting FAQ** for all 50 states + DC: when polls close, what gets counted first, mail
  deadlines, recount rules and typical call times, each with official sources.
- **Polite to its sources.** A background refresh stores everything in MongoDB, and each site
  gets at most half of its published rate limit, so spamming a command can't make the bot hammer
  anyone. By default commands only read the database. If you'd rather they fetch fresh numbers,
  set `EW_COMMAND_DATA=live`: commands then go to the source when what's stored is over a minute
  old, within the same limits ([details](docs/customizing.md#database-or-live-commands)).

## Get it running

You'll need **Python 3.11 or newer**, a **Discord account**, and **MongoDB**: a free
[Atlas](https://www.mongodb.com/atlas) cluster, or a local one
(`docker run -d -p 27017:27017 mongo:8.0`). Setup takes about ten minutes.

1. **Create the bot.** At <https://discord.com/developers/applications>, click _New
   Application_, then _Bot_ → _Reset Token_ and copy the token. No privileged intents needed.
2. **Invite it to your server.** Under _OAuth2 → URL Generator_, tick the `bot` and
   `applications.commands` scopes, then these permissions: **View Channels, Send Messages, Embed
   Links, Read Message History**. Open the URL it makes and pick your server.
3. **Get the code.**

   ```sh
   git clone https://github.com/<you>/election-watch-discord
   cd election-watch-discord
   make install          # or: python -m venv .venv && .venv/bin/pip install -e .
   cp .env.example .env
   ```

4. **Fill in `.env`.** Only two lines are required:

   ```sh
   DISCORD_TOKEN=your-bot-token
   MONGODB_URI=mongodb://127.0.0.1:27017      # or your Atlas mongodb+srv://… URI
   ```

   While you're testing, also set `DEV_GUILD_ID` to your server's ID, so slash commands show up
   instantly there. Every other setting is optional and explained in `.env.example`.

5. **Check it, then start it.**

   ```sh
   .venv/bin/python -m electionwatch --check   # checks .env, MongoDB and the race list, then exits
   .venv/bin/python -m electionwatch           # starts the bot
   ```

6. **Set it up in Discord.** Run `/setup channel:#election-night`, then `/watch add` for each race
   you care about (autocomplete helps). Until you add your own, the server follows the default
   list in `config/races.yaml`.

That's it. Results start flowing as soon as the states publish them.

## Election night checklist

- **Turn on the extra sources you want.** Out of the box the bot only reads official feeds and
  free APIs. The national fallbacks and outlet calls read news sites' public data, so they're off
  until you opt in. Check each site's terms first. Fox's election feed, for one, asks bots not to
  crawl it (`robots.txt`). For example:

  ```sh
  EW_ENABLED_SOURCES=cbs_results,fox_results,nbc,cbs,fox,abc,cnn_exit_polls,270towin,wikipedia
  ```

  Without it, results still come from the states and civicAPI. No race calls are posted (AP and
  DDHQ need a paid key), but civicAPI's free calls are one command away:
  `/calls outlets:CIVICAPI enabled:on`.

- **On the morning,** run `make health` to see which sources answer. civicAPI rows say `TEST` while
  it still serves practice data, and they should flip to `OK` once real returns arrive. In
  Discord, `/limits` shows how often each source is being asked.
- **Watch a few races, not hundreds.** A busy channel can only take about five messages every
  five seconds, so the default 5-minute posting mode is your friend.

## Commands

Replies are private (only you see them) unless they're posts in the election channel.

**For everyone**

| Command                                   | What it does                                                                     |
| ----------------------------------------- | -------------------------------------------------------------------------------- |
| `/races`                                  | your private race browser                                                        |
| `/odds [race]`                            | Kalshi and Polymarket odds, with 1 h and 24 h moves                              |
| `/polls race:<race> [count] [internals]`  | the latest polls for a race                                                      |
| `/earlyvote [state] [breakdown] [county]` | early-vote numbers (party, method, age, county; one county with its party split) |
| `/faq [state]`                            | a state's voting FAQ                                                             |
| `/favorite add·remove <race>`             | pin races in your browser                                                        |
| `/oddsalerts`                             | this server's odds-alert settings                                                |
| `/settings`                               | this server's settings                                                           |
| `/limits`                                 | how often data refreshes, and this server's limits                               |

**For admins** (Manage Server, or the admin role from `/setup`)

| Command                                                    | What it does                                                     |
| ---------------------------------------------------------- | ---------------------------------------------------------------- |
| `/setup channel:<#channel> [admin_role]`                   | where the bot posts, and who else may configure it               |
| `/watch add·remove·list <race>`                            | the races your server follows                                    |
| `/posting mode:<default·interval·immediate> [minutes]`     | how often results are posted                                     |
| `/calls outlets:<…> on·off`                                | which outlets' race calls are posted                             |
| `/oddsalerts on·off [threshold] [window] [race·category]`  | alerts when the odds swing hard, server-wide or per race         |
| `/turnout on·off`, `/exitpolls on·off`                     | turnout lines and exit-poll links                                |
| `/earlyvote_posts on·off`                                  | a daily early-vote summary for the races you watch               |
| `/pause`, `/resume`                                        | stop and restart all posting                                     |
| `/refresh [what]`                                          | fetch fresh data now instead of waiting for the next refresh     |
| `/limits [user_cooldown] [user_hourly] [refresh_cooldown]` | change this server's command limits (`-1` puts back the default) |

Odds alerts can be tuned for one race (`race:PA-08`) or a kind of race (`category:` senate,
house, statewide, state-senate, state-house, mayor or local). A race setting beats its
category's, which beats the server-wide one, and anything left out comes from the next level
down. `enabled:default` removes an override. Only watched races alert, and a server can keep up
to 100 race overrides.

## Where the numbers come from

| Coverage                              | States                                                                                                                                          |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| The state's own election-night feed   | AR, AZ, CA, CO, CT, DC, DE, FL, GA, HI, IA, ID, IL, IN, KS, LA, MD, MN, MT, NC, ND, NE (opt-in), NM, OH, OR, PA, RI, SC, SD, UT, VA, VT, WA, WV |
| County feeds only (part of the state) | TX (20 counties, about half the vote), MI, WI (opt-in), NJ, MO, NY (opt-in), TN, AL                                                             |
| No official feed the bot can reach    | KY, MA, ME, MS, NH, NV, OK, WY: civicAPI, plus the national fallbacks if you turn them on                                                       |
| [civicAPI](https://civicapi.org)      | every state + DC, from federal races down to local ones; it fills all the gaps above                                                            |

Official feeds always win. civicAPI is a free results API that aggregates state and county
returns. It's only used for races no official feed answered, and every post that uses it links
back to it. In checks against official results it matched closely, and the winners always
agreed. Still, treat its numbers as election-night numbers, not certified totals (details in
[docs/civicapi-accuracy.md](docs/civicapi-accuracy.md)). Per-state notes live in
[docs/sources-results.md](docs/sources-results.md) and
[docs/state-coverage-plan.md](docs/state-coverage-plan.md).

## Good to know

- **Most 2026 feeds go live on election day.** The bot finds them automatically where a state
  allows it. A few need a value set by hand (`CLARITY_FEEDS`, `MT_EID`, `SD_EID`), see
  `.env.example`.
- **Feeds can change without warning.** If a state renames a field on the night, that one source
  stops and everything else keeps going.
- **The bot never gets around bot checks.** Sites behind Cloudflare challenges and the like are
  simply skipped, which is why a few states rely on civicAPI.
- **AP and DDHQ calls cost money.** Their adapters are ready, but they stay off without a key.
- **Outlet call times are approximate.** Most outlets don't publish the exact moment of a call.
- **Exit polls are links** to the outlet's page, not summaries.
- **Turnout only shows where a feed publishes registered voters** (NC, GA, FL and a few others).
- **Multi-seat races show the top five candidates.**
- **Refresh rates follow each source's limits.** Results and calls refresh about every minute,
  county feeds that share a host about every 2 minutes, polls every 30 minutes and early vote
  hourly. `/limits` shows the live numbers.
- **It's not an official source.** The numbers are only as good as the feeds, and calls come from
  the outlets that made them.

## Privacy

The bot only stores what it needs, in your own MongoDB:

- **Per server:** the channel it posts in, the admin role, the watched races and settings.
- **Per user** (only if they use the browser or `/favorite`): their Discord user ID, favorite
  races, home state and the browser tab they opened last.

It doesn't read messages (no message-content intent) and keeps no analytics. Cooldowns live in
memory and vanish on restart. Requests to data sources carry the bot's name and a link to this
project, nothing about your server or users.

Stored settings stay until they're deleted; there's no automatic expiry. If someone asks you to
delete their data, remove their document from the `users` collection (`_id` is their Discord
user ID), or a server's from `guilds`. Members can see their server's bot settings with
`/settings`, and the public refresh rates with `/limits`.

## Troubleshooting

- **The slash commands don't show up.** Global commands can take a while to appear. Set
  `DEV_GUILD_ID` to your server's ID and restart: they register there instantly.
- **`--check` fails on MongoDB.** Is the server running? For Atlas, make sure your IP is on the
  cluster's access list.
- **No results yet.** Before polls close, that's normal. Run `make health` to see what each source
  answers right now.
- **A race says it's "queued".** Nobody was watching it, so the next refresh fetches it. Try
  again in a minute or two.

## Race keys

You can type races in plain English (`Texas Senate`, `North Carolina Congressional District 1`)
or as short keys:

| Key                            | Race                                                              |
| ------------------------------ | ----------------------------------------------------------------- |
| `NC-01`, `AK-AL`               | US House district (`AL` = at-large)                               |
| `TX SEN`, `OH SEN S`           | US Senate (`S` = special election, e.g. Ohio and Florida in 2026) |
| `GA GOV`, `AZ AG`              | a statewide office (`GOV`, `LTGOV`, `AG`, `SOS`, `TREAS`, …)      |
| `TX SD-05`, `TX HD-121`        | state senate / state house district                               |
| `TX LOCAL harris-county-judge` | a local race                                                      |
| `PR-AL`, `GU GOV`              | territories                                                       |

Commands don't mind how you type the state or the district: `pa 08`, `PA08`,
`pennsylvania-08`, `Penn. 8` and even `pensylvaniua 08` all mean `PA-08`. `/faq` and
`/earlyvote` take a state's code, name, abbreviation (`N.C.`, `Calif.`) or a close misspelling.

Parties get markers: 🔵 Democratic · 🔴 Republican · 🟡 Libertarian · 🟢 Green · 📜
Constitution · ⚪ independent or no party, and a marker each for 97 smaller parties. See
[docs/parties.md](docs/parties.md), and edit `electionwatch/data/parties.yaml` to change any.

## Contributing

Pull requests are welcome. Start with [AGENTS.md](AGENTS.md) (the short rules) and
[tests/README.md](tests/README.md). The usual loop:

```sh
make test      # unit tests, no network, about 20 seconds
make sim       # a quick election-night simulation
make lint      # what CI checks (make format fixes it)
```

Adding a data source is one module under `electionwatch/sources/` plus a test against a saved
real response. [docs/customizing.md](docs/customizing.md) walks you through it. Found a security
problem? Please report it privately, see [SECURITY.md](SECURITY.md).

## How it works

```
 every 60 s ─► Poller.tick()        each source only when its turn and its rate budget allow
                ├─ results adapters ─► MongoDB: latest numbers + history, change detection
                ├─ race calls       ─► new calls only
                ├─ odds             ─► MongoDB odds history, swing alerts
                └─ polls            ─► MongoDB (every 30 min)
                          │
            per server: posting rule, outlets, thresholds
                          ▼
              posts in #election-night

 every hour ─► early-vote refresher ─► MongoDB
 every 60 s ─► race-list refresher  ─► MongoDB (a few states at a time)

 slash commands ─► MongoDB only
```

If a results source is slow or failing, its last answer stands in for up to 10 minutes, so posts
don't flip between sources. Every rate and limit can be changed: see
[docs/customizing.md](docs/customizing.md#refresh-rates-and-limits).

## License

[MIT](LICENSE). The election data belongs to its publishers, and following the terms of any
source you turn on is up to you.
