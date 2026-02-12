"""Cog base class for grouping related bot functionality."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .command import Command
from .event import EventHandler

if TYPE_CHECKING:
    from .bot import Bot


class Cog:
    """Base class for bot extensions (cogs).

    Subclass this and define commands/event handlers as methods.
    Commands use the @command() decorator, event handlers use @on_event().
    """

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.__cog_name__ = type(self).__name__
        self.__cog_commands__: list[Command] = []
        self.__cog_event_handlers__: list[EventHandler] = []

        # Discover commands and event handlers on the class
        for name in dir(type(self)):
            value = getattr(type(self), name, None)
            if isinstance(value, Command):
                # Clone the command so each cog instance has its own
                cmd = Command(
                    name=value.name,
                    callback=value.callback,
                    description=value.description,
                    aliases=list(value.aliases),
                    cog=self,
                )
                self.__cog_commands__.append(cmd)
            elif isinstance(value, EventHandler):
                handler = EventHandler(
                    event_type=value.event_type,
                    callback=value.callback,
                    filters=dict(value.filters),
                    cog=self,
                )
                self.__cog_event_handlers__.append(handler)

    async def cog_load(self) -> None:
        """Called when the cog is loaded. Override for setup."""

    async def cog_unload(self) -> None:
        """Called when the cog is unloaded. Override for cleanup."""
