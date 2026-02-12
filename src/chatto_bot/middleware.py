"""Middleware chain for before/after hooks."""

from __future__ import annotations

from typing import Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import Context

MiddlewareFunc = Callable[["Context", Callable[[], Awaitable[None]]], Awaitable[None]]


class MiddlewareChain:
    """Ordered chain of middleware functions.

    Each middleware receives (ctx, next) where next() calls the
    next middleware or the final handler.
    """

    def __init__(self) -> None:
        self._middlewares: list[MiddlewareFunc] = []

    def add(self, func: MiddlewareFunc) -> None:
        self._middlewares.append(func)

    def remove(self, func: MiddlewareFunc) -> None:
        self._middlewares.remove(func)

    async def run(self, ctx: Context, handler: Callable[[], Awaitable[None]]) -> None:
        """Execute the middleware chain, ending with the given handler."""
        if not self._middlewares:
            await handler()
            return

        async def _build_chain(index: int) -> Callable[[], Awaitable[None]]:
            if index >= len(self._middlewares):
                return handler

            middleware = self._middlewares[index]

            async def next_fn() -> None:
                next_handler = await _build_chain(index + 1)
                await next_handler()

            async def wrapped() -> None:
                await middleware(ctx, next_fn)

            return wrapped

        chain = await _build_chain(0)
        await chain()
