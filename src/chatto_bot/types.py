"""Dataclasses mirroring the Chatto GraphQL schema."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class User:
    id: str
    login: str
    display_name: str
    avatar_url: str | None = None
    presence_status: str = "OFFLINE"


@dataclass
class Attachment:
    id: str
    filename: str
    content_type: str
    size: int
    width: int = 0
    height: int = 0
    url: str = ""


@dataclass
class Reaction:
    emoji: str
    count: int
    users: list[User] = field(default_factory=list)
    has_reacted: bool = False


# --- Event types (inner union) ---


@dataclass
class MessagePostedEvent:
    space_id: str
    room_id: str
    message_body_id: str
    body: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    in_reply_to: str | None = None
    in_thread: str | None = None
    reactions: list[Reaction] = field(default_factory=list)
    updated_at: str | None = None
    reply_count: int = 0
    last_reply_at: str | None = None


@dataclass
class MessageUpdatedEvent:
    space_id: str
    room_id: str
    message_body_id: str
    body: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    reactions: list[Reaction] = field(default_factory=list)


@dataclass
class MessageDeletedEvent:
    space_id: str
    room_id: str
    message_body_id: str


@dataclass
class UserJoinedRoomEvent:
    space_id: str
    room_id: str


@dataclass
class UserLeftRoomEvent:
    space_id: str
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


EventType = (
    MessagePostedEvent
    | MessageUpdatedEvent
    | MessageDeletedEvent
    | UserJoinedRoomEvent
    | UserLeftRoomEvent
    | ReactionAddedEvent
    | ReactionRemovedEvent
    | UserTypingEvent
    | PresenceChangedEvent
)


@dataclass
class SpaceEvent:
    id: str
    created_at: str
    actor_id: str
    sequence_id: str
    event: EventType
    actor: User | None = None


# Map GraphQL __typename to dataclass + field name mapping
_GRAPHQL_TO_EVENT: dict[str, type] = {
    "MessagePostedEvent": MessagePostedEvent,
    "MessageUpdatedEvent": MessageUpdatedEvent,
    "MessageDeletedEvent": MessageDeletedEvent,
    "UserJoinedRoomEvent": UserJoinedRoomEvent,
    "UserLeftRoomEvent": UserLeftRoomEvent,
    "ReactionAddedEvent": ReactionAddedEvent,
    "ReactionRemovedEvent": ReactionRemovedEvent,
    "UserTypingEvent": UserTypingEvent,
    "PresenceChangedEvent": PresenceChangedEvent,
}

# snake_case event name -> GraphQL typename
EVENT_NAME_TO_TYPE: dict[str, type] = {
    "message_posted": MessagePostedEvent,
    "message_updated": MessageUpdatedEvent,
    "message_deleted": MessageDeletedEvent,
    "user_joined_room": UserJoinedRoomEvent,
    "user_left_room": UserLeftRoomEvent,
    "reaction_added": ReactionAddedEvent,
    "reaction_removed": ReactionRemovedEvent,
    "user_typing": UserTypingEvent,
    "presence_changed": PresenceChangedEvent,
}

# Reverse: type -> snake_case name
_TYPE_TO_EVENT_NAME: dict[type, str] = {v: k for k, v in EVENT_NAME_TO_TYPE.items()}


def _camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    import re

    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


def _parse_user(data: dict) -> User:
    return User(
        id=data["id"],
        login=data["login"],
        display_name=data["displayName"],
        avatar_url=data.get("avatarUrl"),
        presence_status=data.get("presenceStatus", "OFFLINE"),
    )


def _parse_attachment(data: dict) -> Attachment:
    return Attachment(
        id=data["id"],
        filename=data["filename"],
        content_type=data["contentType"],
        size=data["size"],
        width=data.get("width", 0),
        height=data.get("height", 0),
        url=data.get("url", ""),
    )


def _parse_reaction(data: dict) -> Reaction:
    return Reaction(
        emoji=data["emoji"],
        count=data["count"],
        users=[_parse_user(u) for u in data.get("users", [])],
        has_reacted=data.get("hasReacted", False),
    )


def _parse_inner_event(data: dict) -> EventType:
    """Parse a SpaceEvent's inner event union from GraphQL JSON."""
    typename = data.get("__typename")
    if not typename:
        raise ValueError("Event data missing __typename")

    cls = _GRAPHQL_TO_EVENT.get(typename)
    if not cls:
        raise ValueError(f"Unknown event type: {typename}")

    # Build kwargs by converting camelCase keys to snake_case
    kwargs: dict = {}
    for key, value in data.items():
        if key == "__typename":
            continue
        snake_key = _camel_to_snake(key)

        # Handle nested types
        if snake_key == "attachments" and isinstance(value, list):
            value = [_parse_attachment(a) for a in value]
        elif snake_key == "reactions" and isinstance(value, list):
            value = [_parse_reaction(r) for r in value]

        kwargs[snake_key] = value

    return cls(**kwargs)


def parse_space_event(data: dict) -> SpaceEvent:
    """Parse a full SpaceEvent from GraphQL subscription JSON."""
    event_data = data.get("event", {})
    actor_data = data.get("actor")

    return SpaceEvent(
        id=data["id"],
        created_at=data["createdAt"],
        actor_id=data["actorId"],
        sequence_id=data["sequenceId"],
        event=_parse_inner_event(event_data),
        actor=_parse_user(actor_data) if actor_data else None,
    )


def event_name(event: EventType) -> str:
    """Get the snake_case event name for an event instance."""
    return _TYPE_TO_EVENT_NAME.get(type(event), "unknown")
