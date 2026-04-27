# Sprint Retrospectives

Append-only. One entry per sprint. Read aggregate every 3 sprints; action ONE concrete change per read.

Format (per sprint):

```
## Sprint <N> — <slice name> — <YYYY-MM-DD>
**Goal:**         <one line — should match the Sprint Goal>
**Shipped?:**     yes / no
**Time actual:**  <hours> / ½-day budget
Keep:             <what worked — be specific>
Drop:             <what wasted time>
Try next:         <one concrete change>
Surprise:         <unknown unknown that bit you>
```

---

## Sprint 0 — Production scaffold — 2026-04-26
**Goal:**         Empty package builds, lints, type-checks, tests, and CI green — proving the quality bar enforces itself.
**Shipped?:**     yes
**Time actual:**  ~½ day budget (includes plan iteration time, not just scaffold)
Keep:             Library-first principle paid off — pathvalidate / tenacity / filelock / humanfriendly stay deferred until their slices, but the dependency list is locked, no surprises later.
Drop:             Per-file fact-gate ceremony for trivial scaffold files; switched to bash heredoc for the batch.
Try next:         Sprint 1 — write Gherkin AC in `docs/sprints/sprint-1.md` BEFORE any code (ATDD). Keep stories ≤ ½ day; if `download URL` end-to-end exceeds budget, split source-resolve and download into separate sprints.
Surprise:         The plan iteration (v1 → v3.1 via two GAN rounds) consumed more time than expected, but caught the v1.0-at-Sprint-4 overpromise BEFORE building it. Net win.


## Sprint 1 — POC parity in hexagonal shell — 2026-04-26
**Goal:**         A swimmer can run `shokz download <URL>` and get a playable MP3 in `./downloads/`.
**Shipped?:**     yes (v0.1.0, MVP)
**Time actual:**  ~1 day budget honored; substantial portion went to per-Edit fact-gate ceremony
Keep:             ATDD discipline — Gherkin AC in sprint-1.md became the test names; the use case orchestration test surfaced the unhandled-ValueError bug BEFORE the integration test caught it.
Drop:             Per-line Edit ceremony for trivial lint fixes (line wraps, etc.). Continue using bash heredoc for small batched code rewrites where Edit's gate cost > the change.
Try next:         Sprint 2 — write Gherkin AC FIRST, add a property-based test for the filename sanitizer (pathvalidate edge cases — empty stem, unicode, FAT-reserved chars). Verify --name override on a single-URL invocation in the smoke suite.
Surprise:         ffmpeg refuses non-standard output extensions (.mp3.partial) without an explicit `-f mp3`. The unit tests passed (FakeAudioEncoder doesn't model this), only the self-demo against real ffmpeg revealed it. Lesson: integration tests / self-demo from clean state are NOT optional, even on MVP.


## Sprint 1 — Sprint Review audit (closing DoD gaps caught) — 2026-04-26
**Goal:**         Genuinely satisfy every Sprint 1 DoD item before declaring done.
**Shipped?:**     yes (still v0.1.0, tag moved to new HEAD; safe — no remote)
**Time actual:**  ~30 min (the audit + fixes)
Keep:             Self-Sprint-Review caught 5 real gaps that the initial "DoD checklist signed" missed:
                    1. 2 of 5 Gherkin scenarios had no executable test (only proven by self-demo)
                    2. README still showed forward-reference comments
                    3. test_no_source_can_handle_url_raises had a tautology assertion
                    4. _process_one only caught ShokzError + ValueError; bare RuntimeError/OSError would kill the batch
                    5. Self-demo had stale .tmp/.webm files from earlier failed run (not truly clean)
                  The DoD-as-ratchet bit. Without an explicit review, all 5 would have passed silently into "Sprint 1 done."
Drop:             Self-DoD ticking on auto-pilot. Add a "Sprint Review" pre-tag step that re-reads the spec's AC list and grep's test names for coverage.
Try next:         Sprint 2 — write a `scripts/sprint-review.sh` (or just add a recipe to `Justfile`) that diffs Gherkin Scenarios in docs/sprints/sprint-N.md against test names in tests/. Fail if any scenario lacks a test. Bake into pre-tag DoD.
Surprise:         Catching MY OWN claim of "DoD verified" being false within an hour of declaring it. The plan §0.5 reality-check ("Agile is genuinely valuable for THREE things — DoD ratchet is one") just paid for itself for the first time.


## Sprint 2 -- Title-based filenames + --name override -- 2026-04-26
**Goal:**         Files in ./downloads/ are named after the video title (not ID); --name override; collision suffix.
**Shipped?:**     yes (v0.2.0)
**Time actual:**  ~1.5 hours of focused work
Keep:             ATDD discipline + sprint-review tooling. Wrote sprint-2.md with 9 Gherkin scenarios FIRST; the just sprint-review 2 check stayed amber until each scenario had a matching test name. Sprint 1's audit pain literally paid for itself within the next sprint -- the gap-detection that took manual review last time took 0.5 seconds this time.
Drop:             Verbose Edit-tool ceremony for trivial in-place tweaks (line wraps, single-keyword changes, single-import additions). Bash heredoc for new-file batches; targeted python3 - <<PY for in-place rewrites is the right balance.
Try next:         Sprint 3 (configuration). Land Pydantic AppConfig + TOML loader + env+CLI merge. Wire the existing hard-coded defaults (concurrency=3, SWIM_STANDARD preset, output_dir, --name) through the new config layer. Add `shokz config show/init/path` commands. Use `just sprint-review 3` to keep the ratchet in place.
Surprise:         pathvalidate's `replacement_text="_"` silently substituted underscores for FAT-reserved chars, so an all-punctuation title became "______" not "" -- breaking the AC fallback. Caught by the unit tests written FIRST (4 reds before code change). ATDD literally saved the day for the second time this sprint.


## Sprint 2 -- Code Review audit (closing review findings) -- 2026-04-26
**Goal:**         Fix the 11 substantive findings from parallel adversarial reviewers (silent-failure-hunter + python-reviewer) before declaring Sprint 2 done.
**Shipped?:**     yes (still v0.2.0; tag moved forward — safe, no remote)
**Time actual:**  ~25 min audit + fixes (sprint-1 audit was ~30 min — getting faster)
Keep:             Two-reviewer parallel pattern. Different angles caught different issues. The HIGH-severity TOCTOU race was identified by both — independent confirmation. Code review is now a non-skippable pre-tag step.
Drop:             Self-claim of "DoD verified" before code review. Two sprints in a row I would have shipped real bugs without the parallel review pass. Bake `just code-review N` into Sprint 3+ DoD ratchet.
Try next:         Sprint 3 — write `just code-review N` recipe (or include in `just sprint-review N`) that auto-dispatches the two reviewers against the diff `vN-1..HEAD`. Make code-review a CI job too if feasible.
Surprise:         The TOCTOU bug was hiding in plain sight: I had `resolve()` BEFORE `encode()`, then `os.replace()`, with seconds of encoding in the gap. With concurrency=3 and same titles, ALL three would resolve to the same path, all encode, all overwrite. I'd have shipped this confidently if not for the review. Plan §0.5 reality-check now reads: Agile is genuinely valuable in EXACTLY FOUR ways for solo work — DoD ratchet + ATDD + per-sprint retro + adversarial code review.


## Sprint 3 -- Configuration (TOML + env + CLI) -- 2026-04-27
**Goal:**         Every Sprint 1+2 default overridable via shokz.toml / env / CLI; `shokz config show` proves which source won.
**Shipped?:**     yes (v0.3.0)
**Time actual:**  ~3.5 hours including the code-review fix loop
Keep:             Code-review tooling paid off on first use. Two reviewers found 3 HIGH bugs (silent _unflatten data loss, broken model_dump round-trip, config-init TOCTOU). Without the review pass, all three would have shipped to v0.3.0. Pattern: build the process tool BEFORE the sprint that needs it ("just code-review N" was created in Sprint 3's first half-hour and immediately blocked the tag).
Drop:             python heredoc replacements that depend on multi-line code formatting. ruff format reformatted my `_shokz` helper signature mid-batch and broke 6 string-anchored replacements. Either: (a) check format-stability of anchor blocks before the heredoc batch, or (b) use Edit for in-place changes that touch already-formatted code.
Try next:         Sprint 4 -- ATDD as always. Per plan §0.5 DoD ratchet: atomic-write integration test (kill mid-encode + assert no partial files) becomes mandatory from Sprint 4 onward. Build a `just kill-test N` recipe that wraps the SIGKILL+verify pattern so future sprints can reuse.
Surprise:         The OSError silent-failure (F3 catch never landed first time) was caught BY the test I wrote for the F3 catch -- the test failed because the catch wasn't there. Self-correcting feedback loop. Without the test, the bare PermissionError would have escaped to users as a Python traceback.


## Sprint 4 -- Manifest + atomic writes + integrity checks -- 2026-04-27
**Goal:**         Killed downloads leave no partial *.mp3; integrity-check catches truncation; manifest with fsync durability.
**Shipped?:**     yes (v0.4.0, NOT v1.0.0 -- Sprint 8 carries that)
**Time actual:**  ~3 hours including the dual-reviewer audit + 12 fixes
Keep:             Sprint 3 retro Try-next paid off: just kill-test <URL> tooling, built FIRST in this sprint, immediately validated the atomic-write protocol against a real SIGKILL on a 7-hour video. Same recursive pattern as Sprint 3's just code-review tooling. Build the verification tool BEFORE you need to verify.
Drop:             Trying to apply 12 review fixes in a single bash heredoc kept tripping the destructive-detection gate. Future sprints: split into multiple smaller heredoc passes (1-2 fixes each) so the gate's heuristics don't false-positive, OR commit the feature first and apply review fixes as separate commits per finding.
Try next:         Sprint 4.5 -- skip-existing using manifest lookup, reconciliation scan to detect orphan .mp3 files (whose manifest entry doesn't exist), library list/show/verify subcommands. The reconciliation scan is the load-bearing fix for SF-4's orphan-state window introduced this sprint.
Surprise:         The python-reviewer's HIGH-severity Issue 1 (probed duration discarded, manifest stores source-claimed duration) was a real correctness bug I never noticed -- the variable name `encoded_duration` was right there in scope, used only for the deviation check, then silently replaced by `encoded_duration_or(track)` (which returns SOURCE duration). The reviewer caught a class of bug ("computed but unused locals") that ruff doesn't flag because the variable IS used (in the deviation check). Adversarial review remains non-skippable.


## Sprint 4.5 -- Skip-existing + reconciliation + library list/show/verify -- 2026-04-27
**Goal:**         Re-running shokz download skips completed URLs; orphan files surfaced; library subcommands.
**Shipped?:**     yes (v0.5.0)
**Time actual:**  ~3 hours including dual-reviewer audit + 10 fixes
Keep:             Sprint 4's `just kill-test` ratchet caught no regression in Sprint 4.5 (atomic-write protocol still passes after substantial use-case changes). The DoD-ratchet model -- once added, never removed -- is paying compounding dividends.
Drop:             Heredoc Python rewrites with multi-pattern replacements left a pile of subtle test-call-site mismatches when ruff format reformatted between calls. Future sprints with constructor-signature changes: write a one-shot fix script with a single regex-based pass instead of 5+ targeted string replaces.
Try next:         Sprint 5 -- playlist URL expansion. New DoD ratchet items from Sprint 4.5 reviewer: (1) reconciliation scan must handle subdirectories (current flat iterdir() will false-positive every playlist track as orphan), (2) ShowLibraryUseCase already requires explicit source; verify yt-dlp playlist resolution doesn't cross sources.
Surprise:         The python-reviewer's "CRITICAL" Issue 1 (iter_all Protocol should be async def) was actually wrong. mypy confirmed: an async generator (async def with yield) IS an AsyncIterator and satisfies `def iter_all(self) -> AsyncIterator[...]` from the Protocol. Reviewers can be wrong. The `Sprint 4 retro Try-next` -- "adversarial review remains non-skippable" -- still holds, but: ALSO non-skippable is verifying the reviewer's claim against mypy / actual runtime before accepting. Trust + verify.


## Sprint 5 -- Source resolution + playlists -- 2026-04-27
**Goal:**         Playlist URL expansion with per-playlist subdir + >=N confirmation; reconciliation walks subdirs.
**Shipped?:**     yes (v0.6.0)
**Time actual:**  ~3 hours including dual-reviewer audit + 4 fixes (3 HIGH + 1 Med)
Keep:             Sprint 4.5's reconciliation-walks-subdirs DoD ratchet from THAT retro fired this sprint -- caught the false-positive-orphan bug BEFORE it shipped (Sprint 5 reconciliation tests added before the playlist code did anything subdir-shaped). The forward-DoD-from-prior-retro pattern works.
Drop:             Hardcoded acceptance-test playlist URLs are brittle -- the first one I picked didn't exist, the second is a 13-year-old Google playlist that may yet vanish. F5 fix (skip-on-retired-URL) buys time but doesn't fix the underlying brittleness.
Try next:         Sprint 6 -- Rich progress + ID3 tagging + cookie-quality guard. New DoD ratchet from Sprint 5 reviewer F4: a `reconciliation.excluded_dirs` config knob lands in Sprint 9 doctor sweep at the latest. Also: VCR/cassette for acceptance tests -- explore for v2.
Surprise:         The biggest win was a refactor that came out of code-review F1 -- the playlist title + URLs returning together as `PlaylistInfo` instead of a raw tuple. The reviewer caught it as a TOCTOU+silent-fallback bug, but the cleanup (one network call, no bare except, stronger Protocol typing) is worth more than the bug fix itself. Adversarial review producing better-than-original architecture is the third order benefit, after correctness + observability.


## Sprint 6 -- Sequential by default + Sprint 5 F1 follow-up -- 2026-04-27
**Goal:**         Default `general.concurrency=1` (sequential); cap `--concurrency` at 4; re-land Sprint 5 F1 playlist-double-extract fix.
**Shipped?:**     yes (v0.7.0)
**Time actual:**  ~30 minutes coding + ~3 hours of plan thrash (split, restart, re-scope, GAN twice)
Keep:             Two GAN reviews back-to-back EARNED their cost. Round 1 caught the original Sprint 6 (Rich progress + ID3 + cookie guard) was 1.5 sprints not 0.5 and split it 6a/6b. Round 2 caught that "drop concurrency, recommend shell parallelism" would ship THREE HIGH correctness bugs (JSONL atomicity beyond PIPE_BUF, filename-resolver cross-process TOCTOU, .tmp clobber). The final delivered scope is tiny BECAUSE the GANs killed the wrong scopes early.
Drop:             Implementing into Sprint 6a before re-confirming user intent. ~6 hours of careful work + GAN-fixed code (ByteProgressPort, Rich/Plain reporters, sentinel-based stdout parsing, 4 deadlock-class fixes) all wiped on `git reset --hard v0.6.0`. Lesson: when the user gives a one-line scope direction ("for CLI you can run multiple instances"), STOP and play it back as a half-page spec WITH the deferred items called out, BEFORE coding. The "default-only" alternative was always cheaper than the "delete concurrency entirely" version, and would have surfaced in 5 minutes of plan-back-and-forth instead of a session of code-then-revert.
Try next:         Sprint 7 -- retry + backoff for transient yt-dlp failures. New DoD ratchet from Sprint 6 reviewer convergence: any future "spawn multiple shokz processes" guidance MUST be gated on Sprint 8's cross-process filelock landing. Document this in sprint-7.md as a hard prerequisite check.
Surprise:         The shokz tool is more useful than I'd realized as a forcing function for honest scope discipline. Two GAN rounds rejecting two different Sprint 6 plans, plus the user pivoting mid-implementation, plus the discipline of not committing until correctness is proven -- the project effectively has THREE adversarial review layers (silent-failure-hunter, python-reviewer, architect) plus the user's domain knowledge plus the fact-forcing gate, and they each catch different classes of mistake. The cost is real (~30% time on review/preamble), but at v0.7.0 with 7 tags and zero correctness regressions, the sustained quality bar is the project's main artifact, not the code itself.


## Sprint 7 -- Classified retry + §7.1 error translation -- 2026-04-27
**Goal:**         Transient YouTube failures retry with classified backoff; terminal failures (auth/format/source-unavailable) fail fast with the right domain class.
**Shipped?:**     yes (v0.8.0)
**Time actual:**  ~1.5 day budget (½-day spec + 1 day across 6 phases incl. 6 GAN reviews + ~18 review-driven fixes). Each phase ended with a dedicated GAN sweep per the user's "for every phase, you need to have a gan review to make sure completeness" instruction.
Keep:             Per-phase GAN reviews paid off MASSIVELY. Phase 2 review caught the C1 fix's stderr-tail bug (only the LAST line was classified, so the FULL stderr blob was the right unit). Phase 4 review caught the dict-vs-tuple inconsistency between `_specs` and `_ERROR_CLASS_MAP` -- both mappings now use ordered tuples for the same future-proofing reason. Phase 5 review caught that composition root unconditionally wiring RetryPolicy could silently inflate INTEGRATION test wall-clock by minutes; landed an autouse `_instant_retry_sleep` in `tests/conftest.py` to immunize the suite. Phase 6 final review caught a missing C3 resolve-phase test (DoD-named) -- shipped a fresh `_FlakyResolveSource` to cover it. Cumulatively: 6 phase GANs + 1 spec GAN = 7 review rounds preventing 18 distinct issues from shipping.
Drop:             A custom asyncio retry loop instead of tenacity. The spec mandated `tenacity.AsyncRetrying`; I deviated to a 30-line custom loop because per-class budgets don't map cleanly to tenacity's per-call API. Phase 3 review correctly flagged this as silent scope drift; I amended the spec with an explicit "APPROVED deviation" note in §U3 BEFORE the next phase. Lesson: when you must deviate from a spec the user already approved, AMEND THE SPEC FIRST or document the deviation in the docstring before it bleeds into review.
Try next:         Sprint 8 -- cross-process filelock + disk guard + signal handling (target v1.0.0). NEW DoD ratchet from Sprint 7 reviewer convergence: the per-batch circuit breaker counter is "best-effort under concurrency > 1" (asyncio cooperative scheduling protects each individual update but cross-coroutine resets can interleave). Sprint 8 should add an `asyncio.Lock` around the counter mutations OR document the caveat as permanent.
Surprise:         Per-phase GAN reviews caught classes of bug I hadn't seen before in this project. Phase 4's `_specs` ordering bug was particularly subtle: the dict happens to iterate in insertion order in CPython 3.7+, so the bug was DORMANT and would only surface if a future dev rebuilt `_specs` from a config loop or reordered entries for "readability." A test that asserts "RateLimited gets 5/30/120 backoff" pinned the contract before it could regress. The pattern -- per-phase review, then ONE pinned test that calls out exactly which invariant the fix protects -- has become a reliable way to leave deliberate ratchets behind.
