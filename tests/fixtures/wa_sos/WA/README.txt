Washington Secretary of State — statewide results export (results.vote.wa.gov)
=============================================================================

Source (fetched 2026-10-27):
  https://results.vote.wa.gov/results/20241105/export/20241105_AllState.xml
  https://results.vote.wa.gov/results/20241105/          (human page, same data)
  https://results.vote.wa.gov/results/20241105/Export.html  (lists every export file)

Election: 2024-11-05 general. Format: plain XML, one <Result> per candidate per
contest with RaceName, Candidate, Party, Votes, PercentageOfTotalVote,
JurisdictionName.

Trimming: the real AllState export (all statewide, federal, legislative,
judicial and measure rows) was reduced to eight contests / 23 rows covering
U.S. Senate, one congressional district, Governor, Attorney General, one
legislative district's three contests (Senator, Representative Pos. 1 and
Pos. 2) and one judicial position, plus one ballot measure (Yes/No choices,
blank Party). Element text is byte-for-byte from the source; only surrounding
indentation was added by ElementTree.indent().

The 2026 general folder (https://results.vote.wa.gov/results/20261103/) returned
HTTP 404 on 2026-10-27; the export URL for a date is
https://results.vote.wa.gov/results/<YYYYMMDD>/export/<YYYYMMDD>_AllState.xml.
