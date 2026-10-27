New Mexico Secretary of State — resultsSW.aspx pages (electionresults.sos.nm.gov)
================================================================================

Source (fetched 2026-10-27, HTTP 200, text/html):

  https://electionresults.sos.nm.gov/resultsSW.aspx?text=All&type=FED&map=CTY
  https://electionresults.sos.nm.gov/resultsSW.aspx?text=All&type=LGX&map=CTY

Election: the site serves whatever election is currently published; on 2026-10-27 that was
the 2026-11-03 general election, one week before the vote, with "Precincts Fully: 0 / 2204"
and every candidate count 0. There is no archived 2024 page in this markup (the
Results.aspx?ID=... links return an empty shell), so the fixture records the real 2026
candidate set with zero counts rather than invented numbers.

Fixture files and trimming
--------------------------

2026_general_federal.html
  The four federal contests: United States Senator, United States Representative
  DISTRICT 1, 2 and 3.

2026_general_legislative.html
  The first three legislative contests: State Representative DISTRICT 1, 2 and 3
  (25 on the page).

Both files: the <head> is replaced by a short stub keeping the real <title>; every contest
block - heading, "Precincts Fully" line, candidate h1/h2 names and party, vote column - is
exactly as served.

Fields the page does not carry
------------------------------

  - no winner marker (no winner icon exists in this markup)
  - no percentages
  - no "last updated" stamp

so those stay None/False in the parsed results and the adapter is marked "configured,
unverified with non-zero counts" in docs/results/states-3.md.
