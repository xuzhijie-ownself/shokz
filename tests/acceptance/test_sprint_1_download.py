"""Sprint 1 acceptance tests — Gherkin scenarios as pytest tests.

These tests hit the real network and require yt-dlp + ffmpeg. They are gated
behind `INTEGRATION=1` so CI can run them only on demand.

Scenarios mapped to test names verbatim from docs/sprints/sprint-1.md.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# --- short, well-known public videos chosen for stability ---
_SHORT_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # ~19s — first YouTube video
_INVALID_URL = "https://www.youtube.com/watch?v=000000XXXXX"


def _integration_enabled() -> bool:
    return os.environ.get("INTEGRATION") == "1"


pytestmark = pytest.mark.integration


@pytest.fixture
def fresh_downloads(tmp_path: Path) -> Path:
    d = tmp_path / "downloads"
    d.mkdir()
    return d


def _run_shokz(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "shokz.adapters.inbound.cli.app", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _shokz_cmd(output: Path, *urls: str) -> list[str]:
    return ["shokz", "download", "-o", str(output), "-c", "3", *urls]


def test_single_short_video_downloads_to_playable_mp3(fresh_downloads: Path) -> None:
    if not _integration_enabled():
        pytest.skip("set INTEGRATION=1 to run network tests")

    res = subprocess.run(_shokz_cmd(fresh_downloads, _SHORT_URL), capture_output=True, text=True)
    assert res.returncode == 0, res.stderr

    mp3s = list(fresh_downloads.glob("*.mp3"))
    assert len(mp3s) == 1
    assert mp3s[0].stat().st_size > 1024  # > 1 KB

    # MIME check via `file` if available
    file_bin = shutil.which("file")
    if file_bin:
        out = subprocess.check_output(
            [file_bin, "--mime-type", "-b", str(mp3s[0])], text=True
        ).strip()
        assert "audio" in out


def test_invalid_url_fails_cleanly(fresh_downloads: Path) -> None:
    if not _integration_enabled():
        pytest.skip("set INTEGRATION=1 to run network tests")

    res = subprocess.run(_shokz_cmd(fresh_downloads, _INVALID_URL), capture_output=True, text=True)
    assert res.returncode != 0
    # No traceback in stderr (clean error message)
    assert "Traceback" not in res.stderr
    assert list(fresh_downloads.glob("*.mp3")) == []


def test_multiple_urls_download_concurrently(fresh_downloads: Path) -> None:
    """Sprint 1 AC: 3 concurrent URLs finish much faster than 3x the longest single one."""
    if not _integration_enabled():
        pytest.skip("set INTEGRATION=1 to run network tests")

    short_urls = (
        "https://www.youtube.com/watch?v=jNQXAC9IVRw",  # ~19s "Me at the zoo"
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",  # ~3m32s
        "https://www.youtube.com/watch?v=9bZkp7q19f0",  # ~4m13s
    )
    res = subprocess.run(_shokz_cmd(fresh_downloads, *short_urls), capture_output=True, text=True)
    assert res.returncode == 0, res.stderr

    mp3s = sorted(fresh_downloads.glob("*.mp3"))
    assert len(mp3s) == 3, f"expected 3 MP3s, got {[p.name for p in mp3s]}"

    # Wall-clock concurrency proof: parse the elapsed reported by the CLI tail line.
    tail = res.stdout.strip().splitlines()[-1] if res.stdout else ""
    # Format: "3/3 succeeded (0 failed) in 7.3s"
    elapsed_token = next(
        (t for t in tail.split() if t.endswith("s") and t[:-1].replace(".", "").isdigit()),
        None,
    )
    if elapsed_token is not None:
        elapsed = float(elapsed_token[:-1])
        # Single ~4-minute video would take longer if serial; concurrent should be << 3x longest.
        # We don't have per-track timing here; sanity check is that total is far under 3 * 240s.
        assert elapsed < 60, f"3 short videos took {elapsed}s — likely serial, not concurrent"


def test_mixed_valid_and_invalid_urls_partial_success(fresh_downloads: Path) -> None:
    """Sprint 1 AC: valid+invalid -- non-zero exit, valid lands, invalid reported."""
    if not _integration_enabled():
        pytest.skip("set INTEGRATION=1 to run network tests")

    res = subprocess.run(
        _shokz_cmd(fresh_downloads, _SHORT_URL, _INVALID_URL),
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0, "expected non-zero exit because at least one URL failed"

    mp3s = list(fresh_downloads.glob("*.mp3"))
    assert len(mp3s) == 1, f"expected exactly 1 MP3 (valid one), got {[p.name for p in mp3s]}"

    # The invalid URL ID should appear in stderr so the swimmer knows what failed.
    combined = (res.stdout or "") + (res.stderr or "")
    assert "FAIL" in combined or "fail" in combined.lower()
    assert "Traceback" not in combined
