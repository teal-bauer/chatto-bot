"""WebSocket subscription manager using the graphql-transport-ws protocol."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Awaitable, Callable

import websockets

from ._queries import ROOM_EVENT_FRAGMENT
from .types import RoomEvent, parse_instance_event, parse_room_event

if TYPE_CHECKING:
    from .config import BotConfig

logger = logging.getLogger(__name__)

SERVER_EVENTS_QUERY = ROOM_EVENT_FRAGMENT + """
subscription ServerEvents {
    myServerEvents { ...RoomEventFields }
}
"""


INSTANCE_EVENTS_QUERY = """\
subscription InstanceEvents {
    myInstanceEvents {
        actorId
        event {
            __typename
            ... on ServerConfigUpdatedEvent {
                serverName motd welcomeMessage blockedUsernames
            }
            ... on ServerUpdatedEvent {
                name description logoUrl bannerUrl
            }
            ... on UserCreatedEvent { userId login displayName }
            ... on UserDeletedEvent { userId }
            ... on UserJoinedServerEvent { userId }
            ... on UserLeftServerEvent { userId }
            ... on UserProfileUpdatedEvent {
                userId displayName avatarUrl login
            }
            ... on ServerUserPreferencesUpdatedEvent {
                timezone timeFormat
            }
            ... on NotificationLevelChangedEvent {
                nlcRoomId: roomId
                level effectiveLevel
            }
            ... on MentionNotificationEvent {
                roomId
                room { name }
                actor { id displayName }
            }
            ... on NewDirectMessageNotificationEvent {
                roomId
                sender { id displayName avatarUrl }
                conversationName
            }
            ... on NotificationCreatedEvent {
                notificationId roomId eventId inReplyToId
            }
            ... on NotificationDismissedEvent { notificationId }
            ... on NewMessageInServerEvent { roomId }
            ... on RoomMarkedAsReadEvent { roomId }
            ... on ThreadFollowChangedEvent {
                roomId threadRootEventId isFollowing
            }
            ... on RoomLayoutUpdatedEvent { changed }
            ... on SessionTerminatedEvent { reason }
        }
    }
}"""


class SubscriptionManager:
    """Manages the bot's WebSocket subscriptions to Chatto.

    The server exposes two streams: ``myServerEvents`` (one global stream of
    all room events the user can see) and ``myInstanceEvents`` (account- and
    instance-wide notifications). Both are kept alive with auto-reconnect.
    """

    SERVER_TASK_KEY = "_server"
    INSTANCE_TASK_KEY = "_instance"

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self._tasks: dict[str, asyncio.Task] = {}

    async def _run_server_subscription(
        self,
        callback: Callable[[RoomEvent], Awaitable[None]],
    ) -> None:
        """Run a single ``myServerEvents`` subscription connection."""
        headers = {
            "Cookie": self.config.cookie_header,
            "Origin": self.config.instance,
        }

        logger.info("Connecting server subscription")

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
                "id": "server",
                "type": "subscribe",
                "payload": {"query": SERVER_EVENTS_QUERY},
            }))
            logger.info("Server subscription active")

            async for raw in ws:
                msg = json.loads(raw)
                msg_type = msg.get("type")

                if msg_type == "next":
                    try:
                        event_data = msg["payload"]["data"]["myServerEvents"]
                        event = parse_room_event(event_data)
                        await callback(event)
                    except Exception:
                        logger.exception("Error processing event")

                elif msg_type == "error":
                    logger.error("Server subscription error: %s", msg.get("payload"))

                elif msg_type == "complete":
                    logger.info("Server subscription completed by server")
                    break

                elif msg_type == "ping":
                    await ws.send(json.dumps({"type": "pong"}))

    async def _server_subscribe_loop(
        self,
        callback: Callable[[RoomEvent], Awaitable[None]],
    ) -> None:
        """Keep server subscription alive with auto-reconnect."""
        backoff = 1.0

        while True:
            try:
                await self._run_server_subscription(callback)
            except asyncio.CancelledError:
                logger.info("Server subscription cancelled")
                return
            except Exception:
                logger.exception(
                    "Server subscription error, reconnecting in %.1fs", backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
            else:
                logger.warning(
                    "Server subscription ended, reconnecting in %.1fs", backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _run_instance_subscription(
        self,
        callback: Callable[[RoomEvent], Awaitable[None]] | None,
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
        callback: Callable[[RoomEvent], Awaitable[None]] | None,
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

    def start_server(
        self,
        callback: Callable[[RoomEvent], Awaitable[None]],
    ) -> asyncio.Task:
        """Start the global ``myServerEvents`` subscription."""
        if self.SERVER_TASK_KEY in self._tasks:
            return self._tasks[self.SERVER_TASK_KEY]
        task = asyncio.create_task(
            self._server_subscribe_loop(callback),
            name="sub-server",
        )
        self._tasks[self.SERVER_TASK_KEY] = task
        return task

    def start_instance(
        self,
        callback: Callable[[RoomEvent], Awaitable[None]] | None = None,
    ) -> None:
        """Start the instance subscription.

        If a ``callback`` is provided, instance events are dispatched through
        it; otherwise the subscription runs purely for presence.
        """
        if self.INSTANCE_TASK_KEY not in self._tasks:
            task = asyncio.create_task(
                self._instance_subscribe_loop(callback),
                name="sub-instance",
            )
            self._tasks[self.INSTANCE_TASK_KEY] = task

    async def stop(self) -> None:
        """Stop all subscriptions."""
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
