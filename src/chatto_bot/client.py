"""High-level client wrapping chattolib's ``ChattoClient``.

Every method here maps to one chattolib call and returns chattolib's own
dataclass (see ``chattolib.types``) rather than a hand-rolled dict, per the
interface contract. Errors from chattolib (``chattolib.exceptions``) are
translated to :class:`ChattoError` (and its :class:`Unauthenticated`
subclass) so callers don't need to import chattolib's exception types
directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from chattolib.exceptions import ChattoAuthError, ChattoConnectError
from chattolib.types import (
    DirectoryMember,
    Message,
    PresenceStatus,
    Room,
    RoomDirectoryScope,
    RoomWithViewerState,
    TimelinePage,
    ViewerUser,
)

if TYPE_CHECKING:
    from .transport import Transport

logger = logging.getLogger(__name__)

_NOT_FOUND_CODES = frozenset({"not_found"})


class ChattoError(Exception):
    """A Chatto API call failed. ``code`` is chattolib's Connect error code string."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class Unauthenticated(ChattoError):
    """The bearer token/session was rejected.

    Callers should invoke ``transport.relogin()`` and retry (or reconnect, for
    the realtime layer); this is not necessarily an unrecoverable error.
    """


def _map_error(exc: Exception) -> ChattoError:
    if isinstance(exc, ChattoAuthError):
        return Unauthenticated("unauthenticated", str(exc))
    if isinstance(exc, ChattoConnectError):
        if exc.code == "unauthenticated":
            return Unauthenticated(exc.code, exc.message)
        return ChattoError(exc.code, exc.message)
    return ChattoError("unknown", str(exc))


class Client:
    """Async wrapper over chattolib's ``ChattoClient``.

    Holds no state of its own beyond the ``Transport`` reference -- every
    method reads ``transport.chatto_client`` fresh on each call, since
    ``Transport.relogin()`` swaps that object out in place.
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    @property
    def _chatto(self):
        return self._transport.chatto_client

    async def _call(self, coro):
        try:
            return await coro
        except (ChattoAuthError, ChattoConnectError) as exc:
            raise _map_error(exc) from exc

    async def _call_or_none(self, coro):
        try:
            return await coro
        except ChattoConnectError as exc:
            if exc.code in _NOT_FOUND_CODES:
                return None
            raise _map_error(exc) from exc
        except ChattoAuthError as exc:
            raise _map_error(exc) from exc

    async def close(self) -> None:
        """Close the underlying transport (and its ``ChattoClient``)."""
        await self._transport.close()

    # --- Viewer ---

    async def get_viewer(self) -> ViewerUser:
        return await self._call(self._chatto.viewer_user())

    # --- Rooms / room directory ---

    async def list_rooms(
        self, scope: str | RoomDirectoryScope | None = None
    ) -> list[RoomWithViewerState]:
        resolved = scope if isinstance(scope, RoomDirectoryScope) else (
            RoomDirectoryScope(scope) if scope else RoomDirectoryScope.ALL
        )
        return await self._call(self._chatto.list_rooms(resolved))

    async def get_room(self, room_id: str) -> Room | None:
        rws = await self._call(self._chatto.get_room(room_id))
        return rws.room if rws is not None else None

    async def join_room(self, room_id: str) -> Room:
        return await self._call(self._chatto.join_room(room_id))

    async def leave_room(self, room_id: str) -> bool:
        """Leave a room. Raises :class:`ChattoError` for DM and universal
        rooms, which cannot be left; callers should catch it to message the
        user rather than crash."""
        return await self._call(self._chatto.leave_room(room_id))

    async def start_dm(self, participant_ids: list[str]) -> Room:
        return await self._call(self._chatto.start_dm(participant_ids))

    # --- Messages ---

    async def create_message(
        self,
        room_id: str,
        body: str,
        *,
        in_reply_to: str | None = None,
        thread_root_event_id: str | None = None,
    ) -> Message:
        return await self._call(
            self._chatto.post_message(
                room_id,
                body,
                in_reply_to=in_reply_to or "",
                thread_root_event_id=thread_root_event_id or "",
            )
        )

    async def update_message(self, room_id: str, event_id: str, body: str) -> Message:
        return await self._call(
            self._chatto.update_message(room_id, event_id, body=body)
        )

    async def delete_message(self, room_id: str, event_id: str) -> None:
        await self._call(self._chatto.delete_message(room_id, event_id))

    async def get_message(self, room_id: str, event_id: str) -> Message | None:
        """Fetch one message. Returns ``None`` on not-found -- including a
        message retracted between an event signal and this fetch."""
        return await self._call_or_none(self._chatto.get_message(room_id, event_id))

    async def batch_get_messages(
        self, room_id: str, event_ids: list[str]
    ) -> list[Message]:
        """Room-scoped batch fetch. Missing/retracted ids are silently
        omitted from the result by the server, not raised as errors."""
        return await self._call(self._chatto.batch_get_messages(room_id, event_ids))

    # --- Room timeline ---

    async def get_room_events(
        self,
        room_id: str,
        *,
        limit: int = 50,
        before: str | None = None,
        after: str | None = None,
    ) -> TimelinePage:
        """Fetch a page of room timeline events. ``before``/``after`` are
        opaque server-issued cursors (``TimelinePage.start_cursor``/``end_cursor``),
        not event IDs -- do not construct or parse them."""
        return await self._call(
            self._chatto.get_room_events(
                room_id, limit=limit, before=before, after=after
            )
        )

    async def get_room_events_around(
        self, room_id: str, event_id: str, limit: int = 50
    ) -> TimelinePage:
        page, _target_index = await self._call(
            self._chatto.get_room_events_around(room_id, event_id, limit=limit)
        )
        return page

    async def mark_room_as_read(self, room_id: str, up_to_event_id: str) -> None:
        await self._call(self._chatto.mark_room_as_read(room_id, up_to_event_id))

    # --- Reactions ---

    async def add_reaction(self, room_id: str, message_event_id: str, emoji: str) -> None:
        """``emoji`` is a shortcode (e.g. ``"thumbsup"``), not a literal glyph."""
        await self._call(self._chatto.add_reaction(room_id, message_event_id, emoji))

    async def remove_reaction(self, room_id: str, message_event_id: str, emoji: str) -> None:
        await self._call(self._chatto.remove_reaction(room_id, message_event_id, emoji))

    # --- Users / member directory ---

    async def list_users(self, search: str = "", *, limit: int = 50) -> list[DirectoryMember]:
        users, _page = await self._call(self._chatto.list_users(search=search, limit=limit))
        return users

    async def batch_get_users(self, user_ids: list[str]) -> list[DirectoryMember]:
        return await self._call(self._chatto.batch_get_users(user_ids))

    # --- Presence / typing / read state ---

    async def update_presence(
        self, status: str | PresenceStatus = "PRESENCE_STATUS_ONLINE"
    ) -> None:
        """Set the current user's live presence. Transient -- callers must keep
        refreshing on an interval; OFFLINE cannot be set explicitly (the
        server rejects it)."""
        resolved = status if isinstance(status, PresenceStatus) else PresenceStatus(status)
        await self._call(self._chatto.update_presence(resolved))

    async def update_typing_indicator(
        self,
        room_id: str,
        is_typing: bool = True,
        thread_root_event_id: str | None = None,
    ) -> None:
        """Refresh the live-only typing indicator for a room or thread.

        chattolib only supports "I am typing now" (it expires on its own via
        a server-side TTL); there is no request for "stopped typing". When
        ``is_typing`` is ``False`` this is a deliberate no-op rather than an
        error, so existing bot code that calls
        ``update_typing_indicator(room, False)`` to mean "stop" keeps working
        without raising.
        """
        if not is_typing:
            return
        await self._call(
            self._chatto.update_typing_indicator(
                room_id, thread_root_event_id=thread_root_event_id or ""
            )
        )
