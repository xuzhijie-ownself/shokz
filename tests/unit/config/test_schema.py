"""Unit tests for AppConfig validators -- Sprint 3."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shokz.config.schema import AppConfig, AudioPreset


def test_defaults_validate_clean() -> None:
    """Sprint 3 AC: 'Built-in defaults apply when no config file exists' (schema half)."""
    cfg = AppConfig()
    assert cfg.general.output_dir.as_posix().endswith("downloads")
    assert cfg.general.concurrency == 3
    assert cfg.audio.preset is AudioPreset.SWIM_STANDARD
    assert cfg.filenames.template == "{title}"
    assert cfg.sources.youtube.ejs_source == "ejs:github"


def test_invalid_value_concurrency_negative_is_rejected_at_load_time() -> None:
    """Sprint 3 AC: 'Invalid value (e.g. concurrency = -1) is rejected at load time'."""
    with pytest.raises(ValidationError) as exc:
        AppConfig.model_validate({"general": {"concurrency": -1}})
    msg = str(exc.value)
    assert "concurrency" in msg
    assert "greater than or equal to 1" in msg.lower() or ">= 1" in msg


def test_channels_must_be_one_or_two() -> None:
    with pytest.raises(ValidationError, match="channels"):
        AppConfig.model_validate({"audio": {"channels": 3}})


def test_unknown_top_level_key_rejected() -> None:
    """Frozen + extra='forbid' = unknown keys fail loud."""
    with pytest.raises(ValidationError, match="extra"):
        AppConfig.model_validate({"banana": "yes"})


def test_collision_policy_must_be_valid_enum() -> None:
    with pytest.raises(ValidationError, match="collision"):
        AppConfig.model_validate({"filenames": {"collision": "bogus"}})


def test_log_level_must_match_pattern() -> None:
    with pytest.raises(ValidationError, match=r"logging|level"):
        AppConfig.model_validate({"logging": {"level": "VERBOSE"}})
