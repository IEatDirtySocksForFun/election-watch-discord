"""Process entry point: ``python -m electionwatch`` or ``--check``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Mapping

import httpx

from electionwatch.bot.discord import ElectionBot
from electionwatch.config import load_env, load_races
from electionwatch.sources.base import load_all
from electionwatch.store.mongo import MongoStore


def validate_env(environ: Mapping[str, str]) -> list[str]:
    """Return names of invalid/missing settings, never the values."""
    errors = [name for name in ("DISCORD_TOKEN", "MONGODB_URI") if not environ.get(name)]
    if environ.get("DEV_GUILD_ID"):
        try:
            int(environ["DEV_GUILD_ID"])
        except ValueError:
            errors.append("DEV_GUILD_ID")
    if environ.get("EW_COMMAND_DATA", "").strip().lower() not in ("", "db", "live"):
        errors.append("EW_COMMAND_DATA")
    return errors


def build_sources(registry: Mapping[str, Mapping[str, type]], environ: Mapping[str, str]) -> dict:
    """Instantiate the adapters the poller should use.

    Adapters disabled by default (HTML scrapes, untested feeds) are skipped unless the operator
    lists those adapters in EW_ENABLED_SOURCES, e.g. EW_ENABLED_SOURCES=cbs,fox; EW_DISABLED_SOURCES
    turns off default adapters.
    """

    def names(var: str) -> set[str]:
        return {name.strip() for name in environ.get(var, "").split(",") if name.strip()}

    wanted, unwanted = names("EW_ENABLED_SOURCES"), names("EW_DISABLED_SOURCES")
    only = source_states(environ.get("EW_SOURCE_STATES", ""))
    built: dict = {}
    for kind, adapters in registry.items():
        built[kind] = []
        for name, adapter in adapters.items():
            if name in unwanted or not (adapter.enabled_by_default or name in wanted):
                continue
            instance = adapter()
            if name in only and getattr(instance, "states", None) is not None:
                # Instance attribute: narrows this adapter only, the class keeps its states.
                instance.states = frozenset(instance.states) & only[name]
            built[kind].append(instance)
        # Adapter order is priority order (the poller takes the first complete answer):
        # national fallbacks go after every official feed, whatever order modules imported in.
        built[kind].sort(key=lambda source: bool(getattr(source, "fallback", False)))
    return built


def source_states(raw: str) -> dict[str, frozenset[str]]:
    """EW_SOURCE_STATES="fox_results=TX,TN,AL;cbs_results=TX" -> {name: {USPS codes}}.

    Limits a (multi-state) adapter to the listed states, e.g. a national fallback switched
    on only for the states without a complete official feed. Unlisted adapters are
    unchanged; this never enables an adapter by itself (EW_ENABLED_SOURCES does that)."""
    by_source: dict[str, frozenset[str]] = {}
    for part in raw.split(";"):
        name, sep, states = part.partition("=")
        if not sep or not name.strip():
            continue
        by_source[name.strip()] = frozenset(code.strip().upper() for code in states.split(",") if code.strip())
    return by_source


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
    store = MongoStore(os.environ["MONGODB_URI"], os.environ.get("MONGODB_DB", "electionwatch"))
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


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs every request URL at INFO; keyed APIs (AP) carry the API key in the query string.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


async def run() -> int:
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    errors = validate_env(os.environ)
    if errors:
        print("Invalid or missing environment: " + ", ".join(errors), file=sys.stderr)
        return 2
    load_races()  # fail before connecting if the YAML is invalid
    store = MongoStore(os.environ["MONGODB_URI"], os.environ.get("MONGODB_DB", "electionwatch"))
    await store.ping()
    await store.create_indexes()
    registry = load_all()
    sources = build_sources(registry, os.environ)
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
