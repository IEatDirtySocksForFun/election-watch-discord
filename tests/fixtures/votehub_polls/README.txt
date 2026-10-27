VoteHub public polling API
==========================

URL: https://api.votehub.com/polls
Fetched: 2026-10-27 15:29 UTC
User-Agent used for the fetch: election-watch-discord/0.1 (polling bot)

The endpoint returns one JSON array of every poll VoteHub tracks (~5,960
records, 2.9 MB, no auth, no pagination; query parameters are ignored). The
fixture keeps eight real records, unmodified, chosen to cover the shapes the
adapter has to handle:

- 2026 Michigan      US Senate, Tyson Group, partisan "REP", sponsor listed
- 2026 Ohio          US Senate, Bowling Green State University/YouGov, LV, 4 answers
- 2026 Texas         US Senate
- 2026 North Carolina US Senate
- 2026 Iowa          Governor
- 2026 IL-04         US House, seat_name "IL-04", sponsor listed
- 2026               generic ballot (no state in "subject" -> must be ignored)
- JD Vance           approval poll (not a race -> must be ignored)

Record order is the API's own (newest first); the adapter re-sorts by end date.
