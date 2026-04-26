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
