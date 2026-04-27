"""`shokz download URL [URL...]` — Sprint 3 (config-aware).

Loads AppConfig (TOML + env + CLI), layers CLI flags on top, passes to the
composition root. CLI flags use sentinel defaults so unspecified means
"use the config layer's value".
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC
from pathlib import Path
from typing import Any

import typer

from shokz.adapters.inbound.cli._summary import print_batch_summary
from shokz.application.use_cases.batch_download import BatchDownloadInput
from shokz.composition import build_container
from shokz.config.loader import ConfigLoadError, load_config
from shokz.config.presets import resolve_audio_spec
from shokz.config.schema import AudioPreset
from shokz.domain.errors import (
    FilenameCollision,
    NameAmbiguous,
    NameInvalid,
    NameOutsideOutputDir,
    ShokzError,
)
from shokz.observability.logging import configure_logging, set_run_id


def download_command(
    urls: list[str] = typer.Argument(..., help="One or more YouTube URLs."),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory for final MP3s. (config: general.output_dir)",
    ),
    name: str = typer.Option(
        "",
        "--name",
        help="Override filename for a single URL (no extension).",
    ),
    concurrency: int | None = typer.Option(
        None,
        "--concurrency",
        "-c",
        min=1,
        max=4,
        help=(
            "In-process parallel downloads (1-4). Default 1 (sequential). "
            "Multi-process invocations against the same --output are NOT "
            "safe -- see Sprint 8. (config: general.concurrency)"
        ),
    ),
    keep_raw: bool | None = typer.Option(
        None,
        "--keep-raw/--no-keep-raw",
        help="Keep .tmp/ raw downloads after encode. (config: general.keep_raw)",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-download even if a manifest entry + file already exist.",
    ),
    preset: AudioPreset | None = typer.Option(
        None,
        "--preset",
        "-p",
        help="swim-low | swim-standard | swim-high | custom (config: audio.preset)",
        case_sensitive=False,
    ),
    bitrate: int | None = typer.Option(
        None,
        "--bitrate",
        "-b",
        min=16,
        max=320,
        help="MP3 bitrate kbps (only when --preset custom; config: audio.bitrate_kbps)",
    ),
    log_level: str | None = typer.Option(
        None,
        "--log-level",
        help="DEBUG | INFO | WARNING | ERROR (config: logging.level)",
    ),
) -> None:
    """Download one or more YouTube videos and convert each to MP3."""

    # Build CLI overrides dict (only non-None values count).
    cli_overrides: dict[str, Any] = {}
    if output is not None:
        cli_overrides["general.output_dir"] = str(output)
    if concurrency is not None:
        cli_overrides["general.concurrency"] = concurrency
    if keep_raw is not None:
        cli_overrides["general.keep_raw"] = keep_raw
    if preset is not None:
        cli_overrides["audio.preset"] = preset.value
    if bitrate is not None:
        cli_overrides["audio.bitrate_kbps"] = bitrate
    if log_level is not None:
        cli_overrides["logging.level"] = log_level

    try:
        loaded = load_config(cli_overrides=cli_overrides)
    except ConfigLoadError as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)
    config = loaded.config

    configure_logging(level=config.logging_.level)
    run_id = _now_iso_compact()
    set_run_id(run_id)
    logging.getLogger("shokz.cli").info(
        "run_id=%s urls=%d concurrency=%d", run_id, len(urls), config.general.concurrency
    )

    # Sprint 2: --name only valid with exactly one URL (CLI pre-check).
    name_override = name if name else None
    if name_override is not None and len(urls) != 1:
        typer.echo(
            f"error: --name requires exactly one URL, got {len(urls)}",
            err=True,
        )
        sys.exit(2)

    container = build_container(config)
    inp = BatchDownloadInput(
        urls=tuple(urls),
        output_dir=config.general.output_dir,
        spec=resolve_audio_spec(config.audio),
        concurrency=config.general.concurrency,
        keep_raw=config.general.keep_raw,
        name_override=name_override,
        force=force,
    )

    try:
        result = asyncio.run(container.batch_download.execute(inp))
    except NameAmbiguous as e:
        # User error: --name + multiple URLs. Exit 2 = invalid invocation.
        typer.echo(f"error: {e}", err=True)
        sys.exit(2)
    except NameInvalid as e:
        # User error: --name sanitizes to empty. Exit 2 = invalid invocation.
        typer.echo(f"error: {e}", err=True)
        sys.exit(2)
    except NameOutsideOutputDir as e:
        # Security/config issue: traversal or symlinked output_dir. Exit 1.
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)
    except FilenameCollision as e:
        # Runtime state issue: suffix loop exhausted. Exit 1.
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)
    except ShokzError as e:
        # Any other domain error: clean message, exit 1.
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        # F4 (Sprint 2 silent-failure fix): top-level catch-all so users see a
        # clean message instead of a Python traceback. Sprint 7 will narrow.
        logging.getLogger("shokz.cli").exception("unexpected error")
        typer.echo(f"unexpected error: {e!r} (run with --log-level DEBUG for details)", err=True)
        sys.exit(1)

    print_batch_summary(result, kind="batch")

    if result.failed > 0:
        sys.exit(1)


def _now_iso_compact() -> str:
    """Filesystem-safe ISO-8601 timestamp (e.g. 2026-04-26T22-41-03)."""
    from datetime import datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
