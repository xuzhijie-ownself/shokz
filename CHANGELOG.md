# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
