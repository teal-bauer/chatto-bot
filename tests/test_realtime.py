"""Tests for realtime.py's connection-level dispatch guard.

D2 regression: when the bot's ``on_envelope`` callback raises ``Unauthenticated``
(a bearer token revoked mid-run) or ``RealtimeStopped``, the guard around
``await on_envelope(event.raw)`` in ``Realtime._stream_events`` must let it
propagate -- not log-and-swallow it like an ordinary handler error -- so the
connection tears down and the exception reaches the supervisor
(``Bot._run_realtime``), which reconnects (and, for ``Unauthenticated``, relogs
in first).

chattolib's ``RealtimeConnection`` (the wire-protocol layer this module now
delegates to) is faked here rather than a raw WebSocket, since the actual
handshake/frame decoding is chattolib's responsibility, not chatto-bot's --
see ``chattolib.realtime`` and the module docstring in ``chatto_bot/realtime.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chatto_bot.client import Unauthenticated
from chatto_bot.realtime import Realtime, RealtimeStopped


class _FakeRealtimeEvent:
    def __init__(self, raw) -> None:
        self.raw = raw


class _FakeConnection:
    """Stands in for ``chattolib.realtime.RealtimeConnection``: serves a
    fixed list of events in order, then ends the stream (mirroring a server
    that simply stops sending -- not a close/error frame)."""

    def __init__(self, events: list) -> None:
        self._events = list(events)
        self.server_hello = MagicMock(
            heartbeat_interval_seconds=30, server_version="test", capabilities=[]
        )
        self.closed = False

    async def connect(self) -> None:
        return None

    async def events(self):
        for event in self._events:
            yield event

    async def close(self) -> None:
        self.closed = True


def _make_realtime(monkeypatch: pytest.MonkeyPatch, events: list) -> tuple[Realtime, _FakeConnection]:
    import chatto_bot.realtime as realtime_module

    transport = MagicMock()
    transport.chatto_client = MagicMock()

    fake_conn = _FakeConnection(events)
    monkeypatch.setattr(realtime_module, "RealtimeConnection", lambda client: fake_conn)
    return Realtime(transport), fake_conn


class TestDispatchGuardPropagation:
    @pytest.mark.asyncio
    async def test_unauthenticated_from_on_envelope_propagates(self, monkeypatch):
        realtime, conn = _make_realtime(
            monkeypatch, [_FakeRealtimeEvent(raw="envelope-1")]
        )

        on_envelope = AsyncMock(
            side_effect=Unauthenticated("unauthenticated", "token revoked")
        )
        on_reconnect = AsyncMock()

        with pytest.raises(Unauthenticated):
            await realtime._run_connection(on_envelope, on_reconnect)

        on_reconnect.assert_awaited_once()
        on_envelope.assert_awaited_once_with("envelope-1")
        assert conn.closed  # connection torn down even though the error propagated

    @pytest.mark.asyncio
    async def test_realtime_stopped_from_on_envelope_propagates(self, monkeypatch):
        realtime, conn = _make_realtime(
            monkeypatch, [_FakeRealtimeEvent(raw="envelope-1")]
        )

        on_envelope = AsyncMock(side_effect=RealtimeStopped("fatal_code", "nope"))
        on_reconnect = AsyncMock()

        with pytest.raises(RealtimeStopped):
            await realtime._run_connection(on_envelope, on_reconnect)

    @pytest.mark.asyncio
    async def test_ordinary_handler_error_is_swallowed(self, monkeypatch):
        """An unrelated bug in a bot's handler shouldn't tear the connection
        down -- only Unauthenticated/RealtimeStopped are special-cased."""
        realtime, conn = _make_realtime(
            monkeypatch,
            [_FakeRealtimeEvent(raw="envelope-1"), _FakeRealtimeEvent(raw="envelope-2")],
        )

        calls = []

        async def flaky_on_envelope(env):
            calls.append(env)
            if len(calls) == 1:
                raise RuntimeError("boom, unrelated bug")

        on_reconnect = AsyncMock()

        # Runs to a clean end (the fake connection's event stream is
        # exhausted) instead of raising, proving the loop survived the first
        # handler's RuntimeError instead of tearing the connection down.
        await realtime._run_connection(flaky_on_envelope, on_reconnect)

        assert calls == ["envelope-1", "envelope-2"]
        assert conn.closed


class TestCatchUpRunsBeforeLiveEvents:
    @pytest.mark.asyncio
    async def test_on_reconnect_runs_before_any_event_is_dispatched(self, monkeypatch):
        realtime, conn = _make_realtime(
            monkeypatch, [_FakeRealtimeEvent(raw="envelope-1")]
        )

        order = []

        async def on_reconnect():
            order.append("catch_up")

        async def on_envelope(env):
            order.append(("event", env))

        await realtime._run_connection(on_envelope, on_reconnect)

        assert order == ["catch_up", ("event", "envelope-1")]

    @pytest.mark.asyncio
    async def test_on_reconnect_failure_does_not_tear_down_connection(self, monkeypatch):
        """A catch-up failure shouldn't tear down an otherwise-healthy
        connection -- live events must still get dispatched."""
        realtime, conn = _make_realtime(
            monkeypatch, [_FakeRealtimeEvent(raw="envelope-1")]
        )

        on_envelope = AsyncMock()
        on_reconnect = AsyncMock(side_effect=RuntimeError("catch-up blew up"))

        await realtime._run_connection(on_envelope, on_reconnect)

        on_envelope.assert_awaited_once_with("envelope-1")
