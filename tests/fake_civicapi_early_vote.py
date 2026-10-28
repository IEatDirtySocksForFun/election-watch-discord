"""Serves the recorded civicAPI early-vote responses (tests/fixtures/civicapi_early_vote/) over
an httpx MockTransport, so command and payload tests run the real client without a network.

Anything not recorded answers 404 with civicAPI's real "not found" body (a state without data).
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from electionwatch.sources.early_vote import EarlyVoteClient

FIX = Path(__file__).parent / "fixtures" / "civicapi_early_vote"
BASE = "/api/v2/early-vote/"

#: request path (+ sorted query) -> fixture file
ROUTES = {
    "US/returned": "us_returned.json",
    "US/requested": "us_requested.json",
    "US/inperson": "us_inperson.json",
    "US/grace": "us_grace.json",
    "NC/returned": "nc_returned.json",
    "NC/inperson": "nc_inperson.json",
    "NC/capabilities": "nc_capabilities.json",
    "NC/returned/demographics?by=party": "nc_returned_demographics_party.json",
    "GA/returned": "ga_returned.json",
    "GA/inperson": "ga_inperson.json",
    "GA/capabilities": "ga_capabilities.json",
    "GA/inperson/demographics?by=age_range": "ga_inperson_demographics_age_range.json",
    "TN/capabilities": "tn_capabilities.json",
    "historical/2022/NC/returned?date=2022-11-01": "historical_2022_nc_returned.json",
    "historical/2024/NC/returned?date=2024-10-29": "historical_2024_nc_returned.json",
}


def load(name: str):
    return json.loads((FIX / name).read_text())


def route(request: httpx.Request) -> str:
    path = request.url.path.removeprefix(BASE)
    query = "&".join(f"{k}={v}" for k, v in sorted(request.url.params.multi_items()))
    return f"{path}?{query}" if query else path


def handler(request: httpx.Request) -> httpx.Response:
    name = ROUTES.get(route(request))
    if name is None:
        return httpx.Response(404, json=load("not_found.json"))
    return httpx.Response(200, json=load(name))


def mock_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def fixture_client(http: httpx.AsyncClient | None = None, **kwargs) -> EarlyVoteClient:
    """An EarlyVoteClient over the recorded responses, without the 1 s request spacing."""
    kwargs.setdefault("min_interval", 0)
    return EarlyVoteClient(http or mock_http_client(), **kwargs)
