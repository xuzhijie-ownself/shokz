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
    concurrency: int = Field(default=3, ge=1, le=16)
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


class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")


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

    @model_validator(mode="after")
    def _custom_preset_requires_explicit_bitrate(self) -> AppConfig:
        # Sprint 3 sanity: if preset != custom, bitrate_kbps may differ from
        # preset's value -- that is OK (preset is the *source* of truth in
        # the resolver). We don't enforce here. Hook reserved for Sprint 7.
        return self
