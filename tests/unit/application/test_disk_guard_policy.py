"""Sprint 8 Phase 3: DiskGuardPolicy unit tests.

Drives:
  - Batch sum math + safety_multiplier
  - None entries are skipped (default best-effort mode)
  - require_estimate=True rejects None entries with helpful message
  - DiskFull carries need_bytes + have_bytes for structured inspection
  - humanfriendly format uses binary=True ("GiB" not "GB")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NamedTuple

import pytest

from shokz.application.policies.disk_guard import DiskGuardPolicy
from shokz.domain.errors import DiskFull


class _FakeUsage(NamedTuple):
    total: int
    used: int
    free: int


def _patch_free(monkeypatch: pytest.MonkeyPatch, free_bytes: int) -> None:
    """Make shutil.disk_usage return a controlled free-bytes value."""
    monkeypatch.setattr(
        "shokz.application.policies.disk_guard.shutil.disk_usage",
        lambda _path: _FakeUsage(
            total=10**12, used=10**12 - free_bytes, free=free_bytes
        ),
    )


def test_passes_when_sum_times_multiplier_fits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3 GiB estimate * 2.0 = 6 GiB needed; 10 GiB free -> passes silently."""
    _patch_free(monkeypatch, free_bytes=10 * 1024**3)
    policy = DiskGuardPolicy(safety_multiplier=2.0)
    policy.check_batch(tmp_path, estimates=[3 * 1024**3])  # no raise


def test_raises_disk_full_when_sum_times_multiplier_exceeds_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4 * 3 GiB * 2.0 = 24 GiB needed; 10 GiB free -> raises DiskFull
    (matches the spec's Gherkin "Disk guard ONE batch-level pre-flight" scenario).
    """
    _patch_free(monkeypatch, free_bytes=10 * 1024**3)
    policy = DiskGuardPolicy(safety_multiplier=2.0)
    with pytest.raises(DiskFull) as exc_info:
        policy.check_batch(tmp_path, estimates=[3 * 1024**3] * 4)
    err = exc_info.value
    # Sprint 8 GAN M2: humanfriendly binary=True -> "GiB" units.
    assert "GiB" in str(err)
    # Phase 1 GAN HIGH#1: structured attributes.
    assert err.need_bytes == 24 * 1024**3
    assert err.have_bytes == 10 * 1024**3


def test_none_entries_skipped_in_default_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sprint 8 GAN L4 default: None filesize_approx -> WARNING + proceed."""
    _patch_free(monkeypatch, free_bytes=10 * 1024**3)
    policy = DiskGuardPolicy(safety_multiplier=2.0, require_estimate=False)
    with caplog.at_level(logging.WARNING, logger="shokz.policy.disk_guard"):
        # 1 GiB known + 2 None entries; should NOT raise (10 GiB > 1*2 = 2 GiB).
        policy.check_batch(tmp_path, estimates=[1024**3, None, None])
    assert any(
        "skip disk guard" in rec.message and "2/3" in rec.message
        for rec in caplog.records
    )


def test_none_entries_raise_in_require_estimate_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sprint 8 GAN L4 strict: require_estimate=True -> reject None entries."""
    _patch_free(monkeypatch, free_bytes=10 * 1024**3)
    policy = DiskGuardPolicy(safety_multiplier=2.0, require_estimate=True)
    with pytest.raises(DiskFull, match="filesize_approx"):
        policy.check_batch(tmp_path, estimates=[1024**3, None, None])


def test_all_unknown_in_default_mode_returns_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All-None estimates in best-effort mode = nothing to check; no raise."""
    _patch_free(monkeypatch, free_bytes=0)  # even 0 free; we don't check
    policy = DiskGuardPolicy(safety_multiplier=2.0, require_estimate=False)
    policy.check_batch(tmp_path, estimates=[None, None, None])


def test_safety_multiplier_3x_doubles_needed_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1 GiB * 3.0 = 3 GiB needed; 2 GiB free -> raises DiskFull."""
    _patch_free(monkeypatch, free_bytes=2 * 1024**3)
    policy = DiskGuardPolicy(safety_multiplier=3.0)
    with pytest.raises(DiskFull) as exc_info:
        policy.check_batch(tmp_path, estimates=[1024**3])
    assert exc_info.value.need_bytes == 3 * 1024**3


def test_humanfriendly_binary_format_uses_gibibytes_not_gigabytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sprint 8 GAN M2: error message uses 'GiB' (IEC binary) not 'GB' (SI)."""
    _patch_free(monkeypatch, free_bytes=100 * 1024**2)  # 100 MiB
    policy = DiskGuardPolicy(safety_multiplier=2.0)
    with pytest.raises(DiskFull) as exc_info:
        policy.check_batch(tmp_path, estimates=[500 * 1024**2])  # 500 MiB
    msg = str(exc_info.value)
    # binary=True yields "GiB"/"MiB" not "GB"/"MB"
    assert "GiB" in msg or "MiB" in msg
    # "GB" should NOT appear except as part of "GiB" -- strip "GiB" first.
    assert "GB" not in msg.replace("GiB", "")
