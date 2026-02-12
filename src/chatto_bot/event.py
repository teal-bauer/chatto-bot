"""Event handler decorator and event router."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import Context


@dataclass
class EventHandler:
    """A registered event handler."""

    event_type: str  # snake_case event name, e.g. "message_posted"
    callback: Callable[..., Awaitable[None]]
    filters: dict[str, str] = field(default_factory=dict)
    cog: Any = None

    async def invoke(self, ctx: Context) -> None:
        if self.cog is not None:
            await self.callback(self.cog, ctx)
        else:
            await self.callback(ctx)

    def matches(self, ctx: Context) -> bool:
        """Check if this handler's filters match the given context."""
        for key, value in self.filters.items():
            if key == "room" and ctx.room_id != value:
                return False
            if key == "space" and ctx.space_id != value:
                return False
            if key == "actor" and ctx.actor and ctx.actor.id != value:
                return False
        return True


def on_event(
    event_type: str,
    *,
    room: str | None = None,
    space: str | None = None,
    actor: str | None = None,
) -> Callable:
    """Decorator to register a raw event handler.

    event_type is the snake_case event name without "Event" suffix:
        "message_posted", "reaction_added", "user_joined_room", etc.
    """

    def decorator(func: Callable[..., Awaitable[None]]) -> EventHandler:
        filters: dict[str, str] = {}
        if room:
            filters["room"] = room
        if space:
            filters["space"] = space
        if actor:
            filters["actor"] = actor

        handler = EventHandler(
            event_type=event_type,
            callback=func,
            filters=filters,
        )
        func.__event_handler__ = handler  # type: ignore[attr-defined]
        return handler  # type: ignore[return-value]

    return decorator
