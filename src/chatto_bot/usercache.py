"""Actor/user cache: cache-through lookups over ``Client.batch_get_users``.

Realtime envelopes carry only IDs (``actor_id``, ``user_id``, ...), never full
user profiles. ``Hydrator`` resolves those IDs through this cache instead of
issuing a request per event, and invalidates entries when the realtime stream
tells us a profile or presence changed.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chattolib.types import User
    from .client import Client


def _unwrap_user(obj: Any) -> User:
    """Pull the ``User`` dataclass out of whatever ``batch_get_users`` returns.

    The interface contract types ``Client.batch_get_users`` as returning
    ``list[User]``, but chattolib's member-directory-backed implementation
    returns ``list[DirectoryMember]`` (``DirectoryMember.user`` plus
    roles/created_at). Accept either shape so this cache doesn't care which
    one the client settles on.
    """
    user = getattr(obj, "user", None)
    return user if user is not None else obj


class UserCache:
    """Cache-through ``User`` lookup used to resolve realtime actor IDs."""

    def __init__(self, client: Client) -> None:
        self._client = client
        self._cache: dict[str, User] = {}
        # Guards concurrent get_many() calls from double-fetching the same
        # missing IDs when multiple envelopes hydrate at once.
        self._lock = asyncio.Lock()

    async def get(self, user_id: str) -> User | None:
        """Look up one user, fetching (and caching) on a miss."""
        if not user_id:
            return None
        cached = self._cache.get(user_id)
        if cached is not None:
            return cached
        resolved = await self.get_many([user_id])
        return resolved.get(user_id)

    async def get_many(self, ids: list[str]) -> dict[str, User]:
        """Look up many users, fetching only the ones not already cached."""
        wanted = [i for i in dict.fromkeys(ids) if i]
        missing = [i for i in wanted if i not in self._cache]
        if missing:
            async with self._lock:
                # Re-check after acquiring the lock: a concurrent get_many()
                # may have already filled these in while we were waiting.
                missing = [i for i in missing if i not in self._cache]
                if missing:
                    fetched = await self._client.batch_get_users(missing)
                    for raw in fetched:
                        user = _unwrap_user(raw)
                        if user is not None and user.id:
                            self._cache[user.id] = user
        return {i: self._cache[i] for i in wanted if i in self._cache}

    def invalidate(self, user_id: str) -> None:
        """Drop a cached entry so the next lookup refetches it.

        Call this on ``user_profile_updated`` / ``presence_changed`` realtime
        events for the affected ``user_id``.
        """
        self._cache.pop(user_id, None)
