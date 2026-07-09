"""Admin commands, restricted to configured admin users."""

from __future__ import annotations

import logging

from chatto_bot import Bot, ChattoError, Cog, Context, command

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
            rooms = await self.bot.client.list_rooms()
        except Exception as e:
            await ctx.reply(f"Error: {e}")
            return
        if not rooms:
            await ctx.reply("No rooms visible.")
            return
        lines = ["**Rooms:**"]
        for rws in rooms:
            room = rws.room
            if room is None:
                continue
            mark = " [joined]" if rws.viewer_state and rws.viewer_state.is_member else ""
            kind = room.kind.name if room.kind is not None else ""
            kind_str = f" ({kind.lower()})" if kind and kind != "UNSPECIFIED" else ""
            lines.append(f"- `{room.id}` — {room.name}{kind_str}{mark}")
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
        except ChattoError as e:
            if e.code == "failed_precondition":
                await ctx.reply(
                    f"Can't leave `{room_id}`: DM and universal rooms can't be left."
                )
            else:
                await ctx.reply(f"Error: {e}")
        except Exception as e:
            await ctx.reply(f"Error: {e}")


async def setup(bot: Bot):
    await bot.add_cog(Admin(bot))
