"""Context object wrapping a SpaceEvent with convenience methods."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .types import (
    MessagePostedEvent,
    MessageUpdatedEvent,
    ReactionAddedEvent,
    ReactionRemovedEvent,
    SpaceEvent,
    User,
)

if TYPE_CHECKING:
    from .bot import Bot


class Context:
    """Event context passed to command and event handlers.

    Provides the raw event plus convenience methods for common actions
    like replying, reacting, etc.
    """

    def __init__(self, bot: Bot, event: SpaceEvent) -> None:
        self.bot = bot
        self.event = event

    @property
    def actor(self) -> User | None:
        return self.event.actor

    @property
    def space_id(self) -> str:
        """Extract space_id from the inner event (most event types have it)."""
        inner = self.event.event
        if hasattr(inner, "space_id"):
            return inner.space_id
        return ""

    @property
    def room_id(self) -> str:
        """Extract room_id from the inner event."""
        inner = self.event.event
        if hasattr(inner, "room_id"):
            return inner.room_id
        return ""

    @property
    def is_dm(self) -> bool:
        """Whether this event is from a direct message."""
        return self.space_id == "DM"

    @property
    def body(self) -> str | None:
        """Message body, if the event is a message type."""
        inner = self.event.event
        if hasattr(inner, "body"):
            return inner.body
        return None

    @property
    def message_body_id(self) -> str | None:
        """Message body ID for editing/deleting."""
        inner = self.event.event
        if hasattr(inner, "message_body_id"):
            return inner.message_body_id
        return None

    @property
    def event_id(self) -> str:
        """The SpaceEvent id (used for replies and reactions)."""
        return self.event.id

    async def reply(self, body: str, **kwargs: Any) -> dict:
        """Reply in the same room (top-level message)."""
        return await self.bot.client.post_message(
            self.space_id, self.room_id, body, **kwargs
        )

    async def reply_in_thread(self, body: str) -> dict:
        """Reply in the thread of the current message.

        If the current message is already in a thread, replies to the thread root.
        Otherwise, starts a new thread on the current message.
        """
        inner = self.event.event
        # Determine the thread root event ID
        thread_root = self.event.id
        if isinstance(inner, MessagePostedEvent) and inner.in_thread:
            thread_root = inner.in_thread

        return await self.bot.client.post_message(
            self.space_id, self.room_id, body, in_reply_to=thread_root
        )

    async def react(self, emoji: str) -> bool:
        """Add a reaction to the message that triggered this event."""
        return await self.bot.client.add_reaction(
            self.space_id, self.room_id, self.event.id, emoji
        )

    async def unreact(self, emoji: str) -> bool:
        """Remove a reaction from the triggering message."""
        return await self.bot.client.remove_reaction(
            self.space_id, self.room_id, self.event.id, emoji
        )

    async def edit(self, body: str) -> bool:
        """Edit the bot's own message (only works if the event is the bot's)."""
        if not self.message_body_id:
            return False
        return await self.bot.client.edit_message(
            self.space_id, self.room_id, self.message_body_id, body
        )

    async def delete(self) -> bool:
        """Delete the bot's own message."""
        if not self.message_body_id:
            return False
        return await self.bot.client.delete_message(
            self.space_id, self.room_id, self.message_body_id
        )
