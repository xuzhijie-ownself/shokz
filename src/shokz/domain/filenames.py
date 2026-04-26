"""Filename sanitization & template rendering -- pure domain.

Wraps `pathvalidate` for cross-platform sanitization (FAT/exFAT/NTFS reserved
names, Windows-reserved tokens like CON/LPT1, control chars, separators).

Per Sprint 2 spec (docs/sprints/sprint-2.md):
  - Default template: "{title}"
  - Tokens: {title} {uploader} {id} {source} {duration} {date}
  - Empty/all-punct title falls back to "untitled-{id}"
  - Unicode preserved (FAT32 supports VFAT long names; exFAT is unicode-native)
  - Max 120 bytes (UTF-8 aware)

This module is PURE: no I/O, no asyncio. Filesystem decisions (collision
handling, path-traversal, .mp3 suffix) live in `policies/filename_resolver.py`.
"""

from __future__ import annotations

import logging
import re
from typing import Final

from pathvalidate import sanitize_filename as _pv_sanitize

from shokz.domain.models import Track

DEFAULT_TEMPLATE: Final[str] = "{title}"
DEFAULT_MAX_BYTES: Final[int] = 120

# Sprint 2 supports these tokens. {date} is intentionally absent: yt-dlp's
# upload_date metadata isn't wired into Track yet (Sprint 5).
_SUPPORTED_TOKENS: Final[frozenset[str]] = frozenset(
    {"title", "uploader", "id", "source", "duration"}
)

_log = logging.getLogger("shokz.domain.filenames")

_CONTROL_RE: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_filename(raw: str, *, max_bytes: int = DEFAULT_MAX_BYTES) -> str:
    """Return a FAT/exFAT-safe filename stem (no extension), or '' if input is unusable."""
    cleaned = _CONTROL_RE.sub("", raw)
    # replacement_text="" drops FAT-reserved chars rather than replacing with _,
    # so all-punctuation titles collapse to "" and the caller falls back to
    # untitled-{id} (Sprint 2 AC: 'Empty or all-punct title falls back to ...').
    safe = _pv_sanitize(cleaned, platform="universal", replacement_text="")
    safe = safe.strip().strip(".").strip()
    safe = _truncate_utf8_bytes(safe, max_bytes)
    return safe


def render_template(track: Track, template: str = DEFAULT_TEMPLATE) -> str:
    """Render a filename stem from a Track using the given template."""
    used = set(re.findall(r"\{(\w+)\}", template))
    unsupported = used - _SUPPORTED_TOKENS
    if unsupported:
        raise ValueError(
            f"unsupported template token(s): {sorted(unsupported)}; "
            f"supported: {sorted(_SUPPORTED_TOKENS)}"
        )
    if not track.title:
        # Sprint 2 silent-failure fix (F5): surface unrenderable titles in logs
        # so a buggy adapter or a genuinely-untitled video is detectable.
        _log.warning(
            "track id=%s has empty title; template render will produce empty stem "
            "and the resolver will fall back to untitled-{id}",
            track.id,
        )
    duration_str = _format_duration(track.duration_s) if track.duration_s else ""
    rendered = template.format(
        title=track.title or "",
        uploader=track.uploader or "",
        id=track.id,
        source=track.source_name,
        duration=duration_str,
        date="",
    )
    return rendered


def fallback_stem(track: Track) -> str:
    return f"untitled-{track.id}"


def _truncate_utf8_bytes(s: str, max_bytes: int) -> str:
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    cut = max_bytes
    while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
        cut -= 1
    return encoded[:cut].decode("utf-8", errors="ignore")


def _format_duration(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}-{m:02d}-{s:02d}"
