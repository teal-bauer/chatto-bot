"""Admin commands — restricted to configured admin users."""

from __future__ import annotations

import logging

from chatto_bot import Bot, Cog, Context, command

logger = logging.getLogger(__name__)


class Admin(Cog):

    @command(desc="List available spaces", admin=True)
    async def spaces(self, ctx: Context):
        try:
            all_spaces = await self.bot.client.get_spaces()
        except Exception as e:
            await ctx.reply(f"Error: {e}")
            return
        if not all_spaces:
            await ctx.reply("No spaces visible.")
            return
        subscribed = set(self.bot._all_spaces)
        lines = ["**Spaces:**"]
        for s in all_spaces:
            mark = " (subscribed)" if s["id"] in subscribed else ""
            member = " [joined]" if s.get("viewerIsMember") else ""
            lines.append(f"- `{s['id']}` — {s['name']}{member}{mark}")
        await ctx.reply("\n".join(lines))

    @command(desc="List rooms in a space", admin=True)
    async def rooms(self, ctx: Context, space_id: str = ""):
        if not space_id:
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
        bot_id = self.bot.user.id if self.bot.user else ""
        lines = [f"**Rooms in `{space_id}`:**"]
        for r in rooms:
            members = r.get("members", [])
            is_member = any(m["user"]["id"] == bot_id for m in members)
            mark = " [joined]" if is_member else ""
            lines.append(f"- `{r['id']}` — {r['name']}{mark}")
        await ctx.reply("\n".join(lines))

    @command(desc="Join a room", admin=True)
    async def join(self, ctx: Context, args: str = ""):
        parts = args.split()
        if len(parts) == 1:
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

    @command(desc="Leave a room", admin=True)
    async def leave(self, ctx: Context, args: str = ""):
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
