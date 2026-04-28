"""WebSocket subscription manager using the graphql-transport-ws protocol."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Awaitable, Callable

import websockets

from ._queries import SPACE_EVENT_FRAGMENT
from .types import SpaceEvent, parse_instance_event, parse_space_event

if TYPE_CHECKING:
    from .config import BotConfig

logger = logging.getLogger(__name__)

SPACE_EVENTS_QUERY = SPACE_EVENT_FRAGMENT + """
subscription SpaceEvents($spaceId: ID!) {
    mySpaceEvents(spaceId: $spaceId) { ...SpaceEventFields }
}
"""


INSTANCE_EVENTS_QUERY = """\
subscription InstanceEvents {
    myInstanceEvents {
        actorId
        event {
            __typename
            ... on InstanceConfigUpdatedEvent {
                instanceName motd welcomeMessage
            }
            ... on SpaceCreatedEvent { spaceId }
            ... on SpaceUpdatedEvent {
                spaceId name description logoUrl bannerUrl
            }
            ... on SpaceDeletedEvent { spaceId }
            ... on UserJoinedSpaceEvent { spaceId }
            ... on UserLeftSpaceEvent { spaceId }
            ... on UserProfileUpdatedEvent {
                userId displayName avatarUrl login
            }
            ... on InstanceUserPreferencesUpdatedEvent {
                timezone timeFormat
            }
            ... on NotificationLevelChangedEvent {
                spaceId roomId level effectiveLevel
            }
            ... on MentionNotificationEvent {
                spaceId roomId
                space { name }
                room { name }
                actor { id displayName }
            }
            ... on NewDirectMessageNotificationEvent {
                roomId
                sender { id displayName avatarUrl }
                conversationName
            }
            ... on NotificationCreatedEvent {
                notificationId spaceId roomId eventId inReplyToId
            }
            ... on NotificationDismissedEvent { notificationId }
            ... on NewMessageInSpaceEvent { spaceId roomId }
            ... on RoomMarkedAsReadEvent { spaceId roomId }
            ... on ThreadFollowChangedEvent {
                spaceId roomId threadRootEventId isFollowing
            }
            ... on RoomLayoutUpdatedEvent { spaceId }
            ... on SessionTerminatedEvent { reason }
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

    async def subscribe(
        self,
        space_id: str,
        callback: Callable[[SpaceEvent], Awaitable[None]],
    ) -> None:
        """Subscribe to a space's events with auto-reconnect."""
        backoff = 1.0

        while True:
            try:
                await self._run_subscription(space_id, callback)
            except asyncio.CancelledError:
                logger.info("Subscription cancelled for space %s", space_id)
                return
            except Exception:
                logger.exception(
                    "Subscription error for space %s, reconnecting in %.1fs",
                    space_id,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
            else:
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
            await ws.send(json.dumps({"type": "connection_init"}))
            ack = json.loads(await ws.recv())
            if ack.get("type") != "connection_ack":
                raise RuntimeError(f"Expected connection_ack, got: {ack}")

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

            async for raw in ws:
                msg = json.loads(raw)
                msg_type = msg.get("type")

                if msg_type == "next":
                    try:
                        event_data = msg["payload"]["data"]["mySpaceEvents"]
                        event = parse_space_event(event_data, space_id=space_id)
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

    async def _run_instance_subscription(
        self,
        callback: Callable[[SpaceEvent], Awaitable[None]] | None,
    ) -> None:
        """Subscribe to myInstanceEvents and dispatch events through callback.

        If ``callback`` is None the subscription still runs (for presence
        keepalive) but events are discarded.
        """
        headers = {
            "Cookie": self.config.cookie_header,
            "Origin": self.config.instance,
        }

        logger.info("Connecting instance subscription")

        async with websockets.connect(
            self.config.ws_url,
            subprotocols=["graphql-transport-ws"],
            additional_headers=headers,
        ) as ws:
            await ws.send(json.dumps({"type": "connection_init"}))
            ack = json.loads(await ws.recv())
            if ack.get("type") != "connection_ack":
                raise RuntimeError(f"Expected connection_ack, got: {ack}")

            await ws.send(json.dumps({
                "id": "instance",
                "type": "subscribe",
                "payload": {"query": INSTANCE_EVENTS_QUERY},
            }))
            logger.info("Instance subscription active (presence online)")

            async for raw in ws:
                msg = json.loads(raw)
                msg_type = msg.get("type")

                if msg_type == "next" and callback is not None:
                    try:
                        event_data = msg["payload"]["data"]["myInstanceEvents"]
                        event = parse_instance_event(event_data)
                        await callback(event)
                    except Exception:
                        logger.exception("Error processing instance event")

                elif msg_type == "error":
                    logger.error("Instance subscription error: %s", msg.get("payload"))

                elif msg_type == "complete":
                    logger.info("Instance subscription completed by server")
                    break

                elif msg_type == "ping":
                    await ws.send(json.dumps({"type": "pong"}))

    async def _instance_subscribe_loop(
        self,
        callback: Callable[[SpaceEvent], Awaitable[None]] | None,
    ) -> None:
        """Keep instance subscription alive with auto-reconnect."""
        backoff = 1.0

        while True:
            try:
                await self._run_instance_subscription(callback)
            except asyncio.CancelledError:
                logger.info("Instance subscription cancelled")
                return
            except Exception:
                logger.exception(
                    "Instance subscription error, reconnecting in %.1fs", backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
            else:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

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

    def start_instance(
        self,
        callback: Callable[[SpaceEvent], Awaitable[None]] | None = None,
    ) -> None:
        """Start the instance subscription.

        If a ``callback`` is provided, instance events are dispatched through
        it; otherwise the subscription runs purely for presence.
        """
        if "_instance" not in self._tasks:
            task = asyncio.create_task(
                self._instance_subscribe_loop(callback),
                name="sub-instance",
            )
            self._tasks["_instance"] = task

    async def stop_one(self, space_id: str) -> None:
        """Stop a single subscription by space ID."""
        task = self._tasks.pop(space_id, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def stop(self) -> None:
        """Stop all subscriptions."""
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
