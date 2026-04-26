"""Audio presets tuned for Shokz swimming headphones (mono, modest bitrate)."""

from __future__ import annotations

from typing import Final

from shokz.domain.models import AudioSpec

SWIM_LOW: Final[AudioSpec] = AudioSpec(
    codec="mp3", bitrate_kbps=48, channels=1, sample_rate_hz=22050
)
SWIM_STANDARD: Final[AudioSpec] = AudioSpec(
    codec="mp3", bitrate_kbps=64, channels=1, sample_rate_hz=44100
)
SWIM_HIGH: Final[AudioSpec] = AudioSpec(
    codec="mp3", bitrate_kbps=96, channels=1, sample_rate_hz=44100
)
