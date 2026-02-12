"""Timed reminders plugin — set, list, and cancel reminders."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from chatto_bot import Bot, Cog, Context, command

if TYPE_CHECKING:
    from chatto_bot.client import Client

logger = logging.getLogger(__name__)

REMINDERS_PATH = Path(".chatto-bot-reminders.json")
CHECK_INTERVAL = 30  # seconds


def _short_id() -> str:
    return os.urandom(4).hex()


_NAMED_TIMES = {
    "morning": (9, 0),
    "noon": (12, 0),
    "afternoon": (14, 0),
    "evening": (18, 0),
    "night": (22, 0),
}

_NAMED_TIMES_RE = "|".join(_NAMED_TIMES)


def _resolve_time(spec: str) -> tuple[int, int]:
    """Resolve a time spec (HH:MM or named time) to (hour, minute)."""
    if spec in _NAMED_TIMES:
        return _NAMED_TIMES[spec]
    h, m = spec.split(":")
    return int(h), int(m)


def _parse_remind_args(text: str) -> tuple[str, datetime | None, str]:
    """Parse remind command arguments.

    Returns (target, due_at, message).
    target is 'me' or a display name (without @).
    due_at is None if parsing fails.
    """
    text = text.strip()
    if not text:
        raise ValueError("No arguments provided")

    # Extract target: 'me' or '@displayname' (everything before on/at/in/to/named-time)
    named_lookahead = "|".join(rf"{n}\s" for n in _NAMED_TIMES)
    target_match = re.match(
        rf"(me|@\S+(?:\s+\S+)*?)\s+(?=on\s|at\s|in\s|to\s|{named_lookahead})", text, re.IGNORECASE
    )
    if not target_match:
        # Maybe it's just "me to <msg>" with no time spec
        target_match = re.match(r"(me|@\S+(?:\s+\S+)*?)\s+to\s", text, re.IGNORECASE)
        if not target_match:
            raise ValueError(
                "Could not parse target. Use `me` or `@username`."
            )

    target_raw = target_match.group(1)
    target = target_raw.lstrip("@") if target_raw.lower() != "me" else "me"
    rest = text[target_match.end() :].strip()

    # If we consumed up to 'to', rest is just everything after that prefix match
    # Re-parse from rest for time specs
    # But first, if target_match ended at 'to ', message is rest and no time
    if text[target_match.start(0) : target_match.end(0)].rstrip().endswith("to"):
        # No time spec, "me to <msg>" — default to next morning
        h, m = _NAMED_TIMES["morning"]
        due = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if due <= now:
            due += timedelta(days=1)
        return target, due, rest

    now = datetime.now(timezone.utc)
    due_date = None
    due_time = None

    # Try relative: in <N><unit>
    rel_match = re.match(r"in\s+(\d+)\s*(m|min|mins|minutes?|h|hrs?|hours?|d|days?)\s+to\s+(.+)", rest, re.IGNORECASE)
    if rel_match:
        amount = int(rel_match.group(1))
        unit = rel_match.group(2)[0].lower()
        message = rel_match.group(3).strip()
        if unit == "m":
            delta = timedelta(minutes=amount)
        elif unit == "h":
            delta = timedelta(hours=amount)
        else:
            delta = timedelta(days=amount)
        return target, now + delta, message

    # Time pattern: HH:MM or named time (morning, noon, afternoon, evening, night)
    time_pat = rf"(\d{{1,2}}:\d{{2}}|{_NAMED_TIMES_RE})"

    # Try absolute: on YYYY-MM-DD [at <time>] to <msg>
    abs_match = re.match(
        rf"on\s+(\d{{4}}-\d{{2}}-\d{{2}})(?:\s+at\s+{time_pat})?\s+to\s+(.+)",
        rest, re.IGNORECASE,
    )
    if abs_match:
        due_date = datetime.strptime(abs_match.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if abs_match.group(2):
            h, m = _resolve_time(abs_match.group(2).lower())
            due_date = due_date.replace(hour=h, minute=m)
        else:
            h, m = _NAMED_TIMES["morning"]
            due_date = due_date.replace(hour=h, minute=m)
        return target, due_date, abs_match.group(3).strip()

    # Try time only: at <time> to <msg>
    time_match = re.match(rf"at\s+{time_pat}\s+to\s+(.+)", rest, re.IGNORECASE)
    if time_match:
        h, m = _resolve_time(time_match.group(1).lower())
        due = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if due <= now:
            due += timedelta(days=1)
        return target, due, time_match.group(2).strip()

    # Try bare named time: <named_time> to <msg> (without "at")
    named_match = re.match(rf"({_NAMED_TIMES_RE})\s+to\s+(.+)", rest, re.IGNORECASE)
    if named_match:
        h, m = _NAMED_TIMES[named_match.group(1).lower()]
        due = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if due <= now:
            due += timedelta(days=1)
        return target, due, named_match.group(2).strip()

    # Fallback: no time spec, default to next morning
    to_match = re.match(r"to\s+(.+)", rest, re.IGNORECASE)
    if to_match:
        h, m = _NAMED_TIMES["morning"]
        due = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if due <= now:
            due += timedelta(days=1)
        return target, due, to_match.group(1).strip()

    raise ValueError("Could not parse time. Use `in <N>m/h/d`, `on YYYY-MM-DD`, `at HH:MM`, or a named time (morning/noon/afternoon/evening/night).")


class Remind(Cog):
    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self._checker_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        self._checker_task = asyncio.create_task(self._checker_loop())
        logger.info("Reminder checker started")

    async def cog_unload(self) -> None:
        if self._checker_task and not self._checker_task.done():
            self._checker_task.cancel()
            try:
                await self._checker_task
            except asyncio.CancelledError:
                pass
        logger.info("Reminder checker stopped")

    # --- Storage ---

    def _load(self) -> list[dict]:
        if REMINDERS_PATH.exists():
            try:
                return json.loads(REMINDERS_PATH.read_text())
            except Exception:
                logger.exception("Failed to load reminders")
        return []

    def _save(self, reminders: list[dict]) -> None:
        REMINDERS_PATH.write_text(json.dumps(reminders, indent=2))

    # --- User resolution ---

    async def _resolve_user(self, name: str, space_id: str) -> dict | None:
        """Resolve a display name to a user dict via space member search."""
        if space_id == "DM":
            return None
        try:
            members = await self.bot.client.search_space_members(space_id, name)
        except Exception:
            logger.debug("Member search failed for %r in %s", name, space_id)
            return None
        # Case-insensitive match on displayName
        for user in members:
            if user["displayName"].lower() == name.lower():
                return user
        # If only one result, use it
        if len(members) == 1:
            return members[0]
        return None

    # --- Commands ---

    @command(desc="Set a reminder", aliases=["rm"])
    async def remind(self, ctx: Context, args: str = ""):
        """Usage: !remind me/\@user [on YYYY-MM-DD] [at HH:MM] [in Nm/h/d] to <message>"""
        if not args:
            await ctx.reply(
                "Usage: `!remind me in 5m to check the build`\n"
                "       `!remind me on 2025-03-01 at 14:00 to submit report`\n"
                "       `!remind @user in 1h to review PR`\n"
                "       `!remind cancel <id>` — cancel a reminder\n"
                "See also: `!reminders` to list pending reminders."
            )
            return

        # Handle cancel subcommand
        if args.startswith("cancel "):
            short_id = args[7:].strip()
            return await self._cancel(ctx, short_id)

        try:
            target_name, due_at, message = _parse_remind_args(args)
        except ValueError as e:
            await ctx.reply(f"Could not parse reminder: {e}")
            return

        if not message:
            await ctx.reply("Missing message. Add `to <message>` at the end.")
            return

        # Resolve target user
        actor = ctx.actor
        if not actor:
            return

        if target_name == "me":
            target_id = actor.id
            target_display = actor.display_name
            target_login = actor.login
        else:
            user = await self._resolve_user(target_name, ctx.space_id)
            if not user:
                await ctx.reply(f"Could not find user `{target_name}` in this space.")
                return
            target_id = user["id"]
            target_display = user["displayName"]
            target_login = user["login"]

        reminder = {
            "id": _short_id(),
            "creator_id": actor.id,
            "target_id": target_id,
            "target_name": target_display,
            "target_login": target_login,
            "space_id": ctx.space_id,
            "room_id": ctx.room_id,
            "due_at": due_at.isoformat(),
            "message": message,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        reminders = self._load()
        reminders.append(reminder)
        self._save(reminders)

        due_str = due_at.strftime("%Y-%m-%d %H:%M UTC")
        target_str = "you" if target_id == actor.id else f"**{target_display}**"
        await ctx.react("⏰")
        await ctx.reply(
            f"Reminder set for {target_str} at {due_str}: {message} (id: `{reminder['id']}`)"
        )

    @command(desc="List your pending reminders")
    async def reminders(self, ctx: Context):
        actor = ctx.actor
        if not actor:
            return

        all_reminders = self._load()
        mine = [
            r for r in all_reminders
            if r["creator_id"] == actor.id or r["target_id"] == actor.id
        ]

        if not mine:
            await ctx.reply("You have no pending reminders.")
            return

        lines = ["**Your pending reminders:**"]
        for r in sorted(mine, key=lambda x: x["due_at"]):
            due = datetime.fromisoformat(r["due_at"]).strftime("%Y-%m-%d %H:%M UTC")
            target = "you" if r["target_id"] == actor.id else f"@{r['target_name']}"
            lines.append(f"- `{r['id']}` — {due} → {target}: {r['message']}")

        await ctx.reply("\n".join(lines))

    async def _cancel(self, ctx: Context, short_id: str) -> None:
        actor = ctx.actor
        if not actor:
            return

        reminders = self._load()
        found = None
        for i, r in enumerate(reminders):
            if r["id"] == short_id:
                found = i
                break

        if found is None:
            await ctx.reply(f"No reminder found with id `{short_id}`.")
            return

        r = reminders[found]
        if r["creator_id"] != actor.id and r["target_id"] != actor.id:
            await ctx.reply("You can only cancel your own reminders.")
            return

        reminders.pop(found)
        self._save(reminders)
        await ctx.reply(f"Cancelled reminder `{short_id}`: {r['message']}")

    # --- Background checker ---

    async def _checker_loop(self) -> None:
        await asyncio.sleep(5)  # initial delay
        while True:
            try:
                await self._check_due()
            except Exception:
                logger.exception("Error in reminder checker")
            await asyncio.sleep(CHECK_INTERVAL)

    async def _check_due(self) -> None:
        reminders = self._load()
        now = datetime.now(timezone.utc)
        due = []
        remaining = []

        for r in reminders:
            due_at = datetime.fromisoformat(r["due_at"])
            if due_at <= now:
                due.append(r)
            else:
                remaining.append(r)

        if not due:
            return

        for r in due:
            try:
                login = r.get("target_login", r["target_name"])
                msg = f"⏰ @{login} reminder: {r['message']}"
                await self.bot.client.post_message(
                    r["space_id"], r["room_id"], msg
                )
            except Exception:
                logger.exception(
                    "Failed to deliver reminder %s in %s/%s",
                    r["id"], r["space_id"], r["room_id"],
                )

        self._save(remaining)


async def setup(bot: Bot):
    await bot.add_cog(Remind(bot))
