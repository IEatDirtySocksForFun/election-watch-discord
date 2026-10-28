# civicAPI accuracy vs official results (2026-10-27)

## Verdict

- **Statewide/federal races, after certification: trustworthy.** VA Gov/LtGov/AG were within 1-2 votes, and NJ Gov was exact. NC 2024 Council of State was exact (only Treasurer was off, by 855 votes). TX and CA Senate totals equal the certified figures (Cruz 5,990,741; Schiff 9,036,252), where MEDSL runs slightly low.
- **Legislative/down-ballot: mostly exact, but some data is stale or wrong and nothing flags it.** VA HD: 98 of 100 exact (HD-55 off by 3 write-ins). NC 2024 legislature: 4 bad races, including HD-61, where Harrison has 23,899 votes against 33,804 official and her opponent is inflated, and HD-82, which is exactly +3,000. **The NJ Assembly was never updated after election night**: all 40 districts are 2-11% low (HD-23 is 31% low), still say percent_reporting 99 (HD-15/28 say 0), and the SD-35 special is 5% low. Winners still agree in every race.
- **NC 2026 primary: 81 of 81 exact** (as of today).
- **Use as a fallback for election-night numbers and leads. Don't treat it as authoritative final totals, and don't use its calls.**

## Calls / test data

- `called_at` was **absent on every real race** (108 race details from 2024, 2025 and 2026 primaries/specials). It appears only on current **test_data=true** 2026-11-03 races with fake winners and times (TX Senate "Talarico" winner, called_at 2026-10-27T13:34Z; VA Senate called 2026-10-27T11:00Z). So call-time plausibility against AP **cannot be measured**. CivicAPICallSource has never produced a real call in the archive.
- No past race has test_data=true. Search rows never carry `test_data`; only the race detail does (the adapter already reads the detail).
- Today TX and VA 2026-11-03 carry test votes (40 TX rows, 12 VA rows). The adapter drops them correctly.

## Structural problems found in the adapter

1. **Duplicate rows per race key.** `state_races` does `setdefault`, so the first row wins and the result depends on row order:
   - MA SEN has a phantom "Baker 1,791,579 vs Warren 1,615,529" row.
   - VT SEN and DE SEN have 2-candidate shadow rows with different numbers. All of these have `has_breakdown=false`.
   - TX HD-56 and TX-18 have a regular race and a "Special" race that collide, because `race_key` sets `special` only for "US Senate".
   - Primaries collide across parties: VA-05 D and VA-05 R map to one key on 2026-08-04.
2. **`type == "Senate"`** (used in AZ, CA, DE, MD) is not mapped to SEN, so those Senate races become LOCAL keys and are invisible to SEN lookups. Only "US Senate" is mapped.
3. **percent_reporting is unreliable:**
   - 590 of 5,862 rows say 0 while carrying votes (VA 2025-01-07 specials, NJ HD-15/28).
   - NJ says 99 on undercounted totals.
4. **region_results is not a county partition.** It mixes County with State House regions (DE, IA), CA congressional and supervisor districts, and an incomplete MA town list. Summing it is wrong in 13 races. Filter on `type in {County, City}` if county results are ever parsed.
5. **Party strings pass through raw:** "Nonpartisan" (used for write-ins), "Non Partisan", "Write In", "NP", "Forward", "Petitioning Candidate", and "\x00".

## Name / write-in handling

- civicAPI uses its own name forms:
  - VA drops the period in "Jr." and "Sr." (2 races).
  - NC 2024: 71 candidates differ (adds middle initials or legal names, e.g. "Walter Vincent Smith" vs "Vinny Smith"; strips nicknames).
  - NC 2026: 10 differ ("DECEASED" dropped).
- Write-ins: VA's aggregate "Write-in" matches the official count, with party "Nonpartisan".
- NC: named write-ins lose their "(Write-In)" suffix, so they look like ballot candidates. "Write-In (Miscellaneous)" is dropped.
- IA/VT/MD add a "Write-In" row that MEDSL lacks.

## Recommended adapter changes

1. Dedupe per key: prefer `has_breakdown`, then more candidates, then the latest `last_updated`. Set `special` from "Special" in election_name for every type. Add the party to the key, or skip primaries.
2. Map `type in {"US Senate","Senate"}` to SEN.
3. Normalize party with `results._common.norm_party`, adding aliases NONPARTISAN/NON PARTISAN/NP→NPA, WRITE IN→WRI and FORWARD→FWD. Mark a candidate named "Write-in" as WRI.
4. Treat percent_reporting 0 with votes as unknown (None). Never let civicAPI totals override an official feed after election week.
5. Match names across sources by surname plus first initial, ignoring suffix punctuation and nicknames.
6. Disable or ignore CivicAPICallSource until a real `called_at` is seen on a non-test race.

## Coverage gaps

- **2024 archive:** races exist for only 14 states (AL4, AZ, CA, CO1, CT1, DC1, DE, IA, MA, MD1, NC, NY13, SC1, TX, VT). There is nothing for PA, GA, MI, WI, NV, OH or FL.
- **MA and VT legislature:** named districts, so 194 of 205 MA rows and 127 of 136 VT rows get no key.
- **No region_results for single-county districts:** 15 NJ districts, 5 TX/CA/AZ House seats and NC-02.
- **VA specials (2025-01-07, 2025-09-09, 2026-01-13) and the 2026-08-04 primary were not checked.** The VA ENR jurisdiction listing (`/api/elections/virginia`) now returns **404**, so `va_elect._election_id` discovery fails for any unpinned date. **This is a repo bug worth a pin or a fix.**

## Method caveats

- **2024 references:** NC uses the repo `nc_sbe` adapter. The other states use MEDSL precinct-derived totals, which are themselves sometimes incomplete:
  - AZ HD-16/HD-17, NY HD-09..11 and AZ HD-01 have MEDSL name mangling.
  - CA has 153 "differs", but MEDSL runs about 0.1% low there (civic CA SEN = certified Schiff 9,036,252).
  - 2024 county comparisons all use MEDSL.
- **Unresolved:** AZ SD-25 (Dunn, uncontested): civic 59,826 vs MEDSL 88,531.
- **NJ party column:** the "party issues" for NJ in the JSON are artifacts of the PDF party-column slicing and should be ignored.
- **Files:** scripts are `civ.py`, `cmp.py`, `va2025.py`, `nj_pdf.py`, `nj2025.py`, `y2024.py`, `nc2026.py` and `make_report.py`. Raw JSON is in `results_*.json` and `phantoms_2024.json`, and official inputs are in `official/`.


## VA 2025 general (ENR certified)

- Races listed: 103; compared: 103; exact (every candidate): 100; votes exact but write-in/label differs: 0; differ: 3; reference incomplete: 0; not comparable: 0
- Over usable comparisons: max abs diff 3; median 0; max % 0.0983; median % 0.0; max race-% 0.008
- Winners agree: 103/103
- County detail: 23 races, 445 county pairs compared, 2 mismatched; counties missing in civicAPI: 0

| race | result | max abs | max % | race % | official total | civic total | winners | %rep | counties (both/off, mism) | names/notes |
|---|---|---|---|---|---|---|---|---|---|---|
| VA AG | differs | 2 | 0.0001 | 0.0 | 3396499 | 3396497 | yes | 100 | 133/133, 1 |  |
| VA GOV | exact | 0 | 0.0 | 0.0 | 3433340 | 3433340 | yes | 100 | 133/133, 0 |  |
| VA HD-01 | exact | 0 | 0.0 | 0.0 | 41541 | 41541 | yes | 100 | 1/1, 0 |  |
| VA HD-02 | exact | 0 | 0.0 | 0.0 | 33080 | 33080 | yes | 100 |  |  |
| VA HD-03 | exact | 0 | 0.0 | 0.0 | 28950 | 28950 | yes | 100 |  |  |
| VA HD-04 | exact | 0 | 0.0 | 0.0 | 19059 | 19059 | yes | 100 |  |  |
| VA HD-05 | exact | 0 | 0.0 | 0.0 | 35602 | 35602 | yes | 100 |  |  |
| VA HD-06 | exact | 0 | 0.0 | 0.0 | 40575 | 40575 | yes | 100 | 1/1, 0 |  |
| VA HD-07 | exact | 0 | 0.0 | 0.0 | 40835 | 40835 | yes | 100 | 1/1, 0 |  |
| VA HD-08 | exact | 0 | 0.0 | 0.0 | 31047 | 31047 | yes | 100 |  |  |
| VA HD-09 | exact | 0 | 0.0 | 0.0 | 32601 | 32601 | yes | 100 | 1/1, 0 |  |
| VA HD-10 | exact | 0 | 0.0 | 0.0 | 35541 | 35541 | yes | 100 |  |  |
| VA HD-100 | exact | 0 | 0.0 | 0.0 | 33835 | 33835 | yes | 100 | 3/3, 0 |  |
| VA HD-11 | exact | 0 | 0.0 | 0.0 | 32275 | 32275 | yes | 100 |  |  |
| VA HD-12 | exact | 0 | 0.0 | 0.0 | 33551 | 33551 | yes | 100 | 1/1, 0 |  |
| VA HD-13 | exact | 0 | 0.0 | 0.0 | 32015 | 32015 | yes | 100 |  |  |
| VA HD-14 | exact | 0 | 0.0 | 0.0 | 28113 | 28113 | yes | 100 |  |  |
| VA HD-15 | exact | 0 | 0.0 | 0.0 | 40587 | 40587 | yes | 100 | 1/1, 0 |  |
| VA HD-16 | exact | 0 | 0.0 | 0.0 | 31258 | 31258 | yes | 100 |  |  |
| VA HD-17 | exact | 0 | 0.0 | 0.0 | 33257 | 33257 | yes | 100 |  |  |
| VA HD-18 | exact | 0 | 0.0 | 0.0 | 34037 | 34037 | yes | 100 |  |  |
| VA HD-19 | exact | 0 | 0.0 | 0.0 | 18669 | 18669 | yes | 100 |  | name: Rozia A. "J.R." Henson, Jr. → Rozia A. "J.R." Henson, Jr |
| VA HD-20 | exact | 0 | 0.0 | 0.0 | 20738 | 20738 | yes | 100 |  |  |
| VA HD-21 | exact | 0 | 0.0 | 0.0 | 31454 | 31454 | yes | 100 |  |  |
| VA HD-22 | exact | 0 | 0.0 | 0.0 | 36977 | 36977 | yes | 100 | 1/1, 0 |  |
| VA HD-23 | exact | 0 | 0.0 | 0.0 | 24077 | 24077 | yes | 100 |  |  |
| VA HD-24 | exact | 0 | 0.0 | 0.0 | 24882 | 24882 | yes | 100 |  |  |
| VA HD-25 | exact | 0 | 0.0 | 0.0 | 25841 | 25841 | yes | 100 |  |  |
| VA HD-26 | exact | 0 | 0.0 | 0.0 | 29457 | 29457 | yes | 100 |  |  |
| VA HD-27 | exact | 0 | 0.0 | 0.0 | 28272 | 28272 | yes | 100 | 1/1, 0 |  |
| VA HD-28 | exact | 0 | 0.0 | 0.0 | 35954 | 35954 | yes | 100 |  |  |
| VA HD-29 | exact | 0 | 0.0 | 0.0 | 35565 | 35565 | yes | 100 | 1/1, 0 |  |
| VA HD-30 | exact | 0 | 0.0 | 0.0 | 44007 | 44007 | yes | 100 |  |  |
| VA HD-31 | exact | 0 | 0.0 | 0.0 | 37735 | 37735 | yes | 100 |  |  |
| VA HD-32 | exact | 0 | 0.0 | 0.0 | 32304 | 32304 | yes | 100 |  |  |
| VA HD-33 | exact | 0 | 0.0 | 0.0 | 35249 | 35249 | yes | 100 |  |  |
| VA HD-34 | exact | 0 | 0.0 | 0.0 | 28963 | 28963 | yes | 100 |  |  |
| VA HD-35 | exact | 0 | 0.0 | 0.0 | 37692 | 37692 | yes | 100 |  |  |
| VA HD-36 | exact | 0 | 0.0 | 0.0 | 36338 | 36338 | yes | 100 |  |  |
| VA HD-37 | exact | 0 | 0.0 | 0.0 | 36597 | 36597 | yes | 100 |  |  |
| VA HD-38 | exact | 0 | 0.0 | 0.0 | 24531 | 24531 | yes | 100 |  |  |
| VA HD-39 | exact | 0 | 0.0 | 0.0 | 35959 | 35959 | yes | 100 |  |  |
| VA HD-40 | exact | 0 | 0.0 | 0.0 | 36425 | 36425 | yes | 100 |  |  |
| VA HD-41 | exact | 0 | 0.0 | 0.0 | 33184 | 33184 | yes | 100 |  |  |
| VA HD-42 | exact | 0 | 0.0 | 0.0 | 32801 | 32801 | yes | 100 |  |  |
| VA HD-43 | exact | 0 | 0.0 | 0.0 | 26905 | 26905 | yes | 100 |  |  |
| VA HD-44 | exact | 0 | 0.0 | 0.0 | 31588 | 31588 | yes | 100 |  |  |
| VA HD-45 | exact | 0 | 0.0 | 0.0 | 26573 | 26573 | yes | 100 |  |  |
| VA HD-46 | exact | 0 | 0.0 | 0.0 | 30193 | 30193 | yes | 100 |  |  |
| VA HD-47 | exact | 0 | 0.0 | 0.0 | 32240 | 32240 | yes | 100 | 5/5, 0 |  |
| VA HD-48 | exact | 0 | 0.0 | 0.0 | 31216 | 31216 | yes | 100 | 3/3, 0 |  |
| VA HD-49 | exact | 0 | 0.0 | 0.0 | 28738 | 28738 | yes | 100 |  |  |
| VA HD-50 | exact | 0 | 0.0 | 0.0 | 32126 | 32126 | yes | 100 |  |  |
| VA HD-51 | exact | 0 | 0.0 | 0.0 | 37304 | 37304 | yes | 100 | 3/3, 0 |  |
| VA HD-52 | exact | 0 | 0.0 | 0.0 | 29862 | 29862 | yes | 100 | 2/2, 0 |  |
| VA HD-53 | exact | 0 | 0.0 | 0.0 | 40649 | 40649 | yes | 100 |  |  |
| VA HD-54 | exact | 0 | 0.0 | 0.0 | 30723 | 30723 | yes | 100 |  |  |
| VA HD-55 | differs | 3 | 0.0983 | 0.008 | 36136 | 36133 | yes | 100 |  |  |
| VA HD-56 | exact | 0 | 0.0 | 0.0 | 41565 | 41565 | yes | 100 |  |  |
| VA HD-57 | exact | 0 | 0.0 | 0.0 | 43545 | 43545 | yes | 100 |  |  |
| VA HD-58 | exact | 0 | 0.0 | 0.0 | 38848 | 38848 | yes | 100 |  |  |
| VA HD-59 | exact | 0 | 0.0 | 0.0 | 44750 | 44750 | yes | 100 |  |  |
| VA HD-60 | exact | 0 | 0.0 | 0.0 | 48103 | 48103 | yes | 100 |  |  |
| VA HD-61 | exact | 0 | 0.0 | 0.0 | 40707 | 40707 | yes | 100 | 3/3, 0 |  |
| VA HD-62 | exact | 0 | 0.0 | 0.0 | 37077 | 37077 | yes | 100 |  |  |
| VA HD-63 | exact | 0 | 0.0 | 0.0 | 38332 | 38332 | yes | 100 |  |  |
| VA HD-64 | exact | 0 | 0.0 | 0.0 | 36514 | 36514 | yes | 100 |  |  |
| VA HD-65 | exact | 0 | 0.0 | 0.0 | 35085 | 35085 | yes | 100 |  |  |
| VA HD-66 | exact | 0 | 0.0 | 0.0 | 36112 | 36112 | yes | 100 |  | name: Robert D. "Bobby" Orrock, Sr. → Robert D. "Bobby" Orrock, Sr |
| VA HD-67 | exact | 0 | 0.0 | 0.0 | 39721 | 39721 | yes | 100 | 6/6, 0 |  |
| VA HD-68 | exact | 0 | 0.0 | 0.0 | 41549 | 41549 | yes | 100 |  |  |
| VA HD-69 | exact | 0 | 0.0 | 0.0 | 39073 | 39073 | yes | 100 |  |  |
| VA HD-70 | exact | 0 | 0.0 | 0.0 | 29769 | 29769 | yes | 100 |  |  |
| VA HD-71 | exact | 0 | 0.0 | 0.0 | 44948 | 44948 | yes | 100 | 3/3, 0 |  |
| VA HD-72 | exact | 0 | 0.0 | 0.0 | 45399 | 45399 | yes | 100 | 4/4, 0 |  |
| VA HD-73 | exact | 0 | 0.0 | 0.0 | 50451 | 50451 | yes | 100 |  |  |
| VA HD-74 | exact | 0 | 0.0 | 0.0 | 39736 | 39736 | yes | 100 |  |  |
| VA HD-75 | exact | 0 | 0.0 | 0.0 | 28773 | 28773 | yes | 100 |  |  |
| VA HD-76 | exact | 0 | 0.0 | 0.0 | 33902 | 33902 | yes | 100 |  |  |
| VA HD-77 | exact | 0 | 0.0 | 0.0 | 30222 | 30222 | yes | 100 |  |  |
| VA HD-78 | exact | 0 | 0.0 | 0.0 | 44501 | 44501 | yes | 100 |  |  |
| VA HD-79 | exact | 0 | 0.0 | 0.0 | 30209 | 30209 | yes | 100 |  |  |
| VA HD-80 | exact | 0 | 0.0 | 0.0 | 30267 | 30267 | yes | 100 |  |  |
| VA HD-81 | exact | 0 | 0.0 | 0.0 | 28867 | 28867 | yes | 100 |  |  |
| VA HD-82 | exact | 0 | 0.0 | 0.0 | 34417 | 34417 | yes | 100 | 4/4, 0 |  |
| VA HD-83 | exact | 0 | 0.0 | 0.0 | 37036 | 37036 | yes | 100 |  |  |
| VA HD-84 | exact | 0 | 0.0 | 0.0 | 38270 | 38270 | yes | 100 |  |  |
| VA HD-85 | exact | 0 | 0.0 | 0.0 | 21020 | 21020 | yes | 100 |  |  |
| VA HD-86 | exact | 0 | 0.0 | 0.0 | 31719 | 31719 | yes | 100 |  |  |
| VA HD-87 | exact | 0 | 0.0 | 0.0 | 29375 | 29375 | yes | 100 |  |  |
| VA HD-88 | exact | 0 | 0.0 | 0.0 | 23125 | 23125 | yes | 100 |  |  |
| VA HD-89 | exact | 0 | 0.0 | 0.0 | 37698 | 37698 | yes | 100 |  |  |
| VA HD-90 | exact | 0 | 0.0 | 0.0 | 38905 | 38905 | yes | 100 |  |  |
| VA HD-91 | exact | 0 | 0.0 | 0.0 | 24512 | 24512 | yes | 100 |  |  |
| VA HD-92 | exact | 0 | 0.0 | 0.0 | 24339 | 24339 | yes | 100 |  |  |
| VA HD-93 | exact | 0 | 0.0 | 0.0 | 22506 | 22506 | yes | 100 |  |  |
| VA HD-94 | exact | 0 | 0.0 | 0.0 | 18936 | 18936 | yes | 100 |  |  |
| VA HD-95 | exact | 0 | 0.0 | 0.0 | 20900 | 20900 | yes | 100 |  |  |
| VA HD-96 | exact | 0 | 0.0 | 0.0 | 28329 | 28329 | yes | 100 |  |  |
| VA HD-97 | exact | 0 | 0.0 | 0.0 | 30476 | 30476 | yes | 100 |  |  |
| VA HD-98 | exact | 0 | 0.0 | 0.0 | 35678 | 35678 | yes | 100 | 1/1, 0 |  |
| VA HD-99 | exact | 0 | 0.0 | 0.0 | 35411 | 35411 | yes | 100 |  |  |
| VA LTGOV | differs | 1 | 0.0001 | 0.0 | 3414177 | 3414176 | yes | 100 | 133/133, 1 |  |

## NJ 2025 general (DOS certified PDF)

- Races listed: 42; compared: 42; exact (every candidate): 1; votes exact but write-in/label differs: 0; differ: 41; reference incomplete: 0; not comparable: 0
- Over usable comparisons: max abs diff 15764; median 2317.5; max % 30.6794; median % 5.3314; max race-% 8.334
- Winners agree: 42/42
- County detail: 42 races, 85 county pairs compared, 64 mismatched; counties missing in civicAPI: 15

| race | result | max abs | max % | race % | official total | civic total | winners | %rep | counties (both/off, mism) | names/notes |
|---|---|---|---|---|---|---|---|---|---|---|
| NJ GOV | exact | 0 | 0.0 | 0.0 | 3334359 | 3334359 | yes | 99 | 21/21, 0 |  |
| NJ SD-35 (special) | differs | 1660 | 5.2406 | 3.804 | 43633 | 41574 | yes | 81.11 | 2/2, 2 |  |
| NJ HD-01 | differs | 1637 | 4.7937 | 1.08 | 151535 | 146254 | yes | 99 | 3/3, 3 |  |
| NJ HD-02 | differs | 1289 | 3.2646 | 0.819 | 157298 | 153319 | yes | 99 | 0/1, 0 |  |
| NJ HD-03 | differs | 1629 | 3.4024 | 0.882 | 184712 | 180148 | yes | 99 | 3/3, 3 |  |
| NJ HD-04 | differs | 2385 | 4.5615 | 1.266 | 188418 | 181941 | yes | 99 | 3/3, 3 |  |
| NJ HD-05 | differs | 2924 | 8.6177 | 2.085 | 140257 | 132824 | yes | 99 | 2/2, 2 |  |
| NJ HD-06 | differs | 3509 | 5.5884 | 1.935 | 181356 | 172597 | yes | 99 | 2/2, 2 |  |
| NJ HD-07 | differs | 3835 | 6.149 | 2.083 | 184117 | 174262 | yes | 99 | 0/1, 0 |  |
| NJ HD-08 | differs | 2927 | 5.8498 | 1.534 | 190766 | 182050 | yes | 99 | 2/2, 2 |  |
| NJ HD-09 | differs | 847 | 2.1118 | 0.387 | 219038 | 215839 | yes | 99 | 0/1, 0 |  |
| NJ HD-10 | differs | 914 | 2.5323 | 0.468 | 195299 | 191735 | yes | 99 | 2/2, 2 |  |
| NJ HD-11 | differs | 2053 | 5.1639 | 1.175 | 174797 | 168760 | yes | 99 | 0/1, 0 |  |
| NJ HD-12 | differs | 1475 | 4.3218 | 0.836 | 176536 | 171322 | yes | 99 | 4/4, 4 |  |
| NJ HD-13 | differs | 1901 | 4.3135 | 0.935 | 203283 | 197169 | yes | 99 | 0/1, 0 |  |
| NJ HD-14 | differs | 3748 | 7.5665 | 2.172 | 172575 | 162193 | yes | 99 | 2/2, 2 |  |
| NJ HD-15 | differs | 3436 | 6.0746 | 3.089 | 111239 | 104527 | yes | 0 | 2/2, 2 |  |
| NJ HD-16 | differs | 3280 | 5.8078 | 1.738 | 188721 | 180145 | yes | 99 | 4/4, 4 |  |
| NJ HD-17 | differs | 2936 | 6.2629 | 2.404 | 122120 | 114972 | yes | 99 | 2/2, 2 |  |
| NJ HD-18 | differs | 3468 | 7.1241 | 2.303 | 150570 | 141426 | yes | 99 | 0/1, 0 |  |
| NJ HD-19 | differs | 2342 | 5.8803 | 1.89 | 123931 | 117667 | yes | 99 | 0/1, 0 |  |
| NJ HD-20 | differs | 3330 | 9.4425 | 4.034 | 82550 | 74990 | yes | 99 | 0/1, 0 |  |
| NJ HD-21 | differs | 6240 | 11.3527 | 3.095 | 201598 | 180656 | yes | 99 | 4/4, 4 |  |
| NJ HD-22 | differs | 2333 | 4.4885 | 1.559 | 149689 | 143784 | yes | 99 | 2/2, 2 |  |
| NJ HD-23 | differs | 15764 | 30.6794 | 8.334 | 189159 | 138116 | yes | 85.5 | 3/3, 3 |  |
| NJ HD-24 | differs | 2475 | 6.2609 | 1.298 | 190673 | 181238 | yes | 99 | 3/3, 3 |  |
| NJ HD-25 | differs | 2958 | 6.0055 | 1.518 | 194854 | 186137 | yes | 99 | 2/2, 2 |  |
| NJ HD-26 | differs | 2086 | 4.5575 | 1.091 | 191286 | 184917 | yes | 99 | 2/2, 2 |  |
| NJ HD-27 | differs | 4341 | 7.4497 | 2.674 | 162347 | 152190 | yes | 99 | 2/2, 2 |  |
| NJ HD-28 | differs | 4479 | 9.3498 | 4.702 | 95261 | 86363 | yes | 0 | 2/2, 2 |  |
| NJ HD-29 | differs | 1548 | 6.2968 | 2.65 | 58405 | 55000 | yes | 99 | 2/2, 2 |  |
| NJ HD-30 | differs | 2302 | 4.7865 | 1.452 | 158545 | 152814 | yes | 99 | 2/2, 2 |  |
| NJ HD-31 | differs | 1829 | 5.3894 | 2.019 | 90579 | 86256 | yes | 99 | 0/1, 0 |  |
| NJ HD-32 | differs | 2678 | 7.087 | 2.759 | 97050 | 90770 | yes | 99 | 0/1, 0 |  |
| NJ HD-33 | differs | 897 | 2.2633 | 0.86 | 104357 | 102211 | yes | 99 | 0/1, 0 |  |
| NJ HD-34 | differs | 3516 | 7.1635 | 2.814 | 124950 | 117001 | yes | 99 | 0/1, 0 |  |
| NJ HD-35 | differs | 1623 | 5.2734 | 1.904 | 85257 | 81238 | yes | 99 | 2/2, 2 |  |
| NJ HD-36 | differs | 1244 | 3.6127 | 1.111 | 111926 | 108619 | yes | 99 | 2/2, 2 |  |
| NJ HD-37 | differs | 1359 | 3.0952 | 1.067 | 127381 | 123785 | yes | 99 | 0/1, 0 |  |
| NJ HD-38 | differs | 1200 | 2.6265 | 0.769 | 156075 | 152777 | yes | 99 | 0/1, 0 |  |
| NJ HD-39 | differs | 1260 | 2.5993 | 0.636 | 198190 | 194237 | yes | 99 | 0/1, 0 |  |
| NJ HD-40 | differs | 1996 | 4.3514 | 1.037 | 192441 | 186277 | yes | 99 | 3/3, 3 |  |

## NC 2024 general (nc_sbe ENR final)

- Races listed: 191; compared: 191; exact (every candidate): 184; votes exact but write-in/label differs: 3; differ: 4; reference incomplete: 0; not comparable: 0
- Over usable comparisons: max abs diff 9905; median 0; max % 53.7646; median % 0.0; max race-% 23.109
- Winners agree: 191/191
- County detail: 15 races, 751 county pairs compared, 102 mismatched; counties missing in civicAPI: 1

| race | result | max abs | max % | race % | official total | civic total | winners | %rep | counties (both/off, mism) | names/notes |
|---|---|---|---|---|---|---|---|---|---|---|
| NC AG | exact | 0 | 0.0 | 0.0 | 5590371 | 5590371 | yes | 100 | 100/100, 14 |  |
| NC AUD | exact | 0 | 0.0 | 0.0 | 5531088 | 5531088 | yes | 100 | 100/100, 14 |  |
| NC GOV | exact | 0 | 0.0 | 0.0 | 5591547 | 5591547 | yes | 100 | 100/100, 14 | name: Mark Robinson → Mark K. Robinson; Vinny Smith → Walter Vincent Smith |
| NC HD-01 | exact | 0 | 0.0 | 0.0 | 49110 | 49110 | yes | 100 |  |  |
| NC HD-02 | exact | 0 | 0.0 | 0.0 | 49206 | 49206 | yes | 100 |  |  |
| NC HD-03 | exact | 0 | 0.0 | 0.0 | 48355 | 48355 | yes | 100 |  |  |
| NC HD-04 | exact | 0 | 0.0 | 0.0 | 35054 | 35054 | yes | 100 |  | name: Vernon Moore → Vernon R. Moore |
| NC HD-05 | exact | 0 | 0.0 | 0.0 | 41281 | 41281 | yes | 100 |  | name: Howard Hunter III → Howard J. Hunter, III |
| NC HD-06 | exact | 0 | 0.0 | 0.0 | 39894 | 39894 | yes | 100 |  |  |
| NC HD-07 | exact | 0 | 0.0 | 0.0 | 48985 | 48985 | yes | 100 |  |  |
| NC HD-08 | exact | 0 | 0.0 | 0.0 | 35486 | 35486 | yes | 100 |  |  |
| NC HD-09 | exact | 0 | 0.0 | 0.0 | 48061 | 48061 | yes | 100 |  |  |
| NC HD-10 | exact | 0 | 0.0 | 0.0 | 40264 | 40264 | yes | 100 |  | name: John Bell → John R. Bell IV |
| NC HD-100 | exact | 0 | 0.0 | 0.0 | 28937 | 28937 | yes | 100 |  |  |
| NC HD-101 | exact | 0 | 0.0 | 0.0 | 34424 | 34424 | yes | 100 |  | name: Carolyn G. Logan → Carolyn Logan |
| NC HD-102 | exact | 0 | 0.0 | 0.0 | 39811 | 39811 | yes | 100 |  |  |
| NC HD-103 | exact | 0 | 0.0 | 0.0 | 44381 | 44381 | yes | 100 |  | name: Laura Budd → Laura H. Budd |
| NC HD-104 | exact | 0 | 0.0 | 0.0 | 49377 | 49377 | yes | 100 |  |  |
| NC HD-105 | exact | 0 | 0.0 | 0.0 | 54385 | 54385 | yes | 100 |  | name: Tricia Ann Cotham → Patricia Ann Cotham |
| NC HD-106 | exact | 0 | 0.0 | 0.0 | 40633 | 40633 | yes | 100 |  | name: Carla Cunningham → Carla D. Cunningham |
| NC HD-107 | exact | 0 | 0.0 | 0.0 | 38603 | 38603 | yes | 100 |  |  |
| NC HD-108 | exact | 0 | 0.0 | 0.0 | 44874 | 44874 | yes | 100 |  | name: John A. Torbett → John Torbett |
| NC HD-109 | exact | 0 | 0.0 | 0.0 | 48541 | 48541 | yes | 100 |  |  |
| NC HD-11 | exact | 0 | 0.0 | 0.0 | 49014 | 49014 | yes | 100 |  | name: Allison A. Dahle → Allison Dahle |
| NC HD-110 | exact | 0 | 0.0 | 0.0 | 42686 | 42686 | yes | 100 |  | name: Kelly Hastings → Kelly E. Hastings; Justin (JW) Matthews → Justin Matthews |
| NC HD-111 | exact | 0 | 0.0 | 0.0 | 42544 | 42544 | yes | 100 |  | name: Frances Rollinson Webber → Frances Robinson Webber |
| NC HD-112 | exact | 0 | 0.0 | 0.0 | 32721 | 32721 | yes | 100 |  |  |
| NC HD-113 | exact | 0 | 0.0 | 0.0 | 51690 | 51690 | yes | 100 |  |  |
| NC HD-114 | exact | 0 | 0.0 | 0.0 | 54346 | 54346 | yes | 100 |  | name: Eric Ager → J. Eric Ager |
| NC HD-115 | exact | 0 | 0.0 | 0.0 | 50923 | 50923 | yes | 100 |  |  |
| NC HD-116 | exact | 0 | 0.0 | 0.0 | 42815 | 42815 | yes | 100 |  |  |
| NC HD-117 | exact | 0 | 0.0 | 0.0 | 52993 | 52993 | yes | 100 |  |  |
| NC HD-118 | exact | 0 | 0.0 | 0.0 | 50287 | 50287 | yes | 100 |  |  |
| NC HD-119 | exact | 0 | 0.0 | 0.0 | 48512 | 48512 | yes | 100 |  |  |
| NC HD-12 | exact | 0 | 0.0 | 0.0 | 40358 | 40358 | yes | 100 |  |  |
| NC HD-120 | exact | 0 | 0.0 | 0.0 | 50612 | 50612 | yes | 100 |  |  |
| NC HD-13 | exact | 0 | 0.0 | 0.0 | 50339 | 50339 | yes | 100 |  |  |
| NC HD-14 | exact | 0 | 0.0 | 0.0 | 25498 | 25498 | yes | 100 |  |  |
| NC HD-15 | exact | 0 | 0.0 | 0.0 | 37877 | 37877 | yes | 100 |  |  |
| NC HD-16 | exact | 0 | 0.0 | 0.0 | 51062 | 51062 | yes | 100 |  |  |
| NC HD-17 | exact | 0 | 0.0 | 0.0 | 67569 | 67569 | yes | 100 |  |  |
| NC HD-18 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 39930 | 38975 | yes | 100 |  | only official: Wallace West (Write-In), Write-In (Miscellaneous); only civic: Wallace West |
| NC HD-19 | exact | 0 | 0.0 | 0.0 | 67148 | 67148 | yes | 100 |  |  |
| NC HD-20 | exact | 0 | 0.0 | 0.0 | 57283 | 57283 | yes | 100 |  |  |
| NC HD-21 | exact | 0 | 0.0 | 0.0 | 48733 | 48733 | yes | 100 |  |  |
| NC HD-22 | exact | 0 | 0.0 | 0.0 | 42905 | 42905 | yes | 100 |  |  |
| NC HD-23 | exact | 0 | 0.0 | 0.0 | 44530 | 44530 | yes | 100 |  |  |
| NC HD-24 | exact | 0 | 0.0 | 0.0 | 41768 | 41768 | yes | 100 |  |  |
| NC HD-25 | exact | 0 | 0.0 | 0.0 | 48905 | 48905 | yes | 100 |  |  |
| NC HD-26 | exact | 0 | 0.0 | 0.0 | 54346 | 54346 | yes | 100 |  |  |
| NC HD-27 | exact | 0 | 0.0 | 0.0 | 31914 | 31914 | yes | 100 |  |  |
| NC HD-28 | exact | 0 | 0.0 | 0.0 | 41980 | 41980 | yes | 100 |  |  |
| NC HD-29 | exact | 0 | 0.0 | 0.0 | 47945 | 47945 | yes | 100 |  |  |
| NC HD-30 | exact | 0 | 0.0 | 0.0 | 41916 | 41916 | yes | 100 |  |  |
| NC HD-31 | exact | 0 | 0.0 | 0.0 | 43291 | 43291 | yes | 100 |  |  |
| NC HD-32 | exact | 0 | 0.0 | 0.0 | 43342 | 43342 | yes | 100 |  |  |
| NC HD-33 | exact | 0 | 0.0 | 0.0 | 42114 | 42114 | yes | 100 |  |  |
| NC HD-34 | exact | 0 | 0.0 | 0.0 | 44647 | 44647 | yes | 100 |  |  |
| NC HD-35 | exact | 0 | 0.0 | 0.0 | 56995 | 56995 | yes | 100 |  |  |
| NC HD-36 | exact | 0 | 0.0 | 0.0 | 52514 | 52514 | yes | 100 |  |  |
| NC HD-37 | exact | 0 | 0.0 | 0.0 | 59884 | 59884 | yes | 100 |  |  |
| NC HD-38 | exact | 0 | 0.0 | 0.0 | 32854 | 32854 | yes | 100 |  |  |
| NC HD-39 | exact | 0 | 0.0 | 0.0 | 36730 | 36730 | yes | 100 |  | name: James Roberson → James A. Roberson |
| NC HD-40 | exact | 0 | 0.0 | 0.0 | 52927 | 52927 | yes | 100 |  | name: Mike Munger → Michael Munger |
| NC HD-41 | exact | 0 | 0.0 | 0.0 | 41235 | 41235 | yes | 100 |  |  |
| NC HD-42 | exact | 0 | 0.0 | 0.0 | 24633 | 24633 | yes | 100 |  |  |
| NC HD-43 | exact | 0 | 0.0 | 0.0 | 43175 | 43175 | yes | 100 |  | name: Janene (Dublin) Ackles → Janene Dublin Ackles |
| NC HD-44 | exact | 0 | 0.0 | 0.0 | 33624 | 33624 | yes | 100 |  | name: Charles Smith → Charles R. Smith |
| NC HD-45 | exact | 0 | 0.0 | 0.0 | 25090 | 25090 | yes | 100 |  |  |
| NC HD-46 | exact | 0 | 0.0 | 0.0 | 29064 | 29064 | yes | 100 |  |  |
| NC HD-47 | exact | 0 | 0.0 | 0.0 | 31610 | 31610 | yes | 100 |  |  |
| NC HD-48 | exact | 0 | 0.0 | 0.0 | 36298 | 36298 | yes | 100 |  |  |
| NC HD-49 | exact | 0 | 0.0 | 0.0 | 32814 | 32814 | yes | 100 |  |  |
| NC HD-50 | exact | 0 | 0.0 | 0.0 | 35367 | 35367 | yes | 100 |  | name: Renee A. Price → Renee Price |
| NC HD-51 | exact | 0 | 0.0 | 0.0 | 40168 | 40168 | yes | 100 |  | name: John Sauls → John I. Sauls |
| NC HD-52 | exact | 0 | 0.0 | 0.0 | 42301 | 42301 | yes | 100 |  | name: Ben Moss, Jr. → Ben Moss |
| NC HD-53 | exact | 0 | 0.0 | 0.0 | 45874 | 45874 | yes | 100 |  | name: Howard Penny, Jr. → Howard Penny |
| NC HD-54 | exact | 0 | 0.0 | 0.0 | 54624 | 54624 | yes | 100 |  | name: Joe Godfrey → Joseph Godfrey |
| NC HD-55 | exact | 0 | 0.0 | 0.0 | 38847 | 38847 | yes | 100 |  |  |
| NC HD-56 | exact | 0 | 0.0 | 0.0 | 45726 | 45726 | yes | 100 |  | name: Jeffrey Hoagland → Jeffrey C. Hoagland |
| NC HD-57 | exact | 0 | 0.0 | 0.0 | 46941 | 46941 | yes | 100 |  |  |
| NC HD-58 | exact | 0 | 0.0 | 0.0 | 32379 | 32379 | yes | 100 |  | name: Amos Quick → Amos L. Quick, III |
| NC HD-59 | exact | 0 | 0.0 | 0.0 | 55468 | 55468 | yes | 100 |  | name: Alan Branson → J. Alan Branson |
| NC HD-60 | exact | 0 | 0.0 | 0.0 | 37827 | 37827 | yes | 100 |  |  |
| NC HD-61 | differs | 9905 | 53.7646 | 23.109 | 42862 | 37827 | yes | 100 |  | name: Mary Price (Pricey) Harrison → Mary Harrison |
| NC HD-62 | exact | 0 | 0.0 | 0.0 | 54986 | 54986 | yes | 100 |  |  |
| NC HD-63 | exact | 0 | 0.0 | 0.0 | 43272 | 43272 | yes | 100 |  | name: Stephen Ross → Stephen M. Ross |
| NC HD-64 | exact | 0 | 0.0 | 0.0 | 44753 | 44753 | yes | 100 |  |  |
| NC HD-65 | exact | 0 | 0.0 | 0.0 | 37457 | 37457 | yes | 100 |  |  |
| NC HD-66 | exact | 0 | 0.0 | 0.0 | 44385 | 44385 | yes | 100 |  |  |
| NC HD-67 | exact | 0 | 0.0 | 0.0 | 48457 | 48457 | yes | 100 |  |  |
| NC HD-68 | exact | 0 | 0.0 | 0.0 | 54097 | 54097 | yes | 100 |  |  |
| NC HD-69 | exact | 0 | 0.0 | 0.0 | 51887 | 51887 | yes | 100 |  |  |
| NC HD-70 | exact | 0 | 0.0 | 0.0 | 44137 | 44137 | yes | 100 |  | name: Susan Lee (Susie) Scott → Susan Lee Scott |
| NC HD-71 | exact | 0 | 0.0 | 0.0 | 31755 | 31755 | yes | 100 |  |  |
| NC HD-72 | exact | 0 | 0.0 | 0.0 | 31479 | 31479 | yes | 100 |  |  |
| NC HD-73 | exact | 0 | 0.0 | 0.0 | 43977 | 43977 | yes | 100 |  |  |
| NC HD-74 | exact | 0 | 0.0 | 0.0 | 51923 | 51923 | yes | 100 |  | name: Jeffrey (Jeff) Zenger → Jeff Zenger |
| NC HD-75 | exact | 0 | 0.0 | 0.0 | 45861 | 45861 | yes | 100 |  | name: Donny C. Lambeth → Donny Lambeth |
| NC HD-76 | exact | 0 | 0.0 | 0.0 | 43389 | 43389 | yes | 100 |  |  |
| NC HD-77 | exact | 0 | 0.0 | 0.0 | 52147 | 52147 | yes | 100 |  |  |
| NC HD-78 | exact | 0 | 0.0 | 0.0 | 50913 | 50913 | yes | 100 |  |  |
| NC HD-79 | exact | 0 | 0.0 | 0.0 | 50778 | 50778 | yes | 100 |  |  |
| NC HD-80 | exact | 0 | 0.0 | 0.0 | 44781 | 44781 | yes | 100 |  |  |
| NC HD-81 | exact | 0 | 0.0 | 0.0 | 47264 | 47264 | yes | 100 |  |  |
| NC HD-82 | differs | 3000 | 11.1255 | 6.047 | 49614 | 52614 | yes | 100 |  |  |
| NC HD-83 | exact | 0 | 0.0 | 0.0 | 46104 | 46104 | yes | 100 |  |  |
| NC HD-84 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 45738 | 45392 | yes | 100 |  | only official: Lisa Mozer (Write-In), Write-In (Miscellaneous); only civic: Lisa Mozer |
| NC HD-85 | exact | 0 | 0.0 | 0.0 | 48719 | 48719 | yes | 100 |  |  |
| NC HD-86 | exact | 0 | 0.0 | 0.0 | 44601 | 44601 | yes | 100 |  | name: Hugh Blackwell → Hugh A. Blackwell |
| NC HD-87 | exact | 0 | 0.0 | 0.0 | 45329 | 45329 | yes | 100 |  |  |
| NC HD-88 | exact | 0 | 0.0 | 0.0 | 33555 | 33555 | yes | 100 |  |  |
| NC HD-89 | exact | 0 | 0.0 | 0.0 | 50166 | 50166 | yes | 100 |  |  |
| NC HD-90 | exact | 0 | 0.0 | 0.0 | 45793 | 45793 | yes | 100 |  |  |
| NC HD-91 | exact | 0 | 0.0 | 0.0 | 44402 | 44402 | yes | 100 |  |  |
| NC HD-92 | exact | 0 | 0.0 | 0.0 | 37214 | 37214 | yes | 100 |  |  |
| NC HD-93 | exact | 0 | 0.0 | 0.0 | 50593 | 50593 | yes | 100 |  |  |
| NC HD-94 | exact | 0 | 0.0 | 0.0 | 45902 | 45902 | yes | 100 |  |  |
| NC HD-95 | exact | 0 | 0.0 | 0.0 | 50257 | 50257 | yes | 100 |  | name: Richard (Todd) Carver → Richard Carver |
| NC HD-96 | exact | 0 | 0.0 | 0.0 | 43666 | 43666 | yes | 100 |  | name: Elizabeth (Eli) Glynn → Elizabeth Glynn |
| NC HD-97 | exact | 0 | 0.0 | 0.0 | 43332 | 43332 | yes | 100 |  |  |
| NC HD-98 | exact | 0 | 0.0 | 0.0 | 51883 | 51883 | yes | 100 |  |  |
| NC HD-99 | exact | 0 | 0.0 | 0.0 | 32145 | 32145 | yes | 100 |  |  |
| NC LTGOV | exact | 0 | 0.0 | 0.0 | 5589852 | 5589852 | yes | 100 | 100/100, 14 | name: Rachel Hunt → Rachel H. Hunt; Shannon W. Bray → Shannon Bray |
| NC SD-01 | exact | 0 | 0.0 | 0.0 | 109784 | 109784 | yes | 100 |  |  |
| NC SD-02 | exact | 0 | 0.0 | 0.0 | 112413 | 112413 | yes | 100 |  | name: Norman W. Sanderson → Norman W. Sanderson, Jr.; Tare (T) Davis → Tare Davis |
| NC SD-03 | exact | 0 | 0.0 | 0.0 | 106729 | 106729 | yes | 100 |  | name: Bob Brinson → Robert Brinson, Jr.; Charles T. Dudley → Charles Dudley |
| NC SD-04 | exact | 0 | 0.0 | 0.0 | 100485 | 100485 | yes | 100 |  | name: Buck Newton → E. S. Newton III; Raymond Smith, Jr. → Raymond E. Smith, Jr. |
| NC SD-05 | exact | 0 | 0.0 | 0.0 | 107909 | 107909 | yes | 100 |  |  |
| NC SD-06 | exact | 0 | 0.0 | 0.0 | 77442 | 77442 | yes | 100 |  |  |
| NC SD-07 | exact | 0 | 0.0 | 0.0 | 121073 | 121073 | yes | 100 |  | name: Michael Lee → Michael V. Lee |
| NC SD-08 | differs | 180 | 0.2068 | 0.124 | 145285 | 145465 | yes | 100 |  | name: Bill Rabon → William P. Rabon |
| NC SD-09 | exact | 0 | 0.0 | 0.0 | 105532 | 105532 | yes | 100 |  |  |
| NC SD-10 | exact | 0 | 0.0 | 0.0 | 120470 | 120470 | yes | 100 |  |  |
| NC SD-11 | exact | 0 | 0.0 | 0.0 | 112519 | 112519 | yes | 100 |  |  |
| NC SD-12 | exact | 0 | 0.0 | 0.0 | 92762 | 92762 | yes | 100 |  |  |
| NC SD-13 | exact | 0 | 0.0 | 0.0 | 127603 | 127603 | yes | 100 |  |  |
| NC SD-14 | exact | 0 | 0.0 | 0.0 | 101442 | 101442 | yes | 100 |  |  |
| NC SD-15 | exact | 0 | 0.0 | 0.0 | 102137 | 102137 | yes | 100 |  | name: Jay J. Chaudhuri → Jay Chaudhuri |
| NC SD-16 | exact | 0 | 0.0 | 0.0 | 84424 | 84424 | yes | 100 |  |  |
| NC SD-17 | exact | 0 | 0.0 | 0.0 | 96271 | 96271 | yes | 100 |  | name: Mrs. Sydney Batch → Sydney Batch |
| NC SD-18 | exact | 0 | 0.0 | 0.0 | 123112 | 123112 | yes | 100 |  |  |
| NC SD-19 | exact | 0 | 0.0 | 0.0 | 88586 | 88586 | yes | 100 |  |  |
| NC SD-20 | exact | 0 | 0.0 | 0.0 | 124698 | 124698 | yes | 100 |  | name: Natalie S. Murdock → Natalie Murdock |
| NC SD-21 | exact | 0 | 0.0 | 0.0 | 107546 | 107546 | yes | 100 |  | name: Maurice (Butch) Holland, Jr. → Maurice Holland, Jr. |
| NC SD-22 | exact | 0 | 0.0 | 0.0 | 97518 | 97518 | yes | 100 |  | name: Ray Ubinger → Raymond C. Ubinger |
| NC SD-23 | exact | 0 | 0.0 | 0.0 | 117157 | 117157 | yes | 100 |  |  |
| NC SD-24 | exact | 0 | 0.0 | 0.0 | 80925 | 80925 | yes | 100 |  | name: Danny Earl Britt, Jr. → Danny Earl Britt |
| NC SD-25 | exact | 0 | 0.0 | 0.0 | 112473 | 112473 | yes | 100 |  |  |
| NC SD-26 | exact | 0 | 0.0 | 0.0 | 124095 | 124095 | yes | 100 |  | name: Philip E. (Phil) Berger → Philip E. Berger; Alvin Robinson → Alvin D. Robinson |
| NC SD-27 | exact | 0 | 0.0 | 0.0 | 107157 | 107157 | yes | 100 |  |  |
| NC SD-28 | exact | 0 | 0.0 | 0.0 | 78876 | 78876 | yes | 100 |  |  |
| NC SD-29 | exact | 0 | 0.0 | 0.0 | 103400 | 103400 | yes | 100 |  | name: Dave Craven, Jr. → David Craven |
| NC SD-30 | exact | 0 | 0.0 | 0.0 | 118636 | 118636 | yes | 100 |  |  |
| NC SD-31 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 125657 | 125471 | yes | 100 |  | only official: Write-In (Miscellaneous), Teresa Hopper Prizer (Write-In); only civic: Teresa Hopper Prizer |
| NC SD-32 | exact | 0 | 0.0 | 0.0 | 97331 | 97331 | yes | 100 |  | name: Paul Lowe → Paul A. Lowe, Jr. |
| NC SD-33 | exact | 0 | 0.0 | 0.0 | 108624 | 108624 | yes | 100 |  | name: Tangela (Lucy Horne) Morgan → Tangela Morgan |
| NC SD-34 | exact | 0 | 0.0 | 0.0 | 109895 | 109895 | yes | 100 |  | name: Paul Newton → Paul R. Newton |
| NC SD-35 | exact | 0 | 0.0 | 0.0 | 129516 | 129516 | yes | 100 |  |  |
| NC SD-36 | exact | 0 | 0.0 | 0.0 | 111546 | 111546 | yes | 100 |  |  |
| NC SD-37 | exact | 0 | 0.0 | 0.0 | 127961 | 127961 | yes | 100 |  |  |
| NC SD-38 | exact | 0 | 0.0 | 0.0 | 94162 | 94162 | yes | 100 |  |  |
| NC SD-39 | exact | 0 | 0.0 | 0.0 | 88183 | 88183 | yes | 100 |  |  |
| NC SD-40 | exact | 0 | 0.0 | 0.0 | 85223 | 85223 | yes | 100 |  | name: Joyce Waddell → Joyce D. Waddell |
| NC SD-41 | exact | 0 | 0.0 | 0.0 | 95805 | 95805 | yes | 100 |  |  |
| NC SD-42 | exact | 0 | 0.0 | 0.0 | 124311 | 124311 | yes | 100 |  | name: Mrs. Woodson Bradley → Woodson Bradley |
| NC SD-43 | exact | 0 | 0.0 | 0.0 | 107005 | 107005 | yes | 100 |  |  |
| NC SD-44 | exact | 0 | 0.0 | 0.0 | 112842 | 112842 | yes | 100 |  |  |
| NC SD-45 | exact | 0 | 0.0 | 0.0 | 113873 | 113873 | yes | 100 |  | name: Mark Hollo → Mark W. Hollo |
| NC SD-46 | exact | 0 | 0.0 | 0.0 | 105279 | 105279 | yes | 100 |  | name: Warren Daniel → Warren T. Daniel |
| NC SD-47 | exact | 0 | 0.0 | 0.0 | 117854 | 117854 | yes | 100 |  |  |
| NC SD-48 | exact | 0 | 0.0 | 0.0 | 114772 | 114772 | yes | 100 |  |  |
| NC SD-49 | exact | 0 | 0.0 | 0.0 | 117325 | 117325 | yes | 100 |  | name: Kristie Tincher Sluder → Kristie Sluder |
| NC SD-50 | exact | 0 | 0.0 | 0.0 | 125320 | 125320 | yes | 100 |  |  |
| NC SOS | exact | 0 | 0.0 | 0.0 | 5560788 | 5560788 | yes | 100 | 100/100, 13 |  |
| NC SUPT | exact | 0 | 0.0 | 0.0 | 5544559 | 5544559 | yes | 100 | 100/100, 9 | name: Maurice (Mo) Green → Maurice Green |
| NC TREAS | differs | 855 | 0.0325 | 0.015 | 5528367 | 5529503 | yes | 100 | 100/100, 14 |  |
| NC-01 | exact | 0 | 0.0 | 0.0 | 376324 | 376324 | yes | 100 |  | name: Don Davis → Donald G. Davis; Tom Bailey → Thomas A. Bailey |
| NC-02 | exact | 0 | 0.0 | 0.0 | 405517 | 405517 | yes | 100 | 0/1, 0 |  |
| NC-03 | exact | 0 | 0.0 | 0.0 | 320841 | 320841 | yes | 100 | 11/11, 3 | name: Greg Murphy → Gregory Murphy |
| NC-04 | exact | 0 | 0.0 | 0.0 | 428780 | 428780 | yes | 100 | 4/4, 1 |  |
| NC-05 | exact | 0 | 0.0 | 0.0 | 400694 | 400694 | yes | 100 |  |  |
| NC-06 | exact | 0 | 0.0 | 0.0 | 337320 | 337320 | yes | 100 | 6/6, 1 | name: Addison McDowell → Addison P. McDowell |
| NC-07 | exact | 0 | 0.0 | 0.0 | 433534 | 433534 | yes | 100 |  |  |
| NC-08 | exact | 0 | 0.0 | 0.0 | 400349 | 400349 | yes | 100 | 9/9, 2 | name: Justin E. Dues → Justin Dues |
| NC-09 | exact | 0 | 0.0 | 0.0 | 373077 | 373077 | yes | 100 | 7/7, 1 |  |
| NC-10 | exact | 0 | 0.0 | 0.0 | 406695 | 406695 | yes | 100 |  | name: Ralph R. Scott, Jr. → Ralph R. Scott |
| NC-11 | exact | 0 | 0.0 | 0.0 | 432523 | 432523 | yes | 100 |  |  |
| NC-12 | exact | 0 | 0.0 | 0.0 | 350755 | 350755 | yes | 100 |  |  |
| NC-13 | exact | 0 | 0.0 | 0.0 | 415490 | 415490 | yes | 100 | 8/8, 1 |  |
| NC-14 | exact | 0 | 0.0 | 0.0 | 401256 | 401256 | yes | 100 | 6/6, 1 | name: Tim Moore → Timothy K. Moore |

## 2024 general, other states (MEDSL precinct-derived reference)

- Races listed: 645; compared: 629; exact (every candidate): 267; votes exact but write-in/label differs: 142; differ: 211; reference incomplete: 7; not comparable: 16
- Over usable comparisons: max abs diff 28705; median 0.0; max % 95.2869; median % 0.0; max race-% 32.424
- Winners agree: 619/620
- County detail: 51 races, 969 county pairs compared, 525 mismatched; counties missing in civicAPI: 8

| race | result | max abs | max % | race % | official total | civic total | winners | %rep | counties (both/off, mism) | names/notes |
|---|---|---|---|---|---|---|---|---|---|---|
| AL-01 | exact | 0 | 0.0 | 0.0 | 329548 | 329548 | yes | 100 | 9/9, 9 |  |
| AL-02 | exact | 0 | 0.0 | 0.0 | 289455 | 289455 | yes | 100 | 13/13, 12 |  |
| AL-03 | exact | 0 | 0.0 | 0.0 | 243848 | 243848 | yes | 100 | 11/11, 11 |  |
| AL-04 | exact | 0 | 0.0 | 0.0 | 274498 | 274498 | yes | 100 | 13/13, 13 |  |
| AZ HD-01 | reference artifact (MEDSL names / same surname) | 40022 | 4501.9123 | 15.4 | 259889 | 261527 | yes | 100 |  | only official: Marcia Smith, Ruby Smith |
| AZ HD-02 | exact | 0 | 0.0 | 0.0 | 157499 | 157499 | yes | 100 |  |  |
| AZ HD-03 | exact | 0 | 0.0 | 0.0 | 237661 | 237661 | yes | 100 |  |  |
| AZ HD-04 | exact | 0 | 0.0 | 0.0 | 275191 | 275191 | yes | 100 |  |  |
| AZ HD-05 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 153098 | 152665 | yes | 100 |  | only official: *Not Qualified 1*, *Not Qualified 2* |
| AZ HD-06 | differs | 2 | 0.0078 | 0.001 | 161999 | 162001 | yes | 100 |  |  |
| AZ HD-07 | exact | 0 | 0.0 | 0.0 | 226102 | 226102 | yes | 100 |  |  |
| AZ HD-08 | exact | 0 | 0.0 | 0.0 | 139781 | 139781 | yes | 100 |  |  |
| AZ HD-09 | exact | 0 | 0.0 | 0.0 | 147312 | 147312 | yes | 100 |  |  |
| AZ HD-10 | exact | 0 | 0.0 | 0.0 | 211033 | 211033 | yes | 100 |  |  |
| AZ HD-11 | exact | 0 | 0.0 | 0.0 | 127485 | 127485 | yes | 100 |  |  |
| AZ HD-12 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 181750 | 181102 | yes | 100 |  | only official: *Not Qualified 1*, *Not Qualified 2* |
| AZ HD-13 | exact | 0 | 0.0 | 0.0 | 221571 | 221571 | yes | 100 |  |  |
| AZ HD-14 | exact | 0 | 0.0 | 0.0 | 173997 | 173997 | yes | 100 |  |  |
| AZ HD-15 | exact | 0 | 0.0 | 0.0 | 200772 | 200772 | yes | 100 |  |  |
| AZ HD-16 | differs | 10222 | 29.0216 | 8.398 | 121717 | 148326 | yes | 100 |  |  |
| AZ HD-17 | reference incomplete (MEDSL) | 68742 | 1311.1196 | 394.774 | 17413 | 214509 | NO | 100 |  |  |
| AZ HD-18 | no official comparison data | | | | | | | | | |
| AZ HD-19 | reference incomplete (MEDSL) | 21366 | 84.0194 | 19.864 | 107559 | 167160 | yes | 100 |  |  |
| AZ HD-20 | no official comparison data | | | | | | | | | |
| AZ HD-21 | reference incomplete (MEDSL) | 35194 | 336.0185 | 112.776 | 31207 | 125194 | yes | 100 |  |  |
| AZ HD-22 | exact | 0 | 0.0 | 0.0 | 121875 | 121875 | yes | 100 |  |  |
| AZ HD-23 | reference incomplete (MEDSL) | 8822 | 30.8969 | 9.962 | 88554 | 111130 | yes | 100 |  |  |
| AZ HD-24 | exact | 0 | 0.0 | 0.0 | 58927 | 58927 | yes | 100 |  |  |
| AZ HD-25 | exact | 0 | 0.0 | 0.0 | 173974 | 173974 | yes | 100 |  |  |
| AZ HD-26 | exact | 0 | 0.0 | 0.0 | 83816 | 83816 | yes | 100 |  |  |
| AZ HD-27 | exact | 0 | 0.0 | 0.0 | 148076 | 148076 | yes | 100 |  |  |
| AZ HD-28 | exact | 0 | 0.0 | 0.0 | 210731 | 210731 | yes | 100 |  |  |
| AZ HD-29 | exact | 0 | 0.0 | 0.0 | 219043 | 219043 | yes | 100 |  |  |
| AZ HD-30 | differs | 1080 | 1.3888 | 0.591 | 182650 | 180163 | yes | 100 |  |  |
| AZ SD-01 | differs | 1110 | 2.3126 | 0.776 | 143106 | 144055 | yes | 100 |  | only official: Mike S Fogel |
| AZ SD-02 | exact | 0 | 0.0 | 0.0 | 102509 | 102509 | yes | 100 |  |  |
| AZ SD-03 | exact | 0 | 0.0 | 0.0 | 110048 | 110048 | yes | 100 |  |  |
| AZ SD-04 | exact | 0 | 0.0 | 0.0 | 146693 | 146693 | yes | 100 |  |  |
| AZ SD-05 | exact | 0 | 0.0 | 0.0 | 98721 | 98721 | yes | 100 |  |  |
| AZ SD-06 | exact | 0 | 0.0 | 0.0 | 72721 | 72721 | yes | 100 |  |  |
| AZ SD-07 | exact | 0 | 0.0 | 0.0 | 124156 | 124156 | yes | 100 |  |  |
| AZ SD-08 | exact | 0 | 0.0 | 0.0 | 87639 | 87639 | yes | 100 |  |  |
| AZ SD-09 | exact | 0 | 0.0 | 0.0 | 81333 | 81333 | yes | 100 |  |  |
| AZ SD-10 | exact | 0 | 0.0 | 0.0 | 114731 | 114731 | yes | 100 |  |  |
| AZ SD-11 | exact | 0 | 0.0 | 0.0 | 72201 | 72201 | yes | 100 |  |  |
| AZ SD-12 | exact | 0 | 0.0 | 0.0 | 121295 | 121295 | yes | 100 |  |  |
| AZ SD-13 | exact | 0 | 0.0 | 0.0 | 117957 | 117957 | yes | 100 |  |  |
| AZ SD-14 | exact | 0 | 0.0 | 0.0 | 126805 | 126805 | yes | 100 |  |  |
| AZ SD-15 | exact | 0 | 0.0 | 0.0 | 137410 | 137410 | yes | 100 |  |  |
| AZ SD-16 | exact | 0 | 0.0 | 0.0 | 104274 | 104274 | yes | 100 |  |  |
| AZ SD-17 | exact | 0 | 0.0 | 0.0 | 152383 | 152383 | yes | 100 |  |  |
| AZ SD-18 | exact | 0 | 0.0 | 0.0 | 89003 | 89003 | yes | 100 |  |  |
| AZ SD-19 | exact | 0 | 0.0 | 0.0 | 113747 | 113747 | yes | 100 |  |  |
| AZ SD-20 | exact | 0 | 0.0 | 0.0 | 65214 | 65214 | yes | 100 |  |  |
| AZ SD-21 | exact | 0 | 0.0 | 0.0 | 64282 | 64282 | yes | 100 |  |  |
| AZ SD-22 | exact | 0 | 0.0 | 0.0 | 70065 | 70065 | yes | 100 |  |  |
| AZ SD-23 | exact | 0 | 0.0 | 0.0 | 79260 | 79260 | yes | 100 |  |  |
| AZ SD-24 | exact | 0 | 0.0 | 0.0 | 38436 | 38436 | yes | 100 |  |  |
| AZ SD-25 | differs | 28705 | 32.4237 | 32.424 | 88531 | 59826 | yes | 100 |  |  |
| AZ SD-26 | exact | 0 | 0.0 | 0.0 | 47024 | 47024 | yes | 100 |  |  |
| AZ SD-27 | exact | 0 | 0.0 | 0.0 | 67274 | 67274 | yes | 100 |  |  |
| AZ SD-28 | exact | 0 | 0.0 | 0.0 | 98286 | 98286 | yes | 100 |  |  |
| AZ SD-29 | exact | 0 | 0.0 | 0.0 | 119532 | 119532 | yes | 100 |  |  |
| AZ SD-30 | differs | 1177 | 1.384 | 0.989 | 119040 | 117508 | yes | 100 |  |  |
| AZ SEN | differs | 802 | 80.7018 | 0.024 | 3347495 | 3348776 | yes | 100 | 15/15, 12 | only official: Benjamin Rodriguez, David Bozic, Shawn Petty |
| AZ-01 | exact | 0 | 0.0 | 0.0 | 434504 | 434504 | yes | 100 | 0/1, 0 |  |
| AZ-02 | differs | 772 | 0.3499 | 0.19 | 405413 | 406431 | yes | 100 | 9/9, 6 | only civic: Richard W. Ester, David Alexander, Charles E. Holt, Jr. |
| AZ-03 | exact | 0 | 0.0 | 0.0 | 202065 | 202065 | yes | 100 | 0/1, 0 |  |
| AZ-04 | exact | 0 | 0.0 | 0.0 | 334545 | 334545 | yes | 100 | 0/1, 0 |  |
| AZ-05 | exact | 0 | 0.0 | 0.0 | 423308 | 423308 | yes | 100 | 2/2, 0 |  |
| AZ-06 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 431129 | 431220 | yes | 100 | 5/5, 3 | only civic: Luis Pozzolo |
| AZ-07 | differs | 12 | 0.007 | 0.004 | 270996 | 271011 | yes | 100 | 6/6, 1 |  |
| AZ-08 | exact | 0 | 0.0 | 0.0 | 368613 | 368613 | yes | 100 |  |  |
| AZ-09 | exact | 0 | 0.0 | 0.0 | 382223 | 382223 | yes | 100 | 4/4, 0 |  |
| CA HD-01 | differs | 202 | 0.1614 | 0.097 | 209264 | 209554 | yes | 100 |  |  |
| CA HD-02 | differs | 442 | 0.3939 | 0.189 | 234378 | 235135 | yes | 100 |  |  |
| CA HD-03 | differs | 374 | 0.2851 | 0.189 | 198043 | 198540 | yes | 100 |  |  |
| CA HD-04 | differs | 151 | 0.1787 | 0.075 | 200427 | 200698 | yes | 100 |  |  |
| CA HD-05 | differs | 223 | 0.1282 | 0.079 | 280697 | 280973 | yes | 100 |  |  |
| CA HD-06 | differs | 26 | 0.0393 | 0.013 | 199754 | 199798 | yes | 100 |  |  |
| CA HD-07 | differs | 22 | 0.0189 | 0.01 | 216704 | 216741 | yes | 100 |  |  |
| CA HD-08 | differs | 141 | 0.1444 | 0.067 | 210904 | 211177 | yes | 100 |  |  |
| CA HD-09 | differs | 161 | 0.1247 | 0.087 | 184240 | 184437 | yes | 100 |  |  |
| CA HD-10 | differs | 29 | 0.0369 | 0.016 | 184123 | 184174 | yes | 100 |  |  |
| CA HD-11 | differs | 311 | 0.3581 | 0.147 | 210971 | 211439 | yes | 100 |  |  |
| CA HD-12 | differs | 255 | 0.2299 | 0.105 | 243500 | 243891 | yes | 100 |  |  |
| CA HD-13 | differs | 60 | 0.1014 | 0.044 | 137200 | 137308 | yes | 100 |  |  |
| CA HD-14 | differs | 88 | 0.0958 | 0.048 | 182280 | 182423 | yes | 100 |  |  |
| CA HD-15 | differs | 216 | 0.2392 | 0.105 | 205220 | 205612 | yes | 100 |  |  |
| CA HD-16 | differs | 186 | 0.2068 | 0.074 | 250827 | 251165 | yes | 100 |  |  |
| CA HD-17 | differs | 6 | 0.0035 | 0.003 | 200384 | 200390 | yes | 100 |  |  |
| CA HD-18 | differs | 22 | 0.0334 | 0.013 | 167023 | 167056 | yes | 100 |  |  |
| CA HD-19 | differs | 11 | 0.0142 | 0.006 | 196452 | 196474 | yes | 100 |  |  |
| CA HD-20 | differs | 97 | 0.1745 | 0.056 | 173496 | 173675 | yes | 100 |  |  |
| CA HD-21 | differs | 58 | 0.0709 | 0.029 | 199414 | 199509 | yes | 100 |  |  |
| CA HD-22 | differs | 265 | 0.306 | 0.172 | 154252 | 154601 | yes | 100 |  |  |
| CA HD-23 | differs | 67 | 0.0822 | 0.035 | 193651 | 193782 | yes | 100 |  |  |
| CA HD-24 | differs | 39 | 0.0724 | 0.025 | 159011 | 159086 | yes | 100 |  |  |
| CA HD-25 | differs | 66 | 0.1104 | 0.042 | 157708 | 157829 | yes | 100 |  |  |
| CA HD-26 | differs | 23 | 0.0381 | 0.017 | 137155 | 137199 | yes | 100 |  |  |
| CA HD-27 | differs | 797 | 1.2496 | 0.574 | 138867 | 140135 | yes | 100 |  |  |
| CA HD-28 | differs | 134 | 0.1441 | 0.059 | 226246 | 226488 | yes | 100 |  |  |
| CA HD-29 | differs | 20 | 0.0201 | 0.013 | 150861 | 150891 | yes | 100 |  |  |
| CA HD-30 | differs | 34 | 0.038 | 0.014 | 238271 | 238328 | yes | 100 |  |  |
| CA HD-31 | differs | 179 | 0.3896 | 0.155 | 115610 | 115887 | yes | 100 |  |  |
| CA HD-32 | differs | 196 | 0.1706 | 0.101 | 194582 | 194872 | yes | 100 |  |  |
| CA HD-33 | differs | 114 | 0.1573 | 0.099 | 115183 | 115348 | yes | 100 |  |  |
| CA HD-34 | differs | 753 | 0.6436 | 0.399 | 188799 | 189903 | yes | 100 |  |  |
| CA HD-35 | differs | 184 | 0.4217 | 0.179 | 102974 | 103275 | yes | 100 |  |  |
| CA HD-36 | differs | 434 | 0.5491 | 0.284 | 152721 | 153403 | yes | 100 |  |  |
| CA HD-37 | differs | 146 | 0.1965 | 0.077 | 189394 | 189679 | yes | 100 |  |  |
| CA HD-38 | differs | 518 | 0.732 | 0.281 | 184221 | 185232 | yes | 100 |  |  |
| CA HD-39 | differs | 131 | 0.2484 | 0.105 | 124835 | 125023 | yes | 100 |  |  |
| CA HD-40 | differs | 113 | 0.1058 | 0.05 | 226447 | 226614 | yes | 100 |  |  |
| CA HD-41 | differs | 118 | 0.1214 | 0.05 | 234434 | 234629 | yes | 100 |  |  |
| CA HD-42 | differs | 269 | 0.2175 | 0.099 | 270764 | 271158 | yes | 100 |  |  |
| CA HD-43 | differs | 3 | 0.007 | 0.002 | 129005 | 129011 | yes | 100 |  |  |
| CA HD-44 | differs | 13 | 0.0175 | 0.006 | 217923 | 217941 | yes | 100 |  |  |
| CA HD-45 | differs | 210 | 0.3909 | 0.154 | 135964 | 136366 | yes | 100 |  |  |
| CA HD-46 | differs | 19 | 0.0301 | 0.011 | 170089 | 170117 | yes | 100 |  |  |
| CA HD-47 | differs | 474 | 0.3997 | 0.204 | 231894 | 232610 | yes | 100 |  |  |
| CA HD-48 | differs | 72 | 0.0709 | 0.044 | 164403 | 164517 | yes | 100 |  |  |
| CA HD-49 | differs | 40 | 0.0634 | 0.027 | 149233 | 149309 | yes | 100 |  |  |
| CA HD-50 | differs | 90 | 0.1477 | 0.065 | 138104 | 138283 | yes | 100 |  |  |
| CA HD-51 | differs | 20 | 0.0273 | 0.01 | 205445 | 205479 | yes | 100 |  |  |
| CA HD-52 | differs | 2 | 0.0037 | 0.001 | 162698 | 162702 | yes | 100 |  |  |
| CA HD-53 | differs | 65 | 0.106 | 0.045 | 144630 | 144751 | yes | 100 |  |  |
| CA HD-54 | differs | 13 | 0.0259 | 0.012 | 105833 | 105858 | yes | 100 |  |  |
| CA HD-55 | differs | 3 | 0.0057 | 0.002 | 183373 | 183378 | yes | 100 |  |  |
| CA HD-56 | differs | 39 | 0.0527 | 0.023 | 166591 | 166668 | yes | 100 |  |  |
| CA HD-57 | differs | 1 | 0.0029 | 0.001 | 88621 | 88623 | yes | 100 |  |  |
| CA HD-58 | differs | 47 | 0.0601 | 0.03 | 155925 | 155988 | yes | 100 |  |  |
| CA HD-59 | differs | 195 | 0.1404 | 0.085 | 229674 | 229947 | yes | 100 |  |  |
| CA HD-60 | differs | 77 | 0.1329 | 0.059 | 129825 | 129939 | yes | 100 |  |  |
| CA HD-61 | differs | 63 | 0.075 | 0.04 | 158945 | 159036 | yes | 100 |  |  |
| CA HD-62 | differs | 11 | 0.0159 | 0.009 | 129339 | 129357 | yes | 100 |  |  |
| CA HD-63 | differs | 80 | 0.0651 | 0.037 | 214567 | 214676 | yes | 100 |  |  |
| CA HD-64 | differs | 19 | 0.0227 | 0.012 | 164166 | 164199 | yes | 100 |  |  |
| CA HD-65 | differs | 49 | 0.0791 | 0.038 | 130113 | 130192 | yes | 100 |  |  |
| CA HD-66 | differs | 12 | 0.0091 | 0.005 | 218649 | 218666 | yes | 100 |  |  |
| CA HD-67 | differs | 68 | 0.0956 | 0.041 | 164750 | 164862 | yes | 100 |  |  |
| CA HD-68 | differs | 31 | 0.0563 | 0.023 | 132176 | 132234 | yes | 100 |  |  |
| CA HD-69 | differs | 17 | 0.0198 | 0.01 | 175907 | 175935 | yes | 100 |  |  |
| CA HD-70 | differs | 54 | 0.0562 | 0.031 | 175579 | 175670 | yes | 100 |  |  |
| CA HD-71 | differs | 153 | 0.1035 | 0.064 | 240147 | 240356 | yes | 100 |  |  |
| CA HD-72 | differs | 121 | 0.077 | 0.046 | 264375 | 264529 | yes | 100 |  |  |
| CA HD-73 | differs | 85 | 0.1033 | 0.045 | 190672 | 190810 | yes | 100 |  |  |
| CA HD-74 | differs | 60 | 0.0512 | 0.026 | 230449 | 230546 | yes | 100 |  |  |
| CA HD-75 | differs | 118 | 0.0975 | 0.056 | 212316 | 212504 | yes | 100 |  |  |
| CA HD-76 | differs | 150 | 0.1559 | 0.072 | 209349 | 209600 | yes | 100 |  |  |
| CA HD-77 | differs | 46 | 0.0298 | 0.018 | 255087 | 255156 | yes | 100 |  |  |
| CA HD-78 | differs | 36 | 0.0206 | 0.021 | 175142 | 175178 | yes | 100 |  |  |
| CA HD-79 | differs | 30 | 0.0379 | 0.02 | 146554 | 146605 | yes | 100 |  |  |
| CA HD-80 | differs | 22 | 0.0206 | 0.012 | 186567 | 186604 | yes | 100 |  |  |
| CA SD-01 | differs | 522 | 0.1906 | 0.144 | 362521 | 363211 | yes | 100 |  |  |
| CA SD-03 | differs | 511 | 0.3093 | 0.116 | 441930 | 442834 | yes | 100 |  |  |
| CA SD-05 | differs | 197 | 0.1166 | 0.055 | 358528 | 358804 | yes | 100 |  |  |
| CA SD-07 | differs | 79 | 0.0529 | 0.023 | 348680 | 348838 | yes | 100 |  |  |
| CA SD-09 | differs | 341 | 0.1765 | 0.092 | 371733 | 372334 | yes | 100 |  |  |
| CA SD-11 | differs | 23 | 0.0129 | 0.006 | 417828 | 417863 | yes | 100 |  |  |
| CA SD-13 | differs | 159 | 0.1104 | 0.037 | 434272 | 434563 | yes | 100 |  |  |
| CA SD-15 | differs | 98 | 0.0822 | 0.026 | 379839 | 380029 | yes | 100 |  |  |
| CA SD-17 | differs | 136 | 0.0545 | 0.033 | 414639 | 414854 | yes | 100 |  |  |
| CA SD-19 | differs | 1164 | 0.5581 | 0.305 | 381299 | 383030 | yes | 100 |  |  |
| CA SD-21 | differs | 742 | 0.4926 | 0.181 | 410556 | 412021 | yes | 100 |  |  |
| CA SD-23 | differs | 364 | 0.191 | 0.1 | 364147 | 364652 | yes | 100 |  |  |
| CA SD-25 | differs | 119 | 0.0723 | 0.029 | 407923 | 408128 | yes | 100 |  |  |
| CA SD-27 | differs | 152 | 0.0827 | 0.033 | 453746 | 453972 | yes | 100 |  |  |
| CA SD-29 | differs | 310 | 0.2567 | 0.11 | 281342 | 281905 | yes | 100 |  |  |
| CA SD-31 | differs | 68 | 0.0527 | 0.024 | 282170 | 282276 | yes | 100 |  |  |
| CA SD-33 | differs | 50 | 0.0235 | 0.016 | 311062 | 311134 | yes | 100 |  |  |
| CA SD-35 | differs | 73 | 0.0608 | 0.03 | 242862 | 243006 | yes | 100 |  |  |
| CA SD-37 | differs | 252 | 0.1086 | 0.055 | 458234 | 458615 | yes | 100 |  |  |
| CA SD-39 | differs | 93 | 0.0402 | 0.022 | 423290 | 423446 | yes | 100 |  |  |
| CA SEN | differs | 9941 | 0.1577 | 0.065 | 15332241 | 15348846 | yes | 100 | 58/58, 45 |  |
| CA SEN (special) | no official comparison data | | | | | | | | | |
| CA-01 | differs | 442 | 0.2123 | 0.139 | 318622 | 319228 | yes | 100 |  |  |
| CA-02 | differs | 499 | 0.3073 | 0.132 | 378791 | 379617 | yes | 100 | 6/6, 5 |  |
| CA-03 | differs | 351 | 0.1501 | 0.083 | 421855 | 422313 | yes | 100 |  |  |
| CA-04 | differs | 409 | 0.2669 | 0.12 | 341965 | 342680 | yes | 100 | 5/5, 4 |  |
| CA-05 | differs | 299 | 0.1315 | 0.081 | 368160 | 368562 | yes | 100 | 8/8, 7 |  |
| CA-06 | differs | 39 | 0.0321 | 0.014 | 287011 | 287072 | yes | 100 |  |  |
| CA-07 | differs | 68 | 0.0692 | 0.023 | 295634 | 295770 | yes | 100 |  |  |
| CA-08 | differs | 206 | 0.1917 | 0.076 | 272688 | 273030 | yes | 100 |  |  |
| CA-09 | differs | 168 | 0.1388 | 0.067 | 251099 | 251357 | yes | 100 |  |  |
| CA-10 | differs | 399 | 0.3226 | 0.11 | 363752 | 364544 | yes | 100 | 2/2, 1 |  |
| CA-11 | differs | 12 | 0.0047 | 0.004 | 339096 | 339111 | yes | 100 | 0/1, 0 |  |
| CA-12 | differs | 32 | 0.0194 | 0.011 | 282974 | 283025 | yes | 100 |  |  |
| CA-13 | differs | 1039 | 0.9959 | 0.496 | 209327 | 210921 | yes | 100 |  |  |
| CA-14 | differs | 127 | 0.1427 | 0.046 | 276139 | 276388 | yes | 100 |  |  |
| CA-15 | differs | 79 | 0.0732 | 0.027 | 289408 | 289544 | yes | 100 |  |  |
| CA-16 | differs | 120 | 0.0669 | 0.039 | 308302 | 308476 | yes | 100 |  |  |
| CA-17 | differs | 39 | 0.0473 | 0.015 | 254799 | 254877 | yes | 100 |  |  |
| CA-18 | differs | 72 | 0.073 | 0.032 | 228375 | 228506 | yes | 100 |  |  |
| CA-19 | differs | 137 | 0.0931 | 0.038 | 364079 | 364320 | yes | 100 |  |  |
| CA-20 | differs | 242 | 0.1628 | 0.084 | 288382 | 288788 | yes | 100 |  |  |
| CA-21 | differs | 173 | 0.1869 | 0.089 | 195261 | 195531 | yes | 100 | 2/2, 1 |  |
| CA-22 | differs | 311 | 0.3488 | 0.186 | 167055 | 167507 | yes | 100 |  |  |
| CA-23 | differs | 1079 | 0.682 | 0.41 | 263272 | 264849 | yes | 100 |  |  |
| CA-24 | differs | 423 | 0.3023 | 0.124 | 341671 | 342479 | yes | 100 |  |  |
| CA-25 | differs | 417 | 0.3905 | 0.171 | 244294 | 245031 | yes | 100 |  |  |
| CA-26 | differs | 527 | 0.36 | 0.158 | 333388 | 334306 | yes | 100 |  |  |
| CA-27 | differs | 224 | 0.1536 | 0.075 | 299768 | 300090 | yes | 100 |  |  |
| CA-28 | differs | 119 | 0.1079 | 0.038 | 314741 | 314944 | yes | 100 |  |  |
| CA-29 | differs | 14 | 0.0221 | 0.007 | 209665 | 209686 | yes | 100 |  |  |
| CA-30 | differs | 36 | 0.0365 | 0.012 | 311596 | 311659 | yes | 100 |  |  |
| CA-31 | differs | 117 | 0.0802 | 0.047 | 247754 | 247951 | yes | 100 |  |  |
| CA-32 | differs | 38 | 0.034 | 0.012 | 321570 | 321645 | yes | 100 |  |  |
| CA-33 | differs | 226 | 0.2358 | 0.097 | 232844 | 233275 | yes | 100 |  |  |
| CA-34 | differs | 4 | 0.0048 | 0.002 | 189408 | 189414 | yes | 100 | 0/1, 0 |  |
| CA-35 | differs | 153 | 0.1381 | 0.066 | 233268 | 233555 | yes | 100 |  |  |
| CA-36 | differs | 26 | 0.0196 | 0.007 | 357939 | 357987 | yes | 100 |  |  |
| CA-37 | differs | 3 | 0.0019 | 0.001 | 204811 | 204814 | yes | 100 |  |  |
| CA-38 | differs | 40 | 0.0242 | 0.015 | 275862 | 275928 | yes | 100 |  |  |
| CA-39 | differs | 54 | 0.0543 | 0.024 | 229566 | 229660 | yes | 100 |  |  |
| CA-40 | differs | 240 | 0.1133 | 0.063 | 383288 | 383635 | yes | 100 |  |  |
| CA-41 | differs | 323 | 0.1766 | 0.091 | 353986 | 354445 | yes | 100 |  |  |
| CA-42 | differs | 16 | 0.0188 | 0.007 | 233533 | 233563 | yes | 100 |  |  |
| CA-43 | differs | 89 | 0.0791 | 0.042 | 213101 | 213232 | yes | 100 | 0/1, 0 |  |
| CA-44 | differs | 37 | 0.0257 | 0.016 | 230798 | 230852 | yes | 100 |  |  |
| CA-45 | differs | 89 | 0.0565 | 0.028 | 315738 | 315875 | yes | 100 |  |  |
| CA-46 | differs | 78 | 0.101 | 0.037 | 211145 | 211292 | yes | 100 |  |  |
| CA-47 | differs | 161 | 0.0939 | 0.046 | 353055 | 353275 | yes | 100 |  |  |
| CA-48 | differs | 291 | 0.1364 | 0.081 | 359883 | 360290 | yes | 100 |  |  |
| CA-49 | differs | 88 | 0.0487 | 0.023 | 378195 | 378347 | yes | 100 |  |  |
| CA-50 | differs | 134 | 0.1041 | 0.037 | 360440 | 360695 | yes | 100 |  |  |
| CA-51 | differs | 63 | 0.0389 | 0.019 | 327471 | 327584 | yes | 100 |  |  |
| CA-52 | differs | 39 | 0.024 | 0.015 | 259658 | 259718 | yes | 100 |  |  |
| CO-08 | exact | 0 | 0.0 | 0.0 | 333616 | 333616 | yes | 100 | 3/3, 1 |  |
| CT SD-21 | exact | 0 | 0.0 | 0.0 | 36236 | 36236 | yes | 100 |  |  |
| DE GOV | exact | 0 | 0.0 | 0.0 | 498635 | 498635 | yes | 100 | 3/3, 0 |  |
| DE HD-01 | exact | 0 | 0.0 | 0.0 | 9252 | 9252 | yes | 100 |  |  |
| DE HD-02 | exact | 0 | 0.0 | 0.0 | 6590 | 6590 | yes | 100 |  |  |
| DE HD-03 | exact | 0 | 0.0 | 0.0 | 6307 | 6307 | yes | 100 |  |  |
| DE HD-04 | exact | 0 | 0.0 | 0.0 | 18163 | 18163 | yes | 100 |  |  |
| DE HD-05 | exact | 0 | 0.0 | 0.0 | 8214 | 8214 | yes | 100 |  |  |
| DE HD-06 | exact | 0 | 0.0 | 0.0 | 13352 | 13352 | yes | 100 |  |  |
| DE HD-07 | exact | 0 | 0.0 | 0.0 | 10255 | 10255 | yes | 100 |  |  |
| DE HD-08 | exact | 0 | 0.0 | 0.0 | 13745 | 13745 | yes | 100 |  |  |
| DE HD-09 | exact | 0 | 0.0 | 0.0 | 16606 | 16606 | yes | 100 |  |  |
| DE HD-10 | exact | 0 | 0.0 | 0.0 | 13161 | 13161 | yes | 100 |  |  |
| DE HD-11 | exact | 0 | 0.0 | 0.0 | 9896 | 9896 | yes | 100 |  |  |
| DE HD-12 | exact | 0 | 0.0 | 0.0 | 16450 | 16450 | yes | 100 |  |  |
| DE HD-13 | exact | 0 | 0.0 | 0.0 | 9803 | 9803 | yes | 100 |  |  |
| DE HD-14 | exact | 0 | 0.0 | 0.0 | 17625 | 17625 | yes | 100 |  |  |
| DE HD-15 | exact | 0 | 0.0 | 0.0 | 9286 | 9286 | yes | 100 |  |  |
| DE HD-16 | exact | 0 | 0.0 | 0.0 | 8092 | 8092 | yes | 100 |  |  |
| DE HD-17 | exact | 0 | 0.0 | 0.0 | 7956 | 7956 | yes | 100 |  |  |
| DE HD-18 | exact | 0 | 0.0 | 0.0 | 7760 | 7760 | yes | 100 |  |  |
| DE HD-19 | exact | 0 | 0.0 | 0.0 | 11081 | 11081 | yes | 100 |  |  |
| DE HD-20 | exact | 0 | 0.0 | 0.0 | 17469 | 17469 | yes | 100 |  |  |
| DE HD-21 | exact | 0 | 0.0 | 0.0 | 12812 | 12812 | yes | 100 |  |  |
| DE HD-22 | exact | 0 | 0.0 | 0.0 | 15704 | 15704 | yes | 100 |  |  |
| DE HD-23 | exact | 0 | 0.0 | 0.0 | 7934 | 7934 | yes | 100 |  |  |
| DE HD-24 | exact | 0 | 0.0 | 0.0 | 9551 | 9551 | yes | 100 |  |  |
| DE HD-25 | exact | 0 | 0.0 | 0.0 | 9194 | 9194 | yes | 100 |  |  |
| DE HD-26 | exact | 0 | 0.0 | 0.0 | 7123 | 7123 | yes | 100 |  |  |
| DE HD-27 | exact | 0 | 0.0 | 0.0 | 13817 | 13817 | yes | 100 |  |  |
| DE HD-28 | exact | 0 | 0.0 | 0.0 | 8620 | 8620 | yes | 100 |  |  |
| DE HD-29 | exact | 0 | 0.0 | 0.0 | 14146 | 14146 | yes | 100 |  |  |
| DE HD-30 | exact | 0 | 0.0 | 0.0 | 10118 | 10118 | yes | 100 |  |  |
| DE HD-31 | exact | 0 | 0.0 | 0.0 | 7109 | 7109 | yes | 100 |  |  |
| DE HD-32 | exact | 0 | 0.0 | 0.0 | 7731 | 7731 | yes | 100 |  |  |
| DE HD-33 | exact | 0 | 0.0 | 0.0 | 9101 | 9101 | yes | 100 |  |  |
| DE HD-34 | exact | 0 | 0.0 | 0.0 | 12947 | 12947 | yes | 100 |  |  |
| DE HD-35 | exact | 0 | 0.0 | 0.0 | 9015 | 9015 | yes | 100 |  |  |
| DE HD-36 | exact | 0 | 0.0 | 0.0 | 12340 | 12340 | yes | 100 |  |  |
| DE HD-37 | exact | 0 | 0.0 | 0.0 | 8250 | 8250 | yes | 100 |  |  |
| DE HD-38 | exact | 0 | 0.0 | 0.0 | 15341 | 15341 | yes | 100 |  |  |
| DE HD-39 | exact | 0 | 0.0 | 0.0 | 7440 | 7440 | yes | 100 |  |  |
| DE HD-40 | exact | 0 | 0.0 | 0.0 | 9086 | 9086 | yes | 100 |  |  |
| DE HD-41 | exact | 0 | 0.0 | 0.0 | 14732 | 14732 | yes | 100 |  |  |
| DE INSCOM | exact | 0 | 0.0 | 0.0 | 493729 | 493729 | yes | 100 | 3/3, 0 |  |
| DE LTGOV | exact | 0 | 0.0 | 0.0 | 494084 | 494084 | yes | 100 | 3/3, 0 |  |
| DE SD-02 | exact | 0 | 0.0 | 0.0 | 15353 | 15353 | yes | 100 |  |  |
| DE SD-03 | exact | 0 | 0.0 | 0.0 | 11831 | 11831 | yes | 100 |  |  |
| DE SD-04 | exact | 0 | 0.0 | 0.0 | 18984 | 18984 | yes | 100 |  |  |
| DE SD-06 | exact | 0 | 0.0 | 0.0 | 37188 | 37188 | yes | 100 |  |  |
| DE SD-10 | exact | 0 | 0.0 | 0.0 | 19510 | 19510 | yes | 100 |  |  |
| DE SD-11 | exact | 0 | 0.0 | 0.0 | 15898 | 15898 | yes | 100 |  |  |
| DE SD-16 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 16094 | 16275 | yes | 100 |  | only civic: William R. McVay |
| DE SD-17 | differs | 7 | 0.0478 | 0.048 | 14630 | 14623 | yes | 100 |  |  |
| DE SD-18 | exact | 0 | 0.0 | 0.0 | 18563 | 18563 | yes | 100 |  |  |
| DE SD-21 | exact | 0 | 0.0 | 0.0 | 17105 | 17105 | yes | 100 |  |  |
| DE SEN | exact | 0 | 0.0 | 0.0 | 500606 | 500606 | yes | 100 | 3/3, 0 |  |
| DE-AL | exact | 0 | 0.0 | 0.0 | 497436 | 497436 | yes | 100 | 3/3, 0 |  |
| IA HD-01 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 8632 | 8643 | yes | 100 |  | only civic: Write-In |
| IA HD-02 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 11743 | 11761 | yes | 100 |  | only civic: Write-In |
| IA HD-03 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15248 | 15272 | yes | 100 |  | only civic: Write-in |
| IA HD-04 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 14292 | 14564 | yes | 100 |  | only civic: Write-in |
| IA HD-05 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 14282 | 14318 | yes | 100 |  | only civic: Write-in |
| IA HD-06 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 10020 | 10254 | yes | 100 |  | only civic: Write-in |
| IA HD-07 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15208 | 15274 | yes | 100 |  | only civic: Write-in |
| IA HD-08 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 10459 | 10731 | yes | 100 |  | only civic: Write-In |
| IA HD-09 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 16014 | 16035 | yes | 100 |  | only civic: Write-in |
| IA HD-10 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15293 | 15544 | yes | 100 |  | only civic: Write-in |
| IA HD-100 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 13955 | 14001 | yes | 100 |  | only civic: Write-In |
| IA HD-11 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15710 | 15724 | yes | 100 |  | only civic: Write-in |
| IA HD-12 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 13912 | 13930 | yes | 100 |  | only civic: Write-in |
| IA HD-13 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 17024 | 17055 | yes | 100 |  | only civic: Write-in |
| IA HD-14 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 11116 | 11401 | yes | 100 |  | only civic: Write-In |
| IA HD-15 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 18450 | 18481 | yes | 100 |  | only civic: Write-in |
| IA HD-16 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 17141 | 17172 | yes | 100 |  | only civic: Write-in |
| IA HD-17 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 12638 | 12847 | yes | 100 |  | only civic: Write-in |
| IA HD-18 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15103 | 15149 | yes | 100 |  | only civic: Write-in |
| IA HD-19 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 13220 | 13246 | yes | 100 |  | only civic: Write-In |
| IA HD-20 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 11201 | 11208 | yes | 100 |  | only civic: Write-In |
| IA HD-21 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 17350 | 17373 | yes | 100 |  | only civic: Write-in |
| IA HD-22 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 19995 | 20025 | yes | 100 |  | only civic: Write-In |
| IA HD-23 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 17776 | 17812 | yes | 100 |  | only civic: Write-in |
| IA HD-24 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 14774 | 14809 | yes | 100 |  | only civic: Write-in |
| IA HD-25 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 12293 | 12305 | yes | 100 |  | only civic: Write-In |
| IA HD-26 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15135 | 15167 | yes | 100 |  | only civic: Write-in |
| IA HD-27 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 21315 | 21333 | yes | 100 |  | only civic: Write-In |
| IA HD-28 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 19476 | 19503 | yes | 100 |  | only civic: Write-In |
| IA HD-29 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 7948 | 8303 | yes | 100 |  | only civic: Write-In |
| IA HD-30 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 11992 | 12045 | yes | 100 |  | only civic: Write-In |
| IA HD-31 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 12465 | 12978 | yes | 100 |  | only civic: Write-in |
| IA HD-32 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 11837 | 12337 | yes | 100 |  | only civic: Write-In |
| IA HD-33 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 9239 | 9395 | yes | 100 |  | only civic: Write-In |
| IA HD-34 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 10128 | 10180 | yes | 100 |  | only civic: Write-In |
| IA HD-35 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15879 | 15924 | yes | 100 |  | only civic: Write-In |
| IA HD-36 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 13031 | 13346 | yes | 100 |  | only civic: Write-In |
| IA HD-37 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 14955 | 15213 | yes | 100 |  | only civic: Write-in |
| IA HD-38 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 16199 | 16207 | yes | 100 |  | only civic: Write-In |
| IA HD-39 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 13728 | 13763 | yes | 100 |  | only civic: Write-In |
| IA HD-40 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 17122 | 17146 | yes | 100 |  | only civic: Write-In |
| IA HD-41 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 21276 | 21309 | yes | 100 |  | only civic: Write-In |
| IA HD-42 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 18067 | 18094 | yes | 100 |  | only civic: Write-In |
| IA HD-43 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 18837 | 18860 | yes | 100 |  | only civic: Write-In |
| IA HD-44 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15082 | 15204 | yes | 100 |  | only civic: Write-In |
| IA HD-45 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 19825 | 19865 | yes | 100 |  | only civic: Write-In |
| IA HD-46 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 19702 | 19722 | yes | 100 |  | only civic: Write-in |
| IA HD-47 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15526 | 15551 | yes | 100 |  | only civic: Write-in |
| IA HD-48 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 17676 | 17705 | yes | 100 |  | only civic: Write-in |
| IA HD-49 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 10139 | 10231 | yes | 100 |  | only civic: Write-In |
| IA HD-50 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 11802 | 12165 | yes | 100 |  | only civic: Write-In |
| IA HD-51 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 17994 | 18025 | yes | 100 |  | only civic: Write-in |
| IA HD-52 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 12306 | 12316 | yes | 100 |  | only civic: Write-In |
| IA HD-53 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15634 | 15658 | yes | 100 |  | only civic: Write-in |
| IA HD-54 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 13435 | 13646 | yes | 100 |  | only civic: Write-in |
| IA HD-55 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 13003 | 13231 | yes | 100 |  | only civic: Write-in |
| IA HD-56 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15043 | 15066 | yes | 100 |  | only civic: Write-in |
| IA HD-57 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 18049 | 18067 | yes | 100 |  | only civic: Write-in |
| IA HD-58 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15842 | 15863 | yes | 100 |  | only civic: Write-in |
| IA HD-59 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15767 | 15782 | yes | 100 |  | only civic: Write-In |
| IA HD-60 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 14459 | 14699 | yes | 100 |  | only civic: Write-in |
| IA HD-61 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 9230 | 9445 | yes | 100 |  | only civic: Write-In |
| IA HD-62 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 9408 | 9475 | yes | 100 |  | only civic: Write-In |
| IA HD-63 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 14550 | 14838 | yes | 100 |  | only civic: Write-in |
| IA HD-64 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15938 | 15983 | yes | 100 |  | only civic: Write-in |
| IA HD-65 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 18557 | 18577 | yes | 100 |  | only civic: Write-In |
| IA HD-66 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 17211 | 17224 | yes | 100 |  | only civic: Write-in |
| IA HD-67 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 14178 | 14443 | yes | 100 |  | only civic: Write-in |
| IA HD-68 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15392 | 15406 | yes | 100 |  | only civic: Write-in |
| IA HD-69 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15022 | 15031 | yes | 100 |  | only civic: Write-In |
| IA HD-70 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 17369 | 17391 | yes | 100 |  | only civic: Write-in |
| IA HD-71 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 14191 | 14212 | yes | 100 |  | only civic: Write-In |
| IA HD-72 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 16088 | 16114 | yes | 100 |  | only civic: Write-In |
| IA HD-73 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 12213 | 12787 | yes | 100 |  | only civic: Write-In |
| IA HD-74 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 11368 | 11795 | yes | 100 |  | only civic: Write-In |
| IA HD-75 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 11529 | 11800 | yes | 100 |  | only civic: Write-In |
| IA HD-76 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 17109 | 17128 | yes | 100 |  | only civic: Write-in |
| IA HD-77 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 9523 | 9853 | yes | 100 |  | only civic: Write-In |
| IA HD-78 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 10803 | 11168 | yes | 100 |  | only civic: Write-In |
| IA HD-79 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15169 | 15188 | yes | 100 |  | only civic: Write-In |
| IA HD-80 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 17312 | 17331 | yes | 100 |  | only civic: Write-In |
| IA HD-81 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 9806 | 10382 | yes | 100 |  | only civic: Write-In, Luana Stoltenberg |
| IA HD-82 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 17028 | 17037 | yes | 100 |  | only civic: Write-in |
| IA HD-83 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 19268 | 19282 | yes | 100 |  | only civic: Write-In |
| IA HD-84 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15922 | 16021 | yes | 100 |  | only civic: Write-in |
| IA HD-85 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 13794 | 14120 | yes | 100 |  | only civic: Write-In |
| IA HD-86 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 16478 | 16504 | yes | 100 |  | only civic: Write-In |
| IA HD-87 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 14262 | 14287 | yes | 100 |  | only civic: Write-in |
| IA HD-88 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 12191 | 12475 | yes | 100 |  | only civic: Write-in |
| IA HD-89 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 11945 | 12100 | yes | 100 |  | only civic: Write-In |
| IA HD-90 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 14477 | 14696 | yes | 100 |  | only civic: Write-In |
| IA HD-91 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 19408 | 19424 | yes | 100 |  | only civic: Write-in |
| IA HD-92 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15463 | 15489 | yes | 100 |  | only civic: Write-in |
| IA HD-93 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 19311 | 19335 | yes | 100 |  | only civic: Write-In |
| IA HD-94 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 17616 | 17643 | yes | 100 |  | only civic: Write-In |
| IA HD-95 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 15807 | 15841 | yes | 100 |  | only civic: Write-in |
| IA HD-96 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 13720 | 13739 | yes | 100 |  | only civic: Write-In |
| IA HD-97 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 13182 | 13199 | yes | 100 |  | only civic: Write-In |
| IA HD-98 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 13125 | 13154 | yes | 100 |  | only civic: Write-In |
| IA HD-99 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 13917 | 13955 | yes | 100 |  | only civic: Write-in |
| IA SD-02 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 27438 | 27683 | yes | 100 |  | only civic: Write-in |
| IA SD-04 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 23727 | 24258 | yes | 100 |  | only civic: Write-in |
| IA SD-06 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 28445 | 28535 | yes | 100 |  | only civic: Write-in |
| IA SD-08 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 35501 | 35574 | yes | 100 |  | only civic: Write-in |
| IA SD-10 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 24682 | 24709 | yes | 100 |  | only civic: Write-In |
| IA SD-12 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 32757 | 32800 | yes | 100 |  | only civic: Write-in |
| IA SD-14 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 40905 | 40964 | yes | 100 |  | only civic: Write-In |
| IA SD-16 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 30741 | 31037 | yes | 100 |  | only civic: Write-in |
| IA SD-18 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 25564 | 26330 | yes | 100 |  | only civic: Write-In |
| IA SD-20 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 31244 | 31310 | yes | 100 |  | only civic: Write-In |
| IA SD-22 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 36464 | 36528 | yes | 100 |  | only civic: Write-In |
| IA SD-24 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 33584 | 33634 | yes | 100 |  | only civic: Write-in |
| IA SD-26 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 30088 | 30136 | yes | 100 |  | only civic: Write-in |
| IA SD-28 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 30487 | 30545 | yes | 100 |  | only civic: Write-in |
| IA SD-30 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 33034 | 33092 | yes | 100 |  | only civic: Write-in |
| IA SD-32 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 34177 | 34204 | yes | 100 |  | only civic: Write-in |
| IA SD-34 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 32899 | 32935 | yes | 100 |  | only civic: Write-in |
| IA SD-36 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 30232 | 30275 | yes | 100 |  | only civic: Write-In |
| IA SD-38 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 34078 | 34116 | yes | 100 |  | only civic: Write-in |
| IA SD-40 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 32920 | 32950 | yes | 100 |  | only civic: Write-In |
| IA SD-42 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 27726 | 28518 | yes | 100 |  | only civic: Write-in |
| IA SD-44 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 28033 | 28114 | yes | 100 |  | only civic: Write-in |
| IA SD-46 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 35068 | 35106 | yes | 100 |  | only civic: Write-in |
| IA SD-48 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 30036 | 30081 | yes | 100 |  | only civic: Write-in |
| IA SD-50 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 28604 | 28654 | yes | 100 |  | only civic: Write-in |
| IA-01 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 413111 | 414078 | yes | 100 | 20/20, 20 | only civic: Write-in |
| IA-02 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 408461 | 408802 | yes | 100 | 22/22, 22 | only civic: Write-in |
| IA-03 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 411708 | 412905 | yes | 100 | 21/21, 21 | only civic: Write-in |
| IA-04 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 372697 | 373824 | yes | 100 | 36/36, 36 | only civic: Write-in |
| MA SEN | differs | 25 | 0.0012 | 0.001 | 3413329 | 3413359 | yes | 100 | 14/14, 8 |  |
| MD SEN | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 3015623 | 3015618 | yes | 100 | 24/24, 24 | only official: Christy Renee Helmondollar, Irwin William Gibbs |
| NY HD-01 | no official comparison data | | | | | | | | | |
| NY HD-02 | no official comparison data | | | | | | | | | |
| NY HD-03 | no official comparison data | | | | | | | | | |
| NY HD-04 | no official comparison data | | | | | | | | | |
| NY HD-05 | no official comparison data | | | | | | | | | |
| NY HD-06 | no official comparison data | | | | | | | | | |
| NY HD-07 | no official comparison data | | | | | | | | | |
| NY HD-08 | no official comparison data | | | | | | | | | |
| NY HD-09 | reference incomplete (MEDSL) | 28721 | 252.315 | 119.239 | 24087 | 68830 | yes | 100 |  |  |
| NY HD-10 | reference incomplete (MEDSL) | 32230 | 1140.6579 | 538.243 | 5988 | 64225 | yes | 100 |  |  |
| NY HD-11 | reference incomplete (MEDSL) | 24996 | 1762.4373 | 762.771 | 3277 | 52859 | yes | 100 |  |  |
| NY HD-12 | no official comparison data | | | | | | | | | |
| NY HD-13 | exact | 0 | 0.0 | 0.0 | 56050 | 56050 | yes | 100 |  |  |
| SC HD-50 | exact | 0 | 0.0 | 0.0 | 13512 | 13512 | yes | 100 |  |  |
| TX HD-01 | exact | 0 | 0.0 | 0.0 | 66843 | 66843 | yes | 100 |  |  |
| TX HD-02 | exact | 0 | 0.0 | 0.0 | 88404 | 88404 | yes | 100 |  |  |
| TX HD-03 | exact | 0 | 0.0 | 0.0 | 85793 | 85793 | yes | 100 |  |  |
| TX HD-04 | exact | 0 | 0.0 | 0.0 | 86527 | 86527 | yes | 100 |  |  |
| TX HD-05 | differs | 5 | 0.5525 | 0.007 | 75286 | 75291 | yes | 100 |  |  |
| TX HD-06 | exact | 0 | 0.0 | 0.0 | 78655 | 78655 | yes | 100 |  |  |
| TX HD-07 | exact | 0 | 0.0 | 0.0 | 79576 | 79576 | yes | 100 |  |  |
| TX HD-08 | exact | 0 | 0.0 | 0.0 | 74899 | 74899 | yes | 100 |  |  |
| TX HD-09 | differs | 13 | 0.0176 | 0.018 | 73993 | 74006 | yes | 100 |  |  |
| TX HD-10 | exact | 0 | 0.0 | 0.0 | 69634 | 69634 | yes | 100 |  |  |
| TX HD-100 | exact | 0 | 0.0 | 0.0 | 34119 | 34119 | yes | 100 |  |  |
| TX HD-101 | exact | 0 | 0.0 | 0.0 | 62118 | 62118 | yes | 100 |  |  |
| TX HD-102 | exact | 0 | 0.0 | 0.0 | 35788 | 35788 | yes | 100 |  |  |
| TX HD-103 | exact | 0 | 0.0 | 0.0 | 40330 | 40330 | yes | 100 |  |  |
| TX HD-104 | exact | 0 | 0.0 | 0.0 | 33295 | 33295 | yes | 100 |  |  |
| TX HD-105 | exact | 0 | 0.0 | 0.0 | 41778 | 41778 | yes | 100 |  |  |
| TX HD-106 | exact | 0 | 0.0 | 0.0 | 101322 | 101322 | yes | 100 |  |  |
| TX HD-107 | exact | 0 | 0.0 | 0.0 | 29546 | 29546 | yes | 100 |  |  |
| TX HD-108 | exact | 0 | 0.0 | 0.0 | 104534 | 104534 | yes | 100 |  |  |
| TX HD-109 | exact | 0 | 0.0 | 0.0 | 56138 | 56138 | yes | 100 |  |  |
| TX HD-11 | differs | 18 | 0.0289 | 0.029 | 62320 | 62338 | yes | 100 |  |  |
| TX HD-110 | exact | 0 | 0.0 | 0.0 | 30618 | 30618 | yes | 100 |  |  |
| TX HD-111 | exact | 0 | 0.0 | 0.0 | 53039 | 53039 | yes | 100 |  |  |
| TX HD-112 | exact | 0 | 0.0 | 0.0 | 88101 | 88101 | yes | 100 |  |  |
| TX HD-113 | exact | 0 | 0.0 | 0.0 | 59279 | 59279 | yes | 100 |  |  |
| TX HD-114 | exact | 0 | 0.0 | 0.0 | 69393 | 69393 | yes | 100 |  |  |
| TX HD-115 | exact | 0 | 0.0 | 0.0 | 69401 | 69401 | yes | 100 |  |  |
| TX HD-116 | exact | 0 | 0.0 | 0.0 | 57640 | 57640 | yes | 100 |  |  |
| TX HD-117 | exact | 0 | 0.0 | 0.0 | 69087 | 69087 | yes | 100 |  |  |
| TX HD-118 | exact | 0 | 0.0 | 0.0 | 75870 | 75870 | yes | 100 |  |  |
| TX HD-119 | exact | 0 | 0.0 | 0.0 | 59923 | 59923 | yes | 100 |  |  |
| TX HD-12 | exact | 0 | 0.0 | 0.0 | 83430 | 83430 | yes | 100 |  |  |
| TX HD-120 | exact | 0 | 0.0 | 0.0 | 38208 | 38208 | yes | 100 |  |  |
| TX HD-121 | exact | 0 | 0.0 | 0.0 | 97117 | 97117 | yes | 100 |  |  |
| TX HD-122 | exact | 0 | 0.0 | 0.0 | 110198 | 110198 | yes | 100 |  |  |
| TX HD-123 | exact | 0 | 0.0 | 0.0 | 44043 | 44043 | yes | 100 |  |  |
| TX HD-124 | exact | 0 | 0.0 | 0.0 | 49326 | 49326 | yes | 100 |  |  |
| TX HD-125 | exact | 0 | 0.0 | 0.0 | 48251 | 48251 | yes | 100 |  |  |
| TX HD-126 | exact | 0 | 0.0 | 0.0 | 60365 | 60365 | yes | 100 |  |  |
| TX HD-127 | exact | 0 | 0.0 | 0.0 | 90980 | 90980 | yes | 100 |  |  |
| TX HD-128 | exact | 0 | 0.0 | 0.0 | 66485 | 66485 | yes | 100 |  |  |
| TX HD-129 | exact | 0 | 0.0 | 0.0 | 86177 | 86177 | yes | 100 |  |  |
| TX HD-13 | differs | 9 | 0.0163 | 0.012 | 72629 | 72618 | yes | 100 |  |  |
| TX HD-130 | exact | 0 | 0.0 | 0.0 | 91941 | 91941 | yes | 100 |  |  |
| TX HD-131 | exact | 0 | 0.0 | 0.0 | 36948 | 36948 | yes | 100 |  |  |
| TX HD-132 | exact | 0 | 0.0 | 0.0 | 91774 | 91774 | yes | 100 |  |  |
| TX HD-133 | exact | 0 | 0.0 | 0.0 | 54283 | 54283 | yes | 100 |  |  |
| TX HD-134 | exact | 0 | 0.0 | 0.0 | 99517 | 99517 | yes | 100 |  |  |
| TX HD-135 | exact | 0 | 0.0 | 0.0 | 43114 | 43114 | yes | 100 |  |  |
| TX HD-136 | exact | 0 | 0.0 | 0.0 | 72850 | 72850 | yes | 100 |  |  |
| TX HD-137 | exact | 0 | 0.0 | 0.0 | 25274 | 25274 | yes | 100 |  |  |
| TX HD-138 | exact | 0 | 0.0 | 0.0 | 73693 | 73693 | yes | 100 |  |  |
| TX HD-139 | exact | 0 | 0.0 | 0.0 | 46196 | 46196 | yes | 100 |  |  |
| TX HD-14 | exact | 0 | 0.0 | 0.0 | 66594 | 66594 | yes | 100 |  |  |
| TX HD-140 | exact | 0 | 0.0 | 0.0 | 22272 | 22272 | yes | 100 |  |  |
| TX HD-141 | exact | 0 | 0.0 | 0.0 | 32492 | 32492 | yes | 100 |  |  |
| TX HD-142 | exact | 0 | 0.0 | 0.0 | 41430 | 41430 | yes | 100 |  |  |
| TX HD-143 | exact | 0 | 0.0 | 0.0 | 27796 | 27796 | yes | 100 |  |  |
| TX HD-144 | exact | 0 | 0.0 | 0.0 | 26617 | 26617 | yes | 100 |  |  |
| TX HD-145 | exact | 0 | 0.0 | 0.0 | 46104 | 46104 | yes | 100 |  |  |
| TX HD-146 | exact | 0 | 0.0 | 0.0 | 55122 | 55122 | yes | 100 |  |  |
| TX HD-147 | exact | 0 | 0.0 | 0.0 | 64160 | 64160 | yes | 100 |  |  |
| TX HD-148 | exact | 0 | 0.0 | 0.0 | 51587 | 51587 | yes | 100 |  |  |
| TX HD-149 | exact | 0 | 0.0 | 0.0 | 47212 | 47212 | yes | 100 |  |  |
| TX HD-15 | exact | 0 | 0.0 | 0.0 | 73720 | 73720 | yes | 100 |  |  |
| TX HD-150 | exact | 0 | 0.0 | 0.0 | 80181 | 80181 | yes | 100 |  |  |
| TX HD-16 | exact | 0 | 0.0 | 0.0 | 91315 | 91315 | yes | 100 |  |  |
| TX HD-17 | exact | 0 | 0.0 | 0.0 | 80920 | 80920 | yes | 100 |  |  |
| TX HD-18 | exact | 0 | 0.0 | 0.0 | 80075 | 80075 | yes | 100 |  |  |
| TX HD-19 | differs | 1 | 0.0011 | 0.001 | 123379 | 123380 | yes | 100 |  |  |
| TX HD-20 | exact | 0 | 0.0 | 0.0 | 107234 | 107234 | yes | 100 |  |  |
| TX HD-21 | exact | 0 | 0.0 | 0.0 | 66398 | 66398 | yes | 100 |  |  |
| TX HD-22 | exact | 0 | 0.0 | 0.0 | 34336 | 34336 | yes | 100 |  |  |
| TX HD-23 | differs | 9 | 0.0187 | 0.011 | 80507 | 80521 | yes | 100 |  |  |
| TX HD-24 | exact | 0 | 0.0 | 0.0 | 78761 | 78761 | yes | 100 |  |  |
| TX HD-25 | exact | 0 | 0.0 | 0.0 | 77001 | 77001 | yes | 100 |  |  |
| TX HD-26 | exact | 0 | 0.0 | 0.0 | 82066 | 82066 | yes | 100 |  |  |
| TX HD-27 | exact | 0 | 0.0 | 0.0 | 82502 | 82502 | yes | 100 |  |  |
| TX HD-28 | differs | 300 | 0.5273 | 0.319 | 93948 | 93648 | yes | 100 |  |  |
| TX HD-29 | exact | 0 | 0.0 | 0.0 | 80715 | 80715 | yes | 100 |  |  |
| TX HD-30 | differs | 5 | 0.0292 | 0.007 | 74291 | 74300 | yes | 100 |  |  |
| TX HD-31 | differs | 11 | 0.0217 | 0.022 | 50642 | 50653 | yes | 100 |  |  |
| TX HD-32 | exact | 0 | 0.0 | 0.0 | 78747 | 78747 | yes | 100 |  |  |
| TX HD-33 | exact | 0 | 0.0 | 0.0 | 70996 | 70996 | yes | 100 |  |  |
| TX HD-34 | exact | 0 | 0.0 | 0.0 | 51566 | 51566 | yes | 100 |  |  |
| TX HD-35 | exact | 0 | 0.0 | 0.0 | 25896 | 25896 | yes | 100 |  |  |
| TX HD-36 | exact | 0 | 0.0 | 0.0 | 32483 | 32483 | yes | 100 |  |  |
| TX HD-37 | exact | 0 | 0.0 | 0.0 | 55604 | 55604 | yes | 100 |  |  |
| TX HD-38 | exact | 0 | 0.0 | 0.0 | 33944 | 33944 | yes | 100 |  |  |
| TX HD-39 | exact | 0 | 0.0 | 0.0 | 44270 | 44270 | yes | 100 |  |  |
| TX HD-40 | exact | 0 | 0.0 | 0.0 | 34671 | 34671 | yes | 100 |  |  |
| TX HD-41 | reference artifact (MEDSL names / same surname) | 26618 | 46.5293 | 46.529 | 57207 | 57207 | yes | 100 |  | only civic: John Robert Guerra |
| TX HD-42 | differs | 38 | 0.0984 | 0.098 | 38622 | 38584 | yes | 100 |  |  |
| TX HD-43 | differs | 5 | 0.0229 | 0.008 | 65663 | 65654 | yes | 100 |  |  |
| TX HD-44 | exact | 0 | 0.0 | 0.0 | 88246 | 88246 | yes | 100 |  |  |
| TX HD-45 | exact | 0 | 0.0 | 0.0 | 93224 | 93224 | yes | 100 |  |  |
| TX HD-46 | differs | 2 | 0.0033 | 0.002 | 83053 | 83055 | yes | 100 |  |  |
| TX HD-47 | differs | 3 | 0.0051 | 0.003 | 98078 | 98082 | yes | 100 |  |  |
| TX HD-48 | exact | 0 | 0.0 | 0.0 | 87502 | 87502 | yes | 100 |  |  |
| TX HD-49 | differs | 8 | 0.0099 | 0.01 | 80490 | 80498 | yes | 100 |  |  |
| TX HD-50 | differs | 1 | 0.0021 | 0.002 | 48288 | 48289 | yes | 100 |  |  |
| TX HD-51 | exact | 0 | 0.0 | 0.0 | 52801 | 52801 | yes | 100 |  |  |
| TX HD-52 | exact | 0 | 0.0 | 0.0 | 111714 | 111714 | yes | 100 |  |  |
| TX HD-53 | exact | 0 | 0.0 | 0.0 | 99464 | 99464 | yes | 100 |  |  |
| TX HD-54 | exact | 0 | 0.0 | 0.0 | 56519 | 56519 | yes | 100 |  |  |
| TX HD-55 | exact | 0 | 0.0 | 0.0 | 68724 | 68724 | yes | 100 |  |  |
| TX HD-56 | no official comparison data | | | | | | | | | |
| TX HD-56 (special) | no official comparison data | | | | | | | | | |
| TX HD-57 | exact | 0 | 0.0 | 0.0 | 89014 | 89014 | yes | 100 |  |  |
| TX HD-58 | exact | 0 | 0.0 | 0.0 | 77695 | 77695 | yes | 100 |  |  |
| TX HD-59 | exact | 0 | 0.0 | 0.0 | 79514 | 79514 | yes | 100 |  |  |
| TX HD-60 | exact | 0 | 0.0 | 0.0 | 93326 | 93326 | yes | 100 |  |  |
| TX HD-61 | exact | 0 | 0.0 | 0.0 | 98145 | 98145 | yes | 100 |  |  |
| TX HD-62 | exact | 0 | 0.0 | 0.0 | 86302 | 86302 | yes | 100 |  |  |
| TX HD-63 | exact | 0 | 0.0 | 0.0 | 84187 | 84187 | yes | 100 |  |  |
| TX HD-64 | exact | 0 | 0.0 | 0.0 | 94328 | 94328 | yes | 100 |  |  |
| TX HD-65 | exact | 0 | 0.0 | 0.0 | 99970 | 99970 | yes | 100 |  |  |
| TX HD-66 | exact | 0 | 0.0 | 0.0 | 95392 | 95392 | yes | 100 |  |  |
| TX HD-67 | exact | 0 | 0.0 | 0.0 | 93158 | 93158 | yes | 100 |  |  |
| TX HD-68 | differs | 2 | 0.0085 | 0.002 | 91256 | 91259 | yes | 100 |  |  |
| TX HD-69 | differs | 6 | 0.0276 | 0.009 | 68091 | 68101 | yes | 100 |  |  |
| TX HD-70 | exact | 0 | 0.0 | 0.0 | 73116 | 73116 | yes | 100 |  |  |
| TX HD-71 | exact | 0 | 0.0 | 0.0 | 72091 | 72091 | yes | 100 |  |  |
| TX HD-72 | exact | 0 | 0.0 | 0.0 | 57821 | 57821 | yes | 100 |  |  |
| TX HD-73 | exact | 0 | 0.0 | 0.0 | 128610 | 128610 | yes | 100 |  |  |
| TX HD-74 | differs | 11 | 0.0417 | 0.02 | 54564 | 54581 | yes | 100 |  |  |
| TX HD-75 | exact | 0 | 0.0 | 0.0 | 35033 | 35033 | yes | 100 |  |  |
| TX HD-76 | exact | 0 | 0.0 | 0.0 | 70385 | 70385 | yes | 100 |  |  |
| TX HD-77 | exact | 0 | 0.0 | 0.0 | 35427 | 35427 | yes | 100 |  |  |
| TX HD-78 | exact | 0 | 0.0 | 0.0 | 45474 | 45474 | yes | 100 |  |  |
| TX HD-79 | exact | 0 | 0.0 | 0.0 | 41652 | 41652 | yes | 100 |  |  |
| TX HD-80 | differs | 8 | 0.0283 | 0.015 | 52399 | 52413 | yes | 100 |  |  |
| TX HD-81 | differs | 44 | 0.1059 | 0.106 | 41552 | 41508 | yes | 100 |  |  |
| TX HD-82 | exact | 0 | 0.0 | 0.0 | 61101 | 61101 | yes | 100 |  |  |
| TX HD-83 | differs | 5 | 0.0072 | 0.007 | 69894 | 69899 | yes | 100 |  |  |
| TX HD-84 | exact | 0 | 0.0 | 0.0 | 57754 | 57754 | yes | 100 |  |  |
| TX HD-85 | exact | 0 | 0.0 | 0.0 | 75040 | 75040 | yes | 100 |  |  |
| TX HD-86 | exact | 0 | 0.0 | 0.0 | 68942 | 68942 | yes | 100 |  |  |
| TX HD-87 | differs | 12 | 27.907 | 0.022 | 53408 | 53420 | yes | 100 |  |  |
| TX HD-88 | exact | 0 | 0.0 | 0.0 | 54093 | 54093 | yes | 100 |  |  |
| TX HD-89 | exact | 0 | 0.0 | 0.0 | 92192 | 92192 | yes | 100 |  |  |
| TX HD-90 | exact | 0 | 0.0 | 0.0 | 35674 | 35674 | yes | 100 |  |  |
| TX HD-91 | exact | 0 | 0.0 | 0.0 | 53970 | 53970 | yes | 100 |  |  |
| TX HD-92 | exact | 0 | 0.0 | 0.0 | 35274 | 35274 | yes | 100 |  |  |
| TX HD-93 | exact | 0 | 0.0 | 0.0 | 88403 | 88403 | yes | 100 |  |  |
| TX HD-94 | exact | 0 | 0.0 | 0.0 | 78722 | 78722 | yes | 100 |  |  |
| TX HD-95 | exact | 0 | 0.0 | 0.0 | 43827 | 43827 | yes | 100 |  |  |
| TX HD-96 | exact | 0 | 0.0 | 0.0 | 85090 | 85090 | yes | 100 |  |  |
| TX HD-97 | exact | 0 | 0.0 | 0.0 | 88564 | 88564 | yes | 100 |  |  |
| TX HD-98 | exact | 0 | 0.0 | 0.0 | 98678 | 98678 | yes | 100 |  |  |
| TX HD-99 | exact | 0 | 0.0 | 0.0 | 75941 | 75941 | yes | 100 |  |  |
| TX RRC | differs | 2405 | 59.2219 | 0.022 | 10967196 | 10965115 | yes | 100 | 254/254, 79 |  |
| TX SD-06 | exact | 0 | 0.0 | 0.0 | 189293 | 189293 | yes | 100 |  |  |
| TX SD-07 | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 396589 | 396589 | NO | 100 |  | only official: Bettencort; only civic: Paul Bettencourt |
| TX SD-08 | exact | 0 | 0.0 | 0.0 | 454385 | 454385 | yes | 100 |  |  |
| TX SD-10 | differs | 2 | 0.0009 | 0.001 | 368635 | 368638 | yes | 100 |  |  |
| TX SD-12 | exact | 0 | 0.0 | 0.0 | 452609 | 452609 | yes | 100 |  |  |
| TX SD-14 | differs | 14 | 0.0044 | 0.004 | 321021 | 321035 | yes | 100 |  |  |
| TX SD-15 | exact | 0 | 0.0 | 0.0 | 324195 | 324195 | yes | 100 |  |  |
| TX SD-16 | exact | 0 | 0.0 | 0.0 | 187557 | 187557 | yes | 100 |  |  |
| TX SD-17 | differs | 4 | 0.0017 | 0.001 | 371450 | 371455 | yes | 100 |  |  |
| TX SD-20 | exact | 0 | 0.0 | 0.0 | 178987 | 178987 | yes | 100 |  |  |
| TX SD-23 | exact | 0 | 0.0 | 0.0 | 253413 | 253413 | yes | 100 |  |  |
| TX SD-25 | differs | 1 | 0.0003 | 0.0 | 505964 | 505965 | yes | 100 |  |  |
| TX SD-27 | differs | 8 | 0.0168 | 0.003 | 255348 | 255334 | yes | 100 |  |  |
| TX SD-29 | differs | 9 | 0.0052 | 0.005 | 174722 | 174731 | yes | 100 |  |  |
| TX SD-30 | exact | 0 | 0.0 | 0.0 | 445930 | 445930 | yes | 100 |  |  |
| TX SEN | differs | 930 | 95.2869 | 0.008 | 11290233 | 11291854 | yes | 100 | 254/254, 84 |  |
| TX-01 | differs | 19 | 0.0073 | 0.007 | 258504 | 258523 | yes | 100 |  |  |
| TX-02 | exact | 0 | 0.0 | 0.0 | 326883 | 326883 | yes | 100 |  |  |
| TX-03 | exact | 0 | 0.0 | 0.0 | 380747 | 380747 | yes | 100 | 2/2, 0 |  |
| TX-04 | exact | 0 | 0.0 | 0.0 | 353299 | 353299 | yes | 100 |  |  |
| TX-05 | exact | 0 | 0.0 | 0.0 | 299897 | 299897 | yes | 100 | 6/6, 0 |  |
| TX-06 | differs | 2 | 0.002 | 0.001 | 286442 | 286438 | yes | 100 |  |  |
| TX-07 | exact | 0 | 0.0 | 0.0 | 244471 | 244471 | yes | 100 |  |  |
| TX-08 | exact | 0 | 0.0 | 0.0 | 342177 | 342177 | yes | 100 |  |  |
| TX-09 | exact | 0 | 0.0 | 0.0 | 184141 | 184141 | yes | 100 | 3/3, 0 |  |
| TX-10 | differs | 3 | 0.0017 | 0.001 | 347813 | 347818 | yes | 100 |  |  |
| TX-11 | differs | 41 | 0.0193 | 0.019 | 212016 | 211975 | yes | 100 | 20/20, 2 |  |
| TX-12 | exact | 0 | 0.0 | 0.0 | 339718 | 339718 | yes | 100 |  |  |
| TX-13 | differs | 3 | 0.0012 | 0.001 | 240619 | 240622 | yes | 100 |  |  |
| TX-14 | exact | 0 | 0.0 | 0.0 | 306195 | 306195 | yes | 100 |  |  |
| TX-15 | exact | 0 | 0.0 | 0.0 | 223769 | 223769 | yes | 100 |  |  |
| TX-16 | differs | 221 | 58.6207 | 0.1 | 221049 | 220828 | yes | 100 |  |  |
| TX-17 | differs | 6 | 0.0051 | 0.002 | 291031 | 291042 | yes | 100 |  |  |
| TX TX-18 | no official comparison data | | | | | | | | | |
| TX TX-18 (special) | no official comparison data | | | | | | | | | |
| TX-19 | differs | 89 | 0.0414 | 0.033 | 266462 | 266375 | yes | 100 |  |  |
| TX-20 | exact | 0 | 0.0 | 0.0 | 157890 | 157890 | yes | 100 | 0/1, 0 |  |
| TX-21 | differs | 1 | 0.0007 | 0.0 | 426422 | 426423 | yes | 100 |  |  |
| TX-22 | exact | 0 | 0.0 | 0.0 | 336889 | 336889 | yes | 100 |  |  |
| TX-23 | differs | 18 | 0.0146 | 0.006 | 290059 | 290093 | yes | 100 |  |  |
| TX-24 | exact | 0 | 0.0 | 0.0 | 376626 | 376626 | yes | 100 |  |  |
| TX-25 | differs | 1298 | 43.8662 | 0.488 | 265992 | 264703 | yes | 100 |  |  |
| TX-26 | exact | 0 | 0.0 | 0.0 | 388427 | 388427 | yes | 100 |  |  |
| TX-27 | differs | 10 | 0.0054 | 0.004 | 278565 | 278576 | yes | 100 |  |  |
| TX-28 | differs | 35 | 0.0279 | 0.015 | 237648 | 237607 | yes | 100 |  |  |
| TX-29 | exact | 0 | 0.0 | 0.0 | 152209 | 152209 | yes | 100 |  |  |
| TX-30 | exact | 0 | 0.0 | 0.0 | 232825 | 232825 | yes | 100 |  |  |
| TX-31 | exact | 0 | 0.0 | 0.0 | 355557 | 355557 | yes | 100 | 6/6, 0 |  |
| TX-32 | exact | 0 | 0.0 | 0.0 | 232464 | 232464 | yes | 100 | 3/3, 0 |  |
| TX-33 | exact | 0 | 0.0 | 0.0 | 166153 | 166153 | yes | 100 |  |  |
| TX-34 | differs | 6 | 0.0058 | 0.003 | 200392 | 200383 | yes | 100 |  |  |
| TX-35 | differs | 2 | 0.0024 | 0.001 | 253116 | 253119 | yes | 100 |  |  |
| TX-36 | differs | 11 | 0.0053 | 0.004 | 297026 | 297018 | yes | 100 |  |  |
| TX-37 | differs | 10 | 0.004 | 0.003 | 340845 | 340857 | yes | 100 |  |  |
| TX-38 | exact | 0 | 0.0 | 0.0 | 342764 | 342764 | yes | 100 | 0/1, 0 |  |
| VT AG | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 346668 | 347158 | yes | 100 | 14/14, 14 | only civic: Write-In |
| VT AUD | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 341261 | 341793 | yes | 100 | 14/14, 14 | only civic: Write-In |
| VT GOV | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 361950 | 362841 | yes | 100 | 14/14, 14 | only civic: Write-In |
| VT LTGOV | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 351401 | 352414 | yes | 100 | 14/14, 14 | only civic: Write-In |
| VT SEN | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 362855 | 363249 | yes | 100 | 14/14, 14 | only civic: Write-In |
| VT SOS | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 344051 | 344524 | yes | 100 | 14/14, 14 | only civic: Write-In |
| VT TREAS | exact (votes); write-in/label only | 0 | 0.0 | 0.0 | 346897 | 347312 | yes | 100 | 14/14, 14 | only civic: Write-In |

## NC 2026-03-03 primary (nc_sbe ENR)

- Races listed: 81; compared: 81; exact (every candidate): 81; votes exact but write-in/label differs: 0; differ: 0; reference incomplete: 0; not comparable: 0
- Over usable comparisons: max abs diff 0; median 0; max % 0.0; median % 0.0; max race-% 0.0
- Winners agree: 81/81
- County detail: 0 races, 0 county pairs compared, 0 mismatched; counties missing in civicAPI: 0

| race | result | max abs | max % | race % | official total | civic total | winners | %rep | counties (both/off, mism) | names/notes |
|---|---|---|---|---|---|---|---|---|---|---|
| State House 1 Republican | exact | 0 | 0.0 | 0.0 | 9671 | 9671 | yes | 100 |  |  |
| State House 43 Democratic | exact | 0 | 0.0 | 0.0 | 5838 | 5838 | yes | 100 |  |  |
| State House 110 Republican | exact | 0 | 0.0 | 0.0 | 6683 | 6683 | yes | 100 |  |  |
| US House 7 Republican | exact | 0 | 0.0 | 0.0 | 54139 | 54139 | yes | 100 |  |  |
| State Senate 12 Republican | exact | 0 | 0.0 | 0.0 | 10412 | 10412 | yes | 100 |  |  |
| State Senate 22 Democratic | exact | 0 | 0.0 | 0.0 | 34707 | 34707 | yes | 100 |  |  |
| State House 22 Republican | exact | 0 | 0.0 | 0.0 | 9264 | 9264 | yes | 100 |  |  |
| State House 59 Democratic | exact | 0 | 0.0 | 0.0 | 7845 | 7845 | yes | 100 |  |  |
| State House 45 Democratic | exact | 0 | 0.0 | 0.0 | 5620 | 5620 | yes | 100 |  |  |
| US House 12 Democratic | exact | 0 | 0.0 | 0.0 | 69219 | 69219 | yes | 100 |  |  |
| State Senate 9 Republican | exact | 0 | 0.0 | 0.0 | 19863 | 19863 | yes | 100 |  |  |
| State House 4 Republican | exact | 0 | 0.0 | 0.0 | 6721 | 6721 | yes | 100 |  |  |
| State House 32 Republican | exact | 0 | 0.0 | 0.0 | 5649 | 5649 | yes | 100 |  |  |
| State House 25 Democratic | exact | 0 | 0.0 | 0.0 | 8698 | 8698 | yes | 100 |  |  |
| State House 65 Republican | exact | 0 | 0.0 | 0.0 | 13334 | 13334 | yes | 100 |  |  |
| State Senate 5 Republican | exact | 0 | 0.0 | 0.0 | 6456 | 6456 | yes | 100 |  |  |
| State House 23 Democratic | exact | 0 | 0.0 | 0.0 | 9392 | 9392 | yes | 100 |  |  |
| State House 37 Democratic | exact | 0 | 0.0 | 0.0 | 9376 | 9376 | yes | 100 |  |  |
| State House 89 Republican | exact | 0 | 0.0 | 0.0 | 9308 | 9308 | yes | 100 |  |  |
| US Senate Republican | exact | 0 | 0.0 | 0.0 | 627404 | 627404 | yes | 100 |  | name: Donald M. (Don) Brown → Don Brown; Elizabeth A. Temple → Elizabeth Temple |
| US House 5 Democratic | exact | 0 | 0.0 | 0.0 | 45240 | 45240 | yes | 100 |  |  |
| US House 10 Democratic | exact | 0 | 0.0 | 0.0 | 41161 | 41161 | yes | 100 |  |  |
| State Senate 21 Republican | exact | 0 | 0.0 | 0.0 | 15778 | 15778 | yes | 100 |  |  |
| State Senate 10 Republican | exact | 0 | 0.0 | 0.0 | 17564 | 17564 | yes | 100 |  |  |
| State House 67 Democratic | exact | 0 | 0.0 | 0.0 | 2828 | 2828 | yes | 100 |  |  |
| US House 13 Republican | exact | 0 | 0.0 | 0.0 | 48973 | 48973 | yes | 100 |  |  |
| US House 11 Democratic | exact | 0 | 0.0 | 0.0 | 76084 | 76084 | yes | 100 |  |  |
| State Senate 26 Republican | exact | 0 | 0.0 | 0.0 | 26247 | 26247 | yes | 100 |  | name: Philip E. (Phil) Berger → Philip E. Berger |
| State House 38 Democratic | exact | 0 | 0.0 | 0.0 | 10652 | 10652 | yes | 100 |  |  |
| State House 90 Republican | exact | 0 | 0.0 | 0.0 | 14368 | 14368 | yes | 100 |  |  |
| State House 78 Republican | exact | 0 | 0.0 | 0.0 | 11736 | 11736 | yes | 100 |  |  |
| US House 14 Republican | exact | 0 | 0.0 | 0.0 | 51244 | 51244 | yes | 100 |  |  |
| State House 6 Democratic | exact | 0 | 0.0 | 0.0 | 4126 | 4126 | yes | 100 |  |  |
| State House 117 Republican | exact | 0 | 0.0 | 0.0 | 7843 | 7843 | yes | 100 |  |  |
| State House 99 Democratic | exact | 0 | 0.0 | 0.0 | 7243 | 7243 | yes | 100 |  |  |
| US House 4 Democratic | exact | 0 | 0.0 | 0.0 | 125655 | 125655 | yes | 100 |  |  |
| State Senate 30 Republican | exact | 0 | 0.0 | 0.0 | 20396 | 20396 | yes | 100 |  |  |
| State House 119 Republican | exact | 0 | 0.0 | 0.0 | 7948 | 7948 | yes | 100 |  |  |
| State House 79 Republican | exact | 0 | 0.0 | 0.0 | 14246 | 14246 | yes | 100 |  |  |
| State Senate 1 Republican | exact | 0 | 0.0 | 0.0 | 19941 | 19941 | yes | 100 |  |  |
| State House 6 Republican | exact | 0 | 0.0 | 0.0 | 4006 | 4006 | yes | 100 |  | name: W. H. (Bill) Morris → W. H. Morris |
| State Senate 34 Republican | exact | 0 | 0.0 | 0.0 | 13971 | 13971 | yes | 100 |  |  |
| State House 48 Republican | exact | 0 | 0.0 | 0.0 | 3548 | 3548 | yes | 100 |  |  |
| US House 5 Republican | exact | 0 | 0.0 | 0.0 | 71944 | 71944 | yes | 100 |  | name: Joseph (Joey) Osborne → Joseph Osborne; Roman H. (Chad) Williams II → Roman H. Williams II |
| State House 105 Republican | exact | 0 | 0.0 | 0.0 | 5172 | 5172 | yes | 100 |  |  |
| State House 35 Republican | exact | 0 | 0.0 | 0.0 | 5547 | 5547 | yes | 100 |  |  |
| State House 26 Republican | exact | 0 | 0.0 | 0.0 | 6835 | 6835 | yes | 100 |  |  |
| State House 16 Republican | exact | 0 | 0.0 | 0.0 | 8342 | 8342 | yes | 100 |  |  |
| State House 51 Republican | exact | 0 | 0.0 | 0.0 | 5548 | 5548 | yes | 100 |  |  |
| State House 28 Republican | exact | 0 | 0.0 | 0.0 | 7918 | 7918 | yes | 100 |  |  |
| State Senate 37 Democratic | exact | 0 | 0.0 | 0.0 | 9016 | 9016 | yes | 100 |  |  |
| US House 10 Republican | exact | 0 | 0.0 | 0.0 | 52779 | 52779 | yes | 100 |  |  |
| US House 6 Democratic | exact | 0 | 0.0 | 0.0 | 39007 | 39007 | yes | 100 |  |  |
| State Senate 12 Democratic | exact | 0 | 0.0 | 0.0 | 9614 | 9614 | yes | 100 |  |  |
| State House 17 Republican | exact | 0 | 0.0 | 0.0 | 11273 | 11273 | yes | 100 |  |  |
| State House 73 Democratic | exact | 0 | 0.0 |  | 0 | 0 | yes | 100 |  | name: Kim DeLaney - DECEASED → Kim DeLaney |
| State House 55 Republican | exact | 0 | 0.0 | 0.0 | 5703 | 5703 | yes | 100 |  |  |
| US House 14 Democratic | exact | 0 | 0.0 | 0.0 | 39515 | 39515 | yes | 100 |  |  |
| US House 11 Republican | exact | 0 | 0.0 | 0.0 | 68184 | 68184 | yes | 100 |  |  |
| US House 8 Democratic | exact | 0 | 0.0 | 0.0 | 38132 | 38132 | yes | 100 |  |  |
| State Senate 17 Republican | exact | 0 | 0.0 | 0.0 | 4807 | 4807 | yes | 100 |  |  |
| State House 32 Democratic | exact | 0 | 0.0 | 0.0 | 7960 | 7960 | yes | 100 |  |  |
| State House 95 Republican | exact | 0 | 0.0 | 0.0 | 5347 | 5347 | yes | 100 |  | name: Richard (Todd) Carver → Richard Carver |
| US House 12 Republican | exact | 0 | 0.0 | 0.0 | 9752 | 9752 | yes | 100 |  |  |
| US Senate Democratic | exact | 0 | 0.0 | 0.0 | 827755 | 827755 | yes | 100 |  |  |
| State House 118 Republican | exact | 0 | 0.0 | 0.0 | 9075 | 9075 | yes | 100 |  | name: James M. (Jimmy) Rogers → James M. Rogers |
| US House 9 Democratic | exact | 0 | 0.0 | 0.0 | 44221 | 44221 | yes | 100 |  |  |
| State Senate 32 Democratic | exact | 0 | 0.0 | 0.0 | 20104 | 20104 | yes | 100 |  |  |
| State Senate 18 Republican | exact | 0 | 0.0 | 0.0 | 12608 | 12608 | yes | 100 |  |  |
| US House 1 Republican | exact | 0 | 0.0 | 0.0 | 67376 | 67376 | yes | 100 |  |  |
| State House 80 Republican | exact | 0 | 0.0 | 0.0 | 7280 | 7280 | yes | 100 |  |  |
| US House 13 Democratic | exact | 0 | 0.0 | 0.0 | 50337 | 50337 | yes | 100 |  |  |
| State House 9 Democratic | exact | 0 | 0.0 | 0.0 | 7137 | 7137 | yes | 100 |  |  |
| State House 50 Democratic | exact | 0 | 0.0 | 0.0 | 13767 | 13767 | yes | 100 |  |  |
| State House 60 Democratic | exact | 0 | 0.0 | 0.0 | 6970 | 6970 | yes | 100 |  | name: Joseph (Joe) Alston → Joseph Alston |
| State House 106 Democratic | exact | 0 | 0.0 | 0.0 | 11047 | 11047 | yes | 100 |  |  |
| State House 27 Democratic | exact | 0 | 0.0 | 0.0 | 13581 | 13581 | yes | 100 |  |  |
| State House 43 Republican | exact | 0 | 0.0 | 0.0 | 4029 | 4029 | yes | 100 |  |  |
| State House 81 Republican | exact | 0 | 0.0 | 0.0 | 8336 | 8336 | yes | 100 |  |  |
| State House 113 Republican | exact | 0 | 0.0 | 0.0 | 12026 | 12026 | yes | 100 |  |  |
| US House 3 Democratic | exact | 0 | 0.0 | 0.0 | 41572 | 41572 | yes | 100 |  |  |
