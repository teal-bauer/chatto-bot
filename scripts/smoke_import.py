#!/usr/bin/env python3
"""Smoke test for the chattolib rebase: no network, just the wiring.

Builds a real ``Bot``, registers a command, and drives a synthetic
``message_posted`` realtime envelope (a real google-protobuf message built
from chattolib's bundled ``realtime_pb2``) through:

    Realtime -> Bot._on_envelope -> Hydrator.hydrate (client.get_message
    mocked to return a chattolib ``Message``) -> types.parse_envelope ->
    Context -> command dispatch -> ctx.reply -> Client.create_message

Only the two network-calling edges (``get_message`` and ``create_message``)
are mocked; everything in between -- hydration, oneof unwrapping, event-name
resolution, command parsing/dispatch, Context -- is the real code path.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from chattolib._pb.chatto.realtime.v1 import realtime_pb2 as rt
from chattolib.types import Message, Room, RoomKind
from google.protobuf.timestamp_pb2 import Timestamp

from chatto_bot.bot import Bot


async def main() -> None:
    bot = Bot(instance="https://smoke.example.invalid", token="fake-token", prefix="!")

    replies: list[tuple[str, str]] = []

    @bot.command(name="ping")
    async def ping(ctx):
        reply_body = "pong"
        await ctx.reply(reply_body)
        replies.append((ctx.room_id, reply_body))

    # Mock the two edges that would otherwise hit the network.
    hydrated_message = Message(
        id="E1",
        room_id="R1",
        created_at=None,
        actor_id="U1",
        body="!ping",
    )
    bot.client.get_message = AsyncMock(return_value=hydrated_message)
    bot.client.create_message = AsyncMock(return_value=hydrated_message)
    bot.users.get = AsyncMock(return_value=None)
    bot.client.get_room = AsyncMock(return_value=Room(id="R1", kind=RoomKind.CHANNEL))

    created_at = Timestamp()
    created_at.FromDatetime(datetime.now(timezone.utc))
    envelope = rt.RealtimeEventEnvelope(
        id="Eevt1",
        actor_id="U1",
        created_at=created_at,
        message_posted=rt.RealtimeMessagePostedEvent(room_id="R1", message_event_id="E1"),
    )

    await bot._on_envelope(envelope)

    assert replies == [("R1", "pong")], f"command did not dispatch as expected: {replies}"
    bot.client.get_message.assert_awaited_once_with("R1", "E1")
    bot.client.create_message.assert_awaited_once()
    call_args = bot.client.create_message.call_args
    assert call_args.args[:2] == ("R1", "pong"), f"unexpected reply call: {call_args}"

    print("OK: message_posted envelope -> hydrate -> parse_envelope -> Context -> "
          "command dispatch -> reply, all without network")
    print(f"  replies dispatched: {replies}")
    print(f"  create_message call args: {call_args}")


if __name__ == "__main__":
    asyncio.run(main())
