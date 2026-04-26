"""Unit tests for JsonlManifest -- Sprint 4."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from shokz.adapters.outbound.jsonl_manifest import JsonlManifest
from shokz.domain.models import FailureEntry, ManifestEntry


def _entry(track_id: str = "abc") -> ManifestEntry:
    return ManifestEntry(
        schema_version=1,
        source="youtube",
        track_id=track_id,
        original_title=f"Original {track_id}",
        filename_stem=f"sanitized-{track_id}",
        mp3_path=f"sanitized-{track_id}.mp3",
        bitrate_kbps=64,
        duration_s=120.0,
        downloaded_at="2026-04-27T01:23:45Z",
    )


def _failure(url: str = "https://x") -> FailureEntry:
    return FailureEntry(
        schema_version=1,
        source="youtube",
        track_id="abc",
        url=url,
        error_class="SourceUnavailable",
        error_message="404",
        failed_at="2026-04-27T01:23:45Z",
    )


@pytest.mark.asyncio
async def test_manifest_is_jsonl_with_schema_version_1_per_row(tmp_path: Path) -> None:
    """Sprint 4 AC: 'Manifest is JSONL with schema_version=1 per row'."""
    m = JsonlManifest(
        manifest_path=tmp_path / "m.jsonl",
        failures_path=tmp_path / "f.jsonl",
    )
    await m.record(_entry("a"))
    await m.record(_entry("b"))
    await m.record(_entry("c"))

    lines = (tmp_path / "m.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3
    for line in lines:
        row = json.loads(line)
        assert row["schema_version"] == 1
        for key in (
            "source",
            "track_id",
            "original_title",
            "filename_stem",
            "mp3_path",
            "bitrate_kbps",
            "duration_s",
            "downloaded_at",
        ):
            assert key in row, f"missing field {key!r} in {row}"


@pytest.mark.asyncio
async def test_manifest_fsync_verification(tmp_path: Path) -> None:
    """Sprint 4 AC: 'Manifest fsync verification (unit-level)'.

    Patches os.fsync to count calls. After one record(), fsync MUST have been
    called twice: once for the file fd, once for the parent dir fd.
    """
    m = JsonlManifest(
        manifest_path=tmp_path / "m.jsonl",
        failures_path=tmp_path / "f.jsonl",
    )

    fsync_targets: list[int] = []
    real_fsync = os.fsync

    def counting_fsync(fd: int) -> None:
        fsync_targets.append(fd)
        real_fsync(fd)

    with patch("shokz.adapters.outbound.jsonl_manifest.os.fsync", counting_fsync):
        await m.record(_entry("a"))

    # Exactly 2 fsyncs per record(): file fd + parent-dir fd.
    assert len(fsync_targets) == 2, (
        f"expected 2 fsync calls (file + parent dir), got {len(fsync_targets)}"
    )


@pytest.mark.asyncio
async def test_failure_log_separate_file(tmp_path: Path) -> None:
    """record_failure writes to failures.jsonl, not manifest.jsonl."""
    m = JsonlManifest(
        manifest_path=tmp_path / "m.jsonl",
        failures_path=tmp_path / "f.jsonl",
    )
    await m.record_failure(_failure("https://example.com/dead"))

    assert not (tmp_path / "m.jsonl").exists()
    rows = (tmp_path / "f.jsonl").read_text().strip().splitlines()
    assert len(rows) == 1
    parsed = json.loads(rows[0])
    assert parsed["url"] == "https://example.com/dead"
    assert parsed["error_class"] == "SourceUnavailable"
    assert parsed["schema_version"] == 1
