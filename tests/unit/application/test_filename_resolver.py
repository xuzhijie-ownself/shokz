"""Unit tests for FilenameResolver -- Sprint 2."""

from __future__ import annotations

from pathlib import Path

import pytest

from shokz.application.policies.filename_resolver import FilenameResolver
from shokz.domain.errors import FilenameCollision, NameInvalid
from shokz.domain.models import Track


def _track(title: str, _id: str = "abc123") -> Track:
    return Track(
        id=_id,
        title=title,
        uploader="Up",
        duration_s=60,
        source_url="https://example.com/x",
        source_name="youtube",
    )


def _exists_factory(existing: set[Path]):
    def _exists(p: Path) -> bool:
        return p in existing

    return _exists


def test_filename_resolver_unit_level_with_fakes(tmp_path: Path) -> None:
    """Sprint 2 AC: 'Filename resolver -- unit-level with fakes' (no collision case)."""
    resolver = FilenameResolver(output_dir=tmp_path)
    track = _track("Foo")
    out = resolver.resolve(track, name_override=None, exists=lambda _p: False)
    assert out == tmp_path / "Foo.mp3"


def test_filename_collision_auto_suffixes_default_policy(tmp_path: Path) -> None:
    """Sprint 2 AC: 'Filename collision auto-suffixes (default policy)'."""
    resolver = FilenameResolver(output_dir=tmp_path)
    track = _track("Foo")
    existing = {
        tmp_path / "Foo.mp3",
        tmp_path / "Foo (2).mp3",
        tmp_path / "Foo (3).mp3",
    }
    out = resolver.resolve(track, name_override=None, exists=_exists_factory(existing))
    assert out == tmp_path / "Foo (4).mp3"


def test_filename_defaults_to_sanitized_video_title(tmp_path: Path) -> None:
    """Sprint 2 AC: 'Filename defaults to sanitized video title'."""
    resolver = FilenameResolver(output_dir=tmp_path)
    track = _track("Soft Piano Sleep Music", _id="eiV0nvJ9fRM")
    out = resolver.resolve(track, name_override=None, exists=lambda _p: False)
    assert out == tmp_path / "Soft Piano Sleep Music.mp3"
    assert "eiV0nvJ9fRM" not in out.name


def test_name_flag_overrides_the_title_for_a_single_url(tmp_path: Path) -> None:
    """Sprint 2 AC: '--name flag overrides the title for a single URL'."""
    resolver = FilenameResolver(output_dir=tmp_path)
    track = _track("Original Title")
    out = resolver.resolve(track, name_override="Sleep Mix Vol 1", exists=lambda _p: False)
    assert out == tmp_path / "Sleep Mix Vol 1.mp3"


def test_path_traversal_in_name_is_rejected(tmp_path: Path) -> None:
    """Sprint 2 AC: 'Path traversal in --name is rejected'.

    pathvalidate strips separator chars, so '../etc/evil' becomes a safe stem
    INSIDE output_dir. Defense-in-depth: an override that sanitizes to empty
    (e.g. '///') raises NameOutsideOutputDir.
    """
    resolver = FilenameResolver(output_dir=tmp_path)
    track = _track("Hello")

    out = resolver.resolve(track, name_override="../etc/evil", exists=lambda _p: False)
    assert out.parent.resolve() == tmp_path.resolve()

    with pytest.raises(NameInvalid):
        resolver.resolve(track, name_override="///", exists=lambda _p: False)


def test_empty_or_all_punctuation_title_falls_back_to_untitled_id(tmp_path: Path) -> None:
    """Sprint 2 AC: 'Empty or all-punctuation title falls back to untitled-{id}'."""
    resolver = FilenameResolver(output_dir=tmp_path)
    for bad_title in ["", "...", "<<<>>>"]:
        track = _track(bad_title, _id="dQw4w9WgXcQ")
        out = resolver.resolve(track, name_override=None, exists=lambda _p: False)
        assert out == tmp_path / "untitled-dQw4w9WgXcQ.mp3"


def test_collision_exhaustion_raises_filename_collision(tmp_path: Path) -> None:
    """Sprint 2 silent-failure fix (F1): suffix loop exhaustion raises FilenameCollision."""
    resolver = FilenameResolver(output_dir=tmp_path)
    track = _track("Foo")
    # exists callback that ALWAYS returns True forces the loop to exhaust.
    with pytest.raises(FilenameCollision, match="exhausted"):
        resolver.resolve(track, name_override=None, exists=lambda _p: True)
