Mississippi Secretary of State — statewide recapitulation report (CSV)
=======================================================================

Source (fetched 2026-10-27, HTTP 200, Content-Type application/octet-stream):

  https://www.sos.ms.gov/elections/electionresults/2024ElectionRecapSheets.csv
  (linked from https://www.sos.ms.gov/elections/electionresults_aspx/elections_results_2024_general.aspx)

Election: 2024-11-05 general (certified recapitulation, not election night).

Trimming: the file keeps its header and, for the counties Adams, Alcorn and Hinds, every
row of United States-President, United States-Senate and US House Of Rep 01 (all
candidates, all three counties) plus the Southern District Supreme Court District 2
Position 2 rows. 36 of 1357 data rows; kept rows are byte-for-byte the source's
(quoted CSV, CRLF line endings, trailing space in the Harris elector name included).

Columns: County, Office, Candidate, Party, County Total, State Total. "State Total" is the
statewide figure repeated on every county row, so the parser groups by office and uses it
instead of summing counties (a partial county set would under-count).
