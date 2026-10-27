"""Process entry point: ``python -m electionwatch`` or ``--check``."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping

import httpx

from electionwatch.bot.discord import ElectionBot
from electionwatch.config import load_env, load_races
from electionwatch.sources.base import load_all
from electionwatch.store.mongo import MongoStore


def validate_env(environ: Mapping[str, str]) -> list[str]:
    """Return names of invalid/missing settings, never their values."""
    errors = [name for name in ("DISCORD_TOKEN", "MONGO_URI") if not environ.get(name)]
    if environ.get("DEV_GUILD_ID"):
        try:
            int(environ["DEV_GUILD_ID"])
        except ValueError:
            errors.append("DEV_GUILD_ID")
    return errors


async def check() -> int:
    errors = validate_env(os.environ)
    if errors:
        print("Invalid or missing environment: " + ", ".join(errors), file=sys.stderr)
        return 2
    try:
        races = load_races()
    except Exception as exc:
        print(f"Could not load races.yaml: {exc}", file=sys.stderr)
        return 2
    store = MongoStore(os.environ["MONGO_URI"], os.environ.get("MONGO_DB", "electionwatch"))
    try:
        await store.ping()
        await store.create_indexes()
    except Exception as exc:
        print(f"MongoDB check failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    finally:
        await store.close()
    print(f"Configuration OK; MongoDB OK; {len(races)} default races loaded")
    return 0


async def run() -> int:
    errors = validate_env(os.environ)
    if errors:
        print("Invalid or missing environment: " + ", ".join(errors), file=sys.stderr)
        return 2
    load_races()  # fail before connecting if the YAML is invalid
    store = MongoStore(os.environ["MONGO_URI"], os.environ.get("MONGO_DB", "electionwatch"))
    await store.ping()
    await store.create_indexes()
    registry = load_all()
    sources = {kind: [adapter() for adapter in adapters.values()] for kind, adapters in registry.items()}
    async with httpx.AsyncClient(
        timeout=30,
        headers={"User-Agent": "election-watch-discord/0.1 (open-source election results bot)"},
    ) as client:
        bot = ElectionBot(store, sources, client)
        try:
            await bot.start(os.environ["DISCORD_TOKEN"])
        finally:
            if not bot.is_closed():
                await bot.close()
            await store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Track US election results in Discord")
    parser.add_argument("--check", action="store_true", help="validate env, MongoDB, and races.yaml, then exit")
    args = parser.parse_args(argv)
    load_env()
    try:
        return asyncio.run(check() if args.check else run())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
