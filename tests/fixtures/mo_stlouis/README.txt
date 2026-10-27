Fixtures for the mo_stlouis adapter (St. Louis County, MO).

st_louis_2024_csv.csv
  https://extcontent.stlouisco.com/BOE/eResults/el241105/CSV.csv
  (9,364,370 bytes / 104,636 data rows in full; el241105 = 2024-11-05 general election.)
  Captured 2026-10-27 with a plain GET and the descriptive User-Agent
  "Mozilla/5.0 (compatible; election-watch-discord/0.1; ...)".

  Trimmed to the first five precincts (AP - 001 .. AP - 005) and seven contests:
  U.S. SENATOR, U.S. REPRESENTATIVE - DISTRICT 1, GOVERNOR, STATE REPRESENTATIVE -
  DISTRICT 73, STATE - CONSTITUTIONAL AMENDMENT NO. 2, ST. LOUIS COUNTY - PROPOSITION A,
  and the judicial retention contest "KELLY C. BRONIEC" (YES/NO). Rows, header and the
  cp1252 encoding are exactly as published (the file contains 0xC9 in "RENÉE"); 114 rows,
  10,237 bytes.

  Column layout: #Precinct, Precinct ID, Precinct Reporting, Registered Voters,
  Ballots Cast, Turnout Percentage, Contest Title, Contest ID, Contest Party, Choice Name,
  Choice ID, Choice Party, Total Votes.

Nothing was synthesised; only rows were selected.
