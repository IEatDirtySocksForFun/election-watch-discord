oh_sos — Ohio Secretary of State live results, 2026 general count in progress
Fetched 2026-11-04 (UTC; election night 2026-11-03) with the bot's User-Agent.

  https://liveresults.ohiosos.gov/Api/v1/Contest/getContestList
      -> contest_list.json   (trimmed to the Governor and Lieutenant Governor,
                              U.S. Senator, Representative to Congress and
                              Secretary of State parents; the Representative group
                              keeps only the District 01 grouping)
  https://liveresults.ohiosos.gov/Api/v1/Contest/GetContestDetailsObject
        ?contestName=<parent>&contestObjectId=<grouping>&resultsLevel=&countyId=
      -> governor.json       (contestList trimmed to the Adams, Allen, Ashland,
                              Ashtabula and Wyandot county rows; every kept row
                              object verbatim, contestStatistics whole)
      -> congress_01.json    (all three county rows: Clinton, Hamilton, Warren)

The governor file keeps five of the 88 county rows, so its vote and precinct totals are
partial by design; congress_01.json is complete for the district. Counting was under way:
the Senate contest had all 88 counties reporting 4,126,726 votes, and OH-01 had 778 of
784 precincts reporting.
