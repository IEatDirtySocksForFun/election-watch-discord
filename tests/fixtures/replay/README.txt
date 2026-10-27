Fixtures for tests/test_replay*.py (no network in tests).

Trimmed real responses, fetched 2026-10-27:
- medsl_de24_sample.csv: 24 rows of de24.csv inside
  https://raw.githubusercontent.com/MEDSL/2024-elections-official/main/individual_states/de24.zip
- openelections_ga_2020_sample.csv: header + 10 rows of
  https://raw.githubusercontent.com/openelections/openelections-data-ga/master/2020/20201103__ga__general.csv
- nyt_2024_pa_us_senate_trimmed.json: race metadata + 3 of the timeseries points of
  https://static01.nyt.com/elections-assets/pages/data/2024-11-05/results-pennsylvania-us-senate.json
- nyt_2020_ga_senate_trimmed.json: race metadata + 4 timeseries points of
  https://static01.nyt.com/elections-assets/2020/data/api/2020-11-03/race-page/georgia/senate.json

Hand-made (synthetic, not real results):
- timelines/2024/PA_SEN.jsonl: 4-snapshot replay timeline
- 2024/DE/senate.csv: tiny county-level results file for build_timelines
