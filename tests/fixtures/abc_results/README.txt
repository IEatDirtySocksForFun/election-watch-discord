ABC results fixtures trimmed from ABC News' server-rendered state results widget.
The adapter scrapes the widget HTML and reads window['__abcnews__'] as JSON.

- 2024-tx-senate.html:
  https://abcnews.go.com/widgets/generalstateresults?chamber=senate&hideMap=true&stateAbbrev=tx&year=2024&type=general
  Trimmed to page.content.generalStateResults (state, chamber, aggregates) for
  the Texas Senate race; candidate votes/percentages preserved.
- 2024-tx-house.html:
  https://abcnews.go.com/widgets/generalstateresults?chamber=house&hideMap=true&stateAbbrev=tx&year=2024&type=general
  Trimmed to the TX-38 aggregate; candidate votes/percentages preserved.

Date: 2026-10-27 16:05 UTC
