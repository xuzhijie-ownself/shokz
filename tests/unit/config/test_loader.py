"""Unit tests for the layered config loader -- Sprint 3."""

from __future__ import annotations

from pathlib import Path

import pytest

from shokz.config.loader import ConfigLoadError, load_config


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_built_in_defaults_apply_when_no_config_file_exists(tmp_path: Path) -> None:
    """Sprint 3 AC: 'Built-in defaults apply when no config file exists'."""
    loaded = load_config(
        user_toml=tmp_path / "no-such-user.toml",
        project_toml=tmp_path / "no-such-project.toml",
        env={},
    )
    assert loaded.config.general.concurrency == 3
    assert loaded.config.audio.preset.value == "swim-standard"
    assert loaded.sources["general.concurrency"] == "built-in"
    assert loaded.loaded_files == ()
    assert (tmp_path / "no-such-user.toml") in loaded.missing_files
    assert (tmp_path / "no-such-project.toml") in loaded.missing_files


def test_project_local_shokz_toml_overrides_built_in_defaults(tmp_path: Path) -> None:
    """Sprint 3 AC: 'Project-local shokz.toml overrides built-in defaults'."""
    project = tmp_path / "shokz.toml"
    _write(project, "[general]\nconcurrency = 5\n")

    loaded = load_config(
        user_toml=tmp_path / "no-user.toml",
        project_toml=project,
        env={},
    )
    assert loaded.config.general.concurrency == 5
    assert loaded.sources["general.concurrency"] == str(project)


def test_env_var_overrides_project_toml(tmp_path: Path) -> None:
    """Sprint 3 AC: 'Env var overrides project TOML'."""
    project = tmp_path / "shokz.toml"
    _write(project, "[general]\nconcurrency = 5\n")

    loaded = load_config(
        user_toml=tmp_path / "no-user.toml",
        project_toml=project,
        env={"SHOKZ_GENERAL__CONCURRENCY": "7"},
    )
    assert loaded.config.general.concurrency == 7
    assert "env SHOKZ_GENERAL__CONCURRENCY" in loaded.sources["general.concurrency"]


def test_cli_flag_overrides_env_var(tmp_path: Path) -> None:
    """Sprint 3 AC: 'CLI flag overrides env var'."""
    loaded = load_config(
        cli_overrides={"general.concurrency": 9},
        user_toml=tmp_path / "no-user.toml",
        project_toml=tmp_path / "no-project.toml",
        env={"SHOKZ_GENERAL__CONCURRENCY": "7"},
    )
    assert loaded.config.general.concurrency == 9
    assert loaded.sources["general.concurrency"] == "CLI"


def test_config_precedence_unit_level(tmp_path: Path) -> None:
    """Sprint 3 AC: 'Config precedence -- unit-level'.

    Stack a value across all 4 layers, assert top-most (CLI) wins and
    source-tracking dict reports the right origin for each key.
    """
    user = tmp_path / "user.toml"
    project = tmp_path / "project.toml"
    _write(user, "[general]\nconcurrency = 2\n")
    _write(project, "[general]\nconcurrency = 5\n")

    loaded = load_config(
        cli_overrides={"general.concurrency": 9},
        user_toml=user,
        project_toml=project,
        env={"SHOKZ_GENERAL__CONCURRENCY": "7"},
    )
    assert loaded.config.general.concurrency == 9
    assert loaded.sources["general.concurrency"] == "CLI"

    # A key set ONLY in TOML still records the TOML source.
    _write(project, '[general]\nconcurrency = 5\n[audio]\npreset = "swim-low"\n')
    loaded2 = load_config(
        user_toml=tmp_path / "no-user.toml",
        project_toml=project,
        env={},
    )
    assert loaded2.sources["audio.preset"] == str(project)


def test_invalid_toml_produces_a_clear_error_not_a_python_traceback(tmp_path: Path) -> None:
    """Sprint 3 AC: 'Invalid TOML produces a clear error, not a Python traceback'."""
    project = tmp_path / "shokz.toml"
    _write(project, "[general\nconcurrency = 5\n")  # missing closing bracket

    with pytest.raises(ConfigLoadError) as exc:
        load_config(
            user_toml=tmp_path / "no-user.toml",
            project_toml=project,
            env={},
        )
    assert "TOML" in str(exc.value) or "parse" in str(exc.value).lower()
    assert str(project) in str(exc.value)


def test_invalid_value_concurrency_minus_one_rejected(tmp_path: Path) -> None:
    """Sprint 3 AC: 'Invalid value (e.g. concurrency = -1) is rejected at load time'."""
    project = tmp_path / "shokz.toml"
    _write(project, "[general]\nconcurrency = -1\n")

    with pytest.raises(ConfigLoadError) as exc:
        load_config(
            user_toml=tmp_path / "no-user.toml",
            project_toml=project,
            env={},
        )
    msg = str(exc.value)
    assert "general.concurrency" in msg
    assert ">= 1" in msg or "greater than or equal" in msg.lower()


def test_custom_audio_preset_overrides_via_toml(tmp_path: Path) -> None:
    """Sprint 3 AC: 'Custom audio preset overrides via TOML' (loader half).

    The full encoding integration runs in acceptance with INTEGRATION=1.
    """
    project = tmp_path / "shokz.toml"
    _write(project, '[audio]\npreset = "custom"\nbitrate_kbps = 96\n')

    loaded = load_config(
        user_toml=tmp_path / "no-user.toml",
        project_toml=project,
        env={},
    )
    from shokz.config.presets import resolve_audio_spec

    spec = resolve_audio_spec(loaded.config.audio)
    assert spec.bitrate_kbps == 96
    assert spec.codec == "mp3"


def test_env_value_string_coerced_to_int() -> None:
    """SHOKZ_*=numeric_string should round-trip to int via Pydantic."""
    loaded = load_config(
        user_toml=Path("/no-user.toml"),
        project_toml=Path("/no-project.toml"),
        env={"SHOKZ_GENERAL__CONCURRENCY": "4"},
    )
    assert loaded.config.general.concurrency == 4
    assert isinstance(loaded.config.general.concurrency, int)


# ============================================================
# Sprint 3 review-fix coverage
# ============================================================


def test_unflatten_collision_raises_config_load_error(tmp_path: Path) -> None:
    """C1: a key set both as scalar and as a prefix raises ConfigLoadError."""
    with pytest.raises(ConfigLoadError, match="key conflict"):
        load_config(
            cli_overrides={
                "general": "scalar-value",
                "general.concurrency": 5,
            },
            user_toml=tmp_path / "no-user.toml",
            project_toml=tmp_path / "no-project.toml",
            env={},
        )


def test_logging_alias_round_trip_via_model_dump_validate() -> None:
    """C2: AppConfig.model_dump() output must round-trip through model_validate()."""
    from shokz.config.schema import AppConfig

    cfg = AppConfig()
    dumped = cfg.model_dump(by_alias=True)
    reloaded = AppConfig.model_validate(dumped)
    assert reloaded == cfg

    # And the python-attr form must work too (populate_by_name=True).
    dumped_py = cfg.model_dump()
    reloaded_py = AppConfig.model_validate(dumped_py)
    assert reloaded_py == cfg


def test_load_toml_oserror_raises_config_load_error(tmp_path: Path) -> None:
    """F3: OSError during read() (e.g. PermissionError) -> ConfigLoadError.

    Was a fragile Path.open monkeypatch (fired for pytest's own file ops);
    now uses real chmod 0o000 to make the file unreadable for the duration
    of the test. Restored to 0o600 on teardown so pytest can clean tmp_path.
    """
    import os

    if os.geteuid() == 0:
        pytest.skip("running as root: chmod 0 doesn't block reads")

    project = tmp_path / "shokz.toml"
    project.write_text("[general]\nconcurrency = 5\n")
    project.chmod(0o000)
    try:
        with pytest.raises(ConfigLoadError, match="failed to read TOML"):
            load_config(
                user_toml=tmp_path / "no-user.toml",
                project_toml=project,
                env={},
            )
    finally:
        project.chmod(0o600)


def test_env_inf_and_nan_for_floats_rejected(tmp_path: Path) -> None:
    """C4: SHOKZ_*=inf / nan must NOT silently become a working float."""
    with pytest.raises(ConfigLoadError):
        load_config(
            user_toml=tmp_path / "no-user.toml",
            project_toml=tmp_path / "no-project.toml",
            env={"SHOKZ_SOURCES__YOUTUBE__SLEEP_REQUESTS": "inf"},
        )

    with pytest.raises(ConfigLoadError):
        load_config(
            user_toml=tmp_path / "no-user.toml",
            project_toml=tmp_path / "no-project.toml",
            env={"SHOKZ_SOURCES__YOUTUBE__SLEEP_REQUESTS": "nan"},
        )


def test_validation_error_message_includes_source_file(tmp_path: Path) -> None:
    """C8: invalid value's error message names the file/layer it came from."""
    project = tmp_path / "shokz.toml"
    project.write_text("[general]\nconcurrency = -1\n")
    with pytest.raises(ConfigLoadError) as exc:
        load_config(
            user_toml=tmp_path / "no-user.toml",
            project_toml=project,
            env={},
        )
    msg = str(exc.value)
    assert "general.concurrency" in msg
    assert str(project) in msg or "from:" in msg


def test_builtin_defaults_derived_from_appconfig() -> None:
    """C12: BUILTIN_DEFAULTS is the same as AppConfig().model_dump (single source)."""
    from shokz.config.defaults import BUILTIN_DEFAULTS
    from shokz.config.schema import AppConfig

    # Sanity: a key from each section must be present and match the schema default.
    cfg = AppConfig()
    assert BUILTIN_DEFAULTS["general.concurrency"] == cfg.general.concurrency
    assert BUILTIN_DEFAULTS["audio.preset"] == cfg.audio.preset.value
    # logging uses the alias in flattened form
    assert BUILTIN_DEFAULTS["logging.level"] == cfg.logging_.level
