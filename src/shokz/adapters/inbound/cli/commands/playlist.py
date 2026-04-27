"""`shokz playlist URL` -- Sprint 5."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import typer

from shokz.application.use_cases.batch_download import BatchDownloadInput
from shokz.composition import build_container
from shokz.config.loader import ConfigLoadError, load_config
from shokz.config.presets import resolve_audio_spec
from shokz.domain.errors import (
    FilenameCollision,
    NameAmbiguous,
    NameInvalid,
    NameOutsideOutputDir,
    ShokzError,
    SourceUnavailable,
)
from shokz.domain.filenames import sanitize_filename
from shokz.domain.models import TrackStatus
from shokz.observability.logging import configure_logging, set_run_id


def playlist_command(
    url: str = typer.Argument(..., help="YouTube playlist URL."),
    output: Path | None = typer.Option(None, "--output", "-o"),
    playlist_subdir: bool = typer.Option(
        True,
        "--playlist-subdir/--no-playlist-subdir",
        help="Land tracks under downloads/<playlist title>/ (default: yes).",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Bypass the large-playlist confirmation prompt."
    ),
    confirm_threshold: int | None = typer.Option(
        None,
        "--confirm-threshold",
        min=1,
        help=(
            "Playlists at or above this size require confirmation. "
            "(config: sources.youtube.playlist_confirm_threshold)"
        ),
    ),
    concurrency: int | None = typer.Option(
        None,
        "--concurrency",
        "-c",
        min=1,
        max=4,
        help=(
            "In-process parallel downloads within this playlist (1-4). "
            "Default 1 (sequential). Multi-process invocations against the "
            "same --output are NOT safe -- see Sprint 8. "
            "(config: general.concurrency)"
        ),
    ),
    keep_raw: bool | None = typer.Option(None, "--keep-raw/--no-keep-raw"),
    force: bool = typer.Option(False, "--force"),
    log_level: str | None = typer.Option(None, "--log-level"),
) -> None:
    """Expand a YouTube playlist URL and download every video as MP3."""

    cli_overrides: dict[str, object] = {}
    if output is not None:
        cli_overrides["general.output_dir"] = str(output)
    if concurrency is not None:
        cli_overrides["general.concurrency"] = concurrency
    if keep_raw is not None:
        cli_overrides["general.keep_raw"] = keep_raw
    if log_level is not None:
        cli_overrides["logging.level"] = log_level
    if confirm_threshold is not None:
        cli_overrides["sources.youtube.playlist_confirm_threshold"] = confirm_threshold

    try:
        loaded = load_config(cli_overrides=cli_overrides)
    except ConfigLoadError as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)
    config = loaded.config

    configure_logging(level=config.logging_.level)
    run_id = _now_iso_compact()
    set_run_id(run_id)
    log = logging.getLogger("shokz.cli")
    log.info("playlist run_id=%s url=%s", run_id, url)

    container = build_container(config)

    # Step 1: expand the playlist via single network call (title + URLs).
    try:
        playlist_info = asyncio.run(container.expand_playlist.execute(url))
    except SourceUnavailable as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)
    except ShokzError as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)

    item_urls = playlist_info.item_urls
    playlist_title = playlist_info.title

    if not item_urls:
        typer.echo("error: playlist contains no items", err=True)
        sys.exit(1)

    typer.echo(f"playlist '{playlist_title}' resolved to {len(item_urls)} item(s)")

    # Step 2: large-playlist confirmation gate.
    threshold = config.sources.youtube.playlist_confirm_threshold
    if len(item_urls) >= threshold and not yes:
        typer.echo(
            f"error: playlist has {len(item_urls)} items >= threshold ({threshold}). "
            f"Pass --yes to confirm, or raise the threshold via "
            f"--confirm-threshold or sources.youtube.playlist_confirm_threshold.",
            err=True,
        )
        sys.exit(1)

    # Step 3: compute target_dir (per-playlist subdir or top-level).
    # Sprint 6 / Sprint 5 F1 follow-up: PlaylistInfo.title is already
    # populated by ExpandPlaylistUseCase (single network call). Drop the
    # leftover second extract_info round-trip and the bare-except fallback
    # that silently masked unrelated errors.
    output_dir = config.general.output_dir
    target_dir: Path | None = None
    if playlist_subdir:
        subdir_stem = sanitize_filename(playlist_title) or "playlist"
        target_dir = output_dir / subdir_stem

    inp = BatchDownloadInput(
        urls=tuple(item_urls),
        output_dir=output_dir,
        spec=resolve_audio_spec(config.audio),
        concurrency=config.general.concurrency,
        keep_raw=config.general.keep_raw,
        name_override=None,  # --name is single-URL only; not exposed for playlists
        force=force,
        target_dir=target_dir,
    )

    try:
        result = asyncio.run(container.batch_download.execute(inp))
    except NameAmbiguous as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(2)
    except NameInvalid as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(2)
    except NameOutsideOutputDir as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)
    except FilenameCollision as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)
    except ShokzError as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        log.exception("unexpected error")
        typer.echo(f"unexpected error: {e!r}", err=True)
        sys.exit(1)

    typer.echo(
        f"\n{result.succeeded}/{len(result.results)} succeeded "
        f"({result.skipped} skipped, {result.failed} failed) "
        f"in {result.elapsed_s:.1f}s"
    )
    for r in result.results:
        if r.status is TrackStatus.SUCCESS and r.final_path is not None:
            typer.echo(f"  OK    {r.final_path.name}")
        elif r.status is TrackStatus.SKIPPED and r.final_path is not None:
            typer.echo(f"  SKIP  {r.final_path.name}")
        else:
            typer.echo(f"  FAIL  {r.error or '(unknown)'}", err=True)

    if result.failed > 0:
        sys.exit(1)


def _now_iso_compact() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
