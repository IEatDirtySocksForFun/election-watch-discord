Connecticut Secretary of the State Election Management System (EMS) fixtures.
The adapter reads the JSON files behind the public reporting app; every request
follows the host's 301 redirect to the vendor's canonical host.

- elections.json:
  https://ctemspublic.pcctg.net/ng-app/data/Elections.json
  Fetched on 2026-11-03 (05:16 UTC). Trimmed to the 2026 state election
  (ID 108, the default entry) and one older entry, to keep the discovery test
  small. The full file lists every election the system holds.
- version.json:
  https://ctemspublic.pcctg.net/ng-app/data/election/108/Version.json
  Fetched on 2026-11-03. Unchanged apart from the trailing newlines.
- lookupdata.json / electiondata.json:
  https://ctemspublic.pcctg.net/ng-app/data/election/108/51795/Lookupdata.json
  https://ctemspublic.pcctg.net/ng-app/data/election/108/51795/Electiondata.json
  Fetched on 2026-11-03 (05:16 UTC), before any votes were counted. Trimmed to
  four offices (Governor and Lieutenant Governor, Representative in Congress 1,
  State Senator 1, State Representative 1) with their candidates, party codes
  and vote rows; the town and polling-place maps are dropped. The candidate
  address field is removed. Every vote is 0 and the header reads
  "0 of 779" precincts.
- 2024-lookupdata.json / 2024-electiondata.json:
  https://ctemspublic.pcctg.net/ng-app/data/election/91/80741/Lookupdata.json
  https://ctemspublic.pcctg.net/ng-app/data/election/91/80741/Electiondata.json
  Fetched on 2026-11-03; the November 2024 general election, still served by the
  system. Trimmed to the United States Senator office and its vote rows, to test
  the merge of party lines (a candidate nominated by two parties) and real vote
  totals. Candidate address fields are removed.

Date: 2026-11-03 05:16 UTC
