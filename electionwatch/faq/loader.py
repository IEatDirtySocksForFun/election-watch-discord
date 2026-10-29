"""Load and validate the static FAQ data shipped with the bot.

The data lives next to this module:

    states.yaml   per-state FAQ used by the /faq command
    outlets.yaml  national outlets and decision desks listed by /faq without a state

Both files are parsed with ``yaml.safe_load`` once and cached.  The returned
dictionaries are shared: treat every one as read-only.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import yaml

from electionwatch.racekeys import STATES

DATA_DIR = Path(__file__).resolve().parent

#: Discord embed field limit (the FAQ renders one field per key).
MAX_FIELD_CHARS = 1024

REQUIRED_STATE_KEYS = (
    "official_results_url",
    "sos_url",
    "results_vendor",
    "polls_close_local",
    "timezones",
    "count_order",
    "mail_deadline",
    "mail_processing",
    "typical_call_time",
    "recount_threshold",
    "ranked_choice",
    "notes",
    "sources",
)
OPTIONAL_STATE_KEYS = ("polls_close_note", "unverified")
#: Free-text fields that are rendered in an embed and must stay short.
STATE_TEXT_KEYS = (
    "polls_close_local",
    "count_order",
    "mail_deadline",
    "mail_processing",
    "typical_call_time",
    "recount_threshold",
    "ranked_choice",
    "notes",
)
REQUIRED_OUTLETS = frozenset({"AP", "NBC", "CBS", "ABC", "CNN", "FOX", "NYT", "DDHQ", "VOTEHUB", "REUTERS"})
REQUIRED_OUTLET_KEYS = ("name", "results_url", "notes")


class FAQDataError(ValueError):
    """The FAQ data file is missing or does not match the schema."""


def _https_url(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise FAQDataError(f"{where}: expected a URL string, got {value!r}")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise FAQDataError(f"{where}: URL must be absolute https, got {value!r}")
    return value


def _text(value: object, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise FAQDataError(f"{where}: expected a string, got {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise FAQDataError(f"{where}: must not be empty")
    if len(value) > MAX_FIELD_CHARS:
        raise FAQDataError(f"{where}: {len(value)} chars exceeds Discord's {MAX_FIELD_CHARS} limit")
    return value


def _read_yaml(name: str) -> dict:
    path = DATA_DIR / name
    try:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:  # missing/unreadable file
        raise FAQDataError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise FAQDataError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise FAQDataError(f"{path}: top level must be a mapping of codes to records")
    return data


def _validate_states(data: dict) -> dict[str, dict]:
    codes = set(STATES)
    missing = sorted(codes - set(data))
    extra = sorted(set(data) - codes, key=str)
    if missing:
        raise FAQDataError(f"states.yaml: missing state codes: {', '.join(missing)}")
    if extra:
        raise FAQDataError(f"states.yaml: unknown state codes: {', '.join(map(str, extra))}")

    for code in sorted(codes):
        rec = data[code]
        where = f"states.yaml[{code}]"
        if not isinstance(rec, dict):
            raise FAQDataError(f"{where}: expected a mapping")
        missing_keys = [k for k in REQUIRED_STATE_KEYS if k not in rec]
        if missing_keys:
            raise FAQDataError(f"{where}: missing keys: {', '.join(missing_keys)}")
        unknown = sorted(set(rec) - set(REQUIRED_STATE_KEYS) - set(OPTIONAL_STATE_KEYS))
        if unknown:
            raise FAQDataError(f"{where}: unknown keys: {', '.join(unknown)}")

        _https_url(rec["official_results_url"], f"{where}.official_results_url")
        _https_url(rec["sos_url"], f"{where}.sos_url")

        vendor = rec["results_vendor"]
        if vendor is not None:
            _text(vendor, f"{where}.results_vendor", allow_empty=True)

        for key in STATE_TEXT_KEYS:
            _text(rec[key], f"{where}.{key}")
        if rec.get("polls_close_note") is not None:
            _text(rec["polls_close_note"], f"{where}.polls_close_note", allow_empty=True)

        zones = rec["timezones"]
        if not isinstance(zones, list) or not zones:
            raise FAQDataError(f"{where}.timezones: expected a non-empty list")
        for i, zone in enumerate(zones):
            _text(zone, f"{where}.timezones[{i}]")

        sources = rec["sources"]
        if not isinstance(sources, list) or not sources:
            raise FAQDataError(f"{where}.sources: expected a non-empty list")
        for i, url in enumerate(sources):
            _https_url(url, f"{where}.sources[{i}]")

        unverified = rec.get("unverified", [])
        if not isinstance(unverified, list):
            raise FAQDataError(f"{where}.unverified: expected a list")
        for i, field in enumerate(unverified):
            _text(field, f"{where}.unverified[{i}]")
    return data


def _validate_outlets(data: dict) -> dict[str, dict]:
    missing = sorted(REQUIRED_OUTLETS - set(data))
    if missing:
        raise FAQDataError(f"outlets.yaml: missing outlets: {', '.join(missing)}")

    for key, rec in data.items():
        where = f"outlets.yaml[{key}]"
        if not isinstance(key, str) or key != key.upper():
            raise FAQDataError(f"{where}: outlet keys must be uppercase")
        if not isinstance(rec, dict):
            raise FAQDataError(f"{where}: expected a mapping")
        missing_keys = [k for k in REQUIRED_OUTLET_KEYS if k not in rec]
        if missing_keys:
            raise FAQDataError(f"{where}: missing keys: {', '.join(missing_keys)}")
        _text(rec["name"], f"{where}.name")
        _https_url(rec["results_url"], f"{where}.results_url")
        exit_poll = rec.get("exit_poll_url")
        if exit_poll is not None:
            _https_url(exit_poll, f"{where}.exit_poll_url")
        _text(rec["notes"], f"{where}.notes")
    return data


@lru_cache(maxsize=1)
def load_states() -> dict[str, dict]:
    """Return the validated per-state FAQ, keyed by USPS code (AL..WY, DC)."""
    return _validate_states(_read_yaml("states.yaml"))


@lru_cache(maxsize=1)
def load_outlets() -> dict[str, dict]:
    """Return the validated national outlet/decision-desk metadata."""
    return _validate_outlets(_read_yaml("outlets.yaml"))
