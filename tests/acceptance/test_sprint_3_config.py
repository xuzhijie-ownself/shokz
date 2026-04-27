"""Sprint 3 acceptance tests -- Gherkin scenarios as pytest tests.

Most are pure CLI tests (no network). One is gated INTEGRATION=1 because it
runs an actual download to verify the custom-bitrate config wiring end-to-end.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_SHORT_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def _integration_enabled() -> bool:
    return os.environ.get("INTEGRATION") == "1"


def _clean_env(tmp_path: Path) -> dict[str, str]:
    """Sprint 3 review fix C10: override HOME to a tmp dir so the subprocess
    doesn't pick up the developer's real ~/.config/shokz/config.toml.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("SHOKZ_")}
    env["HOME"] = str(tmp_path)
    return env


def _shokz(
    *args: str, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["shokz", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env=env,
    )


def test_built_in_defaults_apply_when_no_config_file_exists(tmp_path: Path) -> None:
    """Sprint 3 AC scenario; Sprint 6: default concurrency lowered 3 -> 1."""
    # Run from a clean cwd (no shokz.toml) and an env stripped of SHOKZ_*.
    clean_env = _clean_env(tmp_path)
    res = _shokz("config", "show", cwd=tmp_path, env=clean_env)
    assert res.returncode == 0, res.stderr
    out = res.stdout
    assert "general.concurrency = 1" in out
    assert "built-in" in out
    assert "audio.preset = <AudioPreset.SWIM_STANDARD" in out or "swim-standard" in out
    assert "filenames.template = '{title}'" in out


def test_project_local_shokz_toml_overrides_built_in_defaults(tmp_path: Path) -> None:
    """Sprint 3 AC scenario; Sprint 6: cap lowered 16 -> 4."""
    (tmp_path / "shokz.toml").write_text("[general]\nconcurrency = 4\n")
    clean_env = _clean_env(tmp_path)
    res = _shokz("config", "show", cwd=tmp_path, env=clean_env)
    assert res.returncode == 0, res.stderr
    assert "general.concurrency = 4" in res.stdout
    assert "shokz.toml" in res.stdout


def test_env_var_overrides_project_toml(tmp_path: Path) -> None:
    """Sprint 3 AC scenario."""
    (tmp_path / "shokz.toml").write_text("[general]\nconcurrency = 2\n")
    env = {**os.environ, "SHOKZ_GENERAL__CONCURRENCY": "3"}
    res = _shokz("config", "show", cwd=tmp_path, env=env)
    assert res.returncode == 0, res.stderr
    assert "general.concurrency = 3" in res.stdout
    assert "env SHOKZ_GENERAL__CONCURRENCY" in res.stdout


def test_cli_flag_overrides_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sprint 3 AC scenario.

    Sprint 3 review fix C11: was a `python -c` subprocess wrapping the loader,
    which the python-reviewer flagged as a bad smell (process-spawn overhead +
    opaque failures + redundant with the unit test). Replaced with a direct
    in-process call. The CLI surface for the same scenario is exercised by
    tests/unit/test_cli_smoke.py via Typer CliRunner.
    """
    from shokz.config.loader import load_config

    monkeypatch.setenv("SHOKZ_GENERAL__CONCURRENCY", "3")
    loaded = load_config(
        cli_overrides={"general.concurrency": 4},
        user_toml=tmp_path / "no-user.toml",
        project_toml=tmp_path / "no-project.toml",
    )
    assert loaded.config.general.concurrency == 4
    assert loaded.sources["general.concurrency"] == "CLI"


def test_shokz_config_init_writes_a_commented_sample_toml(tmp_path: Path) -> None:
    """Sprint 3 AC: 'shokz config init writes a commented sample TOML'."""
    res = _shokz("config", "init", "--path", str(tmp_path / "shokz.toml"))
    assert res.returncode == 0, res.stderr
    written = (tmp_path / "shokz.toml").read_text()
    for section in ("[general]", "[audio]", "[filenames]", "[sources.youtube]", "[logging]"):
        assert section in written


def test_shokz_config_init_refuses_to_overwrite_existing_file_no_force(tmp_path: Path) -> None:
    """Sprint 3 AC: 'shokz config init refuses to overwrite existing file (no --force)'."""
    target = tmp_path / "shokz.toml"
    target.write_text("# my custom config\n[general]\nconcurrency = 9\n")
    original = target.read_text()

    res = _shokz("config", "init", "--path", str(target))
    assert res.returncode != 0
    assert "exists" in (res.stdout + res.stderr).lower()
    assert "force" in (res.stdout + res.stderr).lower()
    assert target.read_text() == original  # untouched


def test_shokz_config_path_lists_which_config_files_were_loaded(tmp_path: Path) -> None:
    """Sprint 3 AC: 'shokz config path lists which config files were loaded'."""
    (tmp_path / "shokz.toml").write_text("[general]\nconcurrency = 4\n")
    clean_env = _clean_env(tmp_path)
    res = _shokz("config", "path", cwd=tmp_path, env=clean_env)
    assert res.returncode == 0, res.stderr
    out = res.stdout
    assert "loaded" in out.lower()
    assert "shokz.toml" in out
    assert "missing" in out.lower()


def test_invalid_toml_produces_a_clear_error_not_a_python_traceback(tmp_path: Path) -> None:
    """Sprint 3 AC: 'Invalid TOML produces a clear error, not a Python traceback'."""
    (tmp_path / "shokz.toml").write_text("[general\nconcurrency = 5\n")  # syntax error
    clean_env = _clean_env(tmp_path)
    res = _shokz("config", "show", cwd=tmp_path, env=clean_env)
    assert res.returncode != 0
    combined = res.stdout + res.stderr
    assert "TOML" in combined or "parse" in combined.lower()
    assert "shokz.toml" in combined
    assert "Traceback" not in combined


def test_invalid_value_e_g_concurrency_minus_one_rejected_at_load_time(tmp_path: Path) -> None:
    """Sprint 3 AC: 'Invalid value (e.g. concurrency = -1) is rejected at load time'."""
    (tmp_path / "shokz.toml").write_text("[general]\nconcurrency = -1\n")
    clean_env = _clean_env(tmp_path)
    res = _shokz("config", "show", cwd=tmp_path, env=clean_env)
    assert res.returncode != 0
    combined = res.stdout + res.stderr
    assert "general.concurrency" in combined
    assert ">= 1" in combined or "greater than or equal" in combined.lower()


@pytest.mark.integration
def test_custom_audio_preset_overrides_via_toml(tmp_path: Path) -> None:
    """Sprint 3 AC: 'Custom audio preset overrides via TOML' (end-to-end)."""
    if not _integration_enabled():
        pytest.skip("set INTEGRATION=1 to run network tests")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (tmp_path / "shokz.toml").write_text('[audio]\npreset = "custom"\nbitrate_kbps = 96\n')
    res = _shokz("download", "-o", str(downloads), _SHORT_URL, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    mp3s = list(downloads.glob("*.mp3"))
    assert len(mp3s) == 1
    # Verify bitrate via ffprobe
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            str(mp3s[0]),
        ],
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0
    import json

    data = json.loads(probe.stdout)
    bitrate_bps = int(data["streams"][0]["bit_rate"])
    # Allow ±10% tolerance for CBR encoder rounding
    assert 86 * 1000 <= bitrate_bps <= 106 * 1000, (
        f"expected ~96 kbps, got {bitrate_bps / 1000:.1f} kbps"
    )
