# Sprint 2 — Title-based filenames + `--name` override

**Date:** 2026-04-26
**Tag target:** `v0.2.0`
**Effort:** ~½ day

## Sprint Goal

Files in `./downloads/` are named after the video title (FAT/exFAT-safe), not the YouTube ID. The swimmer can override with `--name "Custom Name"` for a single URL. Collisions auto-suffix.

## User Story

```
Title: Title-based filenames + --name override

As a Swimmer who just downloaded 20 tracks, I want each .mp3 to be
named after the video title (e.g. "Soft Piano Sleep Music.mp3" -- NOT
"eiV0nvJ9fRM.mp3"), so that I can recognize and curate my pool playlist
without opening each file.

Acceptance Criteria (Gherkin -- written BEFORE code):

  Scenario: Filename defaults to sanitized video title
    Given a fresh ./downloads/
    When I run `shokz download URL` for a video titled "Soft Piano Sleep Music"
    Then the resulting file is downloads/Soft Piano Sleep Music.mp3
     And no file is named after the YouTube video ID

  Scenario: --name flag overrides the title for a single URL
    Given a fresh ./downloads/
    When I run `shokz download --name "Sleep Mix Vol 1" URL`
    Then the resulting file is downloads/Sleep Mix Vol 1.mp3

  Scenario: --name flag rejects multiple URLs
    Given any state
    When I run `shokz download --name "X" URL1 URL2`
    Then exit code is non-zero
     And stderr contains a clear message that --name requires exactly one URL
     And no downloads occur

  Scenario: Filename collision auto-suffixes (default policy)
    Given downloads/Soft Piano Sleep Music.mp3 already exists from a previous run
    When I run `shokz download URL` for a different video also titled
        "Soft Piano Sleep Music"
    Then the new file lands at downloads/Soft Piano Sleep Music (2).mp3
     And the original file is untouched

  Scenario: Path traversal in --name is rejected
    Given any state
    When I run `shokz download --name "../etc/evil" URL`
    Then exit code is non-zero
     And stderr indicates the name is invalid or escapes the output directory
     And no file is created outside ./downloads/

  Scenario: Unicode title is preserved on exFAT-friendly filesystem
    Given a fresh ./downloads/
    When I run `shokz download URL` for a Chinese-titled video
        (e.g. "10 hour relaxing piano music")
    Then the resulting file contains the unicode characters in its name
     And the file is a valid MP3

  Scenario: Empty or all-punctuation title falls back to untitled-{id}
    Given any state
    When the resolved Track has title "punct only" or "..." or ""
    Then the filename stem becomes "untitled-{video_id}"
     And the .mp3 file is created successfully

  Scenario: Sanitizer property -- every title produces a non-empty FAT-safe stem
    For ANY title input (random unicode, ASCII control chars, path separators,
        Windows reserved names like CON/LPT1, all-punctuation, very long strings)
    The sanitizer returns a non-empty string
      with no characters from < > : " / \\ | ? * \\x00-\\x1f
      with no leading/trailing dots or spaces
      not equal to any FAT-reserved name (case-insensitive)
      and within max_length bytes (UTF-8)

  Scenario: Filename resolver -- unit-level with fakes
    Given a FilenameResolver with template "{title}", suffix policy
      and a track with title "Foo" and no existing collision
    When resolve() is called
    Then the returned path is output_dir/Foo.mp3

    Given the same resolver and 3 prior collisions named "Foo.mp3", "Foo (2).mp3", "Foo (3).mp3"
    When resolve() is called for a new "Foo" track
    Then the returned path is output_dir/Foo (4).mp3

Non-functional:
  - Sanitizer is a PURE function (no I/O), unit-tested with property-based
    cases for the edge bullets above.
  - Resolver is also pure given a `(path) -> bool` exists callback.
  - Path-traversal guard rejects: absolute paths, "..", any path whose resolved
    parent != output_dir.
  - Sanitization preserves unicode (FAT32 supports VFAT long names; exFAT is unicode-native).

Out of scope (defer to listed sprint):
  - Configurable filename TEMPLATE (e.g. "{uploader} - {title}")  -> Sprint 3 (config)
  - Other collision policies (overwrite | skip | fail)             -> Sprint 3 (config)
  - Manifest entry preservation of original_title separately      -> Sprint 4
  - `original_title` vs `filename_stem` divergence detection      -> Sprint 4
  - Skip-existing by manifest match (vs filename match)            -> Sprint 4.5
  - Reconciliation scan for orphan files                          -> Sprint 4.5

INVEST: Independent (Sprint 1 unblocks), Negotiable, Valuable (user's #1 ask),
        Estimable (½ day per plan), Small, Testable (Gherkin above)
```

## Definition of Ready (DoR) — checked

- [x] Sprint Goal written
- [x] User Story with Gherkin AC (9 scenarios)
- [x] Affected files listed (see "Files to land")
- [x] Ports/contracts named: no new ports (pure domain + new policy module)
- [x] Test approach: unit (sanitizer + resolver pure-function tests, property-based);
      CLI smoke (`--name` validation); acceptance (Gherkin → integration with real downloads)
- [x] Dependencies on prior sprints: Sprint 1 v0.1.0 ✓
- [x] Out-of-scope list written
- [x] Estimated ½ day

## Files to land in Sprint 2

### domain/ (pure)
- `src/shokz/domain/filenames.py` — `sanitize_filename`, `render_template`, FAT-reserved-name check (wraps `pathvalidate`)
- `src/shokz/domain/paths.py` — `is_path_within`, `assert_within` for traversal guard
- `src/shokz/domain/errors.py` — extend with `NameOutsideOutputDir`, `FilenameCollision`, `NameAmbiguous`

### application/policies/
- `src/shokz/application/policies/__init__.py`
- `src/shokz/application/policies/filename_resolver.py` — `FilenameResolver` (template + override + collision)

### application/use_cases/
- Update `batch_download.py` — call `FilenameResolver.resolve()` instead of hard-coded `f"{track.id}.mp3"`. Surface override via `BatchDownloadInput.name_override: str | None`.

### adapters/inbound/cli/
- Update `commands/download.py` — add `--name` flag with single-URL guard

### composition root
- Update `composition.py` — wire `FilenameResolver` into `BatchDownloadUseCase`

### tests/
- `tests/unit/domain/__init__.py`
- `tests/unit/domain/test_filenames.py` — property-based tests for sanitizer
- `tests/unit/application/test_filename_resolver.py` — collision suffix logic
- `tests/unit/test_cli_smoke.py` — extend with `--name` flag tests
- `tests/acceptance/test_sprint_2_filenames.py` — Gherkin scenarios as pytest

## Definition of Done (DoD) — verify before close

- [ ] All 9 Gherkin AC scenarios pass as pytest tests
- [ ] `just sprint-review 2` passes (mechanical Gherkin↔test name check from Sprint 1 retro)
- [ ] `just lint` clean
- [ ] `just typecheck` clean (mypy --strict)
- [ ] `just test` green; coverage ≥ 80% on touched `src/shokz/{domain,application}/`
- [ ] CHANGELOG.md `[Unreleased]` → `[0.2.0]`
- [ ] README.md Use section: `--name "..."` example added
- [ ] Conventional Commits: `feat(filenames): title-based names + --name override (Sprint 2)`
- [ ] Self-demo from clean `./downloads/`: `shokz download URL` produces a title-named file (verify NOT `{id}.mp3`)
- [ ] Self-demo collision: same URL twice → `(2)` suffix on second
- [ ] Self-demo `--name`: single-URL produces named file; multi-URL with `--name` exits non-zero
- [ ] Git tag pushed: `v0.2.0`
- [ ] Retro entry appended to RETRO.md
