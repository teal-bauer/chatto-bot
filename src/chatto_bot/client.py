"""High-level async ConnectRPC client wrapping generated Chatto service stubs.

Every method here maps to one (or, for typing, occasionally zero) generated RPC call
and returns the generated proto response message (or a field pulled off it) rather
than a hand-rolled dict, per the interface contract. Errors from the generated
clients (``connectrpc.errors.ConnectError``) are translated to :class:`ChattoError`
(and its :class:`Unauthenticated` subclass) so callers don't need to import
``connectrpc`` directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from protobuf import Oneof

from ._pb.chatto.api.v1.member_directory_pb import (
    BatchGetUsersRequest,
    DirectoryMember,
    ListUsersRequest,
)
from ._pb.chatto.api.v1.member_directory_connect import UserServiceClient
from ._pb.chatto.api.v1.message_types_pb import Message
from ._pb.chatto.api.v1.messages_connect import MessageServiceClient
from ._pb.chatto.api.v1.messages_pb import (
    BatchGetMessagesRequest,
    CreateMessageRequest,
    DeleteMessageRequest,
    GetMessageRequest,
    UpdateMessageRequest,
)
from ._pb.chatto.api.v1.pagination_pb import PageRequest
from ._pb.chatto.api.v1.presence_pb import PresenceStatus, UpdatePresenceRequest
from ._pb.chatto.api.v1.reactions_pb import AddReactionRequest, RemoveReactionRequest
from ._pb.chatto.api.v1.read_state_pb import MarkRoomAsReadRequest
from ._pb.chatto.api.v1.room_directory_connect import RoomDirectoryServiceClient
from ._pb.chatto.api.v1.room_directory_pb import (
    GetRoomRequest,
    ListRoomsRequest,
    RoomDirectoryScope,
    RoomWithViewerState,
)
from ._pb.chatto.api.v1.room_timeline_pb import (
    GetRoomEventsAroundRequest,
    GetRoomEventsRequest,
    GetThreadEventsRequest,
    RoomTimelinePage,
)
from ._pb.chatto.api.v1.rooms_connect import RoomServiceClient
from ._pb.chatto.api.v1.rooms_pb import (
    JoinRoomRequest,
    LeaveRoomRequest,
    Room,
    StartDMRequest,
    UpdateTypingIndicatorRequest,
)
from ._pb.chatto.api.v1.threads_connect import ThreadServiceClient
from ._pb.chatto.api.v1.account_connect import MyAccountServiceClient
from ._pb.chatto.api.v1.viewer_connect import ViewerServiceClient
from ._pb.chatto.api.v1.viewer_pb import GetViewerRequest, ViewerUser

if TYPE_CHECKING:
    from .transport import Transport

logger = logging.getLogger(__name__)

_SCOPE_PREFIX = "ROOM_DIRECTORY_SCOPE_"
_PRESENCE_PREFIX = "PRESENCE_STATUS_"


class ChattoError(Exception):
    """A ConnectRPC call failed. ``code`` is a :class:`connectrpc.code.Code`."""

    def __init__(self, code: Code, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")


class Unauthenticated(ChattoError):
    """The bearer token/session was rejected (``Code.UNAUTHENTICATED``).

    Callers should invoke ``transport.relogin()`` and retry (or reconnect, for the
    realtime layer); this is not necessarily an unrecoverable error.
    """


def _map_error(exc: ConnectError) -> ChattoError:
    if exc.code == Code.UNAUTHENTICATED:
        return Unauthenticated(exc.code, exc.message)
    return ChattoError(exc.code, exc.message)


def _resolve_scope(scope: str | RoomDirectoryScope | None) -> RoomDirectoryScope:
    if scope is None:
        return RoomDirectoryScope.ALL
    if isinstance(scope, RoomDirectoryScope):
        return scope
    name = scope.removeprefix(_SCOPE_PREFIX)
    return RoomDirectoryScope[name]


def _resolve_presence(status: str | PresenceStatus) -> PresenceStatus:
    if isinstance(status, PresenceStatus):
        return status
    name = status.removeprefix(_PRESENCE_PREFIX)
    return PresenceStatus[name]


class Client:
    """Async wrappers over the generated Chatto ConnectRPC service clients.

    Holds no per-call state of its own; every method builds a request, resolves the
    matching long-lived service client from ``transport.client(...)``, sends the
    current auth headers, and unwraps (or re-raises) the response.
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return self._transport.headers()

    async def _call_or_raise(self, coro):
        try:
            return await coro
        except ConnectError as exc:
            raise _map_error(exc) from exc

    async def _call_or_none(self, coro):
        try:
            return await coro
        except ConnectError as exc:
            if exc.code == Code.NOT_FOUND:
                return None
            raise _map_error(exc) from exc

    async def close(self) -> None:
        """Close the underlying transport (and its cached service clients)."""
        await self._transport.close()

    # --- Viewer ---

    async def get_viewer(self) -> ViewerUser:
        client = self._transport.client(ViewerServiceClient)
        resp = await self._call_or_raise(
            client.get_viewer(GetViewerRequest(), headers=self._headers())
        )
        return resp.user

    # --- Rooms / room directory ---

    async def list_rooms(
        self, scope: str | RoomDirectoryScope | None = None
    ) -> list[RoomWithViewerState]:
        """List rooms visible to the current user.

        ``RoomDirectoryService.ListRooms`` has no ``PageRequest``/``PageInfo`` --
        unlike most other list RPCs, it's documented as returning a finite snapshot
        of every matching room in one response, so there is nothing to paginate.
        """
        client = self._transport.client(RoomDirectoryServiceClient)
        resp = await self._call_or_raise(
            client.list_rooms(
                ListRoomsRequest(scope=_resolve_scope(scope)), headers=self._headers()
            )
        )
        return list(resp.rooms)

    async def get_room(self, room_id: str) -> Room:
        client = self._transport.client(RoomDirectoryServiceClient)
        resp = await self._call_or_raise(
            client.get_room(GetRoomRequest(room_id=room_id), headers=self._headers())
        )
        return resp.room.room

    async def join_room(self, room_id: str) -> Room:
        client = self._transport.client(RoomServiceClient)
        resp = await self._call_or_raise(
            client.join_room(JoinRoomRequest(room_id=room_id), headers=self._headers())
        )
        return resp.room

    async def leave_room(self, room_id: str) -> bool:
        """Leave a room. Raises :class:`ChattoError` (typically
        ``Code.FAILED_PRECONDITION``) for DM and universal rooms, which cannot be
        left; callers should catch it to message the user rather than crash.
        """
        client = self._transport.client(RoomServiceClient)
        resp = await self._call_or_raise(
            client.leave_room(LeaveRoomRequest(room_id=room_id), headers=self._headers())
        )
        return resp.left

    async def start_dm(self, participant_ids: list[str]) -> Room:
        client = self._transport.client(RoomServiceClient)
        resp = await self._call_or_raise(
            client.start_dm(
                StartDMRequest(participant_ids=participant_ids), headers=self._headers()
            )
        )
        return resp.room

    # --- Messages ---

    async def create_message(
        self,
        room_id: str,
        body: str,
        *,
        in_reply_to: str | None = None,
        thread_root_event_id: str | None = None,
    ) -> Message:
        client = self._transport.client(MessageServiceClient)
        resp = await self._call_or_raise(
            client.create_message(
                CreateMessageRequest(
                    room_id=room_id,
                    body=body,
                    in_reply_to=in_reply_to or "",
                    thread_root_event_id=thread_root_event_id or "",
                ),
                headers=self._headers(),
            )
        )
        return resp.message

    async def update_message(self, room_id: str, event_id: str, body: str) -> Message:
        client = self._transport.client(MessageServiceClient)
        resp = await self._call_or_raise(
            client.update_message(
                UpdateMessageRequest(room_id=room_id, event_id=event_id, body=body),
                headers=self._headers(),
            )
        )
        return resp.message

    async def delete_message(self, room_id: str, event_id: str) -> None:
        client = self._transport.client(MessageServiceClient)
        await self._call_or_raise(
            client.delete_message(
                DeleteMessageRequest(room_id=room_id, event_id=event_id),
                headers=self._headers(),
            )
        )

    async def get_message(self, room_id: str, event_id: str) -> Message | None:
        """Fetch one message. Returns ``None`` on ``NOT_FOUND`` -- including a
        message retracted between an event signal and this fetch."""
        client = self._transport.client(MessageServiceClient)
        resp = await self._call_or_none(
            client.get_message(
                GetMessageRequest(room_id=room_id, event_id=event_id),
                headers=self._headers(),
            )
        )
        return resp.message if resp is not None else None

    async def batch_get_messages(
        self, room_id: str, event_ids: list[str]
    ) -> list[Message]:
        """Room-scoped batch fetch, 1-100 ids. Missing/retracted ids are silently
        omitted from the result by the server, not raised as errors."""
        client = self._transport.client(MessageServiceClient)
        resp = await self._call_or_raise(
            client.batch_get_messages(
                BatchGetMessagesRequest(room_id=room_id, event_ids=event_ids),
                headers=self._headers(),
            )
        )
        return list(resp.messages)

    # --- Room timeline ---

    async def get_room_events(
        self,
        room_id: str,
        *,
        limit: int = 50,
        before: str | None = None,
        after: str | None = None,
    ) -> RoomTimelinePage:
        """Fetch a page of room timeline events. ``before``/``after`` are opaque
        server-issued cursors (``RoomTimelinePage.start_cursor``/``end_cursor``),
        not event IDs -- do not construct or parse them."""
        cursor = None
        if before is not None:
            cursor = Oneof("before", before)
        elif after is not None:
            cursor = Oneof("after", after)
        client = self._transport.client(RoomServiceClient)
        resp = await self._call_or_raise(
            client.get_room_events(
                GetRoomEventsRequest(room_id=room_id, limit=limit, cursor=cursor),
                headers=self._headers(),
            )
        )
        return resp.page

    async def get_thread_events(
        self,
        room_id: str,
        thread_root_event_id: str,
        *,
        limit: int = 50,
        before: str | None = None,
        after: str | None = None,
    ) -> RoomTimelinePage:
        """Fetch a page of one thread's timeline events. ``before``/``after`` are
        opaque server-issued cursors (``RoomTimelinePage.start_cursor``/``end_cursor``),
        not event IDs -- do not construct or parse them."""
        cursor = None
        if before is not None:
            cursor = Oneof("before", before)
        elif after is not None:
            cursor = Oneof("after", after)
        client = self._transport.client(ThreadServiceClient)
        resp = await self._call_or_raise(
            client.get_thread_events(
                GetThreadEventsRequest(
                    room_id=room_id,
                    thread_root_event_id=thread_root_event_id,
                    limit=limit,
                    cursor=cursor,
                ),
                headers=self._headers(),
            )
        )
        return resp.page

    async def get_room_events_around(
        self, room_id: str, event_id: str, limit: int = 50
    ) -> RoomTimelinePage:
        client = self._transport.client(RoomServiceClient)
        resp = await self._call_or_raise(
            client.get_room_events_around(
                GetRoomEventsAroundRequest(room_id=room_id, event_id=event_id, limit=limit),
                headers=self._headers(),
            )
        )
        return resp.page

    async def mark_room_as_read(self, room_id: str, up_to_event_id: str) -> None:
        client = self._transport.client(RoomServiceClient)
        await self._call_or_raise(
            client.mark_room_as_read(
                MarkRoomAsReadRequest(room_id=room_id, up_to_event_id=up_to_event_id),
                headers=self._headers(),
            )
        )

    # --- Reactions (MessageService.AddReaction/RemoveReaction) ---

    async def add_reaction(self, room_id: str, message_event_id: str, emoji: str) -> None:
        """``emoji`` is a shortcode (e.g. ``"thumbsup"``), not a literal glyph."""
        client = self._transport.client(MessageServiceClient)
        await self._call_or_raise(
            client.add_reaction(
                AddReactionRequest(
                    room_id=room_id, message_event_id=message_event_id, emoji=emoji
                ),
                headers=self._headers(),
            )
        )

    async def remove_reaction(self, room_id: str, message_event_id: str, emoji: str) -> None:
        client = self._transport.client(MessageServiceClient)
        await self._call_or_raise(
            client.remove_reaction(
                RemoveReactionRequest(
                    room_id=room_id, message_event_id=message_event_id, emoji=emoji
                ),
                headers=self._headers(),
            )
        )

    # --- Users / member directory ---

    async def list_users(self, search: str = "", *, limit: int = 50) -> list[DirectoryMember]:
        client = self._transport.client(UserServiceClient)
        resp = await self._call_or_raise(
            client.list_users(
                ListUsersRequest(search=search, page=PageRequest(limit=limit, offset=0)),
                headers=self._headers(),
            )
        )
        return list(resp.users)

    async def batch_get_users(self, user_ids: list[str]) -> list[DirectoryMember]:
        client = self._transport.client(UserServiceClient)
        resp = await self._call_or_raise(
            client.batch_get_users(
                BatchGetUsersRequest(user_ids=user_ids), headers=self._headers()
            )
        )
        return list(resp.users)

    # --- Presence / typing / read state ---

    async def update_presence(
        self, status: str | PresenceStatus = "PRESENCE_STATUS_ONLINE"
    ) -> None:
        """Set the current user's live presence. Transient -- callers must keep
        refreshing on an interval; ``PRESENCE_STATUS_OFFLINE`` cannot be set
        explicitly (the server rejects it)."""
        client = self._transport.client(MyAccountServiceClient)
        await self._call_or_raise(
            client.update_presence(
                UpdatePresenceRequest(status=_resolve_presence(status)),
                headers=self._headers(),
            )
        )

    async def update_typing_indicator(
        self,
        room_id: str,
        is_typing: bool = True,
        thread_root_event_id: str | None = None,
    ) -> None:
        """Refresh the live-only typing indicator for a room or thread.

        The RPC only supports "I am typing now" (it expires on its own via a
        server-side TTL); there is no proto field for "stopped typing". When
        ``is_typing`` is ``False`` this is a deliberate no-op rather than an error,
        so existing bot code that calls ``update_typing_indicator(room, False)``
        to mean "stop" keeps working without raising.
        """
        if not is_typing:
            return
        client = self._transport.client(RoomServiceClient)
        await self._call_or_raise(
            client.update_typing_indicator(
                UpdateTypingIndicatorRequest(
                    room_id=room_id, thread_root_event_id=thread_root_event_id or ""
                ),
                headers=self._headers(),
            )
        )
