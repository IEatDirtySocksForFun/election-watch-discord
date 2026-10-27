"""Discord rendering for the poller's Post seam."""
from __future__ import annotations

import discord

from electionwatch.poster import Post


# Discord's hard limits (https://discord.com/developers/docs/resources/channel#embed-object).
MAX_TITLE = 256
MAX_DESCRIPTION = 4096
MAX_FIELD_NAME = 256
MAX_FIELD_VALUE = 1024
MAX_FOOTER = 2048
MAX_EMBED_TOTAL = 6000      # title + description + fields + footer + author, combined


def clip(text: str, limit: int) -> str:
    """Cut text to a Discord limit so one huge race cannot drop the whole post."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def render_embed(post: Post) -> discord.Embed:
    if post.color is not None:
        color = discord.Color(post.color)
    elif post.lines and post.lines[0].startswith("🔴"):
        color = discord.Color.red()
    elif post.lines and post.lines[0].startswith("🔵"):
        color = discord.Color.blue()
    else:
        color = discord.Color.light_gray()
    embed = discord.Embed(
        title=clip(post.title, MAX_TITLE),
        description=clip("\n".join(post.lines), MAX_DESCRIPTION),
        url=post.url,
        color=color,
    )
    for name, value in post.fields:
        embed.add_field(
            name=clip(name, MAX_FIELD_NAME), value=clip(value, MAX_FIELD_VALUE), inline=False
        )
    if post.footer:
        embed.set_footer(text=clip(post.footer, MAX_FOOTER))
    return embed


class DiscordPoster:
    def __init__(self, client: discord.Client):
        self.client = client

    async def post(self, guild_id: int, channel_id: int, post: Post) -> None:
        channel = self.client.get_channel(channel_id)
        if channel is None:
            channel = await self.client.fetch_channel(channel_id)
        await channel.send(embed=render_embed(post))
