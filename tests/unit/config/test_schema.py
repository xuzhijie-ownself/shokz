"""Unit tests for AppConfig validators -- Sprint 3."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shokz.config.schema import AppConfig, AudioPreset


def test_default_concurrency_is_1_sequential() -> None:
    """Sprint 6 AC: default concurrency is 1 (sequential by default)."""
    cfg = AppConfig()
    assert cfg.general.output_dir.as_posix().endswith("downloads")
    assert cfg.general.concurrency == 1
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


def test_concurrency_above_4_is_rejected() -> None:
    """Sprint 6 AC: --concurrency / general.concurrency cap is 4 (was 16).

    Higher caps are unsafe today (cross-process JSONL atomicity, filename
    TOCTOU). Sprint 8 may restore a higher cap once the filelock lands.
    """
    with pytest.raises(ValidationError) as exc:
        AppConfig.model_validate({"general": {"concurrency": 5}})
    msg = str(exc.value)
    assert "concurrency" in msg
    assert "less than or equal to 4" in msg.lower() or "<= 4" in msg


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


# ---------------------------------------------------------------------------
# Sprint 7: RetrySection defaults + bounds
# ---------------------------------------------------------------------------


def test_retry_defaults_match_sprint_7_spec() -> None:
    """Sprint 7 AC: retry budgets ship with conservative defaults."""
    cfg = AppConfig()
    assert cfg.retry.max_attempts_rate_limited == 3
    assert cfg.retry.max_attempts_network == 2
    assert cfg.retry.max_attempts_corrupt == 1
    assert cfg.retry.backoff_base_s == 1.0
    assert cfg.retry.wall_clock_budget_s == 180.0


@pytest.mark.parametrize(
    "field",
    ["max_attempts_rate_limited", "max_attempts_network", "max_attempts_corrupt"],
)
def test_retry_max_attempts_capped_at_5(field: str) -> None:
    """Sprint 7 GAN U5: cap retries at 5 so a TOML can't turn a 60-track
    playlist into a 60-hour wait. Parameterized so a future le=50 typo on
    one field is caught."""
    with pytest.raises(ValidationError, match=field):
        AppConfig.model_validate({"retry": {field: 99}})


def test_retry_wall_clock_budget_capped_at_600s() -> None:
    """Sprint 7 GAN U5: per-track wall-clock budget ceiling 600s."""
    with pytest.raises(ValidationError, match="wall_clock_budget_s"):
        AppConfig.model_validate({"retry": {"wall_clock_budget_s": 86400.0}})


def test_retry_wall_clock_budget_lower_bound_1s() -> None:
    """Sprint 7 GAN U5: wall-clock budget can't be less than 1s
    (otherwise even the first attempt would race the budget)."""
    with pytest.raises(ValidationError, match="wall_clock_budget_s"):
        AppConfig.model_validate({"retry": {"wall_clock_budget_s": 0.5}})


def test_retry_backoff_base_must_be_positive() -> None:
    """Sprint 7 GAN U5: backoff_base_s ge=0.1 prevents busy-loop retry."""
    with pytest.raises(ValidationError, match="backoff_base_s"):
        AppConfig.model_validate({"retry": {"backoff_base_s": 0.0}})


def test_retry_backoff_base_capped_at_60s() -> None:
    """Sprint 7 GAN U5 (review-pass extension): le=60.0 prevents an
    accidentally-huge base from turning a small batch into an hours-long stall."""
    with pytest.raises(ValidationError, match="backoff_base_s"):
        AppConfig.model_validate({"retry": {"backoff_base_s": 61.0}})


# ---------------------------------------------------------------------------
# Sprint 8: DiskSection + LockSection defaults + bounds
# ---------------------------------------------------------------------------


def test_disk_defaults_match_sprint_8_spec() -> None:
    """Sprint 8: disk pre-flight defaults are conservative and opt-in-strict."""
    cfg = AppConfig()
    assert cfg.disk.safety_multiplier == 2.0
    assert cfg.disk.require_estimate is False


def test_disk_safety_multiplier_lower_bound() -> None:
    """ge=1.0: a multiplier <1.0 would mean less-than-estimated free is OK."""
    with pytest.raises(ValidationError, match="safety_multiplier"):
        AppConfig.model_validate({"disk": {"safety_multiplier": 0.5}})


def test_disk_safety_multiplier_upper_bound() -> None:
    """le=10.0: capped so a typo can't turn 1 GB into 'need 10 GB'."""
    with pytest.raises(ValidationError, match="safety_multiplier"):
        AppConfig.model_validate({"disk": {"safety_multiplier": 11.0}})


def test_lock_default_timeout_is_5s() -> None:
    """Sprint 8: enough time for the holder to release on graceful exit;
    short enough not to surprise the user."""
    cfg = AppConfig()
    assert cfg.lock.timeout_s == 5.0


def test_lock_timeout_lower_bound_zero() -> None:
    """ge=0.0: 0 = immediate classification (no wait)."""
    cfg = AppConfig.model_validate({"lock": {"timeout_s": 0.0}})
    assert cfg.lock.timeout_s == 0.0


def test_lock_timeout_capped_at_60s() -> None:
    """le=60.0: a typo can't make `shokz` hang for hours waiting on a lock."""
    with pytest.raises(ValidationError, match="timeout_s"):
        AppConfig.model_validate({"lock": {"timeout_s": 3600.0}})
