oh_sos — Ohio Secretary of State live results (liveresults.ohiosos.gov)
Fetched 2026-10-27 with a descriptive User-Agent; the site is pointed at the 2026 general
election and nothing has been counted yet, so every vote count in these fixtures is 0.

  https://liveresults.ohiosos.gov/Api/v1/Contest/getContestList
      -> contest_list.json   (trimmed: statewide offices, US Senate, US House CD-01/02,
                              State Senate 01/03, State Representative 01/02,
                              State Issue 3, one Supreme Court race)
  https://liveresults.ohiosos.gov/Api/v1/Contest/GetContestDetailsObject
        ?contestName=<parent>&contestObjectId=<grouping>&resultsLevel=&countyId=
      -> governor.json, attorney_general.json, us_senator_special.json,
         supreme_court.json, issue_3.json           (first 3 + last county row kept)
      -> congress_01.json, congress_02.json         (all rows kept: 3 and 9 counties)
      -> state_senate_01.json, state_senate_03.json, state_rep_01.json, state_rep_02.json

Trimmed by dropping the contest groups/parents not listed above and, for the statewide
contests, most of the 88 county rows — every kept row object is untouched, so the parse
sees the real shape (statewide vote totals in these files are therefore partial; the
adapter sums whatever rows the API returns).

Ohio's 2026 US Senate race is the unexpired term ("U.S. Senator - Unexpired Term Ending
01/03/2029"), which maps to the special-election key OH SEN S.
