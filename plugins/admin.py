"""Admin commands — restricted to configured admin users."""

from __future__ import annotations

import logging

from chatto_bot import Bot, Cog, Context, command

logger = logging.getLogger(__name__)


class Admin(Cog):

    @command(desc="Manage spaces: list, join, leave", admin=True)
    async def spaces(self, ctx: Context, args: str = ""):
        parts = args.split()
        sub = parts[0].lower() if parts else "list"

        if sub == "list" or not parts:
            await self._spaces_list(ctx)
        elif sub == "join":
            if len(parts) < 2:
                await ctx.reply("Usage: `!spaces join <space_id>`")
                return
            await self._spaces_join(ctx, parts[1])
        elif sub == "leave":
            if len(parts) < 2:
                await ctx.reply("Usage: `!spaces leave <space_id>`")
                return
            await self._spaces_leave(ctx, parts[1])
        else:
            await ctx.reply("Usage: `!spaces [list|join <id>|leave <id>]`")

    async def _spaces_list(self, ctx: Context):
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

    async def _spaces_join(self, ctx: Context, space_id: str):
        try:
            await self.bot.client.join_space(space_id)
            await self.bot.subscribe_space(space_id)
            await ctx.reply(f"Joined and subscribed to space `{space_id}`.")
        except Exception as e:
            await ctx.reply(f"Error: {e}")

    async def _spaces_leave(self, ctx: Context, space_id: str):
        try:
            await self.bot.client.leave_space(space_id)
            await self.bot.unsubscribe_space(space_id)
            await ctx.reply(f"Left and unsubscribed from space `{space_id}`.")
        except Exception as e:
            await ctx.reply(f"Error: {e}")

    @command(desc="Manage rooms: list, join, leave", admin=True)
    async def rooms(self, ctx: Context, args: str = ""):
        parts = args.split()
        sub = parts[0].lower() if parts else "list"

        if sub == "list" or not parts:
            space_id = parts[1] if len(parts) > 1 else ""
            await self._rooms_list(ctx, space_id)
        elif sub == "join":
            if len(parts) < 2:
                await ctx.reply("Usage: `!rooms join <room_id> [space_id]`")
                return
            space_id = parts[2] if len(parts) > 2 else ""
            await self._rooms_join(ctx, parts[1], space_id)
        elif sub == "leave":
            if len(parts) < 2:
                await ctx.reply("Usage: `!rooms leave <room_id> [space_id]`")
                return
            space_id = parts[2] if len(parts) > 2 else ""
            await self._rooms_leave(ctx, parts[1], space_id)
        else:
            await ctx.reply("Usage: `!rooms [list [space_id]|join <id>|leave <id>]`")

    async def _rooms_list(self, ctx: Context, space_id: str):
        if not space_id:
            if self.bot.config.spaces:
                space_id = self.bot.config.spaces[0]
            else:
                await ctx.reply("Usage: `!rooms list <space_id>`")
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
            mark = " [joined]" if r.get("joined") else ""
            lines.append(f"- `{r['id']}` — {r['name']}{mark}")
        await ctx.reply("\n".join(lines))

    async def _rooms_join(self, ctx: Context, room_id: str, space_id: str):
        if not space_id:
            if self.bot.config.spaces:
                space_id = self.bot.config.spaces[0]
            else:
                await ctx.reply("Usage: `!rooms join <room_id> <space_id>`")
                return
        try:
            await self.bot.client.join_room(space_id, room_id)
            await ctx.reply(f"Joined room `{room_id}` in space `{space_id}`.")
        except Exception as e:
            await ctx.reply(f"Error: {e}")

    async def _rooms_leave(self, ctx: Context, room_id: str, space_id: str):
        if not space_id:
            if self.bot.config.spaces:
                space_id = self.bot.config.spaces[0]
            else:
                await ctx.reply("Usage: `!rooms leave <room_id> <space_id>`")
                return
        try:
            await self.bot.client.leave_room(space_id, room_id)
            await ctx.reply(f"Left room `{room_id}` in space `{space_id}`.")
        except Exception as e:
            await ctx.reply(f"Error: {e}")


async def setup(bot: Bot):
    await bot.add_cog(Admin(bot))
