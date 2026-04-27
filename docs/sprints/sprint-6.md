# Sprint 6 — Sequential by default + Sprint 5 F1 follow-up

**Date:** 2026-04-27
**Tag target:** `v0.7.0`
**Effort:** ~½ hour

## Sprint Goal

A swimmer running `shokz download <URL>` gets sequential, predictable behavior by default. Power users who curate large playlists can opt back into limited in-process parallelism via `--concurrency`. The `playlist` CLI no longer makes a redundant network round-trip for the playlist title.

## Background — why this is tiny

The original Sprint 6 plan ("Rich progress + ID3 tagging + cookie-quality guard") was split into 6a/6b after a GAN review found 5 convergent HIGH issues (cross-thread Rich/asyncio hazards, Protocol extension under runtime_checkable, etc.). Sprint 6a was started, then the user decided the project should stay strictly CLI with no live progress UI and no in-process concurrency.

A second GAN review interrogated "drop concurrency, recommend shell parallelism" and found THREE HIGH correctness bugs that would ship if we recommended multi-process invocation against the same `--output`:

1. **JSONL append corruption beyond PIPE_BUF** — `jsonl_manifest.py:31` already documents single-process-only; `O_APPEND` is atomic only ≤ 512 B on macOS, and a long entry exceeds that, allowing kernel-level write interleaving.
2. **Cross-process filename-resolver TOCTOU** — two URLs with titles that sanitize to the same stem silently overwrite, both manifest rows point to one file, the lost track is invisible to `library verify`.
3. **`.tmp/<track_id>.webm` clobber** — same URL in two shells truncates the in-flight raw file; encoder gets a partially-written webm; size + duration tolerance can pass a near-end truncation.

Plus the playlist regression: a 60-track playlist at the prior `concurrency=3` default took ~1.5 h; sequential = ~5 h, and shell parallelism is unavailable (it's one URL).

Conclusion: don't drop concurrency. **Drop only the default**, keep the flag (capped lower) as the escape hatch, defer safe shell parallelism to Sprint 8 (filelock — already on the roadmap).

## User Story

```
Title: Sequential by default

As a Swimmer who pastes ONE URL at a time, I want the CLI to download
one thing, finish it, and stop -- with no surprise concurrency, no live
bars, and no need to reason about parallelism. As a Curator who pasted
a 60-track playlist, I want the SAME tool to still finish before bedtime.

Acceptance Criteria (Gherkin -- written BEFORE code):

  Scenario: Default concurrency is 1 (sequential)
    Given a fresh shokz install with no shokz.toml and no SHOKZ_* env
    When I run `shokz config show`
    Then general.concurrency == 1
     And `shokz download URL_A URL_B URL_C` processes URLs strictly in order
     And the manifest records appear in invocation order

  Scenario: --concurrency flag still works for power users
    Given a 3-URL invocation
    When I run `shokz download --concurrency 3 URL_A URL_B URL_C`
    Then up to 3 downloads run concurrently in-process
     And the existing per-track failure isolation property holds

  Scenario: --concurrency cap is now 4 (was 16)
    Given a CLI invocation with --concurrency 5
    When the command parses arguments
    Then the parser rejects with exit code != 0
     And the error mentions "max 4" or "<= 4"

  Scenario: Playlist invocation respects the new default + flag
    Given a small playlist URL
    When I run `shokz playlist <URL>` (no --concurrency)
    Then it downloads tracks sequentially (concurrency=1)
    And given `shokz playlist --concurrency 3 <URL>`
    Then up to 3 tracks download concurrently in-process

  Scenario: --help / docs warn against multi-process
    Given the CLI --help for download or playlist
    When the user reads the --concurrency flag description
    Then it states "in-process only; multi-process invocations against the
        same --output are NOT safe -- see Sprint 8"

  Scenario: Sprint 5 F1 follow-up -- playlist no longer double-extracts
    Given a playlist URL
    When `shokz playlist URL` runs
    Then exactly ONE yt-dlp extract_info network call happens for the playlist
     And the playlist title is sourced from PlaylistInfo.title
     And there is no second extract_info / try-except block in playlist.py

Non-functional:
  - Behaviour preserves all Sprint 4 atomic-write properties (kill-test still PASS).
  - Behaviour preserves Sprint 4.5 skip-existing + reconciliation single-process semantics.
  - The `--concurrency` flag is documented as "in-process only".

Out of scope (deferred):
  - Cross-process filelock for safe multi-process invocation -> Sprint 8
  - Live progress UI (Rich/Plain/byte progress)              -> intentionally never
  - ID3 tagging (mutagen)                                    -> retired Sprint 6 sub-scope
  - Cookie-quality guard                                     -> retired Sprint 6 sub-scope
  - Any rewrite of the asyncio use case -> still async (downstream ports are async)

INVEST: Independent, Negotiable, Valuable (gives the swimmer the simple
        behaviour they asked for AND keeps the playlist escape hatch),
        Estimable (~30 min), Small, Testable (6 Gherkin scenarios)
```

## Definition of Ready (DoR) — checked

- [x] Sprint Goal written
- [x] Gherkin AC (6 scenarios)
- [x] Affected files listed (below)
- [x] Ports/contracts named: NONE -- no port changes
- [x] Test approach: extend `test_schema.py` for default+max change; extend `test_batch_download.py` to assert sequential ordering when concurrency=1; existing Sprint 1 concurrency test moved to opt-in via `--concurrency 3`
- [x] Dependencies on prior sprints: Sprint 5 v0.6.0 ✓
- [x] Out-of-scope list written
- [x] Estimated ~30 min

## Files to land in Sprint 6

### config
- `src/shokz/config/schema.py` — `GeneralConfig.concurrency`: `default=3 → default=1`, `le=16 → le=4`

### adapters/inbound/cli/commands/
- `src/shokz/adapters/inbound/cli/commands/download.py` — `--concurrency` help text + `max=16 → max=4`
- `src/shokz/adapters/inbound/cli/commands/playlist.py` — same; **also drop the leftover `extract_info` block at lines 121–128 (Sprint 5 F1 follow-up)** as a separate commit

### tests/
- `tests/unit/config/test_schema.py` — assert new default + max
- `tests/unit/application/test_batch_download.py` — assert sequential ordering when no `--concurrency`; existing 3-URL gather test now passes `concurrency=3` explicitly to keep the assertion

### docs
- `CHANGELOG.md` `[Unreleased]` → `[0.7.0]` — call out the breaking-default change clearly
- `README.md` — note the new default + the multi-process safety warning

## Definition of Done (DoD) — verify before close

- [ ] All 6 Gherkin AC scenarios pass as pytest tests
- [ ] `just sprint-review 6` passes (Gherkin ↔ test name coverage)
- [ ] `just lint / format / typecheck` clean
- [ ] `just test` green; coverage ≥ 80%
- [ ] **Atomic-write protocol still holds** (`just kill-test` Sprint 4 ratchet)
- [ ] **Reconciliation handles subdirs** (Sprint 4.5 retro DoD ratchet)
- [ ] CHANGELOG.md `[Unreleased]` → `[0.7.0]` notes the breaking default
- [ ] README.md updated (new default + multi-process warning)
- [ ] **TWO commits**: `fix(playlist): drop redundant extract_info round-trip (Sprint 5 F1 follow-up)` then `feat(general): default concurrency to 1; cap flag at 4 (Sprint 6, v0.7.0)`
- [ ] Self-demo: `shokz download <small-url>` runs sequentially; `shokz download --concurrency 3 <a> <b> <c>` runs concurrently
- [ ] Git tag pushed: `v0.7.0`
- [ ] Retro entry appended to RETRO.md noting the Sprint 6 → 6a/6b → reset → tiny-Sprint-6 trajectory and what we learned about not-shipping-shell-parallelism
