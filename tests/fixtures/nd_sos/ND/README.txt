North Dakota Secretary of State — resultsSW.aspx pages (results.sos.nd.gov)
=======================================================================

Source (fetched 2026-10-27, HTTP 200, text/html):

  https://results.sos.nd.gov/resultsSW.aspx?text=All&type=SW&map=CTY   (2024_general_statewide.html)
  https://results.sos.nd.gov/resultsSW.aspx?text=All&type=LG&map=CTY   (2024_general_legislative.html)

Election: the site serves one election at a time; on 2026-10-27 it served
"2024 General Election Results" (385 of 385 precincts reported), so that is the fixture.

Fixture files and trimming
--------------------------

2024_general_statewide.html
  Contests kept, unmodified: "United States Senator", "Representative in Congress",
  "Governor and Lt. Governor", "Related to the legalization of cannabis and related
  regulation." The other ten statewide contests (President, State Auditor, State
  Treasurer, Insurance Commissioner, Public Service Commissioner, the five other
  measures) are dropped.

2024_general_legislative.html
  Contests kept: "State Senator District 2", "State Representative District 04a"
  (the two-letter district letter is real: 4A). The other 50 legislative contests are
  dropped.

Both files: the <head> (scripts, styles, the 52-district navigation) is replaced by a
short stub that keeps the real <title>, because the parser reads only the contest blocks.
Every kept contest block - headings, "Precincts Fully" line, candidate rows, party divs,
winner-icon <img> and vote/percentage columns - is exactly as served.

No timestamps
-------------

The pages stamp no "last updated" time, so the adapter reports source_updated_at = None.
