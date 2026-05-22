"""Dataclasses mirroring the Chatto GraphQL schema.

Covers every inner-event type emitted by the global ``myEvents`` subscription.
Unknown typenames parse to an ``UnknownEvent`` placeholder so the bot keeps
running through API additions.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# --- Leaf types ---


@dataclass
class User:
    id: str
    login: str = ""
    display_name: str = ""
    avatar_url: str | None = None
    presence_status: str = "OFFLINE"


@dataclass
class VideoProcessingVariant:
    url: str = ""
    quality: str = ""
    width: int = 0
    height: int = 0
    size: int = 0


@dataclass
class VideoProcessing:
    status: str = ""
    duration_ms: int = 0
    width: int = 0
    height: int = 0
    thumbnail_url: str | None = None
    variants: list[VideoProcessingVariant] = field(default_factory=list)
    error_message: str | None = None


@dataclass
class Attachment:
    id: str
    filename: str = ""
    content_type: str = ""
    room_id: str = ""
    width: int = 0
    height: int = 0
    url: str = ""
    thumbnail_url: str | None = None
    video_processing: VideoProcessing | None = None


@dataclass
class Reaction:
    emoji: str
    count: int
    users: list[User] = field(default_factory=list)
    has_reacted: bool = False


@dataclass
class LinkPreview:
    url: str = ""
    title: str | None = None
    description: str | None = None
    image_url: str | None = None
    site_name: str | None = None
    embed_type: str | None = None
    embed_id: str | None = None


# --- Inner event union: room (server-wide) subscription ---


@dataclass
class MessagePostedEvent:
    room_id: str
    body: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    link_preview: LinkPreview | None = None
    in_reply_to: str | None = None
    thread_root_event_id: str | None = None
    reactions: list[Reaction] = field(default_factory=list)
    updated_at: str | None = None
    reply_count: int = 0
    last_reply_at: str | None = None
    echo_of_event_id: str | None = None
    echo_from_thread_root_event_id: str | None = None
    thread_participants: list[User] = field(default_factory=list)
    viewer_is_following_thread: bool = False


@dataclass
class MessageUpdatedEvent:
    room_id: str
    message_event_id: str


@dataclass
class MessageDeletedEvent:
    room_id: str
    message_event_id: str


@dataclass
class RoomCreatedEvent:
    room_id: str
    name: str | None = None
    description: str | None = None


@dataclass
class UserJoinedRoomEvent:
    room_id: str


@dataclass
class UserLeftRoomEvent:
    room_id: str


@dataclass
class RoomUpdatedEvent:
    room_id: str


@dataclass
class RoomDeletedEvent:
    room_id: str


@dataclass
class RoomArchivedEvent:
    room_id: str


@dataclass
class RoomUnarchivedEvent:
    room_id: str


@dataclass
class ReactionAddedEvent:
    room_id: str
    message_event_id: str
    emoji: str


@dataclass
class ReactionRemovedEvent:
    room_id: str
    message_event_id: str
    emoji: str


@dataclass
class UserTypingEvent:
    room_id: str
    thread_root_event_id: str | None = None


@dataclass
class PresenceChangedEvent:
    status: str


@dataclass
class VideoProcessingCompletedEvent:
    room_id: str
    attachment_id: str
    message_event_id: str


@dataclass
class ServerMemberDeletedEvent:
    user_id: str


@dataclass
class CallParticipantJoinedEvent:
    room_id: str


@dataclass
class CallParticipantLeftEvent:
    room_id: str


# --- Inner event union: instance subscription ---


@dataclass
class ServerConfigUpdatedEvent:
    server_name: str | None = None
    motd: str | None = None
    welcome_message: str | None = None
    blocked_usernames: str | None = None


@dataclass
class UserCreatedEvent:
    user_id: str
    login: str | None = None
    display_name: str | None = None


@dataclass
class UserDeletedEvent:
    user_id: str


@dataclass
class ServerUpdatedEvent:
    name: str | None = None
    description: str | None = None
    logo_url: str | None = None
    banner_url: str | None = None


@dataclass
class UserProfileUpdatedEvent:
    user_id: str
    display_name: str | None = None
    avatar_url: str | None = None
    login: str | None = None


@dataclass
class ServerUserPreferencesUpdatedEvent:
    timezone: str | None = None
    time_format: str | None = None


@dataclass
class NotificationLevelChangedEvent:
    room_id: str = ""
    level: str = ""
    effective_level: str = ""


@dataclass
class MentionNotificationEvent:
    room_id: str = ""
    room: dict | None = None
    actor: User | None = None


@dataclass
class NewDirectMessageNotificationEvent:
    room_id: str = ""
    sender: User | None = None
    conversation_name: str | None = None


@dataclass
class NotificationCreatedEvent:
    notification_id: str
    room_id: str = ""
    event_id: str = ""
    in_reply_to_id: str | None = None


@dataclass
class NotificationDismissedEvent:
    notification_id: str


@dataclass
class RoomMarkedAsReadEvent:
    room_id: str


@dataclass
class ThreadFollowChangedEvent:
    room_id: str = ""
    thread_root_event_id: str = ""
    is_following: bool = False


@dataclass
class RoomGroupsUpdatedEvent:
    changed: bool = False


@dataclass
class MentionStatusClearedEvent:
    room_id: str


@dataclass
class SessionTerminatedEvent:
    reason: str | None = None


@dataclass
class HeartbeatEvent:
    alive: bool = True


# --- Catch-all for unknown server events ---


@dataclass
class UnknownEvent:
    """Placeholder dispatched when the server sends a __typename we don't model.

    Lets the bot keep running through API additions; handlers can match
    ``event_name == "unknown"`` and inspect ``raw`` if they want.
    """

    typename: str
    raw: dict


# --- Type unions / lookup tables ---


RoomInnerEvent = (
    MessagePostedEvent
    | MessageUpdatedEvent
    | MessageDeletedEvent
    | RoomCreatedEvent
    | UserJoinedRoomEvent
    | UserLeftRoomEvent
    | RoomUpdatedEvent
    | RoomDeletedEvent
    | RoomArchivedEvent
    | RoomUnarchivedEvent
    | ReactionAddedEvent
    | ReactionRemovedEvent
    | UserTypingEvent
    | PresenceChangedEvent
    | VideoProcessingCompletedEvent
    | ServerMemberDeletedEvent
    | CallParticipantJoinedEvent
    | CallParticipantLeftEvent
)

InstanceInnerEvent = (
    ServerConfigUpdatedEvent
    | UserCreatedEvent
    | UserDeletedEvent
    | ServerUpdatedEvent
    | UserProfileUpdatedEvent
    | ServerUserPreferencesUpdatedEvent
    | NotificationLevelChangedEvent
    | MentionNotificationEvent
    | NewDirectMessageNotificationEvent
    | NotificationCreatedEvent
    | NotificationDismissedEvent
    | RoomMarkedAsReadEvent
    | ThreadFollowChangedEvent
    | RoomGroupsUpdatedEvent
    | MentionStatusClearedEvent
    | SessionTerminatedEvent
    | HeartbeatEvent
)

EventType = RoomInnerEvent | InstanceInnerEvent | UnknownEvent


_GRAPHQL_TO_EVENT: dict[str, type] = {
    # Room (server-wide)
    "MessagePostedEvent": MessagePostedEvent,
    "MessageUpdatedEvent": MessageUpdatedEvent,
    "MessageDeletedEvent": MessageDeletedEvent,
    "RoomCreatedEvent": RoomCreatedEvent,
    "UserJoinedRoomEvent": UserJoinedRoomEvent,
    "UserLeftRoomEvent": UserLeftRoomEvent,
    "RoomUpdatedEvent": RoomUpdatedEvent,
    "RoomDeletedEvent": RoomDeletedEvent,
    "RoomArchivedEvent": RoomArchivedEvent,
    "RoomUnarchivedEvent": RoomUnarchivedEvent,
    "ReactionAddedEvent": ReactionAddedEvent,
    "ReactionRemovedEvent": ReactionRemovedEvent,
    "UserTypingEvent": UserTypingEvent,
    "PresenceChangedEvent": PresenceChangedEvent,
    "VideoProcessingCompletedEvent": VideoProcessingCompletedEvent,
    "ServerMemberDeletedEvent": ServerMemberDeletedEvent,
    "CallParticipantJoinedEvent": CallParticipantJoinedEvent,
    "CallParticipantLeftEvent": CallParticipantLeftEvent,
    # Instance
    "ServerConfigUpdatedEvent": ServerConfigUpdatedEvent,
    "UserCreatedEvent": UserCreatedEvent,
    "UserDeletedEvent": UserDeletedEvent,
    "ServerUpdatedEvent": ServerUpdatedEvent,
    "UserProfileUpdatedEvent": UserProfileUpdatedEvent,
    "ServerUserPreferencesUpdatedEvent": ServerUserPreferencesUpdatedEvent,
    "NotificationLevelChangedEvent": NotificationLevelChangedEvent,
    "MentionNotificationEvent": MentionNotificationEvent,
    "NewDirectMessageNotificationEvent": NewDirectMessageNotificationEvent,
    "NotificationCreatedEvent": NotificationCreatedEvent,
    "NotificationDismissedEvent": NotificationDismissedEvent,
    "RoomMarkedAsReadEvent": RoomMarkedAsReadEvent,
    "ThreadFollowChangedEvent": ThreadFollowChangedEvent,
    "RoomGroupsUpdatedEvent": RoomGroupsUpdatedEvent,
    "MentionStatusClearedEvent": MentionStatusClearedEvent,
    "SessionTerminatedEvent": SessionTerminatedEvent,
    "HeartbeatEvent": HeartbeatEvent,
}

# snake_case event name -> dataclass (also the public handler-registration key)
EVENT_NAME_TO_TYPE: dict[str, type] = {
    # Room
    "message_posted": MessagePostedEvent,
    "message_updated": MessageUpdatedEvent,
    "message_deleted": MessageDeletedEvent,
    "room_created": RoomCreatedEvent,
    "user_joined_room": UserJoinedRoomEvent,
    "user_left_room": UserLeftRoomEvent,
    "room_updated": RoomUpdatedEvent,
    "room_deleted": RoomDeletedEvent,
    "room_archived": RoomArchivedEvent,
    "room_unarchived": RoomUnarchivedEvent,
    "reaction_added": ReactionAddedEvent,
    "reaction_removed": ReactionRemovedEvent,
    "user_typing": UserTypingEvent,
    "presence_changed": PresenceChangedEvent,
    "video_processing_completed": VideoProcessingCompletedEvent,
    "server_member_deleted": ServerMemberDeletedEvent,
    "call_participant_joined": CallParticipantJoinedEvent,
    "call_participant_left": CallParticipantLeftEvent,
    # Instance
    "server_config_updated": ServerConfigUpdatedEvent,
    "user_created": UserCreatedEvent,
    "user_deleted": UserDeletedEvent,
    "server_updated": ServerUpdatedEvent,
    "user_profile_updated": UserProfileUpdatedEvent,
    "server_user_preferences_updated": ServerUserPreferencesUpdatedEvent,
    "notification_level_changed": NotificationLevelChangedEvent,
    "mention_notification": MentionNotificationEvent,
    "new_direct_message_notification": NewDirectMessageNotificationEvent,
    "notification_created": NotificationCreatedEvent,
    "notification_dismissed": NotificationDismissedEvent,
    "room_marked_as_read": RoomMarkedAsReadEvent,
    "thread_follow_changed": ThreadFollowChangedEvent,
    "room_groups_updated": RoomGroupsUpdatedEvent,
    "mention_status_cleared": MentionStatusClearedEvent,
    "session_terminated": SessionTerminatedEvent,
    "heartbeat": HeartbeatEvent,
    "unknown": UnknownEvent,
}

_TYPE_TO_EVENT_NAME: dict[type, str] = {v: k for k, v in EVENT_NAME_TO_TYPE.items()}


# --- Top-level wrapper (used for both subscriptions) ---


@dataclass
class RoomEvent:
    """Wrapper around an inner event from the global ``myEvents`` stream.

    For room-scoped events, all fields are populated. For server-wide
    notification events, ``id`` and ``created_at`` are typically empty
    and ``actor`` is ``None``.
    """

    actor_id: str
    event: EventType
    id: str = ""
    created_at: str = ""
    actor: User | None = None


# --- Parsing helpers ---


def _camel_to_snake(name: str) -> str:
    import re

    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


def _parse_user(data: dict) -> User:
    return User(
        id=data.get("id", ""),
        login=data.get("login", ""),
        display_name=data.get("displayName", ""),
        avatar_url=data.get("avatarUrl"),
        presence_status=data.get("presenceStatus", "OFFLINE"),
    )


def _parse_video_processing_variant(data: dict) -> VideoProcessingVariant:
    return VideoProcessingVariant(
        url=data.get("url", ""),
        quality=data.get("quality", ""),
        width=data.get("width", 0),
        height=data.get("height", 0),
        size=data.get("size", 0),
    )


def _parse_video_processing(data: dict) -> VideoProcessing:
    return VideoProcessing(
        status=data.get("status", ""),
        duration_ms=data.get("durationMs", 0),
        width=data.get("width", 0),
        height=data.get("height", 0),
        thumbnail_url=data.get("thumbnailUrl"),
        variants=[_parse_video_processing_variant(v) for v in data.get("variants", [])],
        error_message=data.get("errorMessage"),
    )


def _parse_attachment(data: dict) -> Attachment:
    vp = data.get("videoProcessing")
    return Attachment(
        id=data["id"],
        filename=data.get("filename", ""),
        content_type=data.get("contentType", ""),
        room_id=data.get("roomId", ""),
        width=data.get("width", 0),
        height=data.get("height", 0),
        url=data.get("url", ""),
        thumbnail_url=data.get("thumbnailUrl"),
        video_processing=_parse_video_processing(vp) if vp else None,
    )


def _parse_reaction(data: dict) -> Reaction:
    return Reaction(
        emoji=data["emoji"],
        count=data["count"],
        users=[_parse_user(u) for u in data.get("users", [])],
        has_reacted=data.get("hasReacted", False),
    )


def _parse_link_preview(data: dict) -> LinkPreview:
    return LinkPreview(
        url=data.get("url", ""),
        title=data.get("title"),
        description=data.get("description"),
        image_url=data.get("imageUrl"),
        site_name=data.get("siteName"),
        embed_type=data.get("embedType"),
        embed_id=data.get("embedId"),
    )


# GraphQL response keys we receive aliased to dodge field-merge type
# collisions (e.g. NotificationLevelChangedEvent.roomId is nullable while
# every other event's roomId is non-null). Map back to canonical names
# before parsing so the dataclass fields stay clean.
_FIELD_ALIASES: dict[str, str] = {
    "nlcRoomId": "roomId",
    "utThreadRootEventId": "threadRootEventId",
    "mpThreadRootEventId": "threadRootEventId",
}


def _parse_inner_event(data: dict) -> EventType:
    """Parse a wrapper's inner event union from GraphQL JSON."""
    typename = data.get("__typename")
    if not typename:
        raise ValueError("Event data missing __typename")

    cls = _GRAPHQL_TO_EVENT.get(typename)
    if cls is None:
        logger.warning("Unknown event type from server: %s", typename)
        return UnknownEvent(typename=typename, raw=dict(data))

    known: dict[str, dataclasses.Field] = {f.name: f for f in dataclasses.fields(cls)}

    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key == "__typename":
            continue
        canonical = _FIELD_ALIASES.get(key, key)
        snake_key = _camel_to_snake(canonical)
        if snake_key not in known:
            continue  # unknown field, drop quietly so future API fields don't crash

        if snake_key == "attachments" and isinstance(value, list):
            value = [_parse_attachment(a) for a in value]
        elif snake_key == "reactions" and isinstance(value, list):
            value = [_parse_reaction(r) for r in value]
        elif snake_key == "link_preview" and isinstance(value, dict):
            value = _parse_link_preview(value)
        elif snake_key == "thread_participants" and isinstance(value, list):
            value = [_parse_user(u) for u in value]
        elif snake_key in ("actor", "sender") and isinstance(value, dict):
            value = _parse_user(value)

        kwargs[snake_key] = value

    return cls(**kwargs)


def parse_my_event(data: dict) -> RoomEvent:
    """Parse a wrapper from the global subscription (``myEvents``).

    The wrapper carries id, createdAt, actor — same shape regardless of
    whether the inner event is room-scoped or server-wide.
    """
    event_data = data.get("event", {})
    actor_data = data.get("actor")

    return RoomEvent(
        id=data.get("id", ""),
        created_at=data.get("createdAt", ""),
        actor_id=data.get("actorId", ""),
        event=_parse_inner_event(event_data),
        actor=_parse_user(actor_data) if actor_data else None,
    )


parse_room_event = parse_my_event


def event_name(event: EventType) -> str:
    """Get the snake_case event name for an event instance."""
    return _TYPE_TO_EVENT_NAME.get(type(event), "unknown")
