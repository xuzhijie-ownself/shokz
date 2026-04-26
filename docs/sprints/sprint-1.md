# Sprint 1 — POC parity in hexagonal shell

**Date:** 2026-04-26
**Tag target:** `v0.1.0` (MVP)
**Effort:** ~1 day (per plan §8 — only sprint budgeted at 1 day)

## Sprint Goal

A swimmer can run `shokz download <URL>` and get a playable MP3 in `./downloads/`.

## User Story

```
Title: Download one YouTube video to MP3

As a Swimmer with a YouTube URL, I want to run a single command and get
a playable MP3 file in ./downloads/, so that I can copy it to my Shokz
headphones and listen while swimming.

Acceptance Criteria (Gherkin — these become pytest test names):

  Scenario: Single short video downloads to playable MP3
    Given a fresh ./downloads/ directory
      And the conda env "shokz" is active with deps installed
    When I run `shokz download "https://www.youtube.com/watch?v=jNQXAC9IVRw"`
    Then exit code is 0
     And exactly one file matching downloads/*.mp3 exists
     And the file is a valid MP3 (file --mime-type reports audio/mpeg)
     And ffprobe reports duration matching the source within 2 seconds

  Scenario: Multiple URLs download concurrently
    Given a fresh ./downloads/
    When I run `shokz download URL1 URL2 URL3` with 3 distinct short videos
    Then exit code is 0
     And exactly 3 files matching downloads/*.mp3 exist
     And the wall-clock total is less than 3x the longest single download
       (proves concurrency rather than serial execution)

  Scenario: Invalid URL fails cleanly
    Given a fresh ./downloads/
    When I run `shokz download "https://www.youtube.com/watch?v=000000XXXXX"`
    Then exit code is non-zero
     And stderr contains a recognizable failure message (not a Python traceback)
     And no .mp3 files are created in downloads/

  Scenario: Mixed valid + invalid URLs — partial success
    Given a fresh ./downloads/
    When I run `shokz download VALID_URL INVALID_URL`
    Then exit code is non-zero (because at least one failed)
     And exactly 1 file matching downloads/*.mp3 exists (the valid one)
     And stderr lists the failed URL

  Scenario: Use case orchestration — unit-level
    Given a BatchDownloadUseCase wired with FAKE source + encoder + progress
    When I execute it with 3 URLs
    Then the source resolve was called 3 times
     And the encoder.encode was called 3 times
     And the result reports 3 succeeded, 0 failed
     And the encoder received the raw paths emitted by the source

Non-functional:
  - Concurrency bound = 3 (hard-coded in Sprint 1; configurable Sprint 3)
  - File extension is .mp3
  - Filename is `{video_id}.mp3` (Sprint 1 simplification — title-based in Sprint 2)
  - Raw downloads land in downloads/.tmp/ (cleaned post-encode unless --keep-raw)
  - .tmp/ is created if missing
  - Atomic move of finished MP3 from .tmp/ → downloads/ via os.replace
    (full crash-safety guarantees come Sprint 4)

Out of scope (defer to listed sprint):
  - Title-based filenames                  → Sprint 2
  - --name override flag                   → Sprint 2
  - Filename collision policies            → Sprint 2
  - Configuration (TOML/env/CLI overrides) → Sprint 3 (defaults hard-coded)
  - Manifest + skip-existing               → Sprint 4 / 4.5
  - Reconciliation scan                    → Sprint 4.5
  - Playlist URL expansion                 → Sprint 5
  - Rich progress UI                       → Sprint 6 (NULL reporter only)
  - ID3 tagging                            → Sprint 6
  - Retry policy + error translation table → Sprint 7
  - Bitrate cap-to-source                  → Sprint 7
  - Disk guard + lock + signal handling    → Sprint 8
  - Doctor + library verify                → Sprint 9
  - Cookies                                → not yet (only if specific video needs it)

INVEST: Independent (no prior sprint code), Negotiable, Valuable (MVP),
        Estimable (1 day per plan), Small-ish, Testable (Gherkin above)
```

## Definition of Ready (DoR) — checked

- [x] Sprint Goal written
- [x] User Story with Gherkin AC
- [x] Affected files listed (see "Files to land" below)
- [x] Ports/contracts named: `VideoSourcePort`, `AudioEncoderPort`, `ProgressReporterPort`
- [x] Test approach noted: unit (use case + fakes), CLI smoke (Typer CliRunner), acceptance (Gherkin → pytest, gated by INTEGRATION=1 for real network)
- [x] Dependencies on prior sprints: Sprint 0 green CI ✓
- [x] Out-of-scope list written
- [x] Estimated 1 day (per plan)

## Files to land in Sprint 1

### domain/ (pure)
- `src/shokz/domain/__init__.py`
- `src/shokz/domain/models.py` — `Track`, `AudioSpec`, `RawDownload`, `EncodedFile`, `TrackStatus`, `TrackResult`
- `src/shokz/domain/errors.py` — `ShokzError` + minimal taxonomy (full taxonomy lands Sprint 7)
- `src/shokz/domain/presets.py` — `SWIM_LOW`, `SWIM_STANDARD`, `SWIM_HIGH` constants

### application/ports/outbound/
- `src/shokz/application/__init__.py`
- `src/shokz/application/ports/__init__.py`
- `src/shokz/application/ports/outbound/__init__.py`
- `src/shokz/application/ports/outbound/video_source.py` — `VideoSourcePort` Protocol
- `src/shokz/application/ports/outbound/encoder.py` — `AudioEncoderPort` Protocol
- `src/shokz/application/ports/outbound/progress.py` — `ProgressReporterPort` Protocol

### application/use_cases/
- `src/shokz/application/use_cases/__init__.py`
- `src/shokz/application/use_cases/batch_download.py` — `BatchDownloadUseCase`

### adapters/outbound/
- `src/shokz/adapters/__init__.py`
- `src/shokz/adapters/outbound/__init__.py`
- `src/shokz/adapters/outbound/ytdlp_source.py` — resolve via `yt_dlp.YoutubeDL` Python lib, download via subprocess
- `src/shokz/adapters/outbound/ffmpeg_encoder.py` — subprocess
- `src/shokz/adapters/outbound/null_progress.py`

### adapters/inbound/cli/
- `src/shokz/adapters/inbound/__init__.py`
- `src/shokz/adapters/inbound/cli/__init__.py`
- `src/shokz/adapters/inbound/cli/app.py` — Typer root + `run()` entry
- `src/shokz/adapters/inbound/cli/commands/__init__.py`
- `src/shokz/adapters/inbound/cli/commands/download.py`

### composition root
- `src/shokz/composition.py`

### tests/
- `tests/fakes.py` — `FakeVideoSource`, `FakeAudioEncoder`, `FakeProgressReporter`
- `tests/unit/__init__.py`, `tests/unit/application/__init__.py`
- `tests/unit/application/test_batch_download.py`
- `tests/unit/test_cli_smoke.py` — Typer `CliRunner` smoke
- `tests/acceptance/test_sprint_1_download.py` — Gherkin scenarios; `@pytest.mark.integration`

## Definition of Done (DoD) — verify before close

- [ ] All Gherkin AC scenarios pass as pytest tests
- [ ] `just lint` clean
- [ ] `just typecheck` clean (mypy --strict)
- [ ] `just test` green; coverage ≥ 80% on touched `src/shokz/{domain,application}/`
- [ ] CHANGELOG.md `[Unreleased]` updated → release `[0.1.0]`
- [ ] README.md usage section reflects working `shokz download URL`
- [ ] Conventional Commits: `feat: shokz download URL produces a playable MP3 (Sprint 1, MVP)`
- [ ] Self-demo: `rm -rf downloads/* && shokz download <URL>` produces the MP3
- [ ] Git tag pushed: `v0.1.0`
- [ ] Retro entry appended to RETRO.md
