# Sprint 4.5 — Skip-existing + reconciliation + library list/show/verify

**Date:** 2026-04-27
**Tag target:** `v0.5.0`
**Effort:** ~½ day

## Sprint Goal

Re-running `shokz download` on already-completed URLs is near-instant (manifest-driven skip); orphan files (`*.mp3` on disk with no manifest entry) and orphan entries (manifest entry with no file) are surfaced — never silently ignored. The Sprint 4 SF-4 orphan-state window now has a recovery story.

## User Story

```
Title: Skip already-downloaded + reconcile orphans + browse library

As a Swimmer who runs `shokz download` on the same 50-URL playlist every
week, I want shokz to skip URLs I already have (instant, no re-encode),
and I want a `shokz library` command so I can see what's downloaded and
catch files that crashed mid-process or got manually deleted.

Acceptance Criteria (Gherkin -- written BEFORE code):

  Scenario: Skip-existing by manifest match short-circuits the download
    Given downloads/ contains "Foo.mp3" with a manifest entry for
        (youtube, abc123)
    When I run `shokz download <URL for abc123>`
    Then the use case's source.download_audio is NOT called
     And the result reports 1 SKIPPED, 0 succeeded, 0 failed
     And exit code is 0

  Scenario: --force overrides skip-existing
    Given the same setup as above
    When I run `shokz download --force <URL for abc123>`
    Then download_audio IS called
     And the file is re-encoded (collision suffix policy applies)

  Scenario: Skip-existing requires BOTH manifest entry AND file on disk
    Given a manifest entry for (youtube, abc123) BUT downloads/Foo.mp3 was
        manually deleted
    When I run `shokz download <URL for abc123>`
    Then the use case re-downloads (manifest-only is not enough)
     And the result reports 1 succeeded
     And the manifest now has 2 entries for that track_id (append-only)

  Scenario: shokz library list shows manifest entries as a table
    Given downloads/.shokz/manifest.jsonl has 3 entries
    When I run `shokz library list`
    Then output shows a table with columns: title, source, id, bitrate,
        duration, downloaded_at
     And exit code is 0

  Scenario: shokz library show TRACK_ID prints one entry's full detail
    Given a manifest entry for track_id=abc123
    When I run `shokz library show abc123`
    Then output shows all manifest fields including original_title,
        filename_stem, mp3_path, bitrate_kbps, duration_s, downloaded_at
     And exit code is 0

  Scenario: shokz library show on missing track_id exits non-zero
    Given no manifest entry for "no_such_track"
    When I run `shokz library show no_such_track`
    Then exit code is non-zero
     And stderr says no entry found

  Scenario: shokz library verify with clean state exits 0
    Given downloads/Foo.mp3 exists AND a manifest entry exists for it
      AND no other *.mp3 files in downloads/
    When I run `shokz library verify`
    Then exit code is 0
     And output reports 1 OK, 0 orphan files, 0 orphan entries

  Scenario: shokz library verify reports orphan files (on disk, not in manifest)
    Given downloads/Mystery.mp3 exists
      AND no manifest entry references "Mystery.mp3"
    When I run `shokz library verify`
    Then exit code is non-zero
     And stderr lists "Mystery.mp3" as an orphan file
     And the message suggests possible cause (e.g. "process killed
        between os.replace and manifest record -- Sprint 4 SF-4 window")

  Scenario: shokz library verify reports orphan entries (manifest, not on disk)
    Given a manifest entry for track_id=abc123 with mp3_path="Foo.mp3"
      AND downloads/Foo.mp3 was manually deleted
    When I run `shokz library verify`
    Then exit code is non-zero
     And stderr lists Foo.mp3 as an orphan manifest entry

  Scenario: Reconciliation startup scan surfaces orphan files as WARNING
    Given downloads/Mystery.mp3 exists with no manifest entry
    When I run `shokz download <some new URL>` (any new download)
    Then a WARNING is logged once, listing Mystery.mp3 as an orphan
     And the new URL still downloads normally (warning is non-blocking)

  Scenario: Skip-existing policy -- unit-level
    Given a SkipExistingPolicy with a fake manifest containing
        ("youtube", "abc123") -> "downloads/Foo.mp3"
      AND a fake filesystem reporting "downloads/Foo.mp3" exists
    When the policy is queried for ("youtube", "abc123")
    Then it returns SKIPPED with the existing path

    Given the same fake manifest BUT the fake filesystem says
        "downloads/Foo.mp3" does NOT exist
    When the policy is queried for ("youtube", "abc123")
    Then it returns RE_DOWNLOAD (manifest entry stale)

  Scenario: Reconciliation policy -- unit-level
    Given a fake manifest with entries A, B, C
      AND a fake filesystem listing A.mp3, B.mp3, X.mp3 (X not in manifest)
    When reconciliation runs
    Then orphan_files == [X.mp3]  (on disk, not in manifest)
     And orphan_entries == [C]    (in manifest, not on disk)
     And ok_pairs == [(A, A.mp3), (B, B.mp3)]

Non-functional:
  - Skip decision must be < 50ms for a 1000-entry manifest (linear scan
    through JSONL is fine at this size; SQLite migration is v2 territory)
  - Manifest is read every `shokz` invocation; the read is async via
    asyncio.to_thread so the event loop stays responsive
  - Reconciliation startup scan uses asyncio.create_task so the
    download path doesn't block on the scan
  - --force semantics: bypass skip-existing AND let the collision-suffix
    policy resolve the filename (Foo.mp3 -> Foo (2).mp3)

Out of scope (defer to listed sprint):
  - retry policy for the download flow                -> Sprint 7
  - Rich progress bars for library list                -> Sprint 6
  - cross-process file lock around manifest read+write -> Sprint 8
  - SQLite manifest backend                            -> v2 (NOT in v1.0)
  - shokz library export / import                      -> deferred
  - manifest schema migration story                    -> Sprint 5+ when
    schema_version != 1 actually happens

INVEST: Independent (Sprint 4 unblocks), Negotiable, Valuable (this is
        the swimmer's daily UX win), Estimable (½ day per plan),
        Small-ish, Testable (12 Gherkin scenarios above)
```

## Definition of Ready (DoR) — checked

- [x] Sprint Goal written
- [x] User Story with Gherkin AC (12 scenarios)
- [x] Affected files listed
- [x] Ports/contracts named: extend `ManifestPort` with read API (`find_by_track`, `iter_all`)
- [x] Test approach: unit (skip-existing + reconciliation policies with fakes); CLI smoke (library subcommands); acceptance (Gherkin → integration with real downloads + manual deletes)
- [x] Dependencies on prior sprints: Sprint 4 v0.4.0 ✓
- [x] Out-of-scope list written
- [x] Estimated ½ day

## Files to land in Sprint 4.5

### domain/
- `src/shokz/domain/models.py` — small additions if needed (probably none; ManifestEntry already covers it)

### application/ports/outbound/
- `src/shokz/application/ports/outbound/manifest.py` — extend `ManifestPort` with `find_by_track(source, track_id) -> ManifestEntry | None` and `iter_all() -> AsyncIterator[ManifestEntry]`

### adapters/outbound/
- `src/shokz/adapters/outbound/jsonl_manifest.py` — implement `find_by_track` and `iter_all` (linear scan; cache last read with mtime invalidation if needed for perf)

### application/policies/
- `src/shokz/application/policies/skip_existing.py` — `SkipExistingPolicy` (manifest + filesystem inputs)
- `src/shokz/application/policies/reconciliation.py` — `ReconciliationPolicy` (returns `(ok, orphan_files, orphan_entries)`)

### application/use_cases/
- `src/shokz/application/use_cases/batch_download.py` — call `SkipExistingPolicy` early in `_process_one`; trigger startup reconciliation scan as a fire-and-forget WARNING task in `execute`
- `src/shokz/application/use_cases/library_query.py` — `ListLibraryUseCase`, `ShowLibraryUseCase`, `VerifyLibraryUseCase`

### adapters/inbound/cli/commands/
- `src/shokz/adapters/inbound/cli/commands/library_cmd.py` — Typer subapp: list, show, verify
- Update `app.py` — register `library` subcommand group
- Update `download.py` — add `--force` flag (was Sprint 1 placeholder; now real)

### composition root
- Update `composition.py` — wire `SkipExistingPolicy` + `ReconciliationPolicy` + library use cases

### tests/
- `tests/unit/application/test_skip_existing.py`
- `tests/unit/application/test_reconciliation.py`
- `tests/unit/adapters/test_jsonl_manifest.py` — extend with read-API tests
- `tests/unit/test_cli_smoke.py` — extend with library subcommand smoke
- `tests/acceptance/test_sprint_4_5_library.py` — Gherkin scenarios

### Process
- `just sprint-review 4.5` to verify Gherkin↔test name coverage
- `just code-review v0.4.0` to dispatch reviewers (DoD ratchet)

## Definition of Done (DoD) — verify before close

- [ ] All 12 Gherkin AC scenarios pass as pytest tests
- [ ] `just sprint-review 4.5` passes
- [ ] `just code-review v0.4.0` brief generated; reviewers dispatched; convergent + unique findings either fixed OR explicitly deferred-with-reason
- [ ] `just lint / format / typecheck` clean (mypy --strict)
- [ ] `just test` green; coverage ≥ 80% on touched layers
- [ ] **Atomic-write protocol still holds** (Sprint 4 ratchet — `just kill-test` still passes)
- [ ] **Reconciliation scan integration test passes** (NEW Sprint 4.5+ DoD ratchet from plan §0.5)
- [ ] CHANGELOG.md `[Unreleased]` → `[0.5.0]`
- [ ] README.md updated to mention `library` subcommands + skip-existing
- [ ] Conventional Commits: `feat(library): skip-existing + reconciliation + library list/show/verify (Sprint 4.5)`
- [ ] Self-demo: download → re-download (must skip in <1s) → manually delete file → re-download (must re-encode) → `library verify` reports orphan
- [ ] Git tag pushed: `v0.5.0`
- [ ] Retro entry appended to RETRO.md
