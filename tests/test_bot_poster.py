from types import SimpleNamespace

import discord
import pytest

from electionwatch.bot.poster import DiscordPoster, render_embed
from electionwatch.poster import Post


@pytest.mark.parametrize(
    ("line", "expected"),
    [("🔴 Republican", discord.Color.red()), ("🔵 Democrat", discord.Color.blue()), ("Other", discord.Color.light_gray())],
)
def test_embed_colour_and_content(line, expected):
    embed = render_embed(Post("results", "TX SEN", "Texas Senate", [line], footer="Source: test"))
    assert embed.color == expected
    assert embed.description == line
    assert embed.footer.text == "Source: test"


@pytest.mark.asyncio
async def test_discord_poster_sends_one_embed():
    messages = []
    channel = SimpleNamespace(send=lambda **kwargs: send(messages, kwargs))
    client = SimpleNamespace(get_channel=lambda channel_id: channel)
    poster = DiscordPoster(client)
    await poster.post(1, 2, Post("call", "TX SEN", "Texas Senate", ["Called"]))
    assert len(messages) == 1 and isinstance(messages[0]["embed"], discord.Embed)


async def send(messages, kwargs):
    messages.append(kwargs)
