ABC results fixtures trimmed from ABC News' server-rendered state results widget.
The adapter scrapes the widget HTML and reads window['__abcnews__'] as JSON.

- 2024-tx-senate.html:
  https://abcnews.go.com/widgets/generalstateresults?chamber=senate&hideMap=true&stateAbbrev=tx&year=2024&type=general
  Trimmed to page.content.generalStateResults (state, chamber, aggregates) for
  the Texas Senate race; candidate votes/percentages preserved.
- 2024-tx-house.html:
  https://abcnews.go.com/widgets/generalstateresults?chamber=house&hideMap=true&stateAbbrev=tx&year=2024&type=general
  Trimmed to the TX-38 aggregate; candidate votes/percentages preserved.
- 2026-tx-senate.html:
  https://abcnews.go.com/widgets/generalstateresults?chamber=senate&hideMap=true&stateAbbrev=tx&year=2026&type=general
  Fetched on 2026-11-03 (05:15 UTC), before any votes were counted. Trimmed to
  page.content.generalStateResults (state, chamber, aggregates, lastUpdated) for
  the Texas Senate race: the candidate list is complete, every rawVotes and
  reporting value is 0 and result.severity is "noResults". The request answered
  with a 301 to abcnews.com, which the adapter follows.

Date: 2026-11-03 05:15 UTC (2026 fixture); 2026-10-27 16:05 UTC (2024 fixtures)
