Hawaii Office of Elections summary text exports.

2026-general.txt  GET https://elections.hawaii.gov/wp-content/results/2026%20General/summary.txt
    (linked from https://elections.hawaii.gov/election-results/ under the 2026 general
    election's "Text Files")
    Fetched: 2026-11-04
    Tab-delimited: a "#FormatVersion 1" line, a "#Contest ID" header row, then one row per
    candidate. Trimmed to U.S. Representative Districts I/II, Governor and Lieutenant
    Governor, State Senate District 2, State Representative District 43 and Question 1;
    candidate names cut to four in District I.

2024-general.txt  GET https://files.hawaii.gov/elections/files/results/2024/General/summary.txt
    Fetched: 2026-10-27
    Quoted CSV under a "Format#1" line, UTF-16 LE with a byte-order mark. Trimmed to
    President, U.S. Senator, U.S. Representative Districts I/II, State Senate Districts
    3/12, State Representative Districts 3/50, Mayor, Molokai Resident Trustee and
    Questions 1/2. The live fetch returned Cloudflare 522 at the time; the fixture was
    reconstructed from a retrieved text snapshot.

The 2026 export is not served from the 2024 address: the same path under 2026 answers
HTTP 404. The Office of Elections links each election's text exports from its results
page on elections.hawaii.gov instead.
