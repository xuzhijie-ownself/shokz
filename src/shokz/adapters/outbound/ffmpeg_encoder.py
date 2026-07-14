"""FfmpegEncoder — AudioEncoderPort backed by ffmpeg subprocess.

Sprint 1: uses libmp3lame, mono downmix when spec.channels == 1, fixed sample
rate, CBR. Post-encode probe_duration uses ffprobe JSON output (plan §9 risk 4).

Sprint 12: `segment()` stages ffmpeg's output through a private scratch dir so
"what did I produce?" is known rather than inferred. See its docstring.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Final

from shokz.domain.errors import DiskFull, EncodingFailed
from shokz.domain.models import AudioSpec, EncodedFile
from shokz.domain.split_parts import pad_width, part_name

_log = logging.getLogger("shokz.adapter.ffmpeg")

# Matches the scratch-dir names WE tell ffmpeg to write (`part-00001.mp3`).
# Fully under our control, so no user text can reach it.
_SCRATCH_PART_RE: Final[re.Pattern[str]] = re.compile(r"^part-(\d+)\.")


def _scratch_parts_in_order(scratch: Path) -> list[Path]:
    """The parts ffmpeg wrote, in play order.

    Sorted by the PARSED integer rather than lexicographically: ffmpeg's
    `%05d` does not truncate past 99999, so `part-100000` must still sort
    after `part-99999`.
    """
    numbered: list[tuple[int, Path]] = []
    for entry in scratch.iterdir():
        match = _SCRATCH_PART_RE.match(entry.name)
        if match is not None and entry.is_file():
            numbered.append((int(match.group(1)), entry))
    numbered.sort(key=lambda pair: pair[0])
    return [path for _, path in numbered]


class FfmpegEncoder:
    async def encode(self, src: Path, dest: Path, spec: AudioSpec) -> EncodedFile:
        if spec.codec != "mp3":
            raise EncodingFailed(f"Sprint 1 supports mp3 only, got {spec.codec}")

        # `-f mp3` is required because dest may have a non-standard extension
        # (e.g. ".mp3.partial") that ffmpeg cannot infer the format from.
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vn",
            "-acodec",
            "libmp3lame",
            "-ac",
            str(spec.channels),
            "-ab",
            f"{spec.bitrate_kbps}k",
            "-ar",
            str(spec.sample_rate_hz),
            "-f",
            "mp3",
            "-hide_banner",
            "-loglevel",
            "error",
            str(dest),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_b = await proc.communicate()
        if proc.returncode != 0:
            stderr = stderr_b.decode(errors="replace").strip()
            tail = stderr.splitlines()[-1:] or ["ffmpeg failed"]
            _log.warning("ffmpeg exit %s for %s: %s", proc.returncode, src, tail[0])
            # Sprint 8b GAN B4: ffmpeg runs as subprocess -- OSError(ENOSPC)
            # never propagates from communicate(). Detect via stderr text
            # ("No space left on device" / "ENOSPC"). Cleanup the .partial
            # before raising so a retry doesn't see a half-written MP3.
            stderr_lower = stderr.lower()
            if "no space left" in stderr_lower or "enospc" in stderr_lower:
                with contextlib.suppress(OSError):
                    dest.unlink(missing_ok=True)
                # Sprint 8b GAN M5: explain that pre-flight may have
                # underestimated (HLS / fragmented streams).
                _log.warning(
                    "ENOSPC during encode for %s; pre-flight may have "
                    "underestimated -- consider raising [disk] safety_multiplier",
                    src,
                )
                raise DiskFull(f"disk full during ffmpeg encode of {src}")
            raise EncodingFailed(tail[0])

        if not dest.exists() or dest.stat().st_size == 0:
            raise EncodingFailed(f"encoded file missing or empty: {dest}")

        duration_s = await self.probe_duration(dest)
        return EncodedFile(
            path=dest,
            bitrate_kbps=spec.bitrate_kbps,
            channels=spec.channels,
            duration_s=duration_s,
            size_bytes=dest.stat().st_size,
        )

    async def segment(
        self, src: Path, dest_dir: Path, stem: str, suffix: str, segment_seconds: int
    ) -> tuple[Path, ...]:
        """Sprint 12: lossless split, staged through a private temp dir.

        `-c copy` stream-copies the audio -- no decode, no re-encode, no
        generational quality loss. A 326 MB / 11-hour source segments in
        ~4 seconds; a re-encode of the same file takes ~5 minutes.

        WHY THE TEMP DIR (this is the Sprint 12 fix)
        --------------------------------------------
        Sprint 11 pointed ffmpeg's segment muxer straight at the output
        directory using a printf template built from the user's title, then
        answered "what did I just produce?" by scanning that directory from
        1 upward until it hit a gap. A directory scan cannot tell a file
        THIS RUN WROTE from one that was ALREADY THERE, which silently
        reported a previous split's stale parts as freshly written (HIGH:
        corrupt audio on-device), let a failed run's cleanup delete a
        previous GOOD split, and crashed outright on any title containing
        a `%`.

        So ffmpeg now writes into a hidden scratch dir created INSIDE
        `dest_dir` (same filesystem => the renames below are atomic), using
        a template containing ZERO user text. Consequences, all of which
        are the point:

          * "what did I produce?" is exactly the scratch dir's contents --
            known, not inferred. Nothing else can be in there.
          * cleanup is `rmtree(scratch)`, which is structurally incapable
            of touching a file this call did not create.
          * the pad width is computed from the REAL part count, so >99
            parts still sort into play order.
          * an interrupt or a crash leaves the output dir as THIS METHOD
            found it -- nothing is moved into place until ffmpeg succeeds.
            NOTE the precise scope: the use case may already have deleted a
            previous split under `--force` before calling us. That deletion
            is not ours to undo, and it is guarded there (ffmpeg is
            pre-flighted, and an unclearable series aborts before we run).

        LIMITATION, stated plainly rather than papered over: the template
        below is an absolute path, so `dest_dir` and `suffix` DO reach
        ffmpeg's printf expander even though `stem` does not. A '%' in the
        output DIRECTORY name is therefore still mishandled -- a real but
        pre-existing defect (v1.2.0 crashed outright on it). Fixing it means
        running with `cwd=scratch` and a relative template. Do not let this
        docstring drift into claiming the template is user-text-free: a
        lying safety comment is how the next person reintroduces the
        corruption this rewrite abolished.

        `-reset_timestamps 1` makes each part start at t=0 so the device
        shows a sane per-part position rather than an offset into the
        original. `-vn` drops any embedded cover art, which the segment
        muxer would otherwise try to replicate into every part.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        # Inside dest_dir => same filesystem => os.replace below is atomic.
        scratch = Path(tempfile.mkdtemp(dir=dest_dir, prefix=".shokz-split-"))
        try:
            # The FILENAME part of the template holds no user text, so a '%'
            # in the TITLE cannot reach ffmpeg's (or Python's) format
            # machinery -- that was the Sprint 11 crash. `dest_dir` and
            # `suffix` are still user-derived and still in the path; see the
            # LIMITATION note above. Do not overstate this.
            scratch_template = scratch / f"part-%05d{suffix}"
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-vn",
                "-c",
                "copy",
                "-f",
                "segment",
                "-segment_time",
                str(segment_seconds),
                "-reset_timestamps",
                "1",
                "-segment_start_number",
                "1",
                "-hide_banner",
                "-loglevel",
                "error",
                str(scratch_template),
            ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as e:
                raise EncodingFailed(
                    "ffmpeg not found on PATH -- run `shokz doctor`"
                ) from e

            try:
                _, stderr_b = await proc.communicate()
            except asyncio.CancelledError:
                # Ctrl+C: don't orphan ffmpeg writing into a dir we are
                # about to delete. The `finally` below still clears scratch,
                # and nothing was moved into dest_dir, so the output dir is
                # left exactly as we found it.
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(BaseException):
                    await proc.wait()
                raise

            if proc.returncode != 0:
                stderr = stderr_b.decode(errors="replace").strip()
                tail = stderr.splitlines()[-1:] or ["ffmpeg segment failed"]
                _log.warning(
                    "ffmpeg segment exit %s for %s: %s", proc.returncode, src, tail[0]
                )
                if "no space left" in stderr.lower() or "enospc" in stderr.lower():
                    raise DiskFull(f"disk full while splitting {src}")
                raise EncodingFailed(tail[0])

            # Exactly what ffmpeg wrote -- the scratch dir holds nothing else.
            produced = _scratch_parts_in_order(scratch)
            if not produced:
                return ()

            width = pad_width(len(produced))
            parts: list[Path] = []
            for index, staged in enumerate(produced, start=1):
                final = dest_dir / part_name(stem, index, suffix, width)
                os.replace(staged, final)  # atomic: same filesystem
                parts.append(final)

            _log.debug("segmented %s into %d part(s)", src, len(parts))
            return tuple(parts)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    async def probe_duration(self, path: Path) -> float:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            str(path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout_b, _ = await proc.communicate()
        if proc.returncode != 0:
            raise EncodingFailed(f"ffprobe failed for {path}")
        try:
            data = json.loads(stdout_b.decode())
            return float(data["format"]["duration"])
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            raise EncodingFailed(f"ffprobe output unparseable for {path}: {e}") from e
