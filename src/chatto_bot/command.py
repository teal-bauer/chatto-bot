"""Command decorator and argument parsing from type hints."""

from __future__ import annotations

import inspect
import shlex
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import Context


@dataclass
class Command:
    """A registered bot command."""

    name: str
    callback: Callable[..., Awaitable[None]]
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    hidden: bool = False
    cog: Any = None  # set when registered via a Cog

    @property
    def qualified_name(self) -> str:
        return self.name

    @property
    def signature(self) -> str:
        """Generate a usage signature from the function's type hints."""
        sig = inspect.signature(self.callback)
        parts = []
        skip = 1 if self.cog is None else 2  # skip ctx (and self for cogs)
        for i, (name, param) in enumerate(sig.parameters.items()):
            if i < skip:
                continue
            if param.default is inspect.Parameter.empty:
                parts.append(f"<{name}>")
            else:
                parts.append(f"[{name}={param.default}]")
        return " ".join(parts)

    @property
    def help_text(self) -> str:
        """Help text from docstring or description."""
        if self.description:
            return self.description
        doc = self.callback.__doc__
        if doc:
            return doc.strip().split("\n")[0]
        return ""

    async def invoke(self, ctx: Context, args_str: str) -> None:
        """Parse arguments from the message and invoke the command."""
        sig = inspect.signature(self.callback)
        skip = 1 if self.cog is None else 2
        params = list(sig.parameters.values())[skip:]

        # Parse args from the message text
        parsed_args = _parse_args(args_str, params)

        if self.cog is not None:
            await self.callback(self.cog, ctx, *parsed_args)
        else:
            await self.callback(ctx, *parsed_args)


def _parse_args(
    args_str: str, params: list[inspect.Parameter]
) -> list[Any]:
    """Parse a string of arguments according to parameter type hints."""
    if not params:
        return []

    try:
        raw_args = shlex.split(args_str) if args_str.strip() else []
    except ValueError:
        raw_args = args_str.split() if args_str.strip() else []

    result: list[Any] = []
    for i, param in enumerate(params):
        annotation = param.annotation
        if annotation is inspect.Parameter.empty:
            annotation = str

        if i < len(raw_args):
            # Check if this is the last parameter and there are remaining args
            if i == len(params) - 1 and len(raw_args) > len(params):
                # Last param gets the rest of the string joined
                raw_value = " ".join(raw_args[i:])
            else:
                raw_value = raw_args[i]
            result.append(_convert_arg(raw_value, annotation))
        elif param.default is not inspect.Parameter.empty:
            result.append(param.default)
        else:
            raise CommandError(
                f"Missing required argument: {param.name}"
            )

    return result


def _convert_arg(value: str, target_type: type) -> Any:
    """Convert a string argument to the target type."""
    if target_type is str:
        return value
    if target_type is int:
        try:
            return int(value)
        except ValueError:
            raise CommandError(f"Expected integer, got: {value}")
    if target_type is float:
        try:
            return float(value)
        except ValueError:
            raise CommandError(f"Expected number, got: {value}")
    if target_type is bool:
        return value.lower() in ("true", "yes", "1", "on")
    return value


class CommandError(Exception):
    """Raised when command parsing or execution fails."""


def command(
    name: str | None = None,
    *,
    desc: str = "",
    aliases: list[str] | None = None,
    hidden: bool = False,
) -> Callable:
    """Decorator to mark a function as a bot command.

    Can be used on standalone functions (registered on Bot) or
    on methods inside a Cog subclass.
    """

    def decorator(func: Callable[..., Awaitable[None]]) -> Command:
        cmd = Command(
            name=name or func.__name__,
            callback=func,
            description=desc,
            aliases=aliases or [],
            hidden=hidden,
        )
        # Attach the Command object so Bot/Cog can discover it
        func.__command__ = cmd  # type: ignore[attr-defined]
        return cmd  # type: ignore[return-value]

    return decorator
