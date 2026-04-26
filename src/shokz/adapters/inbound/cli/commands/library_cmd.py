"""shokz library list|show|verify -- Sprint 4.5."""

from __future__ import annotations

import asyncio
import sys

import typer

from shokz.composition import build_container
from shokz.config.loader import ConfigLoadError, load_config

library_app = typer.Typer(
    name="library",
    help="Inspect the local library (manifest + downloads/).",
    no_args_is_help=True,
    add_completion=False,
)


@library_app.command("list")
def library_list() -> None:
    """List every manifest entry as a table."""
    try:
        loaded = load_config()
    except ConfigLoadError as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)
    container = build_container(loaded.config)
    entries = asyncio.run(container.list_library.execute())

    if not entries:
        typer.echo("(no entries — manifest is empty)")
        return

    typer.echo(f"{'TITLE':<40}  {'SOURCE':<10}  {'ID':<14}  {'KBPS':>4}  {'DURATION':>10}  WHEN")
    typer.echo("-" * 110)
    for entry in entries:
        title = (entry.original_title or entry.filename_stem)[:40]
        dur = f"{int(entry.duration_s) // 60}:{int(entry.duration_s) % 60:02d}"
        typer.echo(
            f"{title:<40}  {entry.source:<10}  {entry.track_id:<14}  "
            f"{entry.bitrate_kbps:>4}  {dur:>10}  {entry.downloaded_at}"
        )


@library_app.command("show")
def library_show(
    track_id: str = typer.Argument(..., help="Track ID (e.g. YouTube video ID)."),
    source: str = typer.Option("youtube", "--source", help="Source name (default: youtube)."),
) -> None:
    """Print one manifest entry's full detail."""
    try:
        loaded = load_config()
    except ConfigLoadError as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)
    container = build_container(loaded.config)
    entry = asyncio.run(container.show_library.execute(track_id=track_id, source=source))
    if entry is None:
        typer.echo(f"error: no manifest entry for ({source}, {track_id})", err=True)
        sys.exit(1)

    typer.echo(f"track_id        : {entry.track_id}")
    typer.echo(f"source          : {entry.source}")
    typer.echo(f"original_title  : {entry.original_title}")
    typer.echo(f"filename_stem   : {entry.filename_stem}")
    typer.echo(f"mp3_path        : {entry.mp3_path}")
    typer.echo(f"bitrate_kbps    : {entry.bitrate_kbps}")
    typer.echo(f"duration_s      : {entry.duration_s}")
    typer.echo(f"downloaded_at   : {entry.downloaded_at}")
    typer.echo(f"schema_version  : {entry.schema_version}")


@library_app.command("verify")
def library_verify() -> None:
    """Reconcile manifest entries against files on disk."""
    try:
        loaded = load_config()
    except ConfigLoadError as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)
    container = build_container(loaded.config)
    report = asyncio.run(container.verify_library.execute())

    typer.echo(f"OK             : {len(report.ok)}")
    typer.echo(f"orphan files   : {len(report.orphan_files)}")
    typer.echo(f"orphan entries : {len(report.orphan_entries)}")

    if report.orphan_files:
        typer.echo("", err=True)
        typer.echo("orphan files (on disk, not in manifest):", err=True)
        typer.echo(
            "  (likely cause: process killed between os.replace and manifest "
            "record -- Sprint 4 SF-4 window)",
            err=True,
        )
        for p in report.orphan_files:
            typer.echo(f"  - {p}", err=True)

    if report.orphan_entries:
        typer.echo("", err=True)
        typer.echo("orphan manifest entries (in manifest, not on disk):", err=True)
        for entry in report.orphan_entries:
            typer.echo(f"  - {entry.mp3_path} (track_id={entry.track_id})", err=True)

    if not report.is_clean:
        sys.exit(1)
