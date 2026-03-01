"""Admin commands — DM-only, restricted to configured admin users."""

from __future__ import annotations

import logging

from chatto_bot import Bot, Cog, Context, command

logger = logging.getLogger(__name__)


def _is_admin(ctx: Context) -> bool:
    actor = ctx.actor
    if not actor:
        return False
    return actor.login in ctx.bot.config.admins


class Admin(Cog):

    @command(desc="List spaces the bot is subscribed to", hidden=True)
    async def spaces(self, ctx: Context):
        if not _is_admin(ctx):
            return
        spaces = self.bot._all_spaces
        if not spaces:
            await ctx.reply("Not subscribed to any spaces.")
            return
        lines = ["**Subscribed spaces:**"]
        for sid in spaces:
            lines.append(f"- `{sid}`")
        await ctx.reply("\n".join(lines))

    @command(desc="List rooms in a space", hidden=True)
    async def rooms(self, ctx: Context, space_id: str = ""):
        if not _is_admin(ctx):
            return
        if not space_id:
            # Default to first configured space
            if self.bot.config.spaces:
                space_id = self.bot.config.spaces[0]
            else:
                await ctx.reply("Usage: `!rooms <space_id>`")
                return
        try:
            rooms = await self.bot.client.get_rooms(space_id)
        except Exception as e:
            await ctx.reply(f"Error: {e}")
            return
        if not rooms:
            await ctx.reply(f"No rooms found in space `{space_id}`.")
            return
        lines = [f"**Rooms in `{space_id}`:**"]
        for r in rooms:
            lines.append(f"- `{r['id']}` — {r['name']}")
        await ctx.reply("\n".join(lines))

    @command(desc="Join a room", hidden=True)
    async def join(self, ctx: Context, args: str = ""):
        if not _is_admin(ctx):
            return
        parts = args.split()
        if len(parts) == 1:
            # Just room ID, use first configured space
            if not self.bot.config.spaces:
                await ctx.reply("Usage: `!join <space_id> <room_id>`")
                return
            space_id = self.bot.config.spaces[0]
            room_id = parts[0]
        elif len(parts) == 2:
            space_id, room_id = parts
        else:
            await ctx.reply("Usage: `!join [space_id] <room_id>`")
            return
        try:
            await self.bot.client.join_room(space_id, room_id)
            await ctx.reply(f"Joined room `{room_id}` in space `{space_id}`.")
        except Exception as e:
            await ctx.reply(f"Error: {e}")

    @command(desc="Leave a room", hidden=True)
    async def leave(self, ctx: Context, args: str = ""):
        if not _is_admin(ctx):
            return
        parts = args.split()
        if len(parts) == 1:
            if not self.bot.config.spaces:
                await ctx.reply("Usage: `!leave <space_id> <room_id>`")
                return
            space_id = self.bot.config.spaces[0]
            room_id = parts[0]
        elif len(parts) == 2:
            space_id, room_id = parts
        else:
            await ctx.reply("Usage: `!leave [space_id] <room_id>`")
            return
        try:
            await self.bot.client.leave_room(space_id, room_id)
            await ctx.reply(f"Left room `{room_id}` in space `{space_id}`.")
        except Exception as e:
            await ctx.reply(f"Error: {e}")


async def setup(bot: Bot):
    await bot.add_cog(Admin(bot))
