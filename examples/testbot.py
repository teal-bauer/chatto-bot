"""Test bot for the bot-testing room with basic commands."""

import random
import time

from chatto_bot import Bot, Context

bot = Bot(
    spaces=["SjH0ry5SFndJrZy"],
    prefix="!",
)

_start_time = time.monotonic()


@bot.command(desc="Check if the bot is alive")
async def ping(ctx: Context):
    await ctx.reply("Pong!")


@bot.command(desc="Roll dice (D&D notation)", aliases=["dice", "r"])
async def roll(ctx: Context, expression: str = "1d6"):
    """Roll dice: !roll 2d8, !roll 1d100+2d6+4, !roll 3d6-2"""
    import re

    expr = expression.replace(" ", "").lower()
    if not re.fullmatch(r"[0-9d+\-]+", expr):
        await ctx.reply("Invalid dice expression.")
        return

    total = 0
    parts: list[str] = []

    for match in re.finditer(r"([+\-]?)(\d*)d(\d+)|([+\-]?)(\d+)", expr):
        if match.group(3):  # dice term: NdS
            sign_str, count_str, sides_str = match.group(1), match.group(2), match.group(3)
            sign = -1 if sign_str == "-" else 1
            count = int(count_str) if count_str else 1
            sides = int(sides_str)
            if count > 100 or sides > 10000:
                await ctx.reply("Too many dice or sides.")
                return
            rolls = [random.randint(1, sides) for _ in range(count)]
            subtotal = sum(rolls) * sign
            total += subtotal
            roll_str = ", ".join(str(r) for r in rolls)
            prefix = "- " if sign == -1 else ""
            parts.append(f"{prefix}{count}d{sides}: [{roll_str}] = {subtotal}")
        elif match.group(5):  # flat modifier
            sign_str, num_str = match.group(4), match.group(5)
            sign = -1 if sign_str == "-" else 1
            val = int(num_str) * sign
            total += val
            parts.append(f"modifier: {val:+d}")

    detail = "\n".join(parts)
    await ctx.reply(f"{detail}\n**Total: {total}**")


@bot.command(desc="Echo back your message")
async def echo(ctx: Context, message: str = ""):
    if message:
        await ctx.reply(message)
    else:
        await ctx.reply("Usage: !echo <message>")


@bot.command(desc="Flip a coin", aliases=["coin"])
async def flip(ctx: Context):
    result = random.choice(["Heads", "Tails"])
    await ctx.reply(f"**{result}!**")


@bot.command(desc="Pick from a list of choices")
async def choose(ctx: Context, choices: str = ""):
    """Pick randomly from comma-separated choices. Usage: !choose a, b, c"""
    if not choices:
        await ctx.reply("Usage: !choose option1, option2, option3")
        return
    options = [o.strip() for o in choices.split(",") if o.strip()]
    if len(options) < 2:
        await ctx.reply("Give me at least 2 choices separated by commas.")
        return
    await ctx.reply(f"I choose: **{random.choice(options)}**")


@bot.command(desc="Show bot uptime")
async def uptime(ctx: Context):
    elapsed = time.monotonic() - _start_time
    hours, remainder = divmod(int(elapsed), 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    await ctx.reply(f"Uptime: {' '.join(parts)}")


@bot.command(desc="React to the triggering message")
async def react(ctx: Context, emoji: str = ""):
    if not emoji:
        await ctx.reply("Usage: !react <emoji>")
        return
    await ctx.react(emoji)


@bot.command(desc="Show available commands")
async def help(ctx: Context, command_name: str = ""):
    if command_name:
        cmd = bot.get_command(command_name)
        if not cmd:
            await ctx.reply(f"Unknown command: `{command_name}`")
            return
        sig = cmd.signature
        usage = f"`!{cmd.name}"
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
        lines = ["**Available commands:**"]
        for cmd in sorted(bot.commands, key=lambda c: c.name):
            desc = cmd.help_text or "No description"
            lines.append(f"- `!{cmd.name}` — {desc}")
        await ctx.reply("\n".join(lines))


@bot.on_event("message_posted")
async def react_to_sentiment(ctx: Context):
    body = (ctx.body or "").lower().strip().rstrip("!.")
    if body == "adequate bot" and ctx.actor and ctx.actor.login.lower() == "hmans":
        await ctx.react("🎉")
    elif body in ("good bot", "nice bot", "thanks bot", "thank you bot"):
        await ctx.react(random.choice(["❤️", "👍"]))
    elif body in ("bad bot", "boo", "booo", "boooo"):
        await ctx.react("😢")
    else:
        raw = (ctx.body or "").lower()
        mentions_bot = bot.user and f"@{bot.user.login.lower()}" in raw
        if mentions_bot and any(w in raw for w in ("no", "wrong", "bad", "boo")):
            await ctx.react("😢")


@bot.middleware
async def ignore_self(ctx, next):
    """Don't process the bot's own messages."""
    if ctx.actor and bot.user and ctx.actor.id != bot.user.id:
        await next()


if __name__ == "__main__":
    bot.run()
