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
        results = await asyncio.wait_for(cls().fetch(probe_keys(state), client), timeout)
        row["status"] = "OK" if results else "EMPTY"
        row["detail"] = ", ".join(
            f"{result_row.key}: {result_row.candidates[0].name if result_row.candidates else '-'} {result_row.total_votes or 0:,}"
            for result_row in results[:3]
        )
    except SourceUnavailable as exc:
        row["status"], row["detail"] = "UNAVAILABLE", str(exc)[:160]
    except asyncio.TimeoutError:
        row["status"], row["detail"] = "TIMEOUT", f">{timeout:.0f}s"
    except Exception as exc:  # noqa: BLE001 - reporting every failure is the point
        row["status"], row["detail"] = "ERROR", f"{type(exc).__name__}: {str(exc)[:150]}"
    row["secs"] = round(time.monotonic() - t0, 1)
    return row


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", help="write rows to this file")
    ap.add_argument("--timeout", type=float, default=45)
    ap.add_argument("--only", help="comma-separated adapter names")
    args = ap.parse_args()
    reg = load_all()["results"]
    only = set(args.only.split(",")) if args.only else None
    rows: list[dict] = []
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=40, follow_redirects=True, headers={"User-Agent": UA}) as client:

        async def one(name, cls, st):
            async with sem:
                rows.append(await check(name, cls, st, client, args.timeout))

        async def national(name, cls):
            # National outlets serve every state from one feed; asking per state would send
            # ~150 requests in a burst, and NBC/CNN then block the address for a while.
            async with sem:
                t0 = time.monotonic()
                keys = [race_key for st in sorted(cls.states) for race_key in probe_keys(st)]
                try:
                    got = await asyncio.wait_for(cls().fetch(keys, client), args.timeout * 3)
                    by_state = {}
                    for result_rowow in got:
                        by_state.setdefault(result_rowow.key.state, []).append(result_rowow)
                    for st in sorted(cls.states):
                        results = by_state.get(st, [])
                        rows.append(
                            {
                                "adapter": name,
                                "state": st,
                                "default": cls.enabled_by_default,
                                "status": "OK" if results else "EMPTY",
                                "secs": round(time.monotonic() - t0, 1),
                                "detail": ", ".join(
                                    f"{result_rowow.key}: {result_rowow.candidates[0].name if result_rowow.candidates else '-'} {result_rowow.total_votes or 0:,}"
                                    for result_rowow in results[:3]
                                ),
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    status = "UNAVAILABLE" if isinstance(exc, SourceUnavailable) else "ERROR"
                    for st in sorted(cls.states):
                        rows.append(
                            {
                                "adapter": name,
                                "state": st,
                                "default": cls.enabled_by_default,
                                "status": status,
                                "secs": round(time.monotonic() - t0, 1),
                                "detail": f"{type(exc).__name__}: {str(exc)[:150]}",
                            }
                        )

        async def civic(state, gate=asyncio.Semaphore(2)):
            # civicAPI serves test data before election night (fake winners, test_data: true)
            # and the adapter drops it: report TEST so the flag flipping off can be watched.
            from electionwatch.sources.calls.civicapi import _CivicAPI, parse_race

            api = _CivicAPI()
            async with gate:
                t0 = time.monotonic()
                row = {"adapter": "civicapi", "state": state, "default": True}
                try:
                    index = await api.state_races(state, client)
                    probes = [(race_key, index[race_key][0]) for race_key in probe_keys(state) if race_key in index]
                    flags, shown = [], []
                    for key, race_id in probes:
                        race = await api.race(race_id, client) or {}
                        flags.append(bool(race.get("test_data")))
                        race_result = parse_race(race, race_id, key, datetime.now(timezone.utc))
                        if race_result and race_result.total_votes:
                            shown.append(f"{key}: {race_result.candidates[0].name} {race_result.total_votes:,}")
                    federal = sum(
                        1 for race_key in index if race_key.level in (Level.SENATE, Level.GOVERNOR, Level.HOUSE)
                    )
                    row["status"] = "OK" if shown else "TEST" if any(flags) else "EMPTY"
                    row["detail"] = (
                        f"{len(index)} races ({federal} federal/governor); "
                        f"test_data on {sum(flags)}/{len(flags)} probed; " + ", ".join(shown)
                    )
                except Exception as exc:  # noqa: BLE001
                    row["status"] = "UNAVAILABLE" if isinstance(exc, SourceUnavailable) else "ERROR"
                    row["detail"] = f"{type(exc).__name__}: {str(exc)[:150]}"
                row["secs"] = round(time.monotonic() - t0, 1)
                rows.append(row)

        nat = {
            adapter_name: adapter_cls
            for adapter_name, adapter_cls in reg.items()
            if adapter_name.endswith("_results") and len(adapter_cls.states) > 40
        }
        special = set(nat) | {"civicapi"}
        await asyncio.gather(
            *(
                national(adapter_name, adapter_cls)
                for adapter_name, adapter_cls in nat.items()
                if not only or adapter_name in only
            ),
            *(
                civic(st)
                for st in sorted(reg["civicapi"].states)
                if "civicapi" in reg and (not only or "civicapi" in only)
            ),
            *(
                one(adapter_name, adapter_cls, st)
                for adapter_name, adapter_cls in reg.items()
                if adapter_name not in special and (not only or adapter_name in only)
                for st in sorted(adapter_cls.states)
            ),
        )
    rows.sort(key=lambda row: (row["state"], row["adapter"]))
    for row in rows:
        print(f"{row['state']}  {row['adapter']:<16} {row['status']:<11} {row['secs']:>5}s  {row['detail']}")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"election_date": os.environ.get("ELECTION_DATE", "2026-11-03"), "rows": rows}, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
