"""Tests for client.py's request/response mapping onto chattolib.

D5: closes the loop on the reply-threading risk one layer below
``Context`` (see ``test_context.py``) -- verifies ``Client.create_message``
puts ``thread_root_event_id`` (not ``in_reply_to``) onto the actual
``ChattoClient.post_message`` call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from chattolib.exceptions import ChattoAuthError, ChattoConnectError

from chatto_bot.client import Client, ChattoError, Unauthenticated


@pytest.fixture
def client_with_fake_chatto():
    """A real ``Client`` wired to a fake ``ChattoClient`` whose
    ``post_message`` just records the kwargs it was given."""
    fake_chatto = MagicMock()
    fake_chatto.post_message = AsyncMock(return_value=MagicMock(id="E1"))

    transport = MagicMock()
    transport.chatto_client = fake_chatto

    return Client(transport), fake_chatto


class TestCreateMessageThreading:
    @pytest.mark.asyncio
    async def test_thread_root_event_id_reaches_the_request(self, client_with_fake_chatto):
        client, fake_chatto = client_with_fake_chatto

        await client.create_message("R1", "hi", thread_root_event_id="Eroot")

        _, kwargs = fake_chatto.post_message.call_args
        assert kwargs["thread_root_event_id"] == "Eroot"
        assert kwargs["in_reply_to"] == ""

    @pytest.mark.asyncio
    async def test_in_reply_to_is_attribution_only_and_independent(
        self, client_with_fake_chatto
    ):
        """in_reply_to can be set alongside (or instead of)
        thread_root_event_id -- it's a separate, attribution-only field, not
        an alias for threading."""
        client, fake_chatto = client_with_fake_chatto

        await client.create_message(
            "R1", "hi", in_reply_to="Eattrib", thread_root_event_id="Eroot"
        )

        _, kwargs = fake_chatto.post_message.call_args
        assert kwargs["thread_root_event_id"] == "Eroot"
        assert kwargs["in_reply_to"] == "Eattrib"

    @pytest.mark.asyncio
    async def test_no_threading_by_default(self, client_with_fake_chatto):
        client, fake_chatto = client_with_fake_chatto

        await client.create_message("R1", "hi")

        _, kwargs = fake_chatto.post_message.call_args
        assert kwargs["thread_root_event_id"] == ""
        assert kwargs["in_reply_to"] == ""


class TestErrorMapping:
    """D2's Unauthenticated-vs-ChattoError distinction now hinges on
    translating chattolib's exceptions, not connectrpc's -- this is new
    coverage for that seam."""

    @pytest.mark.asyncio
    async def test_connect_error_becomes_chatto_error(self, client_with_fake_chatto):
        client, fake_chatto = client_with_fake_chatto
        fake_chatto.get_room = AsyncMock(
            side_effect=ChattoConnectError("failed_precondition", "nope")
        )

        with pytest.raises(ChattoError) as exc_info:
            await client.get_room("R1")
        assert not isinstance(exc_info.value, Unauthenticated)
        assert exc_info.value.code == "failed_precondition"

    @pytest.mark.asyncio
    async def test_unauthenticated_connect_error_becomes_unauthenticated(
        self, client_with_fake_chatto
    ):
        client, fake_chatto = client_with_fake_chatto
        fake_chatto.get_room = AsyncMock(
            side_effect=ChattoConnectError("unauthenticated", "token expired")
        )

        with pytest.raises(Unauthenticated):
            await client.get_room("R1")

    @pytest.mark.asyncio
    async def test_auth_error_becomes_unauthenticated(self, client_with_fake_chatto):
        client, fake_chatto = client_with_fake_chatto
        fake_chatto.get_room = AsyncMock(side_effect=ChattoAuthError("no session"))

        with pytest.raises(Unauthenticated):
            await client.get_room("R1")

    @pytest.mark.asyncio
    async def test_get_message_not_found_returns_none(self, client_with_fake_chatto):
        """D2/D-message-hydration: get_message must swallow not_found (a
        message retracted between an event signal and this fetch) rather
        than raising, so Hydrator can treat it as "drop this dispatch"."""
        client, fake_chatto = client_with_fake_chatto
        fake_chatto.get_message = AsyncMock(
            side_effect=ChattoConnectError("not_found", "gone")
        )

        result = await client.get_message("R1", "E1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_message_other_errors_propagate(self, client_with_fake_chatto):
        client, fake_chatto = client_with_fake_chatto
        fake_chatto.get_message = AsyncMock(
            side_effect=ChattoConnectError("internal", "boom")
        )

        with pytest.raises(ChattoError):
            await client.get_message("R1", "E1")


class TestGetRoomUnwrapsViewerState:
    """chattolib's ``get_room`` returns a ``RoomWithViewerState``; bot.py
    (room-kind resolution) expects a plain ``Room`` back, matching the old
    ConnectRPC client's ``resp.room.room`` unwrap."""

    @pytest.mark.asyncio
    async def test_unwraps_room(self, client_with_fake_chatto):
        client, fake_chatto = client_with_fake_chatto
        room = MagicMock(id="R1")
        fake_chatto.get_room = AsyncMock(return_value=MagicMock(room=room))

        result = await client.get_room("R1")
        assert result is room

    @pytest.mark.asyncio
    async def test_none_stays_none(self, client_with_fake_chatto):
        client, fake_chatto = client_with_fake_chatto
        fake_chatto.get_room = AsyncMock(return_value=None)

        result = await client.get_room("R1")
        assert result is None
