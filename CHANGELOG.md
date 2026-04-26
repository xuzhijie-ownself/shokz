# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
