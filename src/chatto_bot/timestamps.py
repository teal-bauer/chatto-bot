"""Parsing and building FDR-030 inline message timestamp tokens.

Timestamp tokens are plain message-body text -- `<t:UNIX_SECONDS:F>` -- with
no protobuf field or API surface behind them (see FDR-030). A bot has no
renderer, so this module gives handlers the two things they actually need:
real ``datetime`` objects pulled out of a message body, and a token to post
one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

# Per FDR-030: `<t:UNIX_SECONDS:F>`, 1-12 digits. `F` is the only format v1
# supports; `:R>`, non-digit values, and 13+ digit values are not tokens and
# stay literal body text.
_TOKEN_RE = re.compile(r"<t:(\d{1,12}):F>")

# The largest value the token regex above can ever match (12 nines). Also
# used as the write-side bound, so a token this module builds is always one
# it (and any other FDR-030-conforming reader) can parse back.
_MAX_EPOCH_SECONDS = 10**12 - 1

_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_BLOCKQUOTE_RE = re.compile(r"^[ \t]{0,3}>")
_INLINE_CODE_RE = re.compile(r"(`+).+?\1")


@dataclass
class TimestampToken:
    """One valid `<t:...:F>` token found in a message body."""

    text: str
    start: int
    end: int
    epoch_seconds: int
    datetime: datetime


def _excluded_ranges(body: str) -> list[tuple[int, int]]:
    """Find character ranges of ``body`` that are fenced code, inline code, or
    blockquote content, so token matches inside them can be dropped.

    A bot only ever sees the raw markdown source, not rendered HTML, so this
    scans the source directly using a line-oriented approximation of
    CommonMark fencing/quoting rather than a full parser. It correctly
    handles the common cases (``` and ~~~ fences, `inline` and ``inline``
    spans, and `> quoted` lines) but is not a complete CommonMark
    implementation: it doesn't special-case backslash-escaped backticks, and
    it treats each blockquote line independently rather than following lazy
    continuation rules for multi-line quotes. Classic 4-space-indented code
    blocks are also out of scope -- FDR-030 only calls out fenced code,
    inline code, and blockquotes.
    """
    excluded: list[tuple[int, int]] = []

    in_fence = False
    fence_char = ""
    fence_len = 0
    offset = 0
    for line in body.splitlines(keepends=True):
        line_start = offset
        offset += len(line)
        stripped = line.rstrip("\r\n")

        if in_fence:
            excluded.append((line_start, line_start + len(stripped)))
            fence_match = _FENCE_RE.match(stripped)
            if (
                fence_match
                and fence_match.group(1)[0] == fence_char
                and len(fence_match.group(1)) >= fence_len
                and stripped.strip() == fence_match.group(1)
            ):
                in_fence = False
            continue

        fence_match = _FENCE_RE.match(stripped)
        if fence_match:
            in_fence = True
            fence_char = fence_match.group(1)[0]
            fence_len = len(fence_match.group(1))
            excluded.append((line_start, line_start + len(stripped)))
            continue

        if _BLOCKQUOTE_RE.match(stripped):
            excluded.append((line_start, line_start + len(stripped)))
            continue

        for code_match in _INLINE_CODE_RE.finditer(stripped):
            excluded.append((line_start + code_match.start(), line_start + code_match.end()))

    return excluded


def parse_timestamp_tokens(body: str) -> list[TimestampToken]:
    """Find every valid timestamp token in a message body, in document order.

    Each result carries the literal token text and its position in ``body``
    (so a handler can slice around it or rewrite the body), plus the epoch
    seconds and an equivalent aware UTC ``datetime`` (so handlers never touch
    epoch math directly).

    Honors FDR-030's literal-text exclusions: tokens inside fenced code
    blocks, inline code spans, and blockquotes are not returned -- see
    ``_excluded_ranges`` for the scanning approach and its limits.
    """
    excluded = _excluded_ranges(body)

    def _is_excluded(pos: int) -> bool:
        return any(start <= pos < end for start, end in excluded)

    tokens: list[TimestampToken] = []
    for match in _TOKEN_RE.finditer(body):
        if _is_excluded(match.start()):
            continue
        epoch_seconds = int(match.group(1))
        tokens.append(
            TimestampToken(
                text=match.group(0),
                start=match.start(),
                end=match.end(),
                epoch_seconds=epoch_seconds,
                datetime=datetime.fromtimestamp(epoch_seconds, tz=timezone.utc),
            )
        )
    return tokens


def format_timestamp_token(when: datetime | int | float) -> str:
    """Build a `<t:EPOCH:F>` token from an aware datetime or Unix epoch seconds.

    Every reader renders the token in their own timezone, so the token itself
    must be unambiguous UTC. A naive ``datetime`` is rejected rather than
    assumed to be UTC: silently guessing would write a token that's wrong by
    whatever offset the process's local timezone happens to be. Attach a
    tzinfo (``datetime.timezone.utc`` if the value is already UTC) before
    calling this.
    """
    if isinstance(when, datetime):
        if when.tzinfo is None:
            raise ValueError(
                "format_timestamp_token requires a timezone-aware datetime; "
                "attach a tzinfo (e.g. datetime.timezone.utc) first"
            )
        epoch_seconds = int(when.timestamp())
    else:
        epoch_seconds = int(when)

    if not (0 <= epoch_seconds <= _MAX_EPOCH_SECONDS):
        raise ValueError(
            f"epoch seconds {epoch_seconds} is outside the supported range "
            f"0..{_MAX_EPOCH_SECONDS} (FDR-030 tokens allow at most 12 digits)"
        )

    return f"<t:{epoch_seconds}:F>"
