# Sprint 8.5 — `shokz retry` (re-process `failures.jsonl`)

**Date:** 2026-04-27 (planned)
**Tag target:** `v1.0.1`
**Effort:** ~½ day

## Sprint Goal

A swimmer whose Friday-evening playlist run ended with 3 RateLimited
failures and 1 NetworkError can run `shokz retry` Saturday morning and
recover the 4 missing tracks without re-downloading the 26 that already
succeeded — and without manually copy-pasting URLs out of
`failures.jsonl`.

## Origin

Carry-over from Sprint 8 split. The pre-Sprint-8 GAN sweep moved
`shokz retry` out of v1.0.0 to keep that release focused on safety
primitives. v1.0.0 shipped a clean retry mechanism *within* a batch
(Sprint 7 RetryPolicy); Sprint 8.5 extends that to *across* batches by
treating `failures.jsonl` as the input feed.

## GAN-fix manifest (baked into this spec)

| Tag | What | Source | Status |
|---|---|---|---|
| **C1** | `iter_failures` `OSError` MUST be wrapped as `ManifestReadError` (already exists in `domain/errors.py`) and surfaced via a named CLI `except` branch with actionable text naming the file path. NOT routed through the `Exception` catch-all. | silent#1 | baked |
| **C2** | Dedup key `(source, track_id)` MUST treat `(None, None)` (resolve-failed rows) as a non-deduplicable sentinel — those rows key on `url` instead so that 4 separate resolve-failures don't collapse to 1. | silent#2 | baked |
| **C3** | When two rows share `(source, track_id)` but differ in `url`, the use case MUST log a WARNING naming both URLs and recording the loser into `RetryFailedResult.skipped_url_variants`. Newest-`failed_at` wins is the rule; the warning surfaces the conflict so users can choose differently with `--all` or manual re-invocation. | silent#3 | baked |
| **C4** | `iter_failures` read MUST occur INSIDE the lock context (after lock acquisition). The "no failures → exit cleanly without lock" short-circuit applies ONLY when the file does not exist at command-entry stat-check; once the file exists we acquire the lock before reading to prevent race-with-concurrent-writer silent drops. | silent#4 + silent#7 | baked |
| **C5** | `since=None` semantics: when the resolved scope spans more than 7 days OR > 50 candidate rows, emit a WARNING to stderr ("retrying N failures going back to {oldest_failed_at}; pass --since to limit scope") and require `--all` or explicit `--since` to bypass. Prevents first-run unbounded blast radius. | silent#5 | baked |
| **C6** | `--since` parser MUST emit a TIMEZONE-AWARE UTC `datetime` (`tzinfo=UTC`). `failed_at` MUST be parsed via `datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)` (NOT `fromisoformat` — Python 3.11 `fromisoformat` accepts `Z` but the explicit format makes the contract self-documenting and version-stable). Comparison is `failed_at_dt >= since_dt`. Inclusive-edge case has its own test. | silent#6 | baked |
| **U1** | `_read_jsonl` malformed-row log level MUST be WARNING (currently DEBUG in `iter_all`). Rationale: a partial-write race that silently drops a failure row needs to be visible in default log output, not buried in DEBUG. Sprint 8.5 will lift the log level for both `iter_all` and `iter_failures` (consistency-driven; Sprint 4 deferred this). | silent#7 | baked |
| **U2** | `RetryFailedResult` gains: `skipped_null_identity: tuple[FailureEntry, ...]`, `skipped_url_variants: tuple[FailureEntry, ...]`, `skipped_deduped: tuple[FailureEntry, ...]`. The CLI summary prints non-zero counts so dedup decisions are auditable. | silent#8 | baked |
| **U3** | New Gherkin scenario: SIGINT during the dedup pass (BEFORE any download starts). Asserts exit 130, no batch invoked, lock cleanly released. Forces test coverage for the `asyncio.to_thread` cancellation path. | silent#9 | baked |
| **U4** | Document explicitly under Out of scope: `shokz retry` respects skip-existing and is NOT a "force re-encode at new bitrate" tool. README + `--help` text mention this. No `--force` flag in the retry CLI for v1.0.1. | silent#10 | baked |

## User Story

```
Title: Resume failed downloads from failures.jsonl

As a Swimmer who got rate-limited mid-playlist last night,
I want to run `shokz retry` this morning,
so that the 4 transient failures recover into mp3s without
re-downloading the 26 that already succeeded.

Acceptance Criteria (Gherkin -- written BEFORE code):

  Scenario: retry only retryable error_class by default
    Given failures.jsonl contains 4 rows with error_class:
      - NETWORK_ERROR (track A)
      - RATE_LIMITED (track B)
      - AUTH_REQUIRED (track C)         # terminal
      - FORMAT_UNAVAILABLE (track D)    # terminal
    When `shokz retry` runs (no flags)
    Then BatchDownloadUseCase is invoked with URLs for A and B ONLY
     And C and D are SKIPPED with a stderr message naming each
        ("auth-required tracks are not auto-retried; pass --all to override")

  Scenario: --since filters by failed_at timestamp
    Given failures.jsonl contains:
      - track X: failed_at "2026-04-20T12:00:00Z" (7 days ago)
      - track Y: failed_at "2026-04-26T12:00:00Z" (1 day ago)
    When `shokz retry --since 2d` runs
    Then only Y is retried

  Scenario: deduplication -- keep newest per (source, track_id)
    Given failures.jsonl has 3 rows for track Z (3 retry attempts last
    night, all RATE_LIMITED), at three different timestamps
    When `shokz retry` runs
    Then track Z is queued exactly ONCE
     And the URL passed to BatchDownloadUseCase is the URL from the
        NEWEST row (most-recent failed_at wins)
     And RetryFailedResult.skipped_deduped contains the 2 older entries (U2)

  Scenario: resolve-failed rows with null identity do NOT collapse (C2)
    Given failures.jsonl contains 4 rows ALL with source=null AND track_id=null
        (4 separate resolve-time NETWORK_ERRORs from yt-dlp before any
        Track was constructed), with 4 different urls
    When `shokz retry` runs
    Then ALL 4 urls are queued (no dedup collapse on null-identity)
     And dedup keying for these rows falls back to `url` instead of
        `(source, track_id)`

  Scenario: same (source, track_id) with conflicting urls warns and chooses newest (C3)
    Given failures.jsonl has:
      - track W: source=youtube, track_id=abc, url=https://youtu.be/abc,
                 failed_at "2026-04-26T12:00:00Z"
      - track W: source=youtube, track_id=abc, url=https://m.youtube.com/watch?v=abc,
                 failed_at "2026-04-26T18:00:00Z"
    When `shokz retry` runs
    Then planned tuple has exactly 1 entry for track W
     And the chosen url is the m.youtube.com one (newer failed_at)
     And a WARNING is logged naming BOTH urls + which was kept
     And RetryFailedResult.skipped_url_variants contains the older row

  Scenario: skip-existing short-circuits already-recovered tracks
    Given failures.jsonl row for track P (NETWORK_ERROR yesterday)
      AND manifest.jsonl row for track P (someone manually fixed it
      via `shokz download <url>` since)
    When `shokz retry` runs
    Then P is queued (we don't pre-filter against the manifest)
     AND BatchDownloadUseCase's skip-existing check fires
     AND P returns SkipDecision.SKIPPED with no network call
     AND the summary shows "1 skipped"

  Scenario: --dry-run prints the planned retries without invoking the use case
    Given failures.jsonl with 4 retryable rows
    When `shokz retry --dry-run` runs
    Then stdout lists the 4 URLs + their error_class + their failed_at
     AND BatchDownloadUseCase is NEVER invoked (zero network calls,
         zero ffmpeg invocations)
     AND exit code is 0

  Scenario: --all overrides the terminal-class filter
    Given failures.jsonl contains AUTH_REQUIRED + RATE_LIMITED
    When `shokz retry --all` runs
    Then BOTH are queued
     AND a stderr WARNING flags the AUTH_REQUIRED inclusion
        ("--all bypasses terminal-class filter; auth errors will likely fail again")

  Scenario: --error-class restricts to explicit classes
    Given failures.jsonl with NETWORK_ERROR + RATE_LIMITED + DOWNLOAD_FAILED
    When `shokz retry --error-class RATE_LIMITED` runs
    Then only the RATE_LIMITED row is queued
     AND --error-class can be repeated (`--error-class A --error-class B`)

  Scenario: empty / missing failures.jsonl exits cleanly (C4)
    Given the failures file does not exist (stat-check fails)
    When `shokz retry` runs
    Then stdout prints "no failures to retry"
     AND exit code is 0
     AND no lock is acquired (only when stat-check shows the file is
         absent at command-entry; if the file exists we acquire first,
         read inside the lock, and short-circuit on empty within the lock)

  Scenario: failures.jsonl read failure surfaces as ManifestReadError (C1)
    Given failures.jsonl exists but is unreadable (e.g. EPERM)
    When `shokz retry` runs
    Then it raises ManifestReadError naming the file path
     AND exit code is 1
     AND the message is actionable ("cannot read .shokz/failures.jsonl: ...; check permissions")
     AND it is NOT routed through the "unexpected error" catch-all

  Scenario: --since=None warns when scope is unbounded (C5)
    Given failures.jsonl has 100 retryable rows, oldest 60 days ago
    When `shokz retry` runs (no --since flag, no --all)
    Then a WARNING is printed to stderr naming the count + oldest date
        ("retrying 100 failures going back to 2026-02-26;
          pass --since to limit scope")
     AND the run proceeds (warning, not error)
     AND the run STILL respects RETRYABLE_CLASSES (warning is about scope, not class)

  Scenario: --since accepts ISO-8601 and relative; both produce UTC-aware datetimes (C6)
    Given failures.jsonl row at failed_at exactly "2026-04-26T12:00:00Z"
    When `shokz retry --since 2026-04-26T12:00:00Z` runs
    Then the row IS queued (inclusive >= boundary)
    When `shokz retry --since 2026-04-26T12:00:01Z` runs
    Then the row is NOT queued
    When `shokz retry --since 1d` runs (relative, parsed as now()-1d UTC-aware)
    Then comparison succeeds without TypeError (no naive/aware mismatch)

  Scenario: lock contention surfaces correctly
    Given another shokz process holds the output_dir lock
    When `shokz retry` runs
    Then it raises AnotherRunInProgress with the actionable message
     AND exit code is 1
     AND failures.jsonl is NOT mutated (we never mutate it; this is just
         a safety affirmation against future regressions)

  Scenario: SIGINT mid-retry drains via the same shielded path as `download`
    Given a long retry batch in flight
    When the user sends SIGINT
    Then run_async_with_sigint cancels the main task
     AND asyncio.shield drains in-flight manifest writes
     AND exit code is 130
     AND any newly-recovered tracks have manifest rows that survived

  Scenario: SIGINT during dedup pass (BEFORE batch starts) (U3)
    Given a large failures.jsonl (~100 rows) in the asyncio.to_thread
        read phase
    When the user sends SIGINT during iter_failures
    Then exit code is 130
     AND BatchDownloadUseCase was NEVER invoked
     AND the lock was acquired then cleanly released by `with output_lock:` __exit__
     AND no manifest rows were written

Non-functional:
  - failures.jsonl is read-only from this command (append-only design
    preserved; no "mark resolved" mutation)
  - Idempotent: running `shokz retry` twice with no state change yields
    the same plan (deterministic dedup ordering)
  - DiskGuardPolicy pre-flight runs (filesize_approx feed via the
    existing Sprint 8b path)
  - First-DiskFull-aborts-rest applies (Sprint 8b circuit)

Out of scope (defends against creep):
  - Mutating failures.jsonl (no "delete on success", no "mark as resolved",
    no "rotate"). The manifest is the source of truth for "what we have";
    failures.jsonl is just an audit log of what failed when.
  - New error classes (use the Sprint 7 §7.1 set as-is)
  - Web UI / TUI
  - Bulk retry across multiple output_dirs (one --output per invocation)
  - Resume from a partial download (already handled by yt-dlp + Sprint 4
    adapters)
  - `--retries N` knob (RetryPolicy already governs per-attempt retries
    via config.retry; no new CLI surface for it)
  - `--force` flag in `shokz retry` (U4): retry respects skip-existing.
    A user who changed `audio.preset` and wants to re-encode previously-
    failed tracks at the new bitrate must use `shokz download <url>
    --force`, not `shokz retry`. Documented in README + `--help` text.

INVEST: Independent (no other sprint blocks it), Negotiable (--since /
--all / --error-class are Negotiable knobs; the core dedup+filter loop
is the load-bearing slice), Valuable (saves a swimmer 5min of
copy-pasting URLs), Estimable (½ day; new use case + CLI + ManifestPort
extension), Small, Testable
```

## Architecture

### New use case

`src/shokz/application/use_cases/retry_failed.py`:

```python
@dataclass(frozen=True, slots=True)
class RetryFailedInput:
    output_dir: Path
    spec: AudioSpec
    concurrency: int = 1
    keep_raw: bool = False
    since: datetime | None = None       # filter failed_at >= since
    error_classes: frozenset[str] | None = None  # explicit allow-list
    include_terminal: bool = False      # --all
    dry_run: bool = False

@dataclass(frozen=True, slots=True)
class RetryFailedResult:
    planned: tuple[FailureEntry, ...]   # what was selected
    skipped_terminal: tuple[FailureEntry, ...]
    skipped_old: tuple[FailureEntry, ...]
    # GAN U2: dedup losers + null-identity surfacing for auditability.
    skipped_deduped: tuple[FailureEntry, ...]            # older rows that lost newest-wins
    skipped_url_variants: tuple[FailureEntry, ...]       # same (source,track_id), conflicting urls (C3)
    # Phase B GAN F7: counts NULL-IDENTITY ENTRIES THAT ENDED UP IN
    # `planned` (not raw input rows) so the CLI summary doesn't
    # overstate. int (count), not tuple (entries), per the GAN amendment.
    null_identity_count: int                             # resolve-failed rows kept via url-only key (C2)
    batch_result: BatchDownloadResult | None  # None when dry_run

class RetryFailedUseCase:
    def __init__(self, manifest: ManifestPort, batch_download: BatchDownloadUseCase) -> None: ...
    async def execute(self, inp: RetryFailedInput) -> RetryFailedResult: ...
```

Steps inside `execute()`:

1. Iterate `failures` via `async for entry in self._manifest.iter_failures():`
   (NEW port method; returns `AsyncIterator[FailureEntry]` mirroring
   `iter_all`. Spec note: this is an async-generator factory — call it,
   then `async for`. Do NOT `await` it directly; that raises
   `TypeError: object async_generator can't be used in 'await' expression`.)
2. Filter by `error_class in RETRYABLE_CLASSES` (constant, ordered);
   override with `include_terminal` or explicit `error_classes`
3. Filter by `failed_at >= since` (parsed once)
4. Dedupe by `(source, track_id)` — keep the newest by `failed_at`
5. If `dry_run`: return early with planned + skipped (no batch invoke)
6. Build `BatchDownloadInput(urls=tuple(p.url for p in planned), ...)`
7. Delegate to `self._batch_download.execute(...)` (skip-existing
   handles recovered tracks; lock + SIGINT shield handled by CLI layer)

`RETRYABLE_CLASSES`:

```python
RETRYABLE_CLASSES: Final[frozenset[str]] = frozenset({
    "NETWORK_ERROR",
    "RATE_LIMITED",
    "SOURCE_FILE_CORRUPT",
    "DOWNLOAD_FAILED",       # Sprint 7 catch-all; user might find it transient
    # NOT retried (terminal):
    # AUTH_REQUIRED, FORMAT_UNAVAILABLE, SOURCE_UNAVAILABLE,
    # NAME_OUTSIDE_OUTPUT_DIR, NAME_INVALID, NAME_AMBIGUOUS,
    # ENCODING_FAILED (post-encode failure; usually source-side broken),
    # MANIFEST_INCONSISTENT (recoverable via library verify),
    # DISK_FULL (free-up-disk problem, not retry problem),
    # UNEXPECTED_ERROR (adapter bug; needs investigation, not retry).
})
```

### Port extension

`src/shokz/application/ports/outbound/manifest.py`:

```python
class ManifestPort(Protocol):
    # ... existing methods
    async def iter_failures(self) -> AsyncIterator[FailureEntry]: ...
```

`JsonlManifest.iter_failures()`: same `_read_jsonl` + `FailureEntry(**row)`
pattern as `iter_all`.

### CLI

`src/shokz/adapters/inbound/cli/commands/retry.py`:

```python
@app.command()
def retry_command(
    output: Path | None = typer.Option(None, "--output", "-o"),
    since: str | None = typer.Option(None, "--since",
        help="Only retry failures since this point. Accepts ISO-8601 "
             "(2026-04-26) or relative (2d, 12h, 1w)."),
    error_class: list[str] = typer.Option([], "--error-class", "-e",
        help="Explicit error class to retry (repeatable). "
             "Default: retryable transient classes."),
    all_classes: bool = typer.Option(False, "--all",
        help="Retry every failed row, including terminal classes."),
    dry_run: bool = typer.Option(False, "--dry-run"),
    concurrency: int | None = typer.Option(None, "-c", min=1, max=4),
    log_level: str | None = typer.Option(None, "--log-level"),
) -> None: ...
```

CLI wiring uses the same `_runtime.build_output_lock` +
`run_async_with_sigint` helpers as `download` and `playlist`. The lock
is acquired BEFORE `iter_failures` whenever `failures.jsonl` exists
(spec C4) -- this includes the `--dry-run` path so the dry-run sees a
consistent snapshot under the single-writer guarantee. The only path
that bypasses the lock is the stat-says-file-absent short-circuit:
nothing to read, nothing to serialize.

`--since` parser accepts:
- ISO-8601 date or datetime
- relative: `\d+(s|m|h|d|w)` → timedelta

### Composition

`src/shokz/composition.py` — add `retry_failed` to `Container`,
constructed from `manifest` + the same `batch_download`.

### Files modified / created

| File | Operation |
|---|---|
| `src/shokz/application/use_cases/retry_failed.py` | NEW |
| `src/shokz/application/ports/outbound/manifest.py` | Extend Protocol with `iter_failures` |
| `src/shokz/adapters/outbound/jsonl_manifest.py` | Implement `iter_failures` (mirror of `iter_all`) |
| `src/shokz/adapters/inbound/cli/commands/retry.py` | NEW |
| `src/shokz/adapters/inbound/cli/app.py` | Register `retry` subcommand |
| `src/shokz/composition.py` | Wire `RetryFailedUseCase` |
| `tests/unit/application/test_retry_failed.py` | NEW (10+ tests covering scenarios above) |
| `tests/acceptance/test_sprint_8_5_retry.py` | NEW (end-to-end via fakes; matches Sprint 7's pattern) |
| `CHANGELOG.md` | `[1.0.1]` section |
| `RETRO.md` | Sprint 8.5 entry |
| `README.md` | New `shokz retry` section |

## Definition of Ready (DoR)

- [x] Sprint Goal written (one paragraph, swimmer-facing)
- [x] User Story with 14 Gherkin scenarios (9 original + 5 added by spec GAN: null-identity, url-variant, ManifestReadError path, scope-warn, --since tz edge cases + SIGINT-during-dedup) (will be the test names)
- [x] Affected files listed (12 files; 4 NEW)
- [x] Ports/contracts named: `ManifestPort.iter_failures` is the ONLY
      new port surface; everything else reuses Sprint 8b primitives
- [x] Test approach: unit (use case with fake ManifestPort), acceptance
      (via fakes mirroring Sprint 8b style), no new INTEGRATION test
      (`shokz retry` exercises the same network/encoder paths as
      `shokz download`, already covered by Sprint 1+ INTEGRATION tests)
- [x] Dependencies: v1.0.0 merged + tagged green; FailureEntry stable
      since Sprint 4; failures.jsonl path resolved via Sprint 3 config
- [x] Out-of-scope list written (8 items above)
- [x] Estimated ≤ ½ day (use case ~120 LoC, CLI ~70 LoC, tests ~360 LoC; revised up from initial estimate after spec GAN added 5 scenarios)

## Definition of Done (DoD)

- [ ] All 14 Gherkin scenarios pass as executable pytest tests
- [ ] `ruff check src tests` clean
- [ ] `mypy src` clean
- [ ] Full suite green (target ≥ 247 tests = 237 + 10 new); coverage ≥ 80%
      on touched files
- [ ] Atomic-write + manifest-fsync UNCHANGED (we don't write to either
      file from this command -- only read failures.jsonl and delegate to
      BatchDownloadUseCase, which already preserves the v1.0.0 ratchet)
- [ ] Reconciliation scan UNCHANGED
- [ ] Error-translation table UNCHANGED (we filter on existing classes)
- [ ] `CHANGELOG.md` `[1.0.1]` section updated
- [ ] `README.md` includes `shokz retry` documentation
- [ ] Conventional commit: `feat(retry): retry failed downloads from
      failures.jsonl (Sprint 8.5, v1.0.1)`
- [ ] Self-demo: pretend-fail a download (kill ffmpeg via PATH unset),
      verify failures.jsonl row, run `shokz retry`, watch the recovery
- [ ] Git tag `v1.0.1` pushed
- [ ] Retro entry appended to `RETRO.md`

## Risks (call out before code)

| Risk | Likelihood | Mitigation |
|---|---|---|
| `failures.jsonl` row doesn't carry the URL (only source+track_id) and we can't reconstruct it | LOW | Verify in Phase A: `FailureEntry.url` exists per Sprint 4 schema. If absent, escalate to spec amendment before code. |
| Two `shokz retry` invocations against the same output_dir double-retry the same URLs | LOW | Lock + skip-existing handle this. The lock serializes; the second invocation's BatchDownloadUseCase will skip rows the first one already recovered. |
| `--since` parser ambiguity (is `2d` "last 2 days" or "2026-02-01"?) | MEDIUM | Strict regex match for `\d+[smhdw]` BEFORE attempting ISO-8601 parse. Reject ambiguous inputs with clear error message. |
| User expects `shokz retry` to also delete recovered rows from failures.jsonl | LOW (UX) | README + `--help` text explicitly state "failures.jsonl is append-only audit log; manifest is source of truth". |
| `iter_failures` linear scan over a year-old failures file becomes slow | LOW | Same scan complexity as `iter_all` (Sprint 4.5 already accepted ~50ms for ~1000 entries). v2 territory if it bites. |

## Phase plan

| Phase | Work | GAN gate |
|---|---|---|
| A | `ManifestPort.iter_failures` + JsonlManifest impl with **WARNING-level malformed-row log (U1)** + **OSError → ManifestReadError wrapping (C1)** + 3 unit tests (happy, OSError → ManifestReadError, malformed row warns) | After A: silent-failure-hunter on the read path |
| B | `RetryFailedUseCase` + filter/dedup with **null-identity url-only key (C2)**, **url-variant warn (C3)**, **timezone-aware UTC datetime parser (C6)**, **scope-warn at >7d/>50 rows (C5)** + 9 unit tests covering Gherkin scenarios 1, 2, 3, 4 (dedup + null-identity + variants), 5 (--since edge cases), 7 (--all), 8 (--error-class), and the empty-file path | After B: python-reviewer on use case (focus: dedup correctness, datetime tz, scope-warn) |
| C | CLI command + composition wiring + `--since` parser (regex `\d+[smhdw]` first, then ISO-8601 strict format) + **lock-acquired-before-iter_failures-when-file-exists ordering (C4)** + 3 acceptance tests covering scenarios 9 (lock contention), 10 (SIGINT mid-batch), 11 (SIGINT during dedup, U3) | After C: architect on CLI ergonomics + lock ordering + signal handling |
| D | Self-demo (kill ffmpeg → trigger ENCODING_FAILED row → run `shokz retry --error-class ENCODING_FAILED --all` → observe terminal-class warning then real failure path) + CHANGELOG `[1.0.1]` + README `## shokz retry` section explicitly stating "respects skip-existing; not a force-reencode tool" (U4) + retro + commit + tag v1.0.1 | Final code-review GAN |
