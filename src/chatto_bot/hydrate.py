"""Turn realtime invalidation-signal envelopes into hydrated objects.

Realtime events (``chatto.realtime.v1.RealtimeEventEnvelope``) are signals
first: most carry only IDs and small inline hints, not the full renderable
objects handlers expect (see the hydration notes on each message in
``realtime.proto``). This module fetches those full objects -- a rendered
``Message`` for message events, a resolved actor ``User`` -- before the bot
builds a ``Context`` and dispatches to handlers.

Gating (whether to hydrate a given envelope at all, e.g. "only if a handler
is registered") is the caller's job, not this module's: ``Hydrator.hydrate``
unconditionally does the fetch/resolve work it's asked to do.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .types import event_name

if TYPE_CHECKING:
    from ._pb.chatto.api.v1.message_types_pb import Message
    from ._pb.chatto.api.v1.users_pb import User
    from ._pb.chatto.realtime.v1.realtime_pb import RealtimeEventEnvelope
    from .client import Client
    from .usercache import UserCache

logger = logging.getLogger(__name__)

# message_edited is dispatched under the "message_updated" handler name (see
# types._ONEOF_RENAMES), but the oneof field name below is what
# envelope.event.field actually reports -- that's what we match on.
_MESSAGE_FETCH_FIELDS = frozenset({"message_posted", "message_edited"})


@dataclass
class HydratedEvent:
    """Envelope plus whatever was fetched to satisfy dispatch.

    ``message``/``actor`` are the GENERATED proto objects (not the public
    dataclasses in ``types.py``) -- the caller building ``Context`` passes
    these through ``types.parse_envelope`` to get the public ``RoomEvent``.
    """

    envelope: RealtimeEventEnvelope
    event_name: str
    message: Message | None = None
    actor: User | None = None


class Hydrator:
    """Fetches full objects for invalidation-signal realtime events."""

    def __init__(self, client: Client, users: UserCache) -> None:
        self._client = client
        self._users = users

    async def hydrate(self, envelope: RealtimeEventEnvelope) -> HydratedEvent | None:
        """Hydrate one envelope, or return None if it should be dropped.

        message_posted/message_edited fetch the current ``Message`` via
        ``Client.get_message``; if that comes back None (retracted between
        the signal and our fetch, or otherwise gone) the whole dispatch is
        dropped rather than handed to handlers with a missing message.
        """
        name = event_name(envelope)
        oneof = envelope.event
        payload = oneof.value if oneof is not None else None
        field_name = oneof.field if oneof is not None else None

        message: Message | None = None
        if field_name in _MESSAGE_FETCH_FIELDS and payload is not None:
            room_id = getattr(payload, "room_id", "")
            message_event_id = getattr(payload, "message_event_id", "")
            # TODO: batch via Client.batch_get_messages when dispatching a
            # burst of envelopes from the same room instead of fetching one
            # message at a time.
            message = await self._client.get_message(room_id, message_event_id)
            if message is None:
                logger.debug(
                    "Dropping %s for %s/%s: message not found (retracted?)",
                    name,
                    room_id,
                    message_event_id,
                )
                return None

        actor: User | None = None
        if envelope.actor_id:
            actor = await self._users.get(envelope.actor_id)

        return HydratedEvent(
            envelope=envelope,
            event_name=name,
            message=message,
            actor=actor,
        )
