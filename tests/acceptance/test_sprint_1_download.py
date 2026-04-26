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
