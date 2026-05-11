"""chatto-bot: Python bot framework for Chatto."""

from .version import __version__
from .bot import Bot
from .client import Client, GraphQLError, login
from .cog import Cog
from .command import Command, CommandError, command
from .config import BotConfig
from .context import Context
from .event import EventHandler, on_event
from .middleware import MiddlewareChain
from .types import (
    # Leaf types
    Attachment,
    LinkPreview,
    Reaction,
    RoomEvent,
    SpaceEvent,  # backward-compat alias for RoomEvent
    User,
    VideoProcessing,
    VideoProcessingVariant,
    # Unions
    EventType,
    InstanceInnerEvent,
    RoomInnerEvent,
    SpaceInnerEvent,  # backward-compat alias for RoomInnerEvent
    # Catch-all
    UnknownEvent,
    # Room (server-wide) inner events
    CallParticipantJoinedEvent,
    CallParticipantLeftEvent,
    MessageDeletedEvent,
    MessagePostedEvent,
    MessageUpdatedEvent,
    PresenceChangedEvent,
    ReactionAddedEvent,
    ReactionRemovedEvent,
    RoomArchivedEvent,
    RoomCreatedEvent,
    RoomDeletedEvent,
    RoomUnarchivedEvent,
    RoomUpdatedEvent,
    ServerMemberDeletedEvent,
    UserJoinedRoomEvent,
    UserLeftRoomEvent,
    UserTypingEvent,
    VideoProcessingCompletedEvent,
    # Instance inner events
    ServerConfigUpdatedEvent,
    ServerUserPreferencesUpdatedEvent,
    MentionNotificationEvent,
    NewDirectMessageNotificationEvent,
    NewMessageInServerEvent,
    NotificationCreatedEvent,
    NotificationDismissedEvent,
    NotificationLevelChangedEvent,
    RoomLayoutUpdatedEvent,
    RoomMarkedAsReadEvent,
    ServerUpdatedEvent,
    SessionTerminatedEvent,
    ThreadFollowChangedEvent,
    UserCreatedEvent,
    UserDeletedEvent,
    UserJoinedServerEvent,
    UserLeftServerEvent,
    UserProfileUpdatedEvent,
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
    "__version__",
    # Leaf types
    "Attachment",
    "LinkPreview",
    "Reaction",
    "RoomEvent",
    "SpaceEvent",
    "User",
    "VideoProcessing",
    "VideoProcessingVariant",
    # Unions
    "EventType",
    "InstanceInnerEvent",
    "RoomInnerEvent",
    "SpaceInnerEvent",
    "UnknownEvent",
    # Room (server-wide) inner events
    "CallParticipantJoinedEvent",
    "CallParticipantLeftEvent",
    "MessageDeletedEvent",
    "MessagePostedEvent",
    "MessageUpdatedEvent",
    "PresenceChangedEvent",
    "ReactionAddedEvent",
    "ReactionRemovedEvent",
    "RoomArchivedEvent",
    "RoomCreatedEvent",
    "RoomDeletedEvent",
    "RoomUnarchivedEvent",
    "RoomUpdatedEvent",
    "ServerMemberDeletedEvent",
    "UserJoinedRoomEvent",
    "UserLeftRoomEvent",
    "UserTypingEvent",
    "VideoProcessingCompletedEvent",
    # Instance inner events
    "ServerConfigUpdatedEvent",
    "ServerUserPreferencesUpdatedEvent",
    "MentionNotificationEvent",
    "NewDirectMessageNotificationEvent",
    "NewMessageInServerEvent",
    "NotificationCreatedEvent",
    "NotificationDismissedEvent",
    "NotificationLevelChangedEvent",
    "RoomLayoutUpdatedEvent",
    "RoomMarkedAsReadEvent",
    "ServerUpdatedEvent",
    "SessionTerminatedEvent",
    "ThreadFollowChangedEvent",
    "UserCreatedEvent",
    "UserDeletedEvent",
    "UserJoinedServerEvent",
    "UserLeftServerEvent",
    "UserProfileUpdatedEvent",
]
