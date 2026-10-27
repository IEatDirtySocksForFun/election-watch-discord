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


def parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class Snapshot:
    ts: datetime
    result: RaceResult
    synthetic: bool


def snapshot_from_line(d: dict) -> Snapshot:
    ts = parse_ts(d["ts"])
    r = RaceResult(
        key=parse(d["key"]),
        candidates=[CandidateResult(c["name"], c.get("party"), int(c["votes"]),
                                    incumbent=bool(c.get("incumbent")))
                    for c in d.get("candidates", [])],
        source="replay", fetched_at=ts, source_updated_at=ts,
        precincts_reporting=d.get("precincts_reporting"), precincts_total=d.get("precincts_total"),
        expected_vote_pct=d.get("expected_vote_pct"), total_votes=d.get("total_votes"),
        url=d.get("url"), title=d.get("title"),
    )
    return Snapshot(ts, r.finalize(), bool(d.get("synthetic")))


def snapshot_to_line(r: RaceResult, ts: datetime, synthetic: bool) -> dict:
    return {
        "ts": ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "key": str(r.key),
        "source": r.source, "synthetic": synthetic,
        "candidates": [{"name": c.name, "party": c.party, "votes": c.votes} for c in r.candidates],
        "total_votes": r.total_votes, "expected_vote_pct": r.expected_vote_pct,
        "precincts_reporting": r.precincts_reporting, "precincts_total": r.precincts_total,
        "url": r.url, "title": r.title,
    }


def load_timeline(path: Path) -> list[Snapshot]:
    snaps = [snapshot_from_line(json.loads(line)) for line in Path(path).read_text().splitlines()
             if line.strip()]
    return sorted(snaps, key=lambda s: s.ts)


def load_year(data: Path, year: str, races: list[str] | None = None) -> dict[str, list[Snapshot]]:
    """{canonical key: snapshots} for the requested races (all files in the year if None)."""
    d = Path(data) / "timelines" / str(year)
    if races:
        paths = [d / key_filename(parse(r)) for r in races]
        missing = [str(p) for p in paths if not p.exists()]
        if missing:
            raise FileNotFoundError("no timeline for: " + ", ".join(missing)
                                    + " (run tools/fetch_history.py, then tools/build_timelines.py)")
    else:
        paths = sorted(d.glob("*.jsonl"))
    out = {}
    for p in paths:
        snaps = load_timeline(p)
        if snaps:
            out[str(snaps[0].result.key)] = snaps
    return out
