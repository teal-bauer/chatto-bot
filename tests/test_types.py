"""Tests for types.py — realtime envelope parsing, event-name table, cursors."""

from __future__ import annotations

import datetime

import pytest
from protobuf import Oneof
from protobuf.wkt import Timestamp

from chatto_bot._pb.chatto.api.v1.link_previews_pb import (
    LinkPreview as ProtoLinkPreview,
    SocialPostAuthor as ProtoSocialPostAuthor,
    SocialPostExternalLink as ProtoSocialPostExternalLink,
    SocialPostImage as ProtoSocialPostImage,
    SocialPostPreview as ProtoSocialPostPreview,
)
from chatto_bot._pb.chatto.api.v1.message_types_pb import Message
from chatto_bot._pb.chatto.api.v1.users_pb import User as ProtoUser
from chatto_bot._pb.chatto.realtime.v1 import realtime_pb as rt
from chatto_bot._pb.chatto.api.v1.presence_pb import PresenceStatus
from chatto_bot.types import (
    MessageDeletedEvent,
    MessagePostedEvent,
    MessageUpdatedEvent,
    ReactionAddedEvent,
    RETIRED_EVENT_NAMES,
    RoomArchivedEvent,
    RoomEvent,
    SocialPostPreview,
    UnknownEvent,
    VideoProcessingCompletedEvent,
    event_name,
    format_cursor,
    normalize_presence_status,
    parse_envelope,
    warn_if_retired_event_name,
)


def _envelope(field: str, payload, *, id="E1", actor_id="U1", created_at=None) -> rt.RealtimeEventEnvelope:
    return rt.RealtimeEventEnvelope(
        id=id,
        actor_id=actor_id,
        created_at=created_at or Timestamp.from_datetime(
            datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        ),
        event=Oneof(field, payload),
    )


class TestEventName:
    def test_one_to_one_case(self):
        env = _envelope("reaction_added", rt.RealtimeReactionEvent(room_id="R1", message_event_id="E1", emoji="heart"))
        assert event_name(env) == "reaction_added"

    def test_message_posted(self):
        env = _envelope("message_posted", rt.RealtimeMessagePostedEvent(room_id="R1", message_event_id="E1"))
        assert event_name(env) == "message_posted"

    def test_renames(self):
        edited = _envelope("message_edited", rt.RealtimeMessageEditedEvent(room_id="R1", message_event_id="E1"))
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
        env = _envelope("some_future_event", rt.RealtimeRoomEvent(room_id="R1"))
        assert event_name(env) == "unknown"

    def test_empty_oneof_is_unknown(self):
        env = rt.RealtimeEventEnvelope(id="E1", actor_id="U1")
        assert event_name(env) == "unknown"


class TestParseEnvelope:
    def test_message_posted_uses_hydrated_message_over_signal(self):
        env = _envelope(
            "message_posted", rt.RealtimeMessagePostedEvent(room_id="R-signal", message_event_id="E1")
        )
        message = Message(id="E1", room_id="R-hydrated", body="hello world")
        actor = ProtoUser(id="U1", login="alice", display_name="Alice")

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
            "message_posted", rt.RealtimeMessagePostedEvent(room_id="R-signal", message_event_id="E1")
        )
        room_event = parse_envelope(env)
        assert isinstance(room_event.event, MessagePostedEvent)
        assert room_event.event.room_id == "R-signal"
        assert room_event.event.body is None

    def test_rename_builds_correct_dataclass(self):
        env = _envelope(
            "message_retracted",
            rt.RealtimeMessageRetractedEvent(room_id="R1", message_event_id="E1", reason="spam"),
        )
        room_event = parse_envelope(env)
        assert isinstance(room_event.event, MessageDeletedEvent)
        assert room_event.event.reason == "spam"

    def test_message_edited_builds_updated_dataclass(self):
        env = _envelope(
            "message_edited", rt.RealtimeMessageEditedEvent(room_id="R1", message_event_id="E9")
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
            "message_edited", rt.RealtimeMessageEditedEvent(room_id="R1", message_event_id="E9")
        )
        message = Message(id="E9", room_id="R1", body="edited text")

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
        env = _envelope("some_future_event", rt.RealtimeRoomEvent(room_id="R1"))
        room_event = parse_envelope(env)
        assert isinstance(room_event.event, UnknownEvent)
        assert room_event.event.typename == "some_future_event"

    def test_no_actor(self):
        env = _envelope("room_archived", rt.RealtimeRoomEvent(room_id="R1"), actor_id="")
        room_event = parse_envelope(env)
        assert room_event.actor is None
        assert room_event.actor_id == ""


class TestNormalizePresenceStatus:
    """D3: an unset/UNSPECIFIED proto presence_status must normalize to
    "OFFLINE" (the dataclass default, and the value GraphQL-era bots
    compare against), not the literal enum name "UNSPECIFIED". Proto enum
    scalars are never None -- unset reads as the zero value -- so a bare
    ``str(status)`` surfaces "UNSPECIFIED" for a user who simply never set
    a presence."""

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
        actor = ProtoUser(id="U1", login="alice", display_name="Alice")  # presence_status unset

        room_event = parse_envelope(env, actor=actor)

        assert room_event.actor.presence_status == "OFFLINE"


class TestFormatCursor:
    def test_fixed_width(self):
        ts = Timestamp.from_datetime(
            datetime.datetime(2026, 3, 4, 5, 6, 7, 890, tzinfo=datetime.timezone.utc)
        )
        cursor = format_cursor(ts)
        assert cursor == "2026-03-04T05:06:07.000890Z"

    def test_none_returns_empty_string(self):
        assert format_cursor(None) == ""

    def test_lexical_ordering_matches_chronological(self):
        earlier = format_cursor(
            Timestamp.from_datetime(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))
        )
        later = format_cursor(
            Timestamp.from_datetime(datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc))
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


def _posted_with_message(message: Message) -> RoomEvent:
    env = _envelope(
        "message_posted", rt.RealtimeMessagePostedEvent(room_id="R-signal", message_event_id="E1")
    )
    return parse_envelope(env, message=message)


class TestLinkPreviewSocialPost:
    def test_absent_social_post_stays_none(self):
        message = Message(
            id="E1", room_id="R1", link_preview=ProtoLinkPreview(url="https://example.com")
        )
        room_event = _posted_with_message(message)
        assert room_event.event.link_preview.url == "https://example.com"
        assert room_event.event.link_preview.social_post is None

    def test_no_link_preview_stays_none(self):
        message = Message(id="E1", room_id="R1")
        room_event = _posted_with_message(message)
        assert room_event.event.link_preview is None

    def test_minimal_empty_social_post(self):
        message = Message(
            id="E1",
            room_id="R1",
            link_preview=ProtoLinkPreview(
                url="https://example.com",
                social_post=ProtoSocialPostPreview(provider="bluesky"),
            ),
        )
        room_event = _posted_with_message(message)
        social = room_event.event.link_preview.social_post
        assert isinstance(social, SocialPostPreview)
        assert social.provider == "bluesky"
        assert social.text == ""
        assert social.url == ""
        assert social.author is None
        assert social.images == []
        assert social.external_link is None
        assert social.content_warning is None
        assert social.published_at is None
        assert social.quoted_post is None

    def test_full_social_post(self):
        published = Timestamp.from_datetime(
            datetime.datetime(2026, 2, 3, 4, 5, 6, tzinfo=datetime.timezone.utc)
        )
        message = Message(
            id="E1",
            room_id="R1",
            link_preview=ProtoLinkPreview(
                url="https://example.com/post",
                social_post=ProtoSocialPostPreview(
                    provider="bluesky",
                    author=ProtoSocialPostAuthor(
                        display_name="Alice",
                        handle="alice.bsky.social",
                        avatar_url="https://cdn.example.com/a.png",
                        avatar_asset_id="asset-1",
                    ),
                    text="hello world",
                    published_at=published,
                    images=[
                        ProtoSocialPostImage(
                            url="https://cdn.example.com/i1.png",
                            asset_id="img-1",
                            alt="a cat",
                            width=100,
                            height=200,
                        ),
                    ],
                    external_link=ProtoSocialPostExternalLink(
                        url="https://linked.example.com",
                        title="Linked title",
                        description="Linked description",
                        image_url="https://cdn.example.com/card.png",
                        image_asset_id="card-1",
                    ),
                    content_warning="spoilers",
                    url="https://provider.example.com/post/1",
                ),
            ),
        )
        room_event = _posted_with_message(message)
        social = room_event.event.link_preview.social_post

        assert social.provider == "bluesky"
        assert social.text == "hello world"
        assert social.url == "https://provider.example.com/post/1"
        assert social.content_warning == "spoilers"
        assert social.published_at == format_cursor(published)

        assert social.author.display_name == "Alice"
        assert social.author.handle == "alice.bsky.social"
        assert social.author.avatar_url == "https://cdn.example.com/a.png"
        assert social.author.avatar_asset_id == "asset-1"

        assert len(social.images) == 1
        assert social.images[0].url == "https://cdn.example.com/i1.png"
        assert social.images[0].asset_id == "img-1"
        assert social.images[0].alt == "a cat"
        assert social.images[0].width == 100
        assert social.images[0].height == 200

        assert social.external_link.url == "https://linked.example.com"
        assert social.external_link.title == "Linked title"
        assert social.external_link.description == "Linked description"
        assert social.external_link.image_url == "https://cdn.example.com/card.png"
        assert social.external_link.image_asset_id == "card-1"

        assert social.quoted_post is None

    def test_content_warning_absent(self):
        message = Message(
            id="E1",
            room_id="R1",
            link_preview=ProtoLinkPreview(
                url="https://example.com",
                social_post=ProtoSocialPostPreview(provider="bluesky", text="no warning here"),
            ),
        )
        room_event = _posted_with_message(message)
        assert room_event.event.link_preview.social_post.content_warning is None

    def test_image_without_alt_and_dimensions(self):
        message = Message(
            id="E1",
            room_id="R1",
            link_preview=ProtoLinkPreview(
                url="https://example.com",
                social_post=ProtoSocialPostPreview(
                    provider="bluesky",
                    images=[ProtoSocialPostImage(url="https://cdn.example.com/i.png", asset_id="img-1")],
                ),
            ),
        )
        room_event = _posted_with_message(message)
        image = room_event.event.link_preview.social_post.images[0]
        assert image.alt is None
        assert image.width is None
        assert image.height is None

    def test_explicitly_empty_optionals_are_distinct_from_absent(self):
        # An author who wrote empty alt text, and a zero-size image, are not
        # the same as a server that sent no alt text or dimensions at all.
        message = Message(
            id="E1",
            room_id="R1",
            link_preview=ProtoLinkPreview(
                url="https://example.com",
                social_post=ProtoSocialPostPreview(
                    provider="bluesky",
                    content_warning="",
                    images=[
                        ProtoSocialPostImage(
                            url="https://cdn.example.com/i.png",
                            asset_id="img-1",
                            alt="",
                            width=0,
                            height=0,
                        )
                    ],
                ),
            ),
        )
        social = _posted_with_message(message).event.link_preview.social_post
        assert social.content_warning == ""
        image = social.images[0]
        assert image.alt == ""
        assert image.width == 0
        assert image.height == 0

    def test_quoted_post_one_level(self):
        message = Message(
            id="E1",
            room_id="R1",
            link_preview=ProtoLinkPreview(
                url="https://example.com",
                social_post=ProtoSocialPostPreview(
                    provider="bluesky",
                    text="quoting",
                    quoted_post=ProtoSocialPostPreview(
                        provider="bluesky",
                        text="the original",
                        url="https://provider.example.com/post/original",
                    ),
                ),
            ),
        )
        room_event = _posted_with_message(message)
        social = room_event.event.link_preview.social_post

        assert social.text == "quoting"
        assert social.quoted_post is not None
        assert isinstance(social.quoted_post, SocialPostPreview)
        assert social.quoted_post.text == "the original"
        assert social.quoted_post.url == "https://provider.example.com/post/original"
        # The server omits quotes nested inside a quote, so the mapper never
        # has to recurse past one level in practice.
        assert social.quoted_post.quoted_post is None


class TestMessageDeletedAt:
    """Message.deleted_at (field 21) marks when content was deleted through
    retraction or crypto-shredding. Reachable wherever hydrate.py fetches a
    current Message: message_posted and message_edited. message_retracted's
    signal carries no timestamp and hydrate.py never fetches a Message for
    it, so there is nothing to surface on MessageDeletedEvent."""

    def test_message_posted_surfaces_deleted_at(self):
        deleted = Timestamp.from_datetime(
            datetime.datetime(2026, 5, 6, 7, 8, 9, tzinfo=datetime.timezone.utc)
        )
        message = Message(id="E1", room_id="R1", body="hello", deleted_at=deleted)
        room_event = _posted_with_message(message)
        assert room_event.event.deleted_at == format_cursor(deleted)

    def test_message_posted_deleted_at_absent(self):
        message = Message(id="E1", room_id="R1", body="hello")
        room_event = _posted_with_message(message)
        assert room_event.event.deleted_at is None

    def test_message_edited_surfaces_deleted_at(self):
        deleted = Timestamp.from_datetime(
            datetime.datetime(2026, 5, 6, 7, 8, 9, tzinfo=datetime.timezone.utc)
        )
        env = _envelope(
            "message_edited", rt.RealtimeMessageEditedEvent(room_id="R1", message_event_id="E9")
        )
        message = Message(id="E9", room_id="R1", deleted_at=deleted)

        room_event = parse_envelope(env, message=message)

        assert isinstance(room_event.event, MessageUpdatedEvent)
        assert room_event.event.deleted_at == format_cursor(deleted)

    def test_message_edited_deleted_at_absent(self):
        env = _envelope(
            "message_edited", rt.RealtimeMessageEditedEvent(room_id="R1", message_event_id="E9")
        )
        message = Message(id="E9", room_id="R1", body="still here")

        room_event = parse_envelope(env, message=message)

        assert room_event.event.deleted_at is None
