"""Bot class: the central orchestrator."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
import signal
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from ._pb.chatto.api.v1.rooms_pb import RoomKind
from .client import Client, Unauthenticated
from .cog import Cog
from .command import Command, CommandError
from .config import BotConfig
from .context import Context
from .event import EventHandler
from .hydrate import Hydrator
from .middleware import MiddlewareChain, MiddlewareFunc
from .realtime import Realtime, RealtimeStopped
from .transport import AuthError, Transport
from .usercache import UserCache
from .types import (
    EVENT_NAME_TO_TYPE,
    RoomEvent,
    User,
    event_name,
    format_cursor,
    normalize_presence_status,
    parse_envelope,
    warn_if_retired_event_name,
)

if TYPE_CHECKING:
    from ._pb.chatto.api.v1.room_directory_pb import RoomWithViewerState
    from ._pb.chatto.api.v1.room_timeline_pb import RoomTimelineEvent, RoomTimelineIncludes
    from ._pb.chatto.realtime.v1.realtime_pb import RealtimeEventEnvelope

logger = logging.getLogger(__name__)

# Cursor key used in the persistent state file. The API exposes a single
# global event stream, so one cursor covers everything.
_GLOBAL_CURSOR_KEY = "_global"

# Presence is transient server-side (no "stay ONLINE until told otherwise"),
# so it needs periodic refreshing or the bot drifts to OFFLINE while still
# very much running. The server expires a presence entry 60s after the last
# refresh, so this interval MUST stay comfortably under that TTL; 30s matches
# the server's own recommended client refresh cadence.
_PRESENCE_INTERVAL_SECONDS = 30

# Reconnect catch-up: page size and a hard cap on how many pages we'll walk
# backwards per room, so a room with a huge backlog can't make catch-up run
# forever.
_CATCH_UP_PAGE_LIMIT = 50
_CATCH_UP_MAX_PAGES = 20
_CATCH_UP_HOURS = 1

# Reverse of types.EVENT_NAME_TO_TYPE: dataclass type -> public event name.
# Used to recover the dispatch name from an already-parsed RoomEvent without
# re-deriving it from the realtime envelope (which isn't always available --
# catch-up events come from RoomTimelineEvent, not RealtimeEventEnvelope).
_TYPE_TO_EVENT_NAME: dict[type, str] = {v: k for k, v in EVENT_NAME_TO_TYPE.items()}


def _event_name_for(inner: Any) -> str:
    """Dataclass instance -> its public snake_case event name."""
    return _TYPE_TO_EVENT_NAME.get(type(inner), "unknown")


def _format_cutoff(dt: datetime) -> str:
    """Format a plain datetime the same way ``types.format_cursor`` formats a
    protobuf Timestamp, so the catch-up hard cutoff compares lexically
    against stored/emitted cursors."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


class Bot:
    """The main bot object. Holds config, client, realtime stream, and registries."""

    def __init__(
        self,
        instance: str | None = None,
        prefix: str = "!",
        spaces: list[str] | None = None,
        session: str | None = None,
        config_path: str | None = None,
        dms: bool | None = None,
        email: str | None = None,
        password: str | None = None,
        token: str | None = None,
    ) -> None:
        self._config_path = config_path
        self._init_kwargs = {
            "instance": instance,
            "prefix": prefix,
            "spaces": spaces,
            "session": session,
            "email": email,
            "password": password,
            "dms": dms,
            "token": token,
        }
        self.config = BotConfig.load(config_path, **self._init_kwargs)

        self.transport = self._build_transport()
        self.client = Client(self.transport)
        self.users = UserCache(self.client)
        self.hydrator = Hydrator(self.client, self.users)
        self.realtime = Realtime(self.transport)

        self._middleware = MiddlewareChain()

        # Registries
        self._commands: dict[str, Command] = {}
        self._event_handlers: list[EventHandler] = []
        self._cogs: dict[str, Cog] = {}
        self._extensions: dict[str, Any] = {}

        # Bot's own user (populated on connect)
        self.user: User | None = None
        self._closed = False
        self._stop_event: asyncio.Event | None = None
        self._realtime_task: asyncio.Task | None = None
        self._presence_task: asyncio.Task | None = None

        # room_id -> is_dm (RoomKind.DM). Populated from ListRooms
        # at catch-up time and lazily via GetRoom on a cache miss.
        self._room_kinds: dict[str, bool] = {}

        # Persistent state: last processed event timestamp (global cursor).
        self._state_path = Path(".chatto-bot-state.json")
        self._cursor: dict[str, str] = {}
        self._state_dirty = False
        self._state_flush_task: asyncio.Task | None = None
        self._load_state()

    def _build_transport(self) -> Transport:
        return Transport(
            self.config.instance,
            self.config.token or None,
            self.config.session or None,
            identifier=self.config.email or None,
            password=self.config.password or None,
        )

    # --- Command registration ---

    def command(
        self,
        name: str | None = None,
        *,
        desc: str = "",
        aliases: list[str] | None = None,
        admin: bool = False,
    ) -> Callable:
        """Decorator to register a command on this bot."""

        def decorator(func: Callable[..., Awaitable[None]]) -> Command:
            cmd = Command(
                name=name or func.__name__,
                callback=func,
                description=desc,
                aliases=aliases or [],
                admin=admin,
            )
            self.add_command(cmd)
            return cmd

        return decorator

    def add_command(self, cmd: Command) -> None:
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._commands[alias] = cmd

    def remove_command(self, name: str) -> Command | None:
        cmd = self._commands.pop(name, None)
        if cmd:
            for alias in cmd.aliases:
                self._commands.pop(alias, None)
        return cmd

    def get_command(self, name: str) -> Command | None:
        return self._commands.get(name)

    @property
    def commands(self) -> list[Command]:
        """All unique registered commands (no alias duplicates)."""
        seen: set[str] = set()
        result: list[Command] = []
        for cmd in self._commands.values():
            if cmd.name not in seen:
                seen.add(cmd.name)
                result.append(cmd)
        return result

    # --- Event handler registration ---

    def on_event(
        self,
        event_type: str,
        *,
        room: str | None = None,
        actor: str | None = None,
    ) -> Callable:
        """Decorator to register an event handler on this bot."""
        warn_if_retired_event_name(event_type)

        def decorator(func: Callable[..., Awaitable[None]]) -> EventHandler:
            handler = EventHandler(
                event_type=event_type,
                callback=func,
                filters={
                    k: v
                    for k, v in [("room", room), ("actor", actor)]
                    if v is not None
                },
            )
            self._event_handlers.append(handler)
            return handler

        return decorator

    # --- Middleware ---

    def middleware(self, func: MiddlewareFunc) -> MiddlewareFunc:
        """Decorator to register middleware."""
        self._middleware.add(func)
        return func

    # --- Cog management ---

    async def add_cog(self, cog: Cog) -> None:
        """Register a cog and all its commands/handlers."""
        name = cog.__cog_name__
        if name in self._cogs:
            raise ValueError(f"Cog {name!r} is already loaded")

        for cmd in cog.__cog_commands__:
            self.add_command(cmd)

        for handler in cog.__cog_event_handlers__:
            warn_if_retired_event_name(handler.event_type)
        self._event_handlers.extend(cog.__cog_event_handlers__)

        self._cogs[name] = cog
        await cog.cog_load()
        logger.info("Loaded cog: %s", name)

    async def remove_cog(self, name: str) -> Cog | None:
        """Unload a cog by name."""
        cog = self._cogs.pop(name, None)
        if not cog:
            return None

        for cmd in cog.__cog_commands__:
            self.remove_command(cmd.name)

        for handler in cog.__cog_event_handlers__:
            try:
                self._event_handlers.remove(handler)
            except ValueError:
                pass

        await cog.cog_unload()
        logger.info("Unloaded cog: %s", name)
        return cog

    # --- Extension management ---

    async def load_extension(self, module_name: str) -> None:
        """Load an extension module and call its setup(bot) function."""
        if module_name in self._extensions:
            raise ValueError(f"Extension {module_name!r} is already loaded")

        module = importlib.import_module(module_name)
        setup = getattr(module, "setup", None)
        if setup is None:
            raise ValueError(f"Extension {module_name!r} has no setup() function")

        await setup(self)
        self._extensions[module_name] = module
        logger.info("Loaded extension: %s", module_name)

    async def unload_extension(self, module_name: str) -> None:
        """Unload an extension."""
        module = self._extensions.pop(module_name, None)
        if not module:
            raise ValueError(f"Extension {module_name!r} is not loaded")

        teardown = getattr(module, "teardown", None)
        if teardown:
            await teardown(self)

        cog_names = [
            name
            for name, cog in self._cogs.items()
            if type(cog).__module__ == module_name
        ]
        for name in cog_names:
            await self.remove_cog(name)

        logger.info("Unloaded extension: %s", module_name)

    async def reload_extension(self, module_name: str) -> None:
        """Reload an extension (unload + reimport + load).

        If the new version fails to load, the old version is restored.
        """
        import sys

        old_module = self._extensions.get(module_name)
        old_sys_module = sys.modules.get(module_name)

        await self.unload_extension(module_name)

        if module_name in sys.modules:
            del sys.modules[module_name]

        try:
            await self.load_extension(module_name)
        except Exception:
            logger.exception("Failed to reload %s, restoring previous version", module_name)
            if old_sys_module is not None:
                sys.modules[module_name] = old_sys_module
            if old_module is not None:
                setup = getattr(old_module, "setup", None)
                if setup:
                    await setup(self)
                self._extensions[module_name] = old_module
            raise

    # --- Event dispatching ---

    def _has_handler_for(self, name: str) -> bool:
        return any(h.event_type == name for h in self._event_handlers)

    def _will_dispatch(self, name: str) -> bool:
        """Whether hydrating this event is worth it -- it will actually
        reach a handler (or, for message_posted, possibly a command).

        Commands are parsed out of message bodies in ``_dispatch_command``,
        not routed through ``_event_handlers``, so ``message_posted`` must
        hydrate whenever *any* command is registered, independent of
        whether a raw ``message_posted`` handler also exists.
        """
        if name == "message_posted":
            return bool(self._commands) or self._has_handler_for("message_posted")
        return self._has_handler_for(name)

    async def _on_envelope(self, envelope: RealtimeEventEnvelope) -> None:
        """Realtime dispatch entrypoint, passed to ``Realtime.run()``."""
        name = event_name(envelope)
        if not self._will_dispatch(name):
            return

        try:
            hydrated = await self.hydrator.hydrate(envelope)
        except Unauthenticated:
            # The bearer token can be revoked mid-run (hydrate.py calls
            # client.get_message/batch_get_users). Relogin right away, then
            # re-raise: realtime.py's dispatch guard must let this propagate
            # instead of swallowing it, which tears the WS connection down
            # and hands control back to `_run_realtime`'s supervisor loop, so
            # it reconnects and runs catch-up for whatever got missed.
            logger.warning("Credential rejected while hydrating a realtime event")
            await self._handle_unauthenticated()
            raise
        if hydrated is None:
            return  # retracted between signal and fetch, or otherwise dropped

        room_event = parse_envelope(
            hydrated.envelope, message=hydrated.message, actor=hydrated.actor
        )
        await self._dispatch(room_event)

    async def _dispatch(self, event: RoomEvent) -> bool:
        """Dispatch an event through middleware and to handlers.

        Cursor dedup applies to per-room events only (server-wide events
        have no created_at and aren't part of a replay-able stream). The
        cursor only advances *after* a successful dispatch or a
        deterministic filter skip. If middleware raises, the event replays
        on next start instead of being silently dropped.

        Returns whether the event actually reached middleware/handlers --
        ``False`` for a cursor-dedup drop or a deterministic filter skip.
        Callers that count "replayed" events (catch-up) should use this
        instead of assuming every call that didn't raise did real work.
        """
        if event.created_at:
            cursor_ts = self._cursor.get(_GLOBAL_CURSOR_KEY, "")
            if cursor_ts and event.created_at <= cursor_ts:
                return False

        etype = _event_name_for(event.event)
        self._apply_invalidations(etype, event.event)

        room_id = getattr(event.event, "room_id", None) or ""
        if room_id:
            await self._ensure_room_kind(room_id)

        # Deterministic skips: these decisions don't change on retry, so
        # the cursor advances even though no handler runs.
        skipped = (
            (self.config.rooms and room_id and room_id not in self.config.rooms)
            or (not self.config.dms and room_id and self._room_kinds.get(room_id, False))
            or (self.user and event.actor_id == self.user.id)
        )
        if skipped:
            self._commit_cursor(event)
            return False

        ctx = Context(self, event)

        async def handle() -> None:
            if etype == "message_posted":
                body = getattr(event.event, "body", None)
                content = self._command_content(body or "")
                if content is not None:
                    await self._dispatch_command(ctx, content)

            for handler in self._event_handlers:
                if handler.event_type == etype and handler.matches(ctx):
                    try:
                        await handler.invoke(ctx)
                    except Exception:
                        logger.exception("Error in event handler for %s", etype)

        await self._middleware.run(ctx, handle)
        self._commit_cursor(event)
        return True

    def _apply_invalidations(self, etype: str, inner: Any) -> None:
        """Keep the room-kind cache and the user cache from serving stale
        answers for the events known to invalidate them."""
        if etype in ("room_created", "new_direct_message_notification"):
            room_id = getattr(inner, "room_id", "") or ""
            if room_id:
                self._room_kinds.pop(room_id, None)
        elif etype in ("user_profile_updated", "presence_changed"):
            user_id = getattr(inner, "user_id", "")
            if user_id:
                self.users.invalidate(user_id)

    async def _ensure_room_kind(self, room_id: str) -> None:
        """Populate ``self._room_kinds[room_id]`` on a cache miss via GetRoom,
        so ``Context.is_dm`` can stay a synchronous property."""
        if room_id in self._room_kinds:
            return
        try:
            room = await self.client.get_room(room_id)
        except Unauthenticated:
            await self._handle_unauthenticated()
            return
        except Exception:
            logger.debug("Could not resolve room kind for %s", room_id, exc_info=True)
            return
        self._room_kinds[room_id] = room.kind == RoomKind.DM

    def _refresh_room_kinds(self, rooms: list[RoomWithViewerState]) -> None:
        for rws in rooms:
            room = rws.room
            if room is not None and room.id:
                self._room_kinds[room.id] = room.kind == RoomKind.DM

    def _command_content(self, body: str) -> str | None:
        """Return the command text if a message is addressed to the bot, else None.

        Two triggers, both only at the start of the message:
        - the configured prefix, e.g. ``!ping``
        - a mention of the bot, e.g. ``@ChattoBot ping``. Chatto encodes mentions
          as plain ``@login`` text (case-insensitive); we also accept the bot's
          display name when it is a single token, so ``@Chabotto ping`` works too.

        The returned content is the text after the trigger (empty string for a
        bare trigger, which dispatches to no command).
        """
        stripped = body.lstrip()
        if not stripped:
            return None

        prefix = self.config.prefix
        if prefix and stripped.startswith(prefix):
            return stripped[len(prefix) :].lstrip()

        names = []
        if self.user:
            if self.user.login:
                names.append(self.user.login)
            display = self.user.display_name
            if display and not any(c.isspace() for c in display):
                names.append(display)
        if names:
            alternation = "|".join(re.escape(n) for n in names)
            match = re.match(rf"@(?:{alternation})(?=\s|$)", stripped, re.IGNORECASE)
            if match:
                return stripped[match.end() :].lstrip()

        return None

    async def _dispatch_command(self, ctx: Context, content: str) -> None:
        """Parse and dispatch a command from the post-trigger command text."""
        parts = content.split(None, 1)
        if not parts:
            return

        cmd_name = parts[0].lower()
        args_str = parts[1] if len(parts) > 1 else ""

        cmd = self.get_command(cmd_name)
        if not cmd:
            return

        if cmd.admin:
            actor = ctx.actor
            if not actor or actor.login not in self.config.admins:
                return

        try:
            await cmd.invoke(ctx, args_str)
        except CommandError as e:
            await ctx.reply(f"Error: {e}")
        except Exception:
            logger.exception("Error in command %s", cmd_name)

    # --- State persistence ---

    def _load_state(self) -> None:
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text())
                cursor = data.get("cursor") or {}
                # Migrate older per-space cursors to a single global cursor
                # by keeping the most recent timestamp.
                if cursor and _GLOBAL_CURSOR_KEY not in cursor:
                    cursor = {_GLOBAL_CURSOR_KEY: max(cursor.values())}
                self._cursor = cursor
            except Exception:
                self._cursor = {}

    def _mark_state_dirty(self) -> None:
        """Mark state as needing a flush. Actual write happens periodically."""
        self._state_dirty = True
        if self._state_flush_task is None or self._state_flush_task.done():
            try:
                self._state_flush_task = asyncio.create_task(self._flush_state_later())
            except RuntimeError:
                # No event loop, fall back to sync write
                self._save_state()

    async def _flush_state_later(self) -> None:
        """Debounced state flush. Waits a bit then writes once."""
        await asyncio.sleep(5.0)
        if self._state_dirty:
            await asyncio.get_running_loop().run_in_executor(None, self._save_state)

    def _save_state(self) -> None:
        self._state_dirty = False
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"cursor": self._cursor}))
        os.replace(tmp, self._state_path)

    def _commit_cursor(self, event: RoomEvent) -> None:
        """Advance the cursor past this event and schedule a flush."""
        if not event.created_at:
            return
        self._advance_cursor(event.created_at)
        self._mark_state_dirty()

    def _advance_cursor(self, created_at: str) -> None:
        """Update the global cursor if this event is newer than what we've seen."""
        prev = self._cursor.get(_GLOBAL_CURSOR_KEY, "")
        if created_at > prev:
            self._cursor[_GLOBAL_CURSOR_KEY] = created_at

    # --- Reconnect catch-up ---
    #
    # The realtime stream is live-only with no resume cursor (see
    # realtime.py), so every (re)subscribe -- including the very first one --
    # runs this as Realtime's on_reconnect callback.

    async def _catch_up(self) -> None:
        """Page every allowlisted, joined room's timeline backwards from
        "now", stopping at the stored cursor or a 1-hour hard cutoff,
        whichever comes first.

        Every room's missed events are collected *before* anything is
        dispatched, then merged and sorted into one ascending (oldest-first)
        list and dispatched in that order. This matters because ``_dispatch``
        dedups against a single global cursor: dispatching room-by-room would
        advance that cursor to one room's newest missed event before a later
        room's older-but-still-missed events were even looked at, and the
        dedup check would then silently drop them. Collecting everything
        first keeps the cursor advancing monotonically across the whole
        catch-up, regardless of how many rooms are involved.

        Known blind spot: thread replies (only visible via GetThreadEvents,
        not the room timeline) and reactions aren't part of
        GetRoomEvents' payload, so if either happens during a reconnect gap,
        this catch-up won't replay it -- only a live realtime event would
        have caught it.
        """
        try:
            rooms = await self.client.list_rooms()
        except Exception:
            logger.exception("Catch-up: failed to list rooms")
            return

        self._refresh_room_kinds(rooms)

        hard_cutoff = _format_cutoff(
            datetime.now(timezone.utc) - timedelta(hours=_CATCH_UP_HOURS)
        )
        cursor_ts = self._cursor.get(_GLOBAL_CURSOR_KEY, "")

        pending: list[tuple[str, RoomTimelineEvent, RoomTimelineIncludes | None]] = []
        for rws in rooms:
            room = rws.room
            if room is None or not room.id:
                continue
            if not rws.viewer_state or not rws.viewer_state.is_member:
                continue
            if self.config.rooms and room.id not in self.config.rooms:
                continue
            if not self.config.dms and room.kind == RoomKind.DM:
                continue

            pending.extend(
                await self._collect_missed_room_events(room.id, cursor_ts, hard_cutoff)
            )

        pending.sort(key=lambda item: item[0])  # oldest-first, across all rooms

        replayed = 0
        for _, tev, includes in pending:
            try:
                if await self._dispatch_timeline_event(tev, includes):
                    replayed += 1
            except Exception:
                logger.exception("Catch-up: failed to dispatch event %s", tev.id)

        if replayed:
            logger.info("Catch-up replayed %d missed events", replayed)
        self._save_state()

    async def _collect_missed_room_events(
        self, room_id: str, cursor_ts: str, hard_cutoff: str
    ) -> list[tuple[str, RoomTimelineEvent, RoomTimelineIncludes | None]]:
        """Page one room's timeline backwards via the opaque ``before``
        cursor, collecting events newer than ``cursor_ts`` and no older than
        ``hard_cutoff``. Doesn't dispatch anything -- returns
        ``(created_at, event, includes)`` tuples so ``_catch_up`` can merge
        and sort them against every other room's missed events before
        dispatching once, chronologically.
        """
        collected: list[tuple[str, RoomTimelineEvent, RoomTimelineIncludes | None]] = []
        before: str | None = None

        for _ in range(_CATCH_UP_MAX_PAGES):
            try:
                page = await self.client.get_room_events(
                    room_id, limit=_CATCH_UP_PAGE_LIMIT, before=before
                )
            except Exception:
                logger.debug(
                    "Catch-up: skipping room %s (no access)", room_id, exc_info=True
                )
                return collected

            events = list(page.events)
            if not events:
                return collected

            # Events come oldest-first *within* a page (see GetRoomEvents in
            # cli/internal/core/room_events.go). Scan newest-to-oldest --
            # i.e. in reverse -- to find the stop point: the first event
            # at/older than the stored cursor or the hard cutoff. Collecting
            # while walking in reverse also means each page's slice of
            # `collected` comes out newest-first, but that's fine since the
            # caller sorts everything by timestamp before dispatching.
            stop = False
            for tev in reversed(events):
                ts = format_cursor(tev.created_at)
                if (cursor_ts and ts <= cursor_ts) or (ts and ts < hard_cutoff):
                    stop = True
                    break
                collected.append((ts, tev, page.includes))

            if stop or not page.has_older:
                return collected

            before = page.start_cursor
            if not before:
                return collected

        return collected

    async def _dispatch_timeline_event(
        self, tev: RoomTimelineEvent, includes: RoomTimelineIncludes | None
    ) -> bool:
        """Adapt one ``RoomTimelineEvent`` (from GetRoomEvents) into a
        RoomEvent and dispatch it.

        Unlike realtime hydration, the message body is already inline
        (``message_posted`` carries the full ``Message``), so no extra fetch
        is needed; the actor is resolved from the page's ``includes`` map
        when present, falling back to UserCache otherwise. Returns whatever
        ``Bot._dispatch`` returns -- whether the event actually reached
        handlers, as opposed to being cursor-deduped or filtered out.
        """
        oneof = tev.event
        if oneof is None:
            return False
        field = oneof.field

        message = oneof.value.message if field == "message_posted" else None

        actor = None
        if tev.actor_id:
            if includes is not None:
                actor = includes.users.get(tev.actor_id)
            if actor is None and self._will_dispatch(field):
                actor = await self.users.get(tev.actor_id)

        room_event = parse_envelope(tev, message=message, actor=actor)
        return await self._dispatch(room_event)

    # --- Auth / lifecycle ---

    async def _handle_unauthenticated(self) -> None:
        """Called whenever the server rejects our bearer token (RPC or
        realtime). Re-runs login in place; the realtime supervisor loop
        (``_run_realtime``) restarts the stream afterward."""
        logger.warning("Credential rejected by server, attempting relogin")
        try:
            await self.transport.relogin()
        except AuthError:
            logger.exception("Relogin failed: no identifier/password configured")
        except Exception:
            logger.exception("Relogin failed")

    async def _presence_loop(self) -> None:
        """Background task: refresh presence on an interval (see
        `_PRESENCE_INTERVAL_SECONDS`)."""
        while True:
            await asyncio.sleep(_PRESENCE_INTERVAL_SECONDS)
            try:
                await self.client.update_presence("PRESENCE_STATUS_ONLINE")
            except Unauthenticated:
                await self._handle_unauthenticated()
            except Exception:
                logger.warning("Could not refresh presence", exc_info=True)

    async def _run_realtime(self) -> None:
        """Supervises the realtime stream: relogin-and-restart on
        UNAUTHENTICATED, give up (but leave the rest of the bot running) on
        a server-driven RealtimeStopped, return once `close()` has called
        `realtime.stop()`."""
        while not self._closed:
            try:
                await self.realtime.run(self._on_envelope, self._catch_up)
            except Unauthenticated:
                logger.warning("Realtime rejected credentials, relogging in")
                await self._handle_unauthenticated()
                continue
            except RealtimeStopped:
                logger.error(
                    "Realtime connection stopped by server; live events are "
                    "now disabled for this run"
                )
                return
            else:
                return  # run() only returns normally after stop()

    async def _resolve_viewer(self) -> User:
        viewer = await self.client.get_viewer()
        profile = viewer.profile if viewer is not None else None
        if profile is None:
            raise RuntimeError("Authentication failed: GetViewer returned no profile")
        return User(
            id=profile.id,
            login=profile.login,
            display_name=profile.display_name,
            avatar_url=profile.avatar_url,
            presence_status=normalize_presence_status(profile.presence_status),
        )

    async def start(self) -> None:
        """Log in (if needed), verify auth, and start the realtime stream."""
        if not self.transport.token and not self.transport.session:
            if not (self.transport.identifier and self.transport.password):
                raise RuntimeError(
                    "Authentication failed. Set CHATTO_TOKEN, or CHATTO_SESSION, "
                    "or CHATTO_EMAIL + CHATTO_PASSWORD."
                )
            await self.transport.relogin()

        self.user = await self._resolve_viewer()
        logger.info("Authenticated as %s (%s)", self.user.display_name, self.user.login)

        try:
            await self.client.update_presence("PRESENCE_STATUS_ONLINE")
            logger.info("Presence set to ONLINE")
        except Exception:
            logger.warning("Could not set presence (server may not support it)", exc_info=True)

        for ext in self.config.extensions:
            try:
                await self.load_extension(ext)
            except Exception:
                logger.exception("Failed to load extension: %s", ext)

        self._presence_task = asyncio.create_task(self._presence_loop())
        self._realtime_task = asyncio.create_task(self._run_realtime())

    async def close(self) -> None:
        """Gracefully shut down (idempotent)."""
        if self._closed:
            return
        self._closed = True
        logger.info("Shutting down...")
        self._save_state()

        self.realtime.stop()
        for task in (self._realtime_task, self._presence_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.debug("Background task raised during shutdown", exc_info=True)

        for name in list(self._cogs.keys()):
            await self.remove_cog(name)

        await self.client.close()

        if self._stop_event:
            self._stop_event.set()

    async def _reload(self) -> None:
        """Reload config, rebuild the transport/client/realtime stack, and
        reload extensions (SIGHUP handler)."""
        logger.info("Reloading...")
        self._save_state()

        self.realtime.stop()
        for task in (self._realtime_task, self._presence_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await self.client.close()

        self.config = BotConfig.load(self._config_path, **self._init_kwargs)
        self.transport = self._build_transport()
        self.client = Client(self.transport)
        self.users = UserCache(self.client)
        self.hydrator = Hydrator(self.client, self.users)
        self.realtime = Realtime(self.transport)
        self._room_kinds = {}

        if not self.transport.token and not self.transport.session:
            if self.transport.identifier and self.transport.password:
                await self.transport.relogin()

        try:
            self.user = await self._resolve_viewer()
        except Exception:
            logger.exception("Authentication failed after reload")
            return
        logger.info("Re-authenticated as %s (%s)", self.user.display_name, self.user.login)

        for ext_name in list(self._extensions.keys()):
            try:
                await self.reload_extension(ext_name)
            except Exception:
                logger.exception("Failed to reload extension: %s", ext_name)

        self._presence_task = asyncio.create_task(self._presence_loop())
        self._realtime_task = asyncio.create_task(self._run_realtime())

        logger.info("Reload complete")

    def run(self) -> None:
        """Blocking entry point. Starts the event loop and runs until interrupted."""
        logging.basicConfig(
            level=getattr(logging, self.config.log_level, logging.INFO),
            format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

        async def _runner() -> None:
            self._stop_event = asyncio.Event()
            loop = asyncio.get_running_loop()

            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                loop.add_signal_handler(
                    sig, lambda s=sig: asyncio.create_task(self._handle_signal(s))
                )

            try:
                await self.start()
                await self._stop_event.wait()
            except asyncio.CancelledError:
                pass
            finally:
                await self.close()

        asyncio.run(_runner())

    async def _handle_signal(self, sig: signal.Signals) -> None:
        if sig == signal.SIGHUP:
            logger.info("Received SIGHUP, reloading...")
            try:
                await self._reload()
            except Exception:
                logger.exception("Reload failed")
        else:
            logger.info("Received %s, shutting down...", sig.name)
            await self.close()
