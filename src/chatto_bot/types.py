"""Dataclasses mirroring the Chatto GraphQL schema.

Covers both the per-space subscription (`mySpaceEvents`) and the
instance-wide subscription (`myInstanceEvents`). All inner-event types the
server emits today are represented; unknown typenames parse to an
``UnknownEvent`` placeholder so the bot keeps running through API additions.
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
    space_id: str = ""
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


# --- Inner event union: per-space subscription ---


@dataclass
class MessagePostedEvent:
    room_id: str
    body: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    link_preview: LinkPreview | None = None
    in_reply_to: str | None = None
    in_thread: str | None = None
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
class UserJoinedRoomEvent:
    space_id: str
    room_id: str


@dataclass
class UserLeftRoomEvent:
    space_id: str
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
    space_id: str
    room_id: str
    message_event_id: str
    emoji: str


@dataclass
class ReactionRemovedEvent:
    space_id: str
    room_id: str
    message_event_id: str
    emoji: str


@dataclass
class UserTypingEvent:
    space_id: str
    room_id: str
    thread_root_event_id: str | None = None


@dataclass
class PresenceChangedEvent:
    status: str


@dataclass
class VideoProcessingCompletedEvent:
    space_id: str
    room_id: str
    attachment_id: str
    message_event_id: str


@dataclass
class SpaceMemberDeletedEvent:
    space_id: str
    user_id: str


@dataclass
class CallParticipantJoinedEvent:
    space_id: str
    room_id: str


@dataclass
class CallParticipantLeftEvent:
    space_id: str
    room_id: str


# --- Inner event union: instance subscription ---


@dataclass
class InstanceConfigUpdatedEvent:
    instance_name: str | None = None
    motd: str | None = None
    welcome_message: str | None = None


@dataclass
class SpaceCreatedEvent:
    space_id: str


@dataclass
class SpaceUpdatedEvent:
    space_id: str
    name: str | None = None
    description: str | None = None
    logo_url: str | None = None
    banner_url: str | None = None


@dataclass
class SpaceDeletedEvent:
    space_id: str


@dataclass
class UserJoinedSpaceEvent:
    space_id: str


@dataclass
class UserLeftSpaceEvent:
    space_id: str


@dataclass
class UserProfileUpdatedEvent:
    user_id: str
    display_name: str | None = None
    avatar_url: str | None = None
    login: str | None = None


@dataclass
class InstanceUserPreferencesUpdatedEvent:
    timezone: str | None = None
    time_format: str | None = None


@dataclass
class NotificationLevelChangedEvent:
    space_id: str = ""
    room_id: str = ""
    level: str = ""
    effective_level: str = ""


@dataclass
class MentionNotificationEvent:
    space_id: str = ""
    room_id: str = ""
    space: dict | None = None  # {"name": ...}
    room: dict | None = None
    actor: User | None = None  # the user who mentioned (id, displayName)


@dataclass
class NewDirectMessageNotificationEvent:
    room_id: str = ""
    sender: User | None = None  # id, displayName, avatarUrl
    conversation_name: str | None = None


@dataclass
class NotificationCreatedEvent:
    notification_id: str
    space_id: str = ""
    room_id: str = ""
    event_id: str = ""
    in_reply_to_id: str | None = None


@dataclass
class NotificationDismissedEvent:
    notification_id: str


@dataclass
class NewMessageInSpaceEvent:
    space_id: str
    room_id: str


@dataclass
class RoomMarkedAsReadEvent:
    space_id: str
    room_id: str


@dataclass
class ThreadFollowChangedEvent:
    space_id: str = ""
    room_id: str = ""
    thread_root_event_id: str = ""
    is_following: bool = False


@dataclass
class RoomLayoutUpdatedEvent:
    space_id: str


@dataclass
class SessionTerminatedEvent:
    reason: str | None = None


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


SpaceInnerEvent = (
    MessagePostedEvent
    | MessageUpdatedEvent
    | MessageDeletedEvent
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
    | SpaceMemberDeletedEvent
    | CallParticipantJoinedEvent
    | CallParticipantLeftEvent
)

InstanceInnerEvent = (
    InstanceConfigUpdatedEvent
    | SpaceCreatedEvent
    | SpaceUpdatedEvent
    | SpaceDeletedEvent
    | UserJoinedSpaceEvent
    | UserLeftSpaceEvent
    | UserProfileUpdatedEvent
    | InstanceUserPreferencesUpdatedEvent
    | NotificationLevelChangedEvent
    | MentionNotificationEvent
    | NewDirectMessageNotificationEvent
    | NotificationCreatedEvent
    | NotificationDismissedEvent
    | NewMessageInSpaceEvent
    | RoomMarkedAsReadEvent
    | ThreadFollowChangedEvent
    | RoomLayoutUpdatedEvent
    | SessionTerminatedEvent
)

EventType = SpaceInnerEvent | InstanceInnerEvent | UnknownEvent


# Map GraphQL __typename -> dataclass
_GRAPHQL_TO_EVENT: dict[str, type] = {
    # Space
    "MessagePostedEvent": MessagePostedEvent,
    "MessageUpdatedEvent": MessageUpdatedEvent,
    "MessageDeletedEvent": MessageDeletedEvent,
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
    "SpaceMemberDeletedEvent": SpaceMemberDeletedEvent,
    "CallParticipantJoinedEvent": CallParticipantJoinedEvent,
    "CallParticipantLeftEvent": CallParticipantLeftEvent,
    # Instance
    "InstanceConfigUpdatedEvent": InstanceConfigUpdatedEvent,
    "SpaceCreatedEvent": SpaceCreatedEvent,
    "SpaceUpdatedEvent": SpaceUpdatedEvent,
    "SpaceDeletedEvent": SpaceDeletedEvent,
    "UserJoinedSpaceEvent": UserJoinedSpaceEvent,
    "UserLeftSpaceEvent": UserLeftSpaceEvent,
    "UserProfileUpdatedEvent": UserProfileUpdatedEvent,
    "InstanceUserPreferencesUpdatedEvent": InstanceUserPreferencesUpdatedEvent,
    "NotificationLevelChangedEvent": NotificationLevelChangedEvent,
    "MentionNotificationEvent": MentionNotificationEvent,
    "NewDirectMessageNotificationEvent": NewDirectMessageNotificationEvent,
    "NotificationCreatedEvent": NotificationCreatedEvent,
    "NotificationDismissedEvent": NotificationDismissedEvent,
    "NewMessageInSpaceEvent": NewMessageInSpaceEvent,
    "RoomMarkedAsReadEvent": RoomMarkedAsReadEvent,
    "ThreadFollowChangedEvent": ThreadFollowChangedEvent,
    "RoomLayoutUpdatedEvent": RoomLayoutUpdatedEvent,
    "SessionTerminatedEvent": SessionTerminatedEvent,
}

# snake_case event name -> dataclass (also the public handler-registration key)
EVENT_NAME_TO_TYPE: dict[str, type] = {
    # Space
    "message_posted": MessagePostedEvent,
    "message_updated": MessageUpdatedEvent,
    "message_deleted": MessageDeletedEvent,
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
    "space_member_deleted": SpaceMemberDeletedEvent,
    "call_participant_joined": CallParticipantJoinedEvent,
    "call_participant_left": CallParticipantLeftEvent,
    # Instance
    "instance_config_updated": InstanceConfigUpdatedEvent,
    "space_created": SpaceCreatedEvent,
    "space_updated": SpaceUpdatedEvent,
    "space_deleted": SpaceDeletedEvent,
    "user_joined_space": UserJoinedSpaceEvent,
    "user_left_space": UserLeftSpaceEvent,
    "user_profile_updated": UserProfileUpdatedEvent,
    "instance_user_preferences_updated": InstanceUserPreferencesUpdatedEvent,
    "notification_level_changed": NotificationLevelChangedEvent,
    "mention_notification": MentionNotificationEvent,
    "new_direct_message_notification": NewDirectMessageNotificationEvent,
    "notification_created": NotificationCreatedEvent,
    "notification_dismissed": NotificationDismissedEvent,
    "new_message_in_space": NewMessageInSpaceEvent,
    "room_marked_as_read": RoomMarkedAsReadEvent,
    "thread_follow_changed": ThreadFollowChangedEvent,
    "room_layout_updated": RoomLayoutUpdatedEvent,
    "session_terminated": SessionTerminatedEvent,
    # Catch-all
    "unknown": UnknownEvent,
}

_TYPE_TO_EVENT_NAME: dict[type, str] = {v: k for k, v in EVENT_NAME_TO_TYPE.items()}


# --- Top-level wrapper (used for both subscriptions) ---


@dataclass
class SpaceEvent:
    """Wrapper around an inner event, common to space and instance subscriptions.

    For per-space events, all fields are populated. For instance events,
    ``id``, ``created_at``, ``space_id`` and ``actor`` are typically empty.
    """

    actor_id: str
    event: EventType
    id: str = ""
    created_at: str = ""
    space_id: str = ""
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
        space_id=data.get("spaceId", ""),
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


# GraphQL response keys we receive aliased (the web client aliases these to
# work around field-merge type conflicts in the union). Map back to canonical
# names before parsing so the dataclass fields stay clean.
_FIELD_ALIASES: dict[str, str] = {
    "nlcSpaceId": "spaceId",
    "nlcRoomId": "roomId",
    "tfcSpaceId": "spaceId",
    "tfcRoomId": "roomId",
    "rluSpaceId": "spaceId",
}


def _parse_inner_event(data: dict) -> EventType:
    """Parse a SpaceEvent's inner event union from GraphQL JSON."""
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


def parse_space_event(data: dict, space_id: str = "") -> SpaceEvent:
    """Parse a wrapper from the per-space subscription (``mySpaceEvents``).

    ``space_id`` is supplied by the caller since the API no longer carries it
    on the wrapper or on most inner event types.
    """
    event_data = data.get("event", {})
    actor_data = data.get("actor")

    return SpaceEvent(
        id=data.get("id", ""),
        created_at=data.get("createdAt", ""),
        actor_id=data.get("actorId", ""),
        space_id=space_id,
        event=_parse_inner_event(event_data),
        actor=_parse_user(actor_data) if actor_data else None,
    )


def parse_instance_event(data: dict) -> SpaceEvent:
    """Parse a wrapper from the instance subscription (``myInstanceEvents``).

    Instance events have no ``id``, ``createdAt`` or wrapper actor; the
    inner event carries any relevant context.
    """
    event_data = data.get("event", {})
    return SpaceEvent(
        id="",
        created_at="",
        actor_id=data.get("actorId", ""),
        space_id="",
        event=_parse_inner_event(event_data),
        actor=None,
    )


def event_name(event: EventType) -> str:
    """Get the snake_case event name for an event instance."""
    return _TYPE_TO_EVENT_NAME.get(type(event), "unknown")
