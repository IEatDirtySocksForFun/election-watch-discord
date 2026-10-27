"""Discord rendering for the poller's Post seam."""
from __future__ import annotations

import discord

from electionwatch.poster import Post


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
        title=post.title,
        description="\n".join(post.lines),
        url=post.url,
        color=color,
    )
    for name, value in post.fields:
        embed.add_field(name=name, value=value, inline=False)
    if post.footer:
        embed.set_footer(text=post.footer)
    return embed


class DiscordPoster:
    def __init__(self, client: discord.Client):
        self.client = client

    async def post(self, guild_id: int, channel_id: int, post: Post) -> None:
        channel = self.client.get_channel(channel_id)
        if channel is None:
            channel = await self.client.fetch_channel(channel_id)
        await channel.send(embed=render_embed(post))
