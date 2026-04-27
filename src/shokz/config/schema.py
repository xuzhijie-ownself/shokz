"""AppConfig schema (Pydantic v2). Sprint 3 wires Sprint 1+2 knobs."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AudioPreset(StrEnum):
    SWIM_LOW = "swim-low"
    SWIM_STANDARD = "swim-standard"
    SWIM_HIGH = "swim-high"
    CUSTOM = "custom"


class GeneralConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    output_dir: Path = Field(default=Path("./downloads"))
    # Sprint 6: default lowered 3 -> 1 (sequential by default). Max lowered
    # 16 -> 4 because the in-process pool is the ONLY safe parallelism
    # mechanism today; multi-process invocations against the same output_dir
    # are NOT safe (manifest JSONL atomicity beyond PIPE_BUF, filename-resolver
    # TOCTOU, .tmp clobber). Sprint 8 lands cross-process filelock and may
    # restore a higher cap then.
    concurrency: int = Field(default=1, ge=1, le=4)
    keep_raw: bool = False


class AudioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preset: AudioPreset = AudioPreset.SWIM_STANDARD
    bitrate_kbps: int = Field(default=64, ge=16, le=320)
    channels: int = Field(default=1, ge=1, le=2)
    sample_rate_hz: int = Field(default=44100, ge=8000, le=192000)


class FilenamesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    template: str = "{title}"
    collision: str = Field(default="suffix", pattern=r"^(suffix|overwrite|skip|fail)$")
    fat_safe: bool = True
    max_length: int = Field(default=120, ge=8, le=255)


class YouTubeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ejs_source: str = "ejs:github"
    sleep_requests: float = Field(default=1.0, ge=0.0, le=60.0)
    playlist_confirm_threshold: int = Field(default=50, ge=1, le=10000)


class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")


class RetrySection(BaseModel):
    """Sprint 7: classified retry policy budgets.

    All bounds enforced (Sprint 7 GAN U5 / architect#7) so a TOML with
    `max_attempts_rate_limited = 999` can't quietly turn a 60-track playlist
    into a 60-hour wait. validate_default=True so an invalid default fails
    fast at config load instead of at first access.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    max_attempts_rate_limited: int = Field(default=3, ge=0, le=5)
    max_attempts_network: int = Field(default=2, ge=0, le=5)
    max_attempts_corrupt: int = Field(default=1, ge=0, le=5)
    backoff_base_s: float = Field(default=1.0, ge=0.1, le=60.0)
    # Per-track wall-clock cap. Covers download time + sleeps (NOT just
    # sleeps; Sprint 7 GAN C4 / silent#5). Worst-case RateLimited budget:
    # 5 + 30 + 120 = 155s of sleep + a few seconds work fits.
    wall_clock_budget_s: float = Field(default=180.0, ge=1.0, le=600.0)


class DiskSection(BaseModel):
    """Sprint 8: disk-guard pre-flight policy.

    safety_multiplier: estimated_bytes * multiplier must fit in free space.
        Default 2.0 leaves headroom for encode-time intermediate files +
        OS overhead. Bounds 1.0..10.0 (1.0 = no slack; 10.0 = paranoid).
    require_estimate: if True, refuse to download tracks where the source
        couldn't predict file size (live streams, HLS without estimate).
        Default False preserves Sprint 7 best-effort behavior; users who
        want strict pre-flight should explicitly opt in (Sprint 8 GAN L4).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    safety_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    require_estimate: bool = False


class LockSection(BaseModel):
    """Sprint 8: cross-process file lock policy.

    timeout_s: how long to wait for an existing lock to release before
        classifying via the 5-step priority list (corrupt meta -> stale
        PID -> permission-denied -> PID-reuse -> AnotherRunInProgress).
        0.0 = no wait (immediate classification on first contention).
        Bounded 0.0..60.0 so a typo can't make `shokz` hang silently
        for hours.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    timeout_s: float = Field(default=5.0, ge=0.0, le=60.0)


class AppConfig(BaseModel):
    """Top-level config. Frozen so accidental mutation is rejected.

    populate_by_name=True (Sprint 3 review fix C2) lets `model_validate` accept
    BOTH the alias 'logging' AND the python attr 'logging_' — so model_dump()
    round-trips cleanly via model_validate.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    filenames: FilenamesConfig = Field(default_factory=FilenamesConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    logging_: LoggingConfig = Field(default_factory=LoggingConfig, alias="logging")
    retry: RetrySection = Field(default_factory=RetrySection)
    disk: DiskSection = Field(default_factory=DiskSection)
    lock: LockSection = Field(default_factory=LockSection)

    @model_validator(mode="after")
    def _custom_preset_requires_explicit_bitrate(self) -> AppConfig:
        # Sprint 3 sanity: if preset != custom, bitrate_kbps may differ from
        # preset's value -- that is OK (preset is the *source* of truth in
        # the resolver). We don't enforce here. Hook reserved for Sprint 7.
        return self
