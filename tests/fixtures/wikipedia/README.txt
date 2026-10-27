Wikipedia opinion-polling tables (North Carolina Senate, 2026)
=============================================================

Article: 2026 United States Senate election in North Carolina
Fetched: 2026-10-27 15:38 UTC
User-Agent used for the fetch: election-watch-discord/0.1 (polling bot)

Request (one call, both props):

  https://en.wikipedia.org/w/api.php?action=parse
    &page=2026_United_States_Senate_election_in_North_Carolina
    &prop=text|wikitext&format=json&formatversion=2&redirects=1

Files:
- north-carolina-senate.html
  The parsed page HTML, trimmed to the "General election" heading plus the
  first two individual-poll wikitables (nominee polling, then a hypothetical
  matchup table). Everything before/after is dropped; the tables themselves are
  unmodified, including the `rowspan` groupings Wikipedia uses for sub-samples
  ("with leaners") that the adapter deliberately skips.

- north-carolina-senate.wikitext
  The article's party-section candidate lists ("Republican primary",
  "Democratic primary", "Libertarian primary", "Green primary",
  "Independents"), two lines per candidate and at most 30 bullets per section,
  followed by the "General election" heading. The adapter uses these lists to
  map a poll column ("Don Brown") to a party when the column header carries no
  "(R)"/"(D)" marker.

Why this article: it has a general-election table whose pollster cells carry
"(R)"/"(D)" markers (Harper Polling (R), Change Research (D)), a bipartisan
pair (Beacon Research/Shaw & Co. Research) that must not be flagged partisan,
a "through <date>" aggregate table that must be ignored, and a rowspan group.
