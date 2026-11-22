Massachusetts Secretary of the Commonwealth — PD43+ election search results
===========================================================================

Fetched 2026-11-22, HTTP 200, Content-Type text/html; charset=utf-8, with a descriptive
User-Agent. No cookies, no login, no bot challenge.

    https://electionstats.state.ma.us/elections/search/date:2026-11-03
    (linked from https://www.sec.state.ma.us/ele/eleres/eleresidx.htm)

Election: 2026-11-03 general, certified. The Elections Division's results page states that
results are published after certification and that nothing is published on election night.

The real page is 960 KB: a date picker, office/district selection lists and one row per
contest (271 contests on this date). The saved file keeps the table header and 15 complete
contest rows, byte-for-byte as served, wrapped in a minimal <html><body>:

  election-id-173531  U.S. Senate (four candidates, "All Others", "Blanks", total)
  election-id-173526  Governor (joint ticket names)
  election-id-173520  Attorney General
  election-id-173553  U.S. House, 1st Congressional
  election-id-173546  U.S. House, 9th Congressional
  election-id-173555  State Senate, Berkshire, Hampden, Franklin & Hampshire (one candidate)
  election-id-173581  State Senate, Cape and Islands (one candidate)
  election-id-173668  State Representative, 1st Barnstable
  election-id-173580  State Representative, Barnstable, Dukes and Nantucket
  election-id-173541  Governor's Council, 1st
  election-id-173637  District Attorney, Suffolk
  election-id-173614  County Commissioner, Barnstable County
  election-id-173595  Sheriff, Franklin County
  election-id-173613  County Charter Commission, Barnstable
  election-id-173536  Register of Probate, Middlesex County

Markup per contest: a <tr id="election-id-N"> holding four cells (year, office, district,
stage) and a nested <table class="candidates"> with one row per candidate
(td.candidate > div.name, div.party; td.number vote and share). Rows with
class "n_all_other_votes", "n_blank_votes" and "n_total_votes" are ballot statistics. The
winning row carries the "is_winner" class.

Per-office CSVs exist too (/elections/download/<id>/precincts_include:0/, one file per
contest); the adapter reads the single dated search page instead, so one request covers the
whole election.
