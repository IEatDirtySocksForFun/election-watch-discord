from types import SimpleNamespace

import discord
import pytest

from electionwatch.bot.poster import DiscordPoster, render_embed
from electionwatch.poster import Post


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("🔴 Republican", discord.Color.red()),
        ("🔵 Democrat", discord.Color.blue()),
        ("Other", discord.Color.light_gray()),
    ],
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


def test_huge_posts_are_clipped_to_discord_limits():
    post = Post(
        "results",
        "TX LOCAL harris-county-judge",
        "L" * 300,
        ["candidate " + str(index) + " " + "x" * 60 for index in range(200)],
        footer="f" * 3000,
        fields=[("n" * 300, "v" * 2000)],
    )
    embed = render_embed(post)
    assert len(embed.title) <= 256 and embed.title.endswith("…")
    assert len(embed.description) <= 4096 and embed.description.endswith("…")
    assert len(embed.footer.text) <= 2048
    assert len(embed.fields[0].name) <= 256 and len(embed.fields[0].value) <= 1024


async def send(messages, kwargs):
    messages.append(kwargs)
