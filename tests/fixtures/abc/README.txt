ABC fixtures trimmed from ABC News' server-rendered state results widget.
The adapter scrapes the widget HTML and reads window['__abcnews__'] as JSON.

- 2024-tx-senate.html:
  https://abcnews.go.com/widgets/generalstateresults?chamber=senate&hideMap=true&stateAbbrev=tx&year=2024&type=general
- 2024-tx-house.html:
  https://abcnews.go.com/widgets/generalstateresults?chamber=house&hideMap=true&stateAbbrev=tx&year=2024&type=general

Trimmed to page.content.generalStateResults (state, chamber, aggregates) with
the Texas Senate race and TX-38 respectively; candidate winner fields only.

Date: 2026-10-27 15:05 UTC
