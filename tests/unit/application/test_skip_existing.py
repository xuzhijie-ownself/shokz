"""Unit tests for SkipExistingPolicy -- Sprint 4.5."""

from __future__ import annotations

from pathlib import Path

import pytest

from shokz.application.policies.skip_existing import (
    SkipDecision,
    SkipExistingPolicy,
)
from shokz.domain.models import ManifestEntry
from tests.fakes import FakeFileSystem, FakeManifest


def _entry(
    source: str = "youtube", track_id: str = "abc123", mp3_path: str = "Foo.mp3"
) -> ManifestEntry:
    return ManifestEntry(
        schema_version=1,
        source=source,
        track_id=track_id,
        original_title=f"orig {track_id}",
        filename_stem=mp3_path[:-4],  # strip .mp3
        mp3_path=mp3_path,
        bitrate_kbps=64,
        duration_s=120.0,
        downloaded_at="2026-04-27T01:00:00Z",
    )


@pytest.mark.asyncio
async def test_skip_existing_policy_unit_level_skipped(tmp_path: Path) -> None:
    """Sprint 4.5 AC: 'Skip-existing policy -- unit-level' (skip path)."""
    manifest = FakeManifest(successes=[_entry()])
    fs = FakeFileSystem()
    (tmp_path / "Foo.mp3").write_bytes(b"x" * 100)  # file present

    policy = SkipExistingPolicy(manifest=manifest, filesystem=fs, output_dir=tmp_path)
    result = await policy.check("youtube", "abc123")

    assert result.decision is SkipDecision.SKIPPED
    assert result.existing_path == tmp_path / "Foo.mp3"


@pytest.mark.asyncio
async def test_skip_existing_policy_unit_level_re_download_when_file_missing(
    tmp_path: Path,
) -> None:
    """Sprint 4.5 AC: 'Skip-existing policy -- unit-level' (re-download path).

    Manifest has the entry but the file was manually deleted.
    """
    manifest = FakeManifest(successes=[_entry()])
    fs = FakeFileSystem()
    # Note: NOT writing Foo.mp3 to tmp_path

    policy = SkipExistingPolicy(manifest=manifest, filesystem=fs, output_dir=tmp_path)
    result = await policy.check("youtube", "abc123")

    assert result.decision is SkipDecision.RE_DOWNLOAD
    assert result.existing_path is None


@pytest.mark.asyncio
async def test_skip_existing_re_downloads_when_no_manifest_entry(tmp_path: Path) -> None:
    """Manifest is empty -> always re-download."""
    manifest = FakeManifest()  # empty
    fs = FakeFileSystem()

    policy = SkipExistingPolicy(manifest=manifest, filesystem=fs, output_dir=tmp_path)
    result = await policy.check("youtube", "never_seen")

    assert result.decision is SkipDecision.RE_DOWNLOAD
