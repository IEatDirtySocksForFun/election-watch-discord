import yaml

from electionwatch.config import load_env, load_races, parse_env_line
from electionwatch.__main__ import validate_env


def test_env_files_accept_colon_and_equals_without_overriding(monkeypatch, tmp_path):
    first = tmp_path / "first.env"
    second = tmp_path / "second.env"
    first.write_text("DISCORD_TOKEN: from-file\nMONGO_URI=mongodb://file\n", encoding="utf-8")
    second.write_text("MONGO_DB: election_test\n", encoding="utf-8")
    monkeypatch.setenv("EW_ENV_FILES", f"{first},{second}")
    monkeypatch.setenv("DISCORD_TOKEN", "real-environment")
    monkeypatch.delenv("MONGO_URI", raising=False)
    monkeypatch.delenv("MONGO_DB", raising=False)

    loaded = load_env()
    assert loaded == [first, second]
    import os

    assert os.environ["DISCORD_TOKEN"] == "real-environment"
    assert os.environ["MONGO_URI"] == "mongodb://file"
    assert os.environ["MONGO_DB"] == "election_test"
    assert parse_env_line("# secret-like comments are ignored") is None
    assert parse_env_line("not an assignment") is None


def test_validate_env_reports_names_not_values():
    assert validate_env({}) == ["DISCORD_TOKEN", "MONGO_URI"]
    assert validate_env(
        {"DISCORD_TOKEN": "not-printed", "MONGO_URI": "not-printed", "DEV_GUILD_ID": "bad"}
    ) == ["DEV_GUILD_ID"]
    assert validate_env(
        {"DISCORD_TOKEN": "not-printed", "MONGO_URI": "not-printed", "DEV_GUILD_ID": "123"}
    ) == []


def test_load_races_validates_and_canonicalizes(tmp_path):
    path = tmp_path / "races.yaml"
    path.write_text(
        yaml.safe_dump({"important": [{"key": "NC-1", "label": "NC first"}, {"key": "NC-01"}]}),
        encoding="utf-8",
    )
    races = load_races(path)
    assert len(races) == 1
    assert str(races[0].key) == "NC-01" and races[0].label == "NC first"
