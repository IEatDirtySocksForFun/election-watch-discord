"""Schema tests for the static FAQ data (no network)."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

# The shared venv has the primary checkout installed editable; make sure this
# worktree's package is the one under test.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from electionwatch.faq import loader  # noqa: E402
from electionwatch.racekeys import STATES  # noqa: E402

NCSL_MAIL = (
    "https://www.ncsl.org/elections-and-campaigns/table-11-receipt-and-postmark-deadlines-for-absentee-mail-ballots"
)


def _valid_state(**overrides):
    rec = {
        "official_results_url": "https://example.gov/results",
        "sos_url": "https://example.gov/elections",
        "results_vendor": None,
        "polls_close_local": "7:00 PM",
        "timezones": ["America/Chicago"],
        "polls_close_note": None,
        "count_order": "Election-day precincts first, then absentee.",
        "mail_deadline": "Must be received by 7 p.m. on Election Day.",
        "mail_processing": "Processing begins on Election Day.",
        "typical_call_time": "Hours: small precinct counts.",
        "recount_threshold": "No automatic recount; candidate may request one.",
        "ranked_choice": "false",
        "notes": "Test state.",
        "sources": [NCSL_MAIL],
    }
    rec.update(overrides)
    return rec


def test_all_states_and_dc_present():
    states = loader.load_states()
    assert set(states) == set(STATES)
    assert len(states) == 51


def test_required_keys_and_types():
    text_keys = loader.STATE_TEXT_KEYS
    for code, rec in loader.load_states().items():
        assert set(loader.REQUIRED_STATE_KEYS) <= set(rec), code
        assert set(rec) <= set(loader.REQUIRED_STATE_KEYS) | set(loader.OPTIONAL_STATE_KEYS)
        for key in text_keys:
            assert isinstance(rec[key], str) and rec[key].strip(), f"{code}.{key}"
            assert len(rec[key]) <= loader.MAX_FIELD_CHARS, f"{code}.{key}"
        assert isinstance(rec["timezones"], list) and rec["timezones"]
        assert all(isinstance(z, str) and "/" in z for z in rec["timezones"]), code
        assert isinstance(rec["sources"], list) and rec["sources"]
        assert isinstance(rec.get("unverified", []), list)
        if rec["results_vendor"] is not None:
            assert isinstance(rec["results_vendor"], str)


def test_state_urls_are_https():
    for code, rec in loader.load_states().items():
        urls = [rec["official_results_url"], rec["sos_url"], *rec["sources"]]
        for url in urls:
            parsed = urlparse(url)
            assert parsed.scheme == "https", f"{code}: {url}"
            assert parsed.netloc, f"{code}: {url}"


def test_poll_closing_fields():
    for code, rec in loader.load_states().items():
        assert "PM" in rec["polls_close_local"], code
        if len(rec["timezones"]) > 1:
            assert rec.get("polls_close_note"), f"{code} spans time zones but has no note"


def test_unverified_fields_are_real_fields():
    allowed = set(loader.REQUIRED_STATE_KEYS) | set(loader.OPTIONAL_STATE_KEYS)
    for code, rec in loader.load_states().items():
        for field in rec.get("unverified", []):
            assert field in allowed, f"{code}: unknown field marked unverified: {field}"


def test_outlets():
    outlets = loader.load_outlets()
    assert set(outlets) >= loader.REQUIRED_OUTLETS
    for key, rec in outlets.items():
        assert key == key.upper(), key
        assert isinstance(rec["name"], str) and rec["name"].strip(), key
        assert isinstance(rec["notes"], str) and rec["notes"].strip(), key
        assert len(rec["notes"]) <= loader.MAX_FIELD_CHARS, key
        for url in (rec["results_url"], rec.get("exit_poll_url")):
            if url is None:
                continue
            parsed = urlparse(url)
            assert parsed.scheme == "https", f"{key}: {url}"
            assert parsed.netloc, f"{key}: {url}"


def test_loaders_are_cached():
    assert loader.load_states() is loader.load_states()
    assert loader.load_outlets() is loader.load_outlets()


def test_validation_rejects_missing_state():
    data = {code: _valid_state() for code in STATES}
    del data["WY"]
    with pytest.raises(loader.FAQDataError, match="missing state codes"):
        loader._validate_states(data)


def test_validation_rejects_unknown_state():
    data = {code: _valid_state() for code in STATES}
    data["ZZ"] = _valid_state()
    with pytest.raises(loader.FAQDataError, match="unknown state codes"):
        loader._validate_states(data)


def test_validation_rejects_http_url():
    data = {code: _valid_state() for code in STATES}
    data["TX"] = _valid_state(sos_url="http://example.gov/elections")
    with pytest.raises(loader.FAQDataError, match="https"):
        loader._validate_states(data)


def test_validation_rejects_long_text():
    data = {code: _valid_state() for code in STATES}
    data["CA"] = _valid_state(notes="x" * (loader.MAX_FIELD_CHARS + 1))
    with pytest.raises(loader.FAQDataError, match="exceeds"):
        loader._validate_states(data)


def test_validation_rejects_unknown_key():
    data = copy.deepcopy({code: _valid_state() for code in STATES})
    data["FL"]["results_url"] = "https://example.gov"
    with pytest.raises(loader.FAQDataError, match="unknown keys"):
        loader._validate_states(data)


def test_validation_rejects_missing_outlet():
    data = {
        key: {"name": key, "results_url": "https://example.gov", "exit_poll_url": None, "notes": "x"}
        for key in loader.REQUIRED_OUTLETS - {"AP"}
    }
    with pytest.raises(loader.FAQDataError, match="missing outlets"):
        loader._validate_outlets(data)
