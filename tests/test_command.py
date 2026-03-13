"""Tests for command.py — argument parsing and type coercion."""

import pytest
from unittest.mock import AsyncMock

from chatto_bot.command import Command, CommandError, command, _parse_args, _convert_arg
from conftest import make_ctx


class TestConvertArg:
    def test_str(self):
        assert _convert_arg("hello", str) == "hello"

    def test_int(self):
        assert _convert_arg("42", int) == 42

    def test_int_invalid(self):
        with pytest.raises(CommandError, match="Expected integer"):
            _convert_arg("abc", int)

    def test_float(self):
        assert _convert_arg("3.14", float) == pytest.approx(3.14)

    def test_float_invalid(self):
        with pytest.raises(CommandError, match="Expected number"):
            _convert_arg("abc", float)

    def test_bool_true(self):
        for val in ("true", "yes", "1", "on", "True", "YES"):
            assert _convert_arg(val, bool) is True

    def test_bool_false(self):
        for val in ("false", "no", "0", "off", "anything"):
            assert _convert_arg(val, bool) is False

    def test_unknown_type_returns_str(self):
        assert _convert_arg("hello", list) == "hello"


class TestParseArgs:
    def test_no_params(self):
        import inspect
        assert _parse_args("anything", []) == []

    def test_single_string(self):
        import inspect
        params = [inspect.Parameter("name", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
        assert _parse_args("hello", params) == ["hello"]

    def test_missing_required_arg(self):
        import inspect
        params = [inspect.Parameter("name", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
        with pytest.raises(CommandError, match="Missing required"):
            _parse_args("", params)

    def test_default_value_used(self):
        import inspect
        params = [
            inspect.Parameter("name", inspect.Parameter.POSITIONAL_OR_KEYWORD, default="world"),
        ]
        assert _parse_args("", params) == ["world"]

    def test_last_param_captures_rest(self):
        import inspect
        params = [
            inspect.Parameter("cmd", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("msg", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ]
        assert _parse_args("greet hello world there", params) == ["greet", "hello world there"]


class TestCommandInvoke:
    @pytest.mark.asyncio
    async def test_invoke_no_args(self, bot):
        called = False

        async def ping(ctx):
            nonlocal called
            called = True

        cmd = Command(name="ping", callback=ping)
        ctx = make_ctx(bot)
        await cmd.invoke(ctx, "")
        assert called

    @pytest.mark.asyncio
    async def test_invoke_with_typed_args(self, bot):
        called_with = {}

        async def add(ctx, a: int, b: int):
            called_with["sum"] = a + b

        cmd = Command(name="add", callback=add)
        ctx = make_ctx(bot)
        await cmd.invoke(ctx, "3 4")
        assert called_with["sum"] == 7


class TestCommandSignature:
    def test_signature_with_defaults(self):
        async def cmd(ctx, name: str, count: int = 5):
            pass

        c = Command(name="test", callback=cmd)
        assert c.signature == "<name> [count=5]"

    def test_signature_no_params(self):
        async def cmd(ctx):
            pass

        c = Command(name="test", callback=cmd)
        assert c.signature == ""


class TestCommandDecorator:
    def test_creates_command(self):
        @command(name="test", desc="A test", admin=True, aliases=["t"])
        async def test_cmd(ctx):
            pass

        assert isinstance(test_cmd, Command)
        assert test_cmd.name == "test"
        assert test_cmd.admin is True
        assert test_cmd.aliases == ["t"]
        assert test_cmd.description == "A test"

    def test_default_name_from_func(self):
        @command()
        async def my_command(ctx):
            pass

        assert my_command.name == "my_command"
