"""Tests for types.py — event parsing and unknown field handling."""

import pytest
from chatto_bot.types import (
    MessagePostedEvent,
    ReactionAddedEvent,
    SpaceEvent,
    User,
    event_name,
    parse_space_event,
    _parse_inner_event,
)


class TestParseInnerEvent:
    def test_message_posted(self):
        data = {
            "__typename": "MessagePostedEvent",
            "roomId": "R1",
            "body": "hello",
        }
        event = _parse_inner_event(data)
        assert isinstance(event, MessagePostedEvent)
        assert event.room_id == "R1"
        assert event.body == "hello"

    def test_unknown_typename_raises(self):
        with pytest.raises(ValueError, match="Unknown event type"):
            _parse_inner_event({"__typename": "FutureEvent"})

    def test_missing_typename_raises(self):
        with pytest.raises(ValueError, match="missing __typename"):
            _parse_inner_event({"spaceId": "S1"})

    def test_unknown_fields_are_ignored(self):
        """New server-side fields should not crash parsing."""
        data = {
            "__typename": "ReactionAddedEvent",
            "spaceId": "S1",
            "roomId": "R1",
            "messageEventId": "E1",
            "emoji": "heart",
            "futureField": "some value",
            "anotherNewField": 42,
        }
        event = _parse_inner_event(data)
        assert isinstance(event, ReactionAddedEvent)
        assert event.emoji == "heart"
        assert not hasattr(event, "future_field")

    def test_attachments_parsed(self):
        data = {
            "__typename": "MessagePostedEvent",
            "roomId": "R1",
            "body": "check this",
            "attachments": [
                {
                    "id": "A1",
                    "filename": "test.png",
                    "contentType": "image/png",
                    "width": 100,
                    "height": 100,
                    "url": "https://example.com/test.png",
                }
            ],
        }
        event = _parse_inner_event(data)
        assert len(event.attachments) == 1
        assert event.attachments[0].filename == "test.png"


class TestParseSpaceEvent:
    def test_full_event(self):
        data = {
            "id": "E1",
            "createdAt": "2026-01-01T00:00:00Z",
            "actorId": "U1",
            "actor": {
                "id": "U1",
                "login": "alice",
                "displayName": "Alice",
            },
            "event": {
                "__typename": "MessagePostedEvent",
                "roomId": "R1",
                "body": "hi",
            },
        }
        se = parse_space_event(data, space_id="S1")
        assert isinstance(se, SpaceEvent)
        assert se.space_id == "S1"
        assert se.actor.login == "alice"
        assert se.event.body == "hi"

    def test_no_actor(self):
        data = {
            "id": "E1",
            "createdAt": "2026-01-01T00:00:00Z",
            "actorId": "system",
            "actor": None,
            "event": {
                "__typename": "MessagePostedEvent",
                "roomId": "R1",
                "body": "system message",
            },
        }
        se = parse_space_event(data, space_id="S1")
        assert se.actor is None


class TestEventName:
    def test_known_types(self):
        event = MessagePostedEvent(room_id="R1")
        assert event_name(event) == "message_posted"

        event = ReactionAddedEvent(
            space_id="S1", room_id="R1", message_event_id="E1", emoji="heart"
        )
        assert event_name(event) == "reaction_added"

    def test_unknown_type(self):
        assert event_name("not an event") == "unknown"
