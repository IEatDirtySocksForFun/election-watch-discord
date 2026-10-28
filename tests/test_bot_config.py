import yaml

from electionwatch.__main__ import validate_env
from electionwatch.config import load_env, load_races, parse_env_line


def test_env_files_accept_colon_and_equals_without_overriding(monkeypatch, tmp_path):
    first = tmp_path / "first.env"
    second = tmp_path / "second.env"
    first.write_text("DISCORD_TOKEN: from-file\nMONGODB_URI=mongodb://file\n", encoding="utf-8")
    second.write_text("MONGODB_DB: election_test\n", encoding="utf-8")
    monkeypatch.setenv("EW_ENV_FILES", f"{first},{second}")
    monkeypatch.setenv("DISCORD_TOKEN", "real-environment")
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.delenv("MONGODB_DB", raising=False)

    loaded = load_env()
    assert loaded == [first, second]
    import os

    assert os.environ["DISCORD_TOKEN"] == "real-environment"
    assert os.environ["MONGODB_URI"] == "mongodb://file"
    assert os.environ["MONGODB_DB"] == "election_test"
    assert parse_env_line("# secret-like comments are ignored") is None
    assert parse_env_line("not an assignment") is None


def test_validate_env_reports_names_not_values():
    assert validate_env({}) == ["DISCORD_TOKEN", "MONGODB_URI"]
    assert validate_env({"DISCORD_TOKEN": "not-printed", "MONGODB_URI": "not-printed", "DEV_GUILD_ID": "bad"}) == [
        "DEV_GUILD_ID"
    ]
    assert validate_env({"DISCORD_TOKEN": "not-printed", "MONGODB_URI": "not-printed", "DEV_GUILD_ID": "123"}) == []


def test_load_races_validates_and_canonicalizes(tmp_path):
    path = tmp_path / "races.yaml"
    path.write_text(
        yaml.safe_dump({"important": [{"key": "NC-1", "label": "NC first"}, {"key": "NC-01"}]}),
        encoding="utf-8",
    )
    races = load_races(path)
    assert len(races) == 1
    assert str(races[0].key) == "NC-01" and races[0].label == "NC first"


def test_build_sources_keeps_defaults_and_honours_explicit_opt_ins():
    from electionwatch.__main__ import build_sources

    class Default:
        enabled_by_default = True

    class Scrape:
        enabled_by_default = False

    registry = {"results": {"default_adapter": Default, "scrape_adapter": Scrape}, "odds": {}}

    built = build_sources(registry, {})
    assert [type(source).__name__ for source in built["results"]] == ["Default"]
    assert built["odds"] == []

    built = build_sources(registry, {"EW_ENABLED_SOURCES": " scrape_adapter "})
    assert [type(source).__name__ for source in built["results"]] == ["Default", "Scrape"]
    built = build_sources(registry, {"EW_DISABLED_SOURCES": "default_adapter"})
    assert built["results"] == []


def test_source_states_narrows_one_adapter_to_the_listed_states():
    from electionwatch.__main__ import build_sources, source_states

    class National:
        enabled_by_default = False
        states = frozenset({"TX", "TN", "CA", "NY"})

    registry = {"results": {"fox_results": National}}
    env = {"EW_ENABLED_SOURCES": "fox_results", "EW_SOURCE_STATES": "fox_results=tx, tn,WY; other=CA"}
    (fox,) = build_sources(registry, env)["results"]
    assert fox.states == frozenset({"TX", "TN"})  # WY is not one of its states
    assert National.states == frozenset({"TX", "TN", "CA", "NY"})
    # Listing states never enables an adapter by itself.
    assert build_sources(registry, {"EW_SOURCE_STATES": "fox_results=TX"})["results"] == []
    assert source_states("bad;=TX;a=") == {"a": frozenset()}


def test_request_urls_with_api_keys_are_not_logged_at_info(monkeypatch):
    import logging

    from electionwatch.__main__ import configure_logging

    monkeypatch.setattr(logging.getLogger("httpx"), "level", logging.NOTSET)
    configure_logging("INFO")
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
