"""Shared fixtures for chatto-bot tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from chatto_bot.bot import Bot
from chatto_bot.client import Client
from chatto_bot.config import BotConfig
from chatto_bot.context import Context
from chatto_bot.middleware import MiddlewareChain
from chatto_bot.types import (
    MessagePostedEvent,
    SpaceEvent,
    User,
)


@pytest.fixture
def bot_config():
    return BotConfig(
        instance="https://test.example.com",
        prefix="!",
        spaces=["S1"],
        session="fake-session",
        admins=["admin"],
    )


@pytest.fixture
def mock_client(bot_config):
    client = MagicMock(spec=Client)
    client.post_message = AsyncMock(return_value={"id": "E1"})
    client.add_reaction = AsyncMock(return_value=True)
    client.remove_reaction = AsyncMock(return_value=True)
    client.edit_message = AsyncMock(return_value=True)
    client.delete_message = AsyncMock(return_value=True)
    client.me = AsyncMock(return_value={
        "id": "Ubot",
        "login": "testbot",
        "displayName": "Test Bot",
    })
    client.close = AsyncMock()
    return client


@pytest.fixture
def bot(bot_config, mock_client):
    b = Bot.__new__(Bot)
    b.config = bot_config
    b.client = mock_client
    b._commands = {}
    b._event_handlers = []
    b._cogs = {}
    b._extensions = {}
    b.user = User(id="Ubot", login="testbot", display_name="Test Bot")
    b._closed = False
    b._stop_event = None
    b._state_path = MagicMock()
    b._cursor = {}
    b._state_dirty = False
    b._state_flush_task = None
    b._middleware = MiddlewareChain()
    b._subscriptions = MagicMock()
    b._subscriptions.stop = AsyncMock()
    b._config_path = None
    b._init_kwargs = {}
    return b


def make_event(
    body: str = "hello",
    actor_id: str = "Uuser",
    actor_login: str = "testuser",
    space_id: str = "S1",
    room_id: str = "R1",
    event_id: str = "E1",
    in_thread: str | None = None,
) -> SpaceEvent:
    """Create a SpaceEvent for testing."""
    return SpaceEvent(
        id=event_id,
        created_at="2026-01-01T00:00:00Z",
        actor_id=actor_id,
        space_id=space_id,
        event=MessagePostedEvent(
            room_id=room_id,
            body=body,
            in_thread=in_thread,
        ),
        actor=User(
            id=actor_id,
            login=actor_login,
            display_name=actor_login.title(),
        ),
    )


def make_ctx(bot, event=None, **kwargs) -> Context:
    """Create a Context for testing."""
    if event is None:
        event = make_event(**kwargs)
    return Context(bot, event)
