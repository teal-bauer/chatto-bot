"""ConnectRPC transport: base URL, bearer/cookie auth, and generated-client factory.

Chatto's public API is ConnectRPC (protobuf over HTTP) at ``{instance}/api/connect``,
plus a plain REST ``POST {instance}/auth/login`` that predates the ConnectRPC switch
and still returns a bearer token (and, as a fallback, a ``chatto_session`` cookie).

Generated ``*ServiceClient`` classes (see ``_pb/chatto/**/*_connect.py``) are cheap
wrappers around a per-instance HTTP client with no per-request state -- the request
headers (bearer token) are passed on every call, not baked into the client at
construction time. That means one instance per service class can be built lazily and
held for the lifetime of the bot; :meth:`Transport.client` does exactly that instead
of the ``async with ServiceClient(...) as client:`` pattern used by the throwaway
``scripts/spike_discovery.py`` script, which only needed the client for one call.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, TypeVar

import httpx
from connectrpc.client import ConnectClient

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

C = TypeVar("C", bound=ConnectClient)


class AuthError(Exception):
    """Raised when ``POST /auth/login`` fails or returns no usable credential."""


class Transport:
    """Owns the Connect RPC base URL, the current bearer/session credential, and a
    small cache of long-lived generated service clients.

    ``identifier``/``password`` are optional beyond the documented 3-argument
    constructor shape (``instance, token, session``) so that :meth:`relogin` has
    something to re-authenticate with; callers that only ever set a static token
    (no re-login capability) can omit them.
    """

    def __init__(
        self,
        instance: str,
        token: str | None = None,
        session: str | None = None,
        *,
        identifier: str | None = None,
        password: str | None = None,
    ) -> None:
        self.instance = instance.rstrip("/")
        self.token = token or None
        self.session = session or None
        self.identifier = identifier
        self.password = password
        self._clients: dict[type[ConnectClient], ConnectClient] = {}

    @property
    def base_url(self) -> str:
        return f"{self.instance}/api/connect"

    @property
    def ws_url(self) -> str:
        base = self.instance
        if base.startswith("https://"):
            base = "wss://" + base[len("https://") :]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://") :]
        return f"{base}/api/realtime"

    def headers(self) -> dict[str, str]:
        """Auth headers for a ConnectRPC call: bearer token, else cookie fallback."""
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        if self.session:
            return {"Cookie": f"chatto_session={self.session}"}
        return {}

    def client(self, client_cls: type[C]) -> C:
        """Return the shared, long-lived generated Connect client for ``client_cls``,
        constructing it on first use."""
        existing = self._clients.get(client_cls)
        if existing is not None:
            return existing  # type: ignore[return-value]
        created = client_cls(self.base_url)
        self._clients[client_cls] = created
        return created

    async def relogin(self) -> None:
        """Re-run the REST login and replace the stored bearer token in place.

        Used when a ConnectRPC or realtime call comes back ``UNAUTHENTICATED``
        (the bearer token is a revocable runtime credential, not a long-lived
        secret). Requires ``identifier``/``password`` to have been supplied at
        construction time.
        """
        if not (self.identifier and self.password):
            raise AuthError(
                "Cannot re-login: no identifier/password configured on this Transport"
            )
        token, session = await self.login(self.instance, self.identifier, self.password)
        self.token = token or None
        if session:
            self.session = session
        logger.info("Re-authenticated with %s after credential expiry", self.instance)

    @staticmethod
    async def login(instance: str, identifier: str, password: str) -> tuple[str, str]:
        """POST ``{instance}/auth/login`` and return ``(token, session_cookie)``.

        Either may be empty, but not both -- the server returns a bearer ``token``
        field in the JSON body and, for backward compatibility, still sets a
        ``chatto_session`` cookie via ``Set-Cookie``.
        """
        url = f"{instance.rstrip('/')}/auth/login"
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                url, json={"identifier": identifier, "password": password}
            )
            resp.raise_for_status()
            data: Mapping[str, object] = resp.json()
            token = str(data.get("token") or "")
            session = resp.cookies.get("chatto_session") or ""
            if not token and not session:
                raise AuthError("Login succeeded but no token or session cookie returned")
            return token, session

    async def close(self) -> None:
        """Close cached generated clients. Safe to call multiple times."""
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
