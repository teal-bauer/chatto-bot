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

import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    # Only used for type hints; parse_envelope/converters access proto
    # objects duck-typed by attribute name, so types.py has no runtime
    # dependency on _pb or the protobuf runtime.
    from ._pb.chatto.api.v1.message_types_pb import Message as _ProtoMessage
    from ._pb.chatto.api.v1.users_pb import User as _ProtoUser
    from ._pb.chatto.realtime.v1.realtime_pb import RealtimeEvent
    from protobuf.wkt import Timestamp

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
class SocialPostAuthor:
    display_name: str = ""
    handle: str = ""
    avatar_url: str | None = None
    avatar_asset_id: str | None = None


@dataclass
class SocialPostImage:
    url: str = ""
    asset_id: str = ""
    alt: str | None = None
    width: int | None = None
    height: int | None = None


@dataclass
class SocialPostExternalLink:
    url: str = ""
    title: str | None = None
    description: str | None = None
    image_url: str | None = None
    image_asset_id: str | None = None


@dataclass
class SocialPostPreview:
    provider: str = ""
    author: SocialPostAuthor | None = None
    text: str = ""
    published_at: str | None = None
    images: list[SocialPostImage] = field(default_factory=list)
    external_link: SocialPostExternalLink | None = None
    content_warning: str | None = None
    url: str = ""
    # Server omits quotes nested inside a quote, so this is always None on a
    # quoted_post's own quoted_post in practice.
    quoted_post: SocialPostPreview | None = None


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
    social_post: SocialPostPreview | None = None


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
    # Set when the message's content was already deleted (retraction or
    # crypto-shredding) by the time it was hydrated.
    deleted_at: str | None = None


@dataclass
class MessageUpdatedEvent:
    """From ``message_edited``.

    ``hydrate.py`` already fetches the current ``Message`` for this event (to
    honor the retracted-between-signal-and-fetch drop contract), so ``body``
    is populated from that fetch when available -- no need to always call
    ``ctx.fetch_message()`` just to read the edited text. It's still ``None``
    if hydration didn't run (e.g. direct ``parse_envelope()`` use without a
    message passed in); ``ctx.fetch_message()`` remains the way to get
    attachments, reactions, etc. ``deleted_at`` is likewise only populated
    when a hydrated message is passed in.
    """

    room_id: str
    message_event_id: str
    body: str | None = None
    deleted_at: str | None = None


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


# --- Semantic-event catalogue: one dataclass per current events.proto oneof case ---


@dataclass
class RoomSlowModeChangedEvent:
    room_id: str
    slow_mode_seconds: int = 0


@dataclass
class RoomThreadingModeChangedEvent:
    room_id: str
    threading_mode: str = ""


@dataclass
class MessagePinnedEvent:
    room_id: str
    message_event_id: str


@dataclass
class MessageUnpinnedEvent:
    room_id: str
    message_event_id: str


@dataclass
class ServerMotdChangedEvent:
    motd: str = ""


@dataclass
class ViewerPreferencesChangedEvent:
    """Caller-private preferences changed; refetch ViewerService for details."""


@dataclass
class ThreadViewerStateChangedEvent:
    room_id: str
    thread_root_event_id: str = ""
    is_following: bool = False


@dataclass
class ServerProfileChangedEvent:
    """Public server profile changed; refetch ServerService for details."""


@dataclass
class NotificationOccurrencesChangedEvent:
    # Best-effort live hint, absent for updates/removals; clients decide
    # whether to alert after reading current notification state.
    created_notification_id: str | None = None


@dataclass
class NotificationUnreadStateChangedEvent:
    room_id: str
    thread_root_event_id: str = ""


@dataclass
class RoomReadStateChangedEvent:
    room_id: str


@dataclass
class RoomLayoutChangedEvent:
    """Room groups/layout changed; refetch ListRoomGroups for details."""


@dataclass
class RoleCreatedEvent:
    role_name: str = ""


@dataclass
class RoleUpdatedEvent:
    role_name: str = ""


@dataclass
class RoleDeletedEvent:
    role_name: str = ""


@dataclass
class RolesReorderedEvent:
    role_names: list[str] = field(default_factory=list)


@dataclass
class RoleAssignedEvent:
    user_id: str = ""
    role_name: str = ""


@dataclass
class RoleRevokedEvent:
    user_id: str = ""
    role_name: str = ""


@dataclass
class RolePermissionsChangedEvent:
    role_name: str = ""


@dataclass
class ViewerPermissionsChangedEvent:
    """Viewer permissions may have changed; refetch before displaying them."""


@dataclass
class ViewerPresencePreferenceChangedEvent:
    """Private presence choice changed; refetch GetPresencePreference."""


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
    | RoomSlowModeChangedEvent
    | RoomThreadingModeChangedEvent
    | MessagePinnedEvent
    | MessageUnpinnedEvent
    | ServerMotdChangedEvent
    | ViewerPreferencesChangedEvent
    | ThreadViewerStateChangedEvent
    | ServerProfileChangedEvent
    | NotificationOccurrencesChangedEvent
    | NotificationUnreadStateChangedEvent
    | RoomReadStateChangedEvent
    | RoomLayoutChangedEvent
    | RoleCreatedEvent
    | RoleUpdatedEvent
    | RoleDeletedEvent
    | RolesReorderedEvent
    | RoleAssignedEvent
    | RoleRevokedEvent
    | RolePermissionsChangedEvent
    | ViewerPermissionsChangedEvent
    | ViewerPresencePreferenceChangedEvent
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
# (the name bot authors pass to @on_event(...)). Most cases match their oneof
# field name 1:1; these are renamed to keep the handler names bots already
# use -- the three GraphQL-era renames plus names the realtime catalogue
# itself changed:
_ONEOF_RENAMES: dict[str, str] = {
    "message_edited": "message_updated",
    "message_retracted": "message_deleted",
    "asset_processing_succeeded": "video_processing_completed",
    "user_profile_changed": "user_profile_updated",
    "user_account_created": "user_created",
    "user_account_deleted": "user_deleted",
    "voice_call_started": "call_started",
    "voice_call_participant_joined": "call_participant_joined",
    "voice_call_participant_left": "call_participant_left",
    "voice_call_ended": "call_ended",
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
    # Live again under their old names, mapped from user_account_created /
    # user_account_deleted (see _ONEOF_RENAMES).
    "user_created": UserCreatedEvent,
    "user_deleted": UserDeletedEvent,
    # Semantic-event catalogue additions.
    "room_slow_mode_changed": RoomSlowModeChangedEvent,
    "room_threading_mode_changed": RoomThreadingModeChangedEvent,
    "message_pinned": MessagePinnedEvent,
    "message_unpinned": MessageUnpinnedEvent,
    "server_motd_changed": ServerMotdChangedEvent,
    "viewer_preferences_changed": ViewerPreferencesChangedEvent,
    "thread_viewer_state_changed": ThreadViewerStateChangedEvent,
    "server_profile_changed": ServerProfileChangedEvent,
    "notification_occurrences_changed": NotificationOccurrencesChangedEvent,
    "notification_unread_state_changed": NotificationUnreadStateChangedEvent,
    "room_read_state_changed": RoomReadStateChangedEvent,
    "room_layout_changed": RoomLayoutChangedEvent,
    "role_created": RoleCreatedEvent,
    "role_updated": RoleUpdatedEvent,
    "role_deleted": RoleDeletedEvent,
    "roles_reordered": RolesReorderedEvent,
    "role_assigned": RoleAssignedEvent,
    "role_revoked": RoleRevokedEvent,
    "role_permissions_changed": RolePermissionsChangedEvent,
    "viewer_permissions_changed": ViewerPermissionsChangedEvent,
    "viewer_presence_preference_changed": ViewerPresencePreferenceChangedEvent,
    # Retired: no realtime.v1 source, never dispatched. Kept so
    # warn_if_retired_event_name() and documentation have something to point
    # at.
    "server_config_updated": ServerConfigUpdatedEvent,
    "mention_status_cleared": MentionStatusClearedEvent,
    "heartbeat": HeartbeatEvent,
    "unknown": UnknownEvent,
}

# Old handler names the realtime stream no longer emits. A bot that
# registers one of these will never see it fire.
RETIRED_EVENT_NAMES: frozenset[str] = frozenset(
    {
        # Never had a realtime.v1 source.
        "server_config_updated",
        "mention_status_cleared",
        "heartbeat",
        # Superseded by the semantic-event catalogue: these now arrive as
        # refetch hints (notification_occurrences_changed,
        # notification_unread_state_changed, room_read_state_changed,
        # thread_viewer_state_changed, viewer_preferences_changed,
        # server_profile_changed, viewer_presence_preference_changed) or
        # not at all.
        "notification_created",
        "notification_dismissed",
        "notification_level_changed",
        "thread_follow_changed",
        "room_marked_as_read",
        "server_updated",
        "user_custom_status_set",
        "user_custom_status_cleared",
        "server_user_preferences_updated",
        "room_groups_updated",
        "server_member_deleted",
        "mention_notification",
        "new_direct_message_notification",
        "session_terminated",
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
            "Registered a handler for %r, which is retired: realtime.v1 no "
            "longer emits this event, so this handler will never fire.",
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
# These take generated protobuf-py message instances (accessed duck-typed by
# attribute, so this module never imports the _pb packages at runtime) and
# return the public dataclasses above.


def format_cursor(ts: Timestamp | None) -> str:
    """Format a protobuf ``Timestamp`` as a fixed-width UTC ISO string.

    Always emits 6 fractional digits so lexical string comparison stays
    monotonic. The old GraphQL cursor format didn't guarantee a consistent
    fractional-second width, so comparing a fixed-width cursor against an
    old-format one is only best-effort across the upgrade boundary -- see
    the migration note in the design doc.
    """
    if ts is None:
        return ""
    dt = ts.to_datetime()
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def normalize_presence_status(raw: Any) -> str:
    """Map a proto ``PresenceStatus`` enum value to the handler-facing string.

    Proto enum scalars are never ``None`` (unset reads as the zero value,
    ``PRESENCE_STATUS_UNSPECIFIED``, whose ``str()`` is ``"UNSPECIFIED"``),
    so a bare ``str(status)`` would surface "UNSPECIFIED" for a user who has
    simply never set a presence. Normalize that (and any other falsy/missing
    value) to ``"OFFLINE"`` -- the dataclass default and the value the
    GraphQL-era API used to send, so bots comparing ``== "OFFLINE"`` keep
    working.
    """
    text = str(raw) if raw is not None else ""
    return text if text and text != "UNSPECIFIED" else "OFFLINE"


def _user_from_proto(u: _ProtoUser) -> User:
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


def _opt(msg: Any, name: str) -> Any:
    """Read a proto3 ``optional`` scalar, preserving absence.

    The generated runtime reports an unset optional scalar as the zero value
    ("" or 0), which is indistinguishable from one the server set explicitly.
    link_previews.proto asks clients to treat absent metadata as unavailable,
    so key off presence and map absent to None.
    """
    return getattr(msg, name) if msg.has_field(name) else None


def _social_post_author_from_proto(a: Any) -> SocialPostAuthor:
    return SocialPostAuthor(
        display_name=a.display_name,
        handle=a.handle,
        avatar_url=_opt(a, "avatar_url"),
        avatar_asset_id=_opt(a, "avatar_asset_id"),
    )


def _social_post_image_from_proto(img: Any) -> SocialPostImage:
    return SocialPostImage(
        url=img.url,
        asset_id=img.asset_id,
        alt=_opt(img, "alt"),
        width=_opt(img, "width"),
        height=_opt(img, "height"),
    )


def _social_post_external_link_from_proto(el: Any) -> SocialPostExternalLink:
    return SocialPostExternalLink(
        url=el.url,
        title=_opt(el, "title"),
        description=_opt(el, "description"),
        image_url=_opt(el, "image_url"),
        image_asset_id=_opt(el, "image_asset_id"),
    )


def _social_post_from_proto(sp: Any) -> SocialPostPreview:
    return SocialPostPreview(
        provider=sp.provider,
        author=_social_post_author_from_proto(sp.author) if sp.author is not None else None,
        text=sp.text,
        published_at=format_cursor(sp.published_at) if sp.published_at else None,
        images=[_social_post_image_from_proto(i) for i in sp.images],
        external_link=_social_post_external_link_from_proto(sp.external_link)
        if sp.external_link is not None
        else None,
        content_warning=_opt(sp, "content_warning"),
        url=sp.url,
        # Server-side depth limiting (see SocialPostPreview.quoted_post) means
        # this recursion bottoms out after one level in practice.
        quoted_post=_social_post_from_proto(sp.quoted_post) if sp.quoted_post is not None else None,
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
        social_post=_social_post_from_proto(lp.social_post) if lp.social_post is not None else None,
    )


def _thread_viewer_state_from_proto(vs: Any) -> ThreadViewerState:
    return ThreadViewerState(is_following=vs.is_following, has_unread=vs.has_unread)


def _thread_summary_from_proto(t: Any) -> ThreadSummary:
    return ThreadSummary(
        thread_root_event_id=t.thread_root_event_id,
        reply_count=t.reply_count,
        last_reply_at=format_cursor(t.last_reply_at) if t.last_reply_at else None,
        participant_preview_user_ids=list(t.participant_preview_user_ids),
        participant_count=t.participant_count,
        viewer_state=_thread_viewer_state_from_proto(t.viewer_state)
        if t.viewer_state is not None
        else None,
    )


def _message_posted_from_proto(signal: Any, message: _ProtoMessage | None) -> MessagePostedEvent:
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
        deleted_at=format_cursor(message.deleted_at) if message.deleted_at else None,
    )


# field -> builder(payload, message) for every live realtime oneof case.
# `message` is only consulted by message_posted and message_edited.
_EVENT_BUILDERS: dict[str, Any] = {
    "message_posted": lambda p, m: _message_posted_from_proto(p, m),
    "message_edited": lambda p, m: MessageUpdatedEvent(
        room_id=p.room_id,
        message_event_id=p.message_event_id,
        body=m.body if m is not None else None,
        deleted_at=format_cursor(m.deleted_at) if m is not None and m.deleted_at else None,
    ),
    "message_retracted": lambda p, m: MessageDeletedEvent(
        room_id=p.room_id,
        message_event_id=p.message_event_id,
        reason=getattr(p, "reason", None),
    ),
    "message_pinned": lambda p, m: MessagePinnedEvent(
        room_id=p.room_id, message_event_id=p.message_event_id
    ),
    "message_unpinned": lambda p, m: MessageUnpinnedEvent(
        room_id=p.room_id, message_event_id=p.message_event_id
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
    # presence_changed no longer carries a user ID; parse_envelope fills
    # user_id from the envelope's actor afterwards.
    "presence_changed": lambda p, m: PresenceChangedEvent(
        user_id="", status=str(p.status)
    ),
    "room_created": lambda p, m: RoomCreatedEvent(room_id=p.room_id),
    "room_updated": lambda p, m: RoomUpdatedEvent(room_id=p.room_id),
    "room_deleted": lambda p, m: RoomDeletedEvent(room_id=p.room_id),
    "room_archived": lambda p, m: RoomArchivedEvent(room_id=p.room_id),
    "room_unarchived": lambda p, m: RoomUnarchivedEvent(room_id=p.room_id),
    "room_universal_changed": lambda p, m: RoomUniversalChangedEvent(
        room_id=p.room_id, universal=p.universal
    ),
    "room_slow_mode_changed": lambda p, m: RoomSlowModeChangedEvent(
        room_id=p.room_id, slow_mode_seconds=p.slow_mode_seconds
    ),
    "room_threading_mode_changed": lambda p, m: RoomThreadingModeChangedEvent(
        room_id=p.room_id, threading_mode=str(p.threading_mode)
    ),
    "user_joined_room": lambda p, m: UserJoinedRoomEvent(room_id=p.room_id),
    "user_left_room": lambda p, m: UserLeftRoomEvent(room_id=p.room_id),
    "thread_created": lambda p, m: ThreadCreatedEvent(
        room_id=p.room_id, thread_root_event_id=p.thread_root_event_id
    ),
    "thread_viewer_state_changed": lambda p, m: ThreadViewerStateChangedEvent(
        room_id=p.room_id,
        thread_root_event_id=p.thread_root_event_id,
        is_following=p.is_following,
    ),
    "room_read_state_changed": lambda p, m: RoomReadStateChangedEvent(
        room_id=p.room_id
    ),
    "room_layout_changed": lambda p, m: RoomLayoutChangedEvent(),
    "server_motd_changed": lambda p, m: ServerMotdChangedEvent(motd=p.motd),
    "server_profile_changed": lambda p, m: ServerProfileChangedEvent(),
    "user_account_created": lambda p, m: UserCreatedEvent(user_id=p.user_id),
    "user_profile_changed": lambda p, m: UserProfileUpdatedEvent(
        user_id=p.user_id,
        login=None,
        display_name=None,
        avatar_url=None,
    ),
    "user_account_deleted": lambda p, m: UserDeletedEvent(user_id=p.user_id),
    "viewer_preferences_changed": lambda p, m: ViewerPreferencesChangedEvent(),
    "viewer_permissions_changed": lambda p, m: ViewerPermissionsChangedEvent(),
    "viewer_presence_preference_changed": lambda p, m: ViewerPresencePreferenceChangedEvent(),
    "notification_occurrences_changed": lambda p, m: NotificationOccurrencesChangedEvent(
        created_notification_id=p.created_notification_id or None
    ),
    "notification_unread_state_changed": lambda p, m: NotificationUnreadStateChangedEvent(
        room_id=p.room_id, thread_root_event_id=p.thread_root_event_id
    ),
    "role_created": lambda p, m: RoleCreatedEvent(role_name=p.role_name),
    "role_updated": lambda p, m: RoleUpdatedEvent(role_name=p.role_name),
    "role_deleted": lambda p, m: RoleDeletedEvent(role_name=p.role_name),
    "roles_reordered": lambda p, m: RolesReorderedEvent(role_names=list(p.role_names)),
    "role_assigned": lambda p, m: RoleAssignedEvent(
        user_id=p.user_id, role_name=p.role_name
    ),
    "role_revoked": lambda p, m: RoleRevokedEvent(
        user_id=p.user_id, role_name=p.role_name
    ),
    "role_permissions_changed": lambda p, m: RolePermissionsChangedEvent(
        role_name=p.role_name
    ),
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
    "voice_call_started": lambda p, m: CallStartedEvent(
        room_id=p.room_id, call_id=p.call_id, source=getattr(p, "source", "")
    ),
    "voice_call_participant_joined": lambda p, m: CallParticipantJoinedEvent(
        room_id=p.room_id, call_id=p.call_id, source=getattr(p, "source", "")
    ),
    "voice_call_participant_left": lambda p, m: CallParticipantLeftEvent(
        room_id=p.room_id, call_id=p.call_id, source=getattr(p, "source", "")
    ),
    "voice_call_ended": lambda p, m: CallEndedEvent(
        room_id=p.room_id, call_id=p.call_id, source=getattr(p, "source", "")
    ),
}


def event_name(envelope: RealtimeEvent) -> str:
    """Get the public snake_case handler name for a realtime event.

    This is the name bot authors pass to ``@on_event(...)``: almost always
    the oneof case name verbatim, except for the renames in
    ``_ONEOF_RENAMES`` kept for compatibility. Returns ``"unknown"`` if the
    event carries no payload (oneof unset) or an oneof case this module
    doesn't model yet.
    """
    oneof = envelope.event
    if oneof is None:
        return "unknown"
    field_name = oneof.field
    if field_name in _EVENT_BUILDERS:
        return _ONEOF_RENAMES.get(field_name, field_name)
    return "unknown"


def parse_envelope(
    envelope: RealtimeEvent,
    message: _ProtoMessage | None = None,
    actor: _ProtoUser | None = None,
) -> RoomEvent:
    """Adapt a realtime event (plus anything hydrate.py fetched) to a RoomEvent.

    ``message`` is the hydrated ``Message`` proto for ``message_posted``
    (ignored for every other event type). ``actor`` is the hydrated ``User``
    proto for ``envelope.actor_id``, resolved through ``UserCache``.
    """
    oneof = envelope.event
    field_name = oneof.field if oneof is not None else None
    payload = oneof.value if oneof is not None else None

    if field_name is not None and field_name in _EVENT_BUILDERS:
        inner: EventType = _EVENT_BUILDERS[field_name](payload, message)
    else:
        logger.warning("Unmodeled realtime event oneof case: %s", field_name)
        inner = UnknownEvent(typename=field_name or "", raw={})

    if isinstance(inner, PresenceChangedEvent) and not inner.user_id:
        # presence_changed no longer carries a user ID; the actor is the
        # affected user.
        inner.user_id = envelope.actor_id or ""

    return RoomEvent(
        actor_id=envelope.actor_id or "",
        event=inner,
        id=envelope.id or "",
        created_at=format_cursor(envelope.created_at),
        actor=_user_from_proto(actor) if actor is not None else None,
    )
