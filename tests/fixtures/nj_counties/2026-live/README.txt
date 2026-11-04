nj_counties — New Jersey county Clarity ENR feeds, 2026 general live count
Fetched 2026-11-04 (UTC; election night 2026-11-03) with the bot's User-Agent.

Four of the fourteen configured counties published the 2026 general when the files were
captured:

  Cumberland  https://results.enr.clarityelections.com/NJ/Cumberland/127562/384792/json/en/summary.json
  Essex       https://results.enr.clarityelections.com/NJ/Essex/127486/384863/json/en/summary.json
  Passaic     https://results.enr.clarityelections.com/NJ/Passaic/127401/384809/json/en/summary.json
  Somerset    https://results.enr.clarityelections.com/NJ/Somerset/127569/384975/json/en/summary.json

  .../<version>/json/en/electionsettings.json -> <county>_settings.json
      (each trimmed to websiteupdatedat)

summary.json files are trimmed to the contests this adapter maps across counties:

  cumberland_summary.json   United States Senator, United States House of Representatives
                            (no district: stays county-local), Sheriff
  essex_summary.json        United States Senator, Member of the House of Representatives
                            (8th/10th/11th Congressional District)
  passaic_summary.json      Member of the United States Senate, Member of the House of
                            Reps - 5th/9th/11th Cong
  somerset_summary.json     U.S. Senator, U.S. Representative 7th/12th

Every kept contest object is verbatim (C/CH/P/PCT/V/T/TP/PR/W). Counting was under way:
Cumberland had 78 of 106 precincts reporting, Essex 556 of 578, Passaic 135 of 283 and
Somerset 267 of 267.
