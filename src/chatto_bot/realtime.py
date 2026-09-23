"""Realtime WebSocket client for Chatto's binary-protobuf ``/api/realtime`` stream.

Replaces ``subscription.py`` (the old ``graphql-transport-ws`` JSON protocol).
The wire protocol changed but the operational shape is the same: connect,
handshake, stream events, reconnect with exponential backoff on failure. See
``chatto/realtime/v1/realtime.proto`` for the frame definitions and
``cli/internal/http_server/realtime.go`` for how the server drives them.

Handshake: connect with **no** WebSocket subprotocol and send one
``RealtimeSubscribe(protocol_version=4, ...)`` promptly (the server has a
10s handshake timeout); the server then streams ``RealtimeCaughtUp`` once
recovery reaches the live boundary, followed by ``RealtimeEvent`` frames.
The client never sends another application message on the socket.

Liveness: the ``websockets`` library already answers the RFC6455
transport-level ping/pong automatically. On top of that, the application-level
``heartbeat`` frame the server sends periodically is treated as a liveness
signal: if no frame at all arrives within a few multiples of the server's
heartbeat cadence, the connection is considered stalled and torn down so the
reconnect loop can retry. Protocol v4 heartbeats carry only a resume cursor,
so the cadence is assumed (the server currently heartbeats every 15s).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable, NoReturn

import asyncio
import websockets
from connectrpc.code import Code

from ._pb.chatto.realtime.v1 import realtime_pb as rt
from .client import Unauthenticated

if TYPE_CHECKING:
    from .transport import Transport

logger = logging.getLogger(__name__)

# Protocol version this client speaks. The server rejects any other value
# with a fatal `unsupported_protocol` close.
PROTOCOL_VERSION = 4

# Assumed heartbeat cadence (seconds) for stall detection: protocol v4
# heartbeats carry only a resume cursor, so the cadence cannot be read from
# the handshake. The server currently heartbeats every 15s
# (`core.MyEventsHeartbeatInterval`), so this over-estimates generously.
_DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30

# Multiplier applied to the assumed heartbeat interval to get the "no frames
# at all in this long means the connection is dead" watchdog timeout.
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

    Raised for a `RealtimeClose` with `reconnect=false` whose code is not one
    of the auth-failure codes (those raise `Unauthenticated` instead).
    Callers (bot.py) should treat this as a terminal condition for the
    realtime stream rather than something to retry automatically.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}" if code else message)
        self.code = code
        self.message = message


class _Retry(Exception):
    """Internal signal: tear down this connection and reconnect.

    Used for `RealtimeClose(reconnect=true)` so its `retry_after` can
    override the exponential backoff for exactly one attempt.
    """

    def __init__(self, delay_seconds: float | None = None) -> None:
        super().__init__("retry")
        self.delay_seconds = delay_seconds


def _raise_for_close(close: rt.RealtimeClose) -> NoReturn:
    code = close.code
    logger.info(
        "Realtime close: code=%s message=%s reconnect=%s retry_after=%ss",
        code,
        close.message,
        close.reconnect,
        close.retry_after.seconds if close.retry_after is not None else None,
    )
    # Auth-terminal codes tear down regardless of the reconnect flag: the
    # credential is dead, so reconnecting with it would only fail again.
    if code in (
        rt.RealtimeCloseCode.AUTHENTICATION_REQUIRED,
        rt.RealtimeCloseCode.SESSION_TERMINATED,
    ):
        raise Unauthenticated(Code.UNAUTHENTICATED, close.message or str(code))
    if not close.reconnect:
        raise RealtimeStopped(str(code).lower(), close.message)
    delay = None
    if close.retry_after is not None:
        delay = close.retry_after.seconds + close.retry_after.nanos / 1_000_000_000
    raise _Retry(delay)


class Realtime:
    """Manages the bot's connection to Chatto's realtime WebSocket.

    One `Realtime` per bot process. `run()` connects, sends one
    `RealtimeSubscribe`, and streams `RealtimeEvent` frames to
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
        on_envelope: Callable[[rt.RealtimeEvent], Awaitable[None]],
        on_reconnect: Callable[[], Awaitable[None]],
    ) -> None:
        """Keep the realtime connection alive, dispatching events forever.

        `on_envelope` is called once per `RealtimeEvent` (never for
        `heartbeat`, which is liveness-only, not an event). `on_reconnect` is
        called after every `caught_up` -- i.e. after every successful
        (re)subscribe, including the very first one -- so the bot can run its
        bounded catch-up.

        Raises `Unauthenticated` (from `chatto_bot.client`) when the server
        rejects the credential in a close frame, so the caller can
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
        on_envelope: Callable[[rt.RealtimeEvent], Awaitable[None]],
        on_reconnect: Callable[[], Awaitable[None]],
    ) -> None:
        """Run a single WebSocket connection: one subscribe, then the frame loop."""
        token = self.transport.token
        headers = dict(self.transport.headers())
        url = self.transport.ws_url

        logger.info("Connecting realtime stream")

        # No Sec-WebSocket-Protocol offered: this protocol is plain binary
        # protobuf frames over a bare WebSocket, not a subprotocol.
        async with websockets.connect(url, additional_headers=headers) as ws:
            await self._send(
                ws,
                rt.RealtimeSubscribe(
                    protocol_version=PROTOCOL_VERSION,
                    bearer_token=token,
                    initial_state=rt.RealtimeInitialState.LIVE_ONLY,
                ),
            )
            logger.info("Realtime stream subscribed")

            # The server sends caught_up once recovery reaches the live
            # boundary; run catch-up there, before any live event is
            # dispatched. A catch-up failure shouldn't tear down an
            # otherwise-healthy connection.
            caught_up = False
            stall_timeout = _DEFAULT_HEARTBEAT_INTERVAL_SECONDS * _STALL_MULTIPLIER

            while True:
                # caught_up is the first frame on a healthy handshake, so it
                # is bounded by the handshake timeout; afterwards the looser
                # stall deadline applies.
                timeout = stall_timeout if caught_up else _HANDSHAKE_TIMEOUT_SECONDS
                try:
                    frame = await asyncio.wait_for(
                        self._recv_frame(ws), timeout=timeout
                    )
                except asyncio.TimeoutError as exc:
                    raise RuntimeError(
                        "Realtime connection stalled: no frames received"
                    ) from exc

                which = frame.frame
                if which is None:
                    logger.debug("Realtime frame with no payload set")
                    continue

                if which.field == "caught_up":
                    if caught_up:
                        continue
                    caught_up = True
                    logger.info(
                        "Realtime caught up: recovery=%s", which.value.recovery
                    )
                    try:
                        await on_reconnect()
                    except Exception:
                        logger.exception("Realtime on_reconnect (catch-up) failed")
                elif which.field == "event":
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
                    logger.debug("Realtime heartbeat %s", which.value.cursor)
                elif which.field == "snapshot":
                    # Live-only subscribe, so no snapshot is expected;
                    # discard if one arrives.
                    logger.debug("Realtime snapshot discarded")
                elif which.field == "close":
                    _raise_for_close(which.value)
                    return  # unreachable: _raise_for_close never returns
                else:
                    logger.warning(
                        "Unexpected realtime frame after subscribe: %s", which.field
                    )

    @staticmethod
    async def _recv_frame(ws: websockets.ClientConnection) -> rt.RealtimeServerFrame:
        raw = await ws.recv()
        if isinstance(raw, str):
            raise RuntimeError(
                "Realtime protocol violation: received text frame, expected binary"
            )
        return rt.RealtimeServerFrame.from_binary(raw)

    @staticmethod
    async def _send(ws: websockets.ClientConnection, frame: rt.RealtimeSubscribe) -> None:
        await ws.send(frame.to_binary())
