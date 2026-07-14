"""SplitAudioUseCase -- Sprint 11: chop a long MP3 into hour-sized parts.

Why this exists: an 11-hour audiobook downloads as a single 326 MB MP3.
Shokz bone-conduction headphones have no usable seek-within-track UI
underwater -- you get next/previous track and that's about it. Splitting
into hour-sized parts turns "scrub blindly through 11 hours" into "press
next twice".

Three deliberate design constraints:

  1. LOSSLESS. The adapter stream-copies (`ffmpeg -c copy`), never
     re-encodes. No generational quality loss, and a 326 MB source
     segments in seconds rather than the ~5 minutes a re-encode costs.

  2. NO MANIFEST COUPLING. Split reads and writes NOTHING under
     `.shokz/`. It operates on any MP3 already on disk. This keeps
     skip-existing, retry, and reconciliation completely untouched --
     and sidesteps the schema migration that a 1-URL-to-N-rows manifest
     would otherwise force.

  3. NO LOCK. Split emits part-suffixed names (`Title (part 01).mp3`)
     that `download` would never produce, and writes no manifest row, so
     it cannot race a concurrent download. Skipping the cross-process
     lock is safe here, unlike in download / playlist / retry.

Consequence of (2): `shokz library verify` will report the part files as
orphans (on disk, no manifest entry). That is honest and correct -- they
ARE unmanaged files. Split is a post-processing tool, not a download
mode.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from shokz.application.ports.outbound.encoder import AudioEncoderPort
from shokz.domain.errors import ShokzError, SplitFailed

_log = logging.getLogger("shokz.usecase.split_audio")

_SECONDS_PER_HOUR = 3600


@dataclass(frozen=True, slots=True)
class SplitAudioInput:
    source: Path
    hours: float = 1.0
    # Where the parts land. None -> alongside the source.
    output_dir: Path | None = None
    # Opt-in to overwriting a previous split's parts.
    force: bool = False


@dataclass(frozen=True, slots=True)
class SplitAudioResult:
    source: Path
    parts: tuple[Path, ...]
    segment_seconds: int


class SplitAudioUseCase:
    """Validate, then delegate the actual segmenting to the encoder port."""

    def __init__(self, encoder: AudioEncoderPort) -> None:
        self._encoder = encoder

    async def execute(self, inp: SplitAudioInput) -> SplitAudioResult:
        # 1. Validate the source BEFORE shelling out, so the user gets a
        #    clean message instead of an ffmpeg stderr dump.
        if not inp.source.is_file():
            raise SplitFailed(
                f"source file not found: {inp.source}. "
                "Pass the path to an existing audio file."
            )

        # 2. `--hours 0` would make ffmpeg emit zero-length segments
        #    forever. Reject it up front.
        if inp.hours <= 0:
            raise SplitFailed(
                f"--hours must be greater than 0, got {inp.hours}"
            )
        segment_seconds = int(inp.hours * _SECONDS_PER_HOUR)
        if segment_seconds < 1:
            raise SplitFailed(
                f"--hours {inp.hours} rounds to a {segment_seconds}s segment; "
                "use a larger value (minimum is roughly 0.0003 hours = 1s)"
            )

        output_dir = inp.output_dir or inp.source.parent
        output_dir.mkdir(parents=True, exist_ok=True)

        # 3. Build the ffmpeg segment-muxer template. `%02d` is consumed
        #    by ffmpeg, not by us -- hence a printf placeholder embedded
        #    in a Path. Zero-padded + 1-indexed so the parts sort
        #    correctly in the device's file browser.
        dest_template = output_dir / f"{inp.source.stem} (part %02d){inp.source.suffix}"

        # 4. No silent clobber. If a previous split's first part is
        #    sitting there, stop and name it -- unless --force.
        first_part = Path(str(dest_template) % 1)
        if first_part.exists() and not inp.force:
            raise SplitFailed(
                f"{first_part.name} already exists in {output_dir}; "
                "pass --force to overwrite a previous split"
            )

        # 5. Delegate. The adapter owns the ffmpeg invocation + its error
        #    translation; we re-wrap any ShokzError as SplitFailed so the
        #    caller has exactly one class to catch.
        try:
            parts = await self._encoder.segment(
                inp.source, dest_template, segment_seconds
            )
        except ShokzError as e:
            raise SplitFailed(f"ffmpeg could not split {inp.source.name}: {e}") from e

        # 6. ffmpeg exiting 0 while emitting nothing is a silent failure
        #    we refuse to pass off as success.
        if not parts:
            raise SplitFailed(
                f"ffmpeg reported success but produced no parts for {inp.source.name}"
            )

        _log.info(
            "split %s into %d part(s) of %ds in %s",
            inp.source.name, len(parts), segment_seconds, output_dir,
        )
        return SplitAudioResult(
            source=inp.source,
            parts=tuple(parts),
            segment_seconds=segment_seconds,
        )
