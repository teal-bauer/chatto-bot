"""Tests for types.py — realtime envelope parsing, event-name table, cursors.

Realtime envelopes are now real google-protobuf messages from chattolib's
bundled ``realtime_pb2`` (Google protobuf, not the old protobuf-py runtime),
and hydrated resources (``Message``/``User``) are chattolib's own dataclasses
-- see ``chattolib.types``. ``_FakeEnvelope`` stands in only for the "oneof
case our code doesn't model" test, since a real protobuf message can't carry
a field that isn't in its schema.
"""

from __future__ import annotations

import datetime

import pytest
from chattolib._pb.chatto.realtime.v1 import realtime_pb2 as rt
from chattolib.types import Message, PresenceStatus, User as ChattolibUser
from google.protobuf.timestamp_pb2 import Timestamp

from chatto_bot.types import (
    MessageDeletedEvent,
    MessagePostedEvent,
    MessageUpdatedEvent,
    ReactionAddedEvent,
    RETIRED_EVENT_NAMES,
    RoomArchivedEvent,
    RoomEvent,
    UnknownEvent,
    VideoProcessingCompletedEvent,
    event_name,
    format_cursor,
    normalize_presence_status,
    parse_envelope,
    warn_if_retired_event_name,
)


def _ts(dt: datetime.datetime) -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(dt)
    return ts


def _envelope(
    field: str, payload, *, id="E1", actor_id="U1", created_at=None
) -> rt.RealtimeEventEnvelope:
    return rt.RealtimeEventEnvelope(
        id=id,
        actor_id=actor_id,
        created_at=created_at
        or _ts(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)),
        **{field: payload},
    )


class _FakeEnvelope:
    """Stands in for a realtime envelope carrying an oneof case this module
    doesn't model -- a real protobuf message can't hold a field its schema
    doesn't define, so this fakes just enough of the duck-typed protocol
    (``WhichOneof``/``HasField``/attribute access) to exercise that path."""

    def __init__(self, field: str, payload, *, id="E1", actor_id="U1"):
        self._field = field
        self.id = id
        self.actor_id = actor_id
        setattr(self, field, payload)

    def WhichOneof(self, name: str) -> str:
        assert name == "event"
        return self._field

    def HasField(self, name: str) -> bool:
        return False


class TestEventName:
    def test_one_to_one_case(self):
        env = _envelope(
            "reaction_added",
            rt.RealtimeReactionEvent(room_id="R1", message_event_id="E1", emoji="heart"),
        )
        assert event_name(env) == "reaction_added"

    def test_message_posted(self):
        env = _envelope(
            "message_posted",
            rt.RealtimeMessagePostedEvent(room_id="R1", message_event_id="E1"),
        )
        assert event_name(env) == "message_posted"

    def test_renames(self):
        edited = _envelope(
            "message_edited",
            rt.RealtimeMessageEditedEvent(room_id="R1", message_event_id="E1"),
        )
        assert event_name(edited) == "message_updated"

        retracted = _envelope(
            "message_retracted",
            rt.RealtimeMessageRetractedEvent(room_id="R1", message_event_id="E1"),
        )
        assert event_name(retracted) == "message_deleted"

        succeeded = _envelope(
            "asset_processing_succeeded",
            rt.RealtimeAssetProcessingEvent(room_id="R1", asset_id="A1"),
        )
        assert event_name(succeeded) == "video_processing_completed"

    def test_unmodeled_oneof_case_is_unknown(self):
        env = _FakeEnvelope("some_future_event", rt.RealtimeRoomEvent(room_id="R1"))
        assert event_name(env) == "unknown"

    def test_empty_oneof_is_unknown(self):
        env = rt.RealtimeEventEnvelope(id="E1", actor_id="U1")
        assert event_name(env) == "unknown"


class TestParseEnvelope:
    def test_message_posted_uses_hydrated_message_over_signal(self):
        env = _envelope(
            "message_posted",
            rt.RealtimeMessagePostedEvent(room_id="R-signal", message_event_id="E1"),
        )
        message = Message(
            id="E1", room_id="R-hydrated", created_at=None, actor_id="U1", body="hello world"
        )
        actor = ChattolibUser(id="U1", login="alice", display_name="Alice")

        room_event = parse_envelope(env, message=message, actor=actor)

        assert isinstance(room_event, RoomEvent)
        assert isinstance(room_event.event, MessagePostedEvent)
        # Uses the hydrated message's room_id, not the bare signal's.
        assert room_event.event.room_id == "R-hydrated"
        assert room_event.event.body == "hello world"
        assert room_event.actor.login == "alice"
        assert room_event.actor_id == "U1"
        assert room_event.id == "E1"

    def test_message_posted_without_hydration_falls_back_to_signal(self):
        """Defensive fallback for direct parse_envelope() use; normal dispatch
        always hydrates message_posted (see hydrate.py)."""
        env = _envelope(
            "message_posted",
            rt.RealtimeMessagePostedEvent(room_id="R-signal", message_event_id="E1"),
        )
        room_event = parse_envelope(env)
        assert isinstance(room_event.event, MessagePostedEvent)
        assert room_event.event.room_id == "R-signal"
        assert room_event.event.body is None

    def test_rename_builds_correct_dataclass(self):
        env = _envelope(
            "message_retracted",
            rt.RealtimeMessageRetractedEvent(
                room_id="R1", message_event_id="E1", reason="spam"
            ),
        )
        room_event = parse_envelope(env)
        assert isinstance(room_event.event, MessageDeletedEvent)
        assert room_event.event.reason == "spam"

    def test_message_edited_builds_updated_dataclass(self):
        env = _envelope(
            "message_edited",
            rt.RealtimeMessageEditedEvent(room_id="R1", message_event_id="E9"),
        )
        room_event = parse_envelope(env)
        assert isinstance(room_event.event, MessageUpdatedEvent)
        assert room_event.event.message_event_id == "E9"
        assert room_event.event.body is None  # no message passed in -- nothing to populate

    def test_message_edited_uses_hydrated_message_body(self):
        """D4: hydrate.py already fetches the current Message for
        message_edited (to enforce the retracted-between-signal-and-fetch
        drop contract); the builder must not throw that fetch away -- a
        message_updated handler should see the edited body without an extra
        ctx.fetch_message() round trip."""
        env = _envelope(
            "message_edited",
            rt.RealtimeMessageEditedEvent(room_id="R1", message_event_id="E9"),
        )
        message = Message(
            id="E9", room_id="R1", created_at=None, actor_id="U1", body="edited text"
        )

        room_event = parse_envelope(env, message=message)

        assert isinstance(room_event.event, MessageUpdatedEvent)
        assert room_event.event.message_event_id == "E9"
        assert room_event.event.body == "edited text"

    def test_reaction_added(self):
        env = _envelope(
            "reaction_added",
            rt.RealtimeReactionEvent(room_id="R1", message_event_id="E1", emoji="thumbsup"),
        )
        room_event = parse_envelope(env)
        assert isinstance(room_event.event, ReactionAddedEvent)
        assert room_event.event.emoji == "thumbsup"

    def test_room_archived(self):
        env = _envelope("room_archived", rt.RealtimeRoomEvent(room_id="R1"))
        room_event = parse_envelope(env)
        assert isinstance(room_event.event, RoomArchivedEvent)
        assert room_event.event.room_id == "R1"

    def test_asset_processing_succeeded_builds_video_completed(self):
        env = _envelope(
            "asset_processing_succeeded",
            rt.RealtimeAssetProcessingEvent(room_id="R1", asset_id="A1", message_event_id="E1"),
        )
        room_event = parse_envelope(env)
        assert isinstance(room_event.event, VideoProcessingCompletedEvent)
        assert room_event.event.asset_id == "A1"

    def test_unmodeled_oneof_case_becomes_unknown_event(self):
        env = _FakeEnvelope("some_future_event", rt.RealtimeRoomEvent(room_id="R1"))
        room_event = parse_envelope(env)
        assert isinstance(room_event.event, UnknownEvent)
        assert room_event.event.typename == "some_future_event"

    def test_no_actor(self):
        env = _envelope("room_archived", rt.RealtimeRoomEvent(room_id="R1"), actor_id="")
        room_event = parse_envelope(env)
        assert room_event.actor is None
        assert room_event.actor_id == ""


class TestNormalizePresenceStatus:
    """D3: an unset/UNSPECIFIED presence status must normalize to "OFFLINE"
    (the dataclass default, and the value GraphQL-era bots compare against),
    not the literal enum name. chattolib's ``PresenceStatus`` ``str()``
    gives the full wire form (``"PRESENCE_STATUS_ONLINE"``), so
    ``normalize_presence_status`` must also strip that prefix."""

    def test_unspecified_normalizes_to_offline(self):
        assert normalize_presence_status(PresenceStatus.UNSPECIFIED) == "OFFLINE"

    def test_none_normalizes_to_offline(self):
        assert normalize_presence_status(None) == "OFFLINE"

    def test_online_passes_through(self):
        assert normalize_presence_status(PresenceStatus.ONLINE) == "ONLINE"

    def test_user_from_proto_via_parse_envelope(self):
        """End-to-end: an actor with an unset presence_status must not
        surface "UNSPECIFIED" on the public User dataclass."""
        env = _envelope("room_archived", rt.RealtimeRoomEvent(room_id="R1"))
        actor = ChattolibUser(id="U1", login="alice", display_name="Alice")  # presence unset

        room_event = parse_envelope(env, actor=actor)

        assert room_event.actor.presence_status == "OFFLINE"


class TestFormatCursor:
    def test_fixed_width(self):
        ts = _ts(datetime.datetime(2026, 3, 4, 5, 6, 7, 890, tzinfo=datetime.timezone.utc))
        cursor = format_cursor(ts)
        assert cursor == "2026-03-04T05:06:07.000890Z"

    def test_accepts_plain_datetime(self):
        """chattolib dataclasses (Message.updated_at, ThreadSummary.last_reply_at,
        ...) parse timestamps eagerly into plain ``datetime`` objects."""
        dt = datetime.datetime(2026, 3, 4, 5, 6, 7, 890, tzinfo=datetime.timezone.utc)
        assert format_cursor(dt) == "2026-03-04T05:06:07.000890Z"

    def test_none_returns_empty_string(self):
        assert format_cursor(None) == ""

    def test_lexical_ordering_matches_chronological(self):
        earlier = format_cursor(
            _ts(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))
        )
        later = format_cursor(
            _ts(datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc))
        )
        assert earlier < later


class TestRetiredEventNames:
    def test_retired_names_include_graphql_era_handlers(self):
        assert "user_created" in RETIRED_EVENT_NAMES
        assert "user_deleted" in RETIRED_EVENT_NAMES
        assert "server_config_updated" in RETIRED_EVENT_NAMES
        assert "mention_status_cleared" in RETIRED_EVENT_NAMES
        assert "heartbeat" in RETIRED_EVENT_NAMES

    def test_live_names_are_not_retired(self):
        assert "message_posted" not in RETIRED_EVENT_NAMES
        assert "message_updated" not in RETIRED_EVENT_NAMES

    def test_warn_if_retired_event_name_does_not_raise(self):
        # Should be a no-op (log a warning) for both retired and live names.
        warn_if_retired_event_name("user_created")
        warn_if_retired_event_name("message_posted")
