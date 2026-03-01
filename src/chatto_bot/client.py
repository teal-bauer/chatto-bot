"""Async GraphQL HTTP client for Chatto API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

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
            mutation UpdatePresence($status: PresenceStatus!) {
                updateMyPresence(status: $status)
            }
            """,
            {"status": status},
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
        """Post a message and return the SpaceEvent."""
        variables: dict[str, Any] = {
            "input": {
                "spaceId": space_id,
                "roomId": room_id,
                "body": body,
            }
        }
        if in_reply_to:
            variables["input"]["inReplyTo"] = in_reply_to

        data = await self.mutate(
            """
            mutation PostMessage($input: PostMessageInput!) {
                postMessage(input: $input) {
                    id sequenceId createdAt actorId
                    actor { id login displayName }
                    event {
                        ... on MessagePostedEvent {
                            spaceId roomId body messageBodyId
                            inReplyTo inThread
                        }
                    }
                }
            }
            """,
            variables,
        )
        return data["postMessage"]

    async def edit_message(
        self,
        space_id: str,
        room_id: str,
        message_body_id: str,
        body: str,
    ) -> bool:
        data = await self.mutate(
            """
            mutation EditMessage($spaceId: ID!, $roomId: ID!, $messageBodyId: ID!, $body: String!) {
                editMessage(spaceId: $spaceId, roomId: $roomId, messageBodyId: $messageBodyId, body: $body)
            }
            """,
            {
                "spaceId": space_id,
                "roomId": room_id,
                "messageBodyId": message_body_id,
                "body": body,
            },
        )
        return data["editMessage"]

    async def delete_message(
        self, space_id: str, room_id: str, message_body_id: str
    ) -> bool:
        data = await self.mutate(
            """
            mutation DeleteMessage($spaceId: ID!, $roomId: ID!, $messageBodyId: ID!) {
                deleteMessage(spaceId: $spaceId, roomId: $roomId, messageBodyId: $messageBodyId)
            }
            """,
            {
                "spaceId": space_id,
                "roomId": room_id,
                "messageBodyId": message_body_id,
            },
        )
        return data["deleteMessage"]

    async def add_reaction(
        self, space_id: str, room_id: str, message_event_id: str, emoji: str
    ) -> bool:
        data = await self.mutate(
            """
            mutation AddReaction($spaceId: ID!, $roomId: ID!, $messageEventId: ID!, $emoji: String!) {
                addReaction(spaceId: $spaceId, roomId: $roomId, messageEventId: $messageEventId, emoji: $emoji)
            }
            """,
            {
                "spaceId": space_id,
                "roomId": room_id,
                "messageEventId": message_event_id,
                "emoji": emoji,
            },
        )
        return data["addReaction"]

    async def remove_reaction(
        self, space_id: str, room_id: str, message_event_id: str, emoji: str
    ) -> bool:
        data = await self.mutate(
            """
            mutation RemoveReaction($spaceId: ID!, $roomId: ID!, $messageEventId: ID!, $emoji: String!) {
                removeReaction(spaceId: $spaceId, roomId: $roomId, messageEventId: $messageEventId, emoji: $emoji)
            }
            """,
            {
                "spaceId": space_id,
                "roomId": room_id,
                "messageEventId": message_event_id,
                "emoji": emoji,
            },
        )
        return data["removeReaction"]

    async def start_dm(self, participant_ids: list[str]) -> dict:
        """Start or get an existing DM room."""
        data = await self.mutate(
            """
            mutation StartDM($participantIds: [ID!]!) {
                startDM(participantIds: $participantIds) {
                    id name spaceId
                    members { user { id login displayName } }
                }
            }
            """,
            {"participantIds": participant_ids},
        )
        return data["startDM"]

    async def join_room(self, space_id: str, room_id: str) -> bool:
        data = await self.mutate(
            """
            mutation JoinRoom($spaceId: ID!, $roomId: ID!) {
                joinRoom(spaceId: $spaceId, roomId: $roomId)
            }
            """,
            {"spaceId": space_id, "roomId": room_id},
        )
        return data["joinRoom"]

    async def leave_room(self, space_id: str, room_id: str) -> bool:
        data = await self.mutate(
            """
            mutation LeaveRoom($spaceId: ID!, $roomId: ID!) {
                leaveRoom(spaceId: $spaceId, roomId: $roomId)
            }
            """,
            {"spaceId": space_id, "roomId": room_id},
        )
        return data["leaveRoom"]

    async def join_space(self, space_id: str) -> bool:
        data = await self.mutate(
            """
            mutation JoinSpace($spaceId: ID!) {
                joinSpace(spaceId: $spaceId)
            }
            """,
            {"spaceId": space_id},
        )
        return data["joinSpace"]

    async def leave_space(self, space_id: str) -> bool:
        data = await self.mutate(
            """
            mutation LeaveSpace($spaceId: ID!) {
                leaveSpace(spaceId: $spaceId)
            }
            """,
            {"spaceId": space_id},
        )
        return data["leaveSpace"]

    async def get_rooms(self, space_id: str) -> list[dict]:
        """Get all rooms the bot is a member of in a space."""
        data = await self.query(
            """
            query GetRooms($spaceId: ID!) {
                space(id: $spaceId) {
                    rooms { id name archived }
                }
            }
            """,
            {"spaceId": space_id},
        )
        space = data.get("space")
        if not space:
            return []
        return [r for r in space.get("rooms", []) if not r.get("archived")]

    async def search_space_members(
        self, space_id: str, search: str, limit: int = 5
    ) -> list[dict]:
        """Search for members in a space by display name."""
        data = await self.query(
            """
            query SearchMembers($spaceId: ID!, $search: String!, $limit: Int) {
                space(id: $spaceId) {
                    members(search: $search, limit: $limit) {
                        users { id login displayName }
                    }
                }
            }
            """,
            {"spaceId": space_id, "search": search, "limit": limit},
        )
        space = data.get("space")
        if not space:
            return []
        return space.get("members", {}).get("users", [])

    async def get_room_events(
        self,
        space_id: str,
        room_id: str,
        limit: int = 50,
    ) -> list[dict]:
        """Fetch recent events from a room."""
        data = await self.query(
            """
            query RoomEvents($spaceId: ID!, $roomId: ID!, $limit: Int) {
                roomEvents(spaceId: $spaceId, roomId: $roomId, limit: $limit) {
                    id createdAt actorId sequenceId
                    actor { id login displayName avatarUrl presenceStatus }
                    event {
                        __typename
                        ... on MessagePostedEvent {
                            spaceId roomId body messageBodyId
                            attachments { id filename contentType size width height url }
                            inReplyTo inThread
                            reactions { emoji count users { id login displayName } hasReacted }
                            updatedAt replyCount lastReplyAt
                        }
                        ... on MessageUpdatedEvent {
                            spaceId roomId body messageBodyId
                            attachments { id filename contentType size width height url }
                            reactions { emoji count users { id login displayName } hasReacted }
                        }
                        ... on MessageDeletedEvent { spaceId roomId messageBodyId }
                        ... on ReactionAddedEvent { spaceId roomId messageEventId emoji }
                        ... on ReactionRemovedEvent { spaceId roomId messageEventId emoji }
                        ... on UserJoinedRoomEvent { spaceId roomId }
                        ... on UserLeftRoomEvent { spaceId roomId }
                    }
                }
            }
            """,
            {"spaceId": space_id, "roomId": room_id, "limit": limit},
        )
        return data.get("roomEvents", [])
