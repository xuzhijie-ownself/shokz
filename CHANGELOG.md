# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
