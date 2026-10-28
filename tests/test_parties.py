"""Party table: codes, aliases, buckets and the emoji markers the bot shows."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from electionwatch import models, parties
from electionwatch.parties import (
    BUCKETS,
    UNKNOWN_EMOJI,
    PartyDataError,
    load_parties,
    normalize_key,
    party_code,
    party_emoji,
    party_info,
)

ROOT = Path(__file__).resolve().parent.parent
YAML_FILE = ROOT / "electionwatch" / "data" / "parties.yaml"
DOCS = ROOT / "docs" / "parties.md"

#: The owner's fixed mappings (brief) — these must never drift.
FIXED_EMOJI = {
    "DEM": "🔵",
    "REP": "🔴",
    "LIB": "🟡",
    "LBT": "🟡",
    "L": "🟡",
    "Libertarian": "🟡",
    "GRN": "🟢",
    "GRE": "🟢",
    "G": "🟢",
    "Green": "🟢",
    "CST": "📜",
    "CON": "📜",
    "Constitution": "📜",
    "IND": "⚪",
    "I": "⚪",
    "NPA": "⚪",
    "NP": "⚪",
    "U": "⚪",
    "UNA": "⚪",
}

#: The owner's emoji sequence for socialist/communist parties, in order of party size.
SOCIALIST_SEQUENCE = ["✊", "🌹", "☭", "🚩", "🔨", "🌾", "⭐", "🛠️"]

FAR_RIGHT_EMOJI = {"⛓️", "🔒", "🚔", "🏴", "⚠️"}

#: Buckets where many parties deliberately share one marker.
SHARED_EMOJI_BUCKETS = {"dem", "rep", "libertarian", "green", "constitution", "independent"}


@pytest.fixture(scope="module")
def table() -> dict[str, parties.Party]:
    return load_parties()


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return yaml.safe_load(YAML_FILE.read_text(encoding="utf-8"))["parties"]


def test_at_least_ninety_parties(table):
    assert len(table) >= 90


def test_codes_are_unique_uppercase_short_codes(table):
    assert list(table) == [p.code for p in table.values()]
    for code in table:
        assert code == code.upper()
        assert 2 <= len(code) <= 8
        assert code.isalnum()


def test_every_entry_is_complete(table):
    for party in table.values():
        assert party.name.strip()
        assert party.bucket in BUCKETS
        assert party.aliases, f"{party.code} has no aliases"
        assert party.source and party.source.startswith("https://")
        assert party.emoji != UNKNOWN_EMOJI or party.bucket == "independent"


def test_emoji_is_a_single_standard_marker(table):
    for party in table.values():
        emoji = party.emoji
        assert emoji and emoji == emoji.strip()
        assert not any(ch.isspace() for ch in emoji), party.code
        assert len(emoji) <= 3, party.code
        assert not any(ch.isascii() and ch.isalnum() for ch in emoji), party.code


def test_every_alias_and_name_resolves_to_its_code(table, rows):
    for row in rows:
        for label in [row["code"], row["name"], *row["aliases"]]:
            assert party_code(label) == row["code"], f"{label!r} did not resolve to {row['code']}"
            assert party_info(label).code == row["code"]


def test_alias_maps_to_exactly_one_code(table):
    owner: dict[str, str] = {}
    for party in table.values():
        for label in (party.code, party.name, *party.aliases):
            key = normalize_key(label)
            if key in owner:
                assert owner[key] == party.code, f"{label!r} is claimed by two parties"
            owner[key] = party.code
    assert len(owner) == len(set(owner))


def test_aliases_are_distinct_after_folding(table):
    for party in table.values():
        keys = [normalize_key(a) for a in party.aliases]
        assert len(keys) == len(set(keys)), party.code


def test_lookup_ignores_case_and_punctuation():
    assert party_code("lib") == party_code("LIB") == "LIB"
    assert party_code("Lbt.") == "LIB"
    assert party_code("  libertarian  ") == "LIB"
    assert party_code("Libertarian Party of New York") == "LIB"
    assert party_code("d.-f.-l.") == "DEM"
    assert party_code("Peace & Freedom") == party_code("Peace and Freedom Party") == "PFP"
    assert party_code("Aloha ʻĀina") == "ALO"


@pytest.mark.parametrize("label,emoji", sorted(FIXED_EMOJI.items()))
def test_owner_fixed_mappings(label, emoji):
    assert party_emoji(label) == emoji


def test_owner_fixed_codes():
    assert party_code("GOP") == party_code("R") == "REP"
    assert party_code("DFL") == "DEM"
    assert party_code("IND") == party_code("Independent") == "IND"
    assert party_code("NPA") == party_code("NP") == party_code("UNA") == "NP"
    assert party_code("WRI") == party_code("Write-in") == "WRI"


def test_socialist_emoji_sequence_in_size_order(table):
    socialists = [p for p in table.values() if p.bucket == "socialist"]
    assert [p.emoji for p in socialists][: len(SOCIALIST_SEQUENCE)] == SOCIALIST_SEQUENCE
    assert len({p.emoji for p in socialists}) == len(socialists)


def test_far_right_is_narrow_and_distinct(table):
    far = [p for p in table.values() if p.bucket == "far_right"]
    assert {p.name for p in far} == {
        "American Nazi Party",
        "National Socialist Movement",
        "American Freedom Party",
        "Traditionalist Worker Party",
        "National Renaissance Party",
    }
    assert len({p.emoji for p in far}) == len(far)
    assert {p.emoji for p in far} <= FAR_RIGHT_EMOJI
    for party in far:
        assert party.source.startswith("https://en.wikipedia.org/wiki/")


@pytest.mark.parametrize(
    "name",
    ["Conservative Party of New York State", "New Jersey Conservative Party", "Tea Party of Nevada"],
)
def test_mainstream_conservatives_are_not_far_right(name):
    info = party_info(name)
    assert info is not None
    assert info.bucket != "far_right"


def test_emojis_are_distinct_outside_the_shared_buckets(table):
    seen: dict[str, str] = {}
    for party in table.values():
        if party.bucket in SHARED_EMOJI_BUCKETS:
            continue
        assert party.emoji not in seen, f"{party.code} reuses {seen[party.emoji]}'s emoji"
        seen[party.emoji] = party.code


def test_constitution_affiliates_use_the_scroll(table):
    for label in [
        "Constitution Party",
        "American Independent Party",
        "American Independent",
        "Independent American Party",
        "Taxpayers Party of New York",
    ]:
        assert party_emoji(label) == "📜", label


def test_unknown_party_passes_through():
    assert party_code("Bull Moose Party") == "BULL MOOSE PARTY"
    assert party_code("  Bull Moose Party. ") == "BULL MOOSE PARTY"
    assert party_emoji("Bull Moose Party") == UNKNOWN_EMOJI
    assert party_info("BULL MOOSE PARTY") is None
    assert party_info("NOPE") is None


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_empty_party(raw):
    assert party_code(raw) == ""
    assert party_emoji(raw) == UNKNOWN_EMOJI
    assert party_info(raw) is None


def test_party_info_returns_frozen_record(table):
    info = party_info("gop")
    assert info is table["REP"]
    with pytest.raises(Exception):
        info.code = "X"  # frozen dataclass


def test_shared_contract_with_models_alias_table():
    """electionwatch.models.normalize_party is the fallback adapters use; same answers here."""
    for raw, expected in models._PARTY_ALIASES.items():
        assert party_code(raw) == expected, raw


def test_load_parties_is_cached():
    assert load_parties() is load_parties()


def test_docs_table_lists_every_party():
    # docs/parties.md is written by hand; it just has to keep up with the YAML.
    text = DOCS.read_text(encoding="utf-8")
    for party in load_parties().values():
        assert re.search(rf"\|\s*`{re.escape(party.code)}`\s*\|", text), f"{party.code} missing from docs/parties.md"
    assert f"{len(load_parties())} parties" in text


def test_malformed_table_is_rejected(tmp_path, monkeypatch):
    bad = tmp_path / "parties.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "parties": [
                    {
                        "code": "AAA",
                        "name": "A Party",
                        "bucket": "other",
                        "emoji": "🅰️",
                        "aliases": ["Shared"],
                        "source": "https://example.org/a",
                    },
                    {
                        "code": "BBB",
                        "name": "B Party",
                        "bucket": "other",
                        "emoji": "🅱️",
                        "aliases": ["shared"],
                        "source": "https://example.org/b",
                    },
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(parties, "DATA_FILE", bad)
    parties.load_parties.cache_clear()
    parties._index.cache_clear()
    try:
        with pytest.raises(PartyDataError, match="maps to both"):
            parties._index()
    finally:
        monkeypatch.undo()
        parties.load_parties.cache_clear()
        parties._index.cache_clear()
    assert party_code("GOP") == "REP"


def test_truncated_feed_names_map_to_their_party():
    from electionwatch.parties import party_code, party_emoji

    assert party_code("DEMOCRATIC P") == "DEM" and party_emoji("REPUBLICAN P") == "🔴"
    assert party_code("WORKING FAMI") == "WFP"
    assert party_code("WOR") == "WFP" and party_code("LBN") == "LIB"
    assert party_emoji("NON PARTISAN") == "⚪"  # no party stays white
    assert party_code("Green") in {"GRN"}  # exact alias wins over prefix
