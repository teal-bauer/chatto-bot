"""Simple ping/pong example plugin."""

from chatto_bot import Bot, Cog, Context, command


class Ping(Cog):
    @command(desc="Check if the bot is alive")
    async def ping(self, ctx: Context):
        await ctx.reply("Pong!")


async def setup(bot: Bot):
    await bot.add_cog(Ping(bot))
