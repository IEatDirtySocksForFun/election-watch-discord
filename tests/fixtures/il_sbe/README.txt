Illinois State Board of Elections — ElectionVoteTotals.aspx
=========================================================

Recorded: 2026-10-27. The page was showing the 2026 GENERAL PRIMARY (the newest
election at the time); the 2026 general election page is not published yet.

  federal_statewide.html  https://www.elections.il.gov/electionoperations/ElectionVoteTotals.aspx
                          (default "Federal / Statewide" category; 7 office blocks kept:
                           US SENATOR, GOVERNOR AND LIEUTENANT GOVERNOR, ATTORNEY GENERAL,
                           SECRETARY OF STATE, COMPTROLLER, TREASURER, 1ST CONGRESS)
  state_senate.html       .../ElectionVoteTotals.aspx?ID=<token>&OfficeType=<token>  ("Senate")
                          (first 2 blocks: 2ND SENATE, 3RD SENATE)
  state_house.html        .../ElectionVoteTotals.aspx?ID=<token>&OfficeType=<token>  ("District All")
                          (first 2 blocks: 1ST REPRESENTATIVE, 2ND REPRESENTATIVE)

Trimmed for the fixture: the page is cut from just before the election dropdown to
the end of the last kept office block, so the ASP.NET scaffolding, the dropdown and
the category menu are intact while the other office tables are dropped. Vote totals
are the real primary-night numbers from the SBE page.
