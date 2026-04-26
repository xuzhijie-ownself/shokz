"""Composition root — the only file that imports from both halves of the hexagon.

Sprint 1 wired POC parity. Sprint 2 added FilenameResolver. Sprint 3 wires
every Sprint 1+2 knob through an AppConfig (TOML/env/CLI layered).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shokz.adapters.outbound.ffmpeg_encoder import FfmpegEncoder
from shokz.adapters.outbound.jsonl_manifest import JsonlManifest
from shokz.adapters.outbound.local_filesystem import LocalFileSystem
from shokz.adapters.outbound.null_progress import NullProgressReporter
from shokz.adapters.outbound.ytdlp_source import YouTubeSource
from shokz.application.policies.filename_resolver import FilenameResolver
from shokz.application.policies.reconciliation import ReconciliationPolicy
from shokz.application.policies.skip_existing import SkipExistingPolicy
from shokz.application.use_cases.batch_download import BatchDownloadUseCase
from shokz.application.use_cases.expand_playlist import ExpandPlaylistUseCase
from shokz.application.use_cases.library_query import (
    ListLibraryUseCase,
    ShowLibraryUseCase,
    VerifyLibraryUseCase,
)
from shokz.config.schema import AppConfig


@dataclass(frozen=True, slots=True)
class Container:
    """Resolved use cases ready to be invoked by inbound adapters."""

    batch_download: BatchDownloadUseCase
    expand_playlist: ExpandPlaylistUseCase
    list_library: ListLibraryUseCase
    show_library: ShowLibraryUseCase
    verify_library: VerifyLibraryUseCase
    config: AppConfig


def build_container(config: AppConfig) -> Container:
    sources = (YouTubeSource(ejs_source=config.sources.youtube.ejs_source),)
    encoder = FfmpegEncoder()
    progress = NullProgressReporter()
    filesystem = LocalFileSystem()

    output_dir = config.general.output_dir
    state_dir = output_dir / ".shokz"
    manifest = JsonlManifest(
        manifest_path=state_dir / "manifest.jsonl",
        failures_path=state_dir / "failures.jsonl",
    )

    template = config.filenames.template

    # Sprint 3: factory binds resolver to per-call output_dir + configured template.
    def _resolver_factory(output_dir: Path) -> FilenameResolver:
        return FilenameResolver(output_dir=output_dir, template=template)

    skip_existing = SkipExistingPolicy(
        manifest=manifest, filesystem=filesystem, output_dir=output_dir
    )
    reconciliation = ReconciliationPolicy(
        manifest=manifest, filesystem=filesystem, output_dir=output_dir
    )

    batch_download = BatchDownloadUseCase(
        sources=sources,
        encoder=encoder,
        progress=progress,
        filename_resolver_factory=_resolver_factory,
        manifest=manifest,
        filesystem=filesystem,
        skip_existing=skip_existing,
        reconciliation=reconciliation,
    )
    list_library = ListLibraryUseCase(manifest=manifest)
    show_library = ShowLibraryUseCase(manifest=manifest)
    verify_library = VerifyLibraryUseCase(reconciliation=reconciliation)
    expand_playlist = ExpandPlaylistUseCase(sources=sources)
    return Container(
        batch_download=batch_download,
        expand_playlist=expand_playlist,
        list_library=list_library,
        show_library=show_library,
        verify_library=verify_library,
        config=config,
    )
