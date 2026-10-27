270toWin poll-list fragments
============================

The "Latest 2026 <office> Election Polls" pages are rendered from:

  https://www.270towin.com/polls/php/get-polls-list-v2.php
    ?election_year=2026
    &election_types[]=S                (senate; G governor, H house)
    &election_subtypes[]=PE&election_subtypes[]=PR
    &election_subtypes[]=GE&election_subtypes[]=GR
    &limit=40

Fetched: 2026-10-27 15:35 UTC
User-Agent used for the fetch: Mozilla/5.0 (compatible; election-watch-discord/0.1)

The response is an HTML fragment (`<table id="polls-list">`) that the site
injects with jQuery. Only `limit=40` is accepted by the endpoint (100/200/500
and a partial `election_subtypes` list all return 404).

Files (each trimmed to the table header plus the selected real rows, unmodified):
- senate-polls.html  the 2026 Senate list's first six rows: 11970 (ME, Marist
                     Poll), 11968 (MI, Glengariff), 11967 (TX, N+ Univision),
                     11964 (SC), 11961 (NC, Quantus Insights) and 11960
                     (MI, Michigan State Univ.)
- house-polls.html   rows 11963 (CA-34), 11931 (MI-07), 11928 (AR-02),
                     11917 (TX-15) and 11887 (Alaska at-large, linked as
                     "/2026-house-polls/alaska" with no district segment)

Row shape: Date | Type | Poll source (+ sample/MoE in div.poll_metadata) |
Location | nested candidate table with `fill-D`/`fill-R`/`fill-I` classes.
