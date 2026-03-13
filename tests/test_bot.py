"""Tests for bot.py — dispatch, self-message guard, commands, state."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chatto_bot.bot import Bot
from chatto_bot.command import Command
from chatto_bot.context import Context
from chatto_bot.types import (
    MessagePostedEvent,
    PresenceChangedEvent,
    SpaceEvent,
    User,
)
from conftest import make_event, make_ctx


class TestSelfMessageGuard:
    @pytest.mark.asyncio
    async def test_bot_ignores_own_events(self, bot):
        """Bot must not process events from itself."""
        event = make_event(body="!ping", actor_id="Ubot")
        called = False

        async def ping(ctx):
            nonlocal called
            called = True

        cmd = Command(name="ping", callback=ping)
        bot.add_command(cmd)

        await bot._dispatch(event)
        assert not called

    @pytest.mark.asyncio
    async def test_bot_processes_other_users(self, bot):
        """Bot processes events from other users normally."""
        event = make_event(body="!ping", actor_id="Uother")
        called = False

        async def ping(ctx):
            nonlocal called
            called = True

        cmd = Command(name="ping", callback=ping)
        bot.add_command(cmd)

        await bot._dispatch(event)
        assert called


class TestDispatchCommand:
    @pytest.mark.asyncio
    async def test_command_dispatch(self, bot):
        called_with = {}

        async def ping(ctx):
            called_with["ctx"] = ctx

        cmd = Command(name="ping", callback=ping)
        bot.add_command(cmd)

        event = make_event(body="!ping")
        await bot._dispatch(event)
        assert "ctx" in called_with

    @pytest.mark.asyncio
    async def test_command_with_args(self, bot):
        called_with = {}

        async def echo(ctx, message: str = ""):
            called_with["message"] = message

        cmd = Command(name="echo", callback=echo)
        bot.add_command(cmd)

        event = make_event(body="!echo hello world")
        await bot._dispatch(event)
        assert called_with["message"] == "hello world"

    @pytest.mark.asyncio
    async def test_admin_command_blocked(self, bot):
        called = False

        async def admin_cmd(ctx):
            nonlocal called
            called = True

        cmd = Command(name="admin_cmd", callback=admin_cmd, admin=True)
        bot.add_command(cmd)

        event = make_event(body="!admin_cmd", actor_login="regular_user")
        await bot._dispatch(event)
        assert not called

    @pytest.mark.asyncio
    async def test_admin_command_allowed(self, bot):
        called = False

        async def admin_cmd(ctx):
            nonlocal called
            called = True

        cmd = Command(name="admin_cmd", callback=admin_cmd, admin=True)
        bot.add_command(cmd)

        event = make_event(body="!admin_cmd", actor_login="admin")
        await bot._dispatch(event)
        assert called

    @pytest.mark.asyncio
    async def test_non_command_message_ignored(self, bot):
        called = False

        async def ping(ctx):
            nonlocal called
            called = True

        cmd = Command(name="ping", callback=ping)
        bot.add_command(cmd)

        event = make_event(body="just chatting")
        await bot._dispatch(event)
        assert not called


class TestDispatchRoomFilter:
    @pytest.mark.asyncio
    async def test_room_allowlist_blocks(self, bot):
        bot.config.rooms = ["R_allowed"]
        called = False

        async def ping(ctx):
            nonlocal called
            called = True

        cmd = Command(name="ping", callback=ping)
        bot.add_command(cmd)

        event = make_event(body="!ping", room_id="R_other")
        await bot._dispatch(event)
        assert not called

    @pytest.mark.asyncio
    async def test_room_allowlist_allows(self, bot):
        bot.config.rooms = ["R1"]
        called = False

        async def ping(ctx):
            nonlocal called
            called = True

        cmd = Command(name="ping", callback=ping)
        bot.add_command(cmd)

        event = make_event(body="!ping", room_id="R1")
        await bot._dispatch(event)
        assert called


class TestCursorDedup:
    @pytest.mark.asyncio
    async def test_old_events_skipped(self, bot):
        bot._cursor = {"S1": "2026-01-01T00:00:00Z"}
        called = False

        async def ping(ctx):
            nonlocal called
            called = True

        cmd = Command(name="ping", callback=ping)
        bot.add_command(cmd)

        event = make_event(body="!ping")
        event.created_at = "2025-12-31T23:59:59Z"
        await bot._dispatch(event)
        assert not called


class TestStatePersistence:
    def test_save_state_atomic(self, bot):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            bot._state_path = state_path
            bot._cursor = {"S1": "2026-01-01T00:00:00Z"}
            bot.config.spaces = ["S1"]

            bot._save_state()

            assert state_path.exists()
            data = json.loads(state_path.read_text())
            assert data["cursor"]["S1"] == "2026-01-01T00:00:00Z"
            # Temp file should be cleaned up
            assert not state_path.with_suffix(".tmp").exists()

    def test_load_state(self, bot):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text(json.dumps({
                "cursor": {"S1": "2026-01-01T00:00:00Z"},
                "spaces": ["S1", "S2"],
            }))
            bot._state_path = state_path
            bot.config.spaces = ["S1"]

            bot._load_state()

            assert bot._cursor["S1"] == "2026-01-01T00:00:00Z"
            assert "S2" in bot.config.spaces

    def test_load_state_missing_file(self, bot):
        bot._state_path = Path("/nonexistent/path/state.json")
        bot._cursor = {}
        bot._load_state()
        assert bot._cursor == {}

    def test_load_state_corrupt_file(self, bot):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text("not json!")
            bot._state_path = state_path

            bot._load_state()
            assert bot._cursor == {}


class TestCloseOrdering:
    @pytest.mark.asyncio
    async def test_cogs_unloaded_before_client_close(self, bot):
        """Cogs should be unloaded before client.close() so cog_unload() can make API calls."""
        call_order = []

        original_remove_cog = bot.remove_cog.__func__ if hasattr(bot.remove_cog, '__func__') else None

        async def tracking_remove_cog(name):
            call_order.append(f"remove_cog:{name}")
            bot._cogs.pop(name, None)

        async def tracking_client_close():
            call_order.append("client_close")

        cog = MagicMock()
        cog.__cog_commands__ = []
        cog.__cog_event_handlers__ = []
        cog.cog_unload = AsyncMock()
        bot._cogs = {"TestCog": cog}
        bot.remove_cog = tracking_remove_cog
        bot.client.close = tracking_client_close
        bot._save_state = MagicMock()

        await bot.close()

        assert "remove_cog:TestCog" in call_order
        assert "client_close" in call_order
        assert call_order.index("remove_cog:TestCog") < call_order.index("client_close")


class TestBotCommand:
    def test_admin_parameter(self, bot):
        @bot.command(name="secret", admin=True, desc="Admin only")
        async def secret(ctx):
            pass

        cmd = bot.get_command("secret")
        assert cmd is not None
        assert cmd.admin is True

    def test_aliases(self, bot):
        @bot.command(name="hello", aliases=["hi", "hey"])
        async def hello(ctx):
            pass

        assert bot.get_command("hello") is not None
        assert bot.get_command("hi") is not None
        assert bot.get_command("hey") is not None
        assert bot.get_command("hi") is bot.get_command("hello")
