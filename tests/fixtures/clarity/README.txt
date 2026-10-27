Clarity / Scytl county reporting feeds
=====================================

Source (fetched 2026-10-27):
  https://results.enr.clarityelections.com/WV/126209/current_ver.txt
  https://results.enr.clarityelections.com/WV/126209/<ver>/json/en/summary.json
  https://results.enr.clarityelections.com/WV/126209/<ver>/json/en/electionsettings.json
  https://results.enr.clarityelections.com/IL/McLean/127349/current_ver.txt   (382192)
  https://results.enr.clarityelections.com/IL/McLean/127349/382192/json/en/summary.json

wv_2026_primary_summary.json  — WV 2026 primary (entityname "WV", websiteupdatedat
  6/15/2026). Keeps six contests: three party-suffixed ones ("... - REP": U.S. Senator,
  1st Congressional District, 1st Senatorial District) and three non-partisan ones
  (Supreme Court of Appeals Division 1, Intermediate Court of Appeals, Greater
  Huntington Park and Recreation District). Arrays and vote counts verbatim.
wv_2026_primary_settings.json — only websiteupdatedat, entityname, stateprecinctsreporting
  from the real electionsettings.json.
il_mclean_2026_general_summary.json — McLean County IL 2026 general (election id 127349,
  electiondate 11/3/2026, sample data uploaded 10/26/2026; vote counts are 0 because the
  election has not happened). Keeps twelve contests covering federal, statewide,
  legislative, county, judicial-retention and township-question names.

summary.json is a JSON array here; older Clarity builds wrap it in {"Contests": [...]},
which the parser also accepts.

wv_2024_general_summary.json — West Virginia 2024 general (election id 122766,
current_ver.txt 356048), fetched 2026-10-27:
  https://results.enr.clarityelections.com/WV/122766/current_ver.txt
  https://results.enr.clarityelections.com/WV/122766/356048/json/en/summary.json
The full summary holds 129 contests; this fixture keeps 14 (president, U.S. Senate,
1st Congressional District, governor, auditor, treasurer, commissioner of
agriculture, attorney general, state Senate 1 and 17, House of Delegates 1 and 100,
circuit-judge division 2 and "Amendment No.1: ..."). Arrays and vote counts verbatim.
This is the feed configured in clarity.ELECTIONS for 2024-11-05.
