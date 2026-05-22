"""Admin commands, restricted to configured admin users."""

from __future__ import annotations

import logging

from chatto_bot import Bot, Cog, Context, command

logger = logging.getLogger(__name__)


class Admin(Cog):

    @command(desc="Manage rooms: list, join, leave", admin=True)
    async def rooms(self, ctx: Context, args: str = ""):
        parts = args.split()
        sub = parts[0].lower() if parts else "list"

        if sub == "list" or not parts:
            await self._rooms_list(ctx)
        elif sub == "join":
            if len(parts) < 2:
                await ctx.reply("Usage: `!rooms join <room_id>`")
                return
            await self._rooms_join(ctx, parts[1])
        elif sub == "leave":
            if len(parts) < 2:
                await ctx.reply("Usage: `!rooms leave <room_id>`")
                return
            await self._rooms_leave(ctx, parts[1])
        else:
            await ctx.reply("Usage: `!rooms [list|join <id>|leave <id>]`")

    async def _rooms_list(self, ctx: Context):
        try:
            rooms = await self.bot.client.get_rooms()
        except Exception as e:
            await ctx.reply(f"Error: {e}")
            return
        if not rooms:
            await ctx.reply("No rooms visible.")
            return
        lines = ["**Rooms:**"]
        for r in rooms:
            mark = " [joined]" if r.get("joined") else ""
            kind = r.get("type", "")
            kind_str = f" ({kind.lower()})" if kind else ""
            lines.append(f"- `{r['id']}` — {r['name']}{kind_str}{mark}")
        await ctx.reply("\n".join(lines))

    async def _rooms_join(self, ctx: Context, room_id: str):
        try:
            await self.bot.client.join_room(room_id)
            await ctx.reply(f"Joined room `{room_id}`.")
        except Exception as e:
            await ctx.reply(f"Error: {e}")

    async def _rooms_leave(self, ctx: Context, room_id: str):
        try:
            await self.bot.client.leave_room(room_id)
            await ctx.reply(f"Left room `{room_id}`.")
        except Exception as e:
            await ctx.reply(f"Error: {e}")


async def setup(bot: Bot):
    await bot.add_cog(Admin(bot))
