"""Tests for the realtime dispatch pipeline in bot.py: hydration gating,
room-kind/user-cache invalidation, reconnect catch-up, and relogin-on-401.

These exercise the seams introduced by the ConnectRPC/realtime rewrite that
have no equivalent in the old GraphQL-era test suite.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from protobuf import Oneof
from protobuf.wkt import Timestamp

from chatto_bot._pb.chatto.api.v1.message_types_pb import Message, ThreadSummary
from chatto_bot._pb.chatto.api.v1.room_directory_pb import RoomViewerState, RoomWithViewerState
from chatto_bot._pb.chatto.api.v1.room_timeline_pb import (
    RoomMessagePosted,
    RoomTimelineEvent,
    RoomTimelineIncludes,
    RoomTimelinePage,
)
from chatto_bot._pb.chatto.api.v1.rooms_pb import Room, RoomKind
from chatto_bot._pb.chatto.api.v1.users_pb import User as ProtoUser
from chatto_bot._pb.chatto.realtime.v1 import realtime_pb as rt
from chatto_bot.client import Unauthenticated
from chatto_bot.command import Command
from chatto_bot.event import EventHandler
from chatto_bot.hydrate import HydratedEvent
from chatto_bot.types import User


def _envelope(field: str, payload, *, actor_id: str = "U1") -> rt.RealtimeEventEnvelope:
    return rt.RealtimeEventEnvelope(
        id="E1",
        actor_id=actor_id,
        created_at=Timestamp.from_datetime(
            datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        ),
        event=Oneof(field, payload),
    )


class TestCommandContent:
    """The two command triggers: the ``!`` prefix and a leading @mention of the bot.

    The fixture bot has login ``testbot`` and display name ``Test Bot`` (a space,
    so not a single-token mention handle).
    """

    def test_prefix_trigger(self, bot):
        assert bot._command_content("!ping") == "ping"
        assert bot._command_content("!remind me in 5m") == "remind me in 5m"

    def test_bare_prefix_dispatches_to_no_command(self, bot):
        assert bot._command_content("!") == ""

    def test_mention_by_login(self, bot):
        assert bot._command_content("@testbot ping") == "ping"

    def test_mention_is_case_insensitive(self, bot):
        assert bot._command_content("@TestBot ping") == "ping"

    def test_mention_by_single_token_display_name(self, bot):
        bot.user = User(id="Ubot", login="testbot", display_name="Chabotto")
        assert bot._command_content("@Chabotto ping") == "ping"

    def test_multiword_display_name_is_not_a_handle(self, bot):
        # display name "Test Bot" has a space, so "@Test Bot" is not a trigger
        assert bot._command_content("@Test Bot ping") is None

    def test_mention_must_be_at_start(self, bot):
        assert bot._command_content("hey @testbot ping") is None

    def test_mention_requires_a_word_boundary(self, bot):
        # the login must be followed by whitespace or end, not more name chars
        assert bot._command_content("@testbottt ping") is None

    def test_bare_mention_dispatches_to_no_command(self, bot):
        assert bot._command_content("@testbot") == ""

    def test_plain_message_is_not_a_command(self, bot):
        assert bot._command_content("hello @testbot how are you") is None
        assert bot._command_content("") is None

    def test_leading_whitespace_tolerated(self, bot):
        assert bot._command_content("  !ping") == "ping"
        assert bot._command_content("  @testbot ping") == "ping"


class TestWillDispatch:
    def test_message_posted_needs_command_or_handler(self, bot):
        assert bot._will_dispatch("message_posted") is False

        bot.add_command(Command(name="ping", callback=AsyncMock()))
        assert bot._will_dispatch("message_posted") is True

    def test_message_posted_dispatches_with_only_a_handler(self, bot):
        bot._event_handlers.append(
            EventHandler(event_type="message_posted", callback=AsyncMock())
        )
        assert bot._will_dispatch("message_posted") is True

    def test_other_events_need_a_matching_handler(self, bot):
        assert bot._will_dispatch("room_created") is False
        bot._event_handlers.append(
            EventHandler(event_type="room_created", callback=AsyncMock())
        )
        assert bot._will_dispatch("room_created") is True
        # A command registration alone doesn't gate non-message events.
        assert bot._will_dispatch("reaction_added") is False


class TestOnEnvelope:
    @pytest.mark.asyncio
    async def test_skips_hydration_when_nothing_is_registered(self, bot):
        env = _envelope("room_archived", rt.RealtimeRoomEvent(room_id="R1"))
        await bot._on_envelope(env)
        bot.hydrator.hydrate.assert_not_called()

    @pytest.mark.asyncio
    async def test_hydrates_and_dispatches_when_handler_registered(self, bot):
        called = False

        async def handler(ctx):
            nonlocal called
            called = True

        bot._event_handlers.append(EventHandler(event_type="room_archived", callback=handler))
        env = _envelope("room_archived", rt.RealtimeRoomEvent(room_id="R1"))
        bot.hydrator.hydrate = AsyncMock(
            return_value=HydratedEvent(envelope=env, event_name="room_archived")
        )

        await bot._on_envelope(env)

        bot.hydrator.hydrate.assert_awaited_once_with(env)
        assert called

    @pytest.mark.asyncio
    async def test_drops_dispatch_when_hydrate_returns_none(self, bot):
        """A message retracted between the signal and the fetch drops the
        whole dispatch instead of reaching handlers with a missing message."""
        called = False

        async def handler(ctx):
            nonlocal called
            called = True

        bot._event_handlers.append(EventHandler(event_type="message_posted", callback=handler))
        env = _envelope("message_posted", rt.RealtimeMessagePostedEvent(room_id="R1", message_event_id="E1"))
        bot.hydrator.hydrate = AsyncMock(return_value=None)

        await bot._on_envelope(env)

        assert not called


class TestInvalidations:
    @pytest.mark.asyncio
    async def test_user_profile_updated_invalidates_user_cache(self, bot):
        env = _envelope("user_profile_updated", rt.RealtimeUserProfileUpdatedEvent(user_id="U1", login="alice"))
        bot.hydrator.hydrate = AsyncMock(
            return_value=HydratedEvent(envelope=env, event_name="user_profile_updated")
        )
        bot._event_handlers.append(
            EventHandler(event_type="user_profile_updated", callback=AsyncMock())
        )

        await bot._on_envelope(env)

        bot.users.invalidate.assert_called_once_with("U1")

    @pytest.mark.asyncio
    async def test_presence_changed_invalidates_user_cache(self, bot):
        env = _envelope("presence_changed", rt.RealtimePresenceChangedEvent(user_id="U2", status=1))
        bot.hydrator.hydrate = AsyncMock(
            return_value=HydratedEvent(envelope=env, event_name="presence_changed")
        )
        bot._event_handlers.append(
            EventHandler(event_type="presence_changed", callback=AsyncMock())
        )

        await bot._on_envelope(env)

        bot.users.invalidate.assert_called_once_with("U2")

    @pytest.mark.asyncio
    async def test_room_created_invalidates_stale_room_kind(self, bot):
        """A stale is_dm=True cache entry must not survive room_created --
        _dispatch pops it, then re-resolves it via GetRoom before building
        Context, so a later ctx.is_dm read isn't stale."""
        bot._room_kinds["R1"] = True  # stale entry
        bot._event_handlers.append(EventHandler(event_type="room_created", callback=AsyncMock()))
        env = _envelope("room_created", rt.RealtimeRoomEvent(room_id="R1"))
        bot.hydrator.hydrate = AsyncMock(
            return_value=HydratedEvent(envelope=env, event_name="room_created")
        )

        await bot._on_envelope(env)

        # Cache was invalidated and re-resolved (via GetRoom, mocked as a
        # plain channel), not left holding the stale True.
        bot.client.get_room.assert_awaited_once_with("R1")
        assert bot._room_kinds["R1"] is False


class TestIsDmResolution:
    @pytest.mark.asyncio
    async def test_ensure_room_kind_caches_result(self, bot):
        room = MagicMock()
        room.kind = MagicMock()
        bot.client.get_room = AsyncMock(return_value=room)

        await bot._ensure_room_kind("R1")

        assert "R1" in bot._room_kinds
        bot.client.get_room.assert_awaited_once_with("R1")

        # Second call is served from cache, no extra RPC.
        await bot._ensure_room_kind("R1")
        bot.client.get_room.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ensure_room_kind_swallows_errors(self, bot):
        bot.client.get_room = AsyncMock(side_effect=RuntimeError("boom"))
        await bot._ensure_room_kind("R-missing")
        assert "R-missing" not in bot._room_kinds

    @pytest.mark.asyncio
    async def test_ensure_room_kind_relogs_in_on_unauthenticated(self, bot):
        from connectrpc.code import Code

        bot.client.get_room = AsyncMock(side_effect=Unauthenticated(Code.UNAUTHENTICATED, "nope"))
        await bot._ensure_room_kind("R1")
        bot.transport.relogin.assert_awaited_once()


def _dt(hour: int, minute: int = 0, *, day: int = 1) -> datetime.datetime:
    return datetime.datetime(2026, 1, day, hour, minute, tzinfo=datetime.timezone.utc)


def _ts(hour: int, minute: int = 0, *, day: int = 1) -> Timestamp:
    return Timestamp.from_datetime(_dt(hour, minute, day=day))


def _posted_tev(event_id: str, hour: int, minute: int = 0, *, day: int = 1, body: str = "!hi") -> RoomTimelineEvent:
    message = Message(id=event_id, room_id="R1", body=body)
    return RoomTimelineEvent(
        id=event_id,
        actor_id="U1",
        created_at=_ts(hour, minute, day=day),
        event=Oneof("message_posted", RoomMessagePosted(message=message)),
    )


def _recent_dt(minutes_ago: float) -> datetime.datetime:
    """A timestamp ``minutes_ago`` minutes before the real wall-clock ``now``,
    so it survives ``_catch_up``'s live 1-hour hard cutoff (unlike the fixed
    2026-01-01 dates used elsewhere, which only work against an explicitly
    passed-in cutoff)."""
    return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)


def _recent_posted_tev(event_id: str, minutes_ago: float, *, room_id: str = "R1", body: str = "!hi") -> RoomTimelineEvent:
    message = Message(id=event_id, room_id=room_id, body=body)
    return RoomTimelineEvent(
        id=event_id,
        actor_id="U1",
        created_at=Timestamp.from_datetime(_recent_dt(minutes_ago)),
        event=Oneof("message_posted", RoomMessagePosted(message=message)),
    )


def _room_with_viewer_state(room_id: str, *, is_member: bool = True, kind=RoomKind.CHANNEL) -> RoomWithViewerState:
    return RoomWithViewerState(
        room=Room(id=room_id, kind=kind),
        viewer_state=RoomViewerState(is_member=is_member),
    )


def _threaded_root_tev(
    event_id: str,
    created_at: Timestamp,
    last_reply_at: Timestamp | None,
    *,
    room_id: str = "R1",
    thread_root_event_id: str | None = None,
) -> RoomTimelineEvent:
    """A ``message_posted`` tev whose ``Message`` carries a ``ThreadSummary``.
    ``thread_root_event_id=None`` leaves the summary's field blank, exercising
    the fallback to the event's own id."""
    thread = ThreadSummary(
        thread_root_event_id="" if thread_root_event_id is None else thread_root_event_id,
        reply_count=1,
        last_reply_at=last_reply_at,
    )
    message = Message(id=event_id, room_id=room_id, body="root", thread=thread)
    return RoomTimelineEvent(
        id=event_id,
        actor_id="U1",
        created_at=created_at,
        event=Oneof("message_posted", RoomMessagePosted(message=message)),
    )


def _recent_threaded_root_tev(
    event_id: str, minutes_ago: float, last_reply_minutes_ago: float, *, room_id: str = "R1"
) -> RoomTimelineEvent:
    """Thread root with recent (wall-clock-relative) timestamps, so it survives
    ``_catch_up``'s live 1-hour hard cutoff."""
    thread = ThreadSummary(
        thread_root_event_id=event_id,
        reply_count=1,
        last_reply_at=Timestamp.from_datetime(_recent_dt(last_reply_minutes_ago)),
    )
    message = Message(id=event_id, room_id=room_id, body="!root", thread=thread)
    return RoomTimelineEvent(
        id=event_id,
        actor_id="U1",
        created_at=Timestamp.from_datetime(_recent_dt(minutes_ago)),
        event=Oneof("message_posted", RoomMessagePosted(message=message)),
    )


def _recent_reply_tev(
    event_id: str, minutes_ago: float, thread_root_event_id: str, *, room_id: str = "R1"
) -> RoomTimelineEvent:
    """A thread reply (``message_posted`` with ``thread_root_event_id`` set),
    as GetThreadEvents would return it."""
    message = Message(
        id=event_id,
        room_id=room_id,
        body="!reply",
        thread_root_event_id=thread_root_event_id,
    )
    return RoomTimelineEvent(
        id=event_id,
        actor_id="U1",
        created_at=Timestamp.from_datetime(_recent_dt(minutes_ago)),
        event=Oneof("message_posted", RoomMessagePosted(message=message)),
    )


class TestCollectMissedRoomEvents:
    """Tests for ``Bot._collect_missed_room_events`` -- gathers a room's missed
    timeline events (per the reconnect catch-up's ``before``-cursor paging)
    without dispatching anything."""

    @pytest.mark.asyncio
    async def test_collects_events_newer_than_cursor(self, bot):
        tev = _posted_tev("E2", 12, 0)
        page = RoomTimelinePage(
            events=[tev],
            start_cursor="c1",
            end_cursor="c2",
            has_older=False,
            has_newer=False,
            includes=RoomTimelineIncludes(users={}),
        )
        bot.client.get_room_events = AsyncMock(return_value=page)

        cursor_ts = "2026-01-01T00:00:00.000000Z"
        hard_cutoff = "2025-01-01T00:00:00.000000Z"
        collected, _ = await bot._collect_missed_room_events(
            "R1", cursor_ts, hard_cutoff, hard_cutoff
        )

        assert len(collected) == 1
        _, got_tev, _ = collected[0]
        assert got_tev.id == "E2"

    @pytest.mark.asyncio
    async def test_stops_at_hard_cutoff(self, bot):
        """The walk stops at the hard cutoff, not the stored cursor: an event
        older than the hard cutoff ends paging after a single page, and no
        event at/older than the stored cursor is collected for dispatch."""
        old_tev = RoomTimelineEvent(
            id="E-old",
            actor_id="U1",
            created_at=Timestamp.from_datetime(
                datetime.datetime(2025, 6, 1, tzinfo=datetime.timezone.utc)
            ),
            event=Oneof("room_archived", MagicMock(room_id="R1")),
        )
        page = RoomTimelinePage(
            events=[old_tev], start_cursor="c1", end_cursor="c2", has_older=True, has_newer=False
        )
        bot.client.get_room_events = AsyncMock(return_value=page)

        cursor_ts = "2025-12-01T00:00:00.000000Z"
        hard_cutoff = "2025-07-01T00:00:00.000000Z"  # newer than old_tev
        collected, candidates = await bot._collect_missed_room_events(
            "R1", cursor_ts, hard_cutoff, hard_cutoff
        )

        assert collected == []  # older than the cursor -- not dispatch-collected
        assert candidates == set()
        bot.client.get_room_events.assert_awaited_once()  # walk stopped at the hard cutoff

    @pytest.mark.asyncio
    async def test_partial_page_replay_when_oldest_event_is_stale(self, bot):
        """D1 regression (bullet 1): a page mixing one stale event (at/older
        than the cursor) with newer missed events must still replay the
        newer ones. Events arrive oldest-first (real server order -- see
        cli/internal/core/room_events.go); scanning the page front-to-back
        hits the stale event first and would stop immediately, losing every
        newer-but-still-missed event in the same page.
        """
        stale = _posted_tev("E-stale", 8, 0, body="!stale")
        newer1 = _posted_tev("E-newer1", 9, 0, body="!newer1")
        newer2 = _posted_tev("E-newer2", 9, 30, body="!newer2")
        page = RoomTimelinePage(
            events=[stale, newer1, newer2],  # oldest-first, as the server sends them
            start_cursor="c1",
            end_cursor="c2",
            has_older=False,
            has_newer=False,
            includes=RoomTimelineIncludes(users={}),
        )
        bot.client.get_room_events = AsyncMock(return_value=page)

        cursor_ts = "2026-01-01T08:30:00.000000Z"  # between stale and newer1
        hard_cutoff = "2020-01-01T00:00:00.000000Z"
        collected, _ = await bot._collect_missed_room_events(
            "R1", cursor_ts, hard_cutoff, hard_cutoff
        )

        ids = {tev.id for _, tev, _ in collected}
        assert ids == {"E-newer1", "E-newer2"}


class TestCatchUpDispatchOrder:
    """Tests for ``Bot._catch_up``'s gather-sort-dispatch behavior: every
    room's missed events are collected before anything is dispatched, then
    dispatched once, oldest-first, across all rooms combined."""

    @pytest.mark.asyncio
    async def test_full_page_dispatches_oldest_first(self, bot):
        """D1 regression (bullet 2): a full page of missed events (nothing
        stale to stop on) must dispatch oldest-to-newest. Events already
        arrive oldest-first from the server, so no reversal should happen at
        dispatch time -- reversing here would advance the cursor to the
        newest event first and dedup-drop every older sibling in the batch.
        """
        e1 = _recent_posted_tev("E1", 30, body="!one")
        e2 = _recent_posted_tev("E2", 20, body="!two")
        e3 = _recent_posted_tev("E3", 10, body="!three")
        page = RoomTimelinePage(
            events=[e1, e2, e3],
            start_cursor="c1",
            end_cursor="c2",
            has_older=False,
            has_newer=False,
            includes=RoomTimelineIncludes(users={}),
        )
        bot.client.get_room_events = AsyncMock(return_value=page)
        bot.client.list_rooms = AsyncMock(return_value=[_room_with_viewer_state("R1")])
        bot._save_state = MagicMock()

        dispatched_ids = []
        original_dispatch = bot._dispatch

        async def tracking_dispatch(event):
            dispatched_ids.append(event.id)
            return await original_dispatch(event)

        bot._dispatch = tracking_dispatch
        bot.add_command(Command(name="one", callback=AsyncMock()))
        bot.add_command(Command(name="two", callback=AsyncMock()))
        bot.add_command(Command(name="three", callback=AsyncMock()))

        await bot._catch_up()

        assert dispatched_ids == ["E1", "E2", "E3"]

    @pytest.mark.asyncio
    async def test_cross_room_dispatch_does_not_starve_older_room(self, bot):
        """D1 regression (bullet 3): dispatching room-by-room would advance
        the single global cursor to room A's newest event before room B is
        even looked at, and room B's older-but-still-missed events would
        then be dropped by the cursor dedup in ``_dispatch``. Gathering
        every room's missed events first and dispatching them once, sorted
        ascending across rooms, must replay all three.
        """
        room_a_event = _recent_posted_tev("EA", 5, room_id="RA", body="!a")
        room_b_event_1 = _recent_posted_tev("EB1", 30, room_id="RB", body="!b1")
        room_b_event_2 = _recent_posted_tev("EB2", 20, room_id="RB", body="!b2")

        def get_room_events(room_id, *, limit, before=None):
            if room_id == "RA":
                return RoomTimelinePage(
                    events=[room_a_event],
                    start_cursor="ca1",
                    end_cursor="ca2",
                    has_older=False,
                    has_newer=False,
                    includes=RoomTimelineIncludes(users={}),
                )
            if room_id == "RB":
                return RoomTimelinePage(
                    events=[room_b_event_1, room_b_event_2],  # oldest-first
                    start_cursor="cb1",
                    end_cursor="cb2",
                    has_older=False,
                    has_newer=False,
                    includes=RoomTimelineIncludes(users={}),
                )
            raise AssertionError(f"unexpected room_id {room_id}")

        async def fake_get_room_events(room_id, *, limit=50, before=None, after=None):
            return get_room_events(room_id, limit=limit, before=before)

        bot.client.get_room_events = AsyncMock(side_effect=fake_get_room_events)
        # Room A is listed (and would be processed) before room B.
        bot.client.list_rooms = AsyncMock(
            return_value=[
                _room_with_viewer_state("RA"),
                _room_with_viewer_state("RB"),
            ]
        )
        bot._save_state = MagicMock()

        dispatched_ids = []
        original_dispatch = bot._dispatch

        async def tracking_dispatch(event):
            dispatched_ids.append(event.id)
            return await original_dispatch(event)

        bot._dispatch = tracking_dispatch
        for name in ("a", "b1", "b2"):
            bot.add_command(Command(name=name, callback=AsyncMock()))

        await bot._catch_up()

        # Chronological across rooms: room B's older events first, then
        # room A's newer one -- not grouped/room-ordered.
        assert dispatched_ids == ["EB1", "EB2", "EA"]

        # None of the three was dropped by cursor dedup: the global cursor
        # must have ended up at the newest event's (room A's) timestamp, not
        # stuck at an older room's event as it would be if a starved replay
        # had silently dropped room A or ended early.
        from chatto_bot.types import format_cursor

        assert bot._cursor["_global"] == format_cursor(room_a_event.created_at)


class TestThreadCatchup:
    """Thread replies never appear in a room timeline, so catch-up discovers
    candidate threads while walking the room backwards and backfills their
    missed replies via GetThreadEvents."""

    @pytest.mark.asyncio
    async def test_records_candidate_for_preexisting_root_with_recent_reply(self, bot):
        """A root posted *before* the cursor is still a candidate when its
        thread saw a reply after the cursor -- the walk reaches it (it's
        within the hard cutoff) even though dispatch-collection skips it."""
        root = _threaded_root_tev("root1", _ts(8, 0), _ts(12, 0))
        page = RoomTimelinePage(
            events=[root],
            start_cursor="c1",
            end_cursor="c2",
            has_older=False,
            has_newer=False,
            includes=RoomTimelineIncludes(users={}),
        )
        bot.client.get_room_events = AsyncMock(return_value=page)

        cursor_ts = "2026-01-01T10:00:00.000000Z"  # after the root, before the reply
        hard_cutoff = "2020-01-01T00:00:00.000000Z"
        collected, candidates = await bot._collect_missed_room_events(
            "R1", cursor_ts, hard_cutoff, hard_cutoff
        )

        # Root itself predates the cursor, so nothing to dispatch...
        assert collected == []
        # ...but the thread is a backfill candidate.
        assert candidates == {("R1", "root1")}

    @pytest.mark.asyncio
    async def test_no_candidate_when_last_reply_at_or_before_cursor(self, bot):
        """A thread whose most recent reply is at/older than the cursor isn't
        a candidate -- nothing was missed."""
        root = _threaded_root_tev("root1", _ts(8, 0), _ts(9, 0))
        page = RoomTimelinePage(
            events=[root],
            start_cursor="c1",
            end_cursor="c2",
            has_older=False,
            has_newer=False,
            includes=RoomTimelineIncludes(users={}),
        )
        bot.client.get_room_events = AsyncMock(return_value=page)

        cursor_ts = "2026-01-01T10:00:00.000000Z"  # after the last reply
        hard_cutoff = "2020-01-01T00:00:00.000000Z"
        _, candidates = await bot._collect_missed_room_events(
            "R1", cursor_ts, hard_cutoff, hard_cutoff
        )

        assert candidates == set()

    @pytest.mark.asyncio
    async def test_candidate_falls_back_to_event_id_when_summary_blank(self, bot):
        """The root's own ``thread.thread_root_event_id`` may be blank; the
        root event id IS the thread root, so the candidate falls back to it."""
        root = _threaded_root_tev("root1", _ts(8, 0), _ts(12, 0), thread_root_event_id=None)
        page = RoomTimelinePage(
            events=[root],
            start_cursor="c1",
            end_cursor="c2",
            has_older=False,
            has_newer=False,
            includes=RoomTimelineIncludes(users={}),
        )
        bot.client.get_room_events = AsyncMock(return_value=page)

        cursor_ts = "2026-01-01T10:00:00.000000Z"
        hard_cutoff = "2020-01-01T00:00:00.000000Z"
        _, candidates = await bot._collect_missed_room_events(
            "R1", cursor_ts, hard_cutoff, hard_cutoff
        )

        assert candidates == {("R1", "root1")}

    @pytest.mark.asyncio
    async def test_no_candidate_for_root_older_than_discovery_window(self, bot):
        """A root older than the (tighter) discovery cutoff isn't a candidate
        even when its thread saw a reply after the cursor -- discovery is
        bounded to a smaller window than the dispatch walk, so routine
        reconnects don't re-scan a full hour just to find thread activity. The
        root is still walked (and dispatch-collected as a missed message), just
        not treated as a thread-backfill candidate."""
        root = _threaded_root_tev("root1", _ts(8, 0), _ts(12, 0))
        page = RoomTimelinePage(
            events=[root],
            start_cursor="c1",
            end_cursor="c2",
            has_older=False,
            has_newer=False,
            includes=RoomTimelineIncludes(users={}),
        )
        bot.client.get_room_events = AsyncMock(return_value=page)

        cursor_ts = "2026-01-01T07:00:00.000000Z"  # before the root
        hard_cutoff = "2020-01-01T00:00:00.000000Z"
        discovery_cutoff = "2026-01-01T09:00:00.000000Z"  # after the root at 08:00
        collected, candidates = await bot._collect_missed_room_events(
            "R1", cursor_ts, hard_cutoff, discovery_cutoff
        )

        # Root is a missed room message (newer than the cursor) so it's
        # collected, but it's outside the discovery window, so no backfill.
        assert {tev.id for _, tev, _ in collected} == {"root1"}
        assert candidates == set()

    @pytest.mark.asyncio
    async def test_collect_thread_events_excludes_root(self, bot):
        """The root comes back in the thread timeline too, but it already
        flows through the room path, so it's never emitted as a reply."""
        root = _posted_tev("root1", 8, 0, body="root")
        reply1 = _posted_tev("reply-1", 9, 0, body="r1")
        reply2 = _posted_tev("reply-2", 12, 0, body="r2")
        page = RoomTimelinePage(
            events=[root, reply1, reply2],  # oldest-first
            start_cursor="c1",
            end_cursor="c2",
            has_older=False,
            has_newer=False,
            includes=RoomTimelineIncludes(users={}),
        )
        bot.client.get_thread_events = AsyncMock(return_value=page)

        cursor_ts = "2026-01-01T07:00:00.000000Z"  # before everything
        hard_cutoff = "2020-01-01T00:00:00.000000Z"
        collected = await bot._collect_missed_thread_events("R1", "root1", cursor_ts, hard_cutoff)

        ids = {tev.id for _, tev, _ in collected}
        assert ids == {"reply-1", "reply-2"}  # root excluded, replies kept

    @pytest.mark.asyncio
    async def test_collect_thread_events_only_replies_after_cursor(self, bot):
        """Only replies newer than the cursor are collected."""
        root = _posted_tev("root1", 8, 0, body="root")
        reply_stale = _posted_tev("reply-stale", 9, 0, body="stale")
        reply_new = _posted_tev("reply-new", 12, 0, body="new")
        page = RoomTimelinePage(
            events=[root, reply_stale, reply_new],  # oldest-first
            start_cursor="c1",
            end_cursor="c2",
            has_older=False,
            has_newer=False,
            includes=RoomTimelineIncludes(users={}),
        )
        bot.client.get_thread_events = AsyncMock(return_value=page)

        cursor_ts = "2026-01-01T10:00:00.000000Z"  # between stale and new
        hard_cutoff = "2020-01-01T00:00:00.000000Z"
        collected = await bot._collect_missed_thread_events("R1", "root1", cursor_ts, hard_cutoff)

        ids = {tev.id for _, tev, _ in collected}
        assert ids == {"reply-new"}

    @pytest.mark.asyncio
    async def test_catch_up_interleaves_backfilled_reply(self, bot):
        """A backfilled thread reply merges into the room stream and
        dispatches in chronological order relative to a plain room message --
        as a ``message_posted`` with ``thread_root_event_id`` set."""
        # All within the thread-discovery window so the root is found.
        root = _recent_threaded_root_tev("root1", 12, 4)  # replied to at 4 min ago
        mid = _recent_posted_tev("EM", 8, body="!mid")
        reply = _recent_reply_tev("reply1", 4, "root1")

        room_page = RoomTimelinePage(
            events=[root, mid],  # oldest-first
            start_cursor="c1",
            end_cursor="c2",
            has_older=False,
            has_newer=False,
            includes=RoomTimelineIncludes(users={}),
        )
        thread_page = RoomTimelinePage(
            events=[root, reply],  # root comes back too; it's excluded
            start_cursor="ct1",
            end_cursor="ct2",
            has_older=False,
            has_newer=False,
            includes=RoomTimelineIncludes(users={}),
        )
        bot.client.get_room_events = AsyncMock(return_value=room_page)
        bot.client.get_thread_events = AsyncMock(return_value=thread_page)
        bot.client.list_rooms = AsyncMock(return_value=[_room_with_viewer_state("R1")])
        bot._save_state = MagicMock()
        bot._event_handlers.append(
            EventHandler(event_type="message_posted", callback=AsyncMock())
        )

        dispatched = []
        original_dispatch = bot._dispatch

        async def tracking_dispatch(event):
            dispatched.append(event)
            return await original_dispatch(event)

        bot._dispatch = tracking_dispatch

        await bot._catch_up()

        # Root (12m) -> plain room message (8m) -> backfilled reply (4m).
        assert [e.id for e in dispatched] == ["root1", "EM", "reply1"]

        # The reply reached dispatch carrying its thread linkage.
        reply_event = next(e for e in dispatched if e.id == "reply1")
        assert reply_event.event.thread_root_event_id == "root1"

        # GetThreadEvents was consulted for the discovered candidate.
        bot.client.get_thread_events.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_catch_up_backfills_reply_to_preexisting_thread(self, bot):
        """The whole point of the feature, end to end: a thread whose root
        was posted (and dispatched) *before* the disconnect gets a reply
        *during* the gap. With a real stored cursor between the two, catch-up
        must dispatch only the reply -- never the root (already seen before
        the gap) and never twice.
        """
        from chatto_bot.types import format_cursor

        # Gap shorter than the discovery window so the pre-cursor root is both
        # below the cursor AND still inside the window. Root at 10m ago (below
        # the cursor), reply at 2m ago (above it).
        root = _recent_threaded_root_tev("root1", 10, 2)
        reply = _recent_reply_tev("reply1", 2, "root1")
        bot._cursor["_global"] = format_cursor(
            Timestamp.from_datetime(_recent_dt(6))  # between root and reply
        )

        room_page = RoomTimelinePage(
            events=[root],
            start_cursor="c1",
            end_cursor="c2",
            has_older=False,
            has_newer=False,
            includes=RoomTimelineIncludes(users={}),
        )
        thread_page = RoomTimelinePage(
            events=[root, reply],  # root comes back too; it must be excluded
            start_cursor="ct1",
            end_cursor="ct2",
            has_older=False,
            has_newer=False,
            includes=RoomTimelineIncludes(users={}),
        )
        bot.client.get_room_events = AsyncMock(return_value=room_page)
        bot.client.get_thread_events = AsyncMock(return_value=thread_page)
        bot.client.list_rooms = AsyncMock(return_value=[_room_with_viewer_state("R1")])
        bot._save_state = MagicMock()
        bot._event_handlers.append(
            EventHandler(event_type="message_posted", callback=AsyncMock())
        )

        dispatched = []
        original_dispatch = bot._dispatch

        async def tracking_dispatch(event):
            dispatched.append(event)
            return await original_dispatch(event)

        bot._dispatch = tracking_dispatch

        await bot._catch_up()

        # Only the reply -- the pre-cursor root is neither replayed (it's
        # below the cursor) nor double-dispatched (excluded from the thread
        # page by id).
        assert [e.id for e in dispatched] == ["reply1"]
        assert dispatched[0].event.thread_root_event_id == "root1"


class TestOnEnvelopeUnauthenticated:
    @pytest.mark.asyncio
    async def test_hydrate_unauthenticated_relogs_in_and_propagates(self, bot):
        """D2 regression: a bearer token revoked mid-run while hydrating a
        realtime event must trigger a relogin and propagate ``Unauthenticated``
        (rather than being logged and dropped) so the realtime supervisor
        tears the connection down and reconnects -- which is what runs
        catch-up for whatever got missed while the token was bad.
        """
        from connectrpc.code import Code

        bot.add_command(Command(name="ping", callback=AsyncMock()))
        env = _envelope(
            "message_posted", rt.RealtimeMessagePostedEvent(room_id="R1", message_event_id="E1")
        )
        bot.hydrator.hydrate = AsyncMock(
            side_effect=Unauthenticated(Code.UNAUTHENTICATED, "token revoked")
        )

        with pytest.raises(Unauthenticated):
            await bot._on_envelope(env)

        bot.transport.relogin.assert_awaited_once()


class TestRunRealtimeReauth:
    @pytest.mark.asyncio
    async def test_relogs_in_and_restarts_on_unauthenticated(self, bot):
        from connectrpc.code import Code

        call_count = 0

        async def fake_run(on_envelope, on_reconnect):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Unauthenticated(Code.UNAUTHENTICATED, "expired")
            bot._closed = True  # let the supervisor loop exit next check

        bot.realtime.run = AsyncMock(side_effect=fake_run)

        await bot._run_realtime()

        assert call_count == 2
        bot.transport.relogin.assert_awaited_once()
