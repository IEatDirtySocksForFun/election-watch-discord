NBC results fixtures trimmed from the public "firecracker" feed used by NBC's
results pages. Not a documented API; the adapter is a scraper and disabled by
default.

- 2024-senate.json: https://www.nbcnews.com/firecracker/api/v2/national-results/2024-elections/senate-results
  Trimmed to lastModified, electionSeason and mapData entries for AZ, DE and TX
  Senate (candidate names, parties, votes, percentages and href).
- 2024-house.json: https://www.nbcnews.com/firecracker/api/v2/national-results/2024-elections/house-results
  Trimmed to lastModified, electionSeason and the TX-38 mapData entry.
- 2026-senate.json: https://www.nbcnews.com/firecracker/api/v2/national-results/2026-elections/senate-results
  Trimmed to lastModified, electionSeason and the AK and CO Senate entries. The
  2026 general feed opened before any votes were counted, so every vote,
  percentage and reporting field is 0; lastModified is ISO 8601 here.
- 2026-governor.json: https://www.nbcnews.com/firecracker/api/v2/national-results/2026-elections/governor-results
  Trimmed to lastModified, electionSeason and the AL Governor entry (0 votes).
- 2026-house.json: https://www.nbcnews.com/firecracker/api/v2/national-results/2026-elections/house-results
  Trimmed to lastModified, electionSeason and the AL-01 entry (0 votes).

2024 files were fetched 2026-10-27 16:05 UTC; the 2026 files 2026-11-03 12:47 UTC.
