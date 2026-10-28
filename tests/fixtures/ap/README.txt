AP fixtures are trimmed examples based on the AP Elections API v3 response schema.

- 2024-TX-SEN.json: synthetic /v3/elections response for Texas Senate (2024-11-05)
- 2024-CA-HOUSE.json: synthetic /v3/elections response for California House District 12 (2024-11-05)
- 2024-AK-HOUSE.json: synthetic /v3/elections response for Alaska's at-large House seat
  (2024-11-05). AP numbers House seats instead of naming them: an at-large seat is queried
  as SeatNum=1 and comes back as seatName "1", not "AL" (RaceKey normalises a single-seat
  state's seat 1 to XX-AL).

Schema notes the adapter relies on (electionwatch/sources/calls/ap.py):
- Query: officeID S/G/H + statePostal + raceType=G + electionDate; House races add SeatNum
  as an integer without leading zeros — SeatNum=1 for an at-large seat.
- Response: races[] with statePostal, officeID, seatName, candidates[] (first, last, party,
  voteCount, winner="X"), reportingUnits[] carrying the state-level lastUpdated.

Date: 2026-10-27 14:44 UTC

