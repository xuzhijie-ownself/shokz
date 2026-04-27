# Sprint 8a — Safety primitives (v0.9.0)

**Date:** 2026-04-27
**Tag target:** `v0.9.0` (PARTIAL — split mid-implementation per Phase 3 GAN review; wiring + SIGINT + ENOSPC translation deferred to Sprint 8b → v1.0.0)
**Effort:** delivered Phases 1+2+(DiskGuardPolicy only) ~2 hours of coding + 4 GAN passes
**Outcome:** the four new domain errors + FileLockPolicy + DiskGuardPolicy ship as DORMANT library primitives -- ready to be wired by Sprint 8b without further design work.

> **Why this file is `sprint-8a` not `sprint-8`:** mid-Phase-3 the GAN flagged that a partially-wired ENOSPC translation (ffmpeg done, local_filesystem + manifest not done) was a strict regression vs v0.8.0 on the disk-full path. Option C verdict: tag the green primitives now, ship wiring + SIGINT in Sprint 8b. The sections below describe what was ORIGINALLY scoped for Sprint 8; carry-forward items are tracked in `docs/sprints/sprint-8b.md`.

---

# (ORIGINAL Sprint 8 spec preserved below for traceability)

**Original tag target:** `v1.0.0` (the marquee release — master plan §8 calls Sprint 8 the "v1.0" milestone)
**Original effort:** ~1 day (revised after 3-reviewer GAN sweep added 6 BLOCK + 5 MED + 4 LOW fixes)

## Sprint Goal

Three safety primitives a CLI tool needs before a swimmer can trust their library to it:
1. Two `shokz` processes can't silently corrupt the same `downloads/` directory (cross-process file lock with PID + process-start-time identity).
2. `Ctrl+C` mid-download cancels in-flight work cleanly without leaving orphan partials, half-written manifest rows, or write-after-unlink-to-orphan-inode bytes (SIGINT cancellation with explicit sequencing + asyncio.shield with try/finally drain).
3. The user is told "you don't have enough disk" BEFORE downloading 3 GB of audio that won't fit (batch-level disk pre-flight + 3 ENOSPC translation sites).

These are the *correctness* gates that let the project carry a v1.0 label honestly.

## GAN-fix manifest (baked into this spec)

The pre-code GAN sweep flagged 6 BLOCK + 5 MED + 4 LOW issues; ALL bake into this spec before any code is written.

| Tag | What | Source |
|---|---|---|
| **B1** | `asyncio.shield` ALONE doesn't preserve manifest writes -- the awaiter still raises `CancelledError`, leaving the inner task as a fire-and-forget orphan. MANDATORY pattern: `task = asyncio.ensure_future(coro); try: await asyncio.shield(task); except CancelledError: await asyncio.wait_for(task, timeout=5.0); raise`. | architect#1, python-rev#2, silent#2 |
| **B2** | (a) Lock-file PID embed unsafe (filelock opens with `O_TRUNC` on every poll); use SIBLING `.shokz.lock.meta` written via `os.replace` after acquire. (b) PID-reuse: meta MUST include `started_at: float` (psutil.Process(pid).create_time()) AND classification checks `(pid alive) AND abs(recorded_start - actual_start) < 2.0`. (c) JSON parse failure on meta → `StaleLock` with "lock meta corrupt; rm `.shokz/locks/shokz.lock` to proceed". (d) `os.kill(pid, 0)` PermissionError → NEW `LockOwnerUnknown` (alive but other-user). | architect#2, python-rev#1, silent#3 |
| **B3** | Disk guard MUST run as ONE pre-flight in `execute()` after resolve-all, summing batch estimates × multiplier. Per-track check inside `_process_one` is a defensive secondary. First `DiskFull` aborts the rest of the batch (no per-track re-check log spam). | architect#3, silent#5 |
| **B4** | `OSError(ENOSPC)` catch in `ffmpeg_encoder.py` is DEAD CODE -- ffmpeg runs as subprocess, OSError doesn't propagate. Must check stderr text in non-zero-exit branch (`"no space left"` or `"enospc"` substring) and raise `DiskFull(...) from None`. | python-rev#4 |
| **B5** | SIGINT cleanup MUST sequence: SIGTERM subprocess → `await proc.wait()` (with bounded grace, then SIGKILL) → THEN unlink `.tmp/<id>.*`. Otherwise yt-dlp continues writing to the unlinked inode and bytes are silently lost. | silent#1 |
| **B6** | Raw cleanup outside the shield leaks `.webm` files. `_process_one` MUST have a `finally:` block that calls `_cleanup_partial(track.id)` if the track did NOT fully succeed (manifest row written + raw removed). | silent#2 |
| **M1** | `filelock.acquire()` blocks the event loop. Acquire BEFORE `asyncio.run()` in the CLI command (sync); release in `finally` after. Use case stays library-friendly (no lock acquisition inside async code). | architect#6, python-rev#3 |
| **M2** | `humanfriendly.format_size(bytes_val, binary=True)` for IEC units ("GiB" not "GB") -- matches the Gherkin scenario assertion. | python-rev#5 |
| **M3** | Manifest ENOSPC: `raise ManifestInconsistent("manifest write failed for {track_id}") from DiskFull("disk full during manifest append")`. `_ERROR_CLASS_MAP` matches `ManifestInconsistent` first (it already does); failures.jsonl records `MANIFEST_INCONSISTENT` (the recoverable signal that triggers reconciliation hint). The DiskFull is `__cause__`. | python-rev#7 |
| **M4** | Plan §8 line 779 lists `shokz retry` as part of v1.0. Spec defers to 8.5 with rationale; AMEND `.claude/plan/shokz-downloader.md` line 779 in the same PR to move retry to 8.5. | architect#8 |
| **M5** | HLS underestimation: when ffmpeg-stderr-ENOSPC fires after pre-flight passed, log WARNING "pre-flight passed (estimate=Xb) but ENOSPC during encode -- `filesize_approx` likely underestimated; consider `[disk] safety_multiplier > {current}`". | silent#5 |
| **L1** | Double-Ctrl-C: SIGINT handler calls `loop.remove_signal_handler(SIGINT)` immediately on first invocation; second Ctrl-C falls through to default Python KeyboardInterrupt for hard exit. | python-rev#8 |
| **L2** | `failures.jsonl` is unbounded; document as v1.0 known limit; `shokz retry` in 8.5 will document recommended max size. | silent#6 |
| **L3** | `os.kill(pid, 0)` is macOS/Linux only; assert `sys.platform != "win32"` at FileLockPolicy module load with explanatory message. Project targets darwin per CLAUDE.md. | python-rev#6 |
| **L4** | Disk guard `filesize_approx is None` is no longer silent-skip. Default policy: WARNING + skip; `[disk] require_estimate: bool = False` (default False to preserve compatibility). When True, raises `DiskFull` saying "source did not report filesize_approx; pass `--allow-unknown-size` or set `[disk] require_estimate = false`". | architect#4 |

## Scope split (per Sprint 6/7 retro lesson)

The master plan §8 lists Sprint 8 as 4 deliverables. Per "STOP and play it back as a half-page spec WITH deferred items called out, BEFORE coding":

| # | Item | Status |
|---|---|---|
| 1 | Cross-process file lock (`policies/file_lock.py`) | **Sprint 8 (this)** |
| 2 | SIGINT cancellation + `asyncio.shield` for manifest writes | **Sprint 8 (this)** |
| 3 | Disk guard (`policies/disk_guard.py`) + 3 ENOSPC translation rows | **Sprint 8 (this)** |
| 4 | `shokz retry` command (reads `failures.jsonl`) | DEFERRED → Sprint 8.5 (v1.0.1). Plan §8 line 779 amended in same PR (M4). |

## User Story

```
Title: Trust the library: two processes, Ctrl+C, full disk

As a Swimmer who has built up 200 MP3s in `./downloads/`, I want to be
sure that:
  - if I accidentally start `shokz download` twice in two terminals, the
    second one says "another shokz is running" instead of corrupting my
    library;
  - if I hit Ctrl+C while a 7-hour video is downloading, my in-flight
    .tmp/ file is cleaned up and my manifest still matches what's on disk;
  - if I try to download 3 GB of audio onto a drive with 1 GB free, the
    tool tells me BEFORE consuming bandwidth, not after ffmpeg runs out
    of space mid-encode.

Acceptance Criteria (Gherkin -- written BEFORE code):

  Scenario: Second shokz invocation against same output_dir is rejected
    Given a `shokz download URL_LONG` running in process A
      AND `.shokz/locks/shokz.lock` is held by A's PID
      AND `.shokz/locks/shokz.lock.meta` records {pid: A_pid, started_at: A_start_time}
    When I run `shokz download URL_OTHER` in process B
        against the same `--output` dir
    Then process B exits non-zero with error_class ANOTHER_RUN_IN_PROGRESS
     AND stderr names PID(A) and the full lock path
     AND no .tmp file is created by B
     AND A's download is unaffected

  Scenario: Stale lock (holder SIGKILLed) gets clear remediation guidance
    Given `.shokz/locks/shokz.lock` exists with PID 99999 (a dead process)
      AND `.shokz/locks/shokz.lock.meta` parses but PID is dead per os.kill(pid,0)
    When I run `shokz download URL`
    Then exits non-zero with error_class STALE_LOCK
     AND stderr says "stale lock from dead PID 99999; remove
        .shokz/locks/shokz.lock to proceed"
     AND the lock file is NOT auto-removed (user-confirmed cleanup)

  Scenario: PID reuse: alive PID with mismatched start_time -> StaleLock (B2c)
    Given `.shokz/locks/shokz.lock.meta` records started_at = T0
      AND PID at meta.pid is alive but psutil reports create_time T1
      AND |T0 - T1| > 2.0 seconds
    When I run `shokz download URL`
    Then exits with STALE_LOCK (NOT ANOTHER_RUN_IN_PROGRESS)
     AND stderr explains "PID reused since previous shokz invocation"

  Scenario: Lock owner unknown (other-user PID) -> LockOwnerUnknown (B2d)
    Given `.shokz/locks/shokz.lock` is held by another user's process
      AND os.kill(pid, 0) raises PermissionError
    When I run `shokz download URL`
    Then exits with LOCK_OWNER_UNKNOWN
     AND stderr says "PID alive but owned by another user; refusing to assume stale"
     AND the lock is NOT removed

  Scenario: Lock meta corrupt -> StaleLock with diagnostic (B2c)
    Given `.shokz/locks/shokz.lock.meta` exists but JSON parse fails
        (truncated mid-write by a previous SIGKILL)
    When I run `shokz download URL`
    Then exits with STALE_LOCK
     AND stderr says "lock meta corrupt (truncated write); remove
        .shokz/locks/shokz.lock and shokz.lock.meta to proceed"
     AND a WARNING log carries the raw bytes for diagnosis

  Scenario: SIGINT mid-download cancels cleanly with subprocess sequencing (B5)
    Given a long download in progress (yt-dlp subprocess + .tmp/ partial bytes)
    When the user sends SIGINT to the CLI process
    Then the SIGINT handler is removed (L1) so a second Ctrl-C raises KeyboardInterrupt
     AND the yt-dlp subprocess receives SIGTERM
     AND `await proc.wait()` completes (with grace timeout 3s, then SIGKILL)
     AND ONLY THEN are .tmp/<track_id>.* files unlinked (no write-after-unlink races)
     AND no orphan manifest entries are created for the cancelled track
     AND the lock file is released
     AND the exit code is 130 (SIGINT convention)

  Scenario: SIGINT during manifest.record uses shield + try/finally drain (B1)
    Given a track that finished encode + atomic_move
      AND `task = asyncio.ensure_future(manifest.record(entry))` is in progress
    When SIGINT arrives BEFORE the task completes
    Then the awaiter raises CancelledError from `await asyncio.shield(task)`
     AND the `except CancelledError` block calls `await asyncio.wait_for(task, 5.0)`
        so the manifest write LANDS before the propagation
     AND THEN CancelledError propagates to remaining tracks
     AND the track is reported SUCCESS (not FAILED -- the row IS in the manifest)
     AND if the 5s grace expires, log MANIFEST_INCONSISTENT and let reconciliation flag it

  Scenario: Raw cleanup runs in finally block outside the shield (B6)
    Given encode + manifest.record both succeeded
      AND SIGINT arrives BETWEEN manifest.record completion and filesystem.remove(raw)
    When _process_one's finally block runs
    Then the raw .webm in tmp_dir IS removed (the finally cleanup catches what the
        unshielded body missed)
     AND no orphan .webm leaks into .tmp/ (where reconciliation excludes it)

  Scenario: Disk guard ONE batch-level pre-flight blocks before any download (B3)
    Given a batch of 4 tracks each with filesize_approx = 3 GB
      AND `shutil.disk_usage(output_dir).free` reports 10 GB
      AND safety_multiplier = 2.0 (default; sum_estimate * 2 must fit)
    When `shokz download URL_A URL_B URL_C URL_D` runs
    Then the disk guard runs ONCE in execute() after resolve-all
     AND raises DiskFull with message "insufficient disk: need ~24 GiB free; have 10 GiB"
        (humanfriendly with binary=True, M2)
     AND NO yt-dlp subprocess is spawned for ANY track
     AND failures.jsonl records ONE row error_class DISK_FULL (not 4)

  Scenario: Disk guard skips when filesize_approx is None (default policy, L4)
    Given the source can't predict file size (live stream / chunked)
      AND `[disk] require_estimate = false` (default)
    When `shokz download URL` runs
    Then a WARNING log notes "skip disk guard: source did not report
        filesize_approx for <track_id> -- consider [disk] require_estimate=true"
     AND the download proceeds (best-effort)

  Scenario: Disk guard with require_estimate=true rejects unknown-size sources (L4)
    Given `[disk] require_estimate = true` in config
      AND filesize_approx is None for the track
    When `shokz download URL` runs
    Then exits with DISK_FULL (NOT proceeding silently)
     AND stderr says "source did not report filesize_approx; pass
        `--allow-unknown-size` or set [disk] require_estimate = false"

  Scenario: ENOSPC during ffmpeg encode -> DiskFull via stderr-text (B4)
    Given the disk fills DURING the encode phase
    When ffmpeg exits non-zero with stderr containing "No space left on device"
    Then the encoder adapter raises DiskFull (via stderr substring match,
        NOT via OSError catch -- ffmpeg subprocess doesn't propagate OSError)
     AND the .partial file is unlinked (cleanup before raise)
     AND failures.jsonl records error_class DISK_FULL
     AND a WARNING is logged: "ENOSPC during encode for <track_id>; pre-flight
        passed -- filesize_approx may have underestimated; consider higher
        [disk] safety_multiplier" (M5)

  Scenario: ENOSPC during atomic_move (os.replace) -> DiskFull
    Given the disk fills BETWEEN encode-success and os.replace
    When os.replace raises OSError(errno.ENOSPC)
    Then the local_filesystem adapter translates -> DiskFull
     AND the .partial file remains in tmp_dir for next-run cleanup

  Scenario: ENOSPC during manifest append -> ManifestInconsistent FROM DiskFull (M3)
    Given the disk fills DURING JsonlManifest._append_with_fsync
    When the write raises OSError(errno.ENOSPC)
    Then the adapter raises ManifestInconsistent("manifest write failed for {id}")
        FROM DiskFull("disk full during manifest append")
     AND failures.jsonl records error_class MANIFEST_INCONSISTENT
        (the recoverable-signal class; DiskFull is __cause__ for diagnosis)
     AND a STARTUP-NOTICE log warns user to run `shokz library verify`

  Scenario: First batch-level DiskFull aborts the rest of the batch (B3)
    Given a 60-track playlist where the disk pre-flight FAILS for batch
    When DiskFull is raised once at the top of execute()
    Then the rest of the batch is NOT attempted (no per-track guard re-runs)
     AND the CLI summary shows "0/60 succeeded; 60 blocked: disk full"
     AND exits non-zero with the single DISK_FULL error_class

  Scenario: Lock survives `shokz library list/show/verify` (read-only commands)
    Given `shokz download` is running and holding the lock
    When the user runs `shokz library list` in another terminal
    Then library list succeeds (it doesn't acquire the lock)
     AND library verify is also lock-free
     (Only the WRITE commands -- download, playlist -- acquire the lock.)

  Scenario: Acceptance ratchet -- kill-test still PASSES (Sprint 4)
    Given the existing `just kill-test` (SIGKILL mid-encode)
    When run after Sprint 8 changes
    Then it still PASSES (no .partial files left in `downloads/`)
     AND a NEW SIGINT integration test ALSO PASSES (cleaner shutdown
        than SIGKILL because the handler runs)

Non-functional:
  - Library-first: `filelock>=3.13` (already pinned). `humanfriendly>=10`
    (pinned). `psutil>=5.9` for `Process(pid).create_time()` start-time
    check (NEW dep -- add to pyproject.toml).
  - Lock file at `output_dir/.shokz/locks/shokz.lock`; sibling meta at
    `.shokz/locks/shokz.lock.meta`. Meta JSON: `{"pid": int, "started_at":
    float, "iso_started": "2026-04-27T12:00:00Z"}`. Written via `os.replace`
    AFTER `lock.acquire()`. Removed on `lock.release()` if best-effort.
  - Lock acquired SYNC in the CLI command BEFORE `asyncio.run()` (M1) so
    SIGINT during the wait raises KeyboardInterrupt cleanly. Released in
    a `finally` post-loop. Use case stays async-only (no lock acquisition
    in async code).
  - Stale-vs-active classification (in priority order):
    1. JSON parse fails -> `StaleLock("lock meta corrupt; ...")`
    2. `psutil.Process(pid)` raises `NoSuchProcess` -> `StaleLock("dead PID")`
    3. `os.kill(pid, 0)` raises `PermissionError` -> `LockOwnerUnknown(...)`
    4. `Process(pid).create_time()` differs from meta.started_at by >2.0s
       -> `StaleLock("PID reused; create_time mismatch")`
    5. Otherwise -> `AnotherRunInProgress(pid)`
  - SIGINT handler is registered ONLY in CLI command. Pattern:
    ```
    def handler() -> None:
        loop.remove_signal_handler(signal.SIGINT)  # L1
        for task in asyncio.all_tasks(loop):
            task.cancel()
    loop.add_signal_handler(signal.SIGINT, handler)
    ```
  - `_process_one` finally block (B6): if the track did NOT fully succeed
    (manifest row written AND raw removed), call `_cleanup_partial(track.id)`
    to remove leftover `.tmp/<id>.*` files.
  - manifest.record wrap (B1):
    ```
    record_task = asyncio.ensure_future(self._manifest.record(entry))
    try:
        await asyncio.shield(record_task)
    except asyncio.CancelledError:
        try:
            await asyncio.wait_for(record_task, timeout=5.0)
        except asyncio.TimeoutError:
            _log.warning("manifest.record exceeded 5s grace; SF-4 reconciliation will catch")
        raise
    ```
  - download_audio adapter exposes a way to terminate (B5). Either: (a)
    cleanup hook in retry policy receives the running `proc` reference and
    terminates it, OR (b) the use case keeps a per-track `Optional[Process]`
    instance attribute the SIGINT handler can SIGTERM. Option (a) is
    cleaner; bake into Sprint 8.
  - Disk guard math (B3): batch pre-flight = `sum(filesize_approx for track
    in resolved_tracks if track.filesize_approx is not None) *
    safety_multiplier`. Tracks with None filesize_approx are skipped from
    the SUM but counted in the warning under L4.
  - 3 ENOSPC translation sites:
    1. `ffmpeg_encoder.py`: stderr-text check (B4) -- "no space left" /
       "enospc" substring after non-zero exit; cleanup .partial; raise
       `DiskFull(...)`.
    2. `local_filesystem.py`: catch `OSError` with `errno == errno.ENOSPC`
       in `atomic_move`; raise `DiskFull` (leave .partial for next-run
       cleanup).
    3. `jsonl_manifest.py`: catch `OSError(ENOSPC)` in `_append_with_fsync`;
       raise `ManifestInconsistent(...) from DiskFull(...)` (M3).
  - 4 NEW domain errors: `StaleLock`, `AnotherRunInProgress`,
    `LockOwnerUnknown`, `DiskFull`. All inherit ShokzError.
  - `_ERROR_CLASS_MAP` (Sprint 7 isinstance-tuple) extended in PROPER ORDER:
    LockOwnerUnknown / StaleLock / AnotherRunInProgress / DiskFull all
    BEFORE the catch-all `DownloadFailed`. ManifestInconsistent stays at
    its pre-Sprint-7 position so it wins over DiskFull when both apply
    (M3).

Out of scope (deferred):
  - `shokz retry` command -> Sprint 8.5 (v1.0.1). Plan §8 line 779 will
    be amended in this PR (M4).
  - Bitrate cap (`domain/bitrate.py`)            -> Sprint 6b backlog
  - ID3 tagging                                   -> Sprint 6b backlog
  - `--dry-run`                                   -> Sprint 7.5
  - Progress bars / live UI                       -> intentionally never
  - `shokz library verify --tags`                 -> Sprint 9 doctor sweep
  - `shokz doctor` (NFS / non-local FS warning)   -> Sprint 9 doctor sweep
  - Per-batch concurrency lock (Sprint 7 GAN-noted "best-effort under
    concurrency > 1") -> documented as known limitation; cross-process
    file lock prevents the more dangerous race
  - failures.jsonl rotation/cap  -> documented as v1.0 known limitation (L2)

INVEST: Independent, Negotiable, Valuable (THE v1.0 marketing primitives),
        Estimable (~1 day with per-phase GANs and 6 BLOCK fixes baked in),
        Small-ish (3 cohesive primitives), Testable (16 Gherkin scenarios
        + 1 ratchet)
```

## Definition of Ready (DoR) — checked

- [x] Sprint Goal written
- [x] Gherkin AC (16 scenarios + atomic-write ratchet — expanded from 10 by GAN coverage)
- [x] Affected files listed
- [x] Ports/contracts named: NO new ports. NEW POLICIES `FileLockPolicy` + `DiskGuardPolicy`. NEW domain class `LockOwnerUnknown` (added by GAN B2d).
- [x] Test approach: unit (lock acquire/stale-PID/PID-reuse/permission-denied/JSON-corrupt; disk guard math + None-handling; ENOSPC translation per site; shield-with-finally drain pattern); INTEGRATION-gated (SIGINT-mid-download with real subprocess); existing `kill-test` ratchet must hold
- [x] Dependencies: Sprint 7 v0.8.0 ✓; psutil>=5.9 added to pyproject.toml
- [x] Out-of-scope explicit
- [x] Estimated ~1 day (with per-phase GAN reviews)
- [x] All 6 BLOCK + 5 MED + 4 LOW GAN fixes baked into the spec

## Files to land in Sprint 8

### domain/
- `domain/errors.py` — ADD `StaleLock`, `AnotherRunInProgress`, `LockOwnerUnknown`, `DiskFull`. All inherit `ShokzError`. Existing 15 classes UNCHANGED.

### application/policies/
- `application/policies/file_lock.py` — NEW. `FileLockPolicy(lock_path: Path)` exposes `__enter__/__exit__` (sync). Wraps `filelock.FileLock`. Writes meta via `os.replace` AFTER acquire. On Timeout, classifies via the 5-step priority list (B2). Module-level `assert sys.platform != "win32"` (L3).
- `application/policies/disk_guard.py` — NEW. `DiskGuardPolicy(safety_multiplier: float = 2.0, require_estimate: bool = False)` exposes `check_batch(output_dir, estimates: list[int | None]) -> None`. Sums non-None estimates × multiplier; compares against `shutil.disk_usage(output_dir).free`. Logs WARNING for None entries; raises `DiskFull` if `require_estimate=True` AND any estimate is None (L4).

### application/use_cases/
- `application/use_cases/batch_download.py`:
  - call `disk_guard.check_batch(output_dir, [t.filesize_approx for t in resolved])` ONCE in `execute()` AFTER resolve-all (B3); abort batch on first DiskFull
  - manifest.record wrap with shield + try/finally drain (B1)
  - `_process_one` finally block (B6): cleanup raw if track did NOT fully succeed
  - retry policy `on_retry` cleanup hook now also accepts a running subprocess reference for SIGTERM (B5) — OR adapter exposes a per-attempt subprocess reference

### domain/models.py
- `Track.filesize_approx: int | None = None` so the disk guard can sum batch estimates.

### adapters/outbound/
- `adapters/outbound/ytdlp_source.py`:
  - `resolve()` populates `Track.filesize_approx` from `info.get("filesize_approx") or info.get("filesize")`
  - download_audio: terminate-on-cancel pattern (B5). Either: (a) accept a `terminate_on_cancel: asyncio.Event` parameter the use case sets in its SIGINT handler, OR (b) use `asyncio.shield`-aware cleanup. Pick the simpler one in implementation; document.
- `adapters/outbound/ffmpeg_encoder.py` — stderr-text-based ENOSPC translation (B4): in non-zero-exit branch, check `"no space left" in stderr.lower() or "enospc" in stderr.lower()`; cleanup .partial; raise `DiskFull(...)`.
- `adapters/outbound/local_filesystem.py` — catch `OSError(errno.ENOSPC)` in `atomic_move`; raise `DiskFull`.
- `adapters/outbound/jsonl_manifest.py` — catch `OSError(errno.ENOSPC)` in `_append_with_fsync`; raise `ManifestInconsistent(msg) from DiskFull(msg)` (M3).

### adapters/inbound/cli/
- `adapters/inbound/cli/commands/download.py`:
  - Acquire FileLockPolicy SYNC before `asyncio.run` (M1)
  - Register `loop.add_signal_handler(SIGINT, handler)` once inside the asyncio loop
  - Handler removes itself (L1), cancels all tasks
  - Release lock in `finally` post-loop
  - Exit 130 on SIGINT
- `adapters/inbound/cli/commands/playlist.py` — same setup
- `adapters/inbound/cli/_summary.py` — extend `print_batch_summary` for `result.disk_full_count` and `result.locked_out` notice (NEW fields)

### domain/models.py + BatchDownloadResult
- `BatchDownloadResult.disk_full_count: int = 0` (already in original spec)
- (No `locked_out` — lock failures exit before execute() is even called)

### config
- `config/schema.py` — `DiskSection` with `safety_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)` and `require_estimate: bool = False` (L4). `LockSection` with `timeout_s: float = Field(default=5.0, ge=0.0, le=60.0)`. Both `validate_default=True`.

### composition root
- `composition.py` — wire `FileLockPolicy(state_dir / "locks/shokz.lock")` and `DiskGuardPolicy(config.disk.safety_multiplier, config.disk.require_estimate)`.

### pyproject.toml
- Add `psutil>=5.9` for `Process(pid).create_time()` start-time check (B2).
- `[[tool.mypy.overrides]]`: add `psutil` to `ignore_missing_imports = true` (psutil's stubs are partial).

### tests/
- `tests/unit/application/test_file_lock_policy.py` — NEW. Acquire/re-acquire-same-process; meta JSON parse failure → StaleLock; dead PID → StaleLock; PID-reuse start_time mismatch → StaleLock; PermissionError → LockOwnerUnknown; alive same-start → AnotherRunInProgress.
- `tests/unit/application/test_disk_guard_policy.py` — NEW. Batch sum math, None-skip default, require_estimate=True path, humanfriendly binary=True format.
- `tests/unit/adapters/test_ffmpeg_enospc_stderr.py` — NEW. Mock subprocess exit non-zero with ENOSPC stderr text → DiskFull.
- `tests/unit/adapters/test_local_filesystem_enospc.py` — NEW. OSError(ENOSPC) → DiskFull.
- `tests/unit/adapters/test_jsonl_manifest_enospc.py` — NEW. ManifestInconsistent FROM DiskFull chain.
- `tests/unit/application/test_batch_download.py` — extend with: batch-level disk guard pre-flight; first DiskFull aborts batch; manifest.record shield+drain pattern (asserts task.done() after CancelledError); finally-block raw cleanup.
- `tests/acceptance/test_sprint_8_lock_signal_disk.py` — Gherkin scenarios (16). INTEGRATION-gated SIGINT test that spawns real `shokz download`, sends SIGINT, asserts cleanup.

### .claude/plan/shokz-downloader.md
- Line 779: amend Sprint 8 row to remove `shokz retry` from the v1.0 deliverable; note that retry deferred to Sprint 8.5 v1.0.1 (M4).

## Definition of Done (DoD) — verify before close

- [ ] All 16 Gherkin AC scenarios pass as pytest tests
- [ ] `just sprint-review 8` passes
- [ ] `just code-review v0.8.0` brief generated; reviewers dispatched; convergent + unique findings either fixed OR explicitly deferred-with-reason
- [ ] `just lint / format / typecheck` clean
- [ ] `just test` green; coverage ≥ 80%
- [ ] **Atomic-write protocol still holds** (`just kill-test` Sprint 4 ratchet)
- [ ] **Reconciliation handles subdirs** (Sprint 4.5 retro DoD ratchet)
- [ ] **Sequential-by-default still holds** (Sprint 6 ratchet)
- [ ] **Subprocess-stderr classification still holds** (Sprint 7 ratchet)
- [ ] **NEW Sprint 8 ratchets**:
  - SIGINT integration test (INTEGRATION=1) PASSES — sends SIGINT mid-download, asserts no `.tmp/*` files leak, asserts exit code 130, asserts subprocess SIGTERM happened BEFORE unlink (B5 sequencing)
  - shield+drain pattern unit test asserts `task.done() is True` after CancelledError propagates (B1)
  - Lock meta corrupt JSON test asserts StaleLock with diagnostic message (B2c)
- [ ] CHANGELOG.md `[Unreleased]` → `[1.0.0]` (the marquee note)
- [ ] README.md updated (mention multi-process safety, SIGINT, disk pre-flight, v1.0 milestone)
- [ ] `.claude/plan/shokz-downloader.md` line 779 amended (M4)
- [ ] Conventional Commits: `feat(safety): cross-process lock + SIGINT cancellation + disk guard (Sprint 8, v1.0.0)`
- [ ] Self-demo: two terminal `shokz download` invocations -- second exits cleanly with `ANOTHER_RUN_IN_PROGRESS`; SIGINT mid-download leaves clean `.tmp/`
- [ ] Git tag pushed: `v1.0.0` ⭐
- [ ] Retro entry appended to RETRO.md (mark v1.0 milestone reflection)
