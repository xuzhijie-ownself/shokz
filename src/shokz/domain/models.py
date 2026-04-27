"""Domain models — frozen dataclasses, no behavior beyond construction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class TrackStatus(StrEnum):
    """Outcome of a single track in a batch."""

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"  # Sprint 4.5: skip-existing match
    # DRY_RUN reserved for Sprint 7


@dataclass(frozen=True, slots=True)
class Track:
    """Resolved metadata for one source track.

    Sprint 4: original_title preserved separately so the manifest can store
    the unsanitized title (Sprint 2 review C12 — silent-failure-hunter F4).
    """

    id: str
    title: str
    uploader: str | None
    duration_s: int | None
    source_url: str
    source_name: str = "youtube"
    original_title: str | None = None  # Sprint 4: preserved unsanitized; defaults to title
    # Sprint 8b: yt-dlp's filesize_approx (or filesize) for the disk-guard
    # pre-flight. None = source couldn't predict (live stream / chunked HLS
    # without estimate); the policy logs WARNING and proceeds in default
    # best-effort mode (or raises DiskFull if [disk] require_estimate=true).
    filesize_approx: int | None = None


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


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """One row of downloads/.shokz/manifest.jsonl (Sprint 4, schema_version=1)."""

    schema_version: int  # always 1 in Sprint 4; future migrations bump this
    source: str
    track_id: str
    original_title: str
    filename_stem: str
    mp3_path: str  # relative to output_dir
    bitrate_kbps: int
    duration_s: float
    downloaded_at: str  # ISO-8601 UTC e.g. "2026-04-27T01:23:45Z"


@dataclass(frozen=True, slots=True)
class FailureEntry:
    """One row of downloads/.shokz/failures.jsonl (Sprint 4, schema_version=1)."""

    schema_version: int
    source: str | None
    track_id: str | None
    url: str
    error_class: str
    error_message: str
    failed_at: str  # ISO-8601 UTC


@dataclass(frozen=True, slots=True)
class PlaylistInfo:
    """Sprint 5: playlist resolution result -- title + per-item URLs.

    F1 (Sprint 5 review fix): keeping the title alongside the URLs eliminates
    the CLI's double-extract-info call (which had a bare except + silent
    fallback to literal "playlist" dirname).
    """

    title: str
    item_urls: tuple[str, ...]
