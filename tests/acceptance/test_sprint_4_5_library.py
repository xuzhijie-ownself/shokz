"""Sprint 4.5 acceptance tests -- Gherkin scenarios as pytest tests."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

_SHORT_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
_SHORT_URL_2 = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _integration_enabled() -> bool:
    return os.environ.get("INTEGRATION") == "1"


pytestmark = pytest.mark.integration


def _shokz_bin() -> str:
    return str(Path(".venv/bin/shokz").resolve())


def _shokz_env() -> dict[str, str]:
    venv_bin = str(Path(".venv/bin").resolve())
    return {**os.environ, "PATH": f"{venv_bin}:{os.environ.get('PATH', '')}"}


def _run_shokz(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_shokz_bin(), *args],
        capture_output=True,
        text=True,
        env=_shokz_env(),
        cwd=cwd,
    )


def test_skip_existing_by_manifest_match_short_circuits_the_download(tmp_path: Path) -> None:
    """Sprint 4.5 AC scenario 1."""
    if not _integration_enabled():
        pytest.skip("set INTEGRATION=1 to run network tests")
    downloads = tmp_path / "downloads"
    downloads.mkdir()

    # First: real download
    r1 = _run_shokz("download", "-o", str(downloads), _SHORT_URL)
    assert r1.returncode == 0, r1.stderr

    # Second: should be SKIPPED in <2s
    started = time.monotonic()
    r2 = _run_shokz("download", "-o", str(downloads), _SHORT_URL)
    elapsed = time.monotonic() - started
    assert r2.returncode == 0, r2.stderr
    combined = r2.stdout + r2.stderr
    assert "skip" in combined.lower() or "0 succeeded" in combined.lower()
    assert elapsed < 4, f"skip took {elapsed:.1f}s -- expected near-instant"


def test_force_overrides_skip_existing(tmp_path: Path) -> None:
    """Sprint 4.5 AC scenario 2."""
    if not _integration_enabled():
        pytest.skip("set INTEGRATION=1 to run network tests")
    downloads = tmp_path / "downloads"
    downloads.mkdir()

    r1 = _run_shokz("download", "-o", str(downloads), _SHORT_URL)
    assert r1.returncode == 0, r1.stderr
    n1 = len(list(downloads.glob("*.mp3")))

    r2 = _run_shokz("download", "-o", str(downloads), "--force", _SHORT_URL)
    assert r2.returncode == 0, r2.stderr
    n2 = len(list(downloads.glob("*.mp3")))
    # --force re-downloads; collision suffix policy -> Foo.mp3 + Foo (2).mp3
    assert n2 == n1 + 1


def test_skip_existing_requires_both_manifest_entry_and_file_on_disk(tmp_path: Path) -> None:
    """Sprint 4.5 AC scenario 3."""
    if not _integration_enabled():
        pytest.skip("set INTEGRATION=1 to run network tests")
    downloads = tmp_path / "downloads"
    downloads.mkdir()

    r1 = _run_shokz("download", "-o", str(downloads), _SHORT_URL)
    assert r1.returncode == 0, r1.stderr

    # Manually delete the .mp3 (manifest entry remains)
    for mp3 in downloads.glob("*.mp3"):
        mp3.unlink()

    r2 = _run_shokz("download", "-o", str(downloads), _SHORT_URL)
    assert r2.returncode == 0, r2.stderr
    # Should have re-downloaded -- 1 .mp3 back
    assert len(list(downloads.glob("*.mp3"))) == 1


def test_shokz_library_list_shows_manifest_entries_as_a_table(tmp_path: Path) -> None:
    """Sprint 4.5 AC scenario 4."""
    if not _integration_enabled():
        pytest.skip("set INTEGRATION=1 to run network tests")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (tmp_path / "shokz.toml").write_text(f'[general]\noutput_dir = "{downloads}"\n')

    _run_shokz("download", "-o", str(downloads), _SHORT_URL)
    r = _run_shokz("library", "list", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    for col in ("TITLE", "SOURCE", "ID", "KBPS"):
        assert col in out, f"column {col!r} missing from library list output"


def test_shokz_library_show_track_id_prints_one_entry_full_detail(tmp_path: Path) -> None:
    """Sprint 4.5 AC scenarios 5 + 6."""
    if not _integration_enabled():
        pytest.skip("set INTEGRATION=1 to run network tests")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (tmp_path / "shokz.toml").write_text(f'[general]\noutput_dir = "{downloads}"\n')

    _run_shokz("download", "-o", str(downloads), _SHORT_URL)
    r = _run_shokz("library", "show", "jNQXAC9IVRw", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    for field in (
        "track_id",
        "original_title",
        "filename_stem",
        "mp3_path",
        "bitrate_kbps",
        "downloaded_at",
    ):
        assert field in r.stdout

    # missing track_id -> exit non-zero
    r2 = _run_shokz("library", "show", "no_such_track", cwd=tmp_path)
    assert r2.returncode != 0
    assert "no manifest entry" in (r2.stdout + r2.stderr).lower()


def test_shokz_library_verify_with_clean_state_exits_0(tmp_path: Path) -> None:
    """Sprint 4.5 AC scenario 7."""
    if not _integration_enabled():
        pytest.skip("set INTEGRATION=1 to run network tests")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (tmp_path / "shokz.toml").write_text(f'[general]\noutput_dir = "{downloads}"\n')

    _run_shokz("download", "-o", str(downloads), _SHORT_URL)
    r = _run_shokz("library", "verify", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "0 orphan" in r.stdout.lower() or "OK" in r.stdout


def test_shokz_library_verify_reports_orphan_files_on_disk_not_in_manifest(tmp_path: Path) -> None:
    """Sprint 4.5 AC scenario 8."""
    if not _integration_enabled():
        pytest.skip("set INTEGRATION=1 to run network tests")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (tmp_path / "shokz.toml").write_text(f'[general]\noutput_dir = "{downloads}"\n')

    # Create an orphan file directly (no download, no manifest)
    (downloads / "Mystery.mp3").write_bytes(b"\xff\xfb\x90\x00FAKEMP3")
    r = _run_shokz("library", "verify", cwd=tmp_path)
    assert r.returncode != 0
    combined = r.stdout + r.stderr
    assert "Mystery.mp3" in combined
    assert "orphan" in combined.lower()


def test_shokz_library_verify_reports_orphan_entries_manifest_not_on_disk(tmp_path: Path) -> None:
    """Sprint 4.5 AC scenario 9."""
    if not _integration_enabled():
        pytest.skip("set INTEGRATION=1 to run network tests")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (tmp_path / "shokz.toml").write_text(f'[general]\noutput_dir = "{downloads}"\n')

    _run_shokz("download", "-o", str(downloads), _SHORT_URL)
    # Manually delete the .mp3 (manifest entry remains)
    for mp3 in downloads.glob("*.mp3"):
        mp3.unlink()

    r = _run_shokz("library", "verify", cwd=tmp_path)
    assert r.returncode != 0
    combined = r.stdout + r.stderr
    assert "orphan manifest" in combined.lower()


def test_reconciliation_startup_scan_surfaces_orphan_files_as_warning(tmp_path: Path) -> None:
    """Sprint 4.5 AC scenario 10."""
    if not _integration_enabled():
        pytest.skip("set INTEGRATION=1 to run network tests")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "Mystery.mp3").write_bytes(b"\xff\xfb\x90\x00FAKEMP3")

    r = _run_shokz("download", "-o", str(downloads), _SHORT_URL)
    assert r.returncode == 0
    # WARNING for the orphan SHOULD be in stderr (logging configured to stderr)
    combined = r.stdout + r.stderr
    assert "orphan" in combined.lower() or "reconciliation" in combined.lower()
