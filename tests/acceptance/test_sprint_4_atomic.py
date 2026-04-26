"""Sprint 4 acceptance tests -- Gherkin scenarios as pytest tests."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_SHORT_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def _integration_enabled() -> bool:
    return os.environ.get("INTEGRATION") == "1"


pytestmark = pytest.mark.integration


def _shokz_bin() -> str:
    return str(Path(".venv/bin/shokz").resolve())


def _shokz_env() -> dict[str, str]:
    venv_bin = str(Path(".venv/bin").resolve())
    return {**os.environ, "PATH": f"{venv_bin}:{os.environ.get('PATH', '')}"}


def test_atomic_move_via_os_replace_plus_dual_fsync_acceptance(tmp_path: Path) -> None:
    """Sprint 4 AC: 'Atomic move via os.replace + dual fsync' (end-to-end)."""
    if not _integration_enabled():
        pytest.skip("set INTEGRATION=1 to run network tests")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    res = subprocess.run(
        [_shokz_bin(), "download", "-o", str(downloads), _SHORT_URL],
        capture_output=True,
        text=True,
        env=_shokz_env(),
    )
    assert res.returncode == 0, res.stderr
    # Top-level mp3 exists
    mp3s = list(downloads.glob("*.mp3"))
    assert len(mp3s) == 1
    # No partial files in .tmp/
    partials = list((downloads / ".tmp").glob("*.partial")) if (downloads / ".tmp").exists() else []
    assert partials == []
    # Manifest entry was appended
    manifest = downloads / ".shokz" / "manifest.jsonl"
    assert manifest.exists()
    rows = manifest.read_text().strip().splitlines()
    assert len(rows) == 1
    parsed = json.loads(rows[0])
    assert parsed["schema_version"] == 1
    assert parsed["source"] == "youtube"


def test_sigkill_mid_encode_leaves_no_partial_mp3_in_downloads(tmp_path: Path) -> None:
    """Sprint 4 AC: 'SIGKILL mid-encode leaves no partial *.mp3 in downloads/'.

    Uses scripts/kill-test.sh which spawns shokz, sleeps 4s, sends SIGKILL,
    then asserts no top-level *.mp3 in the spawned downloads dir.
    """
    if not _integration_enabled():
        pytest.skip("set INTEGRATION=1 to run network + kill test")
    # Use a longer URL so the kill lands during encoding (not after success).
    long_url = "https://www.youtube.com/watch?v=eiV0nvJ9fRM"  # 7+ hour video
    res = subprocess.run(
        ["bash", "scripts/kill-test.sh", long_url],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],  # py-rev Issue 5
    )
    # Script exit codes: 0 = atomic OK, 1 = partial survived, 2/3 = setup issue
    assert res.returncode == 0, (
        f"kill-test failed (exit {res.returncode}):\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
