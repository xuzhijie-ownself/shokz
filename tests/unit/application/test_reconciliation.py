"""Unit tests for ReconciliationPolicy -- Sprint 4.5."""

from __future__ import annotations

from pathlib import Path

import pytest

from shokz.application.policies.reconciliation import ReconciliationPolicy
from shokz.domain.models import ManifestEntry
from tests.fakes import FakeFileSystem, FakeManifest


def _entry(track_id: str, mp3_path: str) -> ManifestEntry:
    return ManifestEntry(
        schema_version=1,
        source="youtube",
        track_id=track_id,
        original_title=f"orig {track_id}",
        filename_stem=mp3_path[:-4],
        mp3_path=mp3_path,
        bitrate_kbps=64,
        duration_s=120.0,
        downloaded_at="2026-04-27T01:00:00Z",
    )


@pytest.mark.asyncio
async def test_reconciliation_policy_unit_level(tmp_path: Path) -> None:
    """Sprint 4.5 AC: 'Reconciliation policy -- unit-level'.

    Manifest: A, B, C. Disk: A.mp3, B.mp3, X.mp3 (X not in manifest).
    Expect: ok=[A,B], orphan_files=[X], orphan_entries=[C].
    """
    manifest = FakeManifest(
        successes=[
            _entry("A", "A.mp3"),
            _entry("B", "B.mp3"),
            _entry("C", "C.mp3"),
        ]
    )
    # Materialize disk state
    (tmp_path / "A.mp3").write_bytes(b"a")
    (tmp_path / "B.mp3").write_bytes(b"b")
    (tmp_path / "X.mp3").write_bytes(b"x")

    fs = FakeFileSystem()
    policy = ReconciliationPolicy(manifest=manifest, filesystem=fs, output_dir=tmp_path)
    report = await policy.scan()

    ok_ids = {e.track_id for e, _ in report.ok}
    assert ok_ids == {"A", "B"}

    orphan_file_names = {p.name for p in report.orphan_files}
    assert orphan_file_names == {"X.mp3"}

    orphan_entry_ids = {e.track_id for e in report.orphan_entries}
    assert orphan_entry_ids == {"C"}

    assert not report.is_clean


@pytest.mark.asyncio
async def test_reconciliation_clean_state_is_clean(tmp_path: Path) -> None:
    manifest = FakeManifest(successes=[_entry("A", "A.mp3")])
    (tmp_path / "A.mp3").write_bytes(b"a")
    fs = FakeFileSystem()
    policy = ReconciliationPolicy(manifest=manifest, filesystem=fs, output_dir=tmp_path)
    report = await policy.scan()
    assert report.is_clean
    assert len(report.ok) == 1
