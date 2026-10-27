Indiana ENR JSON (enr.indianavoters.in.gov)
==========================================

Recorded: 2026-10-27, from the 2024 general election folder.

  settings.json                    https://enr.indianavoters.in.gov/archive/2024General/data/settings.json
  statewideElectionsC_A.json       https://enr.indianavoters.in.gov/archive/2024General/data/statewideElectionsC_A.json
  OffCatC_1006_A.json              .../data/OffCatC_1006_A.json   (US Senator)
  OffCatC_1005_A.json              .../data/OffCatC_1005_A.json   (US Representative)
  OffCatC_1007_A.json              .../data/OffCatC_1007_A.json   (Governor & Lt. Governor)
  OffCatC_1018_A.json              .../data/OffCatC_1018_A.json   (State Senator)
  OffCatC_1039_A.json              .../data/OffCatC_1039_A.json   (State Representative)

Trimmed for the fixture:
  - settings.json keeps the Root metadata only (the 26-party PolParties table is dropped).
  - statewideElectionsC_A.json keeps five office categories, without the long
    OFFICE_CATEGORY_DESCRIPTION blurbs.
  - the OffCatC files keep StatewideSummary.Race (one race for single-race categories,
    the first two districts for the district categories, at most 5 candidates each)
    and drop OfficeCategory.Regions (every county, hundreds of KB) plus the
    now-unused keys VOTE_FOR / ALLREPORTED etc. Vote counts and isWinner flags are
    the real 2024 certified numbers.
