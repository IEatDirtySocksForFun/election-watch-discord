"""How people type races and states into commands: parse_typed() and resolve_state()."""

import pytest

from electionwatch.bot.early_vote import resolve_state as early_vote_state
from electionwatch.bot.early_vote import state_choices
from electionwatch.bot.logic import faq_embed
from electionwatch.racekeys import STATES, RaceKeyError, parse, parse_typed, resolve_state
from electionwatch.racelists import typed_state


@pytest.mark.parametrize(
    "text, expected",
    [
        ("pa-08", "PA-08"),
        ("PA 08", "PA-08"),
        ("pa08", "PA-08"),
        ("pa 8", "PA-08"),
        ("pennsylvania-08", "PA-08"),
        ("Pennsylvania 08", "PA-08"),
        ("pensylvaniua 08", "PA-08"),
        ("Penn. 8", "PA-08"),
        ("pensylvania congressional district 8", "PA-08"),
        ("N.C. 1", "NC-01"),
        ("new york-12", "NY-12"),
        ("new yrok 12", "NY-12"),
        ("west virgina 2", "WV-02"),
        ("kansas 3", "KS-03"),
        ("arkansas 3", "AR-03"),
        ("ak al", "AK-AL"),
        ("alaska at-large", "AK-AL"),
        ("pensylvania sen", "PA SEN"),
        ("Penn. senate", "PA SEN"),
        ("ohio sen s", "OH SEN S"),
        ("ohio senate special", "OH SEN S"),
        ("texas hd 121", "TX HD-121"),
        ("tex sd 5", "TX SD-05"),
        ("tx local harris-county-judge", "TX LOCAL harris-county-judge"),
    ],
)
def test_typed_races_land_on_one_key(text, expected):
    assert str(parse_typed(text)) == expected


@pytest.mark.parametrize("text", ["garbage", "Narnia 08", "carolina 1", "Washington DC", "pa", "nx 01"])
def test_typed_races_that_name_no_clear_race_are_refused(text):
    with pytest.raises(RaceKeyError):
        parse_typed(text)


def test_feeds_keep_the_strict_parser():
    with pytest.raises(RaceKeyError):
        parse("pensylvania 08")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("PA", "PA"),
        ("pennsylvania", "PA"),
        ("Penn.", "PA"),
        ("pensylvania", "PA"),
        ("N.C.", "NC"),
        ("n carolina", "NC"),
        ("Calif.", "CA"),
        ("w.va.", "WV"),
        ("masachusets", "MA"),
        ("New York State", "NY"),
        ("the state of texas", "TX"),
        ("washington", "WA"),
        ("Washington D.C.", "DC"),
    ],
)
def test_states_by_code_name_abbreviation_or_misspelling(text, expected):
    assert resolve_state(text, STATES) == expected
    assert early_vote_state(text) == expected


@pytest.mark.parametrize("text", ["dakota", "carolina", "nx", ""])
def test_ambiguous_or_unknown_states_are_refused(text):
    with pytest.raises(RaceKeyError):
        resolve_state(text, STATES)


def test_autocomplete_finds_abbreviations_and_misspellings():
    assert state_choices(list(STATES), "N.C.") == [("North Carolina (NC)", "NC")]
    assert state_choices(list(STATES), "pensylvania") == [("Pennsylvania (PA)", "PA")]
    assert typed_state("pensylvania 08") == "PA" and typed_state("Penn. sen") == "PA"


def test_faq_answers_a_misspelt_state_through_the_command():
    from electionwatch.faq.loader import load_outlets, load_states

    assert faq_embed(load_states(), load_outlets(), resolve_state("north carolna")) == faq_embed(
        load_states(), load_outlets(), "NC"
    )
