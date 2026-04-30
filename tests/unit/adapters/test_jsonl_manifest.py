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


# Sprint 8.5 Phase A -- iter_failures + ManifestReadError + WARNING-level
# malformed-row log.


@pytest.mark.asyncio
async def test_iter_failures_yields_in_append_order(tmp_path: Path) -> None:
    """Happy path: iter_failures returns each appended row as a FailureEntry,
    in file order. Mirrors the iter_all contract for failures.jsonl."""
    m = JsonlManifest(
        manifest_path=tmp_path / "m.jsonl",
        failures_path=tmp_path / "f.jsonl",
    )
    await m.record_failure(_failure("https://example.com/A"))
    await m.record_failure(_failure("https://example.com/B"))

    yielded = [e async for e in m.iter_failures()]
    assert len(yielded) == 2
    assert [e.url for e in yielded] == [
        "https://example.com/A",
        "https://example.com/B",
    ]
    assert all(isinstance(e, FailureEntry) for e in yielded)


@pytest.mark.asyncio
async def test_iter_failures_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """No failures.jsonl yet -> empty iterator (NOT raise). Same idle-state
    contract as iter_all."""
    m = JsonlManifest(
        manifest_path=tmp_path / "m.jsonl",
        failures_path=tmp_path / "absent.jsonl",
    )
    yielded = [e async for e in m.iter_failures()]
    assert yielded == []


@pytest.mark.asyncio
async def test_iter_failures_wraps_oserror_as_manifest_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sprint 8.5 C1: an OSError from the read path (e.g. EPERM) MUST
    surface as ManifestReadError with the file path embedded, NOT bubble
    up as a raw OSError that the CLI routes through the catch-all."""
    from shokz.domain.errors import ManifestReadError

    failures_path = tmp_path / "f.jsonl"
    failures_path.write_text('{"schema_version": 1}\n')

    real_open = Path.open

    def _eperm_open(self: Path, *args: object, **kwargs: object) -> object:
        if self == failures_path:
            raise OSError(13, "Permission denied")
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", _eperm_open)

    m = JsonlManifest(
        manifest_path=tmp_path / "m.jsonl",
        failures_path=failures_path,
    )
    with pytest.raises(ManifestReadError, match="cannot read"):
        [e async for e in m.iter_failures()]


@pytest.mark.asyncio
async def test_iter_all_skips_valid_json_with_missing_fields(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Sprint 8.5 final-GAN H2: iter_all retroactively gained
    _safe_construct protection (consistency with iter_failures); pin
    that contract so a future refactor that accidentally removes the
    helper from iter_all gets caught by this test."""
    import logging

    manifest_path = tmp_path / "m.jsonl"
    manifest_path.write_text(
        # Valid ManifestEntry row.
        '{"schema_version": 1, "source": "youtube", "track_id": "abc", '
        '"original_title": "T", "filename_stem": "T", "mp3_path": "T.mp3", '
        '"bitrate_kbps": 64, "duration_s": 12.0, '
        '"downloaded_at": "2026-04-30T00:00:00Z"}\n'
        '{}\n'  # valid JSON, missing required fields
        '{"schema_version": 1, "source": "youtube", "track_id": "def", '
        '"original_title": "T2", "filename_stem": "T2", "mp3_path": "T2.mp3", '
        '"bitrate_kbps": 64, "duration_s": 12.0, '
        '"downloaded_at": "2026-04-30T00:00:00Z", "future_field": "extra"}\n'
    )
    m = JsonlManifest(
        manifest_path=manifest_path,
        failures_path=tmp_path / "f.jsonl",
    )
    with caplog.at_level(logging.WARNING, logger="shokz.adapter.manifest"):
        yielded = [e async for e in m.iter_all()]

    # Only the well-formed row survives; the empty {} and the extra-
    # field row are SKIPPED with WARNINGs, NOT aborting iter_all.
    assert [e.track_id for e in yielded] == ["abc"]
    warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert sum(1 for msg in warning_msgs if "structurally invalid" in msg) == 2, (
        f"expected 2 structurally-invalid WARNINGs; got {warning_msgs}"
    )


@pytest.mark.asyncio
async def test_iter_failures_skips_valid_json_with_missing_fields(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Sprint 8.5 Phase A GAN HIGH#3 / MEDIUM#5: a row that's valid JSON but
    structurally invalid as a FailureEntry (e.g. partial-write produced
    `{}`, OR a future schema_version added a new field) MUST be skipped
    with a WARNING -- NOT raise an unhandled TypeError that aborts the
    iterator and routes through the CLI's unexpected-error catch-all."""
    import logging

    failures_path = tmp_path / "f.jsonl"
    failures_path.write_text(
        '{"schema_version": 1, "source": "youtube", "track_id": "abc", '
        '"url": "https://A", "error_class": "X", "error_message": "y", '
        '"failed_at": "2026-04-30T00:00:00Z"}\n'
        '{}\n'
        '{"schema_version": 1, "source": "youtube", "track_id": "def", '
        '"url": "https://B", "error_class": "X", "error_message": "y", '
        '"failed_at": "2026-04-30T00:00:00Z", "future_field": "extra"}\n'
    )
    m = JsonlManifest(
        manifest_path=tmp_path / "m.jsonl",
        failures_path=failures_path,
    )
    with caplog.at_level(logging.WARNING, logger="shokz.adapter.manifest"):
        yielded = [e async for e in m.iter_failures()]

    assert [e.url for e in yielded] == ["https://A"]
    warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert sum(1 for msg in warning_msgs if "structurally invalid" in msg) == 2, (
        f"expected 2 structurally-invalid WARNINGs; got {warning_msgs}"
    )


def test_read_jsonl_warns_on_malformed_row(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Sprint 8.5 U1: malformed JSONL row gets a WARNING (not DEBUG) so
    partial-write races during concurrent appends are visible in default
    log output. The good rows around the malformed one are still returned."""
    import logging

    from shokz.adapters.outbound.jsonl_manifest import _read_jsonl

    path = tmp_path / "f.jsonl"
    path.write_text(
        '{"schema_version": 1, "url": "https://A"}\n'
        '{not valid json\n'
        '{"schema_version": 1, "url": "https://B"}\n'
    )

    with caplog.at_level(logging.WARNING, logger="shokz.adapter.manifest"):
        rows = _read_jsonl(path)

    assert len(rows) == 2
    assert rows[0]["url"] == "https://A"
    assert rows[1]["url"] == "https://B"
    assert any(
        "malformed jsonl row 2" in r.message
        and r.levelname == "WARNING"
        for r in caplog.records
    ), f"expected WARNING-level malformed-row log; got {[r.levelname for r in caplog.records]}"
