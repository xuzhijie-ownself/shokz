# Sprint 8b — Wire safety primitives + SIGINT + 3 ENOSPC sites (v1.0.0 ⭐)

**Date:** 2026-04-27 (same-day push after v0.9.0; architect-PUSH verdict)
**Tag target:** `v1.0.0`
**Effort:** ~½ day
**Status:** ✅ COMPLETED — wired, GAN-reviewed (1 HIGH + 2 MED found and fixed), 237 tests pass

## Closing notes

- Phase A: 3 ENOSPC translations (`ffmpeg_encoder.py`, `local_filesystem.py`, `jsonl_manifest.py`) + 7 unit tests
- Phase B: `BatchDownloadUseCase` wired with `DiskGuardPolicy`, `asyncio.shield`-drain pattern around `manifest.record`, raw `.tmp/<id>.*` cleanup in `finally`, first-DiskFull-aborts-rest, `BatchDownloadResult.disk_full_count`, parallelised pre-resolve cache
- Phase C: `_runtime.py` helper (FileLockPolicy + SIGINT-handling `asyncio.run`), `download` and `playlist` CLI commands wrapped in `with build_output_lock(config):` plus `run_async_with_sigint(...)`, composition wires `DiskGuardPolicy`
- Phase D: 4 acceptance tests, summary line concurrency-aware, GAN review found 1 HIGH (asyncio `CancelledError` not converted back to `KeyboardInterrupt` because `loop.add_signal_handler` replaces Runner's internal handler) + 2 MED (`glob.escape` for non-YouTube IDs; concurrency>1 multi-trigger summary phrasing). All fixed.

## Origin

Split from original Sprint 8 per Phase 3 GAN review (Option C verdict): tag v0.9.0 with the policy primitives as DORMANT library code, defer wiring + SIGINT + ENOSPC translations to Sprint 8b. Rationale: a partially-wired Phase 3 was a strict regression vs v0.8.0 on the disk-full path (ffmpeg ENOSPC translated cleanly while local_filesystem and jsonl_manifest sites did not), violating the project's "every tag = green DoD" ratchet on its highest-stakes release.

## Sprint Goal (carried from sprint-8a)

The same three v1.0 safety primitives, now WIRED:
1. CLI `download` and `playlist` commands acquire FileLockPolicy BEFORE asyncio.run; second invocation gets AnotherRunInProgress / StaleLock / LockOwnerUnknown per the 5-step classification.
2. SIGINT mid-download cancels in-flight tasks cleanly with explicit subprocess SIGTERM → wait → unlink sequencing; manifest writes survive via `asyncio.shield` + try/finally drain.
3. Disk pre-flight runs ONCE in `execute()` after resolve-all; first DiskFull aborts the rest of the batch; ENOSPC at three runtime sites translates correctly.

## Carried-forward from sprint-8a

### Primitives ALREADY shipped in v0.9.0 (no rework)

- `domain/errors.py`: `AnotherRunInProgress`, `StaleLock` (with `raw_meta_bytes`), `LockOwnerUnknown`, `DiskFull` (with `need_bytes` / `have_bytes`)
- `config/schema.py`: `DiskSection` (`safety_multiplier`, `require_estimate`), `LockSection` (`timeout_s`)
- `application/policies/file_lock.py`: 5-step classification, sibling meta JSON, atomic write, GAN-fixed (TOCTOU retry + release-before-unlink + write-failure releases flock + RuntimeError on impossible own-PID)
- `application/policies/disk_guard.py`: batch-level `check_batch`, humanfriendly binary=True, require_estimate flag
- `pyproject.toml`: `psutil>=5.9,<8`
- 22 unit tests across 3 test files (errors, file_lock, disk_guard)

### Carried-forward GAN findings to address in 8b

| Tag | What | Source |
|-----|------|--------|
| **B1** | `asyncio.shield` + `try/except CancelledError: await task` drain pattern in `manifest.record(entry)` site of `_process_one`. Bounded grace timeout. Test asserts `task.done()` after CancelledError propagates. | architect#1, python-rev#2, silent#2 |
| **B3** | DiskGuardPolicy.check_batch() called ONCE in `execute()` after resolve-all, summing `[t.filesize_approx for t in resolved]`. First DiskFull aborts batch (no per-track guard re-runs). | architect#3 |
| **B4** | ffmpeg_encoder.py: stderr-text ENOSPC translation (`"no space left"` / `"enospc"` substring). Cleanup `.partial` before raise. WARNING log explaining "pre-flight may have underestimated". | python-rev#4 + silent#5 |
| **B5** | yt-dlp subprocess SIGTERM + `await proc.wait()` (with grace timeout, then SIGKILL) BEFORE unlink. Use case's SIGINT handler invokes adapter's terminate hook; closure ordering matters. | silent#1 |
| **B6** | `_process_one` finally block: cleanup raw `.tmp/<id>.*` files if track did NOT fully succeed (manifest row written + raw removed). | silent#2 |
| **M1** | Lock acquired SYNC in CLI commands BEFORE `asyncio.run()`. Released in `finally` post-loop. Use case stays library-friendly. | architect#6, python-rev#3 |
| **M3** | jsonl_manifest.py ENOSPC: `raise ManifestInconsistent(...) from DiskFull(...)`. _ERROR_CLASS_MAP order keeps `MANIFEST_INCONSISTENT` as the visible class (recoverable signal for reconciliation). | python-rev#7 |
| **L1** | Double-Ctrl-C: SIGINT handler removes itself on first invocation; second falls through to KeyboardInterrupt. | python-rev#8 |
| **(new)** | Track.filesize_approx field added to `domain/models.py`; ytdlp_source.resolve() populates from `info.get("filesize_approx") or info.get("filesize")`. | spec |
| **(new)** | local_filesystem.py: `OSError(errno.ENOSPC)` catch in `atomic_move` → raise `DiskFull` (leave `.partial` for next-run cleanup). | spec |

## Files to land (preview — no new ports)

- `domain/models.py` — `Track.filesize_approx: int | None = None`; `BatchDownloadResult.disk_full_count: int = 0`
- `application/use_cases/batch_download.py` — disk_guard.check_batch() pre-flight; asyncio.shield+drain manifest.record; finally-block raw cleanup; first-DiskFull batch abort; circuit-breaker counter for disk-full
- `adapters/outbound/ytdlp_source.py` — `resolve()` populates filesize_approx; `download_audio()` exposes a SIGTERM hook so the use case's SIGINT handler can terminate before unlink
- `adapters/outbound/ffmpeg_encoder.py` — stderr-text ENOSPC → DiskFull (the revert-on-Phase-3 work re-applied with the rest of the path)
- `adapters/outbound/local_filesystem.py` — OSError(ENOSPC) → DiskFull
- `adapters/outbound/jsonl_manifest.py` — OSError(ENOSPC) → ManifestInconsistent FROM DiskFull
- `adapters/inbound/cli/commands/{download,playlist}.py` — FileLockPolicy acquire pre-asyncio.run; SIGINT handler with self-removal + cancel-all
- `adapters/inbound/cli/_summary.py` — surface disk_full_count + lock notice
- `composition.py` — wire FileLockPolicy + DiskGuardPolicy
- 3 NEW ENOSPC test files (ffmpeg, fs, manifest)
- 1 acceptance test file (Sprint 8 Gherkin scenarios)
- `tests/unit/application/test_batch_download.py` — extend with: lock-acquired (CLI-level smoke); disk-guard-blocks-before-download; manifest.record shielded+drained; finally-block raw cleanup; first-DiskFull batch abort

## DoR — to be checked at 8b start

- [ ] All Sprint 8a deferred items above transferred verbatim to Gherkin AC
- [ ] Confirm `kill-test` still PASSES with the SIGINT cleanup ordering
- [ ] Real-device check: SIGINT mid-7-hour-video on actual filesystem leaves clean `.tmp/`
