"""`shokz retry` -- Sprint 8.5: re-process failures.jsonl.

Command surface (per docs/sprints/sprint-8.5.md):

  shokz retry [--output PATH] [--since TIMESTAMP_OR_RELATIVE]
              [--error-class CLS [--error-class CLS ...]]
              [--all] [--dry-run] [-c CONCURRENCY]
              [--log-level LEVEL]

Spec C4: when failures.jsonl exists, the lock is acquired BEFORE
`iter_failures` so the read happens inside the single-writer guarantee.
When stat says the file is absent we short-circuit BEFORE lock acquire
(no work to serialize -- consistent with the `--dry-run` path having no
manifest writes either).

Phase C GAN H1 -- accepted TOCTOU: a concurrent `shokz download` may
write the FIRST `failures.jsonl` row between our stat and our return.
That row is not lost -- it persists for the next `shokz retry` run
(failures.jsonl is append-only). Bounded race; not worth holding the
lock around an empty-file probe.

Spec U4: `shokz retry` respects skip-existing; it is NOT a force-
reencode tool. To re-encode previously-failed tracks at a new bitrate,
use `shokz download <url> --force` instead.
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from shokz.adapters.inbound.cli._runtime import (
    assert_output_dir_safe,
    build_output_lock,
    run_async_with_sigint,
)
from shokz.application.use_cases.retry_failed import (
    RetryFailedInput,
    RetryFailedResult,
    parse_since,
)
from shokz.composition import build_container
from shokz.config.loader import ConfigLoadError, load_config
from shokz.config.presets import resolve_audio_spec
from shokz.domain.errors import (
    AnotherRunInProgress,
    LockOwnerUnknown,
    ManifestReadError,
    NameOutsideOutputDir,
    ShokzError,
    StaleLock,
)
from shokz.observability.logging import configure_logging, set_run_id


def retry_command(
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory whose .shokz/failures.jsonl is the input feed.",
    ),
    since: str | None = typer.Option(
        None,
        "--since",
        help=(
            "Only retry failures since this point. Accepts ISO-8601 "
            "(2026-04-30 or 2026-04-30T12:00:00Z) or relative "
            "(2d, 12h, 1w). Recommended for first-run safety; without it "
            "the command warns when the candidate set is unbounded."
        ),
    ),
    error_class: list[str] = typer.Option(
        [],
        "--error-class",
        "-e",
        help=(
            "Explicit error class to retry (repeatable). "
            "Default: NETWORK_ERROR / RATE_LIMITED / SOURCE_FILE_CORRUPT "
            "/ DOWNLOAD_FAILED. Mutually exclusive with --all."
        ),
    ),
    all_classes: bool = typer.Option(
        False,
        "--all",
        help=(
            "Retry every failed row, including normally-terminal classes "
            "(AUTH_REQUIRED, FORMAT_UNAVAILABLE, ...). Use sparingly; "
            "terminal-class entries usually fail again."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the retry plan and exit without invoking the use case.",
    ),
    concurrency: int | None = typer.Option(
        None,
        "--concurrency",
        "-c",
        min=1,
        max=4,
        help=(
            "In-process parallel downloads (1-4). Default 1 (sequential). "
            "(config: general.concurrency)"
        ),
    ),
    log_level: str | None = typer.Option(
        None,
        "--log-level",
        help="DEBUG | INFO | WARNING | ERROR (config: logging.level)",
    ),
) -> None:
    """Re-process failures.jsonl through `shokz download`.

    Respects skip-existing -- tracks already in the manifest no-op
    without a network call. NOT a force-reencode tool; pass --force to
    `shokz download` for that.
    """

    cli_overrides: dict[str, Any] = {}
    if output is not None:
        cli_overrides["general.output_dir"] = str(output)
    if concurrency is not None:
        cli_overrides["general.concurrency"] = concurrency
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
    log = logging.getLogger("shokz.cli")
    log.info("retry run_id=%s", run_id)

    # M5 (Phase C GAN): cheap flag-combo validation FIRST so a user
    # passing both an invalid --since AND incompatible flags sees the
    # combo error in one shot, not two iterations.
    if error_class and all_classes:
        typer.echo(
            "error: --all and --error-class are mutually exclusive; "
            "pass one or the other",
            err=True,
        )
        sys.exit(2)
    error_classes_set: frozenset[str] | None = (
        frozenset(c.upper() for c in error_class) if error_class else None
    )

    # Parse --since BEFORE locking (cheap; user errors should fail fast).
    since_dt: datetime | None = None
    if since is not None:
        try:
            since_dt = parse_since(since)
        except ValueError as e:
            typer.echo(f"error: {e}", err=True)
            sys.exit(2)

    # Sprint 9: symlink-safety pre-check BEFORE the stat short-circuit
    # below. Without this, a symlinked --output whose target lacks
    # failures.jsonl would silently exit 0 ("no failures to retry"),
    # masking a misconfigured output_dir from the user. M1 carry-forward
    # from Sprint 8.5 Phase C.
    try:
        assert_output_dir_safe(config)
    except NameOutsideOutputDir as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)

    container = build_container(config)

    # Spec C4: stat the failures path BEFORE acquiring the lock. Empty
    # short-circuit avoids serializing nothing. If the file exists, the
    # lock is held for the full read + dedup + delegate window.
    failures_path = config.general.output_dir / ".shokz" / "failures.jsonl"
    if not _stat_exists(failures_path):
        typer.echo("no failures to retry")
        return

    inp = RetryFailedInput(
        output_dir=config.general.output_dir,
        spec=resolve_audio_spec(config.audio),
        concurrency=config.general.concurrency,
        keep_raw=config.general.keep_raw,
        since=since_dt,
        error_classes=error_classes_set,
        include_terminal=all_classes,
        dry_run=dry_run,
    )

    output_lock = build_output_lock(config)
    try:
        with output_lock:
            try:
                result = run_async_with_sigint(
                    container.retry_failed.execute(inp)
                )
            except KeyboardInterrupt:
                typer.echo("interrupted", err=True)
                sys.exit(130)
            except ManifestReadError as e:
                typer.echo(f"error: {e}", err=True)
                sys.exit(1)
            except ValueError as e:
                # H3 (Phase C GAN): defense in depth -- the CLI pre-check
                # at line 148 already rejects --all + --error-class. This
                # catch fires only for non-CLI callers (tests, library
                # use, future IPC) that bypass the pre-check.
                typer.echo(f"error: {e}", err=True)
                sys.exit(2)
            except ShokzError as e:
                typer.echo(f"error: {e}", err=True)
                sys.exit(1)
            except Exception as e:
                log.exception("unexpected error during retry")
                typer.echo(
                    f"unexpected error: {e!r} (run with --log-level DEBUG for details)",
                    err=True,
                )
                sys.exit(1)
    except (AnotherRunInProgress, StaleLock, LockOwnerUnknown) as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)

    # Print the plan / summary.
    if dry_run:
        _print_dry_run(result)
        return

    if not result.planned:
        typer.echo("no failures to retry (after filters)")
        return

    # Defer the per-track summary to the existing batch summary.
    from shokz.adapters.inbound.cli._summary import print_batch_summary

    if result.batch_result is not None:
        print_batch_summary(result.batch_result, kind="batch")

    # Surface the dedup decisions (always; cheap to print).
    _print_retry_audit(result)

    if result.batch_result is not None and result.batch_result.failed > 0:
        sys.exit(1)


def _stat_exists(path: Path) -> bool:
    """Spec C4: separate file-absent (short-circuit) from
    file-unreadable (caller handles via ManifestReadError after lock).
    Pure stat; no read. OSError on stat propagates to the caller (the
    CLI command above wraps it via ManifestReadError downstream)."""
    try:
        return path.is_file()
    except OSError:
        # Treat stat failure as "exists, attempt read under lock and let
        # ManifestReadError surface the real reason".
        return True


def _print_dry_run(result: RetryFailedResult) -> None:
    """Print the planned retry set for --dry-run review."""
    typer.echo(f"\n[dry-run] would retry {len(result.planned)} track(s):")
    for entry in result.planned:
        typer.echo(
            f"  {entry.error_class:<22} {entry.failed_at}  {entry.url}"
        )
    _print_retry_audit(result)
    typer.echo("\n[dry-run] no downloads attempted, no manifest changes.")


def _print_retry_audit(result: RetryFailedResult) -> None:
    """Surface the dedup + filter decisions so users know why a row
    didn't make it into the retry plan. Always called (cheap)."""
    audit_lines: list[str] = []
    if result.skipped_terminal:
        audit_lines.append(
            f"  {len(result.skipped_terminal)} skipped (terminal class; "
            "pass --all to override)"
        )
    if result.skipped_old:
        audit_lines.append(
            f"  {len(result.skipped_old)} skipped (older than --since)"
        )
    if result.skipped_deduped:
        audit_lines.append(
            f"  {len(result.skipped_deduped)} skipped (older retry attempt "
            "for same track; newest wins)"
        )
    if result.skipped_url_variants:
        audit_lines.append(
            f"  {len(result.skipped_url_variants)} skipped (url-variant "
            "for same track; newest URL kept -- see WARNING log for diff)"
        )
    if result.skipped_malformed:
        audit_lines.append(
            f"  {len(result.skipped_malformed)} skipped (unparseable "
            "failed_at; see WARNING log)"
        )
    if result.null_identity_count:
        audit_lines.append(
            f"  {result.null_identity_count} kept via url-only key "
            "(resolve-failed rows with no source/track_id)"
        )
    # H4 (Phase C GAN): print audit to stdout (same stream as the batch
    # summary + dry-run plan). Audit is part of the result, not a
    # warning -- and writing it to stderr broke ordering when the user
    # piped stdout through a pager / `tee` while stderr went to the
    # terminal first. Single stream + deterministic order.
    if audit_lines:
        typer.echo("retry audit:")
        for line in audit_lines:
            typer.echo(line)


def _now_iso_compact() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
