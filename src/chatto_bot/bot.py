"""Bot class: the central orchestrator."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
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
    SpaceEvent,
    User,
    event_name,
    parse_space_event,
)

logger = logging.getLogger(__name__)


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
        self._extensions: dict[str, Any] = {}  # module name -> module

        # Bot's own user (populated on connect)
        self.user: User | None = None
        self._closed = False
        self._stop_event: asyncio.Event | None = None

        # Persistent state: last processed event timestamp per space
        self._state_path = Path(".chatto-bot-state.json")
        self._cursor: dict[str, str] = {}  # space_id -> last created_at
        self._load_state()

    @property
    def _all_spaces(self) -> list[str]:
        """All space IDs to subscribe to, including DM if enabled."""
        spaces = list(self.config.spaces)
        if self.config.dms and "DM" not in spaces:
            spaces.append("DM")
        return spaces

    # --- Command registration ---

    def command(
        self,
        name: str | None = None,
        *,
        desc: str = "",
        aliases: list[str] | None = None,
    ) -> Callable:
        """Decorator to register a command on this bot."""

        def decorator(func: Callable[..., Awaitable[None]]) -> Command:
            cmd = Command(
                name=name or func.__name__,
                callback=func,
                description=desc,
                aliases=aliases or [],
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

        # Register commands
        for cmd in cog.__cog_commands__:
            self.add_command(cmd)

        # Register event handlers
        self._event_handlers.extend(cog.__cog_event_handlers__)

        self._cogs[name] = cog
        await cog.cog_load()
        logger.info("Loaded cog: %s", name)

    async def remove_cog(self, name: str) -> Cog | None:
        """Unload a cog by name."""
        cog = self._cogs.pop(name, None)
        if not cog:
            return None

        # Remove commands
        for cmd in cog.__cog_commands__:
            self.remove_command(cmd.name)

        # Remove event handlers
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

        # Remove any cogs that belong to this extension
        cog_names = [
            name
            for name, cog in self._cogs.items()
            if type(cog).__module__ == module_name
        ]
        for name in cog_names:
            await self.remove_cog(name)

        logger.info("Unloaded extension: %s", module_name)

    async def reload_extension(self, module_name: str) -> None:
        """Reload an extension (unload + reimport + load)."""
        await self.unload_extension(module_name)

        # Force reimport
        import sys

        if module_name in sys.modules:
            del sys.modules[module_name]

        await self.load_extension(module_name)

    # --- Event dispatching ---

    async def _dispatch(self, event: SpaceEvent) -> None:
        """Dispatch a SpaceEvent through middleware and to handlers."""
        # Advance cursor so we don't re-process on next restart
        if ctx_space := (getattr(event.event, "space_id", None) or ""):
            self._advance_cursor(ctx_space, event.created_at)
            self._save_state()

        # Skip events from rooms not in the allowlist (if configured)
        if self.config.rooms:
            room_id = getattr(event.event, "room_id", None)
            if room_id and room_id not in self.config.rooms:
                return

        ctx = Context(self, event)
        etype = event_name(event.event)

        async def handle() -> None:
            # 1. Command dispatch (for message_posted events)
            if isinstance(event.event, MessagePostedEvent) and event.event.body:
                body = event.event.body
                if body.startswith(self.config.prefix):
                    await self._dispatch_command(ctx, body)

            # 2. Event handler dispatch
            for handler in self._event_handlers:
                if handler.event_type == etype and handler.matches(ctx):
                    try:
                        await handler.invoke(ctx)
                    except Exception:
                        logger.exception(
                            "Error in event handler for %s", etype
                        )

        await self._middleware.run(ctx, handle)

    async def _dispatch_command(self, ctx: Context, body: str) -> None:
        """Parse and dispatch a command from a message body."""
        # Strip prefix
        content = body[len(self.config.prefix) :]
        parts = content.split(None, 1)
        if not parts:
            return

        cmd_name = parts[0].lower()
        args_str = parts[1] if len(parts) > 1 else ""

        cmd = self.get_command(cmd_name)
        if not cmd:
            return

        # Enforce admin-only commands
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
                self._cursor = data.get("cursor", {})
            except Exception:
                self._cursor = {}

    def _save_state(self) -> None:
        self._state_path.write_text(json.dumps({"cursor": self._cursor}))

    def _advance_cursor(self, space_id: str, created_at: str) -> None:
        """Update the cursor if this event is newer than what we've seen."""
        prev = self._cursor.get(space_id, "")
        if created_at > prev:
            self._cursor[space_id] = created_at

    # --- Replay ---

    async def _replay_missed(self, space_id: str) -> None:
        """Replay events missed since last shutdown, up to 1 hour."""
        hard_cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        cursor_ts = self._cursor.get(space_id, "")

        try:
            rooms = await self.client.get_rooms(space_id)
        except Exception:
            logger.exception("Failed to fetch rooms for replay in space %s", space_id)
            return

        replayed = 0
        for room in rooms:
            try:
                events = await self.client.get_room_events(
                    space_id, room["id"], limit=50
                )
            except Exception:
                logger.debug("Skipping replay for room %s: no access", room["id"])
                continue

            # Events come newest-first; reverse for chronological processing
            for event_data in reversed(events):
                created_at = event_data.get("createdAt", "")

                # Skip events older than 1 hour
                try:
                    event_time = datetime.fromisoformat(
                        created_at.replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    continue
                if event_time < hard_cutoff:
                    continue

                # Skip events we already processed
                if cursor_ts and created_at <= cursor_ts:
                    continue

                try:
                    event = parse_space_event(event_data)
                    await self._dispatch(event)
                    replayed += 1
                except Exception:
                    logger.debug("Skipping unprocessable replay event")

        if replayed:
            logger.info("Replayed %d missed events in space %s", replayed, space_id)

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

        # Verify authentication
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

        # Set presence to ONLINE
        try:
            await self.client.update_presence("ONLINE")
            logger.info("Presence set to ONLINE")
        except Exception:
            logger.warning("Could not set presence (server may not support it)")

        # Load configured extensions
        for ext in self.config.extensions:
            try:
                await self.load_extension(ext)
            except Exception:
                logger.exception("Failed to load extension: %s", ext)

        # Start subscriptions for each configured space (+ DMs if enabled)
        all_spaces = self._all_spaces
        if not all_spaces:
            logger.warning("No spaces configured — bot won't receive events")
            return

        # Replay missed events from the last hour
        for space_id in all_spaces:
            await self._replay_missed(space_id)

        # Start live subscriptions
        for space_id in all_spaces:
            self._subscriptions.start(space_id, self._dispatch)

    async def close(self) -> None:
        """Gracefully shut down (idempotent)."""
        if self._closed:
            return
        self._closed = True
        logger.info("Shutting down...")
        self._save_state()
        await self._subscriptions.stop()
        await self.client.close()

        # Unload cogs
        for name in list(self._cogs.keys()):
            await self.remove_cog(name)

        # Unblock _runner if it's waiting
        if self._stop_event:
            self._stop_event.set()

    async def _reload(self) -> None:
        """Reload config, reconnect subscriptions, and reload extensions."""
        logger.info("Reloading...")
        self._save_state()

        # Stop current subscriptions
        await self._subscriptions.stop()
        await self.client.close()

        # Re-read config (YAML / .env / env vars), keeping explicit overrides
        self.config = BotConfig.load(self._config_path, **self._init_kwargs)
        self.client = Client(self.config)
        self._subscriptions = SubscriptionManager(self.config)

        # Login if needed (may recreate client/subscriptions)
        await self._ensure_session()

        # Verify auth still works
        me_data = await self.client.me()
        if not me_data:
            logger.error("Authentication failed after reload — check session")
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

        # Reload extensions
        for ext_name in list(self._extensions.keys()):
            try:
                await self.reload_extension(ext_name)
            except Exception:
                logger.exception("Failed to reload extension: %s", ext_name)

        # Replay and resubscribe
        all_spaces = self._all_spaces
        for space_id in all_spaces:
            await self._replay_missed(space_id)
        for space_id in all_spaces:
            self._subscriptions.start(space_id, self._dispatch)

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

            # Signal handlers
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
