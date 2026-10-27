"""Canonical party codes, buckets and Discord emoji markers.

The table is static data shipped next to this module (``data/parties.yaml``, documented in
``docs/parties.md``); nothing here touches the network. Feed strings are matched against each
party's ``aliases`` case- and punctuation-insensitively, so ``"Lbt."``, ``"LIBERTARIAN"`` and
``"Libertarian Party of New York"`` all resolve to ``LIB``::

    from electionwatch.parties import party_code, party_emoji

    party_code("Constitution Party of Oregon")   # 'CST'
    party_emoji("gop")                           # '🔴'
    party_emoji("Nobody We Know")                # '⚪'

Data files are parsed with ``yaml.safe_load`` once and cached; the returned objects are frozen
and shared.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import yaml

DATA_FILE = Path(__file__).resolve().parent / "data" / "parties.yaml"

#: Buckets a party can belong to. ``far_right`` is reserved for parties that reliable sources
#: describe as neo-Nazi, white nationalist or fascist (see the entry's ``source``).
BUCKETS = (
    "dem",
    "rep",
    "libertarian",
    "green",
    "socialist",
    "far_right",
    "constitution",
    "independent",
    "centrist",
    "other",
)

#: Marker for a missing or unrecognised party: the same as "independent / no party".
UNKNOWN_EMOJI = "⚪"

REQUIRED_KEYS = ("code", "name", "bucket", "emoji", "aliases", "source")

_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{1,7}$")
_STRIP_RE = re.compile(r"[^0-9A-Za-z]+")


class PartyDataError(ValueError):
    """The party table is missing, malformed or ambiguous."""


@dataclass(frozen=True)
class Party:
    """One row of ``data/parties.yaml``."""

    code: str
    name: str
    bucket: str
    emoji: str
    aliases: tuple[str, ...] = ()
    source: str | None = None


def normalize_key(value: str) -> str:
    """Fold a party string to its lookup key: "N.P.A.-Constitution" -> "N P A CONSTITUTION".

    Accents and apostrophes are dropped, punctuation becomes a space, case is ignored.
    """
    folded = unicodedata.normalize("NFKD", str(value))
    return " ".join(_STRIP_RE.sub(" ", folded).upper().split())


def _is_single_emoji(value: str) -> bool:
    """One standard emoji/pictograph (possibly with a variation selector): no text, no spaces."""
    return (
        bool(value)
        and value == value.strip()
        and not any(ch.isspace() for ch in value)
        and len(value) <= 3
        and not any(ch.isascii() and ch.isalnum() for ch in value)
    )


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PartyDataError(f"{where}: expected a non-empty string, got {value!r}")
    return value.strip()


def _read_rows() -> list[dict]:
    try:
        with DATA_FILE.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise PartyDataError(f"cannot read {DATA_FILE}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise PartyDataError(f"invalid YAML in {DATA_FILE}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("parties"), list):
        raise PartyDataError(f"{DATA_FILE}: expected a mapping with a 'parties' list")
    return data["parties"]


@lru_cache(maxsize=1)
def load_parties() -> dict[str, Party]:
    """Return the party table keyed by canonical code, in file order."""
    table: dict[str, Party] = {}
    for i, row in enumerate(_read_rows()):
        where = f"parties.yaml[{i}]"
        if not isinstance(row, dict):
            raise PartyDataError(f"{where}: expected a mapping")
        missing = [k for k in REQUIRED_KEYS if k not in row]
        if missing:
            raise PartyDataError(f"{where}: missing keys: {', '.join(missing)}")
        unknown = sorted(set(row) - set(REQUIRED_KEYS))
        if unknown:
            raise PartyDataError(f"{where}: unknown keys: {', '.join(unknown)}")

        code = _text(row["code"], f"{where}.code")
        if not _CODE_RE.match(code):
            raise PartyDataError(f"{where}.code: {code!r} is not an uppercase short code")
        if code in table:
            raise PartyDataError(f"{where}.code: duplicate code {code}")

        name = _text(row["name"], f"{where}.name")
        bucket = _text(row["bucket"], f"{where}.bucket")
        if bucket not in BUCKETS:
            raise PartyDataError(f"{where}.bucket: {bucket!r} is not one of {', '.join(BUCKETS)}")

        emoji = _text(row["emoji"], f"{where}.emoji")
        if not _is_single_emoji(emoji):
            raise PartyDataError(f"{where}.emoji: {emoji!r} is not a single emoji")

        raw_aliases = row["aliases"]
        if not isinstance(raw_aliases, list):
            raise PartyDataError(f"{where}.aliases: expected a list")
        aliases: list[str] = []
        seen: set[str] = set()
        for j, alias in enumerate(raw_aliases):
            alias = _text(alias, f"{where}.aliases[{j}]")
            if normalize_key(alias) in seen:
                continue  # "Aloha ʻĀina" and "Aloha Aina" fold to the same key
            seen.add(normalize_key(alias))
            aliases.append(alias)

        source = _text(row["source"], f"{where}.source")
        parsed = urlparse(source)
        if parsed.scheme != "https" or not parsed.netloc:
            raise PartyDataError(f"{where}.source: expected an absolute https URL, got {source!r}")

        table[code] = Party(
            code=code,
            name=name,
            bucket=bucket,
            emoji=emoji,
            aliases=tuple(aliases),
            source=source,
        )
    if not table:
        raise PartyDataError(f"{DATA_FILE}: no parties defined")
    return table


@lru_cache(maxsize=1)
def _index() -> dict[str, str]:
    """Lookup key -> code for every alias, name and code itself."""
    index: dict[str, str] = {}
    for party in load_parties().values():
        for label in (party.code, party.name, *party.aliases):
            key = normalize_key(label)
            if not key:
                continue
            other = index.get(key)
            if other is not None and other != party.code:
                raise PartyDataError(
                    f"parties.yaml: {label!r} maps to both {other} and {party.code}"
                )
            index[key] = party.code
    return index


def party_code(raw: str | None) -> str:
    """Canonical code for a feed's party string (``"GOP"`` -> ``"REP"``).

    Case- and punctuation-insensitive. A string we do not know is returned uppercased and
    stripped, unchanged apart from a trailing period, so callers can still display it.
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    key = normalize_key(text)
    code = _index().get(key) or _truncated(key)
    if code:
        return code
    return text.upper().rstrip(".")


# Some feeds cut party names to a fixed width ("DEMOCRATIC P", "WORKING FAMI", "UNITED CITIZ").
# A label at least this long that starts exactly one party's name/alias maps to that party.
_MIN_PREFIX = 6


@lru_cache(maxsize=512)
def _truncated(key: str) -> str | None:
    if len(key) < _MIN_PREFIX:
        return None
    codes = {code for label, code in _index().items() if len(label) > len(key) and label.startswith(key)}
    return codes.pop() if len(codes) == 1 else None


def party_info(code: str | None) -> Party | None:
    """The :class:`Party` for a code or any known alias, or ``None`` if we do not know it."""
    resolved = party_code(code)
    return load_parties().get(resolved) if resolved else None


def party_emoji(raw: str | None) -> str:
    """Discord marker for a feed's party string; ⚪ when the party is unknown or empty."""
    party = party_info(raw)
    return party.emoji if party else UNKNOWN_EMOJI
