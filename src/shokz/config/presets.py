"""Resolve AudioPreset (config) to AudioSpec (domain)."""

from __future__ import annotations

from shokz.config.schema import AudioConfig, AudioPreset
from shokz.domain.models import AudioSpec
from shokz.domain.presets import SWIM_HIGH, SWIM_LOW, SWIM_STANDARD


def resolve_audio_spec(cfg: AudioConfig) -> AudioSpec:
    """AudioConfig (preset name + custom values) -> AudioSpec (domain).

    For non-custom presets, returns the preset constant. For 'custom',
    returns a fresh AudioSpec built from the explicit bitrate/channels/rate.
    """
    if cfg.preset is AudioPreset.SWIM_LOW:
        return SWIM_LOW
    if cfg.preset is AudioPreset.SWIM_STANDARD:
        return SWIM_STANDARD
    if cfg.preset is AudioPreset.SWIM_HIGH:
        return SWIM_HIGH
    # CUSTOM
    return AudioSpec(
        codec="mp3",
        bitrate_kbps=cfg.bitrate_kbps,
        channels=cfg.channels,
        sample_rate_hz=cfg.sample_rate_hz,
    )
