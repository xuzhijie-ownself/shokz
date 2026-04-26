# Sprint 4 — Manifest + atomic writes + integrity checks

**Date:** 2026-04-27
**Tag target:** `v0.4.0` (crash-safe single-process writes — NOT yet v1.0.0; that lands at Sprint 8 after lock + signals + disk guard)
**Effort:** ~½ day

## Sprint Goal

A killed download leaves no partial final files in `downloads/`; a successful download is provably the right length and not silently truncated; every successful track is recorded in an append-only JSONL manifest with fsync durability.

## User Story

```
Title: Crash-safe writes + integrity-checked encodes + manifest

As a Swimmer running an overnight 50-URL batch on a laptop, I want
killed/crashed runs to leave NO half-written .mp3 files in downloads/
AND a manifest of what completed, so I can re-run safely and know what
landed without listening to every file.

Acceptance Criteria (Gherkin -- written BEFORE code):

  Scenario: Atomic move via os.replace + dual fsync
    Given a fresh ./downloads/
    When the use case completes one track end-to-end
    Then downloads/<title>.mp3 exists
     And no .partial files are left in downloads/.tmp/
     And a manifest entry was appended to downloads/.shokz/manifest.jsonl
     And the manifest file was fsync'd AND its parent dir was fsync'd

  Scenario: SIGKILL mid-encode leaves no partial *.mp3 in downloads/
    Given a long-running download in progress
    When the shokz process is SIGKILLed during ffmpeg encoding
    Then no top-level *.mp3 file exists in downloads/
     And the manifest has NO entry for the killed track
     And on re-run the same URL is downloaded fresh (no false positive skip)

  Scenario: Post-download size check rejects 0-byte raw file
    Given a video source that returns exit 0 with a 0-byte raw file
    When the use case processes the track
    Then it raises SourceFileCorrupt
     And no .mp3 lands in downloads/
     And a FailureEntry is recorded in downloads/.shokz/failures.jsonl

  Scenario: Post-encode duration check rejects truncated audio
    Given a successful download (raw is correct duration, e.g. 60 s)
      And ffmpeg "succeeds" (exit 0) but encodes only 30 s
    When the use case probes the encoded duration
    Then it raises EncodingFailed (deviation > 2%)
     And no .mp3 lands in downloads/
     And a FailureEntry is recorded

  Scenario: Manifest entry preserves original_title separate from filename
    Given a track titled "Soft Piano: Sleep Music!" with id "abc123"
    When it is downloaded with default template "{title}"
    Then the resulting file is downloads/Soft Piano Sleep Music.mp3
       (sanitized -- colons/exclamations stripped per Sprint 2)
     And the manifest entry stores original_title="Soft Piano: Sleep Music!"
       AND filename_stem="Soft Piano Sleep Music"

  Scenario: Manifest is JSONL with schema_version=1 per row
    Given a fresh ./downloads/
    When 3 tracks complete
    Then downloads/.shokz/manifest.jsonl has exactly 3 lines
     And each line is valid JSON containing "schema_version": 1
     And each line carries: source, track_id, original_title, filename_stem,
         mp3_path, bitrate_kbps, duration_s, downloaded_at (ISO-8601 UTC)

  Scenario: Failure of one track does not corrupt manifest of others
    Given a 3-URL batch where URL #2 raises SourceUnavailable
    When the batch completes
    Then manifest.jsonl has 2 entries (URLs #1 and #3)
     And failures.jsonl has 1 entry (URL #2)
     And both files are valid JSONL (one valid JSON per line)

  Scenario: Manifest fsync verification (unit-level)
    Given a fake fsync-tracking filesystem adapter
    When a manifest entry is appended
    Then os.fsync was called on the manifest file descriptor
     AND os.fsync was called on the manifest directory's fd

  Scenario: Atomic-write integrity (unit-level)
    Given a use case with a real LocalFileSystem and FfmpegEncoder fakes
    When a track is processed
    Then the final .mp3 path is created via os.replace from .tmp/.partial
     And os.fsync is called on the final file
     AND os.fsync is called on the final file's parent directory

  Scenario: Use case integrity -- unit-level with fakes
    Given a fake source returning track.duration_s=60
      And a fake encoder that probes duration=58 (within 2%)
    When the use case processes the track
    Then status is SUCCESS

    Given the same fake source
      And a fake encoder that probes duration=30 (50% short)
    When the use case processes the track
    Then status is FAILED with error matching /duration|truncated|2%/

Non-functional:
  - Atomic move: os.replace on same filesystem (always -- .tmp/ is INSIDE
    downloads/ per plan §3 layout)
  - Manifest fsync: file fd + parent dir fd, both called explicitly
  - Integrity: post-download raw size >= MIN_RAW_BYTES (1024); post-encode
    duration within 2% of resolved track.duration_s
  - Manifest schema_version=1 baked into every entry (Sprint 5+ migrations
    will check this)
  - JSONL append-only -- no rewrites, no compaction in Sprint 4
  - Failure log mirror: same shape, status="failed"

Out of scope (defer to listed sprint):
  - skip_existing logic                 -> Sprint 4.5 (next)
  - Reconciliation scan (orphan files)  -> Sprint 4.5
  - library list / show / verify        -> Sprint 4.5 / Sprint 9
  - Cross-process file lock             -> Sprint 8
  - Disk guard pre-check                -> Sprint 8
  - Signal handling (CancelledError)    -> Sprint 8
  - Retry policy                        -> Sprint 7
  - Cookie quality guard                -> Sprint 6 (lands with progress)

INVEST: Independent (Sprint 3 unblocks), Negotiable, Valuable (this is the
        load-bearing prod-grade claim of v0.4.0+), Estimable (½ day per plan),
        Small-ish, Testable (10 Gherkin scenarios above)
```

## Definition of Ready (DoR) — checked

- [x] Sprint Goal written
- [x] User Story with Gherkin AC (10 scenarios)
- [x] Affected files listed (see "Files to land")
- [x] Ports/contracts named: `ManifestPort`, `FileSystemPort` (both new outbound Protocols)
- [x] Test approach: unit (manifest fsync via fake FS, integrity checks via fake encoder); CLI smoke (manifest appears after `shokz download`); acceptance (Gherkin → integration); SIGKILL via `just kill-test`
- [x] Dependencies on prior sprints: Sprint 3 v0.3.0 ✓
- [x] Out-of-scope list written
- [x] Estimated ½ day

## Files to land in Sprint 4

### domain/
- `src/shokz/domain/models.py` — extend `Track` with `original_title`; add `ManifestEntry`, `FailureEntry` dataclasses
- `src/shokz/domain/errors.py` — add `SourceFileCorrupt`, `ManifestInconsistent`

### application/ports/outbound/
- `src/shokz/application/ports/outbound/manifest.py` — `ManifestPort` (record + record_failure)
- `src/shokz/application/ports/outbound/filesystem.py` — `FileSystemPort` (atomic_move, fsync_dir, mkdir, exists)

### adapters/outbound/
- `src/shokz/adapters/outbound/jsonl_manifest.py` — `JsonlManifest` (append + flush + fsync(fd) + fsync(dir))
- `src/shokz/adapters/outbound/local_filesystem.py` — `LocalFileSystem` (atomic_move via os.replace + fsync chain)

### application/use_cases/
- Update `batch_download.py` —
  - inject `ManifestPort` and `FileSystemPort`
  - post-download size check
  - post-encode duration probe + 2% tolerance check
  - atomic move via FileSystemPort (not raw `os.replace`)
  - record manifest entry after successful move
  - record failure entry on any per-track failure path

### composition root
- Update `composition.py` — wire `JsonlManifest` and `LocalFileSystem` into the use case; manifest paths come from config (`downloads/.shokz/manifest.jsonl` derived from `output_dir`)

### tests/
- `tests/unit/adapters/__init__.py`
- `tests/unit/adapters/test_jsonl_manifest.py` — fsync verification (track os.fsync calls)
- `tests/unit/adapters/test_local_filesystem.py` — atomic move + fsync chain
- `tests/unit/application/test_batch_download.py` — extend with integrity-check tests + manifest-recorded tests
- `tests/acceptance/test_sprint_4_atomic.py` — Gherkin scenarios; INTEGRATION-gated network tests + SIGKILL test wrapper

### Process
- `just sprint-review 4` to verify Gherkin↔test name coverage
- `just code-review v0.3.0` to dispatch reviewers (DoD ratchet)
- `just kill-test <URL>` to verify atomic-write protocol with real SIGKILL

## Definition of Done (DoD) — verify before close

- [ ] All 10 Gherkin AC scenarios pass as pytest tests
- [ ] `just sprint-review 4` passes
- [ ] `just code-review v0.3.0` brief generated; reviewers dispatched; convergent + unique findings either fixed OR explicitly deferred-with-reason
- [ ] **`just kill-test <URL>` passes** (NEW DoD ratchet item from Sprint 3 retro)
- [ ] **Atomic-write integration test passes** (kill mid-write, no partial files) (Sprint 4+ DoD ratchet from plan §0.5)
- [ ] **Integrity check verified** (encoded duration within 2% of source) (Sprint 4+ DoD ratchet from plan §0.5)
- [ ] **Manifest fsync verified** (unit test confirms `os.fsync` is called on fd AND parent dir) (Sprint 4+ DoD ratchet from plan §0.5)
- [ ] `just lint / format / typecheck` clean
- [ ] `just test` green; coverage ≥ 80% on touched layers
- [ ] CHANGELOG.md `[Unreleased]` → `[0.4.0]`
- [ ] README.md updated to mention manifest/.shokz layout + atomic-write guarantee
- [ ] Conventional Commits: `feat(manifest): atomic writes + JSONL manifest + integrity checks (Sprint 4)`
- [ ] Self-demo from clean state: `shokz download URL` produces .mp3 + manifest entry + no .partial files
- [ ] Self-demo: SIGKILL via `just kill-test` shows no partial *.mp3
- [ ] Git tag pushed: `v0.4.0`
- [ ] Retro entry appended to RETRO.md
