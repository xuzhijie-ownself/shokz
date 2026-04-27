"""FfmpegEncoder — AudioEncoderPort backed by ffmpeg subprocess.

Sprint 1: uses libmp3lame, mono downmix when spec.channels == 1, fixed sample
rate, CBR. Post-encode probe_duration uses ffprobe JSON output (plan §9 risk 4).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path

from shokz.domain.errors import DiskFull, EncodingFailed
from shokz.domain.models import AudioSpec, EncodedFile

_log = logging.getLogger("shokz.adapter.ffmpeg")


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
