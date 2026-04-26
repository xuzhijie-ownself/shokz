"""Composition root — the only file that imports from both halves of the hexagon.

Sprint 1 wired POC parity. Sprint 2 added FilenameResolver. Sprint 3 wires
every Sprint 1+2 knob through an AppConfig (TOML/env/CLI layered).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shokz.adapters.outbound.ffmpeg_encoder import FfmpegEncoder
from shokz.adapters.outbound.null_progress import NullProgressReporter
from shokz.adapters.outbound.ytdlp_source import YouTubeSource
from shokz.application.policies.filename_resolver import FilenameResolver
from shokz.application.use_cases.batch_download import BatchDownloadUseCase
from shokz.config.schema import AppConfig


@dataclass(frozen=True, slots=True)
class Container:
    """Resolved use cases ready to be invoked by inbound adapters."""

    batch_download: BatchDownloadUseCase
    config: AppConfig


def build_container(config: AppConfig) -> Container:
    sources = (YouTubeSource(ejs_source=config.sources.youtube.ejs_source),)
    encoder = FfmpegEncoder()
    progress = NullProgressReporter()

    template = config.filenames.template

    # Sprint 3: factory binds resolver to per-call output_dir + configured template.
    # Sprint 7+ may add policy switching (overwrite | skip | fail).
    def _resolver_factory(output_dir: Path) -> FilenameResolver:
        return FilenameResolver(output_dir=output_dir, template=template)

    batch_download = BatchDownloadUseCase(
        sources=sources,
        encoder=encoder,
        progress=progress,
        filename_resolver_factory=_resolver_factory,
    )
    return Container(batch_download=batch_download, config=config)
