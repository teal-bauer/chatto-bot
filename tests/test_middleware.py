"""Tests for middleware.py — middleware chain execution."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from chatto_bot.middleware import MiddlewareChain
from conftest import make_ctx


class TestMiddlewareChain:
    @pytest.mark.asyncio
    async def test_no_middleware(self, bot):
        chain = MiddlewareChain()
        handler = AsyncMock()
        ctx = make_ctx(bot)
        await chain.run(ctx, handler)
        handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_single_middleware(self, bot):
        chain = MiddlewareChain()
        order = []

        async def mw(ctx, next):
            order.append("before")
            await next()
            order.append("after")

        chain.add(mw)

        async def handler():
            order.append("handler")

        ctx = make_ctx(bot)
        await chain.run(ctx, handler)
        assert order == ["before", "handler", "after"]

    @pytest.mark.asyncio
    async def test_middleware_can_short_circuit(self, bot):
        chain = MiddlewareChain()

        async def blocker(ctx, next):
            pass  # doesn't call next()

        chain.add(blocker)
        handler = AsyncMock()
        ctx = make_ctx(bot)
        await chain.run(ctx, handler)
        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_middleware_order(self, bot):
        chain = MiddlewareChain()
        order = []

        async def mw1(ctx, next):
            order.append("mw1")
            await next()

        async def mw2(ctx, next):
            order.append("mw2")
            await next()

        chain.add(mw1)
        chain.add(mw2)

        async def handler():
            order.append("handler")

        ctx = make_ctx(bot)
        await chain.run(ctx, handler)
        assert order == ["mw1", "mw2", "handler"]

    def test_remove_middleware(self):
        chain = MiddlewareChain()

        async def mw(ctx, next):
            await next()

        chain.add(mw)
        assert len(chain._middlewares) == 1
        chain.remove(mw)
        assert len(chain._middlewares) == 0
