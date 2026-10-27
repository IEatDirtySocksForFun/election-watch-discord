Georgia Secretary of State — results.sos.ga.gov
===============================================

Source (fetched 2026-10-27):
  https://results.sos.ga.gov/cdn/results/Georgia/export-2020NovGen.json

Election: 2020-11-03 general (the trimmed fixture), because the 2024 export is 33 MB.
The export is served per general election as export-<YYYY>NovGen.json; 2024 resolves
(HTTP 200), 2026 does not exist yet (HTTP 404 on 2026-10-27).

Trimming: results.ballotItems keeps nine contests — President, both 2020 Senate races
(the regular Perdue seat and the Loeffler special), US House District 1, State Senate
District 1, State Senate Dist 5/Senador Estatal Dist 5 (the bilingual name variant),
State House District 1, Public Service Commission District 4 and Constitutional
Amendment #1. Candidate arrays, groupResults and precinctResults are verbatim.
localResults keeps one county; the adapter does not parse it. `reportingStatuses` was
null in the source and stays null.
