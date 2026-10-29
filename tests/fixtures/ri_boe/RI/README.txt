Rhode Island Board of Elections — ri.gov results JSON
====================================================

Source (fetched 2026-10-27):
  https://rigov.s3.amazonaws.com/election/results/2024/general_election/statewide.json
  https://rigov.s3.amazonaws.com/election/results/2024/general_election/ga_senator.json
  https://rigov.s3.amazonaws.com/election/results/2024/general_election/ga_representative.json
  https://www.ri.gov/election/results/2024/general_election/data/   (page linking the files)
  https://www.ri.gov/election/results/2024/general_election/        (human site)

Election: 2024-11-05 General Election. Format: JSON with election_name,
election_date, last_updated, precinct_count/precincts_reporting and contests[]
(name, contest_number, precinct_count, precincts_reporting, total_votes,
votes_allowed, candidates[] with name/party_code/votes).

Trimming: statewide.json is kept in full (9 contests: president, U.S. Senate,
both U.S. House districts, 5 ballot questions). ga_senator.json keeps the first
6 of 38 senate districts and ga_representative.json the first 6 of 75 house
districts; contests are otherwise unmodified. The per-town JSON files listed on
the same page are not used.

2026: https://rigov.s3.amazonaws.com/election/results/2026/general_election/statewide.json
returned HTTP 403 (missing key) and the RI.gov page 404 on 2026-10-27; the path
is date-derived, so the adapter picks it up when the state publishes.
