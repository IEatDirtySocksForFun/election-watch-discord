Pennsylvania Department of State — electionreturns.pa.gov
=========================================================

Source (fetched 2026-10-27), 2024-11-05 general election (electionid 105, type G):
  https://www.electionreturns.pa.gov/api/ElectionReturn/GetOfficeData?officeId=2&methodName=GetOfficeData&electionid=105&electiontype=G&isactive=0
  https://www.electionreturns.pa.gov/api/ElectionReturn/GetOfficeData?officeId=11&...
  https://www.electionreturns.pa.gov/api/ElectionReturn/GetOfficeData?officeId=12&...
  https://www.electionreturns.pa.gov/api/ElectionReturn/GetOfficeData?officeId=13&...
  https://www.electionreturns.pa.gov/api/ElectionReturn/GetLatestElection?methodName=GetLatestElection

The API returns a JSON string that itself contains JSON; the fixtures reproduce that
(one JSON-encoded document per file).

Trimming: office_us_senate.json is the full United States Senator payload (two
candidates). office_us_house.json keeps the 1st-3rd congressional districts, the state
senate and state house files keep two districts each. One district's candidate array is
verbatim.

GetLatestElection (2026-10-27) reports the 2026 general as ElectionID 117,
ElectionDate 2026-11-03, which is what ELECTIONS uses; GetUpdatedTimeStamp returns ""
for archived elections, so the adapter leaves source_updated_at empty.
