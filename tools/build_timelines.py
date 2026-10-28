#!/usr/bin/env python3
"""Synthesise election-night timelines from final county results.

    python tools/build_timelines.py [DATA_DIR] [--years 2022] [--states PA] [--levels senate,house]

Reads <data>/<year>/<ST>/<level>.csv (from fetch_history.py) and writes
<data>/timelines/<year>/<KEY>.jsonl for every race that has no real series yet. Each county
releases its votes in three batches -- early vote, election day, mail -- in the state's count
order (electionwatch/faq/states.yaml `count_order` if present, else early -> election day ->
mail), spread over ~6 h after polls close with seeded jitter. Batches lean the way they usually
do (mail D, election day R) but every county still ends on its exact certified total.
Every line carries "synthetic": true. Real (NYT) files are never overwritten.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from electionwatch.racekeys import RaceKeyError, parse  # noqa: E402

DEFAULT_DATA = Path.home() / "Downloads" / "election-test-data"
ELECTION_DAY = {
    "2014": "2014-11-04",
    "2016": "2016-11-08",
    "2018": "2018-11-06",
    "2020": "2020-11-03",
    "2022": "2022-11-08",
    "2024": "2024-11-05",
}
BATCHES = ("early", "eday", "mail")
WEIGHT = {"early": 0.35, "eday": 0.45, "mail": 0.20}
LEAN = {"early": {"DEM": 1.1, "REP": 0.9}, "eday": {"DEM": 0.8, "REP": 1.2}, "mail": {"DEM": 1.4, "REP": 0.6}}
# minutes after polls close during which a batch at count-order position i is released
WINDOWS = [(0, 90), (30, 270), (150, 360)]
DEFAULT_ORDER = ["early", "eday", "mail"]


def count_order(state: str, states_yaml: Path | None = None) -> list[str]:
    """Order batches by where 'early', 'election day' and 'mail/absentee' first appear in the
    FAQ's free-text count_order for the state; default early -> election day -> mail."""
    path = states_yaml or ROOT / "electionwatch" / "faq" / "states.yaml"
    try:
        import yaml

        text = yaml.safe_load(path.read_text())[state]["count_order"]
    except Exception:
        return list(DEFAULT_ORDER)
    text = (" ".join(map(str, text)) if isinstance(text, list) else str(text)).lower()
    pats = {"early": r"early|in-person absentee|advance", "eday": r"election[- ]day", "mail": r"mail|absentee|postal"}
    pos = {}
    for batch, pattern in pats.items():
        match = re.search(pattern, text)
        pos[batch] = match.start() if match else 10_000 + DEFAULT_ORDER.index(batch)
    return sorted(BATCHES, key=lambda batch: pos[batch])


def read_level_csv(path: Path) -> dict[str, dict[str, list[tuple[str, str, int]]]]:
    """{race_key: {county: [(candidate, party, votes)]}}"""
    races: dict = defaultdict(lambda: defaultdict(list))
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            races[row["race_key"]][row["county"]].append((row["candidate"], row["party"], int(row["votes"])))
    return races


def split_votes(votes: int, party: str, order: list[str]) -> dict[str, int]:
    """Split one candidate's county votes over the batches, exact total preserved."""
    weights = {batch: WEIGHT[batch] * LEAN[batch].get(party, 1.0) for batch in order}
    total = sum(weights.values())
    split, remaining = {}, votes
    for batch in order[:-1]:
        split[batch] = min(remaining, round(votes * weights[batch] / total))
        remaining -= split[batch]
    split[order[-1]] = remaining
    return split


def synthesise(
    key: str,
    counties: dict[str, list[tuple[str, str, int]]],
    year: str,
    order: list[str],
    close_utc: str = "01:00",
    seed: int = 0,
) -> list[dict]:
    canon = str(parse(key))
    generator = random.Random(f"{seed}:{year}:{canon}")
    day = datetime.fromisoformat(ELECTION_DAY[year]).replace(tzinfo=timezone.utc)
    hour, minute = map(int, close_utc.split(":"))
    close = day + timedelta(days=1 if hour < 12 else 0, hours=hour, minutes=minute)
    parties: dict[str, str] = {}
    final_total = 0
    events: list[tuple[datetime, dict[str, int]]] = []
    for county, candidates in sorted(counties.items()):
        per_batch: dict[str, dict[str, int]] = defaultdict(dict)
        for name, party, votes in candidates:
            parties.setdefault(name, party)
            final_total += votes
            for batch, batch_votes in split_votes(votes, party, order).items():
                per_batch[batch][name] = per_batch[batch].get(name, 0) + batch_votes
        for index, batch in enumerate(order):
            window_start, window_end = WINDOWS[index]
            snapshot_at = close + timedelta(minutes=window_start + generator.random() * (window_end - window_start))
            events.append((snapshot_at.replace(second=0, microsecond=0), per_batch[batch]))
    events.sort(key=lambda event: event[0])
    cumulative: dict[str, int] = {name: 0 for name in parties}
    lines: list[dict] = []
    for index, (snapshot_at, batch_votes) in enumerate(events):
        for name, votes in batch_votes.items():
            cumulative[name] += votes
        if index + 1 < len(events) and events[index + 1][0] == snapshot_at:
            continue  # one snapshot per minute
        running_total = sum(cumulative.values())
        if not running_total:
            continue
        lines.append(
            {
                "ts": snapshot_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "key": canon,
                "source": "synthetic",
                "synthetic": True,
                "candidates": [
                    {"name": name, "party": parties[name] or None, "votes": votes}
                    for name, votes in sorted(cumulative.items(), key=lambda pair: -pair[1])
                ],
                "total_votes": running_total,
                "expected_vote_pct": round(100.0 * running_total / final_total, 1) if final_total else None,
            }
        )
    return lines


def is_real(path: Path) -> bool:
    try:
        with path.open() as fh:
            return not json.loads(fh.readline()).get("synthetic", False)
    except (OSError, ValueError):
        return False


def run(
    data_dir: Path,
    years: list[str],
    states: list[str] | None,
    levels: list[str],
    races: list[str] | None = None,
    force: bool = False,
    seed: int = 0,
) -> int:
    written = 0
    for year in years:
        # Race keys depend on the year (Montana had one House seat until 2022), so parse
        # every key, file name included, as of that election.
        os.environ["ELECTION_DATE"] = ELECTION_DAY[year]
        want = {str(parse(race_key)) for race_key in races} if races else None
        for level_csv in sorted((data_dir / year).glob("*/*.csv")):
            state_code, level = level_csv.parent.name, level_csv.stem
            if level not in levels or (states and state_code not in states):
                continue
            order = count_order(state_code)
            for key, counties in read_level_csv(level_csv).items():
                try:
                    canon = str(parse(key))
                except RaceKeyError:
                    continue
                if want and canon not in want:
                    continue
                out_path = data_dir / "timelines" / year / (canon.replace(" ", "_") + ".jsonl")
                if out_path.exists() and (is_real(out_path) or not force):
                    continue
                lines = synthesise(canon, counties, year, order, seed=seed)
                if not lines:
                    continue
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text("".join(json.dumps(line) + "\n" for line in lines))
                written += 1
    print(f"wrote {written} synthetic timelines")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("data_dir", nargs="?", default=str(DEFAULT_DATA))
    ap.add_argument("--years", default=",".join(ELECTION_DAY))
    ap.add_argument("--states", default="", help="comma-separated USPS codes (default all present)")
    ap.add_argument("--levels", default="senate,governor,house")
    ap.add_argument("--races", default="", help='only these keys, e.g. "PA SEN,GA-06"')
    ap.add_argument("--force", action="store_true", help="rebuild existing synthetic timelines")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    years = [year.strip() for year in args.years.split(",") if year.strip() in ELECTION_DAY]
    return run(
        Path(args.data_dir).expanduser(),
        years,
        [state_code.strip().upper() for state_code in args.states.split(",") if state_code.strip()] or None,
        [level.strip() for level in args.levels.split(",")],
        [race_key.strip() for race_key in args.races.split(",") if race_key.strip()] or None,
        args.force,
        args.seed,
    )


if __name__ == "__main__":
    sys.exit(main())
