"""Sprint 9 — TDD tests for `_runtime.assert_output_dir_safe`.

The new helper lifts symlink rejection out of `BatchDownloadUseCase`
into a CLI-layer pre-check so `download / playlist / retry` all reject
a symlinked `--output` BEFORE acquiring the cross-process lock. M1
carry-forward from Sprint 8.5 Phase C.

Strict TDD: these tests are written BEFORE the helper exists. Initial
run MUST fail with ImportError.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from shokz.adapters.inbound.cli._runtime import assert_output_dir_safe
from shokz.config.schema import AppConfig
from shokz.domain.errors import NameOutsideOutputDir


def _config_with_output_dir(output_dir: Path) -> AppConfig:
    """Build an AppConfig with the output_dir override applied; everything
    else defaults. Pure config construction; no filesystem side effects."""
    return AppConfig.model_validate(
        {"general": {"output_dir": str(output_dir)}}
    )


def test_assert_output_dir_safe_passes_for_real_directory(tmp_path: Path) -> None:
    """Plain directory under tmp_path — no raise."""
    real_dir = tmp_path / "downloads"
    real_dir.mkdir()
    cfg = _config_with_output_dir(real_dir)
    assert_output_dir_safe(cfg)  # must NOT raise


def test_assert_output_dir_safe_passes_for_nonexistent_directory(tmp_path: Path) -> None:
    """Output dir doesn't exist YET — must pass; the use case creates it
    later via `output_dir.mkdir(parents=True, exist_ok=True)`. Pre-check
    is about safety (symlinks), not existence."""
    nonexistent = tmp_path / "not-yet-created"
    cfg = _config_with_output_dir(nonexistent)
    assert_output_dir_safe(cfg)


def test_assert_output_dir_safe_rejects_symlinked_directory(tmp_path: Path) -> None:
    """Output dir IS a symlink to another dir → raise NameOutsideOutputDir
    BEFORE any other CLI work happens."""
    real_target = tmp_path / "real-target"
    real_target.mkdir()
    symlink_path = tmp_path / "symlink-output"
    os.symlink(real_target, symlink_path)
    cfg = _config_with_output_dir(symlink_path)
    with pytest.raises(NameOutsideOutputDir, match="symlink"):
        assert_output_dir_safe(cfg)


def test_assert_output_dir_safe_rejects_symlinked_ancestor(tmp_path: Path) -> None:
    """An ANCESTOR of output_dir is a symlink → raise. Defense in depth:
    a symlink in the middle of the path is just as dangerous as one at
    the leaf because the resolved path may escape the user's intended
    output area."""
    real_target = tmp_path / "real-base"
    real_target.mkdir()
    symlink_base = tmp_path / "symlink-base"
    os.symlink(real_target, symlink_base)
    nested = symlink_base / "downloads"  # symlink in the path
    cfg = _config_with_output_dir(nested)
    with pytest.raises(NameOutsideOutputDir, match="symlink"):
        assert_output_dir_safe(cfg)


# --- CLI wiring tests: all 3 commands must reject BEFORE lock acquire.


def _symlinked_output(tmp_path: Path) -> Path:
    """Build a real-target dir + symlink pointing to it; return the symlink."""
    real_target = tmp_path / "real-target"
    real_target.mkdir()
    symlink_path = tmp_path / "symlink-output"
    os.symlink(real_target, symlink_path)
    return symlink_path


def _no_lock_dir_was_created(symlink_output: Path) -> bool:
    """The .shokz/locks/ dir is created lazily on FileLockPolicy.__enter__.
    If the symlink rejection fired BEFORE lock acquire, the dir does NOT
    exist on either side of the symlink."""
    real_target = symlink_output.resolve()
    return not (real_target / ".shokz" / "locks").exists()


def test_download_rejects_symlinked_output_before_lock(tmp_path: Path) -> None:
    """Sprint 9 wiring: `shokz download` rejects symlinked --output and
    NEVER acquires the lock. Verified via the absence of `.shokz/locks/`
    on the symlink target."""
    from typer.testing import CliRunner

    from shokz.adapters.inbound.cli.app import app

    symlink_output = _symlinked_output(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["download", "https://x/y/fake", "--output", str(symlink_output)],
    )
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output.lower()
    assert _no_lock_dir_was_created(symlink_output), (
        "lock dir was created -- symlink rejection fired AFTER lock acquire"
    )


def test_retry_rejects_symlinked_output_before_lock(tmp_path: Path) -> None:
    """Sprint 9 wiring: `shokz retry` rejects symlinked --output BEFORE the
    stat short-circuit. Without this fix, retry would either silently
    exit 0 (when target lacked failures.jsonl) or waste a lock acquire +
    iter_failures read on a symlinked target. Per Sprint 8.5 Phase C M1."""
    from typer.testing import CliRunner

    from shokz.adapters.inbound.cli.app import app

    symlink_output = _symlinked_output(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["retry", "--output", str(symlink_output)])
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output.lower()
    assert _no_lock_dir_was_created(symlink_output)


def test_playlist_rejects_symlinked_output_before_lock(tmp_path: Path) -> None:
    """Sprint 9 wiring: same contract as download/retry."""
    from typer.testing import CliRunner

    from shokz.adapters.inbound.cli.app import app

    symlink_output = _symlinked_output(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["playlist", "https://x/y/playlist", "--output", str(symlink_output)],
    )
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output.lower()
    assert _no_lock_dir_was_created(symlink_output)
