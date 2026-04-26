# Sprint 5 — Source resolution + playlists

**Date:** 2026-04-27
**Tag target:** `v0.6.0`
**Effort:** ~½ day

## Sprint Goal

The swimmer can paste a YouTube playlist URL, optionally land tracks in a per-playlist subdirectory, and large playlists prompt before downloading.

## User Story

```
Title: Download a YouTube playlist with one command

As a Swimmer who curates "Pool Sleep Mix" as a YouTube playlist, I want
to run `shokz playlist <playlist-url>` and get every video as an MP3 in
a tidy subfolder, so I can drop the whole folder onto my Shokz at once.

Acceptance Criteria (Gherkin -- written BEFORE code):

  Scenario: Playlist URL expands to N video URLs
    Given a YouTube playlist URL with 3 videos
    When I run `shokz playlist <playlist URL>`
    Then 3 MP3 files land under downloads/<sanitized playlist title>/
     And the manifest has 3 entries (one per video)
     And each entry's mp3_path is relative to output_dir (includes the subdir)
     And exit code is 0

  Scenario: --no-playlist-subdir lands files at the top level
    Given a YouTube playlist URL with 3 videos
    When I run `shokz playlist --no-playlist-subdir <URL>`
    Then 3 MP3 files land directly in downloads/ (no subfolder)
     And manifest entries have flat mp3_path values

  Scenario: Large playlist (>= threshold) prompts for confirmation
    Given a playlist URL with 60 videos
      AND --confirm-threshold 50 (default)
    When I run `shokz playlist <URL>` with stdin closed (no human present)
    Then the command exits non-zero without downloading
     And stderr explains the threshold and how to override (--yes / -y)

  Scenario: --yes bypasses the large-playlist confirmation
    Given a playlist URL with 60 videos
    When I run `shokz playlist --yes <URL>`
    Then no prompt is issued
     And the download proceeds (gated by skip-existing as usual)

  Scenario: Playlist resolution rejects a non-playlist URL with clear error
    Given a single-video URL (not a playlist)
    When I run `shokz playlist <single-video URL>`
    Then exit code is non-zero
     And stderr says the URL is not a playlist
     And no files are created

  Scenario: Reconciliation walks subdirectories (Sprint 4.5 retro DoD ratchet)
    Given downloads/My Playlist/Track A.mp3 exists with a manifest entry
        whose mp3_path is "My Playlist/Track A.mp3"
      AND no orphan files
    When I run `shokz library verify`
    Then exit code is 0
     And the report shows 1 OK, 0 orphan files, 0 orphan entries
     (BEFORE this sprint, the flat scan would have flagged the file as orphan)

  Scenario: Reconciliation reports orphan in subdirectory
    Given downloads/My Playlist/Mystery.mp3 exists
      AND no manifest entry references it
    When I run `shokz library verify`
    Then exit code is non-zero
     And stderr lists "My Playlist/Mystery.mp3" as an orphan file

  Scenario: Reconciliation excludes .tmp/ and .shokz/ from scan
    Given downloads/.tmp/foo.partial exists
      AND downloads/.shokz/manifest.jsonl exists
    When I run `shokz library verify`
    Then those files are NEITHER reported as orphan
     (these are state files, not user content)

  Scenario: Skip-existing with --no-playlist-subdir respects manifest match
    Given a playlist with 1 video, downloaded once with --no-playlist-subdir
    When I re-run with the same playlist + same flag
    Then the video is SKIPPED (1 SKIP, 0 succeeded)
     And no new manifest entry is appended

  Scenario: ExpandPlaylistUseCase -- unit-level
    Given a fake source whose resolve_playlist returns 3 entry URLs
    When ExpandPlaylistUseCase.execute is called
    Then it returns the 3 URLs
     And the source's resolve_playlist was called exactly once

  Scenario: Playlist subdir respects FAT-safe sanitization (long unicode title)
    Given a playlist titled "8 Hours -- Sleep Music"
    When I run `shokz playlist <URL>`
    Then the subdir is "8 Hours -- Sleep Music" (or sanitized variant)
     AND the path is FAT/exFAT-safe (no <>:?*\\|" chars)

Non-functional:
  - Playlist resolution uses yt_dlp.YoutubeDL with extract_flat=True so we
    DON'T pay the per-video metadata cost during expansion (just URL list).
  - Per-video metadata is fetched via the existing single-video resolve()
    in the use case (one extract_info per item; concurrency-bounded).
  - >=50 default threshold for confirmation; configurable via
    --confirm-threshold or sources.youtube.playlist_confirm_threshold.
  - Manifest mp3_path stays RELATIVE TO output_dir (top-level), so
    Sprint 4.5 reconciliation paths work with subdirs.

Out of scope (defer to listed sprint):
  - Cross-source playlists (e.g. SoundCloud)              -> when source added
  - Cookie-gated playlists (members-only)                 -> later
  - Retry on partial playlist failures                    -> Sprint 7
  - Resume partial playlist downloads                     -> Sprint 4.5 already
                                                              gives skip-existing,
                                                              so this is "free"
  - Playlist as a single ManifestEntry "album"            -> v2 if requested

INVEST: Independent (Sprint 4.5 unblocks), Negotiable, Valuable (the swimmer's
        primary use case once they have a playlist), Estimable (½ day per
        plan), Small-ish, Testable (11 Gherkin scenarios above)
```

## Definition of Ready (DoR) — checked

- [x] Sprint Goal written
- [x] User Story with Gherkin AC (11 scenarios)
- [x] Affected files listed
- [x] Ports/contracts named: extend `VideoSourcePort` with `resolve_playlist(url) -> tuple[str, ...] | None` (None = not a playlist URL)
- [x] Test approach: unit (ExpandPlaylistUseCase, reconciliation subdir walk); CLI smoke (playlist command); acceptance (Gherkin → integration with a real public playlist)
- [x] Dependencies on prior sprints: Sprint 4.5 v0.5.0 ✓
- [x] Out-of-scope list written
- [x] Estimated ½ day

## Files to land in Sprint 5

### domain/
- `domain/models.py` — `PlaylistInfo` (id, title, item_urls) — or just inline tuples; will decide during implementation

### application/ports/outbound/
- `application/ports/outbound/video_source.py` — extend `VideoSourcePort` with `resolve_playlist(url) -> tuple[str, ...] | None`

### application/use_cases/
- `application/use_cases/expand_playlist.py` — `ExpandPlaylistUseCase`
- Update `batch_download.py` — `BatchDownloadInput.target_dir: Path | None` (where files land; defaults to `output_dir`); manifest paths stay relative to `output_dir`

### application/policies/
- Update `reconciliation.py` — `rglob("*.mp3")` excluding `.tmp/` and `.shokz/` (Sprint 4.5 retro DoD ratchet)

### adapters/outbound/
- `adapters/outbound/ytdlp_source.py` — implement `resolve_playlist` via `yt_dlp.YoutubeDL(extract_flat=True)`

### adapters/inbound/cli/commands/
- `adapters/inbound/cli/commands/playlist.py` — `shokz playlist URL` Typer command
- `adapters/inbound/cli/app.py` — register `playlist` command

### config
- `config/schema.py` — `[sources.youtube] playlist_confirm_threshold: int = 50`

### composition root
- `composition.py` — wire `ExpandPlaylistUseCase`

### tests/
- `tests/unit/application/test_expand_playlist.py`
- `tests/unit/application/test_reconciliation.py` — extend with subdir scan tests
- `tests/acceptance/test_sprint_5_playlists.py` — Gherkin scenarios (real public playlist, gated by INTEGRATION=1)

## Definition of Done (DoD) — verify before close

- [ ] All 11 Gherkin AC scenarios pass as pytest tests
- [ ] `just sprint-review 5` passes
- [ ] `just code-review v0.5.0` brief generated; reviewers dispatched; convergent + unique findings either fixed OR explicitly deferred-with-reason
- [ ] `just lint / format / typecheck` clean
- [ ] `just test` green; coverage ≥ 80%
- [ ] **Atomic-write protocol still holds** (`just kill-test` Sprint 4 ratchet)
- [ ] **Reconciliation handles subdirs** (Sprint 4.5 retro DoD ratchet)
- [ ] CHANGELOG.md `[Unreleased]` → `[0.6.0]`
- [ ] README.md updated to mention `shokz playlist`
- [ ] Conventional Commits: `feat(playlist): playlist URL expansion + per-playlist subdir + >N confirmation (Sprint 5)`
- [ ] Self-demo: `shokz playlist <small-public-playlist>` produces N files in subdir; re-run skips them all; `library verify` reports clean
- [ ] Git tag pushed: `v0.6.0`
- [ ] Retro entry appended to RETRO.md
