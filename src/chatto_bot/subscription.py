"""WebSocket subscription manager using the graphql-transport-ws protocol."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Awaitable, Callable

import websockets

from .types import RoomEvent, parse_my_event

if TYPE_CHECKING:
    from .config import BotConfig

logger = logging.getLogger(__name__)


# Single Chatto subscription endpoint. ``myEvents`` returns a ``ServerEvent``
# wrapping a union (``ServerEventType``) covering every room- and server-level
# event the viewer can see, plus a periodic ``HeartbeatEvent`` keepalive.
#
# Notes on aliases:
# - ``NotificationLevelChangedEvent.roomId`` is nullable while every other
#   event's ``roomId`` is non-null. Aliased to ``nlcRoomId`` to dodge
#   graphql-go's selection-set merge check.
# - ``MessagePostedEvent.threadRootEventId`` and
#   ``UserTypingEvent.threadRootEventId`` are nullable, but
#   ``ThreadFollowChangedEvent.threadRootEventId`` is non-null. The
#   nullable ones are aliased to ``mpThreadRootEventId`` /
#   ``utThreadRootEventId`` for the same reason.
MY_EVENTS_QUERY = """\
subscription MyEvents {
    myEvents {
        id
        createdAt
        actorId
        actor { id login displayName avatarUrl presenceStatus }
        event {
            __typename
            ... on MessagePostedEvent {
                roomId body
                attachments {
                    id roomId filename contentType width height url thumbnailUrl
                    videoProcessing {
                        status durationMs width height thumbnailUrl errorMessage
                        variants { url quality width height size }
                    }
                }
                linkPreview {
                    url title description imageUrl siteName embedType embedId
                }
                inReplyTo mpThreadRootEventId: threadRootEventId
                reactions { emoji count users { id login displayName } hasReacted }
                updatedAt replyCount lastReplyAt
                echoOfEventId echoFromThreadRootEventId
                threadParticipants(first: 5) {
                    id login displayName avatarUrl presenceStatus
                }
                viewerIsFollowingThread
            }
            ... on MessageUpdatedEvent { roomId messageEventId }
            ... on MessageDeletedEvent { roomId messageEventId }
            ... on RoomCreatedEvent { roomId name description }
            ... on UserJoinedRoomEvent { roomId }
            ... on UserLeftRoomEvent { roomId }
            ... on RoomUpdatedEvent { roomId }
            ... on RoomDeletedEvent { roomId }
            ... on RoomArchivedEvent { roomId }
            ... on RoomUnarchivedEvent { roomId }
            ... on ReactionAddedEvent { roomId messageEventId emoji }
            ... on ReactionRemovedEvent { roomId messageEventId emoji }
            ... on UserTypingEvent { roomId utThreadRootEventId: threadRootEventId }
            ... on PresenceChangedEvent { status }
            ... on VideoProcessingCompletedEvent {
                roomId attachmentId messageEventId
            }
            ... on ServerMemberDeletedEvent { userId }
            ... on CallParticipantJoinedEvent { roomId }
            ... on CallParticipantLeftEvent { roomId }
            ... on ServerConfigUpdatedEvent {
                serverName motd welcomeMessage blockedUsernames
            }
            ... on ServerUpdatedEvent {
                name description logoUrl bannerUrl
            }
            ... on UserCreatedEvent { userId login displayName }
            ... on UserDeletedEvent { userId }
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
            ... on RoomMarkedAsReadEvent { roomId }
            ... on ThreadFollowChangedEvent {
                roomId threadRootEventId isFollowing
            }
            ... on RoomGroupsUpdatedEvent { changed }
            ... on MentionStatusClearedEvent { roomId }
            ... on SessionTerminatedEvent { reason }
            ... on HeartbeatEvent { alive }
        }
    }
}"""


class SubscriptionManager:
    """Manages the bot's WebSocket subscription to Chatto's ``myEvents`` stream.

    The server collapsed its previously separate room and instance streams into
    a single subscription that emits every event (plus periodic heartbeats)
    the viewer is permitted to see. We keep it alive with auto-reconnect.
    """

    TASK_KEY = "_events"

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self._tasks: dict[str, asyncio.Task] = {}

    async def _run_subscription(
        self,
        callback: Callable[[RoomEvent], Awaitable[None]],
    ) -> None:
        """Run a single ``myEvents`` subscription connection."""
        headers = {
            "Cookie": self.config.cookie_header,
            "Origin": self.config.instance,
        }

        logger.info("Connecting events subscription")

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
                "id": "events",
                "type": "subscribe",
                "payload": {"query": MY_EVENTS_QUERY},
            }))
            logger.info("Events subscription active")

            async for raw in ws:
                msg = json.loads(raw)
                msg_type = msg.get("type")

                if msg_type == "next":
                    try:
                        event_data = msg["payload"]["data"]["myEvents"]
                        event = parse_my_event(event_data)
                        await callback(event)
                    except Exception:
                        logger.exception("Error processing event")

                elif msg_type == "error":
                    logger.error("Events subscription error: %s", msg.get("payload"))

                elif msg_type == "complete":
                    logger.info("Events subscription completed by server")
                    break

                elif msg_type == "ping":
                    await ws.send(json.dumps({"type": "pong"}))

    # A connection that survives this long counts as "healthy": reset
    # backoff so a later disconnect reconnects fast instead of inheriting
    # a 60-second wait from some earlier outage.
    HEALTHY_THRESHOLD = 30.0

    async def _subscribe_loop(
        self,
        callback: Callable[[RoomEvent], Awaitable[None]],
    ) -> None:
        """Keep the events subscription alive with auto-reconnect."""
        backoff = 1.0
        loop = asyncio.get_running_loop()

        while True:
            t0 = loop.time()
            try:
                await self._run_subscription(callback)
                ended = "ended"
            except asyncio.CancelledError:
                logger.info("Events subscription cancelled")
                return
            except Exception:
                logger.exception("Events subscription error")
                ended = "errored"

            if loop.time() - t0 >= self.HEALTHY_THRESHOLD:
                backoff = 1.0

            logger.warning("Events subscription %s, reconnecting in %.1fs", ended, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    def start(
        self,
        callback: Callable[[RoomEvent], Awaitable[None]],
    ) -> asyncio.Task:
        """Start the global ``myEvents`` subscription."""
        if self.TASK_KEY in self._tasks:
            return self._tasks[self.TASK_KEY]
        task = asyncio.create_task(
            self._subscribe_loop(callback),
            name="sub-events",
        )
        self._tasks[self.TASK_KEY] = task
        return task

    async def stop(self) -> None:
        """Stop the subscription."""
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
