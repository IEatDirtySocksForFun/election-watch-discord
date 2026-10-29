Fixtures for the ny_nycboe adapter (New York City Board of Elections recap CSVs).

Captured 2026-10-27 from the official index page
https://www.vote.nyc/page/election-results-summary-2024 with a plain GET and the
descriptive User-Agent "Mozilla/5.0 (compatible; election-watch-discord/0.1; ...)".
URLs contain spaces, which are percent-encoded when fetched.

nyc_2024_senator_citywide.csv
  https://www.vote.nyc/sites/default/files/pdf/election_results/2024/20241105General Election/00000800000Citywide United States Senator Citywide Recap.csv
  ("Statement and Return Report for Certification", 2024-11-05 general election,
  citywide aggregate, 2,074,227 bytes in full.)
  Trimmed to the header row plus the first 14 rows of the "Total for ..." block
  (5 machine-category rows, then the candidates). 9 KB.

nyc_2024_cd10_new_york.csv
  .../2024/20241105General Election/00101900010New York Representative in Congress 10th Congressional District Recap.csv
  Trimmed to the header row plus 12 "Total for" rows (281 KB in full).

nyc_2024_cd10_kings.csv
  .../2024/20241105General Election/00301900010Kings Representative in Congress 10th Congressional District Recap.csv
  Trimmed to the header row plus 12 "Total for" rows (541 KB in full).

The two CD10 files are the Manhattan and Brooklyn parts of the same race, which is what the
adapter sums; the Senate file is the citywide aggregate, which is why it is not summed with
borough files. Row order and cell contents are exactly as published (only rows are dropped).
