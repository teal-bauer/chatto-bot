"""Realtime WebSocket client for Chatto's binary-protobuf ``/api/realtime`` stream.

Replaces ``subscription.py`` (the old ``graphql-transport-ws`` JSON protocol).
The wire protocol changed but the operational shape is the same: connect,
handshake, stream events, reconnect with exponential backoff on failure. See
``chatto/realtime/v1/realtime.proto`` for the frame definitions and
``cli/internal/http_server/realtime.go`` for how the server drives them.

Handshake: connect with **no** WebSocket subprotocol, send
``RealtimeClientFrame(hello=...)`` promptly (the server has a 10s handshake
timeout), receive ``RealtimeServerHello``, send ``subscribe_events``, receive
``RealtimeSubscribed``, then stream ``RealtimeEventEnvelope`` frames.

Liveness: the ``websockets`` library already answers the RFC6455
transport-level ping/pong automatically (unchanged from ``subscription.py``,
which relied on the same default). On top of that, the application-level
``heartbeat`` frame the server sends periodically is treated as a liveness
signal: if neither an event nor a heartbeat arrives within a few multiples of
``heartbeat_interval_seconds`` (from the server's hello), the connection is
considered stalled and torn down so the reconnect loop can retry. The
protocol's application-level ``ping``/``pong`` messages are client-initiated
only (see ``RealtimeClientFrame.ping`` / ``RealtimeServerFrame.pong`` in the
proto) -- there is no server-to-client ``ping`` for us to reply to, so no
periodic ping sender is implemented here; ``pong`` frames are accepted and
logged if they ever arrive.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable, NoReturn

import asyncio
import websockets
from connectrpc.code import Code
from protobuf import Oneof

from ._pb.chatto.realtime.v1 import realtime_pb as rt
from .client import Unauthenticated

if TYPE_CHECKING:
    from .transport import Transport

logger = logging.getLogger(__name__)

# Protocol version this client speaks. The server accepts 0 (unspecified) or
# this exact value; anything else is a fatal `unsupported_protocol` error.
PROTOCOL_VERSION = 1

# Error codes the server currently sends during the hello/subscribe
# handshake that indicate a bad or revoked credential, as opposed to a
# protocol-level problem. See `realtimeAuthenticatedUser` in
# cli/internal/http_server/realtime.go. Kept as a set (not a single string)
# since the server may grow more auth-flavored codes over time.
_AUTH_ERROR_CODES = {"authentication_required"}

# Fallback assumed heartbeat cadence (seconds) if the server's hello somehow
# reports 0. The server currently always sets this from
# `core.MyEventsHeartbeatInterval`, so this is a defensive default only.
_DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30

# Multiplier applied to the (server-reported or default) heartbeat interval
# to get the "no frames at all in this long means the connection is dead"
# watchdog timeout. Generous headroom since heartbeat cadence is described as
# "approximate" by the proto.
_STALL_MULTIPLIER = 3.0

# Client-side cap on how long we wait for each handshake reply. Slightly
# above the server's own `realtimeHandshakeTimeout` (10s) so the server's
# timeout fires first in the normal case and we get its error/close frame
# rather than timing out first ourselves.
_HANDSHAKE_TIMEOUT_SECONDS = 12.0

# A connection that survives this long counts as "healthy": reset backoff so
# a later disconnect reconnects fast instead of inheriting a long wait from
# an earlier outage. Mirrors subscription.py's HEALTHY_THRESHOLD.
_HEALTHY_THRESHOLD_SECONDS = 30.0
_MAX_BACKOFF_SECONDS = 60.0


class RealtimeStopped(Exception):
    """The server told us not to reconnect, for a non-auth reason.

    Raised for `RealtimeClose(reconnect=false)` and for fatal `RealtimeError`
    frames whose code isn't one of the known auth-failure codes (those raise
    `Unauthenticated` instead). Callers (bot.py) should treat this as a
    terminal condition for the realtime stream rather than something to
    retry automatically.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}" if code else message)
        self.code = code
        self.message = message


class _Retry(Exception):
    """Internal signal: tear down this connection and reconnect.

    Used for `RealtimeClose(reconnect=true)` so its `retry_after_ms` can
    override the exponential backoff for exactly one attempt.
    """

    def __init__(self, delay_seconds: float | None = None) -> None:
        super().__init__("retry")
        self.delay_seconds = delay_seconds


def _raise_for_error(error: rt.RealtimeError) -> NoReturn:
    logger.warning(
        "Realtime error: code=%s message=%s fatal=%s",
        error.code,
        error.message,
        error.fatal,
    )
    if error.code in _AUTH_ERROR_CODES:
        raise Unauthenticated(Code.UNAUTHENTICATED, error.message or error.code)
    if error.fatal:
        raise RealtimeStopped(error.code, error.message)
    # No currently-emitted server error is non-fatal, but the proto allows
    # it in principle. Treat it as transient: tear down and reconnect.
    raise _Retry()


def _raise_for_close(close: rt.RealtimeClose) -> NoReturn:
    logger.info(
        "Realtime close: code=%s message=%s reconnect=%s retry_after_ms=%s",
        close.code,
        close.message,
        close.reconnect,
        close.retry_after_ms,
    )
    if not close.reconnect:
        raise RealtimeStopped(close.code, close.message)
    delay = (close.retry_after_ms / 1000.0) if close.retry_after_ms else None
    raise _Retry(delay)


class Realtime:
    """Manages the bot's connection to Chatto's realtime WebSocket.

    One `Realtime` per bot process. `run()` connects, performs the
    hello/subscribe handshake, and streams `RealtimeEventEnvelope` frames to
    `on_envelope` until cancelled, reconnecting on transient failures with
    1s-to-60s exponential backoff (reset after any connection that stayed up
    for at least `_HEALTHY_THRESHOLD_SECONDS`).
    """

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        """Ask `run()` to exit instead of reconnecting after this attempt.

        Not part of the Agent-3 interface contract's minimal surface, but
        needed for the bot to shut down the realtime task cleanly (mirrors
        `SubscriptionManager.stop()` in the code this replaces). Safe to call
        from another task; takes effect the next time `run()`'s backoff-wait
        or top-of-loop check runs.
        """
        self._stop_event.set()

    async def run(
        self,
        on_envelope: Callable[[rt.RealtimeEventEnvelope], Awaitable[None]],
        on_reconnect: Callable[[], Awaitable[None]],
    ) -> None:
        """Keep the realtime connection alive, dispatching events forever.

        `on_envelope` is called once per `RealtimeEventEnvelope` (never for
        `heartbeat`, which is liveness-only, not an event). `on_reconnect` is
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
        on_envelope: Callable[[rt.RealtimeEventEnvelope], Awaitable[None]],
        on_reconnect: Callable[[], Awaitable[None]],
    ) -> None:
        """Run a single WebSocket connection through handshake and event loop."""
        token = self.transport.token
        headers = dict(self.transport.headers())
        url = self.transport.ws_url

        logger.info("Connecting realtime stream")

        # No Sec-WebSocket-Protocol offered: this protocol is plain binary
        # protobuf frames over a bare WebSocket, not a subprotocol.
        async with websockets.connect(url, additional_headers=headers) as ws:
            await self._send(
                ws,
                rt.RealtimeClientFrame(
                    frame=Oneof(
                        field="hello",
                        value=rt.RealtimeClientHello(
                            protocol_version=PROTOCOL_VERSION, bearer_token=token
                        ),
                    )
                ),
            )

            hello = await self._expect(ws, "hello", timeout=_HANDSHAKE_TIMEOUT_SECONDS)
            logger.info(
                "Realtime server hello: version=%s heartbeat=%ss capabilities=%s",
                hello.server_version,
                hello.heartbeat_interval_seconds,
                hello.capabilities,
            )

            await self._send(
                ws,
                rt.RealtimeClientFrame(
                    frame=Oneof(
                        field="subscribe_events", value=rt.RealtimeSubscribeEvents()
                    )
                ),
            )
            await self._expect(ws, "subscribed", timeout=_HANDSHAKE_TIMEOUT_SECONDS)
            logger.info("Realtime stream subscribed")

            # Run catch-up for this (re)subscribe before dispatching new
            # live events, per the interface contract. A catch-up failure
            # shouldn't tear down an otherwise-healthy connection.
            try:
                await on_reconnect()
            except Exception:
                logger.exception("Realtime on_reconnect (catch-up) failed")

            heartbeat_interval = (
                hello.heartbeat_interval_seconds or _DEFAULT_HEARTBEAT_INTERVAL_SECONDS
            )
            stall_timeout = heartbeat_interval * _STALL_MULTIPLIER

            while True:
                try:
                    frame = await asyncio.wait_for(
                        self._recv_frame(ws), timeout=stall_timeout
                    )
                except asyncio.TimeoutError as exc:
                    raise RuntimeError(
                        "Realtime connection stalled: no frames received"
                    ) from exc

                which = frame.frame
                if which is None:
                    logger.debug("Realtime frame with no payload set")
                    continue

                if which.field == "event":
                    try:
                        await on_envelope(which.value)
                    except (Unauthenticated, RealtimeStopped):
                        # These carry a decision for the supervisor (relogin
                        # + reconnect, or stop reconnecting entirely) -- they
                        # must tear this connection down, not get logged and
                        # dropped like an ordinary handler error.
                        raise
                    except Exception:
                        logger.exception("Error dispatching realtime event")
                elif which.field == "heartbeat":
                    # Liveness only -- never surfaced to on_envelope.
                    logger.debug("Realtime heartbeat %s", which.value.id)
                elif which.field == "pong":
                    # We don't currently send `ping`, but accept it if a
                    # future caller does.
                    logger.debug("Realtime pong %s", which.value.nonce)
                elif which.field == "error":
                    _raise_for_error(which.value)
                elif which.field == "close":
                    _raise_for_close(which.value)
                    return  # unreachable: _raise_for_close never returns
                else:
                    logger.warning(
                        "Unexpected realtime frame after subscribe: %s", which.field
                    )

    async def _expect(
        self, ws: websockets.ClientConnection, field: str, *, timeout: float
    ):
        """Receive one frame and return its payload if it matches `field`.

        Handles `error`/`close` frames arriving instead (raising via
        `_raise_for_error`/`_raise_for_close`) so handshake failures produce
        the same typed exceptions as mid-stream ones.
        """
        frame = await asyncio.wait_for(self._recv_frame(ws), timeout=timeout)
        which = frame.frame
        if which is None:
            raise RuntimeError(f"Realtime handshake: expected {field}, got empty frame")
        if which.field == field:
            return which.value
        if which.field == "error":
            _raise_for_error(which.value)
        if which.field == "close":
            _raise_for_close(which.value)
        raise RuntimeError(f"Realtime handshake: expected {field}, got {which.field}")

    @staticmethod
    async def _recv_frame(ws: websockets.ClientConnection) -> rt.RealtimeServerFrame:
        raw = await ws.recv()
        if isinstance(raw, str):
            raise RuntimeError(
                "Realtime protocol violation: received text frame, expected binary"
            )
        return rt.RealtimeServerFrame.from_binary(raw)

    @staticmethod
    async def _send(ws: websockets.ClientConnection, frame: rt.RealtimeClientFrame) -> None:
        await ws.send(frame.to_binary())
