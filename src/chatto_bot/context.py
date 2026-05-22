"""Context object wrapping a RoomEvent with convenience methods."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .types import MessagePostedEvent, RoomEvent, User

if TYPE_CHECKING:
    from .bot import Bot


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
        """Extract room_id from the inner event."""
        inner = self.event.event
        if hasattr(inner, "room_id"):
            return inner.room_id
        return ""

    @property
    def is_dm(self) -> bool:
        """Whether this event is from a direct message room."""
        return self.bot._room_types.get(self.room_id) == "DM"

    @property
    def body(self) -> str | None:
        """Message body, if the event is a message type."""
        inner = self.event.event
        if hasattr(inner, "body"):
            return inner.body
        return None

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

    async def reply(self, body: str, **kwargs: Any) -> dict:
        """Reply in the same room. If triggered from a thread, replies in that thread."""
        if self.in_thread and "in_reply_to" not in kwargs:
            kwargs["in_reply_to"] = self.in_thread
        return await self.bot.client.post_message(self.room_id, body, **kwargs)

    async def reply_in_thread(self, body: str) -> dict:
        """Reply in the thread of the current message.

        If the current message is already in a thread, replies to the thread root.
        Otherwise, starts a new thread on the current message.
        """
        inner = self.event.event
        thread_root = self.event.id
        if isinstance(inner, MessagePostedEvent) and inner.thread_root_event_id:
            thread_root = inner.thread_root_event_id

        return await self.bot.client.post_message(
            self.room_id, body, in_reply_to=thread_root
        )

    async def react(self, emoji: str) -> bool:
        """Add a reaction to the message that triggered this event."""
        return await self.bot.client.add_reaction(self.room_id, self.event.id, emoji)

    async def unreact(self, emoji: str) -> bool:
        """Remove a reaction from the triggering message."""
        return await self.bot.client.remove_reaction(self.room_id, self.event.id, emoji)

    async def edit(self, body: str) -> bool:
        """Edit the bot's own message (only works if the event is the bot's)."""
        if not isinstance(self.event.event, MessagePostedEvent):
            return False
        return await self.bot.client.edit_message(self.room_id, self.event_id, body)

    async def delete(self) -> bool:
        """Delete the bot's own message."""
        if not isinstance(self.event.event, MessagePostedEvent):
            return False
        return await self.bot.client.delete_message(self.room_id, self.event_id)

    async def fetch_message(self, event_id: str | None = None) -> dict | None:
        """Fetch the current state of a message.

        Useful inside a ``message_updated`` handler, since the update event
        carries only the event id — call ``await ctx.fetch_message()`` to
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
        return await self.bot.client.get_event(self.room_id, event_id)
