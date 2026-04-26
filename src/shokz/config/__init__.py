"""Configuration package: schema, defaults, presets, layered loader."""

from shokz.config.loader import ConfigWithSource, load_config
from shokz.config.schema import (
    AppConfig,
    AudioConfig,
    AudioPreset,
    FilenamesConfig,
    GeneralConfig,
    LoggingConfig,
    SourcesConfig,
    YouTubeConfig,
)

__all__ = [
    "AppConfig",
    "AudioConfig",
    "AudioPreset",
    "ConfigWithSource",
    "FilenamesConfig",
    "GeneralConfig",
    "LoggingConfig",
    "SourcesConfig",
    "YouTubeConfig",
    "load_config",
]
