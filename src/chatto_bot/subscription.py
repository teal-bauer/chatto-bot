"""WebSocket subscription manager using the graphql-transport-ws protocol."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Callable, Awaitable

import websockets

from .types import SpaceEvent, parse_space_event

if TYPE_CHECKING:
    from .config import BotConfig

logger = logging.getLogger(__name__)

SPACE_EVENTS_QUERY = """\
subscription SpaceEvents($spaceId: ID!) {
    mySpaceEvents(spaceId: $spaceId) {
        id
        createdAt
        actorId
        actor { id login displayName avatarUrl presenceStatus }
        sequenceId
        event {
            __typename
            ... on MessagePostedEvent {
                spaceId roomId body messageBodyId
                attachments { id filename contentType size width height url }
                inReplyTo inThread
                reactions { emoji count users { id login displayName } hasReacted }
                updatedAt replyCount lastReplyAt
            }
            ... on MessageUpdatedEvent {
                spaceId roomId body messageBodyId
                attachments { id filename contentType size width height url }
                reactions { emoji count users { id login displayName } hasReacted }
            }
            ... on MessageDeletedEvent {
                spaceId roomId messageBodyId
            }
            ... on UserJoinedRoomEvent {
                spaceId roomId
            }
            ... on UserLeftRoomEvent {
                spaceId roomId
            }
            ... on ReactionAddedEvent {
                spaceId roomId messageEventId emoji
            }
            ... on ReactionRemovedEvent {
                spaceId roomId messageEventId emoji
            }
            ... on UserTypingEvent {
                spaceId roomId threadRootEventId
            }
            ... on PresenceChangedEvent {
                status
            }
        }
    }
}"""


class SubscriptionManager:
    """Manages WebSocket subscriptions to Chatto spaces.

    Uses the graphql-transport-ws protocol directly over websockets.
    """

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self._tasks: dict[str, asyncio.Task] = {}
        self._running = False

    async def subscribe(
        self,
        space_id: str,
        callback: Callable[[SpaceEvent], Awaitable[None]],
    ) -> None:
        """Subscribe to a space's events with auto-reconnect."""
        self._running = True
        backoff = 1.0

        while self._running:
            try:
                await self._run_subscription(space_id, callback)
            except asyncio.CancelledError:
                logger.info("Subscription cancelled for space %s", space_id)
                break
            except Exception:
                if not self._running:
                    break
                logger.exception(
                    "Subscription error for space %s, reconnecting in %.1fs",
                    space_id,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
            else:
                # Clean disconnect (shouldn't happen normally)
                if self._running:
                    logger.warning(
                        "Subscription ended for space %s, reconnecting in %.1fs",
                        space_id,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60.0)

    async def _run_subscription(
        self,
        space_id: str,
        callback: Callable[[SpaceEvent], Awaitable[None]],
    ) -> None:
        """Run a single subscription connection."""
        headers = {
            "Cookie": self.config.cookie_header,
            "Origin": self.config.instance,
        }

        logger.info("Connecting subscription for space %s", space_id)

        async with websockets.connect(
            self.config.ws_url,
            subprotocols=["graphql-transport-ws"],
            additional_headers=headers,
        ) as ws:
            # Connection init
            await ws.send(json.dumps({"type": "connection_init"}))
            ack = json.loads(await ws.recv())
            if ack.get("type") != "connection_ack":
                raise RuntimeError(f"Expected connection_ack, got: {ack}")

            # Subscribe
            sub_msg = {
                "id": "1",
                "type": "subscribe",
                "payload": {
                    "query": SPACE_EVENTS_QUERY,
                    "variables": {"spaceId": space_id},
                },
            }
            await ws.send(json.dumps(sub_msg))
            logger.info("Subscribed to space %s", space_id)

            # Process events
            async for raw in ws:
                if not self._running:
                    break

                msg = json.loads(raw)
                msg_type = msg.get("type")

                if msg_type == "next":
                    try:
                        event_data = msg["payload"]["data"]["mySpaceEvents"]
                        event = parse_space_event(event_data)
                        await callback(event)
                    except Exception:
                        logger.exception("Error processing event")

                elif msg_type == "error":
                    logger.error("Subscription error: %s", msg.get("payload"))

                elif msg_type == "complete":
                    logger.info("Subscription completed by server")
                    break

                elif msg_type == "ping":
                    await ws.send(json.dumps({"type": "pong"}))

    def start(
        self,
        space_id: str,
        callback: Callable[[SpaceEvent], Awaitable[None]],
    ) -> asyncio.Task:
        """Start a subscription task for a space."""
        task = asyncio.create_task(
            self.subscribe(space_id, callback),
            name=f"sub-{space_id}",
        )
        self._tasks[space_id] = task
        return task

    async def stop_one(self, space_id: str) -> None:
        """Stop a single subscription by space ID."""
        task = self._tasks.pop(space_id, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def stop(self) -> None:
        """Stop all subscriptions."""
        self._running = False
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
