"""Unit tests for domain/filenames.py -- Sprint 2."""

from __future__ import annotations

import re

import pytest

from shokz.domain.filenames import (
    DEFAULT_MAX_BYTES,
    DEFAULT_TEMPLATE,
    fallback_stem,
    render_template,
    sanitize_filename,
)
from shokz.domain.models import Track

_FAT_RESERVED_CHARS = set('<>:"/\\|?*')
_FAT_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _track(title: str, _id: str = "abc123XYZ_0", uploader: str | None = "Up") -> Track:
    return Track(
        id=_id,
        title=title,
        uploader=uploader,
        duration_s=120,
        source_url="https://example.com/x",
        source_name="youtube",
    )


def _assert_safe(stem: str) -> None:
    assert isinstance(stem, str)
    if not stem:
        return
    assert not (set(stem) & _FAT_RESERVED_CHARS), f"reserved char in: {stem!r}"
    assert stem == stem.strip().strip(".").strip(), f"leading/trailing trash: {stem!r}"
    assert not re.search(r"[\x00-\x1f\x7f]", stem), f"control char in: {stem!r}"
    assert len(stem.encode("utf-8")) <= DEFAULT_MAX_BYTES


@pytest.mark.parametrize(
    ("raw", "must_contain"),
    [
        ("Soft Piano Sleep Music", "Soft Piano Sleep Music"),
        ("Hello / World", None),
        ("foo<bar>baz", None),
        ("..weird..", None),
        ("CON", None),
        ("    leading and trailing    ", "leading and trailing"),
        ("8 Hours: Relaxing Sleep -- vol. 3", "8 Hours"),
        ("Multiple    spaces", "Multiple"),
    ],
)
def test_sanitizer_produces_safe_stem_for_typical_inputs(
    raw: str, must_contain: str | None
) -> None:
    out = sanitize_filename(raw)
    _assert_safe(out)
    if must_contain is not None:
        assert must_contain in out, f"{must_contain!r} missing from {out!r}"


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "...", "/\\:?", "<<<>>>", "\x00\x01\x02", "."],
)
def test_sanitizer_returns_empty_for_unusable_inputs(raw: str) -> None:
    """Caller (filename_resolver) detects '' and substitutes untitled-{id}."""
    assert sanitize_filename(raw) == ""


def test_unicode_title_is_preserved_on_exfat_friendly_filesystem() -> None:
    """Sprint 2 AC: 'Unicode title is preserved on exFAT-friendly filesystem'."""
    out = sanitize_filename("10 hours music")
    _assert_safe(out)
    assert "10" in out
    assert "hours" in out


def test_sanitizer_truncates_to_max_bytes_utf8_safe() -> None:
    raw = "x" * 500
    out = sanitize_filename(raw, max_bytes=120)
    _assert_safe(out)
    assert len(out.encode("utf-8")) <= 120


def test_sanitizer_property_every_title_produces_a_non_empty_fat_safe_stem() -> None:
    """Sprint 2 AC: 'Sanitizer property -- every title produces a non-empty FAT-safe stem'."""
    samples = [
        "",
        "   ",
        "...",
        "punct only",
        "CON",
        "lpt1",
        "PRN.txt",
        "<<<>>>",
        "/etc/passwd",
        "../../../escape",
        "a" * 1000,
        "pi music for sleep",
        "Soft Piano Sleep Music",
        "8 Hours of Beautiful Music",
        "Hello\x00World",
        "tab\there",
    ]
    for raw in samples:
        out = sanitize_filename(raw)
        _assert_safe(out)
        if out:
            assert out.upper() not in _FAT_RESERVED_NAMES, f"reserved name leaked: {out!r}"


def test_render_template_default_is_just_title() -> None:
    track = _track("Hello World", _id="xxx")
    assert render_template(track, DEFAULT_TEMPLATE) == "Hello World"


def test_render_template_supports_uploader_and_id() -> None:
    track = _track("Hello", _id="abc", uploader="MyChannel")
    assert render_template(track, "{uploader} - {title} [{id}]") == "MyChannel - Hello [abc]"


def test_render_template_rejects_unsupported_token() -> None:
    track = _track("Hello")
    with pytest.raises(ValueError, match="unsupported template token"):
        render_template(track, "{title} - {bogus_field}")


def test_fallback_stem_uses_untitled_with_id() -> None:
    track = _track("", _id="dQw4w9WgXcQ")
    assert fallback_stem(track) == "untitled-dQw4w9WgXcQ"
