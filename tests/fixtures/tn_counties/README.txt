Fixtures for the tn_counties adapter (Shelby County, Enhanced Voting export).

Captured 2026-10-27 with the plain bot User-Agent
"election-watch-discord/0.1 (open-source election results bot)".

shelby_2024_export.json
  https://app.enhancedvoting.com/cdn/results/shelby-county-tn/export-General11052024.json
  (2.2 MB live; electionDate 2024-11-05, 142 precincts, createdAt 2025-01-02.)
  Trimmed to 8 of the export's ballotItems: United States President and Vice President,
  United States Senate, US House of Representatives District 8, US House of Representatives
  District 9, Tennessee Senate District 30, TN House of Representatives District 83,
  "Bartlett Alderman - Position 4 Bartlett", "Collierville Mayor Collierville".
  Each option keeps id/name/ballotOrder/voteCount/politicalParty; the per-precinct and
  per-voting-method arrays (groupResults, precinctResults) were dropped because those arrays are
  ~99% of the file and the adapter does not read the arrays. Contest names, candidate names,
  party strings and vote counts are untouched.

Not captured: the 2026 general export (export-General11032026.json answered 404 on
2026-10-27; Enhanced Voting publishes county exports on election night).

Nothing was synthesised.
