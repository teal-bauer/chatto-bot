"""Tests for realtime.py's connection-level dispatch guard.

D2 regression: when the bot's ``on_envelope`` callback raises ``Unauthenticated``
(a bearer token revoked mid-run) or ``RealtimeStopped``, the guard around
``await on_envelope(which.value)`` in ``Realtime._run_connection`` must let it
propagate -- not log-and-swallow it like an ordinary handler error -- so the
connection tears down and the exception reaches the supervisor
(``Bot._run_realtime``), which reconnects (and, for ``Unauthenticated``, relogs
in first).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from connectrpc.code import Code
from protobuf import Oneof

from chatto_bot._pb.chatto.realtime.v1 import realtime_pb as rt
from chatto_bot.client import Unauthenticated
from chatto_bot.realtime import Realtime, RealtimeStopped


class _FakeWebSocketConnect:
    """Stands in for ``websockets.connect(...)`` used as an async context
    manager. Serves pre-built ``RealtimeServerFrame`` binary frames from a
    queue in order; records anything the client sends."""

    def __init__(self, frames: list[bytes]) -> None:
        self._frames = list(frames)
        self.sent: list[bytes] = []

    async def __aenter__(self) -> "_FakeWebSocketConnect":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def recv(self) -> bytes:
        if not self._frames:
            raise AssertionError("no more frames queued for the fake websocket")
        return self._frames.pop(0)

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _server_frame(field: str, payload) -> bytes:
    return rt.RealtimeServerFrame(frame=Oneof(field, payload)).to_binary()


def _handshake_frames() -> list[bytes]:
    """hello + subscribed frames, enough to get `_run_connection` past the
    handshake and into the steady-state event loop."""
    return [
        _server_frame("hello", rt.RealtimeServerHello(heartbeat_interval_seconds=30)),
        _server_frame("subscribed", rt.RealtimeSubscribed()),
    ]


def _make_realtime(monkeypatch: pytest.MonkeyPatch, frames: list[bytes]) -> Realtime:
    import chatto_bot.realtime as realtime_module

    transport = MagicMock()
    transport.token = "tok"
    transport.headers.return_value = {}
    transport.ws_url = "ws://test.example.com/api/realtime"

    fake_ws = _FakeWebSocketConnect(frames)
    monkeypatch.setattr(
        realtime_module.websockets, "connect", lambda *a, **kw: fake_ws
    )
    return Realtime(transport)


class TestDispatchGuardPropagation:
    @pytest.mark.asyncio
    async def test_unauthenticated_from_on_envelope_propagates(self, monkeypatch):
        envelope = rt.RealtimeEventEnvelope(id="E1", actor_id="U1")
        frames = _handshake_frames() + [_server_frame("event", envelope)]
        realtime = _make_realtime(monkeypatch, frames)

        on_envelope = AsyncMock(
            side_effect=Unauthenticated(Code.UNAUTHENTICATED, "token revoked")
        )
        on_reconnect = AsyncMock()

        with pytest.raises(Unauthenticated):
            await realtime._run_connection(on_envelope, on_reconnect)

        on_reconnect.assert_awaited_once()
        on_envelope.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_realtime_stopped_from_on_envelope_propagates(self, monkeypatch):
        envelope = rt.RealtimeEventEnvelope(id="E1", actor_id="U1")
        frames = _handshake_frames() + [_server_frame("event", envelope)]
        realtime = _make_realtime(monkeypatch, frames)

        on_envelope = AsyncMock(side_effect=RealtimeStopped("fatal_code", "nope"))
        on_reconnect = AsyncMock()

        with pytest.raises(RealtimeStopped):
            await realtime._run_connection(on_envelope, on_reconnect)

    @pytest.mark.asyncio
    async def test_ordinary_handler_error_is_swallowed(self, monkeypatch):
        """An unrelated bug in a bot's handler shouldn't tear the connection
        down -- only Unauthenticated/RealtimeStopped are special-cased."""
        envelope = rt.RealtimeEventEnvelope(id="E1", actor_id="U1")
        frames = _handshake_frames() + [
            _server_frame("event", envelope),
            _server_frame("event", envelope),
        ]
        realtime = _make_realtime(monkeypatch, frames)

        calls = []

        async def flaky_on_envelope(env):
            calls.append(env)
            if len(calls) == 1:
                raise RuntimeError("boom, unrelated bug")

        on_reconnect = AsyncMock()

        with pytest.raises(AssertionError):
            # Runs out of queued frames after the second (successful) event,
            # proving the loop survived the first handler's RuntimeError
            # instead of tearing the connection down.
            await realtime._run_connection(flaky_on_envelope, on_reconnect)

        assert len(calls) == 2
