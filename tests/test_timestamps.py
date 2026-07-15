"""Tests for timestamps.py -- FDR-030 inline timestamp token parsing/building."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from chatto_bot.timestamps import format_timestamp_token, parse_timestamp_tokens


class TestParseTimestampTokens:
    def test_single_token(self):
        tokens = parse_timestamp_tokens("see you at <t:1745764200:F>")
        assert len(tokens) == 1
        token = tokens[0]
        assert token.text == "<t:1745764200:F>"
        assert token.epoch_seconds == 1745764200
        assert token.datetime == datetime(2025, 4, 27, 14, 30, tzinfo=timezone.utc)
        assert token.start == 11
        assert token.end == 11 + len("<t:1745764200:F>")

    def test_multiple_tokens_in_order(self):
        body = "start <t:1000:F> middle <t:2000:F> end"
        tokens = parse_timestamp_tokens(body)
        assert [t.epoch_seconds for t in tokens] == [1000, 2000]

    def test_no_tokens(self):
        assert parse_timestamp_tokens("just a normal message") == []

    def test_relative_format_not_parsed(self):
        assert parse_timestamp_tokens("<t:1745764200:R>") == []

    def test_non_numeric_value_not_parsed(self):
        assert parse_timestamp_tokens("<t:abc:F>") == []

    def test_thirteen_digits_not_parsed(self):
        assert parse_timestamp_tokens("<t:1234567890123:F>") == []

    def test_epoch_zero(self):
        tokens = parse_timestamp_tokens("<t:0:F>")
        assert tokens[0].epoch_seconds == 0
        assert tokens[0].datetime == datetime(1970, 1, 1, tzinfo=timezone.utc)

    def test_token_in_fenced_code_block_ignored(self):
        body = "before\n```\n<t:1745764200:F>\n```\nafter"
        assert parse_timestamp_tokens(body) == []

    def test_token_in_inline_code_ignored(self):
        body = "the token is `<t:1745764200:F>` literally"
        assert parse_timestamp_tokens(body) == []

    def test_token_in_blockquote_ignored(self):
        body = "> <t:1745764200:F>\nnormal <t:2000:F>"
        tokens = parse_timestamp_tokens(body)
        assert [t.epoch_seconds for t in tokens] == [2000]


class TestFormatTimestampToken:
    def test_from_epoch_int(self):
        assert format_timestamp_token(1745764200) == "<t:1745764200:F>"

    def test_from_aware_datetime(self):
        dt = datetime(2025, 4, 27, 14, 30, tzinfo=timezone.utc)
        assert format_timestamp_token(dt) == "<t:1745764200:F>"

    def test_from_aware_datetime_non_utc(self):
        dt = datetime(2025, 4, 27, 16, 30, tzinfo=timezone(timedelta(hours=2)))
        assert format_timestamp_token(dt) == "<t:1745764200:F>"

    def test_naive_datetime_raises(self):
        with pytest.raises(ValueError):
            format_timestamp_token(datetime(2025, 4, 27, 14, 30))

    def test_epoch_zero(self):
        assert format_timestamp_token(0) == "<t:0:F>"

    def test_out_of_range_raises(self):
        with pytest.raises(ValueError):
            format_timestamp_token(10**12)
        with pytest.raises(ValueError):
            format_timestamp_token(-1)

    def test_round_trip(self):
        token_text = format_timestamp_token(1745764200)
        tokens = parse_timestamp_tokens(token_text)
        assert len(tokens) == 1
        assert tokens[0].epoch_seconds == 1745764200
