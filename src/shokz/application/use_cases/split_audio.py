"""SplitAudioUseCase -- chop a long MP3 into hour-sized parts (Sprint 11, fixed Sprint 12).

Why this exists: an 11-hour audiobook downloads as a single 312 MB MP3. Shokz
bone-conduction headphones have no usable seek-within-track UI underwater --
you get next/previous track and little else. Splitting into hour-sized parts
turns "scrub blindly through 11 hours" into "press next twice".

WHAT SPRINT 12 CHANGED
----------------------
Sprint 11 built an ffmpeg printf template here (`f"{stem} (part %02d){suffix}"`)
and passed it across the port boundary, handing naming authority to ffmpeg. The
adapter then answered "what did I produce?" by scanning the output directory
until it hit a gap. That single design error produced four defects, one of them
HIGH severity:

  * `--force` re-split reported a PREVIOUS split's stale parts as freshly
    written -- the user copied audio at the wrong boundaries to the device.
  * A failed segment's cleanup could delete a previous GOOD split.
  * The no-clobber guard checked only `(part 01)`, so a series whose first part
    had been deleted was silently re-split on top of.
  * `str(template) % n` crashed on any title containing `%`.

Now: `domain/split_parts.py` owns naming and enumeration, the port takes a plain
`stem`/`suffix` (never a format string), and `--force` deletes the whole old
series BEFORE segmenting so what remains is exactly the new split.

Three constraints that have NOT changed:

  1. LOSSLESS. The adapter stream-copies (`ffmpeg -c copy`), never re-encodes.
     A 312 MB / 11.35-hour source splits in ~4 seconds with zero quality loss.

  2. NO MANIFEST COUPLING. Split reads and writes NOTHING under `.shokz/`. It
     operates on any MP3 already on disk, so skip-existing, retry and
     reconciliation are untouched -- and the 1-URL-to-N-rows manifest schema
     migration is sidestepped entirely. Honest consequence: `shokz library
     verify` reports part files as orphans, because they ARE unmanaged files.
     Split is a post-processing tool, not a download mode.

  3. NO LOCK. Split emits part-suffixed names that `download` would never
     produce and writes no manifest row, so it cannot race a concurrent
     download.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from shokz.application.ports.outbound.encoder import AudioEncoderPort
from shokz.domain.errors import ShokzError, SplitFailed
from shokz.domain.split_parts import existing_parts

_log = logging.getLogger("shokz.usecase.split_audio")

_SECONDS_PER_HOUR = 3600


@dataclass(frozen=True, slots=True)
class SplitAudioInput:
    source: Path
    hours: float = 1.0
    # Where the parts land. None -> alongside the source.
    output_dir: Path | None = None
    # Opt-in to replacing a previous split of this same file.
    force: bool = False


@dataclass(frozen=True, slots=True)
class SplitAudioResult:
    source: Path
    parts: tuple[Path, ...]
    segment_seconds: int
    # How many parts from a PREVIOUS split --force removed. Surfaced so the CLI
    # can tell the user their old series is gone, rather than leaving them to
    # wonder why the part count dropped.
    deleted_stale: int = 0


class SplitAudioUseCase:
    """Validate, clear the way, then delegate the segmenting to the encoder port."""

    def __init__(self, encoder: AudioEncoderPort) -> None:
        self._encoder = encoder

    async def execute(self, inp: SplitAudioInput) -> SplitAudioResult:
        # 1. Validate the source BEFORE shelling out, so the user gets a clean
        #    message instead of an ffmpeg stderr dump.
        if not inp.source.is_file():
            raise SplitFailed(
                f"source file not found: {inp.source}. "
                "Pass the path to an existing audio file."
            )

        # 2. `--hours 0` would make ffmpeg emit zero-length segments forever.
        if inp.hours <= 0:
            raise SplitFailed(f"--hours must be greater than 0, got {inp.hours}")
        segment_seconds = int(inp.hours * _SECONDS_PER_HOUR)
        if segment_seconds < 1:
            raise SplitFailed(
                f"--hours {inp.hours} rounds to a {segment_seconds}s segment; "
                "use a larger value"
            )

        output_dir = inp.output_dir or inp.source.parent
        stem, suffix = inp.source.stem, inp.source.suffix

        # 3. Deal with a previous split of THIS file. `existing_parts` enumerates
        #    the real directory contents by regex, so it sees the whole series --
        #    including one with holes in it, which the Sprint 11 "does (part 01)
        #    exist?" check was blind to. It never matches another file's parts.
        stale = existing_parts(output_dir, stem, suffix)
        deleted_stale = 0
        if stale:
            if not inp.force:
                raise SplitFailed(
                    f"{len(stale)} existing part(s) for {inp.source.name} in "
                    f"{output_dir} (e.g. {stale[0].name}); "
                    "pass --force to replace that split"
                )

            # Pre-flight ffmpeg BEFORE the destructive delete. Without this,
            # a user whose ffmpeg went missing loses their old parts and gets
            # nothing back -- the one way this release is worse than v1.2.0,
            # where the missing-binary error fired before any cleanup ran.
            if shutil.which("ffmpeg") is None:
                raise SplitFailed(
                    "ffmpeg not found on PATH -- run `shokz doctor`. "
                    "Refusing to delete the previous split when I cannot "
                    "produce a new one."
                )

            # Delete the OLD series *before* segmenting, so what remains
            # afterwards is exactly the new split. (Deliberately NOT
            # segment-then-prune: staging a second full copy alongside the
            # old one triples peak disk, which makes the DiskFull branch --
            # the very thing that motivates the alternative -- more likely.
            # Deleting first FREES a source-file's worth of space.)
            failed: list[tuple[Path, OSError]] = []
            for part in stale:
                try:
                    part.unlink(missing_ok=True)
                    deleted_stale += 1
                except OSError as e:
                    failed.append((part, e))

            # NEVER split on top of a survivor. A stale part that outlives the
            # delete would sit beside a shorter new series -- which IS the
            # corrupt on-disk state this whole sprint exists to abolish. The
            # invariant the fix rests on gets CHECKED, not assumed.
            if failed:
                path, err = failed[0]
                raise SplitFailed(
                    f"--force could not remove {len(failed)} part(s) of the "
                    f"previous split (e.g. {path.name}: {err}); refusing to "
                    "split on top of a series I cannot fully clear"
                )
            survivors = existing_parts(output_dir, stem, suffix)
            if survivors:
                raise SplitFailed(
                    f"{len(survivors)} part(s) of the previous split survived "
                    f"--force (e.g. {survivors[0].name}); refusing to split on "
                    "top of them"
                )

            _log.info(
                "--force: removed %d part(s) from a previous split of %s",
                deleted_stale, inp.source.name,
            )

        # 4. Delegate. The adapter owns the ffmpeg invocation and its error
        #    translation; it is contractually required to return exactly the
        #    parts IT wrote, and to leave output_dir untouched if it fails.
        try:
            parts = await self._encoder.segment(
                inp.source, output_dir, stem, suffix, segment_seconds
            )
        except ShokzError as e:
            raise SplitFailed(f"ffmpeg could not split {inp.source.name}: {e}") from e

        # 5. ffmpeg exiting 0 while producing nothing is a silent failure we
        #    refuse to pass off as success.
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
            deleted_stale=deleted_stale,
        )
