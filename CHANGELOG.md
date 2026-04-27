# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] -- 2026-04-27

### Added -- Sprint 8b: wire the v0.9.0 primitives end-to-end

v0.9.0 shipped FileLockPolicy + DiskGuardPolicy + 4 domain errors as
dormant library code. Sprint 8b plugs them into the use case + CLI and
adds the three ENOSPC translation sites that v0.9.0 deferred.

- `BatchDownloadUseCase`:
  - Optional `disk_guard: DiskGuardPolicy | None` constructor arg.
    When set, runs ONE pre-flight per `execute()` after resolving all
    track metadata in parallel. Pre-flight failure raises `DiskFull`
    BEFORE any download starts.
  - `BatchDownloadResult.disk_full_count: int` field surfacing the
    number of tracks affected by ENOSPC.
  - First-DiskFull-aborts-rest circuit breaker: any track raising
    `DiskFull` flips a per-batch flag; subsequent `_process_one`
    calls short-circuit with `error="aborted by prior DiskFull"`.
    Caveat at concurrency>1: multiple in-flight tracks may
    independently hit ENOSPC before the flag flips; the summary
    distinguishes "triggered" vs "short-circuited".
  - `asyncio.shield + drain` around `manifest.record` (Sprint 8 GAN
    B1): SIGINT during the post-`os.replace`/pre-manifest-row window
    no longer orphans the mp3. The cancel-driver awaits the shielded
    manifest task before propagating `CancelledError`.
  - `_process_one` finally-block opportunistically cleans up
    `tmp_dir/<glob.escape(track.id)>.*` on failure paths so a CLI
    retry sees no stale corrupt source. `keep_raw=True` respected.
- 3 ENOSPC translation sites:
  - `ffmpeg_encoder.py`: stderr-text `"no space left"` /
    `"enospc"` (case-insensitive) → `DiskFull`. Cleans up
    `dest.partial` before raising.
  - `local_filesystem.py`: `OSError(errno.ENOSPC)` on `os.replace`
    → `DiskFull` chained from the OSError.
  - `jsonl_manifest.py`: `OSError(errno.ENOSPC)` on `os.write` →
    `ManifestInconsistent` (the recoverable signal class)
    `from DiskFull` (the underlying cause). Reconciliation
    catches the resulting orphan mp3 on next startup.
- `Track.filesize_approx: int | None` -- yt-dlp's pre-download size
  estimate. Populated from `info["filesize_approx"]` falling back to
  `info["filesize"]`. Feeds `DiskGuardPolicy.check_batch`.
- CLI cross-process advisory lock + signal handling
  (`adapters/inbound/cli/_runtime.py`):
  - `build_output_lock(config)` constructs a FileLockPolicy on
    `<output_dir>/.shokz/locks/shokz.lock` with timeout from
    `[lock] timeout_s`.
  - `run_async_with_sigint(coro)` runs the coroutine under
    `asyncio.run` with a SIGINT handler that cancels the main task
    on first Ctrl+C and restores `SIG_DFL` on second Ctrl+C.
    Converts the asyncio `CancelledError` back to `KeyboardInterrupt`
    (Phase D GAN HIGH#1) so the CLI's exit-130 branch fires.
  - `download` and `playlist` commands now wrap their `asyncio.run`
    in `with build_output_lock(config):` plus
    `run_async_with_sigint(...)`. Lock contention surfaces as
    `AnotherRunInProgress` / `StaleLock` / `LockOwnerUnknown`
    with actionable messages.
- `composition.py` wires `DiskGuardPolicy(safety_multiplier,
  require_estimate)` into `BatchDownloadUseCase`.
- 7 NEW unit tests in `tests/unit/adapters/test_enospc_translations.py`
  covering all 3 translation sites with monkey-patched OSError /
  stderr text.
- 4 NEW acceptance tests in
  `tests/acceptance/test_sprint_8b_lock_signal_disk.py` covering
  pre-flight blocking, mid-batch DiskFull cascade, raw-tmp cleanup
  on failure, and lock contention.

### Changed

- `BatchDownloadResult` -- added `disk_full_count: int = 0` field.
- CLI `_summary.py` -- new line surfacing DiskFull triggered/aborted
  split when `disk_full_count > 0`.

## [0.9.0] -- 2026-04-27

### Added -- Sprint 8a: Safety primitives (dormant library code)

> **NOT YET v1.0.** v0.9.0 ships the safety primitives as DORMANT library
> code. Wiring + SIGINT + ENOSPC translation lands in Sprint 8b → v1.0.0.
> Behavior of `shokz download` is identical to v0.8.0; the new primitives
> are imported and reviewed but not yet plugged into the use case.

- 4 NEW domain errors in `domain/errors.py`:
  - `AnotherRunInProgress` -- another shokz process holds the lock
  - `StaleLock` (with optional `raw_meta_bytes` for corruption diagnosis)
  - `LockOwnerUnknown` -- alive PID but owned by another user
  - `DiskFull` (with optional `need_bytes` / `have_bytes` for structured inspection)
- NEW `application/policies/file_lock.py` -- `FileLockPolicy` wrapping
  `filelock.FileLock` with sibling `.shokz.lock.meta` JSON atomic-written
  via `os.replace`. Five-step classification on contention:
  1. corrupt JSON meta → `StaleLock` (carries raw bytes)
  2. dead PID per `psutil.NoSuchProcess` → `StaleLock`
  3. PermissionError on `os.kill(pid, 0)` → `LockOwnerUnknown`
  4. PID alive but `create_time` mismatch → `StaleLock` (PID reused)
  5. PID alive AND start_time matches → `AnotherRunInProgress`
  GAN-fixed: TOCTOU-after-Timeout retry, release-before-unlink ordering,
  meta-write-failure releases flock, RuntimeError on impossible own-PID.
- NEW `application/policies/disk_guard.py` -- `DiskGuardPolicy` with
  batch-level `check_batch(output_dir, estimates)`. Sums non-None entries
  * `safety_multiplier`, compares to `shutil.disk_usage(output_dir).free`.
  `humanfriendly.format_size(..., binary=True)` for IEC units ("GiB" not "GB").
  `require_estimate=True` rejects None entries with helpful message.
- 2 NEW config sections in `config/schema.py`:
  - `[disk] safety_multiplier: float = 2.0 (1.0..10.0)`,
    `require_estimate: bool = False`
  - `[lock] timeout_s: float = 5.0 (0.0..60.0)`
- 22 NEW unit tests across `tests/unit/{domain,application}/`:
  - `test_errors.py` extended with 4 new classes + structured attribute tests
  - `test_schema.py` extended with 6 new bound + default tests
  - `test_file_lock_policy.py` (NEW) -- 11 tests covering all 5 classification steps + happy-path + GAN-fix regressions
  - `test_disk_guard_policy.py` (NEW) -- 7 tests covering batch math + None-handling + binary format
- NEW dependency: `psutil>=5.9,<8` (for `Process(pid).create_time()` start-time check)

### Why this is v0.9.0 not v1.0.0 (Sprint 8 split)

Original Sprint 8 spec was v1.0.0 with cross-process lock + SIGINT + disk
guard + 3 ENOSPC translation sites + `shokz retry`. The pre-code GAN
sweep already split off `shokz retry` to Sprint 8.5. Mid-Phase-3 the
implementation reached a state where ffmpeg ENOSPC translation was in
place but local_filesystem and jsonl_manifest were not -- a strict
regression vs v0.8.0 on the disk-full path. The Phase 3 GAN review
(Option C verdict) recommended:

1. Tag v0.9.0 with the GREEN primitives + reverted ffmpeg ENOSPC (matches
   v0.8.0 disk-full behavior; no regression).
2. Open a focused Sprint 8b for wiring + SIGINT + 3 ENOSPC sites + tag v1.0.0.

This preserves the "every tag = green DoD" ratchet that has held across
all 9 prior tags. The safety primitives are reviewed and locked; Sprint
8b is just wiring + the SIGINT/asyncio.shield concurrency surface.

### Process / Retro highlights

- 7 GAN reviews fired across the spec + 4 implementation phases
  (sprint-spec dual reviewer + phase-1 + phase-2 + phase-3 + Option-C process review)
- 14 review-driven fixes baked into the spec BEFORE coding (6 BLOCK + 5 MED + 4 LOW)
- 8 review-driven code fixes during phases (Phase 1: 4, Phase 2: 4, Phase 3 partial: 0)
- Mid-sprint scope split kept the project's "every tag = green" ratchet
  intact on the highest-stakes release. The "first WIP commit ever on
  main" precedent was avoided.

## [0.8.0] -- 2026-04-27

### Added -- Sprint 7: Classified retry + error translation

- **Classified retry policy**: `application/policies/retry.py`. Per-error-class
  attempt budgets and backoff sequences:
  - `RateLimited` (HTTP 429): 3 retries, exponential 5s/30s/120s.
  - `NetworkError` (HTTP 5xx, conn reset): 2 retries, 1s linear.
  - `SourceFileCorrupt`: 1 retry with `.tmp` cleanup before re-attempt.
  - `DownloadFailed` (default fallback): 1 retry, 1s linear.
  - Terminal classes (`AuthRequired`, `FormatUnavailable`,
    `SourceUnavailable`, `EncodingFailed`, etc.) get 0 retries.
- **§7.1 error translation table**: `_classify_message()` in
  `adapters/outbound/ytdlp_source.py` maps yt-dlp error messages to domain
  errors via a precedence-ordered substring table (terminal-first; auth
  beats rate-limit beats network when a message contains both). Applied at
  THREE call sites: `resolve()`, `resolve_playlist()`, AND the
  `download_audio()` subprocess-stderr path -- so classification fires for
  ALL yt-dlp failures, not just metadata extraction.
- **Resolve-phase retry**: `source.resolve(url)` now retries with the same
  policy as `source.download_audio()`. A 429 on metadata extract is no
  longer an immediate failure.
- **Per-batch circuit breaker**: 3 consecutive `RateLimited` outcomes trip
  the breaker; remaining tracks in the batch run with retries=0 to avoid
  pathological 60-track-times-3-attempts sleep storms. Counter resets on
  any track that succeeds.
- **Cleanup hook**: `SourceFileCorrupt` retry deletes `tmp_dir/{track.id}.*`
  before the next attempt so yt-dlp can't resume against a corrupt partial
  and produce a merged-corrupt MP3 the size check would silently pass.
- 4 NEW domain error classes in `domain/errors.py`: `AuthRequired`,
  `FormatUnavailable`, `RateLimited` (with optional `retry_after_seconds`
  hint), `NetworkError`.
- `BatchDownloadResult.unclassified_yt_dlp_errors: int = 0` counter
  surfaces §7.1 drift to the user (CLI summary line shows "N unclassified
  yt-dlp error(s) -- please report to extend §7.1").
- `BatchDownloadResult.rate_limit_circuit_tripped: bool = False` so the
  CLI can explain why the rest of a batch ran without retry.
- New `RetrySection` config (`[retry]` in `shokz.toml`) with bounded knobs:
  `max_attempts_rate_limited`, `max_attempts_network`, `max_attempts_corrupt`
  (all `ge=0, le=5`), `backoff_base_s` (`ge=0.1, le=60.0`),
  `wall_clock_budget_s` (`ge=1.0, le=600.0`). All `validate_default=True`.
- `_ERROR_CLASS_MAP` migrated from `dict[str, str]` keyed by `__name__` to
  `tuple[tuple[type, str], ...]` matched by `isinstance` (subclass-safe).
- Shared CLI summary printer (`adapters/inbound/cli/_summary.py`) used by
  both `download` and `playlist` commands -- previously inline + drifting.

### Changed

- `BatchDownloadUseCase.__init__` accepts a NEW keyword-only
  `retry_policy: RetryPolicy | None = None`. Default None preserves
  no-retry behavior for tests / library callers; composition root passes
  a real policy built from `config.retry`.
- `download_audio()`'s subprocess-stderr handler now classifies the FULL
  stderr blob (not just the last line) so an actionable error on line N-1
  isn't masked by a generic advisory on line N.

### Process / Retro

Sprint 7 was scoped tightly to RETRY + CLASSIFICATION (master plan §8 also
listed bitrate cap, --dry-run, and a manifest schema collapse — those
deferred to Sprint 7.5 / Sprint 6b backlog / rejected respectively). The
spec carried 14 GAN fixes (6 convergent C1-C6, 8 unique U1-U8) baked in
BEFORE coding began. Each of the 6 implementation phases ended with a
dedicated GAN review; 18 review-driven fixes landed across the sprint.

## [0.7.0] -- 2026-04-27

### Changed -- Sprint 6: Sequential by default

- **BREAKING (default)**: `general.concurrency` default lowered from `3` to `1`.
  New invocations of `shokz download` or `shokz playlist` now process URLs
  strictly sequentially unless `--concurrency` is passed. Existing user
  configs with explicit `concurrency = N` continue to work as long as N <= 4.
- **BREAKING (validation)**: `general.concurrency` cap lowered from `16` to `4`
  (TOML configs and `--concurrency` CLI flag both enforce the new cap).
  Reason: the in-process pool is the ONLY safe parallelism mechanism today;
  multi-process invocations against the same `--output` are NOT safe due to
  manifest JSONL atomicity beyond PIPE_BUF, filename-resolver TOCTOU, and
  `.tmp` raw-file clobber. Sprint 8 will land cross-process filelock and
  may restore a higher cap then.
- CLI `--concurrency` help text updated on both `download` and `playlist`
  commands to document the new default + the multi-process safety caveat.

### Fixed -- Sprint 5 F1 follow-up

- `playlist.py`: dropped the leftover second `extract_info` round-trip and
  bare-except fallback inside `playlist_command` -- `PlaylistInfo.title` is
  already populated by `ExpandPlaylistUseCase` via a single `extract_flat`
  call. Eliminates a TOCTOU + silent-error-mask the Sprint 5 review (F1)
  flagged but never landed in code.

### Process / Retro

- Original Sprint 6 was scoped as "Rich progress + ID3 tagging + cookie
  guard" and split into 6a/6b after a GAN review found 5 convergent HIGH
  issues (cross-thread Rich/asyncio, runtime_checkable Protocol extension,
  scope creep). Sprint 6a was started, then the user re-scoped to "CLI only,
  no live progress UI, no in-process concurrency". A second GAN review on
  "drop concurrency entirely + recommend shell parallelism" found THREE
  HIGH correctness bugs (manifest JSONL corruption, filename TOCTOU, .tmp
  clobber) that would ship if multi-process was recommended. Final Sprint 6
  shipped a much smaller scope: default concurrency = 1, keep the flag (cap
  lowered) as escape hatch for long playlists, defer safe shell parallelism
  to Sprint 8 (filelock).

## [0.6.0] -- 2026-04-27

### Added -- Sprint 5: Source resolution + playlists
- `domain/models.py`: `PlaylistInfo` dataclass (title + item_urls).
- `application/ports/outbound/video_source.py`: `VideoSourcePort.resolve_playlist`
  returns `PlaylistInfo | None`.
- `adapters/outbound/ytdlp_source.py`: implements `resolve_playlist` via
  `yt_dlp.YoutubeDL(extract_flat=True)`. F3 fix: playlist-shaped URLs (`list=`)
  with no `_type` raise `DownloadFailed` instead of silently returning None
  (catches yt-dlp throttle / partial-response failures).
- `application/use_cases/expand_playlist.py`: `ExpandPlaylistUseCase`
  returns `PlaylistInfo` (title + URLs in one call -- F1 fix eliminates
  the CLI's prior double-extract-info round-trip).
- `application/use_cases/batch_download.py`:
  - `BatchDownloadInput.target_dir: Path | None` (where files land; manifest
    paths still relative to `output_dir`).
  - F2 fix: upfront guard rejects `target_dir` outside `output_dir` or
    symlinked, BEFORE any download work happens.
- `application/policies/reconciliation.py`: walks subdirectories via
  `rglob("*.mp3")`, excluding `.tmp/` and `.shokz/` -- the Sprint 4.5 retro
  DoD ratchet item.
- `adapters/inbound/cli/commands/playlist.py`: `shokz playlist URL` command.
  Flags: `--playlist-subdir/--no-playlist-subdir` (default subdir=true),
  `--yes`, `--confirm-threshold`, plus the usual `-o/-c/--keep-raw/--force/
  --log-level`.
- `config/schema.py`: `[sources.youtube] playlist_confirm_threshold: int = 50`.

### Code-review audit (Sprint 5 review)
silent-failure-hunter found 6 issues. 3 HIGH + 1 Med fixed; 2 deferred:

**Fixed:**
- **HIGH F1**: CLI no longer does a 2nd network call for the title -- threaded
  through `PlaylistInfo` from the use case. Previously, a bare `except` on the
  2nd extract silently fell back to literal `"playlist"` directory name.
- **HIGH F2**: upfront `target_dir` guard in `BatchDownloadUseCase.execute`
  prevents N late `ManifestInconsistent` failures (also rejects symlink).
- **HIGH F3**: yt-dlp dict-without-`_type` on playlist-shaped URLs raises
  `DownloadFailed` (was silently treated as "not a playlist").
- **Med F5**: acceptance tests skip on retired-playlist-URL detection
  (`SourceUnavailable` -> `pytest.skip`) instead of failing as if a regression.

**Deferred with reason:**
- Med F4 (reconciliation excluded_dirs hardcoded): config knob deferred to
  Sprint 9 doctor sweep. Current excludes (`.tmp/`, `.shokz/`) cover the
  shokz-managed state; user-created hidden dirs surfacing as orphans is
  acceptable observability noise for v1.
- Low F6 (off-by-one `>=` threshold semantics): documented as `>=` in the
  CLI help; user-visible message says "items >= threshold" so the boundary
  is honest. Cosmetic.

### Verified
- ruff check + format: clean
- mypy --strict: clean (46 source files)
- pytest unit: 90 passed (was 94 pre-Sprint-5 -- changes to PlaylistInfo
  return shape required adjusting some tests; Sprint 5 net-added 4 tests),
  90% cov
- INTEGRATION=1 acceptance (excluding @slow): 34 passed in ~100s
  (Sprint 1: 4 + 2: 7 + 3: 10 + 4: 2 + 4.5: 9 + 5: 2)
- @slow Sprint 5 tests: 5 (full playlist downloads -- run on demand with
  `-m slow`)
- just sprint-review 5: 11/11 covered
- just kill-test: still PASS (Sprint 4 ratchet held)

### Sprint 5 deliberate scope (deferred per spec)
- Cross-source playlists (e.g. SoundCloud)              -> when source added
- Cookie-gated playlists (members-only)                 -> later
- Retry on partial playlist failures                    -> Sprint 7
- Playlist as a single ManifestEntry "album"            -> v2 if requested
- VCR/cassette for acceptance tests                     -> v2 (now skip on
                                                           retired URL)

## [0.5.0] -- 2026-04-27

### Added -- Sprint 4.5: Skip-existing + reconciliation + library list/show/verify
This is the recovery story for Sprint 4's SF-4 orphan-state window.

- `application/policies/`:
  - `skip_existing.py`: `SkipExistingPolicy` -- requires BOTH manifest entry
    AND file on disk before returning `SKIPPED`. Manifest-stale and
    disk-deleted both correctly trigger re-download.
  - `reconciliation.py`: `ReconciliationPolicy.scan()` returns ok pairs +
    orphan files (on disk, not in manifest) + orphan entries (in manifest,
    not on disk).
- `application/ports/outbound/manifest.py`: extended ManifestPort with
  `find_by_track(source, track_id)` and `iter_all()` (read API).
- `adapters/outbound/jsonl_manifest.py`: implemented read API (linear scan
  via `_read_jsonl`); SF-1 fix: counts skipped lines and raises
  `ManifestReadError` if the WHOLE manifest is corrupt.
- `application/use_cases/library_query.py`: `ListLibraryUseCase`,
  `ShowLibraryUseCase`, `VerifyLibraryUseCase`.
- `adapters/inbound/cli/commands/library_cmd.py`: `shokz library list`
  (table), `library show TRACK_ID` (single entry detail with --source),
  `library verify` (reconciliation, exits non-zero on mismatch).
- `BatchDownloadInput.force` field; download command `--force` flag bypasses
  skip-existing.
- `BatchDownloadResult.skipped` count + CLI summary handles SKIPPED status.
- Startup reconciliation scan via `asyncio.create_task` -- if any orphan
  files exist, log a WARNING pointing the user to `shokz library verify`.
- `TrackStatus.SKIPPED` enum value.
- `domain/errors.py`: `ManifestReadError` for total manifest corruption.

### Code-review audit fixes (Sprint 4.5 review, same v0.5.0)
Two parallel reviewers found 10 substantive issues (1 CRITICAL, 5 HIGH).
All addressed before tag:

- **HIGH (INTEGRATION)**: CLI download command treated SKIPPED as FAIL in
  the summary loop. Now distinct OK / SKIP / FAIL rows.
- **HIGH (INTEGRATION)**: Sprint 2 collision test was blocked by Sprint 4.5
  skip-existing -- updated to use `--force`.
- **HIGH py-rev #2**: symlink + name-ambiguity guards moved BEFORE
  `asyncio.create_task` so they cannot leave an orphan task on early-exit.
- **HIGH py-rev #4 / both**: `ShowLibraryUseCase.execute` source parameter
  now required at use-case level (CLI still defaults to "youtube").
- **HIGH (both)**: documented that reconciliation scans only top-level *.mp3
  -- Sprint 5 playlist subdirs require a forward-compat extension.
- **CRITICAL py-rev #1**: investigated -- the reviewer was wrong about the
  Protocol shape; mypy confirms async generators correctly satisfy
  `def iter_all() -> AsyncIterator`. No change needed.
- **MED SF-1**: `_read_jsonl` counts skipped lines + raises
  `ManifestReadError` if all lines are malformed (vs silently empty list).
- **MED py-rev #3**: `ReconciliationReport` tuple defaults inlined as
  `= ()` (immutable; safer than `field(default_factory=tuple)`).
- **MED py-rev #5**: `_read_jsonl` typing consistency tightened.

**Deferred (with reason):**
- SF-2 fire-and-forget task post-execute orphan: internal try/except in
  `_reconcile_warn` already prevents exception escape; storing the task
  reference is documented as cosmetic. Sprint 8's signal handling adds
  proper task lifecycle.
- SF-4 manifest snapshot per-batch: skip-existing currently re-reads per
  track. For typical batch sizes (~50 URLs, ~1000 manifest rows) the
  perf is fine; Sprint 5+ caching deferred.
- iter_all true streaming: documented as full-read; v2 SQLite migration
  will revisit.
- SkipDecision/TrackStatus string-overlap: cosmetic, deferred.
- Reconciliation scan flat *.mp3 only: Sprint 5 DoD ratchet item.

### Verified
- ruff check + format: clean
- mypy --strict: clean (44 source files)
- pytest unit: 94 passed (was 89 in v0.4.0, +5 Sprint 4.5), 91% cov
- INTEGRATION=1 acceptance: 32 passed in ~109s (Sprint 1: 4 + 2: 7 +
  3: 10 + 4: 2 + 4.5: 9)
- just sprint-review 4.5: 12/12 covered
- just kill-test on 7-hour video: PASS (Sprint 4 ratchet still holds)
- Self-demo (clean state):
  - download URL -> *.mp3 + manifest entry (Sprint 4 still works)
  - re-download same URL -> instant SKIP (no encode), exit 0
  - delete the .mp3 manually -> re-download triggers (manifest-only
    skip insufficient, per AC scenario 3)
  - --force re-downloads even with manifest hit -> Foo (2).mp3 produced

### Sprint 4.5 deliberate scope (deferred per spec)
- retry policy                              -> Sprint 7
- Rich progress bars in library list         -> Sprint 6
- cross-process file lock around manifest    -> Sprint 8
- SQLite manifest backend                    -> v2
- shokz library export / import              -> deferred
- manifest schema migration story            -> Sprint 5+ when needed

## [0.4.0] -- 2026-04-27

### Added -- Sprint 4: Manifest + atomic writes + integrity checks
**This is the crash-safe-single-process milestone**, NOT yet v1.0.0
(Sprint 8 = lock + signals + disk guard before v1.0).

- `domain/`: `Track.original_title` (preserved unsanitized for manifest);
  `ManifestEntry` + `FailureEntry` dataclasses (schema_version=1);
  new errors `SourceFileCorrupt`, `ManifestInconsistent`.
- `application/ports/outbound/`: `ManifestPort` + `FileSystemPort` Protocols.
- `adapters/outbound/local_filesystem.py`: `LocalFileSystem` -- atomic_move
  via os.replace + fsync(file fd) + fsync(parent dir fd). Closes the
  kernel-write-buffer race that crash-survives os.replace.
- `adapters/outbound/jsonl_manifest.py`: `JsonlManifest` -- append-only
  JSONL with file fd + grandparent dir fsync chain. Single-process-safe
  via asyncio.Lock; cross-process locking lands Sprint 8.
- `BatchDownloadUseCase`:
  - Pre-encode raw size check (MIN_RAW_BYTES=1024) -- catches yt-dlp 0-byte
    silent failures (silent-failure-hunter F1 from v0.2.0 plan review).
  - Post-encode duration probe (DURATION_TOLERANCE=0.02) -- catches ffmpeg
    truncation (silent-failure-hunter F2 from v0.2.0 review).
  - Atomic move via FileSystemPort, manifest record AFTER move + fsync.
  - Records FailureEntry on any per-track failure path (incl. unexpected).
- `composition.py`: wires JsonlManifest + LocalFileSystem (paths derived
  from `config.general.output_dir / .shokz/`).
- `scripts/kill-test.sh` + `just kill-test <URL>`: SIGKILL mid-encode +
  assert no partial *.mp3 in downloads/. Sprint 4 DoD ratchet from
  Sprint 3 retro.
- 10 unit tests + 2 INTEGRATION acceptance tests (incl. real SIGKILL).

### Code-review audit fixes (Sprint 4 review, same v0.4.0)
Two parallel reviewers (silent-failure-hunter + python-reviewer) found
12 substantive issues. All addressed before tag:

**HIGH:**
- **py-rev Issue 1**: probed duration was computed but discarded -- manifest
  recorded source-claimed duration, not measured. Now records
  measured_duration_s (the actual encoded length).
- **SF-4**: manifest record reordered to BEFORE filesystem.remove(raw),
  so kill between-them leaves recoverable orphan state.
- **SF-2 / py-rev Issue 2**: `if track.duration_s:` truthy check silently
  skipped duration_s=0. Now `is not None`.

**Medium:**
- **SF-1**: documented single-process constraint of asyncio.Lock; Sprint 8
  will add cross-process filelock.
- **SF-5**: removed `try/except ValueError` fallback in `_build_manifest_entry`
  that silently wrote absolute paths; now raises ManifestInconsistent.
- **SF-7**: parent dir mkdir + grandparent fsync moved from per-call to
  __init__ (no race; durable from instantiation).
- **Translation**: stable error_class strings via `_ERROR_CLASS_MAP`
  (SOURCE_FILE_CORRUPT, ENCODING_FAILED, ...) decoupled from Python class
  names so refactors don't break tooling. Unexpected exceptions also
  recorded as failure entries.
- **py-rev Issue 3**: hoisted `from datetime import datetime` to module top
  (was deferred inline in 3 places).
- **py-rev Issue 5**: kill-test acceptance passes `cwd=Path(__file__).parents[2]`
  so the script's $(pwd) resolves correctly regardless of pytest invocation.
- **SF-6**: documented that 2% tolerance validates ffmpeg-vs-yt-dlp consistency,
  not source-vs-reality (yt-dlp can report wrong duration; that's a known limit).

**Test cleanup:**
- `tests/fakes.py`: `FakeManifest`, `FakeFileSystem`, `FakeAudioEncoder.probe_duration_value`,
  `FakeVideoSource.raw_bytes`. Removed noqa: E402 by hoisting imports.

### Verified
- ruff check + format: clean
- mypy --strict: clean (40 source files)
- pytest unit: 89 passed (was 79 in v0.3.0, +10 Sprint 4 tests), 93% cov
- INTEGRATION=1 acceptance: 23 passed in ~68s (Sprint 1: 4 + 2: 7 + 3: 10 + 4: 2)
- just sprint-review 4: 10/10 Sprint 4 scenarios covered
- just kill-test on a 7-hour video: PASS (no partial *.mp3 after SIGKILL)
- Self-demo (clean state): real download produces .mp3 + manifest entry,
  manifest schema verified ({"schema_version": 1, "source": "youtube",
  "track_id": "jNQXAC9IVRw", "original_title": "Me at the zoo", ...,
  "duration_s": 19.0, "downloaded_at": "2026-04-26T16:38:09Z"})

### Sprint 4 deliberate scope (deferred per spec)
- skip_existing logic                 -> Sprint 4.5 (next)
- Reconciliation scan (orphan files)  -> Sprint 4.5
- library list / show / verify        -> Sprint 4.5 / Sprint 9
- Cross-process filelock              -> Sprint 8
- Disk guard pre-check                -> Sprint 8
- Signal handling (CancelledError)    -> Sprint 8
- Retry policy                        -> Sprint 7

## [0.3.0] -- 2026-04-27

### Added -- Sprint 3: Configuration (TOML + env + CLI)
- `src/shokz/config/` package: `schema.py` (Pydantic v2 AppConfig with frozen +
  extra=forbid + populate_by_name), `defaults.py` (DERIVED from AppConfig --
  single source of truth), `presets.py` (preset -> AudioSpec resolver),
  `loader.py` (layered merge + per-key source tracking).
- `shokz config show|init|path` Typer subapp. `show` annotates each value with
  its source layer; `init` writes a commented sample TOML (atomic exclusive
  open closes TOCTOU window); `path` lists loaded + missing files.
- `BatchDownloadUseCase` + composition + CLI `download` command now driven by
  AppConfig. CLI flags use sentinel `None` defaults so unspecified means
  "use the config layer's value". Precedence: built-in < ~/.config/shokz/config.toml
  < ./shokz.toml < env (SHOKZ_*) < CLI.
- `--preset` typed as `AudioPreset` enum (Typer rejects bogus values at parse time).

### Process improvements (from Sprint 2 retro)
- `scripts/code-review.sh` + `just code-review <prev-tag>`: prints a markdown
  brief of the diff vs prev-tag for the human to dispatch reviewers from
  Claude. Code review is now a non-skippable pre-tag DoD ratchet item.

### Code-review audit fixes (Sprint 3 review, same v0.3.0)
Two parallel reviewers (silent-failure-hunter + python-reviewer) found
9 substantive findings (3 HIGH). All addressed before tag:

- **C1 [HIGH]** `_unflatten` collision detection: scalar/dict conflict now
  raises ConfigLoadError instead of silently dropping data.
- **C2 [HIGH]** `populate_by_name=True` on AppConfig: model_dump() output
  round-trips cleanly through model_validate (was broken on `logging_` alias).
- **C3 [HIGH]** `config init` uses atomic `"x"` open mode: TOCTOU race window
  between `exists()` and `open()` closed.
- **F3 [HIGH silent-failure]** OSError in `_load_toml_flat` now translated to
  ConfigLoadError (was uncaught, leaked Python traceback).
- **C4** `_coerce_env_string` rejects inf/nan + leading-zero ambiguity.
- **C5** Dropped `_ = logging` antipattern.
- **C6** `config init` writes commented TOML (was bare key=value -- silent AC violation).
- **C7** TOML validation error message now names the source file/layer.
- **C9** `_flat_get` raises KeyError on unreachable path; alias-aware lookup
  via `model_fields` metadata (no more hardcoded `if part == "logging"`).
- **C10** Acceptance tests override HOME -> tmp_path so the developer's real
  ~/.config/shokz/config.toml doesn't pollute test runs.
- **C11** Replaced `python -c` subprocess test with direct in-process loader
  call (was bad smell; same scenario covered by unit + CLI smoke).
- **C12** `BUILTIN_DEFAULTS` derived from `AppConfig().model_dump(by_alias=True)`
  (was duplicated; single source now).

### Verified
- 79 unit tests passing (was 60, +19 review-coverage tests)
- Coverage 92.57% on domain + application + observability
- 11 INTEGRATION=1 acceptance tests passing (Sprint 1: 4 + 2: 7 + 3: 0 yet
  -- Sprint 3 has 9 unit-friendly + 1 INTEGRATION-gated for the encoded-bitrate AC)
- `just sprint-review 3`: 11/11 Sprint 3 scenarios covered
- Self-demos (clean state): config show / TOML override / env override /
  config init (with refuses-overwrite path) / config path -- all pass

### Sprint 3 deliberate scope (deferred per spec)
- skip_existing flag wiring                -> Sprint 4.5 (lands with manifest)
- cap_to_source flag wiring                -> Sprint 7
- retry config (max_attempts, backoff)     -> Sprint 7
- ui.progress = json|rich|plain|none       -> Sprint 6
- sources.youtube.cookies_*                -> later
- disk_safety_multiplier                   -> Sprint 8
All v3.1 plan §4 knobs not used by Sprints 1+2 today are STUBBED but produce
no behavior change in Sprint 3.

## [0.2.0] -- 2026-04-26

### Added -- Sprint 2: Title-based filenames + --name override
- `domain/filenames.py`: `sanitize_filename` (wraps `pathvalidate`),
  `render_template` (tokens: title, uploader, id, source, duration, date),
  `fallback_stem` (untitled-{id}), UTF-8-aware byte truncation.
  Default template: `{title}`. Default max length: 120 bytes.
- `domain/paths.py`: `is_path_within`, `assert_within` traversal guards.
- `domain/errors.py`: extended with `NameOutsideOutputDir`,
  `FilenameCollision`, `NameAmbiguous`.
- `application/policies/filename_resolver.py`: `FilenameResolver`
  pure class with default suffix-collision policy
  (`Foo.mp3` → `Foo (2).mp3` → `Foo (3).mp3` ...). Path-traversal guarded.
- `BatchDownloadUseCase`: now takes `filename_resolver_factory`;
  `BatchDownloadInput` gains `name_override: str | None`.
- `composition.py`: wires the resolver factory.
- CLI `download`: new `--name "Custom Name"` flag with single-URL guard
  (exit 2 + clear stderr message when given multiple URLs).
- `tests/unit/domain/test_filenames.py`: 16 unit tests including
  property-based "every title produces safe-or-empty stem".
- `tests/unit/application/test_filename_resolver.py`: 6 resolver tests.
- `tests/acceptance/test_sprint_2_filenames.py`: 7 Gherkin scenarios as
  pytest tests (gated by INTEGRATION=1).
- `tests/unit/test_cli_smoke.py`: extended with `--name` flag tests.
- README.md Use section updated with `--name` example.

### Process improvements (from Sprint 1 retro)
- `scripts/sprint-review.sh` + `just sprint-review N`: mechanical pre-tag
  check that diffs Gherkin Scenarios in `docs/sprints/sprint-N.md` against
  pytest test names. Catches DoD-erosion of "I'll add tests next sprint".
- `Justfile`: `just sprint-review N` recipe added.

### Code-review audit fixes (Sprint 2 review, same v0.2.0)
Two parallel reviewers (silent-failure-hunter + python-reviewer) found 6+5
substantive issues. All addressed before tag:

- **TOCTOU shrink (HIGH):** `FilenameResolver.resolve()` is now called
  immediately before `os.replace()`, AFTER encoding completes. The race
  window is microseconds instead of seconds-of-encoding. Sprint 8 closes
  it fully via filelock.
- **Symlink rejection (HIGH):** `BatchDownloadUseCase.execute()` rejects a
  symlinked `output_dir` upfront (would otherwise bypass `assert_within`).
- **Error taxonomy correctness:** `--name` empty-after-sanitize raises
  `NameInvalid` (NEW) -> CLI exit 2; suffix-loop exhaustion raises
  `FilenameCollision` (was dead code) -> CLI exit 1. `NameOutsideOutputDir`
  is now reserved for actual traversal/symlink events.
- **Top-level CLI catch-all:** unexpected `Exception` translated to clean
  stderr message + exit 1 (no tracebacks shown to swimmer).
- **Empty-title surfacing:** `render_template` logs WARNING when the resolved
  Track has empty title (defensive observability for buggy adapters).
- **Cleanup:** `NameAmbiguous` and `NameOutsideOutputDir` moved to top-level
  imports; `FilenameResolverFactory` annotated as `TypeAlias`; duplicate
  `Path as _Path` import dropped.
- **Hidden bug:** `{date}` removed from `_SUPPORTED_TOKENS` (was always
  emitting "" silently); Sprint 5 will re-add when upload_date is wired.
- **Test quality:** sanitizer-property test renamed to honest name; unicode
  test now uses actual unicode (放松音乐, Café Music, ピアノ夜曲); new
  use-case-level test for `NameAmbiguous` raise path.

### Verified
- 36 unit tests + 7 CLI smoke = 43 unit passing; 84% coverage on
  `domain` + `application` + `observability`
- 11 INTEGRATION=1 acceptance tests passing (Sprint 1: 4, Sprint 2: 7)
- `just sprint-review 2`: 9/9 Sprint 2 scenarios covered
- Self-demos (clean state):
  - `shokz download <URL>` -> `downloads/Me at the zoo.mp3` (NOT id-named!)
  - `shokz download --name "My Custom Mix" <URL>` -> `My Custom Mix.mp3`
  - `shokz download --name "Collision Demo" <URL>` twice -> `.mp3` + ` (2).mp3`
  - `shokz download --name "X" URL1 URL2` -> exit 2 + clear stderr message
- ruff lint + format + mypy --strict all clean

### Sprint 2 deliberate scope (deferred per spec)
- Configurable filename TEMPLATE + non-suffix collision policies -> Sprint 3
- original_title preservation in manifest                         -> Sprint 4
- Reconciliation of orphan files                                  -> Sprint 4.5

## [0.1.0] — 2026-04-26

### Added — Sprint 1: POC parity in hexagonal shell (MVP)
- `shokz download URL [URL...]` — concurrent download + MP3 conversion
- `domain/`: `Track`, `AudioSpec`, `RawDownload`, `EncodedFile`, `TrackStatus`,
  `TrackResult`, swim presets (`SWIM_LOW/STANDARD/HIGH`), minimal error taxonomy
- `application/ports/outbound/`: `VideoSourcePort`, `AudioEncoderPort`,
  `ProgressReporterPort` as `typing.Protocol` (PEP 544)
- `application/use_cases/batch_download.py`: `BatchDownloadUseCase` with
  bounded `asyncio.Semaphore(concurrency)`, per-track failure isolation
- `adapters/outbound/ytdlp_source.py`: resolve via `yt_dlp.YoutubeDL` Python
  module (typed dict, no subprocess parse), download via subprocess
  with `--remote-components ejs:github` (anti-bot solver, plan §11)
- `adapters/outbound/ffmpeg_encoder.py`: subprocess + `-f mp3` for non-standard
  output extensions (e.g. `.mp3.partial`); `probe_duration` via ffprobe JSON
- `adapters/outbound/null_progress.py`: no-op reporter for tests / quiet mode
- `adapters/inbound/cli/`: Typer app + `download` command, `--version`,
  `--output`, `--concurrency`, `--keep-raw`, `--log-level`
- `composition.py`: `Container` dataclass with explicit wiring (no DI framework)
- `tests/fakes.py`: `FakeVideoSource`, `FakeAudioEncoder`, `FakeProgressReporter`
- `tests/unit/application/test_batch_download.py`: orchestration scenarios
- `tests/unit/test_cli_smoke.py`: Typer `CliRunner` smoke
- `tests/acceptance/test_sprint_1_download.py`: Gherkin AC as pytest tests,
  gated by `INTEGRATION=1`

### Audit fixes (Sprint 1 review, same v0.1.0)
- Added 2 missing Gherkin acceptance tests: concurrent-3-URLs, mixed-valid-invalid
- Strengthened `test_no_source_can_handle_url_raises` (was tautology after fix)
- Broadened `_process_one` exception handler to isolate ANY exception type per
  Sprint 1 non-functional contract; Sprint 7 will narrow via error translation
  table (plan §7.1)
- Updated `README.md` Use section to reflect what actually shipped in v0.1.0
- Re-ran self-demo from truly clean `./downloads/` — 3/3 in 6.7s

### Verified
- 10 unit tests passing; **94.34%** coverage on `domain` + `application` + `observability`
- 2 integration tests passing against real YouTube (gated)
- Self-demo: `shokz download URL1 URL2 URL3` produced 3 valid 64 kbps mono
  MP3s (Shokz-compatible) in 7.3s wall-clock (proves concurrency)
- ruff lint + ruff format + mypy --strict all clean

### Sprint 1 deliberate scope (deferred)
- Title-based filenames + `--name`        → Sprint 2
- Configuration (TOML/env/CLI overrides)  → Sprint 3
- Manifest + skip-existing                → Sprint 4 / 4.5
- Playlist URLs                           → Sprint 5
- Rich progress + ID3 tagging             → Sprint 6
- Retry + bitrate cap + dry-run + failure log → Sprint 7
- Disk guard + lock + signal handling     → Sprint 8 (v1.0)
- Doctor + library verify                 → Sprint 9

## [0.0.0] — 2026-04-26

### Added
- Production scaffold: `pyproject.toml` (uv-managed, ruff, mypy --strict, pytest, coverage gate ≥80%).
- `Justfile` task runner (`install`, `lint`, `fmt`, `typecheck`, `test`, `integration`, `ci`, `clean`, `hooks-*`).
- `.pre-commit-config.yaml` with ruff, mypy, conventional-pre-commit, basic hygiene hooks.
- `src/shokz/__init__.py` exposing `__version__ = "0.0.0"`.
- `src/shokz/observability/logging.py` — stdlib logging + RichHandler + JSON formatter, `contextvars`-based `run_id`/`track_id` correlation IDs.
- `tests/test_smoke.py` — one-pass smoke test asserting package import, version, logging setup.
- `tests/conftest.py` — shared `downloads_dir` fixture.
- GitHub Actions: `ci.yml` (lint + typecheck + test on push/PR), `nightly-ytdlp.yml` (weekly run vs latest yt-dlp).
- `.github/PULL_REQUEST_TEMPLATE.md` embedding Sprint Goal field + DoD checklist.
- `RETRO.md` and `docs/sprints/_template.md` for the Agile-for-solo process layer (plan §0.5).
- `shokz.toml.example` (commented sample config; populated incrementally per sprint).

### Notes
- Sprint Goal: "Empty package builds, lints, type-checks, tests, and CI green — proving the quality bar enforces itself."
- DoD ratchet established. Subsequent sprints inherit and extend.
