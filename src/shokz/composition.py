"""Composition root — the only file that imports from both halves of the hexagon.

Sprint 1 wired POC parity. Sprint 2 adds FilenameResolver. Sprint 3 will
collapse the hard-coded defaults into an AppConfig.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shokz.adapters.outbound.ffmpeg_encoder import FfmpegEncoder
from shokz.adapters.outbound.null_progress import NullProgressReporter
from shokz.adapters.outbound.ytdlp_source import YouTubeSource
from shokz.application.policies.filename_resolver import FilenameResolver
from shokz.application.use_cases.batch_download import BatchDownloadUseCase


@dataclass(frozen=True, slots=True)
class Container:
    """Resolved use cases ready to be invoked by inbound adapters."""

    batch_download: BatchDownloadUseCase


def build_container() -> Container:
    sources = (YouTubeSource(),)
    encoder = FfmpegEncoder()
    progress = NullProgressReporter()

    # Sprint 2: factory binds resolver to the per-call output_dir.
    # Sprint 3 will inject template + collision policy from AppConfig.
    def _resolver_factory(output_dir: Path) -> FilenameResolver:
        return FilenameResolver(output_dir=output_dir)

    batch_download = BatchDownloadUseCase(
        sources=sources,
        encoder=encoder,
        progress=progress,
        filename_resolver_factory=_resolver_factory,
    )
    return Container(batch_download=batch_download)
