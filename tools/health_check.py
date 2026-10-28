"""Which results sources work right now? Asks every results adapter, for every state it covers,
for that state's Senate, Governor and first House race, and prints one row per (adapter, state).

    python tools/health_check.py                      # the configured ELECTION_DATE (2026 default)
    ELECTION_DATE=2024-11-05 python tools/health_check.py --json out.json

civicAPI is checked per state with its own status TEST: it lists the races but still flags them
as test data (normal before election night; it must flip to OK once real returns arrive).

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
from datetime import datetime, timezone

import httpx

from electionwatch.models import Level, RaceKey
from electionwatch.sources.base import SourceUnavailable, load_all

UA = "election-watch-discord/0.1 (open-source election results bot)"  # NBC refuses UAs containing a URL


def probe_keys(state: str) -> list[RaceKey]:
    first = "AL" if state in {"AK", "DE", "ND", "SD", "VT", "WY", "DC"} else "01"
    return [
        RaceKey(state, Level.SENATE, "SEN"),
        RaceKey(state, Level.GOVERNOR, "GOV"),
        RaceKey(state, Level.HOUSE, "HOUSE", first),
    ]


async def check(name: str, cls, state: str, client: httpx.AsyncClient, timeout: float) -> dict:
    t0 = time.monotonic()
    row = {"adapter": name, "state": state, "default": cls.enabled_by_default}
    try:
        res = await asyncio.wait_for(cls().fetch(probe_keys(state), client), timeout)
        row["status"] = "OK" if res else "EMPTY"
        row["detail"] = ", ".join(
            f"{r.key}: {r.candidates[0].name if r.candidates else '-'} {r.total_votes or 0:,}" for r in res[:3]
        )
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

        async def national(name, cls):
            # National outlets serve every state from one feed; asking per state would send
            # ~150 requests in a burst, and NBC/CNN then block the address for a while.
            async with sem:
                t0 = time.monotonic()
                keys = [k for st in sorted(cls.states) for k in probe_keys(st)]
                try:
                    got = await asyncio.wait_for(cls().fetch(keys, client), a.timeout * 3)
                    by_state = {}
                    for r in got:
                        by_state.setdefault(r.key.state, []).append(r)
                    for st in sorted(cls.states):
                        res = by_state.get(st, [])
                        rows.append(
                            {
                                "adapter": name,
                                "state": st,
                                "default": cls.enabled_by_default,
                                "status": "OK" if res else "EMPTY",
                                "secs": round(time.monotonic() - t0, 1),
                                "detail": ", ".join(
                                    f"{r.key}: {r.candidates[0].name if r.candidates else '-'} {r.total_votes or 0:,}"
                                    for r in res[:3]
                                ),
                            }
                        )
                except Exception as e:  # noqa: BLE001
                    status = "UNAVAILABLE" if isinstance(e, SourceUnavailable) else "ERROR"
                    for st in sorted(cls.states):
                        rows.append(
                            {
                                "adapter": name,
                                "state": st,
                                "default": cls.enabled_by_default,
                                "status": status,
                                "secs": round(time.monotonic() - t0, 1),
                                "detail": f"{type(e).__name__}: {str(e)[:150]}",
                            }
                        )

        async def civic(st, gate=asyncio.Semaphore(2)):
            # civicAPI serves test data before election night (fake winners, test_data: true)
            # and the adapter drops it: report TEST so the flag flipping off can be watched.
            from electionwatch.sources.calls.civicapi import _CivicAPI, parse_race

            api = _CivicAPI()
            async with gate:
                t0 = time.monotonic()
                row = {"adapter": "civicapi", "state": st, "default": True}
                try:
                    index = await api.state_races(st, client)
                    probes = [(k, index[k][0]) for k in probe_keys(st) if k in index]
                    flags, shown = [], []
                    for key, race_id in probes:
                        race = await api.race(race_id, client) or {}
                        flags.append(bool(race.get("test_data")))
                        result = parse_race(race, race_id, key, datetime.now(timezone.utc))
                        if result and result.total_votes:
                            shown.append(f"{key}: {result.candidates[0].name} {result.total_votes:,}")
                    federal = sum(1 for k in index if k.level in (Level.SENATE, Level.GOVERNOR, Level.HOUSE))
                    row["status"] = "OK" if shown else "TEST" if any(flags) else "EMPTY"
                    row["detail"] = (
                        f"{len(index)} races ({federal} federal/governor); "
                        f"test_data on {sum(flags)}/{len(flags)} probed; " + ", ".join(shown)
                    )
                except Exception as e:  # noqa: BLE001
                    row["status"] = "UNAVAILABLE" if isinstance(e, SourceUnavailable) else "ERROR"
                    row["detail"] = f"{type(e).__name__}: {str(e)[:150]}"
                row["secs"] = round(time.monotonic() - t0, 1)
                rows.append(row)

        nat = {n: c for n, c in reg.items() if n.endswith("_results") and len(c.states) > 40}
        special = set(nat) | {"civicapi"}
        await asyncio.gather(
            *(national(n, c) for n, c in nat.items() if not only or n in only),
            *(
                civic(st)
                for st in sorted(reg["civicapi"].states)
                if "civicapi" in reg and (not only or "civicapi" in only)
            ),
            *(
                one(n, c, st)
                for n, c in reg.items()
                if n not in special and (not only or n in only)
                for st in sorted(c.states)
            ),
        )
    rows.sort(key=lambda r: (r["state"], r["adapter"]))
    for r in rows:
        print(f"{r['state']}  {r['adapter']:<16} {r['status']:<11} {r['secs']:>5}s  {r['detail']}")
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"election_date": os.environ.get("ELECTION_DATE", "2026-11-03"), "rows": rows}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
