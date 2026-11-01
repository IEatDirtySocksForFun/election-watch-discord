Delaware Department of Elections results.

Fetched 2026-10-31 with a descriptive User-Agent.

  https://elections.delaware.gov/results/enr/Election_StatewideResults_ID_GE2026.json
      -> Election_StatewideResults_ID_GE2026.json
      (the JSON the Division's own results page at
       https://elections.delaware.gov/results/enr/GE2026.html reads every five minutes;
       trimmed from 124 rows to the eight contests listed below. Rows are verbatim:
       Election Id / Election Name / Election Date / Results Type / Total Precincts /
       Precincts Reported / Party Id / Party Name / Contest Sorting Order /
       Contest Title / Candidate Id / Candidate Name / Pos / Machine Votes /
       Absentee Votes / Early Voting Votes / Total Votes / Percentage / ReportTime.
       Every vote is 0 because the file is published before the close of polls.)

  Precincts Reported / Total Precincts are contest-scoped: 530 for a statewide contest,
  37 for Senate District 1. All eight kept contests report 0 precincts reported
  pre-election.

  Contests kept: U.S. Senator, Representative In Congress, Attorney General,
  Auditor of Accounts, State Treasurer, State Senator District 1,
  State Representative District 10, New Castle County Sheriff.

Delaware Department of Elections 2024 General Election official CSV.
Source: http://elections.delaware.gov/reports/GE2024.csv
Fetched: 2026-10-27
Trimmed to a subset of federal, statewide, legislative and local contests.

Older elections keep the reports CSV only: after an election its ENR JSON moves to
/reports/json/, and the 2024 general does not answer there either.
