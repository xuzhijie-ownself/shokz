# Sprint 7 — Retry policy + error translation table

**Date:** 2026-04-27
**Tag target:** `v0.8.0`
**Effort:** ~½ day (estimate revised after GAN sweep added ~30-45 min for the C1 stderr-classification fix + circuit breaker + cleanup hook)

## Sprint Goal

Transient YouTube failures (429, 5xx, network blips) retry with classified backoff and eventually succeed; terminal failures (auth, format-unavailable, source-file-corrupt) fail fast with the right domain-error class so the user sees the real cause instead of a misleading `DownloadFailed`. Classification fires on BOTH the Python-API resolve path AND the subprocess-stderr download path.

## GAN-fix manifest (baked into this spec)

| Tag | What | Source | Status |
|---|---|---|---|
| **C1** | `_classify_message(msg)` MUST be applied at THREE sites: `resolve()` exception handler, `resolve_playlist()` exception handler, AND the subprocess-stderr-tail path in `download_audio` (line 178). The current `download_audio` raises `DownloadFailed(stderr_tail)` directly and would NEVER hit the classifier without this fix. | py-rev#2 | baked |
| **C2** | `RetryPolicy` MUST use tenacity `reraise=True` (or unwrap `RetryError` before re-raising). Test asserts the exception type exiting the wrapper is the original domain error. | silent#1 | baked |
| **C3** | Retry wrap covers BOTH `source.resolve(url)` AND `source.download_audio(...)`. Same classification, same budgets. | architect#2, silent#2 | baked |
| **C4** | Wall-clock semantics = total time around `retry_policy.run(...)`, NOT just sleep time. Per-batch circuit breaker: after 3 consecutive `RateLimited` outcomes across tracks, the rest of the batch downgrades to retries=0 with a WARNING log. | architect#3, silent#5 | baked |
| **C5** | Classifier precedence (top-to-bottom, first-match-wins): `AuthRequired > FormatUnavailable > SourceUnavailable > RateLimited > NetworkError > DownloadFailed`. | architect#4, silent#4 | baked |
| **C6** | `SourceFileCorrupt` retry MUST `glob(tmp_dir / f"{track.id}.*")` + `unlink` BEFORE re-attempt (avoid yt-dlp resume against corrupt partial). | architect#1, silent#3 | baked |
| **U1** | Retry unit = "download + integrity-check (size only)". `EncodingFailed` and the duration-tolerance check are TERMINAL in Sprint 7 (0 retries). | architect#1 | baked |
| **U2** | Wrap signature: `await retry_policy.run(coro_factory, classify=...)`. Coro factory returns a fresh awaitable per attempt. RetryPolicy is a pure application policy (no adapter imports). | architect#5 | baked |
| **U3** | `RetryPolicy` MUST avoid blocking the event loop (default `time.sleep` would starve other concurrent slots). **Phase 3 amendment**: original spec said "MUST use `tenacity.AsyncRetrying`"; Phase 3 implementation chose a custom `asyncio.sleep`-based loop instead because tenacity's per-call retry strategy doesn't map cleanly to per-error-class budgets (RateLimited 3 attempts × 5/30/120s exp vs. NetworkError 2 attempts × 1s linear vs. SourceFileCorrupt 1 retry × 1s). The custom loop is ~30 LoC, satisfies the no-event-loop-block invariant via `asyncio.sleep`, and is easier to test. tenacity stays in `pyproject.toml` for any future need; this policy doesn't import it. **Verdict: APPROVED spec deviation, documented in `application/policies/retry.py` module docstring.** | py-rev#1 + Phase 3 GAN | amended |
| **U4** | `_ERROR_CLASS_MAP` migrates from `dict[str, str]` keyed on `__name__` to an ordered `tuple[tuple[type[BaseException], str], ...]` matched via `isinstance` (subclass-safe). | py-rev#3 | baked |
| **U5** | `wall_clock_budget_s: float = Field(default=180.0, ge=1.0, le=600.0)`. `backoff_base_s: float = Field(default=1.0, ge=0.1, le=60.0)`. Each `max_attempts_*: int = Field(..., ge=0, le=5)`. | py-rev#7, architect#7 | baked |
| **U6** | `pyproject.toml` `[[tool.mypy.overrides]]`: add `tenacity` to `ignore_missing_imports = true`. | py-rev#8 | baked |
| **U7** | Pre-existing copy-paste drift between `resolve()` and `resolve_playlist()` classification gets fixed by extraction into the single helper (collateral cleanup). | py-rev#6 | baked |
| **U8** | `BatchDownloadResult.unclassified_yt_dlp_errors: int = 0` counter; CLI summary line shows it when > 0 to surface §7.1 drift. | architect#8 | baked |

## Scope split (per Sprint 6 retro lesson)

The master plan §8 lists Sprint 7 as 4-5 deliverables. Per the "STOP and play it back as a half-page spec WITH deferred items, BEFORE coding" rule:

| # | Item | Status |
|---|---|---|
| 1 | Retry policy (tenacity wrapper) | **Sprint 7 (this)** |
| 2 | Error translation table §7.1 | **Sprint 7 (this)** |
| 3 | Bitrate cap (`domain/bitrate.py`) | DEFERRED → Sprint 6b backlog |
| 4 | `--dry-run` | DEFERRED → Sprint 7.5 |
| 5 | Failed tracks in manifest with `status=failed` | REJECTED — Sprint 4's separate `failures.jsonl` design stands |

## User Story

```
Title: Transient errors retry; terminal errors fail fast

As a Swimmer who pasted 5 URLs over hotel WiFi, I want shokz to
retry the ones that hit 429 or a flaky 5xx -- but I don't want it
wasting 3 attempts on a deleted video or an age-gated one.

Acceptance Criteria (Gherkin -- written BEFORE code):

  Scenario: 429 Too Many Requests retries with long backoff
    Given a fake source whose first 2 download_audio calls raise RateLimited
      AND the 3rd call succeeds
    When the use case processes that URL
    Then the track ends up SUCCESS (not FAILED)
     And exactly 3 download_audio calls were made
     And the wall-clock between attempts is approximately 5s, then 30s
        (allow ±20% for jitter; total <= 50s)

  Scenario: 5xx network error retries with short backoff
    Given a fake source whose first call raises NetworkError
      AND the 2nd call succeeds
    When the use case processes that URL
    Then SUCCESS, exactly 2 calls, gap ~1s

  Scenario: AuthRequired fails immediately (no retry)
    Given a fake source whose download_audio raises AuthRequired
    When the use case processes that URL
    Then FAILED, exactly 1 call (no retry)
     And the failures.jsonl entry's error_class is "AUTH_REQUIRED"
        (not "DOWNLOAD_FAILED" and not "UNEXPECTED_ERROR")

  Scenario: FormatUnavailable fails immediately (no retry)
    Given source raises FormatUnavailable
    Then FAILED, exactly 1 call, error_class "FORMAT_UNAVAILABLE"

  Scenario: SourceUnavailable fails immediately (deleted / private video)
    Given source raises SourceUnavailable
    Then FAILED, exactly 1 call, error_class "SOURCE_UNAVAILABLE"

  Scenario: SourceFileCorrupt retry deletes the partial then re-downloads (C6)
    Given source raises SourceFileCorrupt for the first attempt
      AND the 2nd attempt succeeds
    When the use case processes that URL
    Then SUCCESS, exactly 2 download_audio calls
     AND BEFORE the 2nd call, every file matching tmp_dir/{track.id}.* is unlinked
     AND error_class on the (eventually-succeeded) record is irrelevant -- track is SUCCESS

  Scenario: SourceFileCorrupt exhausted retry stays FAILED
    Given source raises SourceFileCorrupt for both attempts
    Then FAILED, exactly 2 calls, error_class "SOURCE_FILE_CORRUPT"
     AND no leftover tmp_dir/{track.id}.* files (cleanup ran before the exhausted attempt too)

  Scenario: yt-dlp DownloadError "Sign in to confirm your age" -> AuthRequired
    Given the ytdlp_source receives DownloadError("Sign in to confirm your age")
    When the adapter classifies it
    Then it raises AuthRequired (NOT DownloadFailed, NOT SourceUnavailable)
     And the use case does NOT retry

  Scenario: yt-dlp DownloadError "HTTP Error 429" -> RateLimited
    Given DownloadError("HTTP Error 429: Too Many Requests")
    Then ytdlp_source raises RateLimited
     And the use case retries up to 3 times with exponential backoff

  Scenario: yt-dlp DownloadError "HTTP Error 503" -> NetworkError
    Given DownloadError("HTTP Error 503: Service Unavailable")
    Then ytdlp_source raises NetworkError
     And the use case retries up to 2 times with short backoff

  Scenario: yt-dlp DownloadError "Requested format not available" -> FormatUnavailable
    Given DownloadError("Requested format is not available")
    Then ytdlp_source raises FormatUnavailable
     And the use case does NOT retry

  Scenario: Combined message classifies by precedence (C5)
    Given a yt-dlp message containing BOTH "Sign in to confirm your age"
        AND "HTTP Error 429"
    When the classifier matches
    Then it returns AuthRequired (terminal-first precedence)
     AND the use case does NOT retry (no wasted 3-attempt loop)

  Scenario: Subprocess-stderr-tail path is also classified (C1)
    Given the subprocess yt-dlp exits non-zero with stderr ending in
        "ERROR: [youtube] dQw...: Sign in to confirm your age"
    When download_audio reaches its stderr-tail handler
    Then it raises AuthRequired (NOT bare DownloadFailed)
     AND the use case does NOT retry
     (Without this, the entire §7.1 sprint is a no-op for the subprocess path.)

  Scenario: Resolve-phase RateLimited also retries (C3)
    Given source.resolve(url) raises RateLimited twice then succeeds
    When the use case processes that URL
    Then SUCCESS, exactly 3 resolve() calls + 1 download_audio call
     (Confirms retry covers BOTH phases, not just download.)

  Scenario: Per-batch circuit breaker after 3 consecutive RateLimited (C4)
    Given a 5-URL batch where every URL is RateLimited
    When tracks 1, 2, 3 each exhaust their RateLimited retry budget
    Then a WARNING is logged: "circuit breaker tripped: rest of batch will not retry"
     AND tracks 4 and 5 are attempted exactly ONCE each (no retries)
     AND all 5 tracks end FAILED with error_class "RATE_LIMITED"

  Scenario: Unknown DownloadError defaults to DownloadFailed + counter increments (U8)
    Given DownloadError("Some new yt-dlp message we haven't classified")
    Then ytdlp_source raises DownloadFailed
     And the use case retries once before giving up
     And a WARNING is logged ("unclassified yt-dlp error: ...") with the FULL raw message
     AND BatchDownloadResult.unclassified_yt_dlp_errors == 1
     AND the CLI summary line shows "1 unclassified yt-dlp error -- please report"

  Scenario: One failure entry per track regardless of retry count (C2-paired)
    Given a track that retries 3 times then fails terminally with RateLimited
    When _process_one returns
    Then exactly ONE row is appended to failures.jsonl
     And NOT 3 rows (one per attempt)
     And error_class == "RATE_LIMITED" (NOT "UNEXPECTED_ERROR" — proves reraise=True)

  Scenario: Per-track failure isolation preserved (Sprint 1 invariant)
    Given URL_A is RateLimited (will eventually succeed after retry)
      AND URL_B is AuthRequired (terminal)
      AND URL_C is healthy
    When `shokz download URL_A URL_B URL_C` runs (concurrency 1)
    Then URL_A ends SUCCESS, URL_B ends FAILED, URL_C ends SUCCESS
     And the batch summary is "2/3 succeeded (0 skipped, 1 failed)"

Non-functional:
  - Library-first: tenacity >= 8 (already in pyproject.toml since Sprint 0).
    MUST use `tenacity.AsyncRetrying` (or pass `sleep=asyncio.sleep`) so retry
    sleeps don't block the asyncio event loop (U3).
  - MUST use `reraise=True` so the original domain-error class survives the
    retry wrapper -- otherwise failures.jsonl says "UNEXPECTED_ERROR" (C2).
  - Wrap signature: `await retry_policy.run(coro_factory, classify=...)` (U2).
    coro_factory is a zero-arg callable that returns a FRESH awaitable per
    attempt. RetryPolicy is a pure application policy mirroring
    SkipExistingPolicy's shape. The adapter does NOT import retry.py.
  - Retry budgets are CONFIGURABLE via [retry] section (defaults; U5 caps):
    * RateLimited: 3 attempts, exponential 5s/30s/120s, max 50% jitter
    * NetworkError, DownloadFailed: 2 attempts, 1s linear backoff
    * SourceFileCorrupt: 1 retry (so 2 total attempts)
    * EncodingFailed: 0 retries (TERMINAL in Sprint 7 — encode is deterministic; U1)
    * Everything else (Auth/Format/SourceUnavailable/etc): 0 retries
  - Wall-clock budget per track caps at 180s TOTAL elapsed (download time +
    sleeps, NOT just sleeps; C4). Implemented via tenacity.stop_after_delay
    or asyncio.wait_for. On timeout: cancel the wait, attempt to terminate
    the in-flight subprocess, then call the cleanup hook to delete
    tmp_dir/{track.id}.*.
  - Per-batch circuit breaker (C4): use case tracks consecutive RateLimited
    outcomes; after 3 consecutive trips, downgrades remaining tracks to
    retries=0 with a single WARNING log line. Resets when any track succeeds.
  - SourceFileCorrupt retry MUST run a cleanup hook BEFORE the next attempt:
    `glob(tmp_dir / f"{track.id}.*") + unlink` (C6). Otherwise yt-dlp resume
    logic may merge against the corrupt partial.
  - The retry unit is "download + post-download size check" (lines 266-276 of
    batch_download.py). NOT the encode phase. NOT the duration-tolerance
    check (those are terminal in Sprint 7; U1).
  - Failure log behavior unchanged: one row in failures.jsonl per FAILED
    TrackResult, regardless of how many internal retries happened.
  - The error_class string in failures.jsonl uses the FINAL classified
    error type (not "RetryError", not "UNEXPECTED_ERROR"; matters when
    429 retries exhaust -> classification stays "RATE_LIMITED").
  - Classifier precedence order (top-to-bottom, first-match-wins; C5):
    AuthRequired > FormatUnavailable > SourceUnavailable > RateLimited >
    NetworkError > DownloadFailed.
  - Classifier helper applies to THREE sites (C1 + U7):
    1. ytdlp_source.resolve() exception handler
    2. ytdlp_source.resolve_playlist() exception handler
    3. ytdlp_source.download_audio() stderr-tail path (THIS is the no-op-fix)
  - `_ERROR_CLASS_MAP` becomes an ordered `tuple[tuple[type, str], ...]`
    matched via `isinstance` (U4) so subclasses classify correctly.

Out of scope (deferred):
  - Bitrate cap (`domain/bitrate.py`)         -> Sprint 6b backlog
  - `--dry-run` flag                           -> Sprint 7.5
  - Manifest schema collapse (failures into manifest with status) -> REJECTED
  - ID3 tagging                                -> Sprint 6b backlog
  - Any change to failures.jsonl schema        -> none
  - Changes to retry behavior on `encode()`    -> EncodingFailed terminal in Sprint 7

INVEST: Independent (Sprint 6 unblocks), Negotiable, Valuable (transient
        YouTube errors are the #1 source of false-failure reports),
        Estimable (~½ day), Small (paired retry+translate is one cohesive
        deliverable), Testable (17 Gherkin scenarios after GAN expansion)
```

## Definition of Ready (DoR) — checked

- [x] Sprint Goal written
- [x] Gherkin AC (17 scenarios after GAN expansion)
- [x] Affected files listed (below)
- [x] Ports/contracts named: NO new port. `RetryPolicy` is an application-layer policy. `_classify_message` is a private helper inside `ytdlp_source.py`.
- [x] Test approach: unit (RetryPolicy classifies + sleeps right via mocked tenacity; ytdlp_source classifier exhaustively per §7.1 row, including the multi-match precedence case); integration (use case recovers from transient fake failures; one failure log row per track; circuit breaker fires)
- [x] Dependencies: Sprint 6 v0.7.0 ✓
- [x] Out-of-scope explicit
- [x] Estimated ~½ day (with GAN edits: ~30-45 min added beyond original)
- [x] All convergent GAN HIGH findings (C1-C6) baked in
- [x] All unique HIGH findings (U1-U8) baked in

## Files to land in Sprint 7

### domain/
- `domain/errors.py` — ADD `AuthRequired`, `FormatUnavailable`, `RateLimited`, `NetworkError`. Existing classes UNCHANGED.

### application/policies/
- `application/policies/retry.py` — NEW. `RetryPolicy(config: RetrySection)` exposes `async def run(coro_factory, classify, on_retry=None) -> T`. Wraps `tenacity.AsyncRetrying` with `reraise=True` and `stop_after_delay(wall_clock_budget_s)`. Plus per-class attempt count + backoff sequence. Pure application policy: imports only from `domain.errors` and `tenacity`.

### application/use_cases/
- `application/use_cases/batch_download.py`:
  - Wrap `source.resolve(url)` AND `source.download_audio(...)` with `retry_policy.run(...)` (C3)
  - `on_retry` hook for `download_audio` performs `glob(tmp_dir/{track.id}.*) + unlink` (C6)
  - Track `_consecutive_rate_limits: int` instance counter; reset on any SUCCESS; trip circuit breaker at 3 (C4)
  - Migrate `_ERROR_CLASS_MAP` from name-keyed dict to isinstance-ordered tuple (U4)
  - Add 4 new error classes to the map
  - Update `BatchDownloadUseCase.__init__` to accept `retry_policy: RetryPolicy`
  - `BatchDownloadResult.unclassified_yt_dlp_errors: int = 0` (U8); use case increments when `download_failed_unclassified` log fires
  - Track `EncodingFailed` and the duration-tolerance check stay OUTSIDE the retry wrapper (U1)

### adapters/outbound/
- `adapters/outbound/ytdlp_source.py`:
  - Extract `_classify_message(msg: str) -> ShokzError` module-level helper. Ordered list of (substring, error_class) pairs evaluated top-to-bottom (C5). On no match: log WARNING with full raw message + return `DownloadFailed(msg)`.
  - Apply at `resolve()` (line 73) → fixes the existing 4-pattern matcher
  - Apply at `resolve_playlist()` (line 111) → fixes copy-paste drift (U7)
  - Apply at `download_audio()` stderr-tail handler (line 178) → **the C1 no-op-fix**

### config
- `config/schema.py` — `RetrySection` with `validate_default=True`:
  - `max_attempts_rate_limited: int = Field(default=3, ge=0, le=5)`
  - `max_attempts_network: int = Field(default=2, ge=0, le=5)`
  - `max_attempts_corrupt: int = Field(default=1, ge=0, le=5)`
  - `backoff_base_s: float = Field(default=1.0, ge=0.1, le=60.0)`
  - `wall_clock_budget_s: float = Field(default=180.0, ge=1.0, le=600.0)`

### composition root
- `composition.py` — wire `RetryPolicy(config.retry)` and pass to `BatchDownloadUseCase`.

### pyproject.toml
- Add `tenacity` to `[[tool.mypy.overrides]] ignore_missing_imports = true` (U6).

### tests/
- `tests/unit/application/test_retry_policy.py` — NEW. Asserts classification + backoff sequence per error class; asserts `reraise=True` so original error type exits wrapper; asserts `coro_factory` is called once per attempt (fresh awaitable each time).
- `tests/unit/adapters/test_ytdlp_error_translation.py` — NEW. Enumerates every §7.1 row + the multi-match precedence case + the unclassified-counter increment.
- `tests/unit/application/test_batch_download.py` — extend with: 429-then-success retry; AuthRequired no retry; resolve-phase retry; circuit breaker after 3 consecutive RateLimited; SourceFileCorrupt cleanup-then-retry; one-failure-row invariant under retry exhaustion.
- `tests/acceptance/test_sprint_7_retry.py` — Gherkin scenarios.

## Definition of Done (DoD) — verify before close

- [ ] All 17 Gherkin AC scenarios pass as pytest tests
- [ ] `just sprint-review 7` passes
- [ ] `just code-review v0.7.0` brief generated; reviewers dispatched; convergent + unique findings either fixed OR explicitly deferred-with-reason
- [ ] `just lint / format / typecheck` clean (incl. tenacity mypy override; U6)
- [ ] `just test` green; coverage ≥ 80%
- [ ] **Atomic-write protocol still holds** (`just kill-test` Sprint 4 ratchet)
- [ ] **Reconciliation handles subdirs** (Sprint 4.5 retro DoD ratchet)
- [ ] **Sequential-by-default still holds** (Sprint 6 ratchet — tests for `concurrency=1` default still pass)
- [ ] **Subprocess-stderr classification ratchet** (Sprint 7 C1; new): a unit test asserts `download_audio()`'s stderr-tail path raises the SAME domain error classes as `resolve()` for the same yt-dlp message. Without this ratchet, a future refactor could regress the C1 fix.
- [ ] CHANGELOG.md `[Unreleased]` → `[0.8.0]`
- [ ] README.md updated (mention retry policy + auth/format errors fail fast)
- [ ] Conventional Commits: `feat(retry): classified retry policy + yt-dlp error translation table (Sprint 7, v0.8.0)`
- [ ] Self-demo: simulate 429 by editing a test fake; observe 5s/30s sleeps in the log
- [ ] Git tag pushed: `v0.8.0`
- [ ] Retro entry appended to RETRO.md
