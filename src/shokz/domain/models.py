"""Domain models — frozen dataclasses, no behavior beyond construction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class TrackStatus(StrEnum):
    """Outcome of a single track in a batch."""

    SUCCESS = "success"
    FAILED = "failed"
    # SKIPPED, DRY_RUN reserved for Sprint 4.5 / Sprint 7


@dataclass(frozen=True, slots=True)
class Track:
    """Resolved metadata for one source track. Sprint 1 minimal shape.

    Sprint 4 extends with `source_bitrate_kbps`, `source_channels`.
    """

    id: str
    title: str
    uploader: str | None
    duration_s: int | None
    source_url: str
    source_name: str = "youtube"


@dataclass(frozen=True, slots=True)
class AudioSpec:
    """Target audio encoding parameters."""

    codec: str  # "mp3"
    bitrate_kbps: int
    channels: int  # 1 = mono, 2 = stereo
    sample_rate_hz: int


@dataclass(frozen=True, slots=True)
class RawDownload:
    """Output of VideoSourcePort.download_audio — the raw audio file."""

    path: Path
    container: str  # "webm", "m4a", ...
    track: Track


@dataclass(frozen=True, slots=True)
class EncodedFile:
    """Output of AudioEncoderPort.encode — the final MP3."""

    path: Path
    bitrate_kbps: int
    channels: int
    duration_s: float
    size_bytes: int


@dataclass(frozen=True, slots=True)
class TrackResult:
    """Per-track outcome for the batch use case to aggregate."""

    track: Track | None  # None when resolve itself failed
    status: TrackStatus
    final_path: Path | None
    error: str | None
    elapsed_s: float
