"""Tests for types.py — event parsing and unknown field handling."""

import pytest
from chatto_bot.types import (
    MessagePostedEvent,
    ReactionAddedEvent,
    RoomArchivedEvent,
    RoomEvent,
    ServerUpdatedEvent,
    UnknownEvent,
    event_name,
    parse_my_event,
    parse_room_event,
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

    def test_unknown_typename_returns_placeholder(self):
        event = _parse_inner_event(
            {"__typename": "FutureEvent", "newField": "value"}
        )
        assert isinstance(event, UnknownEvent)
        assert event.typename == "FutureEvent"
        assert event.raw["newField"] == "value"
        assert event_name(event) == "unknown"

    def test_missing_typename_raises(self):
        with pytest.raises(ValueError, match="missing __typename"):
            _parse_inner_event({"roomId": "R1"})

    def test_room_archived_event(self):
        event = _parse_inner_event({"__typename": "RoomArchivedEvent", "roomId": "R1"})
        assert isinstance(event, RoomArchivedEvent)
        assert event.room_id == "R1"
        assert event_name(event) == "room_archived"

    def test_unknown_fields_are_ignored(self):
        """New server-side fields should not crash parsing."""
        data = {
            "__typename": "ReactionAddedEvent",
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


class TestParseRoomEvent:
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
        se = parse_room_event(data)
        assert isinstance(se, RoomEvent)
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
        se = parse_room_event(data)
        assert se.actor is None


class TestParseServerWideEvent:
    def test_server_updated(self):
        wrapper = parse_my_event(
            {
                "actorId": "U1",
                "event": {
                    "__typename": "ServerUpdatedEvent",
                    "name": "New name",
                    "description": "desc",
                },
            }
        )
        assert isinstance(wrapper, RoomEvent)
        assert wrapper.actor_id == "U1"
        assert wrapper.id == ""
        assert wrapper.created_at == ""
        assert wrapper.actor is None
        assert isinstance(wrapper.event, ServerUpdatedEvent)
        assert wrapper.event.name == "New name"


class TestEventName:
    def test_known_types(self):
        event = MessagePostedEvent(room_id="R1")
        assert event_name(event) == "message_posted"

        event = ReactionAddedEvent(
            room_id="R1", message_event_id="E1", emoji="heart"
        )
        assert event_name(event) == "reaction_added"

    def test_unknown_type(self):
        assert event_name("not an event") == "unknown"
