"""Tests for context.py -- reply/reply_in_thread threading semantics.

D5: the design's #2 named risk is that ``Context.reply``/``reply_in_thread``
must set ``thread_root_event_id`` (not ``in_reply_to``) on the
``CreateMessageRequest``/``Client.create_message`` call -- ``in_reply_to`` is
attribution only, per the design doc's "Threading" note. This had zero
coverage before.
"""

from __future__ import annotations

import pytest

from conftest import make_ctx


class TestReplyThreading:
    @pytest.mark.asyncio
    async def test_reply_outside_thread_sets_neither(self, bot):
        """A plain reply to a top-level message doesn't invent threading."""
        ctx = make_ctx(bot, event_id="E1", thread_root_event_id=None)

        await ctx.reply("hi")

        _, kwargs = bot.client.create_message.call_args
        assert kwargs.get("thread_root_event_id") is None
        assert kwargs.get("in_reply_to") is None

    @pytest.mark.asyncio
    async def test_reply_inside_thread_sets_thread_root_not_in_reply_to(self, bot):
        """Replying from within a thread keeps the reply in that thread via
        thread_root_event_id -- not in_reply_to, which is attribution only."""
        ctx = make_ctx(bot, event_id="E2", thread_root_event_id="Eroot")

        await ctx.reply("hi")

        _, kwargs = bot.client.create_message.call_args
        assert kwargs.get("thread_root_event_id") == "Eroot"
        assert kwargs.get("in_reply_to") is None

    @pytest.mark.asyncio
    async def test_reply_respects_explicit_thread_root_override(self, bot):
        ctx = make_ctx(bot, event_id="E2", thread_root_event_id="Eroot")

        await ctx.reply("hi", thread_root_event_id="Eother")

        _, kwargs = bot.client.create_message.call_args
        assert kwargs.get("thread_root_event_id") == "Eother"

    @pytest.mark.asyncio
    async def test_reply_in_thread_starts_new_thread_at_triggering_message(self, bot):
        """Outside a thread, reply_in_thread roots a new thread at the
        message that triggered the handler -- via thread_root_event_id, not
        in_reply_to."""
        ctx = make_ctx(bot, event_id="E1", thread_root_event_id=None)

        await ctx.reply_in_thread("hi")

        _, kwargs = bot.client.create_message.call_args
        assert kwargs.get("thread_root_event_id") == "E1"
        assert kwargs.get("in_reply_to") is None

    @pytest.mark.asyncio
    async def test_reply_in_thread_uses_existing_thread_root(self, bot):
        ctx = make_ctx(bot, event_id="E2", thread_root_event_id="Eroot")

        await ctx.reply_in_thread("hi")

        _, kwargs = bot.client.create_message.call_args
        assert kwargs.get("thread_root_event_id") == "Eroot"
        assert kwargs.get("in_reply_to") is None
