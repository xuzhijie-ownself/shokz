"""In-memory port fakes for use-case unit tests.

Each fake records calls so tests can assert on interactions. Failures are
opt-in via constructor knobs to test error isolation paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from shokz.domain.errors import DownloadFailed, EncodingFailed, SourceUnavailable
from shokz.domain.models import AudioSpec, EncodedFile, RawDownload, Track, TrackStatus


def _id_from(url: str) -> str:
    """Deterministic synthetic ID derived from URL — no network."""
    qs = urlparse(url).query
    if "v=" in qs:
        return qs.split("v=", 1)[1].split("&", 1)[0]
    return url.rsplit("/", 1)[-1] or "untitled"


@dataclass
class FakeVideoSource:
    """Returns a Track based on the URL; no network."""

    name: str = "youtube"
    fail_resolve_for: frozenset[str] = field(default_factory=frozenset)
    fail_download_for: frozenset[str] = field(default_factory=frozenset)
    resolve_calls: list[str] = field(default_factory=list)
    download_calls: list[str] = field(default_factory=list)

    def can_handle(self, url: str) -> bool:
        return "youtube.com" in url or "youtu.be" in url or url.startswith("fake://")

    async def resolve(self, url: str) -> Track:
        self.resolve_calls.append(url)
        if url in self.fail_resolve_for:
            raise SourceUnavailable(f"fake-fail resolve {url}")
        track_id = _id_from(url)
        return Track(
            id=track_id,
            title=f"Title for {track_id}",
            uploader="FakeUploader",
            duration_s=120,
            source_url=url,
            source_name=self.name,
        )

    async def download_audio(self, track: Track, dest_dir: Path) -> RawDownload:
        self.download_calls.append(track.id)
        if track.source_url in self.fail_download_for:
            raise DownloadFailed(f"fake-fail download {track.id}")
        # Materialize a tiny "raw file" so the encoder fake can stat it.
        dest_dir.mkdir(parents=True, exist_ok=True)
        raw = dest_dir / f"{track.id}.fake"
        raw.write_bytes(b"FAKE-RAW-AUDIO")
        return RawDownload(path=raw, container="fake", track=track)


@dataclass
class FakeAudioEncoder:
    """Writes a small placeholder MP3 byte sequence; no real encoding."""

    fail_for: frozenset[str] = field(default_factory=frozenset)
    encode_calls: list[tuple[Path, Path, AudioSpec]] = field(default_factory=list)

    async def encode(self, src: Path, dest: Path, spec: AudioSpec) -> EncodedFile:
        self.encode_calls.append((src, dest, spec))
        if src.name in self.fail_for:
            raise EncodingFailed(f"fake-fail encode {src.name}")
        # MP3 sync header for plausibility (3 bytes); plus a few padding bytes.
        dest.write_bytes(b"\xff\xfb\x90\x00FAKEMP3DATA")
        return EncodedFile(
            path=dest,
            bitrate_kbps=spec.bitrate_kbps,
            channels=spec.channels,
            duration_s=120.0,
            size_bytes=dest.stat().st_size,
        )

    async def probe_duration(self, path: Path) -> float:
        return 120.0


@dataclass
class FakeProgressReporter:
    starts: list[tuple[str, str]] = field(default_factory=list)
    finishes: list[tuple[str, TrackStatus, str | None]] = field(default_factory=list)

    def start(self, track_id: str, label: str) -> None:
        self.starts.append((track_id, label))

    def finish(self, track_id: str, status: TrackStatus, message: str | None = None) -> None:
        self.finishes.append((track_id, status, message))
