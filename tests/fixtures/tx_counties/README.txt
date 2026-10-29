Fixtures for the tx_counties adapter (Clarity counties + Harris County's own JSON feed).

Captured 2026-10-27 with a plain curl/httpx GET. The first three files were fetched with
"Mozilla/5.0 (compatible; election-watch-discord/0.1; ...)"; everything from the
galveston_* files on was fetched with the plain bot User-Agent
"election-watch-discord/0.1 (open-source election results bot)".

denton_2024_summary.json
  https://results.enr.clarityelections.com/TX/Denton/122426/355989/json/en/summary.json
  (Election id 122426 = "2024 General Election", version 355989.)
  Trimmed to 7 contests: President/Vice President, United States Senator, U.S.
  Representative District 26, State Representative District 65, Justice of the Supreme
  Court Place 2, Sheriff, City of The Colony Mayor. Contest objects kept verbatim.

el_paso_2024_summary.json
  https://results.enr.clarityelections.com/TX/El_Paso/122595/355580/json/en/summary.json
  (Election id 122595 = "NOVEMBER 2024 GENERAL ELECTION", version 355580.)
  Trimmed to 7 contests: United States Senator, US Representative District 16,
  Railroad Commissioner, Chief Justice 8th Crt of App Dist, Justice 8th Crt of App Dist
  Place 2, Sheriff, Mayor City of El Paso. Kept verbatim; note the abbreviated contest
  names ("Crt of App Dist") this county uses.

denton_2026_summary.json
  https://results.enr.clarityelections.com/TX/Denton/127398/382120/json/en/summary.json
  (Election id 127398 = "2026 General Election", version 382120, captured with zero
  votes counted — the file is live before election night.)
  Trimmed to 8 contests: United States Senator, U.S. Representative District 26, Governor,
  State Representative District 65, Justice 15th Court of Appeals District Place 2, two
  City of The Colony places, CITY OF DALLAS PROPOSITION A.

denton_elections.json
  https://results.enr.clarityelections.com/TX/Denton/elections.json
  Trimmed from 88 entries to the six whose dates matter for discovery (2020 general,
  2024 general, the 2026 primaries/runoffs and the 2026 general).

No response was edited beyond selecting contests/entries; nothing was synthesised.

--- added with the eleven new counties (all 2024 files: election id, version) -------------

Each is {county}_{year}_summary.json from
  https://results.enr.clarityelections.com/TX/{County}/{eid}/{ver}/json/en/summary.json

galveston_2024_summary.json    122842 / 356002   President/Vice President, U.S. Senator,
    U.S. Representative District 14, Justice Supreme Court Place 2, Sheriff,
    County Commissioner Precinct 1
galveston_2026_summary.json    127478 / 381904   United States Senator, United States
    Representative District 14, Governor, Justice Supreme Court Place 2 Unexpired Term
    (live before election night, zero votes)
bell_2024_summary.json         122428 / 355990   US Senator, US Representative Dist 11,
    Justice Supreme Court Pl 2, Presiding Judge Ct of Crim App, Member State BoE Dist 10,
    State Representative Dist 54, Sheriff  (heavily abbreviated contest names)
comal_2024_summary.json        122856 / 355369   President and Vice President, United
    States Senator, United States Representative District 21, "State Representative,
    District 73" (double space), Justice 3rd Court of Appeals District Place 2
    (this county publishes no party codes at all)
midland_2024_summary.json      122496 / 355579   President/Vice President, US Senator,
    Chief Justice 11th Ct of App Dist, Sheriff
gregg_2024_summary.json        122313 / 355603   PRESIDENT/VICE-PRESIDENT, U.S. SENATOR,
    U.S. REPRESENTATIVE DISTRICT 1, JUSTICE 6TH COURT OF APPEALS DISTRICT PLACE 2, SHERIFF
    (all-caps feed, hyphenated "VICE-PRESIDENT")
hood_2024_summary.json         122858 / 355543   President/Vice-President, United States
    Senator, "U. S. Representative District 25" (spaced initials), "Justice, Superme
    Court, Place 6" (county typo), Chief Justice 2nd Court of Appeals District, Sheriff
kaufman_2026_summary.json      127485 / 380761   US Senator, US Representative District 5,
    Governor, "Comm General Land Office", "Justice, Supreme Court, Pl 2 Unexp"
    (live before election night, zero votes)
rockwall_2024_summary.json     122491 / 355310   US Senator, US Representative Dist 4,
    Justice Supreme Court Pl 2, Sheriff  ("REP Ted Cruz" style party prefixes)
rusk_2024_summary.json         122760 / 355999   US Senator, US Representative Dist 1, Sheriff
lamar_2024_summary.json        122497 / 351606   US Senator, US Representative Dist 4,
    Justice Supreme Court Pl 2, County Commissioner Pct 1
bastrop_2024_summary.json      122753 / 356001   President/Vice President, US Senator,
    US Representative Dist 10, US Representative Dist 27, Justice 3rd Ct of App Dist Pl 2,
    Sheriff
travis_2024_summary.json       122432 / 355984   President / Vice President, United States
    Senator, US Representative District 35, US Representative District 37, "State Senator,
    District 14", "Presiding Judge, Court of Criminal Appeals, Place 1", Sheriff, "Travis
    County Proposition A"  (candidates carry parenthesised party prefixes: "(R) Ted Cruz";
    the presidential rows name only the nominee)

Hood's "Rejected write-ins" (227 votes) and "Unassigned write-ins" (0 votes) rows are left
in the fixture on purpose: the parser must drop both rows and keep the counted write-in
"Chad Hagg" (93 votes).

--- Added 2026-10-27 (design/state-fixes; plain bot User-Agent
    "election-watch-discord/0.1 (open-source election results bot)", one GET each) ---

Contest objects are kept verbatim; only the contest list is trimmed.

tarrant_2024_general.json
  https://results.enr.clarityelections.com/TX/Tarrant/122489/355610/json/en/summary.json
  Id from https://www.tarrantcountytx.gov/en/elections/past-election-information/2024-archives/november-5--2024--general-election-results.html
  (Tarrant's elections.json lists nothing). Kept: United States Senator (821,473 votes),
  United States Representative, District 12.

tarrant_2026_general.json
  https://results.enr.clarityelections.com/TX/Tarrant/127297/379881/json/en/summary.json
  Id from https://www.tarrantcountytx.gov/en/elections/current-election-information.html
  ("Graphical Election Night Reporting Tool (ENR)"); electionsettings websiteupdatedat
  10/7/2026. Zero votes. Kept: Senator, District 12, Governor,
  "Justice, Supreme Court, Place 2, (Unexpired Term)".

fort_bend_2024_general.json
  https://results.enr.clarityelections.com/TX/Fort_Bend/122596/354717/json/en/summary.json
  Id from https://www.fortbendcountytx.gov/government/departments/elections-voter-registration/2024-election-results
  Kept: Senator (356,419), "United States Representative, District 7 District 7",
  "State Senator, District 17 District 17", "State Representative, District 26 District 26"
  (Fort Bend repeats the district in the contest name).

fort_bend_2026_general.json
  https://results.enr.clarityelections.com/TX/Fort_Bend/127480/381352/json/en/summary.json
  Id from the county's 2026-election-results page; websiteupdatedat 10/19/2026. Zero votes.

bexar_2026_general.json
  https://results.enr.clarityelections.com/TX/Bexar/127291/379718/json/en/summary.json
  Id from https://elections.bexar.gov/ (redirect target of www.bexar.org/1568/Elections).
  Bexar's elections.json is []. Bexar reported 2024 with an ES&S HTML summary
  (home.bexar.org/el45a.html, 740,804 Senate votes), so there is no 2024 Clarity file.

harris_2024_resultsbyballot.json
  https://appfiles.harrisvotes.com/harrisvotes/prd/Data/1124/resultsbyballot.json
  The JSON behind https://app.harrisvotes.com/electionresults/electionday (embedded in
  https://www.harrisvotes.com/Election-Results/Live-Results). Header kept verbatim
  ("o": 1 = official canvass), 6 of 224 contests: President / Vice President, United States
  Senator (1,548,914 votes), District 7, District 18 - Unexpired Term, Railroad
  Commissioner, State Representative District 134.

harris_2026_resultsbyballot.json
  https://appfiles.harrisvotes.com/harrisvotes/prd/Data/1126/resultsbyballot.json
  2026 general, zero votes ("dt": 2026-09-17), 6 of 231 contests: Senator, District 7,
  Governor, Chief Justice 15th Court of Appeals, County Judge, City of Houston Prop A.
