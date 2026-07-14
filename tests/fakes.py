"""In-memory port fakes for use-case unit tests.

Each fake records calls so tests can assert on interactions. Failures are
opt-in via constructor knobs to test error isolation paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from shokz.domain.errors import DownloadFailed, EncodingFailed, SourceUnavailable
from shokz.domain.models import (
    AudioSpec,
    EncodedFile,
    FailureEntry,
    ManifestEntry,
    RawDownload,
    Track,
    TrackStatus,
)


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
    # Sprint 4: control raw size to test the SourceFileCorrupt path.
    # Default ~4200 bytes is above MIN_RAW_BYTES=1024; tests exercising the
    # integrity check set this to e.g. b"" or short bytes.
    raw_bytes: bytes = field(default_factory=lambda: b"FAKE-RAW-AUDIO" * 300)

    def can_handle(self, url: str) -> bool:
        return "youtube.com" in url or "youtu.be" in url or url.startswith("fake://")

    # Sprint 5: control playlist expansion. Default None = single video.
    # Set playlist_items to mock a playlist; title defaults to 'fake'.
    playlist_items: tuple[str, ...] | None = None
    playlist_title: str = "fake-playlist"

    async def resolve_playlist(self, url: str):  # type: ignore[no-untyped-def]
        from shokz.domain.models import PlaylistInfo

        if self.playlist_items is None:
            return None
        return PlaylistInfo(title=self.playlist_title, item_urls=self.playlist_items)

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
        # Materialize a "raw file" so the encoder fake can stat it.
        # Sprint 4: size is controlled by self.raw_bytes (defaults to 4200B,
        # above MIN_RAW_BYTES=1024 so the integrity check passes by default).
        dest_dir.mkdir(parents=True, exist_ok=True)
        raw = dest_dir / f"{track.id}.fake"
        raw.write_bytes(self.raw_bytes)
        return RawDownload(path=raw, container="fake", track=track)


@dataclass
class FakeAudioEncoder:
    """Writes a small placeholder MP3 byte sequence; no real encoding."""

    fail_for: frozenset[str] = field(default_factory=frozenset)
    encode_calls: list[tuple[Path, Path, AudioSpec]] = field(default_factory=list)
    # Sprint 4: control what probe_duration returns (defaults: match track perfectly).
    probe_duration_value: float = 120.0
    # Sprint 11: AudioEncoderPort.segment call log.
    segment_calls: list[tuple[Path, Path, int]] = field(default_factory=list)

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
        return self.probe_duration_value

    async def segment(
        self, src: Path, dest_template: Path, segment_seconds: int
    ) -> tuple[Path, ...]:
        """Sprint 11: AudioEncoderPort gained `segment`. This shared fake
        mirrors it so every call site that passes `FakeAudioEncoder()`
        where an `AudioEncoderPort` is expected still satisfies the
        Protocol structurally. `BatchDownloadUseCase` never calls it;
        `SplitAudioUseCase` has its own richer fake."""
        self.segment_calls.append((src, dest_template, segment_seconds))
        part = Path(str(dest_template) % 1)
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(b"\xff\xfb\x90\x00FAKEPART")
        return (part,)


@dataclass
class FakeProgressReporter:
    starts: list[tuple[str, str]] = field(default_factory=list)
    finishes: list[tuple[str, TrackStatus, str | None]] = field(default_factory=list)

    def start(self, track_id: str, label: str) -> None:
        self.starts.append((track_id, label))

    def finish(self, track_id: str, status: TrackStatus, message: str | None = None) -> None:
        self.finishes.append((track_id, status, message))


@dataclass
class FakeManifest:
    """Records every successful + failed entry. No I/O."""

    successes: list[ManifestEntry] = field(default_factory=list)
    failures: list[FailureEntry] = field(default_factory=list)

    async def record(self, entry: ManifestEntry) -> None:
        self.successes.append(entry)

    async def record_failure(self, entry: FailureEntry) -> None:
        self.failures.append(entry)

    async def find_by_track(self, source: str, track_id: str):  # type: ignore[no-untyped-def]
        latest = None
        for e in self.successes:
            if e.source == source and e.track_id == track_id:
                latest = e
        return latest

    async def iter_all(self):  # type: ignore[no-untyped-def]
        for e in self.successes:
            yield e

    async def iter_failures(self):  # type: ignore[no-untyped-def]
        # Sprint 8.5: ManifestPort gained iter_failures. The shared
        # FakeManifest mirrors record_failure -> iter_failures so call
        # sites that pass `FakeManifest()` to anything typed
        # `manifest: ManifestPort` stay structurally satisfied.
        for e in self.failures:
            yield e


@dataclass
class FakeFileSystem:
    """Records every atomic_move + tracks fsync calls. Performs real moves
    so the use case sees the file at `final` after the call."""

    moves: list[tuple[Path, Path]] = field(default_factory=list)
    removes: list[Path] = field(default_factory=list)
    fsync_file_calls: list[Path] = field(default_factory=list)
    fsync_dir_calls: list[Path] = field(default_factory=list)

    def atomic_move(self, src: Path, dest: Path) -> None:
        import os

        os.replace(src, dest)
        # Pretend to fsync the file + parent dir (recorded for assertions).
        self.fsync_file_calls.append(dest)
        self.fsync_dir_calls.append(dest.parent)
        self.moves.append((src, dest))

    def exists(self, path: Path) -> bool:
        return path.exists()

    def mkdir_p(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def remove(self, path: Path) -> None:
        self.removes.append(path)
        path.unlink(missing_ok=True)
