# Contributing

Rules for every change sent to this repository, whether written by a person or by a coding
assistant.

## 📏 Rules

- **No AI inside the bot.** The bot is plain, deterministic code: no OpenAI, Anthropic,
  DeepSeek, Kimi, Ollama or any other model API, at runtime or as a dependency. Summaries
  (exit polls and so on) come from parsing, never from a model.
- **No secrets or personal data in the repository.** Tokens and database URIs come from the
  environment (see `.env.example`); never print, log or commit those values. The same applies to
  personal emails, local file paths and real Discord server, channel or user IDs: use
  placeholders such as `100000000000000001`.
- **Shared contracts.** `electionwatch/models.py`, `racekeys.py`, `settings.py`,
  `sources/base.py` and `store/base.py` are used everywhere. Change these files only on purpose, and
  call the change out in the pull request.
- **Tests, no network.** Run `make test` (or `pytest -q`). Source adapters are tested against
  trimmed real responses saved in `tests/fixtures/<adapter>/`, with a `README.txt` recording
  the URL and the fetch date. See [tests/README.md](tests/README.md).
- **Be polite to data sources.** Scrapers of sites without an API set `scrape = True` and
  `enabled_by_default = False` and send a descriptive User-Agent. All fetching runs on the
  request budgets in `electionwatch/ratelimit.py`: the background refreshers always, and slash
  commands only when the bot owner sets `EW_COMMAND_DATA=live` (by default, commands read
  MongoDB only). When a host publishes a rate limit, add it to `HOST_LIMITS` there.
- **Never get past a bot check.** A Cloudflare challenge, captcha, WAF or IP block means "no":
  use another publisher (or civicAPI) instead.
- **New dependencies** need a reason in the pull request.
- **Commits** stay small, with messages such as `feat(area): ...` or `fix(area): ...` that
  explain why.

## 🔗 See also

- [tests/README.md](tests/README.md): running and writing tests
- [docs/customizing.md](docs/customizing.md): adapters, defaults and rate limits
- [SECURITY.md](SECURITY.md): reporting a vulnerability
