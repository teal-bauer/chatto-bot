"""Configuration loading from YAML files and environment variables."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def _load_dotenv() -> None:
    """Load a .env file from the current directory if present (no dependency)."""
    env_path = Path(".env")
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Strip surrounding quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            else:
                # Strip inline comments (only for unquoted values)
                if " #" in value:
                    value = value[: value.index(" #")].rstrip()
            # Don't override existing env vars
            if key not in os.environ:
                os.environ[key] = value


@dataclass
class BotConfig:
    instance: str = "https://dev.chatto.run"
    prefix: str = "!"
    rooms: list[str] = field(default_factory=list)  # allowlist; empty = all rooms
    admins: list[str] = field(default_factory=list)  # login names of admin users
    extensions: list[str] = field(default_factory=list)
    log_level: str = "INFO"
    token: str = ""
    session: str = ""
    email: str = ""
    password: str = ""
    dms: bool = True

    @classmethod
    def load(
        cls,
        config_path: str | Path | None = None,
        *,
        instance: str | None = None,
        prefix: str | None = None,
        spaces: list[str] | None = None,
        token: str | None = None,
        session: str | None = None,
        email: str | None = None,
        password: str | None = None,
        dms: bool | None = None,
    ) -> BotConfig:
        """Load config from YAML file, then overlay env vars, then explicit args.

        ``spaces`` is accepted only so old call sites and config files don't crash
        outright; it's deprecated (see ``_warn_if_spaces`` below) and never applied.
        """
        data: dict = {}

        # 0. Load .env file if present (before reading env vars)
        _load_dotenv()

        # 1. YAML file (optional)
        if config_path:
            path = Path(config_path)
            if path.exists():
                with open(path) as f:
                    data = yaml.safe_load(f) or {}

        _warn_if_spaces(data.get("spaces"), source="config file")

        config = cls(
            instance=data.get("instance", cls.instance),
            prefix=data.get("prefix", cls.prefix),
            rooms=data.get("rooms", []),
            admins=data.get("admins", []),
            extensions=data.get("extensions", []),
            log_level=data.get("log_level", cls.log_level),
            token=data.get("token", cls.token),
            email=data.get("email", cls.email),
            password=data.get("password", cls.password),
            dms=data.get("dms", cls.dms),
        )

        # 2. Environment variables
        if env_instance := os.environ.get("CHATTO_INSTANCE"):
            config.instance = env_instance
        if env_prefix := os.environ.get("CHATTO_PREFIX"):
            config.prefix = env_prefix
        _warn_if_spaces(os.environ.get("CHATTO_SPACES"), source="CHATTO_SPACES env var")
        if env_rooms := os.environ.get("CHATTO_ROOMS"):
            config.rooms = [r.strip() for r in env_rooms.split(",") if r.strip()]
        if env_admins := os.environ.get("CHATTO_ADMINS"):
            config.admins = [a.strip() for a in env_admins.split(",") if a.strip()]
        if env_token := os.environ.get("CHATTO_TOKEN"):
            config.token = env_token
        config.session = os.environ.get("CHATTO_SESSION", "")
        if env_email := os.environ.get("CHATTO_EMAIL"):
            config.email = env_email
        if env_password := os.environ.get("CHATTO_PASSWORD"):
            config.password = env_password
        if env_dms := os.environ.get("CHATTO_DMS"):
            config.dms = env_dms.lower() not in ("0", "false", "no")

        # 3. Explicit arguments (highest priority)
        if instance is not None:
            config.instance = instance
        if prefix is not None:
            config.prefix = prefix
        _warn_if_spaces(spaces, source="explicit argument")
        if token is not None:
            config.token = token
        if session is not None:
            config.session = session
        if email is not None:
            config.email = email
        if password is not None:
            config.password = password
        if dms is not None:
            config.dms = dms

        return config

    @property
    def connect_url(self) -> str:
        return f"{self.instance.rstrip('/')}/api/connect"

    @property
    def ws_url(self) -> str:
        base = self.instance.rstrip("/")
        if base.startswith("https://"):
            base = "wss://" + base[8:]
        elif base.startswith("http://"):
            base = "ws://" + base[7:]
        return f"{base}/api/realtime"

    @property
    def cookie_header(self) -> str:
        return f"chatto_session={self.session}"


def _warn_if_spaces(value: object, *, source: str) -> None:
    """``spaces:`` was never consulted by dispatch (only ``rooms:`` was), and space
    names aren't room IDs, so blindly reinterpreting it would silently match
    nothing. Warn once per call site and ignore it instead."""
    if not value:
        return
    logger.warning(
        "`spaces` is removed and ignored (from %s); use `rooms:` instead. "
        "DMs are now rooms too, so most `spaces:` allowlists have no equivalent.",
        source,
    )
