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
