"""Auth/session holder wrapping chattolib's ``ChattoClient``.

Chatto's public API is reached through ``chattolib.ChattoClient`` (Connect JSON
over HTTP, see ``chattolib.client``), which owns its own ``httpx.AsyncClient``
and base URL. ``Transport`` exists one layer above that: it holds the
credentials (bearer token / session cookie / identifier+password) and the
current ``ChattoClient`` instance, and knows how to replace that instance
wholesale on ``relogin()`` -- something ``ChattoClient`` itself has no notion
of, since it's constructed once with a fixed token.
"""

from __future__ import annotations

import logging

from chattolib.client import ChattoClient
from chattolib.exceptions import ChattoAuthError

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Raised when login fails or returns no usable credential."""


class Transport:
    """Owns the instance URL, the current bearer/session credential, and the
    live ``ChattoClient`` built from them.

    ``identifier``/``password`` are optional beyond the documented 3-argument
    constructor shape (``instance, token, session``) so that :meth:`relogin`
    has something to re-authenticate with; callers that only ever set a
    static token (no re-login capability) can omit them.
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
        self.chatto_client = ChattoClient(
            token=self.token, base_url=self.instance, session_cookie=self.session
        )

    @property
    def base_url(self) -> str:
        return self.chatto_client.base_url

    @property
    def ws_url(self) -> str:
        """Kept for interface parity; chattolib.realtime derives its own
        WebSocket URL from ``chatto_client.base_url``, so nothing here reads
        this anymore."""
        base = self.instance
        if base.startswith("https://"):
            base = "wss://" + base[len("https://") :]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://") :]
        return f"{base}/api/realtime"

    def headers(self) -> dict[str, str]:
        """Auth headers for a direct HTTP call: bearer token, else cookie fallback."""
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        if self.session:
            return {"Cookie": f"chatto_session={self.session}"}
        return {}

    async def relogin(self) -> None:
        """Re-run login and replace the stored credential and ``ChattoClient`` in place.

        Used when a Connect call or realtime handshake comes back
        unauthenticated (the bearer token is a revocable runtime credential,
        not a long-lived secret). Requires ``identifier``/``password`` to have
        been supplied at construction time.
        """
        if not (self.identifier and self.password):
            raise AuthError(
                "Cannot re-login: no identifier/password configured on this Transport"
            )
        try:
            new_client = await ChattoClient.login(
                self.identifier, self.password, base_url=self.instance
            )
        except ChattoAuthError as exc:
            raise AuthError(str(exc)) from exc

        old_client = self.chatto_client
        self.chatto_client = new_client
        self.token = new_client.token
        self.session = new_client.session_cookie
        await old_client.close()
        logger.info("Re-authenticated with %s after credential expiry", self.instance)

    async def close(self) -> None:
        """Close the underlying ``ChattoClient``. Safe to call multiple times."""
        await self.chatto_client.close()
