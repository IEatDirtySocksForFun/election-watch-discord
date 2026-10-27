Minnesota Secretary of State — results file host (electionresultsfiles.sos.mn.gov)

Recorded 2026-10-27 with a plain GET and a descriptive User-Agent
("election-watch-discord/0.1"). No login, no cookies, no bot-challenge round trip:
the SOS results website itself (electionresults.sos.mn.gov) is behind Radware and
answers a __uzdbm_* challenge, but these files are served ungated.

    https://electionresultsfiles.sos.mn.gov/20261103/ussenate.txt
    https://electionresultsfiles.sos.mn.gov/20261103/ushouse.txt
    https://electionresultsfiles.sos.mn.gov/20261103/stsenate.txt
    https://electionresultsfiles.sos.mn.gov/20261103/LegislativeByDistrict.txt
    https://electionresultsfiles.sos.mn.gov/20261103/governor.txt
    https://electionresultsfiles.sos.mn.gov/20261103/auditor.txt
    https://electionresultsfiles.sos.mn.gov/20261103/judicial.txt
    https://electionresultsfiles.sos.mn.gov/20261103/cntyRaceQuestions.txt
    https://electionresultsfiles.sos.mn.gov/20261103/Cntytbl.txt

The files are trimmed to the first few rows of each (a whole contest for the small
ones); bytes are otherwise untouched (the feed is Windows-1252). The 2026 general
folder already carries the SOS's published test/early numbers, which is why several
contests show partial precinct counts.

governor.txt and auditor.txt are the only statewide-executive files: there is no
attorney-general or secretary-of-state export on this host (404 for every spelling
tried), so those two races are out of reach here.
