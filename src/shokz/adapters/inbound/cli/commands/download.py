"""`shokz download URL [URL...]` — Sprint 1 single command.

Sprint 1 hard-codes:
  - output_dir = ./downloads
  - preset     = SWIM_STANDARD (64 kbps mono)
  - concurrency= 3

Sprint 3 wires these to AppConfig (TOML/env/CLI overrides).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC
from pathlib import Path

import typer

from shokz.application.use_cases.batch_download import BatchDownloadInput
from shokz.composition import build_container
from shokz.domain.errors import (
    FilenameCollision,
    NameAmbiguous,
    NameInvalid,
    NameOutsideOutputDir,
    ShokzError,
)
from shokz.domain.models import TrackStatus
from shokz.domain.presets import SWIM_STANDARD
from shokz.observability.logging import configure_logging, set_run_id


def download_command(
    urls: list[str] = typer.Argument(..., help="One or more YouTube URLs."),
    output: Path = typer.Option(
        Path("./downloads"),
        "--output",
        "-o",
        help="Output directory for final MP3s.",
    ),
    name: str = typer.Option(
        "",
        "--name",
        help="Override filename for a single URL (filename only, no extension).",
    ),
    concurrency: int = typer.Option(3, "--concurrency", "-c", min=1, max=16),
    keep_raw: bool = typer.Option(
        False, "--keep-raw", help="Keep .tmp/ raw downloads after encode."
    ),
    log_level: str = typer.Option("INFO", "--log-level", help="DEBUG | INFO | WARNING | ERROR"),
) -> None:
    """Download one or more YouTube videos and convert each to MP3."""

    configure_logging(level=log_level)
    run_id = _now_iso_compact()
    set_run_id(run_id)
    logging.getLogger("shokz.cli").info(
        "run_id=%s urls=%d concurrency=%d", run_id, len(urls), concurrency
    )

    # Sprint 2: --name only valid with exactly one URL.
    name_override = name if name else None
    if name_override is not None and len(urls) != 1:
        typer.echo(
            f"error: --name requires exactly one URL, got {len(urls)}",
            err=True,
        )
        sys.exit(2)

    container = build_container()
    inp = BatchDownloadInput(
        urls=tuple(urls),
        output_dir=output,
        spec=SWIM_STANDARD,
        concurrency=concurrency,
        keep_raw=keep_raw,
        name_override=name_override,
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

    typer.echo(
        f"\n{result.succeeded}/{len(result.results)} succeeded "
        f"({result.failed} failed) in {result.elapsed_s:.1f}s"
    )
    for r in result.results:
        if r.status is TrackStatus.SUCCESS and r.final_path is not None:
            typer.echo(f"  OK   {r.final_path.name}")
        else:
            typer.echo(f"  FAIL {r.error or '(unknown)'}", err=True)

    if result.failed > 0:
        sys.exit(1)


def _now_iso_compact() -> str:
    """Filesystem-safe ISO-8601 timestamp (e.g. 2026-04-26T22-41-03)."""
    from datetime import datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
