"""Environment and default-race configuration.

Environment files are deliberately parsed here instead of adding a dotenv dependency.
Only missing variables are populated: the process environment always has precedence.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping

import yaml

from electionwatch.models import ImportantRace
from electionwatch.racekeys import parse

_ENV_LINE = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*(.*)")
DEFAULT_RACES_FILE = Path(__file__).resolve().parent.parent / "config" / "races.yaml"


def _value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def parse_env_line(line: str) -> tuple[str, str] | None:
    """Parse KEY=value or KEY: value; blank lines and comments are ignored."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    match = _ENV_LINE.fullmatch(stripped)
    if not match:
        return None
    return match.group(1), _value(match.group(2))


def load_env(environ: Mapping[str, str] | None = None) -> list[Path]:
    """Load EW_ENV_FILES (comma separated), or .env, without overriding real env.

    Returns the files which were read. Values are never logged or included in errors.
    """
    env = os.environ if environ is None else environ
    names = env.get("EW_ENV_FILES", "").split(",") if env.get("EW_ENV_FILES") else [".env"]
    loaded: list[Path] = []
    # The real process environment must win even when a custom mapping is supplied.
    target = os.environ
    for name in names:
        path = Path(name.strip()).expanduser()
        if not name.strip() or not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            parsed = parse_env_line(line)
            if parsed and parsed[0] not in env and parsed[0] not in target:
                target[parsed[0]] = parsed[1]
        loaded.append(path)
    return loaded


def races_file() -> Path:
    return Path(os.environ.get("EW_RACES_FILE", str(DEFAULT_RACES_FILE))).expanduser()


def load_races(path: str | Path | None = None) -> list[ImportantRace]:
    """Load and validate the default important races from YAML."""
    source = Path(path) if path is not None else races_file()
    with source.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    entries = config.get("important", [])
    if not isinstance(entries, list):
        raise ValueError(f"{source}: 'important' must be a list")
    races: list[ImportantRace] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("key"):
            raise ValueError(f"{source}: every important race needs a key")
        race = ImportantRace(parse(str(entry["key"])), entry.get("label"), entry.get("notes"))
        key = str(race.key)
        if key not in seen:
            races.append(race)
            seen.add(key)
    return races
