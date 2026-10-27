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
ELECTION_DAY = {"2018": "2018-11-06", "2020": "2020-11-03", "2022": "2022-11-08", "2024": "2024-11-05"}
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
    pats = {"early": r"early|in-person absentee|advance", "eday": r"election[- ]day",
            "mail": r"mail|absentee|postal"}
    pos = {}
    for b, pat in pats.items():
        m = re.search(pat, text)
        pos[b] = m.start() if m else 10_000 + DEFAULT_ORDER.index(b)
    return sorted(BATCHES, key=lambda b: pos[b])


def read_level_csv(path: Path) -> dict[str, dict[str, list[tuple[str, str, int]]]]:
    """{race_key: {county: [(candidate, party, votes)]}}"""
    races: dict = defaultdict(lambda: defaultdict(list))
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            races[r["race_key"]][r["county"]].append((r["candidate"], r["party"], int(r["votes"])))
    return races


def split_votes(votes: int, party: str, order: list[str]) -> dict[str, int]:
    """Split one candidate's county votes over the batches, exact total preserved."""
    w = {b: WEIGHT[b] * LEAN[b].get(party, 1.0) for b in order}
    tot = sum(w.values())
    out, left = {}, votes
    for b in order[:-1]:
        out[b] = min(left, round(votes * w[b] / tot))
        left -= out[b]
    out[order[-1]] = left
    return out


def synthesise(key: str, counties: dict[str, list[tuple[str, str, int]]], year: str,
               order: list[str], close_utc: str = "01:00", seed: int = 0) -> list[dict]:
    canon = str(parse(key))
    rng = random.Random(f"{seed}:{year}:{canon}")
    day = datetime.fromisoformat(ELECTION_DAY[year]).replace(tzinfo=timezone.utc)
    hh, mm = map(int, close_utc.split(":"))
    close = day + timedelta(days=1 if hh < 12 else 0, hours=hh, minutes=mm)
    parties: dict[str, str] = {}
    final_total = 0
    events: list[tuple[datetime, dict[str, int]]] = []
    for county, cands in sorted(counties.items()):
        per_batch: dict[str, dict[str, int]] = defaultdict(dict)
        for name, party, votes in cands:
            parties.setdefault(name, party)
            final_total += votes
            for b, v in split_votes(votes, party, order).items():
                per_batch[b][name] = per_batch[b].get(name, 0) + v
        for i, b in enumerate(order):
            lo, hi = WINDOWS[i]
            at = close + timedelta(minutes=lo + rng.random() * (hi - lo))
            events.append((at.replace(second=0, microsecond=0), per_batch[b]))
    events.sort(key=lambda e: e[0])
    cum: dict[str, int] = {n: 0 for n in parties}
    lines: list[dict] = []
    for i, (at, add) in enumerate(events):
        for n, v in add.items():
            cum[n] += v
        if i + 1 < len(events) and events[i + 1][0] == at:
            continue            # one snapshot per minute
        tot = sum(cum.values())
        if not tot:
            continue
        lines.append({
            "ts": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "key": canon, "source": "synthetic", "synthetic": True,
            "candidates": [{"name": n, "party": parties[n] or None, "votes": v}
                           for n, v in sorted(cum.items(), key=lambda kv: -kv[1])],
            "total_votes": tot,
            "expected_vote_pct": round(100.0 * tot / final_total, 1) if final_total else None,
        })
    return lines


def is_real(path: Path) -> bool:
    try:
        with path.open() as f:
            return not json.loads(f.readline()).get("synthetic", False)
    except (OSError, ValueError):
        return False


def run(data: Path, years: list[str], states: list[str] | None, levels: list[str],
        races: list[str] | None = None, force: bool = False, seed: int = 0) -> int:
    want = {str(parse(r)) for r in races} if races else None
    n = 0
    for year in years:
        for level_csv in sorted((data / year).glob("*/*.csv")):
            st, level = level_csv.parent.name, level_csv.stem
            if level not in levels or (states and st not in states):
                continue
            order = count_order(st)
            for key, counties in read_level_csv(level_csv).items():
                try:
                    canon = str(parse(key))
                except RaceKeyError:
                    continue
                if want and canon not in want:
                    continue
                out = data / "timelines" / year / (canon.replace(" ", "_") + ".jsonl")
                if out.exists() and (is_real(out) or not force):
                    continue
                lines = synthesise(canon, counties, year, order, seed=seed)
                if not lines:
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text("".join(json.dumps(x) + "\n" for x in lines))
                n += 1
    print(f"wrote {n} synthetic timelines")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("data_dir", nargs="?", default=str(DEFAULT_DATA))
    ap.add_argument("--years", default="2018,2020,2022,2024")
    ap.add_argument("--states", default="", help="comma-separated USPS codes (default all present)")
    ap.add_argument("--levels", default="senate,governor,house")
    ap.add_argument("--races", default="", help='only these keys, e.g. "PA SEN,GA-06"')
    ap.add_argument("--force", action="store_true", help="rebuild existing synthetic timelines")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    years = [y.strip() for y in a.years.split(",") if y.strip() in ELECTION_DAY]
    return run(Path(a.data_dir).expanduser(), years,
               [s.strip().upper() for s in a.states.split(",") if s.strip()] or None,
               [x.strip() for x in a.levels.split(",")],
               [r.strip() for r in a.races.split(",") if r.strip()] or None, a.force, a.seed)


if __name__ == "__main__":
    sys.exit(main())
