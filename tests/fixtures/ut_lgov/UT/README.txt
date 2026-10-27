Utah — electionresults.utah.gov (Enhanced Voting CDN export)
============================================================

Source (fetched 2026-10-27):
  https://app.enhancedvoting.com/cdn/results/Utah/export-general11052024.json
  https://electionresults.utah.gov/results/public/Utah/elections/general11052024
      (human page served by the same export)

Election: 2024-11-05 general. Format: one JSON document (29 MB in full) with
electionDate / electionName / createdAt and results.ballotItems[], each item
carrying ballotOptions[] with name, politicalParty, voteCount.

Trimming: the real document was reduced to 11 ballot items (U.S. President,
U.S. Senate, U.S. House 1, Governor, Attorney General, State Auditor, State
Treasurer, State Senate 2, State House 59, State School Board 3, one Supreme
Court retention question, Constitutional Amendment B) and to the
electionDate/electionName/createdAt + results.id/name/ballotItems/
reportingStatuses keys. Item text, ids and vote counts are unchanged;
localResults (county-level breakdown) was dropped.

The 2026 general page is live
(https://electionresults.utah.gov/results/public/Utah/elections/general11032026)
but export-general11032026.json returned HTTP 404 on 2026-10-27; the vendor
posts the export on election night.
