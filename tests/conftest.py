"""Shared fixtures for chatto-bot tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chatto_bot.bot import Bot
from chatto_bot.client import Client
from chatto_bot.config import BotConfig
from chatto_bot.context import Context
from chatto_bot.middleware import MiddlewareChain
from chatto_bot.types import (
    MessagePostedEvent,
    RoomEvent,
    User,
)


@pytest.fixture
def bot_config():
    return BotConfig(
        instance="https://test.example.com",
        prefix="!",
        session="fake-session",
        admins=["admin"],
    )


@pytest.fixture
def mock_client(bot_config):
    client = MagicMock(spec=Client)
    client.create_message = AsyncMock(return_value=MagicMock(id="E1"))
    client.add_reaction = AsyncMock(return_value=None)
    client.remove_reaction = AsyncMock(return_value=None)
    client.update_message = AsyncMock(return_value=MagicMock(id="E1"))
    client.delete_message = AsyncMock(return_value=None)
    # Room-kind resolution (Bot._ensure_room_kind) defaults every room to a
    # plain channel unless a test overrides it.
    client.get_room = AsyncMock(return_value=MagicMock(kind=MagicMock()))
    client.get_viewer = AsyncMock(
        return_value=MagicMock(
            profile=MagicMock(
                id="Ubot",
                login="testbot",
                display_name="Test Bot",
                avatar_url=None,
                presence_status=None,
            )
        )
    )
    client.update_presence = AsyncMock(return_value=None)
    client.list_rooms = AsyncMock(return_value=[])
    client.close = AsyncMock()
    return client


@pytest.fixture
def bot(bot_config, mock_client):
    b = Bot.__new__(Bot)
    b.config = bot_config
    b.client = mock_client

    b.transport = MagicMock()
    b.transport.token = "fake-token"
    b.transport.session = None
    b.transport.identifier = None
    b.transport.password = None
    b.transport.relogin = AsyncMock()
    b.transport.close = AsyncMock()

    b.users = MagicMock()
    b.users.get = AsyncMock(return_value=None)
    b.users.get_many = AsyncMock(return_value={})
    b.users.invalidate = MagicMock()

    b.hydrator = MagicMock()
    b.hydrator.hydrate = AsyncMock(return_value=None)

    b.realtime = MagicMock()
    b.realtime.stop = MagicMock()

    b._commands = {}
    b._event_handlers = []
    b._cogs = {}
    b._extensions = {}
    b.user = User(id="Ubot", login="testbot", display_name="Test Bot")
    b._closed = False
    b._stop_event = None
    b._realtime_task = None
    b._presence_task = None
    b._state_path = MagicMock()
    b._cursor = {}
    b._room_kinds = {}
    b._state_dirty = False
    b._state_flush_task = None
    b._middleware = MiddlewareChain()
    b._config_path = None
    b._init_kwargs = {}
    return b


def make_event(
    body: str = "hello",
    actor_id: str = "Uuser",
    actor_login: str = "testuser",
    room_id: str = "R1",
    event_id: str = "E1",
    thread_root_event_id: str | None = None,
) -> RoomEvent:
    """Create a RoomEvent for testing."""
    return RoomEvent(
        id=event_id,
        created_at="2026-01-01T00:00:00Z",
        actor_id=actor_id,
        event=MessagePostedEvent(
            room_id=room_id,
            body=body,
            thread_root_event_id=thread_root_event_id,
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
