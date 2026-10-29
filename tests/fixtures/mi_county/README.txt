Ottawa County, Michigan — Clarity/Scytl count feed

Recorded 2026-10-27 with a plain GET, a descriptive User-Agent
("election-watch-discord/0.1") and a Referer of the jurisdiction index page. No login,
no agreement, no bot-challenge round trip.

    https://results.enr.clarityelections.com/MI/Ottawa/elections.json
    https://results.enr.clarityelections.com/MI/Ottawa/127391/current_ver.txt   -> 381490
    https://results.enr.clarityelections.com/MI/Ottawa/127391/381490/json/en/summary.json
    .../381490/json/en/electionsettings.json

ottawa_elections.json        first 4 of the 10 entries the index returned (the 11/03/2026
                            general is EID 127391)
ottawa_current_ver.txt       "381490"
ottawa_2026_general_summary.json
                            4 of the 68 contests (United States Senator, US House 2/3/4),
                            exactly as served (a bare JSON list)
ottawa_2026_general_settings.json
                            whole document (28 KB; carries websiteupdatedat)

The 2026 general feed is already configured and serves the real candidate list with
0 votes (websiteupdatedat 10/19/2026), which is why precinct totals are present but
nothing has reported.

Oakland, Macomb and Genesee also live on the vendor host, but each county's elections.json was
empty on the capture date (Clarity fills it when the county configures the general).
