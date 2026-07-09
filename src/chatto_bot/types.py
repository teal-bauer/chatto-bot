"""Dataclasses handlers receive, and the realtime event-name compatibility table.

Chatto v0.4.0 removed GraphQL and replaced the old ``myEvents`` subscription
with a binary-protobuf WebSocket (``chatto.realtime.v1``, see
``realtime.proto``). Realtime events are invalidation signals first: most
carry only IDs and small inline hints, not full renderable objects.
``hydrate.py`` turns a signal into full objects (a fetched ``Message`` proto,
a resolved actor ``User`` proto); this module turns *that* into the public
dataclasses below, which is the shape bot authors have always seen.

Unknown/unmodeled oneof cases parse to an ``UnknownEvent`` placeholder so the
bot keeps running through API additions.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    # Only used for type hints; parse_envelope/converters access chattolib
    # objects (dataclasses for hydrated resources, google-protobuf messages
    # for realtime payloads) duck-typed by attribute name, so types.py has no
    # hard runtime dependency on either shape.
    from chattolib.types import Message as _ChattolibMessage
    from chattolib.types import TimelineEvent
    from chattolib.types import User as _ChattolibUser
    from chattolib._pb.chatto.realtime.v1.realtime_pb2 import RealtimeEventEnvelope
    from google.protobuf.timestamp_pb2 import Timestamp

logger = logging.getLogger(__name__)


# --- Leaf types ---


@dataclass
class CustomUserStatus:
    emoji: str = ""
    text: str = ""
    expires_at: str | None = None


@dataclass
class User:
    id: str
    login: str = ""
    display_name: str = ""
    avatar_url: str | None = None
    presence_status: str = "OFFLINE"
    custom_status: CustomUserStatus | None = None


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
    source_available: bool = False
    reason_code: str = ""
    thumbnail_url: str | None = None
    variants: list[VideoProcessingVariant] = field(default_factory=list)


@dataclass
class Attachment:
    id: str
    filename: str = ""
    content_type: str = ""
    width: int = 0
    height: int = 0
    url: str = ""
    thumbnail_url: str | None = None
    video_processing: VideoProcessing | None = None


@dataclass
class Reaction:
    emoji: str
    count: int
    has_reacted: bool = False
    # Up to 5 user IDs, per MessageReaction.preview_user_ids. Full User
    # objects aren't hydrated for these (out of scope for the first cut of
    # hydrate.py); resolve through UserCache/ctx if a handler needs them.
    preview_user_ids: list[str] = field(default_factory=list)


@dataclass
class LinkPreview:
    url: str = ""
    title: str | None = None
    description: str | None = None
    image_url: str | None = None
    image_asset_id: str | None = None
    site_name: str | None = None
    embed_type: str | None = None
    embed_id: str | None = None


@dataclass
class ThreadViewerState:
    is_following: bool | None = None
    has_unread: bool | None = None


@dataclass
class ThreadSummary:
    thread_root_event_id: str = ""
    reply_count: int = 0
    last_reply_at: str | None = None
    participant_preview_user_ids: list[str] = field(default_factory=list)
    participant_count: int = 0
    viewer_state: ThreadViewerState | None = None


# --- Inner event union: one dataclass per realtime.proto oneof case ---


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
    echo_of_event_id: str | None = None
    echo_from_thread_root_event_id: str | None = None
    channel_echo_event_id: str | None = None
    thread: ThreadSummary | None = None


@dataclass
class MessageUpdatedEvent:
    """From ``message_edited``.

    ``hydrate.py`` already fetches the current ``Message`` for this event (to
    honor the retracted-between-signal-and-fetch drop contract), so ``body``
    is populated from that fetch when available -- no need to always call
    ``ctx.fetch_message()`` just to read the edited text. It's still ``None``
    if hydration didn't run (e.g. direct ``parse_envelope()`` use without a
    message passed in); ``ctx.fetch_message()`` remains the way to get
    attachments, reactions, etc.
    """

    room_id: str
    message_event_id: str
    body: str | None = None


@dataclass
class MessageDeletedEvent:
    """From ``message_retracted``."""

    room_id: str
    message_event_id: str
    reason: str | None = None


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
    user_id: str
    status: str


@dataclass
class RoomCreatedEvent:
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
class UserJoinedRoomEvent:
    room_id: str


@dataclass
class UserLeftRoomEvent:
    room_id: str


@dataclass
class RoomUniversalChangedEvent:
    room_id: str
    universal: bool = False


@dataclass
class NotificationCreatedEvent:
    notification_id: str
    room_id: str = ""
    event_id: str = ""
    in_reply_to_id: str | None = None
    silent: bool = False


@dataclass
class NotificationDismissedEvent:
    notification_id: str


@dataclass
class NotificationLevelChangedEvent:
    room_id: str = ""
    level: str = ""
    effective_level: str = ""


@dataclass
class ThreadFollowChangedEvent:
    room_id: str = ""
    thread_root_event_id: str = ""
    following: bool = False


@dataclass
class RoomMarkedAsReadEvent:
    room_id: str


@dataclass
class ThreadCreatedEvent:
    room_id: str
    thread_root_event_id: str = ""


@dataclass
class ServerUpdatedEvent:
    name: str | None = None
    description: str | None = None
    logo_url: str | None = None
    banner_url: str | None = None


@dataclass
class UserProfileUpdatedEvent:
    user_id: str
    login: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None


@dataclass
class UserCustomStatusSetEvent:
    user_id: str
    emoji: str = ""
    text: str = ""
    expires_at: str | None = None


@dataclass
class UserCustomStatusClearedEvent:
    user_id: str


@dataclass
class ServerUserPreferencesUpdatedEvent:
    timezone: str | None = None
    time_format: str = ""


@dataclass
class RoomGroupsUpdatedEvent:
    changed: bool = True


@dataclass
class ServerMemberDeletedEvent:
    user_id: str


@dataclass
class AssetProcessingStartedEvent:
    asset_id: str
    room_id: str | None = None
    message_event_id: str | None = None


@dataclass
class VideoProcessingCompletedEvent:
    """From ``asset_processing_succeeded``."""

    asset_id: str
    room_id: str | None = None
    message_event_id: str | None = None


@dataclass
class AssetProcessingFailedEvent:
    asset_id: str
    room_id: str | None = None
    message_event_id: str | None = None


@dataclass
class AssetDeletedEvent:
    asset_id: str
    room_id: str | None = None


@dataclass
class CallStartedEvent:
    room_id: str
    call_id: str = ""
    source: str = ""


@dataclass
class CallParticipantJoinedEvent:
    room_id: str
    call_id: str = ""
    source: str = ""


@dataclass
class CallParticipantLeftEvent:
    room_id: str
    call_id: str = ""
    source: str = ""


@dataclass
class CallEndedEvent:
    room_id: str
    call_id: str = ""
    source: str = ""


@dataclass
class MentionNotificationEvent:
    room_id: str = ""
    actor_user_id: str = ""
    room_name: str | None = None
    actor_display_name: str | None = None


@dataclass
class NewDirectMessageNotificationEvent:
    room_id: str = ""
    sender_id: str = ""
    sender_display_name: str | None = None
    sender_avatar_url: str | None = None
    conversation_name: str | None = None


@dataclass
class SessionTerminatedEvent:
    reason: str | None = None


# --- Retired events: kept as documentation + registration targets for
# warn_if_retired_event_name(); realtime.v1 has no signal for these, so a
# handler registered under one of these names will never fire. ---


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
class MentionStatusClearedEvent:
    room_id: str


@dataclass
class HeartbeatEvent:
    """Retired as an envelope event -- heartbeat is now a WS frame."""

    alive: bool = True


# --- Catch-all for unmodeled/retired events ---


@dataclass
class UnknownEvent:
    """Placeholder dispatched when the server sends a oneof case we don't model.

    Lets the bot keep running through API additions; handlers can match
    ``event_name == "unknown"`` and inspect ``raw`` if they want.
    """

    typename: str
    raw: dict = field(default_factory=dict)


EventType = (
    MessagePostedEvent
    | MessageUpdatedEvent
    | MessageDeletedEvent
    | ReactionAddedEvent
    | ReactionRemovedEvent
    | UserTypingEvent
    | PresenceChangedEvent
    | RoomCreatedEvent
    | RoomUpdatedEvent
    | RoomDeletedEvent
    | RoomArchivedEvent
    | RoomUnarchivedEvent
    | UserJoinedRoomEvent
    | UserLeftRoomEvent
    | RoomUniversalChangedEvent
    | NotificationCreatedEvent
    | NotificationDismissedEvent
    | NotificationLevelChangedEvent
    | ThreadFollowChangedEvent
    | RoomMarkedAsReadEvent
    | ThreadCreatedEvent
    | ServerUpdatedEvent
    | UserProfileUpdatedEvent
    | UserCustomStatusSetEvent
    | UserCustomStatusClearedEvent
    | ServerUserPreferencesUpdatedEvent
    | RoomGroupsUpdatedEvent
    | ServerMemberDeletedEvent
    | AssetProcessingStartedEvent
    | VideoProcessingCompletedEvent
    | AssetProcessingFailedEvent
    | AssetDeletedEvent
    | CallStartedEvent
    | CallParticipantJoinedEvent
    | CallParticipantLeftEvent
    | CallEndedEvent
    | MentionNotificationEvent
    | NewDirectMessageNotificationEvent
    | SessionTerminatedEvent
    | ServerConfigUpdatedEvent
    | UserCreatedEvent
    | UserDeletedEvent
    | MentionStatusClearedEvent
    | HeartbeatEvent
    | UnknownEvent
)


# --- Event-name compatibility table ---
#
# realtime.proto oneof case name -> public snake_case handler name
# (the name bot authors pass to @on_event(...)). Almost every case matches
# its oneof field name 1:1; only these three were renamed to keep the old
# GraphQL-era handler names bots already use:
_ONEOF_RENAMES: dict[str, str] = {
    "message_edited": "message_updated",
    "message_retracted": "message_deleted",
    "asset_processing_succeeded": "video_processing_completed",
}

# snake_case handler name -> dataclass. Covers every live realtime.v1 oneof
# case (after renames) plus the retired GraphQL-era names that no longer have
# a realtime source. Useful for documentation/lookups; event_name() below
# does not consult this table.
EVENT_NAME_TO_TYPE: dict[str, type] = {
    "message_posted": MessagePostedEvent,
    "message_updated": MessageUpdatedEvent,
    "message_deleted": MessageDeletedEvent,
    "reaction_added": ReactionAddedEvent,
    "reaction_removed": ReactionRemovedEvent,
    "user_typing": UserTypingEvent,
    "presence_changed": PresenceChangedEvent,
    "room_created": RoomCreatedEvent,
    "room_updated": RoomUpdatedEvent,
    "room_deleted": RoomDeletedEvent,
    "room_archived": RoomArchivedEvent,
    "room_unarchived": RoomUnarchivedEvent,
    "user_joined_room": UserJoinedRoomEvent,
    "user_left_room": UserLeftRoomEvent,
    "room_universal_changed": RoomUniversalChangedEvent,
    "notification_created": NotificationCreatedEvent,
    "notification_dismissed": NotificationDismissedEvent,
    "notification_level_changed": NotificationLevelChangedEvent,
    "thread_follow_changed": ThreadFollowChangedEvent,
    "room_marked_as_read": RoomMarkedAsReadEvent,
    "thread_created": ThreadCreatedEvent,
    "server_updated": ServerUpdatedEvent,
    "user_profile_updated": UserProfileUpdatedEvent,
    "user_custom_status_set": UserCustomStatusSetEvent,
    "user_custom_status_cleared": UserCustomStatusClearedEvent,
    "server_user_preferences_updated": ServerUserPreferencesUpdatedEvent,
    "room_groups_updated": RoomGroupsUpdatedEvent,
    "server_member_deleted": ServerMemberDeletedEvent,
    "asset_processing_started": AssetProcessingStartedEvent,
    "video_processing_completed": VideoProcessingCompletedEvent,
    "asset_processing_failed": AssetProcessingFailedEvent,
    "asset_deleted": AssetDeletedEvent,
    "call_started": CallStartedEvent,
    "call_participant_joined": CallParticipantJoinedEvent,
    "call_participant_left": CallParticipantLeftEvent,
    "call_ended": CallEndedEvent,
    "mention_notification": MentionNotificationEvent,
    "new_direct_message_notification": NewDirectMessageNotificationEvent,
    "session_terminated": SessionTerminatedEvent,
    # Retired: no realtime.v1 source, never dispatched. Kept so
    # warn_if_retired_event_name() and documentation have something to point
    # at.
    "user_created": UserCreatedEvent,
    "user_deleted": UserDeletedEvent,
    "server_config_updated": ServerConfigUpdatedEvent,
    "mention_status_cleared": MentionStatusClearedEvent,
    "heartbeat": HeartbeatEvent,
    "unknown": UnknownEvent,
}

# Old GraphQL-era handler names with no realtime.v1 equivalent. A bot that
# registers one of these will never see it fire.
RETIRED_EVENT_NAMES: frozenset[str] = frozenset(
    {
        "user_created",
        "user_deleted",
        "server_config_updated",
        "mention_status_cleared",
        "heartbeat",
    }
)

_warned_retired_names: set[str] = set()


def warn_if_retired_event_name(name: str) -> None:
    """Log a one-time warning if ``name`` is a retired (dead) handler name.

    Intended to be called from the bot's event-handler registration path
    (``on_event`` / ``Cog`` handler collection) so bot authors get a clear
    signal instead of a handler that silently never fires.
    """
    if name in RETIRED_EVENT_NAMES and name not in _warned_retired_names:
        _warned_retired_names.add(name)
        logger.warning(
            "Registered a handler for %r, which is retired: realtime.v1 has "
            "no equivalent signal (GraphQL removed in v0.4.0/ADR-042), so "
            "this handler will never fire.",
            name,
        )


# --- Top-level wrapper ---


@dataclass
class RoomEvent:
    """Wrapper around one realtime event, adapted to the public dataclasses.

    For most event types ``actor`` is only populated when the caller passes
    a resolved actor through to ``parse_envelope`` (hydrate.py resolves
    ``envelope.actor_id`` through UserCache first).
    """

    actor_id: str
    event: EventType
    id: str = ""
    created_at: str = ""
    actor: User | None = None


# --- Proto -> dataclass converters ---
#
# These take either chattolib dataclasses (hydrated resources: Message,
# User, ThreadSummary, ...) or google-protobuf messages (realtime payloads,
# still accessed duck-typed by attribute) and return the public dataclasses
# above. Both shapes expose the same snake_case attribute names, which is
# what keeps most of this module oblivious to which one it was handed.


def format_cursor(ts: datetime.datetime | Timestamp | None) -> str:
    """Format a timestamp as a fixed-width UTC ISO string.

    Accepts either a plain ``datetime`` (chattolib dataclasses parse
    timestamps eagerly) or a google-protobuf ``Timestamp`` (realtime payloads
    are still raw protobuf). Always emits 6 fractional digits so lexical
    string comparison stays monotonic. The old GraphQL cursor format didn't
    guarantee a consistent fractional-second width, so comparing a
    fixed-width cursor against an old-format one is only best-effort across
    the upgrade boundary -- see the migration note in the design doc.
    """
    if ts is None:
        return ""
    dt = ts if isinstance(ts, datetime.datetime) else ts.ToDatetime()
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


_PRESENCE_STATUS_PREFIX = "PRESENCE_STATUS_"


def normalize_presence_status(raw: Any) -> str:
    """Map a presence status value to the handler-facing short string.

    ``raw`` may be chattolib's ``PresenceStatus`` ``StrEnum`` (whose
    ``str()`` is the full wire form, e.g. ``"PRESENCE_STATUS_ONLINE"``) or a
    plain enum-name string pulled off a realtime protobuf field via
    ``_enum_name`` (same full form). Either way this strips the
    ``PRESENCE_STATUS_`` prefix and normalizes the unspecified/missing case
    to ``"OFFLINE"`` -- the dataclass default and the value the GraphQL-era
    API used to send, so bots comparing ``== "OFFLINE"`` keep working.
    """
    text = str(raw) if raw is not None else ""
    text = text.removeprefix(_PRESENCE_STATUS_PREFIX)
    return text if text and text != "UNSPECIFIED" else "OFFLINE"


def _enum_name(message: Any, field_name: str, prefix: str = "") -> str:
    """Get the symbolic name of a protobuf enum field's current value.

    Plain google-protobuf enum fields read back as plain ``int``s (unlike
    the old protobuf-py runtime, whose enum accessors already stringified to
    a short name) -- ``str(message.field)`` would silently give back digits
    like ``"1"``. Look the name up via the field's enum descriptor instead,
    and strip ``prefix`` (the proto enum's constant prefix, e.g.
    ``"NOTIFICATION_LEVEL_"``) to recover the short form bots have always
    seen.
    """
    descriptor = message.DESCRIPTOR.fields_by_name[field_name]
    name = descriptor.enum_type.values_by_number[getattr(message, field_name)].name
    return name.removeprefix(prefix) if prefix else name


def _user_from_proto(u: _ChattolibUser) -> User:
    custom_status = None
    if u.custom_status is not None:
        custom_status = CustomUserStatus(
            emoji=u.custom_status.emoji,
            text=u.custom_status.text,
            expires_at=format_cursor(u.custom_status.expires_at)
            if u.custom_status.expires_at
            else None,
        )
    return User(
        id=u.id,
        login=u.login,
        display_name=u.display_name,
        avatar_url=u.avatar_url,
        presence_status=normalize_presence_status(u.presence_status),
        custom_status=custom_status,
    )


def _video_variant_from_proto(v: Any) -> VideoProcessingVariant:
    return VideoProcessingVariant(
        url=v.asset_url.url if v.asset_url is not None else "",
        quality=v.quality,
        width=v.width,
        height=v.height,
        size=v.size,
    )


def _video_processing_from_proto(vp: Any) -> VideoProcessing:
    return VideoProcessing(
        status=str(vp.status),
        duration_ms=vp.duration_ms,
        width=vp.width,
        height=vp.height,
        source_available=vp.source_available,
        reason_code=vp.reason_code,
        thumbnail_url=vp.thumbnail_asset_url.url if vp.thumbnail_asset_url is not None else None,
        variants=[_video_variant_from_proto(v) for v in vp.variants],
    )


def _attachment_from_proto(a: Any) -> Attachment:
    return Attachment(
        id=a.id,
        filename=a.filename,
        content_type=a.content_type,
        width=a.width,
        height=a.height,
        url=a.asset_url.url if a.asset_url is not None else "",
        thumbnail_url=a.thumbnail_asset_url.url if a.thumbnail_asset_url is not None else None,
        video_processing=_video_processing_from_proto(a.video_processing)
        if a.video_processing is not None
        else None,
    )


def _reaction_from_proto(r: Any) -> Reaction:
    return Reaction(
        emoji=r.emoji,
        count=r.count,
        has_reacted=r.has_reacted,
        preview_user_ids=list(r.preview_user_ids),
    )


def _link_preview_from_proto(lp: Any) -> LinkPreview:
    return LinkPreview(
        url=lp.url,
        title=lp.title,
        description=lp.description,
        image_url=lp.image_url,
        image_asset_id=lp.image_asset_id,
        site_name=lp.site_name,
        embed_type=lp.embed_type,
        embed_id=lp.embed_id,
    )


def _thread_summary_from_proto(t: Any) -> ThreadSummary:
    """``t`` is chattolib's ``ThreadSummary`` dataclass, which flattens
    ``is_following``/``has_unread`` directly onto the summary -- unlike the
    old protobuf shape, there's no nested ``viewer_state`` message to unwrap.
    """
    return ThreadSummary(
        thread_root_event_id=t.thread_root_event_id,
        reply_count=t.reply_count,
        last_reply_at=format_cursor(t.last_reply_at) if t.last_reply_at else None,
        participant_preview_user_ids=list(t.participant_preview_user_ids),
        participant_count=t.participant_count,
        viewer_state=ThreadViewerState(is_following=t.is_following, has_unread=t.has_unread),
    )


def _message_posted_from_proto(signal: Any, message: _ChattolibMessage | None) -> MessagePostedEvent:
    if message is None:
        # Hydration didn't run (or found nothing) -- fall back to the bare
        # invalidation signal so a handler at least gets room_id. Normal
        # dispatch always hydrates message_posted (see hydrate.py), so this
        # path is mainly a defensive fallback for direct parse_envelope() use.
        return MessagePostedEvent(room_id=signal.room_id)
    return MessagePostedEvent(
        room_id=message.room_id,
        body=message.body,
        attachments=[_attachment_from_proto(a) for a in message.attachments],
        link_preview=_link_preview_from_proto(message.link_preview)
        if message.link_preview is not None
        else None,
        in_reply_to=message.in_reply_to or None,
        thread_root_event_id=message.thread_root_event_id or None,
        reactions=[_reaction_from_proto(r) for r in message.reactions],
        updated_at=format_cursor(message.updated_at) if message.updated_at else None,
        echo_of_event_id=message.echo_of_event_id or None,
        echo_from_thread_root_event_id=message.echo_from_thread_root_event_id or None,
        channel_echo_event_id=message.channel_echo_event_id or None,
        thread=_thread_summary_from_proto(message.thread) if message.thread is not None else None,
    )


# field -> builder(payload, message) for every live realtime.proto oneof
# case. `message` is only consulted by message_posted.
_EVENT_BUILDERS: dict[str, Any] = {
    "message_posted": lambda p, m: _message_posted_from_proto(p, m),
    "message_edited": lambda p, m: MessageUpdatedEvent(
        room_id=p.room_id,
        message_event_id=p.message_event_id,
        body=m.body if m is not None else None,
    ),
    "message_retracted": lambda p, m: MessageDeletedEvent(
        room_id=p.room_id, message_event_id=p.message_event_id, reason=p.reason
    ),
    "reaction_added": lambda p, m: ReactionAddedEvent(
        room_id=p.room_id, message_event_id=p.message_event_id, emoji=p.emoji
    ),
    "reaction_removed": lambda p, m: ReactionRemovedEvent(
        room_id=p.room_id, message_event_id=p.message_event_id, emoji=p.emoji
    ),
    "user_typing": lambda p, m: UserTypingEvent(
        room_id=p.room_id, thread_root_event_id=p.thread_root_event_id
    ),
    "presence_changed": lambda p, m: PresenceChangedEvent(
        user_id=p.user_id, status=normalize_presence_status(_enum_name(p, "status"))
    ),
    "room_created": lambda p, m: RoomCreatedEvent(room_id=p.room_id),
    "room_updated": lambda p, m: RoomUpdatedEvent(room_id=p.room_id),
    "room_deleted": lambda p, m: RoomDeletedEvent(room_id=p.room_id),
    "room_archived": lambda p, m: RoomArchivedEvent(room_id=p.room_id),
    "room_unarchived": lambda p, m: RoomUnarchivedEvent(room_id=p.room_id),
    "user_joined_room": lambda p, m: UserJoinedRoomEvent(room_id=p.room_id),
    "user_left_room": lambda p, m: UserLeftRoomEvent(room_id=p.room_id),
    "room_universal_changed": lambda p, m: RoomUniversalChangedEvent(
        room_id=p.room_id, universal=p.universal
    ),
    "notification_created": lambda p, m: NotificationCreatedEvent(
        notification_id=p.notification_id,
        room_id=p.room_id or "",
        event_id=p.event_id or "",
        in_reply_to_id=p.in_reply_to_id,
        silent=p.silent,
    ),
    "notification_dismissed": lambda p, m: NotificationDismissedEvent(
        notification_id=p.notification_id
    ),
    "notification_level_changed": lambda p, m: NotificationLevelChangedEvent(
        room_id=p.room_id,
        level=_enum_name(p, "level", "NOTIFICATION_LEVEL_"),
        effective_level=_enum_name(p, "effective_level", "NOTIFICATION_LEVEL_"),
    ),
    "thread_follow_changed": lambda p, m: ThreadFollowChangedEvent(
        room_id=p.room_id,
        thread_root_event_id=p.thread_root_event_id,
        following=p.following,
    ),
    "room_marked_as_read": lambda p, m: RoomMarkedAsReadEvent(room_id=p.room_id),
    "thread_created": lambda p, m: ThreadCreatedEvent(
        room_id=p.room_id, thread_root_event_id=p.thread_root_event_id
    ),
    "server_updated": lambda p, m: ServerUpdatedEvent(
        name=p.name, description=p.description, logo_url=p.logo_url, banner_url=p.banner_url
    ),
    "user_profile_updated": lambda p, m: UserProfileUpdatedEvent(
        user_id=p.user_id, login=p.login, display_name=p.display_name, avatar_url=p.avatar_url
    ),
    "user_custom_status_set": lambda p, m: UserCustomStatusSetEvent(
        user_id=p.user_id,
        emoji=p.emoji,
        text=p.text,
        # expires_at is a proto3-optional message field: an unset Timestamp
        # still reads back as a (falsy-looking but truthy) default instance,
        # so HasField -- not truthiness -- is what actually says "was this
        # set".
        expires_at=format_cursor(p.expires_at) if p.HasField("expires_at") else None,
    ),
    "user_custom_status_cleared": lambda p, m: UserCustomStatusClearedEvent(user_id=p.user_id),
    "server_user_preferences_updated": lambda p, m: ServerUserPreferencesUpdatedEvent(
        timezone=p.timezone, time_format=_enum_name(p, "time_format", "TIME_FORMAT_")
    ),
    "room_groups_updated": lambda p, m: RoomGroupsUpdatedEvent(changed=p.changed),
    "server_member_deleted": lambda p, m: ServerMemberDeletedEvent(user_id=p.user_id),
    "asset_processing_started": lambda p, m: AssetProcessingStartedEvent(
        asset_id=p.asset_id, room_id=p.room_id, message_event_id=p.message_event_id
    ),
    "asset_processing_succeeded": lambda p, m: VideoProcessingCompletedEvent(
        asset_id=p.asset_id, room_id=p.room_id, message_event_id=p.message_event_id
    ),
    "asset_processing_failed": lambda p, m: AssetProcessingFailedEvent(
        asset_id=p.asset_id, room_id=p.room_id, message_event_id=p.message_event_id
    ),
    "asset_deleted": lambda p, m: AssetDeletedEvent(asset_id=p.asset_id, room_id=p.room_id),
    "call_started": lambda p, m: CallStartedEvent(
        room_id=p.room_id,
        call_id=p.call_id,
        source=_enum_name(p, "source", "REALTIME_CALL_EVENT_SOURCE_"),
    ),
    "call_participant_joined": lambda p, m: CallParticipantJoinedEvent(
        room_id=p.room_id,
        call_id=p.call_id,
        source=_enum_name(p, "source", "REALTIME_CALL_EVENT_SOURCE_"),
    ),
    "call_participant_left": lambda p, m: CallParticipantLeftEvent(
        room_id=p.room_id,
        call_id=p.call_id,
        source=_enum_name(p, "source", "REALTIME_CALL_EVENT_SOURCE_"),
    ),
    "call_ended": lambda p, m: CallEndedEvent(
        room_id=p.room_id,
        call_id=p.call_id,
        source=_enum_name(p, "source", "REALTIME_CALL_EVENT_SOURCE_"),
    ),
    "mention_notification": lambda p, m: MentionNotificationEvent(
        room_id=p.room_id,
        actor_user_id=p.actor_user_id,
        room_name=p.room_name,
        actor_display_name=p.actor_display_name,
    ),
    "new_direct_message_notification": lambda p, m: NewDirectMessageNotificationEvent(
        room_id=p.room_id,
        sender_id=p.sender_id,
        sender_display_name=p.sender_display_name,
        sender_avatar_url=p.sender_avatar_url,
        conversation_name=p.conversation_name,
    ),
    "session_terminated": lambda p, m: SessionTerminatedEvent(reason=p.reason),
}


def event_name(envelope: RealtimeEventEnvelope) -> str:
    """Get the public snake_case handler name for a realtime event envelope.

    This is the name bot authors pass to ``@on_event(...)``: almost always
    the oneof case name verbatim, except for the three renames in
    ``_ONEOF_RENAMES`` kept for GraphQL-era compatibility. Returns
    ``"unknown"`` if the envelope carries no event (oneof unset) or an
    oneof case this module doesn't model yet.
    """
    field_name = envelope.WhichOneof("event")
    if field_name is not None and field_name in _EVENT_BUILDERS:
        return _ONEOF_RENAMES.get(field_name, field_name)
    return "unknown"


def parse_envelope(
    envelope: RealtimeEventEnvelope,
    message: _ChattolibMessage | None = None,
    actor: _ChattolibUser | None = None,
) -> RoomEvent:
    """Adapt a realtime envelope (plus anything hydrate.py fetched) to a RoomEvent.

    ``message`` is the hydrated ``Message`` dataclass for ``message_posted``
    (ignored for every other event type). ``actor`` is the hydrated ``User``
    dataclass for ``envelope.actor_id``, resolved through ``UserCache``.
    """
    field_name = envelope.WhichOneof("event")
    payload = getattr(envelope, field_name) if field_name else None

    if field_name is not None and field_name in _EVENT_BUILDERS:
        inner: EventType = _EVENT_BUILDERS[field_name](payload, message)
    else:
        logger.warning("Unmodeled realtime event oneof case: %s", field_name)
        inner = UnknownEvent(typename=field_name or "", raw={})

    # created_at is a proto3-optional message field: an unset Timestamp
    # still reads back as a default instance, not None, so HasField (not
    # truthiness) says whether it was actually set.
    created_at = envelope.created_at if envelope.HasField("created_at") else None

    return RoomEvent(
        actor_id=envelope.actor_id or "",
        event=inner,
        id=envelope.id or "",
        created_at=format_cursor(created_at),
        actor=_user_from_proto(actor) if actor is not None else None,
    )


def room_event_from_timeline(
    tev: TimelineEvent, actor: _ChattolibUser | None = None
) -> RoomEvent:
    """Adapt a hydrated ``chattolib.types.TimelineEvent`` (from
    ``GetRoomEvents``, used for reconnect catch-up) into the same public
    ``RoomEvent`` shape realtime dispatch produces.

    Unlike a realtime envelope, ``TimelineEvent`` is already flat -- a plain
    dataclass with a string ``kind`` and an inline ``message``, not a oneof
    -- because chattolib parses the timeline response itself. Only the
    kinds ``TimelineEvent.parse`` recognizes (``message_posted`` plus the
    room lifecycle/membership events) can appear here; that's also the
    historical set ``GetRoomEvents`` ever returned, so switching from the
    old oneof-shaped timeline proto to this flat shape loses no coverage.
    """
    kind = tev.kind or ""
    builder = _TIMELINE_EVENT_BUILDERS.get(kind)
    if builder is not None:
        inner: EventType = builder(tev)
    else:
        logger.warning("Unmodeled timeline event kind: %s", kind)
        inner = UnknownEvent(typename=kind, raw={})

    return RoomEvent(
        actor_id=tev.actor_id or "",
        event=inner,
        id=tev.id or "",
        created_at=format_cursor(tev.created_at),
        actor=_user_from_proto(actor) if actor is not None else None,
    )


_TIMELINE_EVENT_BUILDERS: dict[str, Any] = {
    "message_posted": lambda tev: _message_posted_from_proto(tev, tev.message),
    "room_created": lambda tev: RoomCreatedEvent(room_id=tev.room_id),
    "room_updated": lambda tev: RoomUpdatedEvent(room_id=tev.room_id),
    "room_deleted": lambda tev: RoomDeletedEvent(room_id=tev.room_id),
    "room_archived": lambda tev: RoomArchivedEvent(room_id=tev.room_id),
    "room_unarchived": lambda tev: RoomUnarchivedEvent(room_id=tev.room_id),
    "user_joined_room": lambda tev: UserJoinedRoomEvent(room_id=tev.room_id),
    "user_left_room": lambda tev: UserLeftRoomEvent(room_id=tev.room_id),
}
