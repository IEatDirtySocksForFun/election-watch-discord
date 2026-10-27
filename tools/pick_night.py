#!/usr/bin/env python3
"""Pick one race per category per state for a full election-night simulation.

senate, house (first district, or AL), governor (GOV, else the biggest statewide), state
legislature (biggest SD/HD by votes), local (biggest local contest by votes).
Extra races (e.g. PA-07) are always included. Prints comma-separated race keys.
"""
import csv, sys
from collections import Counter
from pathlib import Path

data, year = Path(sys.argv[1]).expanduser(), sys.argv[2]
extra = [k for k in sys.argv[3].split(",") if k] if len(sys.argv) > 3 else []
picked = list(extra)
for st_dir in sorted(p for p in (data / year).iterdir() if p.is_dir() and len(p.name) == 2):
    for level in ("senate", "house", "governor", "state", "local"):
        f = st_dir / f"{level}.csv"
        if not f.exists():
            continue
        votes = Counter()
        with open(f, newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    votes[row["race_key"]] += int(float(row["votes"] or 0))
                except ValueError:
                    pass
        if not votes:
            continue
        keys = sorted(votes)
        if level == "senate":
            k = next((k for k in keys if not k.endswith(" S")), keys[0])
        elif level == "house":
            k = keys[0]
        elif level == "governor":
            k = next((k for k in keys if k.endswith(" GOV")), votes.most_common(1)[0][0])
        else:
            # skip county slices of statewide offices that some sources file as local
            cands = [(v, k) for k, v in votes.items() if "commissioner-of-labor" not in k
                     and "referendum" not in k and "amendment" not in k]
            k = max(cands)[1] if cands else votes.most_common(1)[0][0]
        if k not in picked:
            picked.append(k)
print(",".join(picked))
