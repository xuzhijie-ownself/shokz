"""AudioEncoderPort — convert raw audio to a target format."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from shokz.domain.models import AudioSpec, EncodedFile


@runtime_checkable
class AudioEncoderPort(Protocol):
    """Encode raw audio (any container) to the target spec (e.g. MP3)."""

    async def encode(self, src: Path, dest: Path, spec: AudioSpec) -> EncodedFile:
        """Read src, write dest matching spec. Returns metadata about output."""

    async def probe_duration(self, path: Path) -> float:
        """Return duration in seconds (used by Sprint 4 integrity check)."""

    async def segment(
        self, src: Path, dest_template: Path, segment_seconds: int
    ) -> tuple[Path, ...]:
        """Sprint 11: chop `src` into `segment_seconds`-long parts.

        `dest_template` carries a printf-style part-number placeholder
        (e.g. `/out/Title (part %02d).mp3`) because that is exactly what
        ffmpeg's segment muxer consumes. Implementations MUST number
        parts from 1 (not 0) so filenames sort naturally on-device.

        MUST be lossless: stream-copy the audio, never re-encode. A
        326 MB source should segment in seconds, not minutes, and the
        parts must be bit-identical in audio content to the source.

        Returns the produced part paths in play order.
        """
