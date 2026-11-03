co_sos — Colorado Secretary of State results (Clarity ENR), 2026 general election
Fetched 2026-11-03 with the bot's User-Agent (one GET per file).

  https://results.enr.clarityelections.com/CO/elections.json
      -> elections.json   (verbatim; the 2026 General is first, EID 127407, 11/3/2026)
  https://results.enr.clarityelections.com/CO/127407/current_ver.txt
      -> "383193" on that date
  https://results.enr.clarityelections.com/CO/127407/383193/json/en/summary.json
      -> summary.json     (trimmed from 302 contests to 7: United States Senator,
                           Governor / Lieutenant Governor, Secretary of State,
                           Representative to the 120th United States Congress - District 1,
                           State Board of Education Member - Congressional District 1 and 3,
                           Colorado Supreme Court Justice - Hood)
  https://results.enr.clarityelections.com/CO/127407/383193/json/en/electionsettings.json
      -> electionsettings.json  (trimmed to the last-updated stamp
                                 "11/3/2026 6:52:34 AM MST")

Each kept contest object is verbatim from the live feed (C/CH/P/PCT/V/T/TP/PR/W). The count
had not started when the files were captured: every choice is 0 votes, every PR is 0, and TP
carries the district's precinct total (64 statewide, 3 for CO-01).

Two naming differences from the 2024 general snapshot in the parent directory:
Governor now appears as "Governor / Lieutenant Governor" (joint ticket, two names in one
choice), and the US Senate contest is "United States Senator". State Board of Education and
the regents are per-congressional-district contests, so the district stays in the LOCAL slug.
