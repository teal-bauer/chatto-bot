"""Auto-generated help command from registered command metadata."""

from chatto_bot import Bot, Cog, Context, command


class Help(Cog):
    @command(desc="Show available commands")
    async def help(self, ctx: Context, command_name: str = ""):
        if command_name:
            cmd = self.bot.get_command(command_name)
            if not cmd:
                await ctx.reply(f"Unknown command: `{command_name}`")
                return

            sig = cmd.signature
            usage = f"`{self.bot.config.prefix}{cmd.name}"
            if sig:
                usage += f" {sig}"
            usage += "`"

            lines = [f"**{cmd.name}**"]
            if cmd.help_text:
                lines.append(cmd.help_text)
            lines.append(f"Usage: {usage}")
            if cmd.aliases:
                lines.append(f"Aliases: {', '.join(cmd.aliases)}")

            await ctx.reply("\n".join(lines))
        else:
            prefix = self.bot.config.prefix
            lines = ["**Available commands:**"]
            for cmd in sorted(self.bot.commands, key=lambda c: c.name):
                desc = cmd.help_text or "No description"
                lines.append(f"- `{prefix}{cmd.name}` — {desc}")
            lines.append(f"\nUse `{prefix}help <command>` for details.")
            await ctx.reply("\n".join(lines))


async def setup(bot: Bot):
    await bot.add_cog(Help(bot))
