South Carolina Election Commission — Clarity/Scytl ENR (enr-scvotes.org)
=========================================================================

Source (fetched 2026-10-27):
  https://results.enr.clarityelections.com/SC/122436/current_ver.txt
      -> 359624
  https://results.enr.clarityelections.com/SC/122436/359624/json/en/summary.json
  https://www.enr-scvotes.org/SC/122436/web.345435/   (human app, same data)
  https://results.enr.clarityelections.com/SC/elections.json  -> []  (2026 index empty)

Election: 2024-11-05 general, Clarity election id 122436. Format: JSON array of
contests with parallel arrays P/CH/V/PCT/W (party, choice name, votes, percent,
winner) plus C (contest name), T (contest total votes), TP (precincts total) and
PR (precincts reporting).

Trimming: the full summary holds 212 contests; this fixture keeps 11 of them
(president, one U.S. House district, state Senate 1 and 19, state House 1 and 35,
Solicitor Circuit 1, Amendment 1, one school board race, one mayor race, and
"Straight Party 1") with every parallel-array entry intact. The saved JSON is a
straight subset of the live array; nothing was edited or reordered.

The 2026 general election id was not listed in /SC/elections.json on 2026-10-27,
so the adapter discovers it from that index at runtime.
