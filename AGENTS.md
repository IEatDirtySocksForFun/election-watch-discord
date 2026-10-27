# Rules for every contributor (human or agent)

- **No LLM / AI calls in the product.** The bot is deterministic code: no OpenAI, Anthropic,
  DeepSeek, Kimi, Ollama or any model API, at runtime or as a dependency. "Condensed" text
  (exit-poll summaries etc.) is built by parsing, not by a model.
- **No secrets in the repo.** Tokens and Mongo URIs come from the environment
  (see .env.example). Never print, log or commit them.
- Code against the shared contracts and don't change them without saying so in your summary:
  `electionwatch/models.py`, `racekeys.py`, `settings.py`, `sources/base.py`, `store/base.py`.
- Stay inside your package's directories (your brief lists them) plus new tests.
- Tests: `pytest -q tests/<your tests>`; no network in tests. Adapters are tested against
  trimmed real responses saved in `tests/fixtures/<adapter>/` (record the URL + date in a
  README.txt next to them).
- No new dependencies beyond pyproject.toml without saying why in your summary.
- Scrapers of sites without an API set `scrape = True` and `enabled_by_default = False`,
  send a descriptive User-Agent, and poll no faster than once a minute.
- Small commits, message style `feat(area): ...` / `fix(area): ...`.
