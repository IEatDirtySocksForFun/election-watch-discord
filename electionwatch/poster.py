"""Seam between the poller and Discord. The poller builds Posts; a Poster delivers them.

DiscordPoster (electionwatch/bot/) renders a Post as an embed. The replay sandbox uses a
RecordingPoster that just collects them, so the whole pipeline runs without Discord.

The poller is constructed as:

    Poller(store: Store, sources: dict[str, list[Source]], poster: Poster,
           clock: Callable[[], datetime], http: httpx.AsyncClient)
    await poller.tick()      # one cycle; the bot calls it every 60 s, replay calls it per
                             # simulated minute after advancing its fake clock
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

PostKind = Literal["results", "call", "turnout", "exitpoll", "odds_swing"]


@dataclass
class Post:
    kind: PostKind
    race: str                      # canonical race key string, "TX SEN"
    title: str                     # "Texas Senate"
    lines: list[str]               # body lines, already formatted (no markdown tables)
    url: str | None = None         # source link
    color: int | None = None       # embed colour; None = by leading party
    footer: str | None = None      # "Source: Texas SOS · 62% expected vote · 9:41 PM CT"
    fields: list[tuple[str, str]] = field(default_factory=list)


class Poster(Protocol):
    async def post(self, guild_id: int, channel_id: int, post: Post) -> None: ...


class RecordingPoster:
    def __init__(self) -> None:
        self.sent: list[tuple[int, int, Post]] = []

    async def post(self, guild_id: int, channel_id: int, post: Post) -> None:
        self.sent.append((guild_id, channel_id, post))
