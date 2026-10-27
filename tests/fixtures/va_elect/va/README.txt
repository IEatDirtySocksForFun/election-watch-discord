va_elect — Virginia Dept. of Elections ENR (Enhanced Voting public API)
Fetched 2026-10-27 with a descriptive User-Agent.

  https://enr.elections.virginia.gov/results/public/api/elections/virginia
      -> elections.json          (trimmed: 2023 general, 2024 general, 2025 general, 2026 general)
  https://enr.elections.virginia.gov/results/public/api/elections/virginia/2025-November-General/data
      -> 2025_general.json       (trimmed: Governor, Lt. Governor, Attorney General, HD-01,
                                  HD-100, Sheriff (Lexington City, Rockbridge County),
                                  Mayor (Town of Brodnax); statistics/voterRegistration dropped)
  https://enr.elections.virginia.gov/results/public/api/elections/virginia/2026-November-General/data
      -> 2026_general.json       (trimmed: US Senate, US House 01 and 11, HD-20 special,
                                  Constitutional Amendment Question 1, Mayor - Belle Haven;
                                  statistics/voterRegistration dropped)

Trimmed by dropping the other ballot items, the per-locality `statistics` array, the
per-precinct `crossCounties`/group breakdowns and the full `election`/`jurisdiction`
objects. Every contest object keeps all of its own keys, so the parse sees the real shape.
2025 is a complete count (133/133 precincts reporting); 2026 was fetched three weeks
before election day, so its vote counts are all zero — that fixture is for shape and
for the "no precincts reported yet" case.
