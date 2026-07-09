"""Realtime supervisor built on chattolib's ``RealtimeConnection``.

chattolib speaks the wire protocol (handshake, frame decoding, oneof
unwrapping -- see ``chattolib.realtime``) but deliberately has no
reconnect/backoff, no reconnect catch-up hook, and no stall watchdog: "Those
stay chatto-bot's job" per the interface contract. This module supplies all
three, exactly as the old hand-rolled websockets client did -- only the
innermost connect/recv loop changed.

Liveness: chattolib's ``RealtimeConnection`` already answers the RFC6455
transport-level ping/pong automatically (via the ``websockets`` library) and
treats the application-level ``heartbeat`` frame as liveness-only, never
surfacing it as an event. On top of that, this module still watches the wall
clock: if neither an event nor a heartbeat arrives within a few multiples of
``heartbeat_interval_seconds`` (from the server's hello), the connection is
considered stalled and torn down so the reconnect loop can retry.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable, NoReturn

import asyncio

from chattolib.realtime import (
    ChattoRealtimeCloseError,
    ChattoRealtimeError,
    RealtimeConnection,
)
from .client import Unauthenticated

if TYPE_CHECKING:
    from chattolib._pb.chatto.realtime.v1.realtime_pb2 import RealtimeEventEnvelope

    from .transport import Transport

logger = logging.getLogger(__name__)

# Error codes the server sends during the hello/subscribe handshake that
# indicate a bad or revoked credential, as opposed to a protocol-level
# problem. Kept as a set (not a single string) since the server may grow more
# auth-flavored codes over time.
_AUTH_ERROR_CODES = {"authentication_required"}

# Fallback assumed heartbeat cadence (seconds) if the server's hello somehow
# reports 0.
_DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30

# Multiplier applied to the (server-reported or default) heartbeat interval
# to get the "no frames at all in this long means the connection is dead"
# watchdog timeout. Generous headroom since heartbeat cadence is described as
# "approximate" by the proto.
_STALL_MULTIPLIER = 3.0

# Client-side cap on how long we wait for chattolib's handshake (its own
# ``connect()`` has no built-in timeout).
_HANDSHAKE_TIMEOUT_SECONDS = 12.0

# A connection that survives this long counts as "healthy": reset backoff so
# a later disconnect reconnects fast instead of inheriting a long wait from
# an earlier outage.
_HEALTHY_THRESHOLD_SECONDS = 30.0
_MAX_BACKOFF_SECONDS = 60.0


class RealtimeStopped(Exception):
    """The server told us not to reconnect, for a non-auth reason.

    Raised for a close frame with ``reconnect=False`` and for fatal error
    frames whose code isn't one of the known auth-failure codes (those raise
    ``Unauthenticated`` instead). Callers (bot.py) should treat this as a
    terminal condition for the realtime stream rather than something to
    retry automatically.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}" if code else message)
        self.code = code
        self.message = message


class _Retry(Exception):
    """Internal signal: tear down this connection and reconnect.

    Used for a close frame with ``reconnect=True`` so its ``retry_after_ms``
    can override the exponential backoff for exactly one attempt.
    """

    def __init__(self, delay_seconds: float | None = None) -> None:
        super().__init__("retry")
        self.delay_seconds = delay_seconds


def _raise_for_error(exc: ChattoRealtimeError) -> NoReturn:
    logger.warning(
        "Realtime error: code=%s message=%s fatal=%s", exc.code, exc.message, exc.fatal
    )
    if exc.code in _AUTH_ERROR_CODES:
        raise Unauthenticated("unauthenticated", exc.message or exc.code) from exc
    if exc.fatal:
        raise RealtimeStopped(exc.code, exc.message) from exc
    # No currently-emitted server error is non-fatal, but the proto allows
    # it in principle. Treat it as transient: tear down and reconnect.
    raise _Retry() from exc


def _raise_for_close(exc: ChattoRealtimeCloseError) -> NoReturn:
    logger.info(
        "Realtime close: code=%s message=%s reconnect=%s retry_after_ms=%s",
        exc.code,
        exc.message,
        exc.reconnect,
        exc.retry_after_ms,
    )
    if not exc.reconnect:
        raise RealtimeStopped(exc.code, exc.message) from exc
    delay = (exc.retry_after_ms / 1000.0) if exc.retry_after_ms else None
    raise _Retry(delay) from exc


class Realtime:
    """Manages the bot's connection to Chatto's realtime WebSocket.

    One `Realtime` per bot process. `run()` connects, performs the
    hello/subscribe handshake (via chattolib), and streams envelopes to
    `on_envelope` until cancelled, reconnecting on transient failures with
    1s-to-60s exponential backoff (reset after any connection that stayed up
    for at least `_HEALTHY_THRESHOLD_SECONDS`).
    """

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        """Ask `run()` to exit instead of reconnecting after this attempt.

        Needed for the bot to shut down the realtime task cleanly. Safe to
        call from another task; takes effect the next time `run()`'s
        backoff-wait or top-of-loop check runs.
        """
        self._stop_event.set()

    async def run(
        self,
        on_envelope: Callable[[RealtimeEventEnvelope], Awaitable[None]],
        on_reconnect: Callable[[], Awaitable[None]],
    ) -> None:
        """Keep the realtime connection alive, dispatching events forever.

        `on_envelope` is called once per event envelope (never for a
        heartbeat, which is liveness-only, not an event). `on_reconnect` is
        called after every successful (re)subscribe -- including the very
        first one -- so the bot can run its bounded catch-up.

        Raises `Unauthenticated` (from `chatto_bot.client`) when the server
        rejects the bearer token during handshake, so the caller can
        re-login and call `run()` again. Raises `RealtimeStopped` when the
        server says not to reconnect for a non-auth reason. Returns normally
        only after `stop()` has been called.
        """
        backoff = 1.0
        loop = asyncio.get_running_loop()

        while not self._stop_event.is_set():
            t0 = loop.time()
            retry_delay: float | None = None
            try:
                await self._run_connection(on_envelope, on_reconnect)
                ended = "ended"
            except asyncio.CancelledError:
                logger.info("Realtime connection cancelled")
                return
            except (Unauthenticated, RealtimeStopped):
                raise
            except _Retry as retry:
                ended = "closed"
                retry_delay = retry.delay_seconds
            except Exception:
                logger.exception("Realtime connection error")
                ended = "errored"

            if self._stop_event.is_set():
                return

            if retry_delay is not None:
                backoff = retry_delay
            elif loop.time() - t0 >= _HEALTHY_THRESHOLD_SECONDS:
                backoff = 1.0

            logger.warning(
                "Realtime connection %s, reconnecting in %.1fs", ended, backoff
            )
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                return  # stop() fired during the backoff sleep
            except asyncio.TimeoutError:
                pass
            if retry_delay is None:
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

    async def _run_connection(
        self,
        on_envelope: Callable[[RealtimeEventEnvelope], Awaitable[None]],
        on_reconnect: Callable[[], Awaitable[None]],
    ) -> None:
        """Run a single realtime connection through handshake and event loop."""
        logger.info("Connecting realtime stream")
        conn = RealtimeConnection(self.transport.chatto_client)
        try:
            await self._connect(conn)

            hello = conn.server_hello
            heartbeat_interval = (
                (hello.heartbeat_interval_seconds if hello is not None else 0)
                or _DEFAULT_HEARTBEAT_INTERVAL_SECONDS
            )
            if hello is not None:
                logger.info(
                    "Realtime server hello: version=%s heartbeat=%ss capabilities=%s",
                    hello.server_version,
                    hello.heartbeat_interval_seconds,
                    hello.capabilities,
                )
            logger.info("Realtime stream subscribed")

            # Run catch-up for this (re)subscribe before dispatching new
            # live events, per the interface contract. A catch-up failure
            # shouldn't tear down an otherwise-healthy connection.
            try:
                await on_reconnect()
            except Exception:
                logger.exception("Realtime on_reconnect (catch-up) failed")

            stall_timeout = heartbeat_interval * _STALL_MULTIPLIER
            await self._stream_events(conn, on_envelope, stall_timeout)
        finally:
            await conn.close()

    async def _connect(self, conn: RealtimeConnection) -> None:
        try:
            await asyncio.wait_for(conn.connect(), timeout=_HANDSHAKE_TIMEOUT_SECONDS)
        except ChattoRealtimeCloseError as exc:
            _raise_for_close(exc)
        except ChattoRealtimeError as exc:
            _raise_for_error(exc)

    async def _stream_events(
        self,
        conn: RealtimeConnection,
        on_envelope: Callable[[RealtimeEventEnvelope], Awaitable[None]],
        stall_timeout: float,
    ) -> None:
        events = conn.events()
        while True:
            try:
                event = await asyncio.wait_for(events.__anext__(), timeout=stall_timeout)
            except asyncio.TimeoutError as exc:
                raise RuntimeError(
                    "Realtime connection stalled: no frames received"
                ) from exc
            except StopAsyncIteration:
                return
            except ChattoRealtimeCloseError as exc:
                _raise_for_close(exc)
            except ChattoRealtimeError as exc:
                _raise_for_error(exc)

            try:
                await on_envelope(event.raw)
            except (Unauthenticated, RealtimeStopped):
                # These carry a decision for the supervisor (relogin +
                # reconnect, or stop reconnecting entirely) -- they must
                # tear this connection down, not get logged and dropped
                # like an ordinary handler error.
                raise
            except Exception:
                logger.exception("Error dispatching realtime event")
