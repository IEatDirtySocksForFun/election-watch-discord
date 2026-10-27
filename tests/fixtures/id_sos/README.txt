Idaho Secretary of State results archive fixtures
=================================================

Recorded: 2026-10-27, from the 2024 general election archive.

index.html
  https://archive.voteidaho.gov/results/2024/general/ (HTTP 200, 24,834 bytes) -
  kept whole: it is the page that links congressional.html, legislative.html,
  presidential.html, county.html, measures.html, turnout.html, xml.html.

congressional.html
  https://archive.voteidaho.gov/results/2024/general/congressional.html
  (HTTP 200, 7,944,167 bytes). Trimmed to the "District 1 US Representative"
  section (heading + its reactable JSON block, unmodified). Real totals:
  Russ Fulcher (REP) 331,049 / Kaylee Peterson (DEM) 118,656 / Matt Loesby (LIB)
  9,594 / Brendan J. Gomez (CON) 6,933.

legislative.html
  https://archive.voteidaho.gov/results/2024/general/legislative.html
  (HTTP 200, 38,369,011 bytes). Trimmed to the "District 1" section up to (not
  including) "District 1 Representative B": the District 1 Senator and District 1
  Representative A headings. The reactable payloads are cut down to the columns the
  adapter reads (ContestantName / TotalVotes / Percent); reactable's styling-only
  payload (`{"x":{"options":...}}`, ~500 KB) is emptied and the inline <table>
  skeletons and <style> blocks are dropped. Numbers are the published ones:
  Jim Woodward (REP) 23,545 / Daniel "Dan" Rose (IND) 7,314 for the Senate seat.

No fixture was made for presidential.html: Idaho 2026 has no presidential race and
governor/statewide pages only appear in gubernatorial years, so the statewide
headings are mapped but not exercised by a fixture.
