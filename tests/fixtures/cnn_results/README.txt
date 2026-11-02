CNN results fixtures captured from the public per-race results JSON feed.

2024 general election (called races, final numbers), fetched 2026-10-27 16:05 UTC:

- 2024-SG-TX.json: https://politics.api.cnn.io/results/race/2024-SG-TX.json
- 2024-HG-CA-12.json: https://politics.api.cnn.io/results/race/2024-HG-CA-12.json

2026 general election pre-election placeholders (zero votes, polls not open),
fetched 2026-11-01 04:12 UTC:

- 2026-SG-TX.json: https://politics.api.cnn.io/results/race/2026-SG-TX.json
- 2026-SG-GA.json: https://politics.api.cnn.io/results/race/2026-SG-GA.json

The 2026 files use the same field names as the 2024 files: `ecKey`, `candidates[]`
(`fullName`, `majorParty`, `voteNum`, `votePercentNum`, `isIncumbent`), `percentReporting`,
`totalVote` and `raceEmbargo.callsRelease`. They have no `voteTimestamp` key (the 2024
files do), and `extractedAt` stops at 2026-10-30. In these captures `percentReporting` and
`totalVote` are 0, every `voteNum` is 0 and `winner` is false; `editorialStatus` is
`scheduled` and `status` is `U`.

Trimmed: the captures keep the fields the parser reads plus the identifying ones
(`stateAbbreviation`, `stateName`, `stateFipsCode`, `contestType`, `raceType`, `status`,
`editorialStatus`, `electionTitle`, `electionType`, `raceTitle`, `extractedAt`,
`percentReporting`, `totalVote`, `raceEmbargo`, `version`) and the per-candidate
`candidatePartyCode`, `partyName`, `firstName`, `lastName` and `votePercentStr`. Dropped:
`cid`, `candidateId`, `lastNameSlug`, `middleName`, `onAir`, `runoffCandidate`,
`countyVotesAvailable`, `externalKey`, `jurisdictionCode`, `raceShortTitle`, `raceToWatch`,
`advanceToReallocation`, `isSwitch`, `uncontested`, `stateNameSlug` and `stateId`.
