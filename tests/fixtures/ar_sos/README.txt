Arkansas Secretary of State / Tally-ENR fixtures
================================================

Recorded: 2026-10-27.

2024_general.json
  https://arkansas.tally-enr.com/results/arkansas/1846/FullDataFile.json
  (HTTP 200, 15,776,776 bytes). ElectionInfo and Turnout kept as published (Turnout
  minus the per-county CountyTurnout map). ContestData trimmed to five contests --
  U.S. President, U.S. Congress District 01, State Senate District 01, State
  Representative District 01, Justice of the Peace District 7 -- each capped at four
  candidates and with its per-county breakdown removed.

2026_uuid_format.json
  https://arkansas.tally-enr.com/results/arkansas/7f77a178-af02-40ec-92db-c5cc50882c68/FullDataFile.json
  (2026 Preferential Primary, 21,246,557 bytes). Only the first two contests are
  kept (three Choices each, Locations emptied) plus three TurnoutData counters. It
  exists to pin the *new* shape, which has ContestID/Choices and no contest names.

sos_results.html
  https://www.sos.arkansas.gov/elections/research/election-results (113,325 bytes).
  The stretch of the live page that holds the election links, from just before the
  first arkansas.tally-enr.com anchor to the last enr.totalresults.com anchor, so
  find_election() sees exactly the labels the SOS publishes (2026 specials first,
  then 2024 General Election = 1846, then older numeric ids).
