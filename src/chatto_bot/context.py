"""Context object wrapping a RoomEvent with convenience methods.

Built fresh for every dispatched event (see ``Bot._dispatch``), from the
already-hydrated ``RoomEvent`` (public dataclasses; see ``types.py``) plus a
back-reference to the ``Bot`` for the actions (``reply``, ``react``, ...).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .client import ChattoError
from .types import MessagePostedEvent, RoomEvent, User

if TYPE_CHECKING:
    from ._pb.chatto.api.v1.message_types_pb import Message
    from .bot import Bot

logger = logging.getLogger(__name__)


class Context:
    """Event context passed to command and event handlers.

    Provides the raw event plus convenience methods for common actions
    like replying, reacting, etc.
    """

    def __init__(self, bot: Bot, event: RoomEvent) -> None:
        self.bot = bot
        self.event = event

    @property
    def actor(self) -> User | None:
        return self.event.actor

    @property
    def room_id(self) -> str:
        """Extract room_id from the inner event, if it has one."""
        inner = self.event.event
        return getattr(inner, "room_id", None) or ""

    @property
    def is_dm(self) -> bool:
        """Whether this event is from a direct-message room.

        Resolved via ``Bot._room_kinds``, a room_id -> is_dm cache the bot
        keeps warm from ``ListRooms``/``GetRoom``. ``Bot._dispatch`` always
        resolves ``self.room_id`` into that cache (fetching it via
        ``GetRoom`` on a miss) before a ``Context`` is ever constructed, so
        this stays a plain, synchronous property.
        """
        return self.bot._room_kinds.get(self.room_id, False)

    @property
    def body(self) -> str | None:
        """Message body, if the event is a message type."""
        inner = self.event.event
        return getattr(inner, "body", None)

    @property
    def event_id(self) -> str:
        """The RoomEvent id (used for replies and reactions)."""
        return self.event.id

    @property
    def in_thread(self) -> str | None:
        """The thread root event ID if this event is in a thread."""
        inner = self.event.event
        if isinstance(inner, MessagePostedEvent) and inner.thread_root_event_id:
            return inner.thread_root_event_id
        return None

    async def reply(self, body: str, **kwargs: Any) -> Message:
        """Reply in the same room.

        If triggered from within a thread, the reply stays in that thread
        (``thread_root_event_id`` is set) unless the caller overrides it.
        ``in_reply_to`` is attribution only -- pass it explicitly if you want
        the reply to visibly reference a specific message.
        """
        if self.in_thread and "thread_root_event_id" not in kwargs:
            kwargs["thread_root_event_id"] = self.in_thread
        return await self.bot.client.create_message(self.room_id, body, **kwargs)

    async def reply_in_thread(self, body: str, **kwargs: Any) -> Message:
        """Reply inside the thread rooted at the triggering message.

        If the current message is already in a thread, replies to that
        thread's root. Otherwise starts a new thread rooted at the
        triggering message.
        """
        kwargs.setdefault("thread_root_event_id", self.in_thread or self.event.id)
        return await self.bot.client.create_message(self.room_id, body, **kwargs)

    async def react(self, emoji: str) -> None:
        """Add a reaction to the message that triggered this event.

        Reactions are cosmetic, so a rejected reaction (message retracted,
        room no longer accessible, ...) is logged and swallowed rather than
        raised -- it shouldn't crash the calling handler.
        """
        try:
            await self.bot.client.add_reaction(self.room_id, self.event_id, emoji)
        except ChattoError:
            logger.warning(
                "Could not add reaction %r to %s/%s",
                emoji,
                self.room_id,
                self.event_id,
                exc_info=True,
            )

    async def unreact(self, emoji: str) -> None:
        """Remove a reaction from the triggering message. See ``react()``."""
        try:
            await self.bot.client.remove_reaction(self.room_id, self.event_id, emoji)
        except ChattoError:
            logger.warning(
                "Could not remove reaction %r from %s/%s",
                emoji,
                self.room_id,
                self.event_id,
                exc_info=True,
            )

    async def edit(self, body: str) -> Message | None:
        """Edit the bot's own message (only meaningful for message events)."""
        if not isinstance(self.event.event, MessagePostedEvent):
            return None
        try:
            return await self.bot.client.update_message(self.room_id, self.event_id, body)
        except ChattoError:
            logger.warning(
                "Could not edit %s/%s", self.room_id, self.event_id, exc_info=True
            )
            return None

    async def delete(self) -> bool:
        """Delete the bot's own message."""
        if not isinstance(self.event.event, MessagePostedEvent):
            return False
        try:
            await self.bot.client.delete_message(self.room_id, self.event_id)
            return True
        except ChattoError:
            logger.warning(
                "Could not delete %s/%s", self.room_id, self.event_id, exc_info=True
            )
            return False

    async def fetch_message(self, event_id: str | None = None) -> Message | None:
        """Fetch the current state of a message.

        Useful inside a ``message_updated`` handler, since the update event
        carries only the event id. Call ``await ctx.fetch_message()`` to
        retrieve the new body, attachments, reactions, etc.

        If ``event_id`` is omitted, falls back to the inner event's
        ``message_event_id`` (set on update/delete events) or the wrapper's
        own id.
        """
        if event_id is None:
            inner = self.event.event
            event_id = getattr(inner, "message_event_id", None) or self.event.id
        if not event_id:
            return None
        return await self.bot.client.get_message(self.room_id, event_id)
