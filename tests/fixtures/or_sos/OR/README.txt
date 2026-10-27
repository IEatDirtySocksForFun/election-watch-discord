Oregon Secretary of State — election-night results (orresultswebservices)
=========================================================================

Captured 2026-10-27 with a plain HTTP client, descriptive User-Agent, no cookies:
the 2026 general election before any ballot has been counted (every count is 0), which is
also the shape of the feed on election night once counting starts.

Source (each file is one `type`):

  https://orresultswebservices.azureedge.us/ResultsAjax.svc/GetMapData
      ?type=SWPAR|FED|HOUSE|SENATE|MEASURE&category=SW&raceID=0&osn=0&county=0&party=0

  2026_general_swpar.json    type=SWPAR   1 race  (Governor, 4 candidates)
  2026_general_fed.json      type=FED     7 races (US Senate + 6 US House districts, 25 rows)
  2026_general_house.json    type=HOUSE   trimmed to the first 2 state house districts
  2026_general_senate.json   type=SENATE  trimmed to the first 2 state senate districts
  2026_general_measure.json  type=MEASURE verbatim — no statewide measures qualified yet

All responses are `application/json; charset=utf-8` and wrapped as {"d": [...]}; the trim
keeps the wrapper and whole race records (every row of the kept RaceIDs), nothing else.
`results.oregonvotes.gov` itself answers 302 to a not-found page right now; the service
above is what that site's front end calls.
