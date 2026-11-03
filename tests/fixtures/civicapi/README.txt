Trimmed real civicAPI responses, fetched with a descriptive User-Agent: TX on 2026-10-27,
GU and NH on 2026-11-03.

search_tx_2026.json   GET https://civicapi.org/api/v2/race/search?country=US&province=TX&startDate=2026-11-03&endDate=2026-11-03&limit=1000
                      (6 of 725 races kept; candidate lists cut to 3)
race_tx_sen_2024.json GET https://civicapi.org/api/v2/race/107793   (2024 TX US Senate, real, called)
race_tx_sen_2026_test.json GET https://civicapi.org/api/v2/race/87438 (2026 TX US Senate: civicAPI test data,
                      "test_data": true with a fake winner; must never be posted)
search_gu_2026.json   GET https://civicapi.org/api/v2/race/search?country=US&province=GU&startDate=2026-11-03&endDate=2026-11-03&limit=1000
                      (2026 GU general, live results; 2 of 6 races kept: Governor and Delegate)
race_gu_gov_2026.json GET https://civicapi.org/api/v2/race/160886   (2026 GU Governor, live results,
                      "test_data": false and no called_at; region_results cut to 1)
race_gu_delegate_2026.json GET https://civicapi.org/api/v2/race/160887 (2026 GU Delegate, live results,
                      "test_data": false and no called_at; region_results cut to 1)
search_nh_2026.json   GET https://civicapi.org/api/v2/race/search?country=US&province=NH&startDate=2026-11-03&endDate=2026-11-03&limit=1000
                      (2026 NH general, live results; 5 of 308 races kept: US Senate, Governor, the
                      State Senate chamber summary, State Senate District 1 and Question 2)
race_nh_sen_2026.json GET https://civicapi.org/api/v2/race/87283 (2026 NH US Senate, live results,
                      "test_data": false and no called_at; region_results cut to 1 town)
race_nh_gov_2026.json GET https://civicapi.org/api/v2/race/87284 (2026 NH Governor, live results,
                      "test_data": false and no called_at; region_results cut to 1 town)
region_results trimmed to 2 counties in both TX race files, to 1 region in the GU and NH race files.
