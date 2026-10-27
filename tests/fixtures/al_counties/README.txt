Fixtures for the al_counties adapter (Madison County, Alabama — Clarity ENR).

Captured 2026-10-27 with one plain GET each, User-Agent
"election-watch-discord/0.1 (open-source election results bot)".

madison_elections.json
  https://results.enr.clarityelections.com/AL/Madison/elections.json  (verbatim)
  Newest entry: EID 126524 "2026 Primary Runoff Election" (6/16/2026). The 2026 general is
  not listed yet; al_counties discovers it by date once the county adds it.

madison_2022_summary.json
  https://results.enr.clarityelections.com/AL/Madison/116245/313343/json/en/summary.json
  ("2022 General Election", final version). Contest objects verbatim, trimmed to 10 of 48:
  STRAIGHT PARTY VOTING, UNITED STATES SENATOR (120,500 votes), UNITED STATES
  REPRESENTATIVE, 5TH CONGRESSIONAL DISTRI (the vendor truncates the name), GOVERNOR,
  LIEUTENANT GOVERNOR, STATE SENATOR DISTRICT NO. 2, STATE REPRESENTATIVE DISTRICT NO. 10,
  SECRETARY OF STATE, MADISON COUNTY SHERIFF, STATEWIDE AMENDMENT 1.

madison_2022_electionsettings.json
  .../116245/313343/json/en/electionsettings.json, trimmed to "websiteupdatedat".

Not used (checked the same day): AL/Jefferson, AL/Mobile, AL/Baldwin, AL/Shelby
elections.json exist but list nothing newer than the 2012 general.
