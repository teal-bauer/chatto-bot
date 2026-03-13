"""Tests for config.py — dotenv parsing and config loading."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from chatto_bot.config import BotConfig, _load_dotenv


class TestLoadDotenv:
    def test_basic_parsing(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\nBAZ=qux\n")
        monkeypatch.delenv("FOO", raising=False)
        monkeypatch.delenv("BAZ", raising=False)
        monkeypatch.chdir(tmp_path)
        _load_dotenv()
        assert os.environ["FOO"] == "bar"
        assert os.environ["BAZ"] == "qux"

    def test_quoted_values(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text('SINGLE=\'value with spaces\'\nDOUBLE="another value"\n')
        monkeypatch.delenv("SINGLE", raising=False)
        monkeypatch.delenv("DOUBLE", raising=False)
        monkeypatch.chdir(tmp_path)
        _load_dotenv()
        assert os.environ["SINGLE"] == "value with spaces"
        assert os.environ["DOUBLE"] == "another value"

    def test_inline_comments(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value # this is a comment\n")
        monkeypatch.delenv("KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        _load_dotenv()
        assert os.environ["KEY"] == "value"

    def test_comments_skipped(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("# full line comment\nKEY=val\n")
        monkeypatch.delenv("KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        _load_dotenv()
        assert os.environ["KEY"] == "val"

    def test_existing_env_not_overridden(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING=new_value\n")
        monkeypatch.setenv("EXISTING", "original")
        monkeypatch.chdir(tmp_path)
        _load_dotenv()
        assert os.environ["EXISTING"] == "original"


class TestBotConfig:
    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            config = BotConfig()
        assert config.prefix == "!"
        assert config.instance == "https://dev.chatto.run"
        assert config.dms is True

    def test_explicit_args_override_all(self):
        with patch.dict(os.environ, {"CHATTO_INSTANCE": "https://env.example.com"}, clear=False):
            config = BotConfig.load(
                None,
                instance="https://explicit.example.com",
                prefix=">>",
            )
        assert config.instance == "https://explicit.example.com"
        assert config.prefix == ">>"

    def test_env_vars(self):
        env = {
            "CHATTO_INSTANCE": "https://env.example.com",
            "CHATTO_PREFIX": ">>",
            "CHATTO_SPACES": "S1,S2",
            "CHATTO_SESSION": "mysession",
            "CHATTO_DMS": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            config = BotConfig.load(None)
        assert config.instance == "https://env.example.com"
        assert config.prefix == ">>"
        assert config.spaces == ["S1", "S2"]
        assert config.session == "mysession"
        assert config.dms is False

    def test_yaml_loading(self, tmp_path, monkeypatch):
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("instance: https://yaml.example.com\nprefix: '#'\nspaces:\n  - S1\n")
        # Ensure no .env file interferes and no env vars override
        monkeypatch.chdir(tmp_path)
        with patch.dict(os.environ, {}, clear=True):
            config = BotConfig.load(str(yaml_file))
        assert config.instance == "https://yaml.example.com"
        assert config.prefix == "#"
        assert config.spaces == ["S1"]

    def test_graphql_url(self):
        config = BotConfig(instance="https://test.example.com")
        assert config.graphql_url == "https://test.example.com/api/graphql"

    def test_ws_url_https(self):
        config = BotConfig(instance="https://test.example.com")
        assert config.ws_url == "wss://test.example.com/api/graphql"

    def test_ws_url_http(self):
        config = BotConfig(instance="http://localhost:3000")
        assert config.ws_url == "ws://localhost:3000/api/graphql"

    def test_cookie_header(self):
        config = BotConfig(session="abc123")
        assert config.cookie_header == "chatto_session=abc123"
