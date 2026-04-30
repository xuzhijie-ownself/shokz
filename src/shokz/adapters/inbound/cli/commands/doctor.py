"""`shokz doctor` -- Sprint 10 read-only diagnostic command.

Renders `RunDoctorUseCase`'s checks as a per-line table and exits 0
if no FAIL was emitted, 1 otherwise. WARN does NOT trigger exit 1
(see `DoctorResult.has_failures`).
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any

import typer

from shokz.application.use_cases.doctor import (
    DoctorResult,
    RunDoctorUseCase,
)
from shokz.config.loader import ConfigLoadError, load_config


def doctor_command(
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory whose writability + disk-free are checked.",
    ),
    log_level: str | None = typer.Option(
        None,
        "--log-level",
        help="DEBUG | INFO | WARNING | ERROR",
    ),
) -> None:
    """Run read-only diagnostics over the user's environment + config.

    Checks: ffmpeg / ffprobe / yt-dlp present, output_dir not symlinked
    + writable, sufficient disk free. Exit 0 on all-PASS-or-WARN; exit 1
    on any FAIL.
    """
    cli_overrides: dict[str, Any] = {}
    if output is not None:
        cli_overrides["general.output_dir"] = str(output)
    if log_level is not None:
        cli_overrides["logging.level"] = log_level

    try:
        loaded = load_config(cli_overrides=cli_overrides)
    except ConfigLoadError as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)
    config = loaded.config

    uc = RunDoctorUseCase(
        config=config,
        which=shutil.which,
        disk_free_bytes=lambda p: shutil.disk_usage(p).free,
        ytdlp_version=_resolve_ytdlp_version,
    )
    result = asyncio.run(uc.execute())

    _print_doctor_table(result)
    if result.has_failures:
        sys.exit(1)


def _resolve_ytdlp_version() -> str:
    """Single-source-of-truth for yt-dlp version probing. Imported lazily
    so failures isolate to the check (rather than aborting CLI import)."""
    from yt_dlp.version import __version__  # type: ignore[import-untyped]

    return str(__version__)


def _print_doctor_table(result: DoctorResult) -> None:
    """Render checks as a human-scannable table.

    Format: `STATUS  NAME                    MESSAGE`. Status is the
    leftmost column for grep-friendliness (PASS / WARN / FAIL).
    """
    typer.echo("\nshokz doctor:")
    for c in result.checks:
        typer.echo(f"  {c.status:<5} {c.name:<22} {c.message}")
    if result.has_failures:
        typer.echo(
            "\nOne or more checks FAILED. Fix the issues above and re-run.",
            err=True,
        )
    else:
        typer.echo("\nAll checks passed (WARN entries informational).")
