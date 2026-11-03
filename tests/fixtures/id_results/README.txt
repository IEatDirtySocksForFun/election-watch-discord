Trimmed real responses from Idaho's live results app, fetched on 2026-11-03 with a
descriptive User-Agent. Both files are the statewide "id" jurisdiction, cut down to a
few ballot items; the `election` object is unchanged. The endpoint is the data
document behind https://voteidaho.gov/election-results/.

2026_general.json GET https://results.voteidaho.gov/results/public/api/elections/id/nov2026/data
                   (6 of 145 ballot items kept: US Senate, US Representative District 1,
                   Governor, State Senator District 1, State Representative District 1
                   Seat A, Proposition 1). Fetched before polls closed: every vote count
                   is 0 and 0 of 44 precincts report. Governor keeps the qualified
                   write-in candidate.
2025_general.json GET https://results.voteidaho.gov/results/public/api/elections/id/nov2025/data
                   (3 of many ballot items kept; all have votes and 2 of 2 precincts
                   reporting, used to test vote and winner parsing).
