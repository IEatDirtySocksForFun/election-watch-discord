# Contributing

Thanks for helping out! These rules apply to everyone sending changes, people and coding
assistants alike.

- **No AI inside the bot.** It's plain, deterministic code: no OpenAI, Anthropic, DeepSeek,
  Kimi, Ollama or any other model API, at runtime or as a dependency. Summaries (exit polls and
  so on) come from parsing, not from a model.
- **No secrets or personal data in the repo.** Tokens and database URIs come from the
  environment (see `.env.example`); never print, log or commit them. The same goes for personal
  emails, local file paths and real Discord server, channel or user IDs: use placeholders like
  `100000000000000001`.
- **Shared contracts.** `electionwatch/models.py`, `racekeys.py`, `settings.py`,
  `sources/base.py` and `store/base.py` are used everywhere. Change them only on purpose, and
  say so in your PR.
- **Tests, no network.** `make test` (or `pytest -q`). Source adapters are tested against
  trimmed real responses saved in `tests/fixtures/<adapter>/`, with a `README.txt` recording the
  URL and the date. See [tests/README.md](tests/README.md).
- **Be polite to data sources.** Scrapers of sites without an API set `scrape = True` and
  `enabled_by_default = False` and send a descriptive User-Agent. Slash commands never call a
  data source: they read MongoDB, and the background refreshers fetch on the request budgets in
  `electionwatch/ratelimit.py`. If a host publishes a rate limit, add it to `HOST_LIMITS` there.
- **Never get past a bot check.** A Cloudflare challenge, captcha, WAF or IP block means "no":
  use another publisher (or civicAPI) instead.
- **New dependencies** need a reason in the PR.
- **Commits:** small, with messages like `feat(area): ...` / `fix(area): ...` that explain why.
