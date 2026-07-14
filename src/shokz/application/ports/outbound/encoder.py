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
        self, src: Path, dest_dir: Path, stem: str, suffix: str, segment_seconds: int
    ) -> tuple[Path, ...]:
        """Chop `src` into `segment_seconds`-long parts inside `dest_dir`.

        Parts are named by `domain.split_parts.part_name(stem, n, suffix,
        width)` -- the single source of truth. Sprint 11 instead passed a
        printf template (`(part %02d).mp3`) across this boundary, which
        handed naming authority to ffmpeg and crashed on any `stem`
        containing a `%`. Implementations MUST treat `stem` as literal text:
        never a format string, never a regex, never a glob.

        THE CONTRACT THAT MATTERS -- returns EXACTLY the parts THIS CALL
        produced, in play order. NOT "the parts currently on disk". Those
        differ whenever `dest_dir` already holds a previous split, and
        conflating them silently reported stale parts as freshly written
        (the Sprint 11 HIGH-severity corruption bug). Implementations must
        therefore KNOW what they wrote rather than infer it from a directory
        scan.

        MUST be lossless: stream-copy, never re-encode. A 326 MB source
        should segment in seconds, and the parts must be bit-identical in
        audio content to the source.

        MUST be atomic-ish on failure: a raise leaves `dest_dir` exactly as
        it was found -- no partial parts of its own, and crucially no damage
        to a PREVIOUS successful split. Deleting the old series is the use
        case's `--force` decision, never the adapter's.

        Returns `()` if the source yielded no parts.
        """
