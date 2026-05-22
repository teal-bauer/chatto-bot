"""Bot class: the central orchestrator."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import signal
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Awaitable

from .client import Client, login
from .cog import Cog
from .command import Command, CommandError, command
from .config import BotConfig
from .context import Context
from .event import EventHandler, on_event
from .middleware import MiddlewareChain, MiddlewareFunc
from .subscription import SubscriptionManager
from .types import (
    MessagePostedEvent,
    RoomEvent,
    User,
    event_name,
    parse_room_event,
)

logger = logging.getLogger(__name__)

# Cursor key used in the persistent state file. The API exposes a single
# global event stream, so one cursor covers everything.
_GLOBAL_CURSOR_KEY = "_global"


class Bot:
    """The main bot object. Holds config, client, subscriptions, and registries."""

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
        }
        self.config = BotConfig.load(config_path, **self._init_kwargs)
        self.client = Client(self.config)
        self._subscriptions = SubscriptionManager(self.config)
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

        # room_id -> RoomType ("CHANNEL" | "DM"), populated from Instance.rooms
        # at startup. Used to gate DM dispatch when ``config.dms`` is false.
        self._room_types: dict[str, str] = {}

        # Persistent state: last processed event timestamp (global cursor).
        self._state_path = Path(".chatto-bot-state.json")
        self._cursor: dict[str, str] = {}
        self._state_dirty = False
        self._state_flush_task: asyncio.Task | None = None
        self._load_state()

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
        space: str | None = None,
        actor: str | None = None,
    ) -> Callable:
        """Decorator to register an event handler on this bot."""

        def decorator(func: Callable[..., Awaitable[None]]) -> EventHandler:
            handler = EventHandler(
                event_type=event_type,
                callback=func,
                filters={
                    k: v
                    for k, v in [
                        ("room", room),
                        ("space", space),
                        ("actor", actor),
                    ]
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

    async def _dispatch(self, event: RoomEvent) -> None:
        """Dispatch an event through middleware and to handlers.

        Cursor dedup applies to per-room events only (instance events have
        no created_at and aren't part of a replay-able stream). The cursor
        only advances *after* a successful dispatch or a deterministic
        filter skip. If middleware raises, the event replays on next
        start instead of being silently dropped.
        """
        if event.created_at:
            cursor_ts = self._cursor.get(_GLOBAL_CURSOR_KEY, "")
            if cursor_ts and event.created_at <= cursor_ts:
                return

        room_id = getattr(event.event, "room_id", None) or ""

        # Deterministic skips: these decisions don't change on retry, so
        # the cursor advances even though no handler runs.
        skipped = (
            (self.config.rooms and room_id and room_id not in self.config.rooms)
            or (not self.config.dms and room_id and self._room_types.get(room_id) == "DM")
            or (self.user and event.actor_id == self.user.id)
        )
        if skipped:
            self._commit_cursor(event)
            return

        ctx = Context(self, event)
        etype = event_name(event.event)

        async def handle() -> None:
            if isinstance(event.event, MessagePostedEvent) and event.event.body:
                body = event.event.body
                if body.startswith(self.config.prefix):
                    await self._dispatch_command(ctx, body)

            for handler in self._event_handlers:
                if handler.event_type == etype and handler.matches(ctx):
                    try:
                        await handler.invoke(ctx)
                    except Exception:
                        logger.exception(
                            "Error in event handler for %s", etype
                        )

        await self._middleware.run(ctx, handle)
        self._commit_cursor(event)

    def _commit_cursor(self, event: RoomEvent) -> None:
        """Advance the cursor past this event and schedule a flush."""
        if not event.created_at:
            return
        self._advance_cursor(event.created_at)
        self._mark_state_dirty()

    async def _dispatch_command(self, ctx: Context, body: str) -> None:
        """Parse and dispatch a command from a message body."""
        content = body[len(self.config.prefix) :]
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

    def _advance_cursor(self, created_at: str) -> None:
        """Update the global cursor if this event is newer than what we've seen."""
        prev = self._cursor.get(_GLOBAL_CURSOR_KEY, "")
        if created_at > prev:
            self._cursor[_GLOBAL_CURSOR_KEY] = created_at

    # --- Replay ---

    async def _refresh_room_types(self) -> list[dict]:
        """Refresh the cached room-type map and return the room list."""
        try:
            rooms = await self.client.get_rooms()
        except Exception:
            logger.exception("Failed to fetch rooms")
            return []
        self._room_types = {r["id"]: r.get("type", "CHANNEL") for r in rooms}
        return rooms

    async def _replay_missed(self, rooms: list[dict]) -> None:
        """Replay events missed since last shutdown, up to 1 hour.

        Walks every room visible to the bot. Cheap when nothing is new since
        the cursor short-circuits replay events older than the cursor.
        """
        hard_cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        cursor_ts = self._cursor.get(_GLOBAL_CURSOR_KEY, "")

        replayed = 0
        for room in rooms:
            if self.config.rooms and room["id"] not in self.config.rooms:
                continue
            if not self.config.dms and room.get("type") == "DM":
                continue
            try:
                page = await self.client.get_room_events(
                    "", room["id"], limit=50
                )
            except Exception:
                logger.debug("Skipping replay for room %s: no access", room["id"])
                continue

            events = page.get("events", []) if isinstance(page, dict) else page
            # Events come newest-first; reverse for chronological processing
            for event_data in reversed(events):
                created_at = event_data.get("createdAt", "")

                try:
                    event_time = datetime.fromisoformat(
                        created_at.replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    continue
                if event_time < hard_cutoff:
                    continue

                if cursor_ts and created_at <= cursor_ts:
                    continue

                try:
                    event = parse_room_event(event_data)
                    await self._dispatch(event)
                    replayed += 1
                except Exception:
                    logger.debug("Skipping unprocessable replay event")

        if replayed:
            logger.info("Replayed %d missed events", replayed)

        self._save_state()

    # --- Lifecycle ---

    async def _ensure_session(self) -> None:
        """Log in with email/password if no session cookie is set."""
        if self.config.session:
            return
        if not self.config.email or not self.config.password:
            return
        logger.info("Logging in as %s...", self.config.email)
        self.config.session = await login(
            self.config.instance, self.config.email, self.config.password,
        )
        self.client = Client(self.config)
        self._subscriptions = SubscriptionManager(self.config)
        logger.info("Login successful")

    async def start(self) -> None:
        """Connect to the API, verify auth, and start subscriptions."""
        await self._ensure_session()

        me_data = await self.client.me()
        if not me_data:
            raise RuntimeError(
                "Authentication failed. Set CHATTO_SESSION or CHATTO_EMAIL + CHATTO_PASSWORD."
            )

        self.user = User(
            id=me_data["id"],
            login=me_data["login"],
            display_name=me_data["displayName"],
            avatar_url=me_data.get("avatarUrl"),
            presence_status=me_data.get("presenceStatus", "OFFLINE"),
        )
        logger.info(
            "Authenticated as %s (%s)", self.user.display_name, self.user.login
        )

        try:
            await self.client.update_presence("ONLINE")
            logger.info("Presence set to ONLINE")
        except Exception:
            logger.warning("Could not set presence (server may not support it)")

        for ext in self.config.extensions:
            try:
                await self.load_extension(ext)
            except Exception:
                logger.exception("Failed to load extension: %s", ext)

        rooms = await self._refresh_room_types()
        await self._replay_missed(rooms)

        self._subscriptions.start(self._dispatch)

    async def close(self) -> None:
        """Gracefully shut down (idempotent)."""
        if self._closed:
            return
        self._closed = True
        logger.info("Shutting down...")
        self._save_state()
        await self._subscriptions.stop()

        for name in list(self._cogs.keys()):
            await self.remove_cog(name)

        await self.client.close()

        if self._stop_event:
            self._stop_event.set()

    async def _reload(self) -> None:
        """Reload config, reconnect subscriptions, and reload extensions."""
        logger.info("Reloading...")
        self._save_state()

        await self._subscriptions.stop()
        await self.client.close()

        self.config = BotConfig.load(self._config_path, **self._init_kwargs)
        self.client = Client(self.config)
        self._subscriptions = SubscriptionManager(self.config)

        await self._ensure_session()

        me_data = await self.client.me()
        if not me_data:
            logger.error("Authentication failed after reload, check session")
            return

        self.user = User(
            id=me_data["id"],
            login=me_data["login"],
            display_name=me_data["displayName"],
            avatar_url=me_data.get("avatarUrl"),
            presence_status=me_data.get("presenceStatus", "OFFLINE"),
        )
        logger.info(
            "Re-authenticated as %s (%s)", self.user.display_name, self.user.login
        )

        for ext_name in list(self._extensions.keys()):
            try:
                await self.reload_extension(ext_name)
            except Exception:
                logger.exception("Failed to reload extension: %s", ext_name)

        rooms = await self._refresh_room_types()
        await self._replay_missed(rooms)

        self._subscriptions.start(self._dispatch)

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
