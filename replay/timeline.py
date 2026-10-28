"""Timeline files: <data>/timelines/<year>/<KEY>.jsonl, one RaceResult-shaped snapshot per line.

    {"ts": "2024-11-06T01:12:00Z", "key": "PA SEN", "source": "nyt", "synthetic": false,
     "candidates": [{"name": "Bob Casey", "party": "DEM", "votes": 1234}, ...],
     "total_votes": 2468, "expected_vote_pct": 4, "url": "..."}

File names are the race key with spaces as underscores ("PA_SEN.jsonl", "NC-01.jsonl").
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import CandidateResult, RaceKey, RaceResult
from electionwatch.racekeys import parse


def key_filename(key: str | RaceKey) -> str:
    return str(key).replace(" ", "_") + ".jsonl"


def parse_ts(text: str) -> datetime:
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class Snapshot:
    ts: datetime
    result: RaceResult
    synthetic: bool


def snapshot_from_line(line: dict) -> Snapshot:
    ts = parse_ts(line["ts"])
    race_result = RaceResult(
        key=parse(line["key"]),
        candidates=[
            CandidateResult(
                candidate["name"],
                candidate.get("party"),
                int(candidate["votes"]),
                incumbent=bool(candidate.get("incumbent")),
            )
            for candidate in line.get("candidates", [])
        ],
        source="replay",
        fetched_at=ts,
        source_updated_at=ts,
        precincts_reporting=line.get("precincts_reporting"),
        precincts_total=line.get("precincts_total"),
        expected_vote_pct=line.get("expected_vote_pct"),
        total_votes=line.get("total_votes"),
        url=line.get("url"),
        title=line.get("title"),
    )
    return Snapshot(ts, race_result.finalize(), bool(line.get("synthetic")))


def snapshot_to_line(race_result: RaceResult, ts: datetime, synthetic: bool) -> dict:
    return {
        "ts": ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "key": str(race_result.key),
        "source": race_result.source,
        "synthetic": synthetic,
        "candidates": [
            {"name": candidate.name, "party": candidate.party, "votes": candidate.votes}
            for candidate in race_result.candidates
        ],
        "total_votes": race_result.total_votes,
        "expected_vote_pct": race_result.expected_vote_pct,
        "precincts_reporting": race_result.precincts_reporting,
        "precincts_total": race_result.precincts_total,
        "url": race_result.url,
        "title": race_result.title,
    }


def load_timeline(path: Path) -> list[Snapshot]:
    snaps = [snapshot_from_line(json.loads(line)) for line in Path(path).read_text().splitlines() if line.strip()]
    return sorted(snaps, key=lambda s: s.ts)


def load_year(data: Path, year: str, races: list[str] | None = None) -> dict[str, list[Snapshot]]:
    """{canonical key: snapshots} for the requested races (all files in the year if None)."""
    d = Path(data) / "timelines" / str(year)
    if races:
        paths = [d / key_filename(parse(r)) for r in races]
        missing = [str(p) for p in paths if not p.exists()]
        if missing:
            raise FileNotFoundError(
                "no timeline for: "
                + ", ".join(missing)
                + " (run tools/fetch_history.py, then tools/build_timelines.py)"
            )
    else:
        paths = sorted(d.glob("*.jsonl"))
    out = {}
    for p in paths:
        snaps = load_timeline(p)
        if snaps:
            out[str(snaps[0].result.key)] = snaps
    return out
