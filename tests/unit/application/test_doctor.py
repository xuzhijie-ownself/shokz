"""Sprint 10 — strict-TDD tests for RunDoctorUseCase.

`shokz doctor` runs read-only diagnostics that surface common
misconfiguration BEFORE the user hits a runtime failure: ffmpeg /
ffprobe / yt-dlp present, output_dir writable + not symlinked,
sufficient disk free vs the [disk] safety_multiplier.

Strict TDD: RED phase. The use case + CheckResult / DoctorResult
dataclasses do not yet exist. Tests will ImportError on first run.

Gherkin scenarios encoded as test functions:
  1. All-green: every check PASSes, has_failures==False
  2. Missing ffmpeg -> single FAIL, has_failures==True
  3. Missing ffprobe -> single FAIL
  4. Symlinked output_dir -> FAIL (reuses Sprint 9 helper as predicate)
  5. Output_dir not writable -> FAIL
  6. Disk space insufficient relative to safety_multiplier -> WARN
     (not FAIL: disk free is dynamic, transient warnings shouldn't
     block the user)
  7. yt-dlp version captured into the PASS message
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from shokz.application.use_cases.doctor import (
    CheckResult,
    DoctorResult,
    RunDoctorUseCase,
)
from shokz.config.schema import AppConfig

# ---------- helpers ----------


def _config(output_dir: Path, *, safety_multiplier: float = 2.0) -> AppConfig:
    return AppConfig.model_validate(
        {
            "general": {"output_dir": str(output_dir)},
            "disk": {"safety_multiplier": safety_multiplier},
        }
    )


def _make_uc(
    config: AppConfig,
    *,
    which: Callable[[str], str | None] | None = None,
    disk_free_bytes: int = 100 * 1024 * 1024 * 1024,  # 100 GiB default
    ytdlp_version: str = "2026.04.30",
) -> RunDoctorUseCase:
    """Build a doctor use case with all external surfaces injectable."""
    return RunDoctorUseCase(
        config=config,
        which=which or (lambda cmd: f"/fake/bin/{cmd}"),
        disk_free_bytes=lambda _path: disk_free_bytes,
        ytdlp_version=lambda: ytdlp_version,
    )


# ---------- scenarios ----------


@pytest.mark.asyncio
async def test_all_checks_pass_returns_zero_failures(tmp_path: Path) -> None:
    """All-green: every external dep present, output_dir is a real
    writable directory, plenty of disk free."""
    output = tmp_path / "downloads"
    output.mkdir()
    uc = _make_uc(_config(output))
    result = await uc.execute()

    assert isinstance(result, DoctorResult)
    assert all(isinstance(c, CheckResult) for c in result.checks)
    assert result.has_failures is False
    assert all(c.status == "PASS" for c in result.checks), [
        (c.name, c.status, c.message) for c in result.checks
    ]


@pytest.mark.asyncio
async def test_missing_ffmpeg_fails_check(tmp_path: Path) -> None:
    """`shutil.which("ffmpeg")` returning None -> FAIL on the ffmpeg
    check; has_failures flips True; other checks still run."""
    output = tmp_path / "downloads"
    output.mkdir()
    uc = _make_uc(
        _config(output),
        which=lambda cmd: None if cmd == "ffmpeg" else f"/fake/bin/{cmd}",
    )
    result = await uc.execute()

    ffmpeg = next(c for c in result.checks if c.name == "ffmpeg")
    assert ffmpeg.status == "FAIL"
    assert "ffmpeg" in ffmpeg.message.lower()
    assert result.has_failures is True


@pytest.mark.asyncio
async def test_missing_ffprobe_fails_check(tmp_path: Path) -> None:
    """ffprobe is a separate dep; check it independently of ffmpeg."""
    output = tmp_path / "downloads"
    output.mkdir()
    uc = _make_uc(
        _config(output),
        which=lambda cmd: None if cmd == "ffprobe" else f"/fake/bin/{cmd}",
    )
    result = await uc.execute()

    ffprobe = next(c for c in result.checks if c.name == "ffprobe")
    assert ffprobe.status == "FAIL"


@pytest.mark.asyncio
async def test_symlinked_output_dir_fails_check(tmp_path: Path) -> None:
    """Symlinked output_dir is the same condition Sprint 9 rejects in
    download/playlist/retry; doctor surfaces it as FAIL pre-emptively."""
    real_target = tmp_path / "real-target"
    real_target.mkdir()
    symlink_path = tmp_path / "symlink-output"
    os.symlink(real_target, symlink_path)
    uc = _make_uc(_config(symlink_path))
    result = await uc.execute()

    output_check = next(c for c in result.checks if c.name == "output_dir")
    assert output_check.status == "FAIL"
    assert "symlink" in output_check.message.lower()
    assert result.has_failures is True


@pytest.mark.asyncio
async def test_output_dir_not_writable_fails_check(tmp_path: Path) -> None:
    """Writability is a real-filesystem-dependent property; we simulate
    it by pointing output_dir at a path whose parent is read-only --
    chmod the parent to 0o500 so the use case's mkdir/touch fails."""
    real_parent = tmp_path / "ro-parent"
    real_parent.mkdir()
    output = real_parent / "downloads"  # doesn't exist; mkdir attempt fires
    real_parent.chmod(0o500)
    try:
        uc = _make_uc(_config(output))
        result = await uc.execute()
        write_check = next(c for c in result.checks if c.name == "output_dir_writable")
        assert write_check.status == "FAIL"
        assert result.has_failures is True
    finally:
        real_parent.chmod(0o700)  # so tmp_path cleanup works


@pytest.mark.asyncio
async def test_low_disk_space_warns_not_fails(tmp_path: Path) -> None:
    """Disk free space is dynamic. WARN (not FAIL) when it's below the
    safety_multiplier-scaled threshold, since a transient low-disk
    state shouldn't block the user from running other commands."""
    output = tmp_path / "downloads"
    output.mkdir()
    # Sub-1-GiB free: triggers WARN for any reasonable expected workload.
    uc = _make_uc(_config(output), disk_free_bytes=100 * 1024 * 1024)  # 100 MiB
    result = await uc.execute()

    disk_check = next(c for c in result.checks if c.name == "disk_free")
    assert disk_check.status == "WARN"
    # has_failures is False because WARN doesn't count as a failure.
    assert result.has_failures is False


@pytest.mark.asyncio
async def test_ytdlp_version_captured_in_pass_message(tmp_path: Path) -> None:
    """When yt-dlp resolves, the PASS message should embed the version
    string so the user can spot stale yt-dlp installs without --verbose."""
    output = tmp_path / "downloads"
    output.mkdir()
    uc = _make_uc(_config(output), ytdlp_version="2026.04.30.180000")
    result = await uc.execute()

    ytdlp = next(c for c in result.checks if c.name == "yt-dlp")
    assert ytdlp.status == "PASS"
    assert "2026.04.30.180000" in ytdlp.message


# ---------- CLI wiring (RED phase 2) ----------


def test_doctor_cli_exit_zero_when_all_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`shokz doctor` exits 0 when every check passes; output names
    every check with a PASS marker for human scannability."""
    import shutil as _shutil

    from typer.testing import CliRunner

    from shokz.adapters.inbound.cli.app import app

    output = tmp_path / "downloads"
    output.mkdir()
    monkeypatch.setattr(_shutil, "which", lambda cmd: f"/fake/bin/{cmd}")
    runner = CliRunner()
    result = runner.invoke(
        app, ["doctor", "--output", str(output)]
    )
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output
    # Every check name appears in the rendered table.
    for name in ("ffmpeg", "ffprobe", "yt-dlp", "output_dir", "disk_free"):
        assert name in result.output, (name, result.output)


def test_doctor_cli_exit_one_when_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When any check FAILs the CLI exits 1; the user sees FAIL markers
    in the rendered output."""
    import shutil as _shutil

    from typer.testing import CliRunner

    from shokz.adapters.inbound.cli.app import app

    output = tmp_path / "downloads"
    output.mkdir()
    # Force ffmpeg-missing so the ffmpeg check FAILs.
    monkeypatch.setattr(
        _shutil, "which", lambda cmd: None if cmd == "ffmpeg" else f"/fake/bin/{cmd}"
    )
    runner = CliRunner()
    result = runner.invoke(
        app, ["doctor", "--output", str(output)]
    )
    assert result.exit_code == 1, result.output
    assert "FAIL" in result.output
