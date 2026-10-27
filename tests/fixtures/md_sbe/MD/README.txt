Maryland State Board of Elections — statewide breakdown CSVs
===========================================================

Source (fetched 2026-10-27, HTTP 200, Content-Type application/octet-stream):

  https://elections.maryland.gov/elections/archive/2024/election_data/PG24_CongressionalBreakDown.csv
  (linked from https://elections.maryland.gov/elections/archive/2024/election_data)
  https://elections.maryland.gov/elections/archive/2024/election_data/PG24_LegislativeBreakDown.csv

Election: 2024-11-05 general (certified). The 2026 files are not published yet; the adapter
finds them by following the *BreakDown.csv links on
/elections/archive/2026/election_data when they appear.

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
