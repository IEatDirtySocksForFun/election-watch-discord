Kansas Secretary of State — unofficial results (sos.ks.gov/ent/)
==============================================================

Source (fetched 2026-10-27, HTTP 200, text/html):

  https://www.sos.ks.gov/ent/statewide.html
  https://www.sos.ks.gov/ent/national.html
  https://www.sos.ks.gov/ent/ks-house.html
  https://www.sos.ks.gov/ent/ks-boe.html
  https://www.sos.ks.gov/ent/judicial.html
  https://www.sos.ks.gov/ent/constitutional.html

The site serves one election at a time. On 2026-10-27 it served the 2026-08-04 primary
("Last Update: 08/05/2026 4:25 AM", 4150 of 4150 precincts); older elections are only
published as PDFs, so no 2024 general HTML exists to capture.

Source (fetched 2026-11-03, HTTP 200, text/html):

  https://www.sos.ks.gov/ent/statewide.html      (Last Update: 11/03/2026 7:59 AM)
  https://www.sos.ks.gov/ent/national.html       (Last Update: 11/03/2026 7:59 AM)

On 2026-11-03 the site served the 2026 general election, with the general-election
navigation (which adds a ks-senate.html tab). ks-boe still answered with the August
primary page and is skipped as stale.

Fixture files (page slices, unmodified markup):

2026_primary_constitutional.html
  Page header + navigation + the single "Constitutional Amendment" contest block.
  This is the only non-party contest in the primary, so it is the one that is parsed.

2026_primary_statewide.html
  Page header + navigation + the first four contest blocks of statewide.html
  ("Governor / Lt. Governor (D)", "Governor / Lt. Governor (R)",
  "Secretary of State (D)", "Secretary of State (R)"). Every block on this page is one
  party's half of a primary, which the parser skips; the test uses the same real blocks
  with the " (D)"/" (R)" heading suffix removed to exercise the general-election path.

2026_general_statewide.html
  Page header + navigation + the first two contest blocks of the 2026-11-03 general
  statewide.html ("Governor / Lt. Governor", "Secretary of State"). Every candidate row
  starts with the party code ("D-Cindy Holscher", "R-Ty Masterson", "UK-Scott E. Morgan");
  all counts are 0 because the count had not started.

2026_general_national.html
  Page header + navigation + the first two contest blocks of the 2026-11-03 general
  national.html ("United States Senate", "United States House of Representatives 1").

Trimming applied to every file, nothing else:

  - the <select> county dropdown in the header is replaced by
    "<select><!-- county list trimmed --></select>";
  - each kept contest block has its collapsed map panel
    ("<div class='race-details collapse' ... <!-- end of .race-details -->") removed;
  - only the contest blocks named above are kept; the rest of the page is dropped.

Candidate rows, precinct lines, "Total Votes" rows, the checkbox ids and the
"Last Update" span are untouched, so every number asserted in tests comes from the state.
