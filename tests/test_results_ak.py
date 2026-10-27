from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level, RaceKey
from electionwatch.racekeys import format_key
from electionwatch.sources.results import ak_doe
from electionwatch.sources.results.ak_doe import parse_ak, race_key

FIX = Path(__file__).parent / "fixtures" / "ak_doe"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def parse():
    return parse_ak((FIX / "ENRbyPrecinct.csv").read_text(encoding="utf-8"),
                    fetched_at=AT, url="https://www.elections.alaska.gov/results/24GENR/")


def by_key():
    return {format_key(r.key): r for r in parse()}


def test_us_house_is_at_large_and_sums_precincts():
    house = by_key()["AK-AL"]
    assert house.key.level is Level.HOUSE
    assert house.key.district == "AL"
    assert house.candidates[0].name == "Nick Begich"
    assert house.candidates[0].party == "REP"
    # Every precinct row for the contest is summed, and percentages are filled in.
    assert house.total_votes == sum(x.votes for x in house.candidates)
    assert house.candidates[0].pct == round(
        100.0 * house.candidates[0].votes / house.total_votes, 2)
    assert house.url == "https://www.elections.alaska.gov/results/24GENR/"


def test_lettered_senate_and_numbered_house_districts():
    keys = by_key()
    assert keys["AK SD-B"].key.district == "B"
    assert keys["AK HD-01"].key.level is Level.STATE
    assert keys["AK HD-01"].key.district == "01"


def test_president_and_ballot_measure_are_local():
    keys = by_key()
    assert keys["AK LOCAL us-president"].candidates
    assert "AK LOCAL bm-1-23amls" in keys
    assert "AK LOCAL supreme-court-borghesan" in keys


def test_titles_are_the_states_own():
    house = by_key()["AK-AL"]
    assert house.title == "U.S. Representative"


def test_race_key_mapping_for_2026_offices():
    assert format_key(race_key("U.S. Senator")) == "AK SEN"
    assert format_key(race_key("Governor / Lt. Governor")) == "AK GOV"
    assert format_key(race_key("House District 40")) == "AK HD-40"
    assert format_key(race_key("Senate District T")) == "AK SD-T"
    assert format_key(race_key("U.S. Representative")) == "AK-AL"


def test_empty_payload_is_not_an_error():
    assert parse_ak("", fetched_at=AT) == []


def _subject_text(ca: dict) -> str:
    """'GlobalSign GCC R46 OV TLS CA 2025' from get_ca_certs()'s nested tuples."""
    return " ".join(
        v for rdn in ca.get("subject") or () for _k, v in rdn
    ) if not isinstance(ca.get("subject"), str) else ca["subject"]


def test_tls_context_adds_the_missing_intermediate_on_top_of_certifi():
    # www.elections.alaska.gov sends a leaf issued by "GlobalSign GCC R46 OV TLS CA 2025"
    # but only the older "GlobalSign RSA OV SSL CA 2018" intermediate, so the default
    # bundle alone cannot build a chain (CERTIFICATE_VERIFY_FAILED).
    import ssl
    import certifi

    plain = ssl.create_default_context(cafile=certifi.where()).get_ca_certs()
    ctx = ak_doe.tls_context()
    extra = [ca for ca in ctx.get_ca_certs() if "GlobalSign GCC R46 OV TLS CA 2025" in _subject_text(ca)]
    assert len(extra) == 1
    assert len(ctx.get_ca_certs()) == len(plain) + 1
    # It is the intermediate, not a root: its issuer is the GlobalSign Root R46.
    assert "GlobalSign Root R46" in _subject_text({"subject": extra[0]["issuer"]})
    assert ak_doe.tls_context() is ctx  # built once


def test_tls_client_verifies_with_that_context_and_keeps_verification_on(monkeypatch):
    recorded = {}

    class Recorder:
        def __init__(self, **kwargs):
            recorded.update(kwargs)

    class Stub:
        timeout = httpx.Timeout(10.0)
        headers = {"User-Agent": "election-watch-test/1.0"}

    monkeypatch.setattr(httpx, "AsyncClient", Recorder)
    ak_doe.tls_client(Stub())

    assert recorded["verify"] is ak_doe.tls_context()
    assert not isinstance(recorded["verify"], bool)  # verify=False would skip the chain
    assert recorded["timeout"] is Stub.timeout
    assert recorded["headers"]["User-Agent"] == "election-watch-test/1.0"


async def test_fetch_uses_the_ak_tls_client(monkeypatch):
    # The shared client verifies against the normal bundle, so fetch must go through
    # the AK-specific one; here it is a passthrough onto the mocked transport.
    import contextlib

    served = (FIX / "ENRbyPrecinct.csv").read_text(encoding="utf-8")
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, text=served)

    @contextlib.asynccontextmanager
    async def passthrough(client):
        yield client

    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    monkeypatch.setattr(ak_doe, "tls_client", passthrough)

    key = RaceKey("AK", Level.HOUSE, "HOUSE", "AL")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await ak_doe.AkDoe().fetch([key], client)
    assert seen == ["https://www.elections.alaska.gov/results/24GENR/ENRbyPrecinct.csv"]
    assert [r.key for r in results] == [key]
    assert results[0].candidates[0].name == "Nick Begich"
