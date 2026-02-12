"""chatto-bot: Python bot framework for Chatto."""

from .bot import Bot
from .client import Client, GraphQLError, login
from .cog import Cog
from .command import Command, CommandError, command
from .config import BotConfig
from .context import Context
from .event import EventHandler, on_event
from .middleware import MiddlewareChain
from .types import (
    Attachment,
    EventType,
    MessageDeletedEvent,
    MessagePostedEvent,
    MessageUpdatedEvent,
    PresenceChangedEvent,
    Reaction,
    ReactionAddedEvent,
    ReactionRemovedEvent,
    SpaceEvent,
    User,
    UserJoinedRoomEvent,
    UserLeftRoomEvent,
    UserTypingEvent,
)

__all__ = [
    "Bot",
    "Client",
    "GraphQLError",
    "login",
    "Cog",
    "Command",
    "CommandError",
    "command",
    "BotConfig",
    "Context",
    "EventHandler",
    "on_event",
    "MiddlewareChain",
    # Types
    "Attachment",
    "EventType",
    "MessageDeletedEvent",
    "MessagePostedEvent",
    "MessageUpdatedEvent",
    "PresenceChangedEvent",
    "Reaction",
    "ReactionAddedEvent",
    "ReactionRemovedEvent",
    "SpaceEvent",
    "User",
    "UserJoinedRoomEvent",
    "UserLeftRoomEvent",
    "UserTypingEvent",
]
