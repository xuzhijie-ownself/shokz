"""`shokz split` -- Sprint 11: chop a long MP3 into hour-sized parts.

Thin CLI wrapper over `SplitAudioUseCase`. Exit codes follow the
project convention:
  0   -- parts written
  1   -- SplitFailed (missing source, ffmpeg failure, would-be clobber)
  2   -- invalid invocation (`--hours 0`, caught by Typer's `min=`)

Unlike download / playlist / retry, this command takes NO cross-process
lock and touches NO manifest: it emits part-suffixed filenames that
`download` would never produce and writes nothing under `.shokz/`, so it
cannot race a concurrent download. See `split_audio.py` for the full
rationale.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

import typer

from shokz.application.use_cases.split_audio import (
    SplitAudioInput,
    SplitAudioResult,
)
from shokz.composition import build_container
from shokz.config.loader import ConfigLoadError, load_config
from shokz.domain.errors import SplitFailed
from shokz.observability.logging import configure_logging


def split_command(
    source: Path = typer.Argument(
        ...,
        help="Path to the audio file to split (e.g. downloads/Long Book.mp3).",
    ),
    hours: float = typer.Option(
        1.0,
        "--hours",
        "-H",
        min=0.001,
        help=(
            "Length of each part, in hours. 1.0 = hourly parts; 0.5 = "
            "half-hourly. Must be greater than 0."
        ),
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Directory for the parts. Default: alongside the source file.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite parts from a previous split of the same file.",
    ),
    log_level: str | None = typer.Option(
        None,
        "--log-level",
        help="DEBUG | INFO | WARNING | ERROR",
    ),
) -> None:
    """Split a long audio file into hour-sized parts (lossless).

    An 11-hour audiobook is one unnavigable file on a Shokz device.
    Splitting turns it into 12 tracks you can skip between underwater.
    Uses ffmpeg stream-copy: no re-encode, no quality loss, seconds not
    minutes.
    """
    cli_overrides: dict[str, Any] = {}
    if log_level is not None:
        cli_overrides["logging.level"] = log_level

    try:
        loaded = load_config(cli_overrides=cli_overrides)
    except ConfigLoadError as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)
    config = loaded.config
    configure_logging(level=config.logging_.level)

    container = build_container(config)
    inp = SplitAudioInput(
        source=source,
        hours=hours,
        output_dir=output,
        force=force,
    )

    try:
        result = asyncio.run(container.split_audio.execute(inp))
    except SplitFailed as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logging.getLogger("shokz.cli").exception("unexpected error during split")
        typer.echo(
            f"unexpected error: {e!r} (run with --log-level DEBUG for details)",
            err=True,
        )
        sys.exit(1)

    _print_split_summary(result)


def _print_split_summary(result: SplitAudioResult) -> None:
    minutes = result.segment_seconds // 60
    typer.echo(
        f"\nsplit {result.source.name} into {len(result.parts)} part(s) "
        f"of up to {minutes} min:"
    )
    for part in result.parts:
        size_mb = part.stat().st_size / (1024 * 1024)
        typer.echo(f"  {part.name}  ({size_mb:.1f} MB)")
    typer.echo(f"\nParts written to {result.parts[0].parent}")
    typer.echo(f"Original left in place: {result.source}")
