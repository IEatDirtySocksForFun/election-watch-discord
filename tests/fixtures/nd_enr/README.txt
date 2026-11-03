North Dakota — Election Night Reporting API (api.resultsnd.sos.nd.gov)
=====================================================================

Source, fetched 2026-11-02 with the bot's User-Agent (HTTP 200, application/json):

  https://api.resultsnd.sos.nd.gov/Election/GetElectionList?cid=north-dakota
    -> 2026_elections.json

  https://api.resultsnd.sos.nd.gov/Contest/GetContestSearchList?cid=north-dakota&electionID=348
    -> 2026_general_contests.json

  https://api.resultsnd.sos.nd.gov/Contest/GetContestResults?cId=north-dakota&electionID=348&contestType=SW
    -> 2026_general_results_sw.json

  https://api.resultsnd.sos.nd.gov/Contest/GetContestResults?cId=north-dakota&electionID=348&contestType=LEG
    -> 2026_general_results_leg.json

  https://api.resultsnd.sos.nd.gov/Contest/GetContestSearchList?cid=north-dakota&electionID=346
    -> 2026_primary_contests.json

  https://api.resultsnd.sos.nd.gov/Contest/GetContestResults?cId=north-dakota&electionID=346&contestType=SW
    -> 2026_primary_results_sw.json

Elections: 348 is the 2026 general election (3 November 2026), 346 the 2026
primary (9 June 2026). Before polls close the general payload reports 0 votes and
0 of 358 precincts in every contest; the primary payload has the finished count
and is kept so the vote, percentage and winner paths are tested on real numbers.

Trimming
--------

2026_elections.json
  Untrimmed (230 bytes).

2026_general_contests.json
  Contests kept, otherwise byte-identical: "Representative in Congress",
  "Secretary of State" and "Constitutional Measure No. 1 ..." (statewide, type
  SW), "State Senator District 01", "State Representative District 01" (two
  seats) and "State Representative Unexpired 2-Year Term District 20"
  (legislative, type LEG). The other 525 contests (county, city, school,
  judicial and conservation) are dropped, as is the redundant alphabetical
  `searchList` index in the response envelope.

2026_general_results_sw.json / 2026_general_results_leg.json
  The matching contests from the two GetContestResults calls; every other
  contest is dropped. The per-county `locations` block inside each kept contest
  is dropped too (300+ county entries per contest); the adapter reads only the
  contest-level totals.

2026_primary_contests.json / 2026_primary_results_sw.json
  Contests kept: "Constitutional Measure No. 1 ... single subject" and "Justice
  of the Supreme Court" (two seats). Trimming as above.

Shapes
------

GetContestSearchList: response.contests is an object keyed by contest id; each
contest has contestId, contestName, contestTypeCode, voteFor and choices (an
object keyed by choice id with id, name, partyID, isWriteIn).

GetContestResults: response.contests is an object keyed by contest id with
contestID, precinctsReporting, totalPrecincts, totalVotes, choices (an array of
choiceID, totalVotes, partyID, votePercent, isWinner), writeInChoices and
locations. The two payloads are joined on contestID / choiceID. `lastUpdated`
is the source timestamp (seven fractional digits, UTC).
