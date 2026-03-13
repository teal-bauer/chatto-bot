"""Tests for event.py — EventHandler filtering."""

import pytest
from unittest.mock import AsyncMock

from chatto_bot.event import EventHandler, on_event
from conftest import make_ctx, make_event


class TestEventHandlerMatches:
    def setup_method(self):
        self.handler = EventHandler(
            event_type="message_posted",
            callback=AsyncMock(),
            filters={},
        )

    def test_no_filters_matches_all(self, bot):
        ctx = make_ctx(bot)
        assert self.handler.matches(ctx)

    def test_room_filter_match(self, bot):
        self.handler.filters = {"room": "R1"}
        ctx = make_ctx(bot, room_id="R1")
        assert self.handler.matches(ctx)

    def test_room_filter_mismatch(self, bot):
        self.handler.filters = {"room": "R1"}
        ctx = make_ctx(bot, room_id="R2")
        assert not self.handler.matches(ctx)

    def test_space_filter_match(self, bot):
        self.handler.filters = {"space": "S1"}
        ctx = make_ctx(bot, space_id="S1")
        assert self.handler.matches(ctx)

    def test_space_filter_mismatch(self, bot):
        self.handler.filters = {"space": "S1"}
        ctx = make_ctx(bot, space_id="S2")
        assert not self.handler.matches(ctx)

    def test_actor_filter_match(self, bot):
        self.handler.filters = {"actor": "Uuser"}
        ctx = make_ctx(bot, actor_id="Uuser")
        assert self.handler.matches(ctx)

    def test_actor_filter_mismatch(self, bot):
        self.handler.filters = {"actor": "Uuser"}
        ctx = make_ctx(bot, actor_id="Uother")
        assert not self.handler.matches(ctx)

    def test_actor_filter_rejects_none_actor(self, bot):
        """Actor filter must NOT pass when actor is None."""
        self.handler.filters = {"actor": "Uuser"}
        event = make_event()
        event.actor = None
        ctx = make_ctx(bot, event=event)
        assert not self.handler.matches(ctx)


class TestOnEventDecorator:
    def test_creates_handler(self):
        @on_event("message_posted", room="R1")
        async def handler(ctx):
            pass

        assert isinstance(handler, EventHandler)
        assert handler.event_type == "message_posted"
        assert handler.filters == {"room": "R1"}

    def test_no_filters(self):
        @on_event("reaction_added")
        async def handler(ctx):
            pass

        assert handler.filters == {}
