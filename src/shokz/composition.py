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
from shokz.application.policies.disk_guard import DiskGuardPolicy
from shokz.application.policies.filename_resolver import FilenameResolver
from shokz.application.policies.reconciliation import ReconciliationPolicy
from shokz.application.policies.retry import RetryPolicy
from shokz.application.policies.skip_existing import SkipExistingPolicy
from shokz.application.use_cases.batch_download import BatchDownloadUseCase
from shokz.application.use_cases.expand_playlist import ExpandPlaylistUseCase
from shokz.application.use_cases.library_query import (
    ListLibraryUseCase,
    ShowLibraryUseCase,
    VerifyLibraryUseCase,
)
from shokz.application.use_cases.retry_failed import RetryFailedUseCase
from shokz.application.use_cases.split_audio import SplitAudioUseCase
from shokz.config.schema import AppConfig


@dataclass(frozen=True, slots=True)
class Container:
    """Resolved use cases ready to be invoked by inbound adapters."""

    batch_download: BatchDownloadUseCase
    expand_playlist: ExpandPlaylistUseCase
    list_library: ListLibraryUseCase
    show_library: ShowLibraryUseCase
    verify_library: VerifyLibraryUseCase
    retry_failed: RetryFailedUseCase
    split_audio: SplitAudioUseCase
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

    # Sprint 7: classified retry with per-error-class budgets.
    retry_policy = RetryPolicy(config.retry)

    # Sprint 8b: batch-level disk pre-flight (DiskGuardPolicy is a frozen
    # dataclass). Constructed from [disk] config; the use case enforces
    # ONE check per execute() after resolve-all.
    disk_guard = DiskGuardPolicy(
        safety_multiplier=config.disk.safety_multiplier,
        require_estimate=config.disk.require_estimate,
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
        retry_policy=retry_policy,
        disk_guard=disk_guard,
    )
    list_library = ListLibraryUseCase(manifest=manifest)
    show_library = ShowLibraryUseCase(manifest=manifest)
    verify_library = VerifyLibraryUseCase(reconciliation=reconciliation)
    expand_playlist = ExpandPlaylistUseCase(sources=sources)
    # Sprint 8.5: shokz retry. Reuses the same batch_download (skip-existing,
    # retry, lock, SIGINT shield, disk pre-flight all flow through).
    retry_failed = RetryFailedUseCase(
        manifest=manifest,
        batch_download=batch_download,
    )
    # Sprint 11: `shokz split` reuses the SAME FfmpegEncoder adapter --
    # its `segment()` shares the subprocess plumbing and ENOSPC
    # translation with `encode()`. No manifest, no lock: split writes
    # part-suffixed files and nothing under `.shokz/`.
    split_audio = SplitAudioUseCase(encoder=encoder)
    return Container(
        batch_download=batch_download,
        expand_playlist=expand_playlist,
        list_library=list_library,
        show_library=show_library,
        verify_library=verify_library,
        retry_failed=retry_failed,
        split_audio=split_audio,
        config=config,
    )
