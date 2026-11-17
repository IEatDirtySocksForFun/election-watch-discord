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

st_louis_2026_csv.csv
  https://extcontent.stlouisco.com/BOE/eResults/el261103/CSV.csv
  (6,713,239 bytes / 70,057 data rows in full; el261103 = 2026-11-03 general election.)
  Captured 2026-11-17 with a plain GET and the descriptive User-Agent
  "Mozilla/5.0 (compatible; election-watch-discord/0.1; ...)".

  Trimmed to the first two precincts (AP - 001 .. AP - 002) and every contest they
  report: 35 contests, including U.S. REPRESENTATIVE -  DISTRICT 1 (two spaces before
  DISTRICT in the 2026 export), STATE SENATE - DISTRICT 14, STATE AUDITOR, STATE
  REPRESENTATIVE - DISTRICT 73, statewide amendments, county measures and judicial
  retention. Rows, header and CRLF line endings are exactly as published; 150 rows,
  14,248 bytes (ASCII).

  Column layout: #Precinct, Precinct ID, Precinct Reporting, Registered Voters,
  Ballots Cast, Turnout Percentage, Contest Title, Contest ID, Contest Party, Choice Name,
  Choice ID, Choice Party, Total Votes.

Nothing was synthesised; only rows were selected.
