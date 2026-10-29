North Carolina State Board of Elections — ENR (er.ncsbe.gov)
============================================================

Source (fetched 2026-10-27):
  https://er.ncsbe.gov/enr/20241105/data/contest.txt
  https://er.ncsbe.gov/enr/20241105/data/results_0.txt
  https://er.ncsbe.gov/enr/20241105/data/county.txt
  https://er.ncsbe.gov/enr/elections.txt

Election: 2024-11-05 general (ENR folder 20241105). elections.txt at fetch time lists
11/03/2026 first with arc "0", and https://er.ncsbe.gov/enr/20261103/data/contest.txt
already returns the 2026 general contest list.

Trimming: contest.txt keeps the ten contests used by the test, results_0.txt keeps only
the candidate rows of those contests, county.txt keeps the statewide (cid "0") row. Rows are byte-for-byte
the source's fields; the files keep NC's format (JSON array without the outer brackets,
CRLF, one object per line, trailing comma). Nothing else was changed.
