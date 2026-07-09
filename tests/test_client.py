"""Tests for client.py's request/response mapping.

D5: closes the loop on the reply-threading risk one layer below
``Context`` (see ``test_context.py``) -- verifies ``Client.create_message``
puts ``thread_root_event_id`` (not ``in_reply_to``) onto the actual
``CreateMessageRequest`` proto sent over the wire.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chatto_bot.client import Client


@pytest.fixture
def client_with_fake_service():
    """A real ``Client`` wired to a fake ``MessageServiceClient`` whose
    ``create_message`` just records the request it was given."""
    fake_service = MagicMock()
    fake_service.create_message = AsyncMock(
        return_value=MagicMock(message=MagicMock(id="E1"))
    )

    transport = MagicMock()
    transport.headers.return_value = {}
    transport.client.return_value = fake_service

    return Client(transport), fake_service


class TestCreateMessageThreading:
    @pytest.mark.asyncio
    async def test_thread_root_event_id_reaches_the_request(self, client_with_fake_service):
        client, fake_service = client_with_fake_service

        await client.create_message("R1", "hi", thread_root_event_id="Eroot")

        req = fake_service.create_message.call_args.args[0]
        assert req.thread_root_event_id == "Eroot"
        assert req.in_reply_to == ""

    @pytest.mark.asyncio
    async def test_in_reply_to_is_attribution_only_and_independent(
        self, client_with_fake_service
    ):
        """in_reply_to can be set alongside (or instead of)
        thread_root_event_id -- it's a separate, attribution-only field, not
        an alias for threading."""
        client, fake_service = client_with_fake_service

        await client.create_message(
            "R1", "hi", in_reply_to="Eattrib", thread_root_event_id="Eroot"
        )

        req = fake_service.create_message.call_args.args[0]
        assert req.thread_root_event_id == "Eroot"
        assert req.in_reply_to == "Eattrib"

    @pytest.mark.asyncio
    async def test_no_threading_by_default(self, client_with_fake_service):
        client, fake_service = client_with_fake_service

        await client.create_message("R1", "hi")

        req = fake_service.create_message.call_args.args[0]
        assert req.thread_root_event_id == ""
        assert req.in_reply_to == ""
