"""Async GraphQL HTTP client for Chatto API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from ._queries import ROOM_EVENT_FRAGMENT

if TYPE_CHECKING:
    from .config import BotConfig

logger = logging.getLogger(__name__)


class GraphQLError(Exception):
    """Raised when the GraphQL response contains errors."""

    def __init__(self, errors: list[dict], data: Any = None):
        self.errors = errors
        self.data = data
        messages = [e.get("message", str(e)) for e in errors]
        super().__init__(f"GraphQL errors: {'; '.join(messages)}")


async def login(instance: str, identifier: str, password: str) -> str:
    """Log in with email/password and return the session cookie value."""
    url = f"{instance.rstrip('/')}/auth/login"
    async with httpx.AsyncClient() as http:
        resp = await http.post(url, json={"identifier": identifier, "password": password})
        resp.raise_for_status()
        session = resp.cookies.get("chatto_session")
        if not session:
            raise RuntimeError("Login succeeded but no session cookie returned")
        return session


class Client:
    """Async GraphQL HTTP client with cookie auth."""

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self._url = config.graphql_url
        self._http = httpx.AsyncClient(
            headers={
                "Content-Type": "application/json",
                "Cookie": config.cookie_header,
                "Origin": config.instance,
                "Accept": "application/graphql-response+json, application/json",
            },
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def execute(
        self, query: str, variables: dict | None = None
    ) -> dict[str, Any]:
        """Execute a GraphQL query/mutation and return the data dict."""
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        resp = await self._http.post(self._url, json=payload)
        resp.raise_for_status()
        result = resp.json()

        if errors := result.get("errors"):
            raise GraphQLError(errors, result.get("data"))

        return result.get("data", {})

    async def query(
        self, query: str, variables: dict | None = None
    ) -> dict[str, Any]:
        return await self.execute(query, variables)

    async def mutate(
        self, query: str, variables: dict | None = None
    ) -> dict[str, Any]:
        return await self.execute(query, variables)

    # --- Convenience methods ---

    async def me(self) -> dict | None:
        """Get the current authenticated user."""
        data = await self.query(
            "{ me { id login displayName avatarUrl presenceStatus } }"
        )
        return data.get("me")

    async def update_presence(self, status: str = "ONLINE") -> bool:
        """Set the bot's presence status."""
        data = await self.mutate(
            """
            mutation UpdatePresence($input: UpdateMyPresenceInput!) {
                updateMyPresence(input: $input)
            }
            """,
            {"input": {"status": status}},
        )
        return data["updateMyPresence"]

    async def post_message(
        self,
        space_id: str,
        room_id: str,
        body: str,
        *,
        in_reply_to: str | None = None,
    ) -> dict:
        """Post a message and return the wrapper RoomEvent.

        ``space_id`` is accepted for backward compatibility but ignored by
        the current Chatto schema.
        """
        del space_id
        variables: dict[str, Any] = {
            "input": {
                "roomId": room_id,
                "body": body,
            }
        }
        if in_reply_to:
            variables["input"]["inReplyTo"] = in_reply_to

        data = await self.mutate(
            ROOM_EVENT_FRAGMENT + """
            mutation PostMessage($input: PostMessageInput!) {
                postMessage(input: $input) { ...RoomEventFields }
            }
            """,
            variables,
        )
        return data["postMessage"]

    async def edit_message(
        self,
        space_id: str,
        room_id: str,
        event_id: str,
        body: str,
    ) -> bool:
        del space_id
        data = await self.mutate(
            """
            mutation EditMessage($input: EditMessageInput!) {
                editMessage(input: $input)
            }
            """,
            {"input": {"roomId": room_id, "eventId": event_id, "body": body}},
        )
        return data["editMessage"]

    async def delete_message(
        self, space_id: str, room_id: str, event_id: str
    ) -> bool:
        del space_id
        data = await self.mutate(
            """
            mutation DeleteMessage($input: DeleteMessageInput!) {
                deleteMessage(input: $input)
            }
            """,
            {"input": {"roomId": room_id, "eventId": event_id}},
        )
        return data["deleteMessage"]

    async def add_reaction(
        self, space_id: str, room_id: str, message_event_id: str, emoji: str
    ) -> bool:
        del space_id
        data = await self.mutate(
            """
            mutation AddReaction($input: AddReactionInput!) {
                addReaction(input: $input)
            }
            """,
            {"input": {"roomId": room_id, "messageEventId": message_event_id, "emoji": emoji}},
        )
        return data["addReaction"]

    async def remove_reaction(
        self, space_id: str, room_id: str, message_event_id: str, emoji: str
    ) -> bool:
        del space_id
        data = await self.mutate(
            """
            mutation RemoveReaction($input: RemoveReactionInput!) {
                removeReaction(input: $input)
            }
            """,
            {"input": {"roomId": room_id, "messageEventId": message_event_id, "emoji": emoji}},
        )
        return data["removeReaction"]

    async def start_dm(self, participant_ids: list[str]) -> dict:
        """Start or get an existing DM room."""
        data = await self.mutate(
            """
            mutation StartDM($input: StartDMInput!) {
                startDM(input: $input) {
                    id name
                    members { id login displayName }
                }
            }
            """,
            {"input": {"participantIds": participant_ids}},
        )
        return data["startDM"]

    async def join_room(self, space_id: str, room_id: str) -> bool:
        del space_id
        data = await self.mutate(
            """
            mutation JoinRoom($input: JoinRoomInput!) {
                joinRoom(input: $input)
            }
            """,
            {"input": {"roomId": room_id}},
        )
        return data["joinRoom"]

    async def leave_room(self, space_id: str, room_id: str) -> bool:
        del space_id
        data = await self.mutate(
            """
            mutation LeaveRoom($input: LeaveRoomInput!) {
                leaveRoom(input: $input)
            }
            """,
            {"input": {"roomId": room_id}},
        )
        return data["leaveRoom"]

    async def get_rooms(self, space_id: str = "") -> list[dict]:
        """Return all rooms visible to the bot, with joined/type metadata.

        ``space_id`` is accepted for backward compatibility but ignored —
        the API no longer scopes rooms by space.
        """
        del space_id
        data = await self.query(
            """
            {
                instance {
                    rooms {
                        id name type archived
                        viewerCanPostMessage
                    }
                }
                me {
                    rooms { id }
                }
            }
            """
        )
        instance = data.get("instance") or {}
        rooms = [r for r in (instance.get("rooms") or []) if not r.get("archived")]
        joined_ids = {r["id"] for r in (data.get("me") or {}).get("rooms", [])}
        for r in rooms:
            r["joined"] = r["id"] in joined_ids
        return rooms

    async def search_members(self, search: str, limit: int = 5) -> list[dict]:
        """Search for members in the instance by display name."""
        data = await self.query(
            """
            query SearchMembers($search: String!, $limit: Int) {
                instance {
                    members(search: $search, limit: $limit) {
                        users { id login displayName }
                    }
                }
            }
            """,
            {"search": search, "limit": limit},
        )
        instance = data.get("instance") or {}
        return (instance.get("members") or {}).get("users") or []

    async def search_space_members(
        self, space_id: str, search: str, limit: int = 5
    ) -> list[dict]:
        """Backward-compat wrapper around :meth:`search_members`."""
        del space_id
        return await self.search_members(search, limit)

    async def get_room_events(
        self,
        space_id: str,
        room_id: str,
        limit: int = 50,
        *,
        before: str | None = None,
        after: str | None = None,
    ) -> dict:
        """Fetch recent events from a room.

        Returns a connection dict ``{"events": [...], "hasOlder": bool,
        "hasNewer": bool}``. Use ``before`` / ``after`` event IDs for paging.
        ``space_id`` is accepted for backward compatibility but ignored.
        """
        del space_id
        variables: dict[str, Any] = {
            "roomId": room_id,
            "limit": limit,
        }
        params = "$roomId: ID!, $limit: Int"
        args = "roomId: $roomId, limit: $limit"
        if before is not None:
            params += ", $before: String"
            args += ", before: $before"
            variables["before"] = before
        if after is not None:
            params += ", $after: String"
            args += ", after: $after"
            variables["after"] = after

        data = await self.query(
            ROOM_EVENT_FRAGMENT + f"""
            query RoomEvents({params}) {{
                roomEvents({args}) {{
                    events {{ ...RoomEventFields }}
                    hasOlder hasNewer
                }}
            }}
            """,
            variables,
        )
        return data.get("roomEvents") or {"events": [], "hasOlder": False, "hasNewer": False}

    async def get_event(
        self, space_id: str, room_id: str, event_id: str
    ) -> dict | None:
        """Fetch a single RoomEvent by id (e.g. to refetch after MessageUpdated)."""
        del space_id
        data = await self.query(
            ROOM_EVENT_FRAGMENT + """
            query GetEvent($roomId: ID!, $eventId: ID!) {
                roomEventByEventId(roomId: $roomId, eventId: $eventId) {
                    ...RoomEventFields
                }
            }
            """,
            {"roomId": room_id, "eventId": event_id},
        )
        return data.get("roomEventByEventId")
