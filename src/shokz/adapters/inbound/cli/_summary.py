"""Shared CLI batch-summary printer.

Sprint 7 Phase 5 GAN MED#2: download.py and playlist.py had nearly-identical
summary blocks that already drifted in wording. This module is the single
source of truth for the per-track summary lines + Sprint 7 drift/breaker
warnings. Both commands import and call `print_batch_summary`.
"""

from __future__ import annotations

from typing import Literal

import typer

from shokz.application.use_cases.batch_download import BatchDownloadResult
from shokz.domain.models import TrackStatus

BatchKind = Literal["batch", "playlist"]


def print_batch_summary(result: BatchDownloadResult, kind: BatchKind) -> None:
    """Print the standard run-end summary lines (succeeded/skipped/failed,
    per-track OK/SKIP/FAIL, Sprint 7 drift counter, Sprint 7 circuit-
    breaker notice). Stdout for results; stderr for warnings + failures."""
    typer.echo(
        f"\n{result.succeeded}/{len(result.results)} succeeded "
        f"({result.skipped} skipped, {result.failed} failed) "
        f"in {result.elapsed_s:.1f}s"
    )
    for r in result.results:
        if r.status is TrackStatus.SUCCESS and r.final_path is not None:
            typer.echo(f"  OK    {r.final_path.name}")
        elif r.status is TrackStatus.SKIPPED and r.final_path is not None:
            typer.echo(f"  SKIP  {r.final_path.name} (already in manifest)")
        else:
            typer.echo(f"  FAIL  {r.error or '(unknown)'}", err=True)

    # Sprint 7 GAN U8: surface §7.1 drift to the user so they know to report
    # novel yt-dlp error shapes for the classification table to grow.
    if result.unclassified_yt_dlp_errors > 0:
        typer.echo(
            f"  {result.unclassified_yt_dlp_errors} unclassified yt-dlp "
            "error(s) -- please report to extend §7.1 "
            "(run with --log-level WARNING for raw text)",
            err=True,
        )
    # Sprint 7 GAN C4: surface circuit-breaker trip so user understands why
    # the rest of the run didn't retry.
    if result.rate_limit_circuit_tripped:
        scope = "batch" if kind == "batch" else "playlist"
        typer.echo(
            f"  rate-limit circuit breaker tripped: rest of {scope} ran "
            "without retry (try again later)",
            err=True,
        )
    # Sprint 8b: surface DiskFull. At concurrency=1 the first DiskFull
    # short-circuits the rest cleanly; at concurrency>1 multiple in-flight
    # tracks may all hit ENOSPC before the circuit trips, so distinguish
    # the trigger(s) from the truly-short-circuited aborts (Sprint 8b GAN
    # MED#3 -- concurrency-aware messaging).
    if result.disk_full_count > 0:
        scope = "batch" if kind == "batch" else "playlist"
        aborted = sum(
            1
            for r in result.results
            if r.error and "aborted by prior DiskFull" in r.error
        )
        triggered = result.disk_full_count - aborted
        typer.echo(
            f"  disk full: {triggered} track(s) hit ENOSPC, "
            f"{aborted} short-circuited by the {scope} circuit "
            "(free up space and re-run -- skip-existing will resume)",
            err=True,
        )
