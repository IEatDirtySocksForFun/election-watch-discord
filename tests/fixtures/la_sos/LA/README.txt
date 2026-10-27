Louisiana Secretary of State — Graphical Election Results (voterportal.sos.la.gov)
=================================================================================

Source (fetched 2026-10-27, HTTP 200, Content-Type application/json):

  https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob=ElectionDates.htm
  https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob=20241105/RacesCandidates_Multiparish.htm
  https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob=20241105/Votes_Multiparish.htm
  https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob=20261103/RacesCandidates_Multiparish.htm
  https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob=20261103/Votes_Multiparish.htm

Elections:

  2024-11-05  general   (folder 20241105; Elections 1900-01-01 version stamp = archived)
  2026-11-03  general   (folder 20261103; races/candidates published 2026-10-27,
                         votes file present with every VoteTotal "0", precincts 0/3722)

Fixture files and trimming
--------------------------

2024_general_races.json / 2024_general_votes.json
  Presidential Electors; U. S. Representative districts 1 and 3; PSC district 2;
  District Judge 21st JDC Division L; Mayor -- Town of Delcambre; CA No. 1 (HB 300).
  The votes file keeps the seven matching "Race" rows (the votes rows carry only ID +
  counts, no titles). All other race rows, and every numeric/string field of the kept
  rows, are exactly as served.

2026_general_races.json
  U. S. Senator (67997); U. S. Representative district 1; State Representative district
  92; PSC district 1; CA No. 1 (SB 180). The 2026 votes blob is not saved because it is
  all zeros; the parser is exercised against it by passing an empty payload.

The media API (/api/MediaRequests/{file}/{date}/{access}/{secret}) was inspected but
needs per-outlet credentials, so the public graphical feed above is used instead.
