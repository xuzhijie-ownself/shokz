"""Composition root — the only file that imports from both halves of the hexagon.

Sprint 1 wires: YouTubeSource + FfmpegEncoder + NullProgressReporter into
BatchDownloadUseCase. Sprint 3 turns the hard-coded values into AppConfig.
"""

from __future__ import annotations

from dataclasses import dataclass

from shokz.adapters.outbound.ffmpeg_encoder import FfmpegEncoder
from shokz.adapters.outbound.null_progress import NullProgressReporter
from shokz.adapters.outbound.ytdlp_source import YouTubeSource
from shokz.application.use_cases.batch_download import BatchDownloadUseCase


@dataclass(frozen=True, slots=True)
class Container:
    """Resolved use cases ready to be invoked by inbound adapters."""

    batch_download: BatchDownloadUseCase


def build_container() -> Container:
    sources = (YouTubeSource(),)
    encoder = FfmpegEncoder()
    progress = NullProgressReporter()
    batch_download = BatchDownloadUseCase(
        sources=sources,
        encoder=encoder,
        progress=progress,
    )
    return Container(batch_download=batch_download)
