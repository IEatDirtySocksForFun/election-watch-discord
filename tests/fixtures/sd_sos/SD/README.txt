South Dakota Secretary of State — media results feed (electionresults.sd.gov)
============================================================================

Fetched 2026-10-27 with a plain HTTP client (no cookies, no JS). Every file below is the
state's own response, HTTP 200. The site was showing the 2026-11-03 general election in
"Testing In Progress" mode (122 of 671 precincts, placeholder counts), which is why the
fixture numbers are small.

Sources:

  2026_general_media.html
      https://electionresults.sd.gov/ResultsMedia.aspx
      Content-Type text/html; 26.7 kB. Carries "General Election November 3, 2026",
      "Results last updated: 10/27/2026 10:53 AM CT" and the hidden inputs
      hidElectionDate=11/3/2026, hidVoterTurnout=6441 (ballots cast),
      hidVoterTotal=622188 (registered voters), hidPrecinctsReported=122.
      Trimmed to: the <form> element from its opening tag to the end of the
      "Results Media" <ul> list. __VIEWSTATE/__VIEWSTATEGENERATOR/__EVENTVALIDATION
      inputs, <script> and <style> blocks were removed; the remaining markup and text are
      byte-for-byte the source's.

  2026_general_swr.csv   https://electionresults.sd.gov/resultsCSV.aspx?text=All&type=SWR&map=CTY
      Content-Type application/text, Content-Disposition attachment;filename=Media.csv.
      Complete (18 data rows): US Senate, US House, Governor/Lt. Governor, Secretary of
      State, Attorney General, State Auditor, State Treasurer, Commissioner of School and
      Public Lands, Public Utilities Commissioner.

  2026_general_bq.csv    ...&type=BQ&map=CTY
      Trimmed: the two "Constitutional Amendment I" and "Constitutional Amendment L" rows
      (Yes and No for each).

  2026_general_leg.csv   ...&type=LEG&map=DIST
      Trimmed: every row for District 01 and District 02 (one State Senator race and one
      two-seat State Representative race per district).

  2026_general_cty.csv   ...&type=CTY&map=CTY
      Trimmed: every Codington and Grant county row (County Auditor, plus the Codington
      county referendum).

  2026_general_mun.csv   ...&type=MUN&map=CTY
      Trimmed: the Brookings "City Council Member" rows.

All CSV files keep their original header and CRLF line endings; candidate names keep the
source's double spaces and trailing blanks, and PrecinctsReporting keeps the Excel guard
(="122/671").
