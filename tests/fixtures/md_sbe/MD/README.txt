Maryland State Board of Elections — statewide breakdown CSVs
===========================================================

Source (fetched 2026-10-27, HTTP 200, Content-Type application/octet-stream):

  https://elections.maryland.gov/elections/archive/2024/election_data/PG24_CongressionalBreakDown.csv
  (linked from https://elections.maryland.gov/elections/archive/2024/election_data)
  https://elections.maryland.gov/elections/archive/2024/election_data/PG24_LegislativeBreakDown.csv

Election: 2024-11-05 general (certified). The 2026 files are not published yet; the adapter
finds the files by following the *BreakDown.csv links on
/elections/archive/2026/election_data once published.

Fixture: 2024_general_congressional_breakdown.csv
  Header plus every County "00" (State of Maryland) row for President - Vice Pres (34),
  U.S. Senator (9) and U.S. Congress districts 01 and 08 (8). 51 of 370 data rows; kept
  rows are byte-for-byte the source's, including all eight "Congressional District N"
  columns (trimming columns would break the statewide sums).

  Rows dropped: court retention ("MD Supreme Court", "Appeals Judge MD"), and the county
  rows (county council / municipal contests) that make up the rest of the file.

Not saved: PG24_LegislativeBreakDown.csv has the same 90 County "00" rows as the
congressional file, so it adds nothing for 2024; its legislative-district columns are the
path used for State Senate / House of Delegates rows in 2026. Maryland held no state
legislative election in 2024 (four-year terms), so that path is unit-tested only.

The votes of a row are the sum of its district columns: the columns are a geographic
partition of the state, so the sum is the statewide total, and for a district contest it is
that district's total (summed across the county rows).


Election-night pages (unofficial)
=================================

Fetched 2026-10-27 with a plain HTTP client, HTTP 200, text/html. These are the pages the
board refreshes while counting, for the tracked election (2026-11-03 general):

  2026_general_index.html
      https://elections.maryland.gov/elections/2026/general_results/index.html
      Trimmed to the contest anchors only: 146 <a href="gen_results_2026_*.html"> /
      gen_qresults_2026_*.html links with the original labels ("Governor / Lt. Governor",
      "Representative in Congress" + "1".."8", "State Senator" + districts, "House of
      Delegates" + districts such as "1A", judges, "Question 1".."Question 3").

  2026_general_governor.html         gen_results_2026_1.html — whole page, unedited
  2026_general_house_delegates_d1.html  gen_results_2026_6_1.html — whole page
  2026_general_question1.html        gen_qresults_2026_1.html — whole page (the option
                                     label sits in <th>, the numbers in <td>)
  2026_primary_congress_district1.html  ../2026/primary_results/gen_results_2026_4_1.html —
                                     the June 2026 primary for US House District 1, kept
                                     because it is the same generator with real numbers and
                                     "Winner Selected" markers (the 2026 general pages all
                                     still read "NR" = not reported)

All pages carry "Last refreshed: 10/21/2026 11:01:50 PM" (the primary page
"07/23/2026 11:05:33 AM") and "(0 of 1979 election day precincts reported)".
