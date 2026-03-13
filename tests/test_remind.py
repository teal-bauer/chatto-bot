"""Tests for plugins/remind.py — argument parsing."""

import sys
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Add plugins to path for import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.remind import _parse_remind_args, _resolve_time


class TestResolveTime:
    def test_named_times(self):
        assert _resolve_time("morning") == (9, 0)
        assert _resolve_time("noon") == (12, 0)
        assert _resolve_time("evening") == (18, 0)

    def test_hhmm(self):
        assert _resolve_time("14:30") == (14, 30)
        assert _resolve_time("0:00") == (0, 0)
        assert _resolve_time("23:59") == (23, 59)

    def test_invalid_bounds(self):
        with pytest.raises(ValueError, match="Invalid time"):
            _resolve_time("25:00")
        with pytest.raises(ValueError, match="Invalid time"):
            _resolve_time("12:60")


class TestParseRemindArgs:
    def test_relative_minutes(self):
        target, due, msg = _parse_remind_args("me in 5m to check build")
        assert target == "me"
        assert msg == "check build"
        assert due is not None
        # Due should be ~5 minutes from now
        diff = (due - datetime.now(timezone.utc)).total_seconds()
        assert 290 < diff < 310  # ~5 minutes

    def test_relative_hours(self):
        target, due, msg = _parse_remind_args("me in 2h to review PR")
        assert target == "me"
        assert msg == "review PR"
        diff = (due - datetime.now(timezone.utc)).total_seconds()
        assert 7190 < diff < 7210  # ~2 hours

    def test_relative_days(self):
        target, due, msg = _parse_remind_args("me in 1d to deploy")
        assert target == "me"
        assert msg == "deploy"
        diff = (due - datetime.now(timezone.utc)).total_seconds()
        assert 86390 < diff < 86410  # ~1 day

    def test_at_time(self):
        target, due, msg = _parse_remind_args("me at 14:00 to standup")
        assert target == "me"
        assert msg == "standup"
        assert due.hour == 14
        assert due.minute == 0

    def test_named_time(self):
        target, due, msg = _parse_remind_args("me morning to check email")
        assert target == "me"
        assert msg == "check email"
        assert due.hour == 9

    def test_absolute_date(self):
        target, due, msg = _parse_remind_args("me on 2026-06-15 to submit report")
        assert target == "me"
        assert msg == "submit report"
        assert due.year == 2026
        assert due.month == 6
        assert due.day == 15

    def test_absolute_date_with_time(self):
        target, due, msg = _parse_remind_args("me on 2026-06-15 at 14:00 to submit")
        assert target == "me"
        assert msg == "submit"
        assert due.hour == 14

    def test_target_user(self):
        target, due, msg = _parse_remind_args("@alice in 1h to review")
        assert target == "alice"
        assert msg == "review"

    def test_me_to_no_time(self):
        """'me to <msg>' with no time spec defaults to next morning."""
        target, due, msg = _parse_remind_args("me to check something")
        assert target == "me"
        assert msg == "check something"
        assert due.hour == 9  # morning default

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="No arguments"):
            _parse_remind_args("")

    def test_bad_target_raises(self):
        with pytest.raises(ValueError, match="Could not parse"):
            _parse_remind_args("nobody in particular")
