# Customizing your fork

## Watch list and defaults

- `config/races.yaml`: the default important races for new servers (key format in the README).
  Servers change their own list with `/watch`.
- `electionwatch/settings.py`: defaults for new servers (posting interval, call outlets, odds
  threshold and window). These are shared contracts: change values, not field names.

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
            continue                      # never fail the batch for one odd race
        out.append(RaceResult(
            key=key, source="example_sos", fetched_at=fetched_at, url=race.get("url"),
            candidates=[CandidateResult(c["name"], c.get("party"), int(c["votes"]))
                        for c in race["candidates"]],
            precincts_reporting=race.get("reporting"), precincts_total=race.get("precincts"),
        ).finalize())
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

## Trying a change against a past election

Run the new adapter's parser over a saved response, or replay a whole night through the poller:
see [testing.md](testing.md). A replay is the quickest way to see how a change to post formatting
or timing looks across a real election night.

## Rules worth keeping

- No LLM or AI calls in the product, and no model SDKs as dependencies.
- No secrets in the repo; everything sensitive comes from the environment.
- Scrapers off by default, descriptive User-Agent, at most one request per minute per site.
