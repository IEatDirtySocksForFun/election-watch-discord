# Historical results (`tools/fetch_history.py`)

The bot's replay/replay-timeline features need past results, which no live feed serves.
`tools/fetch_history.py` builds `<data>/<year>/<state>/{senate,house,governor,state,local}.csv`
plus a `manifest.json` from public bulk data, then `tools/build_timelines.py` synthesises
timelines from the county rows. Everything is plain HTTP + `csv`; no model is involved.

    python tools/fetch_history.py <data-dir> \
        --years 2016 --states TX,MI [--no-timelines]

Sources, in the order they are tried:

| Source | What it gives | Notes |
|--------|---------------|-------|
| `MEDSL/<year>-elections-official` (GitHub) | precinct returns per state, `<year>-<st>-precinct-general.zip` | only 2018/2020/2022/2024 exist; the 2014 and 2016 repos were never published |
| Harvard Dataverse `doi:10.7910/DVN/NT66Z3` | 2020 precinct returns by state | fallback when the GitHub zip is missing |
| Harvard Dataverse `doi:10.7910/DVN/PEJ5QU` | U.S. Senate statewide 1976–2024 | one TSV, drives every `SEN` row |
| `openelections/openelections-data-<st>` (GitHub) | county/town/precinct CSVs per state | primary source for 2014/2016 and for state-legislative races |

GitHub is only listed through a blobless shallow clone (`git clone --filter=blob:none
--no-checkout --depth 1`), cached under `<data>/_cache/git/`, and files are fetched from
`raw.githubusercontent.com`. The REST contents API allows 60 unauthenticated requests an hour
per IP. The clone avoids that limit; the API remains a fallback for machines without `git`.

## Which OpenElections file is read

A repo year directory mixes partitions: one file per county, per town, per ward or a single
whole-state summary. Reading two partitions double-counts votes, reading one file of a
partition loses most of the state. So `parse_openelections_name()` ranks the trailing word
(`…__general__<geo>.csv`) into

    0-2  the whole state in one file (county summary, no geo word, precinct dump)
    3    per district      4  per town     5  per county slice     6  per precinct/ward slice

and `pick_openelections_files()` returns every file of each partition, coarsest first, skipping
a precinct partition of more than `MAX_PARTITION_FILES` files when something coarser exists.
`openelections()` aggregates each partition separately and keeps the one that reports the most
races per election date. Both directions matter in practice: Pennsylvania's whole-state county
file is complete while New Jersey's holds only the statewide offices, and West Virginia's
single county file beats its six county-precinct slices.

`csv` files that use `last,first,middle,suffix` instead of `candidate` (Michigan, some Ohio
files) are supported, and total votes are taken from `votes` or summed from `election_day`,
`absentee`, `mail`, `early`, `provisional` and `advance` columns.

## Coverage for 2014 and 2016

2014 (`--years 2014`, races = distinct race keys):

| State | Levels |
|-------|--------|
| CT | governor 5, house 5, state 111 |
| UT | governor 3, house 4, state 93, local 110 |
| WI | governor 4, state 116 |
| DC, FL, MD | nothing — see below |

2016 (17 states that were empty before):

| State | Levels |
|-------|--------|
| DC | local 288 |
| DE | governor 2, house 1, state 52 |
| MA | house 9, state 200 |
| ME | house 2, state 187 |
| MI | house 14, state 113 |
| MN | house 8, state 200, local 1417 |
| MS | house 4 (no legislative election in 2016) |
| MT | governor 5, house 1, state 125 |
| NE | house 3, local 187 (unicameral legislature is not published in the county files) |
| NJ | house 12, state 3, local 22 (the county files carry only President + U.S. House) |
| NM | governor 1, house 3, state 112 |
| RI | house 2, state 113, local 587 |
| TN | house 9, state 115 |
| TX | governor 1, house 36, state 166, local 2429 |
| WV | governor 6, house 3, state 85 |
| WY | house 1, state 75 |
| VA | nothing — see below |

## What is still missing, and why

- **2014 FL, MD**: `openelections-data-fl` only has 2018/2020/2022, `openelections-data-md`
  is a stub (LICENSE + README). MEDSL has no 2014 GitHub repo, and the 2014 precinct files on
  Dataverse are not listed publicly under a MEDSL DOI we could find.
- **2014 DC**: DC had no federal or state-legislative election in 2014 (council + mayor only);
  `openelections-data-dc` starts at 2016, so only the 2016 local rows exist.
- **2016 VA**: `openelections-data-va` is a stub too; Virginia's own site publishes per-locality
  CSV downloads but only for the current cycle.
- **2016 NE legislature**: Nebraska's unicameral, nonpartisan races are not in the per-county
  files the repo ships.
- **MEDSL 2016 precinct returns** (`doi:10.7910/DVN/NH5S2I`) and **U.S. House 1976–2024**
  (`doi:10.7910/DVN/IG0UN2`) sit behind a Harvard Dataverse guestbook form
  (`"You may not download this file without the required Guestbook response for guestbookID
  458."`). They are deliberately not automated: a human has to accept the guestbook in a
  browser, download the file and drop it into `<data>/_cache/medsl/`. Adding the 2016 file
  would fill the remaining 2016 gaps for states whose OpenElections repos are empty (VA) and
  for Nebraska's legislature.
- **No MEDSL gubernatorial county dataset exists**; gubernatorial rows come from OpenElections
  or the statewide Senate-style files only.

## Upstream data quirks worth knowing

- Utah's `20141104__ut__general__rich__precinct.csv` labels state-house district numbers as
  `US HOUSE` and `STATE SENATE` (districts 71 and 72 appear as senate races). The classifier is
  faithful to the source here, so `UT SD-71/SD-72` appear with a few dozen votes.
- Michigan writes `10th District Representative in Congress …` (district as a prefix) and
  `… Partial Term Ending 01/01/2017 …` for unexpired terms; both are parsed, the latter into a
  `S` (special) key.
- Washington publishes two positions per house district; `POSITION 1`/`POSITION 2` become the
  `A`/`B` suffix of `WA HD-nnA`/`WA HD-nnB`.
- Connecticut's New Haven precinct file abbreviates office names to `Attorney`, `Secretary` and
  `Governor` with a stray district number, and its U.S. House rows are `Congress` with `CD3`.
  All three are mapped (`CT AG`, `CT SOS`, `CT GOV`, `CT-03`); a `Congress` row with no district
  anywhere is dropped rather than invented as `CT-AL`.
