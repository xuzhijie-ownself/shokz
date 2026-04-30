"""RetryFailedUseCase -- Sprint 8.5 retry-from-failures-jsonl orchestrator.

Treats `failures.jsonl` as the input feed for a re-run via the existing
`BatchDownloadUseCase`. Filters by error class and time, dedupes by
(source, track_id), and surfaces the dedup decisions in the result so
`shokz retry --dry-run` is fully auditable.

Spec carry-overs (docs/sprints/sprint-8.5.md GAN-fix manifest):

  - C2: null-identity (source=None, track_id=None) rows key on `url`
        instead of (source, track_id) so 4 separate resolve-failures
        don't collapse to 1.
  - C3: when two rows share (source, track_id) but differ in `url`, log
        WARNING + record the loser in `skipped_url_variants`.
  - C5: scope-warn when --since=None and (oldest > 7 days OR > 50 rows).
  - C6: --since and failed_at parsed as TIMEZONE-AWARE UTC datetimes via
        explicit strptime (NOT fromisoformat -- version-stable).
  - U2: skipped_deduped + skipped_url_variants + null_identity_count
        surfaced in RetryFailedResult.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from shokz.application.ports.outbound.manifest import ManifestPort
from shokz.application.use_cases.batch_download import (
    BatchDownloadInput,
    BatchDownloadResult,
    BatchDownloadUseCase,
)
from shokz.domain.models import AudioSpec, FailureEntry

_log = logging.getLogger("shokz.usecase.retry_failed")

# Sprint 8.5 spec: §"RETRYABLE_CLASSES" -- transient classes only.
# Terminal classes (AUTH_REQUIRED, FORMAT_UNAVAILABLE, SOURCE_UNAVAILABLE,
# NAME_*, ENCODING_FAILED, MANIFEST_INCONSISTENT, DISK_FULL,
# UNEXPECTED_ERROR) are skipped unless --all (include_terminal=True).
RETRYABLE_CLASSES: Final[frozenset[str]] = frozenset({
    "NETWORK_ERROR",
    "RATE_LIMITED",
    "SOURCE_FILE_CORRUPT",
    "DOWNLOAD_FAILED",
})

# Sprint 8.5 C5: scope-warn thresholds.
_SCOPE_WARN_DAYS: Final[int] = 7
_SCOPE_WARN_ROW_COUNT: Final[int] = 50

# Sprint 8.5 C6: explicit format string -- self-documenting + Python-3.10-
# compatible (fromisoformat in 3.10 doesn't accept the trailing 'Z').
_FAILED_AT_FMT: Final[str] = "%Y-%m-%dT%H:%M:%SZ"

# Relative-since parser: integer + unit. Anchored, strict, case-insensitive.
_RELATIVE_SINCE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(\d+)([smhdw])$", re.IGNORECASE
)
_RELATIVE_UNIT_DELTAS: Final[Mapping[str, timedelta]] = {
    "s": timedelta(seconds=1),
    "m": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
}


def parse_since(raw: str, *, now: datetime | None = None) -> datetime:
    """Parse `--since` string into a TIMEZONE-AWARE UTC datetime.

    Accepts:
      - relative: '\\d+[smhdw]' (e.g. "2d", "12h", "1w")
      - ISO-8601 UTC: "YYYY-MM-DDTHH:MM:SSZ" or "YYYY-MM-DD"

    Raises:
      ValueError if raw doesn't match either form. Caller is the CLI;
      surfaces to the user as a clean error message.
    """
    # Phase B GAN F11: naive `now` would silently propagate as a
    # naive `since`, then crash at comparison vs aware `failed_at`.
    if now is not None and now.tzinfo is None:
        raise ValueError("parse_since: `now` must be timezone-aware")
    raw = raw.strip()
    rel = _RELATIVE_SINCE_RE.match(raw)
    if rel is not None:
        n, unit = rel.group(1), rel.group(2).lower()
        anchor = now or datetime.now(UTC)
        # Phase B GAN F10: huge n (e.g. "9999999999d") raises OverflowError
        # from C-int conversion; wrap as ValueError to keep the docstring
        # contract.
        try:
            return anchor - int(n) * _RELATIVE_UNIT_DELTAS[unit]
        except OverflowError as e:
            raise ValueError(
                f"--since: relative duration {raw!r} is out of range"
            ) from e
    # Fall back to ISO-8601. Try the failed_at shape first (Z-suffixed),
    # then date-only.
    try:
        return datetime.strptime(raw, _FAILED_AT_FMT).replace(tzinfo=UTC)
    except ValueError:
        pass
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as e:
        raise ValueError(
            f"--since must be relative (e.g. '2d', '12h', '1w') or "
            f"ISO-8601 UTC (e.g. '2026-04-30' or '2026-04-30T12:00:00Z'); "
            f"got {raw!r}"
        ) from e


def _parse_failed_at(raw: str) -> datetime:
    """Sprint 8.5 C6: parse a failures.jsonl `failed_at` string into a
    timezone-aware UTC datetime via explicit strptime so the comparison
    against `since` cannot raise TypeError on naive/aware mismatch.
    """
    return datetime.strptime(raw, _FAILED_AT_FMT).replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class RetryFailedInput:
    """Inputs for `RetryFailedUseCase.execute`. Mirrors
    `BatchDownloadInput` for the fields that pass through to the underlying
    download, plus the retry-specific filter knobs.
    """

    output_dir: Path
    spec: AudioSpec
    concurrency: int = 1
    keep_raw: bool = False
    # Sprint 8.5: filter knobs.
    since: datetime | None = None        # spec C5: None means "all time" (with warn)
    error_classes: frozenset[str] | None = None  # explicit allow-list overrides RETRYABLE_CLASSES
    include_terminal: bool = False       # --all
    dry_run: bool = False


@dataclass(frozen=True, slots=True)
class RetryFailedResult:
    """Outcome of a `shokz retry` invocation. Captures the dedup +
    filter decisions so the CLI summary (and a future `--dry-run`
    output) can fully account for every input row.

    Sprint 8.5 GAN U2: skipped_deduped + skipped_url_variants +
    null_identity_count are first-class fields, not log-only signals.
    """

    planned: tuple[FailureEntry, ...]
    skipped_terminal: tuple[FailureEntry, ...] = ()
    skipped_old: tuple[FailureEntry, ...] = ()
    skipped_deduped: tuple[FailureEntry, ...] = ()
    skipped_url_variants: tuple[FailureEntry, ...] = ()
    # Phase B GAN F2: rows whose `failed_at` couldn't be parsed (corrupt,
    # partial-write that left empty string, future-schema sub-second
    # suffix the explicit strptime doesn't accept). Skipped + WARN, never
    # crash the run.
    skipped_malformed: tuple[FailureEntry, ...] = ()
    null_identity_count: int = 0
    batch_result: BatchDownloadResult | None = None  # None when dry_run
    # Sprint 8.5 C5: True when the use case emitted a scope warning so the
    # CLI can echo it (or the user's CI can grep for it).
    scope_warned: bool = field(default=False)


class RetryFailedUseCase:
    """Re-process `failures.jsonl` through `BatchDownloadUseCase`.

    The actual download flow (skip-existing, retry, lock, SIGINT shield,
    disk pre-flight) belongs to the underlying `BatchDownloadUseCase`;
    this use case is purely the filter + dedup + delegation layer.
    """

    def __init__(
        self,
        manifest: ManifestPort,
        batch_download: BatchDownloadUseCase,
    ) -> None:
        self._manifest = manifest
        self._batch_download = batch_download

    async def execute(self, inp: RetryFailedInput) -> RetryFailedResult:
        # Phase B GAN F6: --all + --error-class is ambiguous. Reject up
        # front so the CLI can surface a clean error rather than silently
        # ignoring one of the two flags.
        if inp.include_terminal and inp.error_classes is not None:
            raise ValueError(
                "--all (include_terminal=True) and --error-class "
                "(error_classes) are mutually exclusive; pass one or "
                "the other"
            )

        # 1. Read every failure entry. iter_failures wraps OSError as
        #    ManifestReadError per Phase A C1 -- the CLI handles that
        #    branch, not us.
        all_entries: list[FailureEntry] = [
            entry async for entry in self._manifest.iter_failures()
        ]

        # 2. Filter by error_class. Phase C refactor: extracted to
        #    `_filter_by_class` (free function) so `execute()` orchestrates
        #    rather than mixes orchestration with classification logic.
        class_filtered, skipped_terminal = _filter_by_class(
            all_entries,
            error_classes=inp.error_classes,
            include_terminal=inp.include_terminal,
        )

        # 3. Filter by --since (skips malformed `failed_at` per F2).
        time_filtered, skipped_old, skipped_malformed = _filter_by_since(
            class_filtered, since=inp.since,
        )

        # 4. Sprint 8.5 C5: scope-warn when --since=None and the candidate
        #    set is unbounded (oldest > 7 days OR > 50 candidates).
        scope_warned = False
        if inp.since is None and time_filtered:
            scope_warned = self._maybe_scope_warn(time_filtered)

        # 5. Sprint 8.5 C2 + C3: dedup by (source, track_id), with
        #    null-identity falling back to url-only key. Record the
        #    losers + url-variants in the result for auditability.
        planned, skipped_deduped, skipped_url_variants, null_identity_count = (
            _dedupe(time_filtered)
        )

        # 6. Dry-run short-circuit (Gherkin scenario 5).
        if inp.dry_run:
            return RetryFailedResult(
                planned=tuple(planned),
                skipped_terminal=tuple(skipped_terminal),
                skipped_old=tuple(skipped_old),
                skipped_deduped=tuple(skipped_deduped),
                skipped_url_variants=tuple(skipped_url_variants),
                skipped_malformed=tuple(skipped_malformed),
                null_identity_count=null_identity_count,
                batch_result=None,
                scope_warned=scope_warned,
            )

        # 7. Empty-plan short-circuit (Gherkin scenario 8 partial:
        #    failures.jsonl has rows but all filtered out -> nothing to do).
        if not planned:
            return RetryFailedResult(
                planned=(),
                skipped_terminal=tuple(skipped_terminal),
                skipped_old=tuple(skipped_old),
                skipped_deduped=tuple(skipped_deduped),
                skipped_url_variants=tuple(skipped_url_variants),
                skipped_malformed=tuple(skipped_malformed),
                null_identity_count=null_identity_count,
                batch_result=None,
                scope_warned=scope_warned,
            )

        # 8. Delegate to BatchDownloadUseCase. skip-existing fires per
        #    track via the existing v1.0.0 path; lock + SIGINT shield are
        #    held by the CLI layer.
        batch_input = BatchDownloadInput(
            urls=tuple(p.url for p in planned),
            output_dir=inp.output_dir,
            spec=inp.spec,
            concurrency=inp.concurrency,
            keep_raw=inp.keep_raw,
            # name_override / target_dir / force intentionally default --
            # see spec U4 (retry respects skip-existing; not a force-
            # reencode tool).
        )
        batch_result = await self._batch_download.execute(batch_input)

        return RetryFailedResult(
            planned=tuple(planned),
            skipped_terminal=tuple(skipped_terminal),
            skipped_old=tuple(skipped_old),
            skipped_deduped=tuple(skipped_deduped),
            skipped_url_variants=tuple(skipped_url_variants),
            skipped_malformed=tuple(skipped_malformed),
            null_identity_count=null_identity_count,
            batch_result=batch_result,
            scope_warned=scope_warned,
        )

    def _maybe_scope_warn(self, candidates: list[FailureEntry]) -> bool:
        """Sprint 8.5 C5: emit a SINGLE WARNING when --since=None spans
        > 7 days OR > 50 candidate rows. Returns True iff a warning fired.

        Phase B GAN F4: combine count + oldest-date context into one
        message so the user sees both pieces of context regardless of
        which trigger fired.

        Phase B GAN F2 followup: malformed failed_at rows that survived
        Phase A's _safe_construct (an unparseable string field) are
        skipped from the oldest-date computation. They've already been
        bucketed into `skipped_malformed` upstream so this is just a
        defensive filter.
        """
        too_many = len(candidates) > _SCOPE_WARN_ROW_COUNT
        parseable_dates: list[datetime] = []
        for entry in candidates:
            try:
                parseable_dates.append(_parse_failed_at(entry.failed_at))
            except ValueError:
                continue
        if not parseable_dates:
            # No parseable dates -- can't compute age but still warn on count.
            if too_many:
                _log.warning(
                    "retrying %d failures with no --since limit; "
                    "pass --since to limit scope",
                    len(candidates),
                )
                return True
            return False
        oldest_failed_at = min(parseable_dates)
        age_days = (datetime.now(UTC) - oldest_failed_at).days
        too_old = age_days > _SCOPE_WARN_DAYS
        if too_many or too_old:
            _log.warning(
                "retrying %d failures going back to %s (%d days); "
                "pass --since to limit scope",
                len(candidates),
                oldest_failed_at.strftime(_FAILED_AT_FMT),
                age_days,
            )
            return True
        return False


def _filter_by_class(
    entries: list[FailureEntry],
    *,
    error_classes: frozenset[str] | None,
    include_terminal: bool,
) -> tuple[list[FailureEntry], list[FailureEntry]]:
    """Returns (kept, skipped_terminal). When include_terminal=True,
    every entry is kept and a WARNING fires per terminal-class entry
    (--all semantics). Otherwise the explicit `error_classes` allow-list
    OVERRIDES the default RETRYABLE_CLASSES set; rows outside the list
    land in `skipped_terminal`.
    """
    allowed = error_classes if error_classes is not None else RETRYABLE_CLASSES
    kept: list[FailureEntry] = []
    skipped_terminal: list[FailureEntry] = []
    for entry in entries:
        if include_terminal:
            if entry.error_class not in RETRYABLE_CLASSES:
                _log.warning(
                    "--all bypasses terminal-class filter: queueing "
                    "%s for track %s/%s (likely to fail again)",
                    entry.error_class, entry.source, entry.track_id,
                )
            kept.append(entry)
        elif entry.error_class in allowed:
            kept.append(entry)
        else:
            skipped_terminal.append(entry)
    return kept, skipped_terminal


def _filter_by_since(
    entries: list[FailureEntry],
    *,
    since: datetime | None,
) -> tuple[list[FailureEntry], list[FailureEntry], list[FailureEntry]]:
    """Returns (kept, skipped_old, skipped_malformed).

    Phase B GAN F2: a row whose `failed_at` is unparseable (millisecond
    suffix, partial-write empty string, future schema) is bucketed into
    `skipped_malformed` + WARN, NEVER raised -- a single corrupt row
    must not abort the entire retry run.
    """
    kept: list[FailureEntry] = []
    skipped_old: list[FailureEntry] = []
    skipped_malformed: list[FailureEntry] = []
    for entry in entries:
        try:
            entry_dt = _parse_failed_at(entry.failed_at)
        except ValueError as e:
            _log.warning(
                "skipping row with unparseable failed_at %r for %s/%s: %s",
                entry.failed_at, entry.source, entry.track_id, e,
            )
            skipped_malformed.append(entry)
            continue
        if since is None or entry_dt >= since:
            kept.append(entry)
        else:
            skipped_old.append(entry)
    return kept, skipped_old, skipped_malformed


def _dedupe_sort_key(e: FailureEntry) -> datetime:
    """Sort key for `_dedupe`: parsed `failed_at` UTC datetime, with
    unparseable strings sentinel-mapped to `datetime.min` so they sort
    to the oldest position (a malformed row never wins the newest-wins
    contest). Phase B GAN F2 + F3."""
    try:
        return _parse_failed_at(e.failed_at)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def _dedupe(
    entries: list[FailureEntry],
) -> tuple[list[FailureEntry], list[FailureEntry], list[FailureEntry], int]:
    """Sprint 8.5 C2 + C3: dedupe `entries` by (source, track_id),
    keeping the newest `failed_at` per key. Null-identity rows
    (source=None or track_id=None) fall back to url-only keying so 4
    separate resolve-failures don't collapse to 1.

    Returns: (planned, skipped_deduped, skipped_url_variants, null_identity_count)

    - `planned`: entries kept (one per key, newest-wins)
    - `skipped_deduped`: same-key losers (older `failed_at`)
    - `skipped_url_variants`: same key (source, track_id) but DIFFERENT
      `url` values -- WARNING is logged per loser; this surfaces the
      conflict so the user knows we picked one URL over another
    - `null_identity_count`: entries that had to fall back to url keying
    """
    # Group by key. Tuple key uses (None, None, url) shape for null
    # identity to ensure those don't collide with each other.
    # Phase B GAN F1: spec C2 defines null-identity as BOTH fields None
    # (resolve-time failure where no Track was constructed). Partial-null
    # rows (one of source/track_id None) are not contemplated by the
    # current adapter -- if they arise from a future source they keep
    # the (source, track_id) shape they were given.
    grouped: dict[tuple[str | None, str | None, str | None], list[FailureEntry]] = {}
    for entry in entries:
        if entry.source is None and entry.track_id is None:
            key: tuple[str | None, str | None, str | None] = (None, None, entry.url)
        else:
            key = (entry.source, entry.track_id, None)
        grouped.setdefault(key, []).append(entry)

    planned: list[FailureEntry] = []
    skipped_deduped: list[FailureEntry] = []
    skipped_url_variants: list[FailureEntry] = []
    null_identity_count = 0
    # Phase B GAN F3: sort by parsed datetime (not lexicographic
    # `failed_at` string) so a future schema gaining sub-second
    # precision (`...:00.123Z`) doesn't invert chronological order.
    # Unparseable failed_at rows already filtered upstream by F2;
    # if any slip through here, they sort to the oldest position via
    # a sentinel min datetime so winners are never invalid.
    # Sprint 8.5 final-GAN H3: hoisted out of the for-loop (was being
    # redefined per group; pure refactor, no semantic change).
    for key, group in grouped.items():
        sorted_group = sorted(group, key=_dedupe_sort_key, reverse=True)
        winner = sorted_group[0]
        losers = sorted_group[1:]
        planned.append(winner)
        # Phase B GAN F7: count null-identity entries that ENDED UP in
        # planned (not the input count) so the CLI summary doesn't
        # overstate.
        if key[0] is None and key[1] is None:
            null_identity_count += 1
        # C3: detect url-variant collisions among same-(source,track_id) groups.
        if key[1] is not None:  # only meaningful for non-null-identity keys
            for loser in losers:
                if loser.url != winner.url:
                    _log.warning(
                        "url-variant collision for (%s, %s): kept %s, "
                        "discarded %s (failed_at: %s vs %s)",
                        key[0], key[1],
                        winner.url, loser.url,
                        winner.failed_at, loser.failed_at,
                    )
                    skipped_url_variants.append(loser)
                else:
                    skipped_deduped.append(loser)
        else:
            skipped_deduped.extend(losers)
    return planned, skipped_deduped, skipped_url_variants, null_identity_count
