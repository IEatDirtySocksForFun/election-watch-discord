"""Which results sources work right now? Asks every results adapter, for every state it covers,
for that state's Senate, Governor and first House race, and prints one row per (adapter, state).

    python tools/health_check.py                      # the configured ELECTION_DATE (2026 default)
    ELECTION_DATE=2024-11-05 python tools/health_check.py --json out.json

Status: OK (returned results), EMPTY (reachable, nothing for these races yet: normal before
the feed is published), UNAVAILABLE (the adapter reported the feed down/blocked), ERROR (an
exception: a bug or an unexpected response), TIMEOUT.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

import httpx

from electionwatch.models import Level, RaceKey
from electionwatch.sources.base import SourceUnavailable, load_all

UA = "election-watch-discord health check (+https://github.com/IEatDirtySocksForFun/election-watch-discord)"


def probe_keys(state: str) -> list[RaceKey]:
    first = "AL" if state in {"AK", "DE", "ND", "SD", "VT", "WY", "DC"} else "01"
    return [RaceKey(state, Level.SENATE, "SEN"), RaceKey(state, Level.GOVERNOR, "GOV"),
            RaceKey(state, Level.HOUSE, "HOUSE", first)]


async def check(name: str, cls, state: str, client: httpx.AsyncClient, timeout: float) -> dict:
    t0 = time.monotonic()
    row = {"adapter": name, "state": state, "default": cls.enabled_by_default}
    try:
        res = await asyncio.wait_for(cls().fetch(probe_keys(state), client), timeout)
        row["status"] = "OK" if res else "EMPTY"
        row["detail"] = ", ".join(f"{r.key}: {r.candidates[0].name if r.candidates else '-'}"
                                  f" {r.total_votes or 0:,}" for r in res[:3])
    except SourceUnavailable as e:
        row["status"], row["detail"] = "UNAVAILABLE", str(e)[:160]
    except asyncio.TimeoutError:
        row["status"], row["detail"] = "TIMEOUT", f">{timeout:.0f}s"
    except Exception as e:  # noqa: BLE001 - reporting every failure is the point
        row["status"], row["detail"] = "ERROR", f"{type(e).__name__}: {str(e)[:150]}"
    row["secs"] = round(time.monotonic() - t0, 1)
    return row


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", help="write rows to this file")
    ap.add_argument("--timeout", type=float, default=45)
    ap.add_argument("--only", help="comma-separated adapter names")
    a = ap.parse_args()
    reg = load_all()["results"]
    only = set(a.only.split(",")) if a.only else None
    rows: list[dict] = []
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=40, follow_redirects=True, headers={"User-Agent": UA}) as client:
        async def one(name, cls, st):
            async with sem:
                rows.append(await check(name, cls, st, client, a.timeout))

        await asyncio.gather(*(one(n, c, st) for n, c in reg.items()
                               if not only or n in only for st in sorted(c.states)))
    rows.sort(key=lambda r: (r["state"], r["adapter"]))
    for r in rows:
        print(f"{r['state']}  {r['adapter']:<16} {r['status']:<11} {r['secs']:>5}s  {r['detail']}")
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"election_date": os.environ.get("ELECTION_DATE", "2026-11-03"), "rows": rows}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
