# Implementation Plan: shokz YouTube → MP3 Downloader (Production-Grade + Agile, v3.1)

> v3.1 = v3 hardened by GAN Round 1 (two adversarial critics). See **§0.6 GAN Audit** for the diff.
> v3 adds Scrum-for-solo process layer (§0.5, §8). v2 added production quality bar + project-local `./downloads/` + title-based filenames + library-first.
> Hexagonal architecture preserved. POC validated.

## Task Type
- [x] Backend (Python CLI)
- [ ] Frontend
- [ ] Fullstack

---

## 0. Production Quality Bar (NEW)

What "production grade" means here — these are non-negotiable scaffolding decisions that ride along every slice, not nice-to-haves bolted on later.

> **Foundational principle: library-first.** Use a battle-tested library for any cross-cutting concern (retries, locking, sanitization, logging, time, paths). Custom code is reserved for **domain logic** (bitrate-cap math, source-specific orchestration). See §11 for the dependency table and the explicit "wheels rejected" list.

| Concern | Mechanism | Enforcement |
|---|---|---|
| **Type safety** | `mypy --strict` on `src/shokz/` | CI gate; pre-commit |
| **Lint + format** | `ruff check` + `ruff format` | CI gate; pre-commit |
| **Tests** | `pytest` + `pytest-asyncio` + `pytest-cov`; coverage ≥ 80% on `src/shokz/{domain,application}/` | CI gate (`fail_under=80`) |
| **Atomic file writes** | All final files via `tempfile + os.replace` (POSIX-atomic on same FS) | Code review; integration tests for crash scenarios |
| **Structured logging** | stdlib `logging` + JSON formatter for `--ui json`, RichHandler for `--ui rich`. Every log carries `run_id` + `track_id` correlation IDs (contextvars) | Code review |
| **Signal handling** | `asyncio.run` w/ root task that catches SIGINT/SIGTERM, cancels in-flight tasks, `asyncio.shield`s manifest writes, exits clean | Integration test that sends SIGINT mid-download |
| **Disk-space guard** | Pre-flight check: `shutil.disk_usage(downloads/)` ≥ 2× estimated size from `yt-dlp -J` `filesize_approx` | Hard fail before download starts |
| **Manifest schema versioning** | Every JSONL line has `"schema_version": 1`. Loader rejects unknown versions with clear error | Migration policy documented |
| **Idempotency** | `(source, track_id)` is the natural key. Manifest lookup before download. Atomic move = no half-written final files | Integration test: kill + re-run, no dupe |
| **Path traversal protection** | `--name` and filename templates rejected if normalized path escapes `output_dir`; reject `..`, absolute paths, NUL bytes | Unit tests on `domain/filenames.py` |
| **Rate limiting (politeness)** | Pass `--sleep-requests 1.0` to yt-dlp; configurable | Default-on |
| **Pinned external versions** | `yt-dlp>=2026.3,<2027` in `pyproject.toml`; `ffmpeg` version checked by `doctor` | CI nightly run against pinned + latest yt-dlp |
| **Reproducible builds** | `uv.lock` committed; `python = "==3.11.*"` | CI uses `uv sync --frozen` |
| **Pre-commit hooks** | `pre-commit-config.yaml`: ruff, mypy, end-of-file-fixer, check-yaml, check-toml | `pre-commit install` documented in README |
| **CI** | GitHub Actions: lint → typecheck → test (matrix py3.11/3.12) → integration (gated). Status badge in README | `.github/workflows/ci.yml` |
| **Versioning** | Semantic versioning. `__version__` in `src/shokz/__init__.py`. `CHANGELOG.md` (Keep a Changelog format) updated per release | PR template enforces |
| **Doctor auto-runs** | `shokz download` invokes `doctor` checks first unless `--skip-doctor`. Failure aborts with actionable message | Default-on |
| **Resource limits** | Per-run: max disk usage cap, max concurrent downloads, max retries per video, total timeout | Configurable; sane defaults |
| **Observability mode** | `--ui json` emits one JSON event per significant transition (resolved, started, progress, encoded, written, failed) for piping to dashboards | Documented in README; smoke-tested |

These all show up in **Slice 0** below as concrete files.

---

## 0.5. Development Process (Agile / Scrum-for-Solo)

> **Reality check (load-bearing):** Agile is genuinely valuable on this 5-day solo project in **exactly three ways**: (1) **DoD as a ratchet** prevents the production-grade bar from quietly eroding under deadline pressure; (2) **Gherkin acceptance criteria written before code** force "what does this slice mean?" clarity; (3) **per-slice retrospective** compounds learning across a project too short to otherwise reflect. **Everything else** — story points, velocity, daily standups, planning meetings, Scrum Master role, burndown charts — is pure ceremony for a solo dev and is **dropped without guilt**. Total Agile overhead per slice budget: **≤30 min** (DoR + DoD + retro combined). If it exceeds that, we've recreated the bureaucracy Scrum was invented to escape.

### Adapt vs. drop

| Keep (adapted to solo) | Drop (ceremonial waste) |
|---|---|
| **Product Backlog** = the 9 slices in §8, ordered by dependency + risk + value | Daily Standup |
| **Sprint Goal** = one-sentence per slice | Story-point estimation / Planning Poker |
| **Definition of Ready (DoR)** | Velocity tracking |
| **Definition of Done (DoD)** | Sprint Planning *as a meeting* |
| **Sprint Review** = self-demo from clean `./downloads/` | Scrum Master role |
| **Retrospective** = `RETRO.md` append-only entry | Burndown charts |
| **Increment** = each slice tagged + runnable | Backlog Refinement *as a ceremony* |
| **User Stories** with Gherkin AC (ATDD) | Backlog estimation in points |

### Sprint cadence — **9 sprints, ½ day each**

Each slice in §8 IS one sprint. Morning = one sprint, afternoon = next sprint. **Do not** combine two slices into one 1-day sprint (weakens the per-sprint increment guarantee and tempts mid-sprint scope blending). **Do not** plan a single 5-day sprint (that's waterfall in a Scrum hat). Half-day sprints give 9 explicit goal/done cycles, 9 retro datapoints, 9 git tags — exactly the feedback density a solo dev needs to course-correct on a short project.

### User Story template (used in every slice)

```
Title:        <verb-noun, ≤8 words>
As a <persona>, I want <capability>, so that <swimming-context outcome>.

Acceptance Criteria (Gherkin / BDD — written BEFORE code):
  Given <state>
  When <action>
  Then <observable outcome>
  [Given/When/Then for each scenario, including edge cases]

Non-functional: <perf, atomicity, idempotency requirements>
Out of scope:   <explicit exclusions — defends against mid-sprint creep>
INVEST check:   I_N_V_E_S_T (tick each: Independent, Negotiable, Valuable, Estimable, Small, Testable)
```

**Personas:**
- **Swimmer** — primary user; runs the CLI from a terminal once a week to refresh pool playlist
- **Operator** — same human, but troubleshooting (uses `doctor`, reads logs, retries failures)

**Worked example — Sprint 4 (manifest + atomic + skip-existing):**

```
Title: Skip already-downloaded tracks

As a Swimmer rebuilding my pool playlist, I want shokz to skip tracks
already in ./downloads, so that re-running on a 50-URL list takes
seconds instead of re-encoding everything.

AC:
  Scenario: Manifest entry exists and file is on disk
    Given a manifest entry exists for (youtube, eiV0nvJ9fRM)
      And the corresponding .mp3 exists at downloads/Soft Piano Sleep Music.mp3
    When I run `shokz download <same URL>`
    Then exit code is 0
     And stdout shows "SKIPPED: Soft Piano Sleep Music.mp3"
     And no .tmp/ files are created
     And no ffmpeg subprocess is spawned

  Scenario: Manifest entry exists but file was deleted
    Given a manifest entry exists for (youtube, eiV0nvJ9fRM)
      And the .mp3 file does NOT exist on disk
    When I run `shokz download <same URL>`
    Then the track is re-downloaded and manifest re-recorded
     And exit code is 0

  Scenario: --force overrides skip
    Given a manifest entry exists for (youtube, eiV0nvJ9fRM)
      And the .mp3 file exists
    When I run `shokz download --force <same URL>`
    Then the track is re-downloaded
     And the manifest entry is updated (not duplicated)

  Scenario: Process killed mid-write leaves no partial final file
    Given a download is in progress for (youtube, eiV0nvJ9fRM)
    When I send SIGKILL to the shokz process during ffmpeg encode
    Then no file matching downloads/*.mp3 (non-tmp) exists for this track
     And the manifest has no entry for this track
     And re-running `shokz download <URL>` succeeds and creates the final file

Non-functional:
  - Skip decision must be < 50ms for a 1000-entry manifest
  - Atomic-write: zero observable partial files in downloads/ at any instant

Out of scope:
  - --force-all flag (separate slice)
  - Manifest migration from a future schema_version (v2 problem)

INVEST: ✓Independent ✓Negotiable ✓Valuable ✓Estimable ✓Small (½ day) ✓Testable
```

### Definition of Ready (DoR) — checklist at sprint start

Sprint cannot start until **every box ticked** in 5 minutes:

- [ ] Sprint Goal written in one sentence
- [ ] ≥ 1 User Story with full Gherkin AC (the AC become pytest test names)
- [ ] Affected files listed (paths from §1 / §13)
- [ ] Ports/contracts named (no new port invented mid-sprint)
- [ ] Test approach noted (unit / integration / e2e split)
- [ ] Dependencies on prior sprints verified merged + green CI
- [ ] Out-of-scope list written (defends against creep)
- [ ] Estimated ≤ ½ day; if larger, **split into two sprints**

### Definition of Done (DoD) — checklist at sprint end (a ratchet, never relaxed)

Sprint cannot close until **every box ticked**:

- [ ] All Gherkin AC scenarios pass as executable pytest tests
- [ ] `just lint` clean (ruff)
- [ ] `just typecheck` clean (mypy --strict)
- [ ] `just test` green; coverage ≥ 80% on touched `src/shokz/{domain,application}/`
- [ ] **Atomic-write verification** (Sprint 4+): integration test kills process mid-write and asserts no partial final files in `downloads/*.mp3`
- [ ] **Integrity verification** (Sprint 4+): integration test asserts encoded MP3 duration is within 2% of resolved `track.duration_s`
- [ ] **Manifest fsync verification** (Sprint 4+): unit test confirms `os.fsync` is called on the manifest fd AND on the parent dir after each append
- [ ] **Reconciliation scan** (Sprint 4.5+): integration test creates an orphan `.mp3` in `downloads/`, runs the next CLI invocation, asserts a WARNING is logged
- [ ] **Error translation table** (Sprint 7+): unit test enumerates every row of §7.1 and asserts the adapter raises the correct domain error
- [ ] GitHub Actions CI green on PR/branch
- [ ] `CHANGELOG.md` `[Unreleased]` updated (Keep a Changelog format)
- [ ] `README.md` usage section updated if CLI surface changed
- [ ] Conventional Commits used (e.g., `feat(manifest): skip existing tracks`)
- [ ] **Self-demo** executed against clean `./downloads/` from a fresh clone
- [ ] Git tag pushed: `v0.<sprint>.0` (or `v1.0.0` at Sprint 8)
- [ ] Retro entry appended to `RETRO.md`

The DoD is a **ratchet**: each new check-mark stays for life. Once "atomic-write verification" is required (Sprint 4), it is required for every subsequent sprint. **Allowing one slip permanently lowers the bar** — the load-bearing reason solo Agile fails.

### Backlog ordering principle: risk-adjusted user value

The 9-sprint order in §8 is fixed by:
1. **Dependency** (Sprint 0 must precede 1, etc.)
2. **User-stated risk-first** — Sprint 2 (filenames) jumps ahead of "nice" sprints because the user explicitly complained about `eiV0nvJ9fRM.mp3` filenames
3. **Production-grade load-bearing claim** — Sprint 4 (manifest + atomic) jumps ahead because it's the load-bearing claim of "production grade"

### MVP / v1.0 / v1.x release track

| Tag | Reached after | Description | Releasable to whom |
|---|---|---|---|
| **v0.0.0** | Sprint 0 | Empty package, green CI, scaffolding only | nobody (internal milestone) |
| **v0.1.0** (MVP) | Sprint 1 | `shokz download <URL>` produces a playable MP3 in `./downloads/`. Filenames may be ugly. No retry, no manifest, no progress. | the swimmer can use it tonight |
| **v0.2.0** | Sprint 2 | Title-based filenames + `--name` override | swimmer no longer sees `xyz123.mp3` |
| **v0.3.0** | Sprint 3 | Configuration + `shokz config` commands | personalized defaults |
| **v1.0.0** | Sprint 4 | Manifest + atomic writes + idempotent re-runs. Smallest version where overnight 100-URL batches can be killed and resumed safely. | **public release candidate** |
| **v1.1.0** | Sprint 5 | Playlists | playlist URLs |
| **v1.2.0** | Sprint 6 | Rich progress + ID3 tags | nicer UX |
| **v1.3.0** | Sprint 7 | Retry policy + dry-run + failure log | resilient batches |
| **v1.4.0** | Sprint 8 | Disk guard + cross-process lock + signal handling | hardened for unattended runs |
| **v1.5.0** | Sprint 9 | Doctor + library verify + structured-logging polish | full observability |

**MVP is Sprint 1.** It must be releasable, even if rough. **v1.0 is Sprint 4.** That's where the production-grade promise is fulfilled.

### Lightweight retrospective format

**File:** `RETRO.md` at repo root, append-only. Read aggregate every 3 sprints (~1.5 days), action **one** concrete change.

```markdown
## Sprint <N> — <slice name> — <YYYY-MM-DD>
**Goal:**         <one line — should match the Sprint Goal>
**Shipped?:**     yes / no
**Time actual:**  <hours> / ½-day budget
Keep:             <what worked — be specific, e.g., "library-first paid off, pathvalidate saved 2h">
Drop:             <what wasted time — e.g., "wrote custom retry before reading tenacity docs">
Try next:         <one concrete change — e.g., "write Gherkin AC before any code">
Surprise:         <unknown unknown that bit you — keep yourself honest>
```

Force one concrete `Try next:` per entry. Vague "felt productive today" entries are banned.

### Anti-patterns to avoid (solo Agile fail modes)

- **Theater estimation** — assigning Fibonacci points to your own work
- **Fake velocity** — quoting "23 points this sprint" with no team baseline
- **Cargo-cult ceremonies** — running a 30-min "planning meeting" with yourself
- **Story inflation** — splitting trivial work into 5 stories to look productive
- **DoD erosion** — "I'll add tests next sprint" — once allowed, permanent
- **Goal drift mid-sprint** — starting Sprint 4 then "while I'm here" doing Sprint 7 work; defer to backlog
- **Skipping the demo** — never running the CLI from a clean `./downloads/`; ships broken installs
- **Retro as journal** — vague "felt productive"; force `Try next:` action
- **ATDD-skip** — coding before writing the Gherkin; AC must come first, they ARE the test names
- **Branch-and-pray** — committing without Conventional Commits; CHANGELOG generation breaks

### Tooling for the process

| Process artifact | Where it lives | Tool |
|---|---|---|
| Backlog | `.claude/plan/shokz-downloader.md` §8 | this plan |
| Sprint Goal | top of each PR description + `CHANGELOG.md [Unreleased]` line | git + PR template |
| User Stories + AC | one `.md` per sprint in `docs/sprints/sprint-<N>.md` | markdown |
| Acceptance tests | `tests/acceptance/test_sprint_<N>_*.py` — Gherkin scenarios become pytest test names | pytest + pytest-bdd (optional) or just plain pytest |
| DoR check | top of each `docs/sprints/sprint-<N>.md` | markdown checklist |
| DoD check | bottom of each PR description | PR template |
| Retro | `RETRO.md` at repo root | append-only markdown |
| Increments | git tags `v0.<N>.0` and `v1.<M>.0` | git |
| Conventional Commits | enforced via commit-msg hook | `pre-commit` |

---

## 0.6. GAN Round 1 Audit — Adopted Hardening

Two adversarial critics independently reviewed v3 (a pragmatic over-engineering reviewer + a silent-failure / bad-fallback / error-translation reviewer). Convergent and individual findings, with the concrete change adopted in v3.1:

### Convergent (both critics)
| Finding | Adopted change |
|---|---|
| Sprint 4 overloaded — manifest + atomic + skip + library list + SIGKILL test in ½ day is unrealistic | **Split** into Sprint 4 (manifest + atomic + integrity checks) and Sprint 4.5 (skip-existing + library list). See §8 |
| "v1.0 = production-grade" at Sprint 4 is premature; lock + signals + disk guard land later | **v1.0.0 moved to Sprint 8** (after disk guard + lock + signals). Sprint 4 = `v0.4.0` "crash-safe single-process writes". See §8 release table |

### Pragmatist's unique findings adopted
| Finding | Change |
|---|---|
| Logging stack inconsistent (§0 stdlib vs §11 structlog) | **Pick one**: stdlib `logging` + RichHandler + JSON formatter for `--ui json`. Drop `structlog` from §11 |
| Filename identity unstable — manifest natural key is `(source, track_id)` but file rename loses linkage | `ManifestEntry` adds **`original_title`** field separate from `filename_stem` |
| Over-engineering: `--ui json`, `library verify --rebuild-index`, `run_id_strategy` knob, separate `failures.jsonl`, doctor auto-run | **Defer / simplify**: `--ui json` deferred to Sprint 9; `run_id` always timestamp (not configurable); failures stored as `status=failed` rows in manifest; `doctor` opt-in not auto |
| Architecture too wide for problem | **Defer ports**: `LockPort` rolled into Sprint 8; `DoctorPort` rolled into Sprint 9. Other ports kept (Protocols are cheap) |

### Pragmatist findings rejected (with justification)
| Finding | Why rejected |
|---|---|
| Drop manifest schema versioning | Two characters (`"schema_version": 1`) bought now save weeks later |
| Drop `platformdirs` | One dep, correct cross-platform paths, free |
| Collapse all ports to 3 | `typing.Protocol` ports cost nothing; collapsing creates god-objects |

### Silent-failure-hunter's unique findings adopted
| Failure mode | Mitigation added |
|---|---|
| yt-dlp exit 0 + 0-byte/truncated raw file | Post-download size check ≥ `MIN_RAW_BYTES` and optional `ffprobe -show_entries format=duration` on the raw file → raise `SourceFileCorrupt` |
| ffmpeg exit 0 + truncated audio | Post-encode `encoder.probe_duration(partial)` must be within **2%** of `track.duration_s` → raise `EncodingFailed` |
| Manifest entry not durable on power loss | Atomic-write protocol step 7a: `manifest_file.flush(); os.fsync(fd); os.fsync(dirname(manifest))` |
| `pathvalidate` mangles `「♪♪♪」`/`CON`/`...` to empty | Fallback `untitled-{id}`; manifest preserves `original_title` separately |
| SIGKILL between `os.replace` and manifest append | `asyncio.shield` is acknowledged insufficient against SIGKILL. Add **startup reconciliation scan**: any `*.mp3` in `downloads/` not in manifest index → log WARNING, surface in `library verify` |
| Expired `--cookies-from-browser` → silent quality downgrade | After resolve, if `source_bitrate_kbps < target preset's bitrate × 0.5` → log WARNING |
| `collision=skip` treats unrelated videos with same name as already-done | Skip only when existing manifest entry's `(source, track_id)` matches current track. Otherwise fall through to suffix policy |
| Bad `cap_bitrate` contract when `source_kbps is None` | Documented contract: `None` ⇒ uncapped (return target). Unit-tested |
| `library verify` missing-file behavior unspecified | Spec: exits non-zero, lists orphan manifest entries and orphan files (each direction) |
| `yt_dlp.utils.DownloadError` too generic for taxonomy | New §7.1 **error translation table** with message-pattern → domain-error mapping |
| ENOSPC at 3 distinct sites needs distinct handling | Translation table: ffmpeg ENOSPC → `DiskFull` retry-after-cleanup; `os.replace` ENOSPC → fatal cleanup needed; manifest append ENOSPC → fatal + run `library verify` |
| Stale `filelock.Timeout` (lock holder SIGKILLed) | Lock file embeds PID; `AnotherRunInProgress` message includes `inspect .shokz/locks/, remove if PID dead` guidance |

### Both critics defended (kept unchanged)
- `./downloads/` + `.tmp/` + `.shokz/` layout — same-FS atomicity, inspectable state, bounded cleanup. Strongest decision in the plan.

### Rounds remaining
v3.1 is committed; round 2 only if user requests. Convergence threshold met for round 1.

---

## 1. Architecture (Hexagonal — Refined)

```
shokz/
├── pyproject.toml                # uv-managed; ruff + mypy + pytest config
├── uv.lock                       # committed
├── pre-commit-config.yaml        # ruff, mypy, basic hygiene
├── .github/
│   └── workflows/
│       ├── ci.yml                # lint + typecheck + test + integration
│       └── nightly-ytdlp.yml     # weekly run against latest yt-dlp
├── shokz.toml.example            # commented sample config
├── CHANGELOG.md
├── README.md
├── Justfile                      # task runner (just lint, just test, just install)
├── poc/                          # UNTOUCHED — kept as reference
├── downloads/                    # ALL output goes here (gitignored)
│   ├── *.mp3                     # final files (title-based names)
│   ├── .tmp/                     # in-progress: .webm, .m4a, .part, half-encoded .mp3
│   └── .shokz/                   # state (gitignored)
│       ├── manifest.jsonl        # append-only, schema_version=1
│       ├── failures.jsonl        # append-only
│       ├── runs/<run_id>.json    # per-run summary
│       └── locks/                # advisory lock dir (one file per active run)
└── src/shokz/
    ├── __init__.py               # __version__ = "0.1.0"
    │
    ├── domain/                   # PURE: no third-party, no I/O, no asyncio
    │   ├── models.py             # Track, AudioSpec, ManifestEntry, FailureEntry, TrackStatus, RunId
    │   ├── presets.py            # SWIM_LOW / SWIM_STANDARD / SWIM_HIGH
    │   ├── filenames.py          # WRAPS pathvalidate.sanitize_filename + template render + collision suffix
    │   ├── bitrate.py            # cap_bitrate_to_source (per-channel-aware) — pure domain math
    │   ├── paths.py              # path-traversal guard (uses pathlib + pathvalidate)
    │   └── errors.py             # taxonomy: SourceUnavailable, AuthRequired, FormatUnavailable, EncodingFailed, DiskFull, FilesystemError, ConfigError
    │
    ├── application/
    │   ├── ports/                # typing.Protocol — adapters need not inherit
    │   │   ├── inbound/
    │   │   │   └── download.py            # DownloadUseCase Protocol (single facade)
    │   │   └── outbound/
    │   │       ├── video_source.py        # VideoSourcePort (resolve + download_audio + can_handle)
    │   │       ├── encoder.py             # AudioEncoderPort (encode + probe_bitrate + probe_duration)
    │   │       ├── tagger.py              # MetadataTaggerPort
    │   │       ├── manifest.py            # ManifestPort
    │   │       ├── failure_log.py         # FailureLogPort
    │   │       ├── filesystem.py          # FileSystemPort (atomic move, free_space, mkdir, exists)
    │   │       ├── progress.py            # ProgressReporterPort (start/advance/finish/failed)
    │   │       ├── clock.py               # ClockPort
    │   │       ├── lock.py                # LockPort (per-run advisory lock)
    │   │       └── doctor.py              # DoctorPort (preflight checks)
    │   ├── use_cases/
    │   │   ├── download_track.py          # one URL end-to-end
    │   │   ├── batch_download.py          # N URLs, bounded concurrency
    │   │   ├── expand_playlist.py         # playlist → list[Track] (no download)
    │   │   ├── retry_failed.py            # reads failure log → batch_download
    │   │   ├── plan_only.py               # resolve + plan (dry run)
    │   │   ├── library_query.py           # list/show/verify
    │   │   └── doctor_run.py
    │   └── policies/
    │       ├── retry.py                   # WRAPS tenacity.AsyncRetrying — configures classified errors
    │       ├── skip_existing.py           # manifest lookup
    │       ├── filename_resolver.py       # template + collision (uses domain/filenames.py)
    │       ├── disk_guard.py              # pre-flight space check (shutil.disk_usage + humanfriendly)
    │       └── concurrency.py             # bounded gather wrapper
    │
    ├── adapters/
    │   ├── inbound/
    │   │   └── cli/
    │   │       ├── app.py                 # Typer root
    │   │       ├── commands/
    │   │       │   ├── download.py        # `shokz download URL...`
    │   │       │   ├── playlist.py        # `shokz playlist URL`
    │   │       │   ├── retry.py           # `shokz retry [RUN_ID]`
    │   │       │   ├── library.py         # `shokz library list|show|verify`
    │   │       │   ├── config_cmd.py      # `shokz config show|init|path`
    │   │       │   └── doctor.py          # `shokz doctor`
    │   │       ├── flags.py               # shared option definitions
    │   │       └── formatting.py          # Rich tables, JSON event encoding
    │   └── outbound/
    │       ├── ytdlp_source.py            # resolve via yt_dlp.YoutubeDL Python API; download via subprocess (process isolation)
    │       ├── ffmpeg_encoder.py          # FfmpegEncoder — `-progress pipe:1` parsing
    │       ├── mutagen_tagger.py          # ID3v2.4: TIT2 (title), TPE1 (uploader), TALB (playlist), WOAS (URL), TXXX:source_id
    │       ├── jsonl_manifest.py          # append-only JSONL + projected index.json (rebuildable)
    │       ├── jsonl_failure_log.py
    │       ├── local_filesystem.py        # tempfile + os.replace; fsync directory
    │       ├── progress/
    │       │   ├── rich_reporter.py       # multi-bar via rich.live + throttling
    │       │   ├── null_reporter.py       # quiet mode + tests
    │       │   └── json_reporter.py       # one JSON event per line on stdout
    │       ├── system_clock.py
    │       ├── file_lock.py               # WRAPS filelock.FileLock — cross-platform advisory lock
    │       └── doctor_checks.py           # ffmpeg version, yt-dlp version, EJS reachable, disk free, output writable, no orphan .part
    │
    ├── config/
    │   ├── schema.py                      # Pydantic AppConfig + validators
    │   ├── defaults.py                    # built-in defaults
    │   ├── presets.py                     # named audio presets (re-exported)
    │   └── loader.py                      # builtin < user TOML < project TOML < env < CLI
    │
    ├── observability/
    │   ├── logging.py                     # contextvars-based correlation ID injection
    │   └── events.py                      # structured event types for JSON UI mode
    │
    └── composition.py                     # the only file importing both halves
                                           # exposes Container @dataclass(frozen=True)

tests/
├── unit/
│   ├── domain/                            # filenames, bitrate cap, paths, presets — pure, fast
│   ├── application/                       # use cases with fakes
│   └── config/                            # precedence, validation
├── integration/
│   ├── ytdlp/                             # real network, gated by INTEGRATION=1
│   ├── ffmpeg/                            # locally-generated sine waves, no network
│   ├── manifest/                          # crash-resume scenarios with tmp_path
│   └── signals/                           # SIGINT mid-download
├── e2e/
│   └── cli/                               # Typer CliRunner against real adapters w/ mocked network
└── fakes.py                               # FakeVideoSourcePort, FakeManifestPort, FakeClock, ...
```

---

## 2. Filenames (NEW — Detailed)

This is the section that addresses your core feedback: **no more `eiV0nvJ9fRM.mp3`**.

### Default behavior

- Final filename = **video title**, sanitized, with `.mp3` extension.
- Example: `Soft Piano Sleep Music.mp3` instead of `eiV0nvJ9fRM.mp3`.

### Filename template (configurable)

`filenames.template` in TOML defines the structure. Built-in tokens:

| Token | Meaning | Example |
|---|---|---|
| `{title}` | Video title | `Soft Piano Sleep Music` |
| `{uploader}` | Channel / uploader | `Relaxing Music` |
| `{id}` | Source-specific ID | `eiV0nvJ9fRM` |
| `{source}` | Source name | `youtube` |
| `{duration}` | `HH:MM:SS` | `08:05:13` |
| `{date}` | Upload date `YYYY-MM-DD` | `2024-11-30` |

**Default template:** `{title}` (just the title — what you asked for).
**Other useful presets** (commented in `shokz.toml.example`):
- `{title} [{id}]` — append ID for guaranteed uniqueness
- `{uploader} - {title}` — sort by channel in file managers
- `{date} - {title}` — chronological sort

### CLI override

```
shokz download --name "Sleep Mix Vol 1" "https://youtube.com/watch?v=..."
```

Rules:
- `--name` only valid with **exactly one URL** (errors otherwise: `--name requires exactly one URL`)
- `--name` is treated as the literal filename stem (still sanitized; `.mp3` added)
- Path-traversal blocked: `--name "../etc/passwd"` rejected with `NameOutsideOutputDir` error

### Sanitization (`domain/filenames.py`)

Pure function `sanitize_filename(raw: str, *, max_len: int, fat_safe: bool) -> str`:

1. Strip ASCII control chars (`\x00`–`\x1f`)
2. If `fat_safe`: replace `< > : " / \ | ? *` with `_`
3. Strip leading/trailing dots and spaces (Windows reserves these)
4. Reject reserved names (`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`)
5. Truncate to `max_len` **bytes** (UTF-8 aware — won't split a multibyte char). Default 120.
6. If empty after sanitization → `"untitled-{id}"` fallback

Unicode preserved (Chinese / Japanese / emoji titles work on exFAT).

### Collision handling (`policies/filename_resolver.py`)

Pure logic. Given a desired path and an existence-check function:

| Strategy | TOML | Behavior |
|---|---|---|
| `suffix` (default) | `filenames.collision = "suffix"` | `Title.mp3`, `Title (2).mp3`, `Title (3).mp3` ... |
| `overwrite` | `filenames.collision = "overwrite"` | Replace existing file |
| `skip` | `filenames.collision = "skip"` | Treat as already-done; record in manifest |
| `fail` | `filenames.collision = "fail"` | Raise `FilenameCollision` error |

`--skip-existing` (default true) operates on **manifest** keys, not filename collision. So manifest-known tracks skip via `skip_existing.py`; truly-new tracks with name conflicts hit `filename_resolver.py`.

---

## 3. Output Layout (Refined — Project-Local Only)

Everything the app touches lives under one root: **`./downloads/`** (relative to CWD by default; configurable, but no system folders).

```
downloads/
├── Soft Piano Sleep Music.mp3              # final file (title-based)
├── 8 Hours of Beautiful Piano Music.mp3
├── My Custom Mix.mp3                       # via --name
├── .tmp/                                   # in-progress (auto-cleaned on success)
│   ├── youtube-eiV0nvJ9fRM-{run_id}.webm   # raw download
│   ├── youtube-eiV0nvJ9fRM-{run_id}.mp3    # mid-encode (renamed atomically on completion)
│   └── *.part                              # yt-dlp partial files
└── .shokz/                                 # app state
    ├── manifest.jsonl
    ├── failures.jsonl
    ├── runs/
    │   └── 2026-04-26T20-30-12.json        # per-run summary
    └── locks/
        └── shokz.lock                      # only one shokz process runs against this dir at once
```

### Atomic write protocol (every download)

1. yt-dlp writes raw audio to `.tmp/<source>-<id>-<run_id>.<ext>`
2. ffmpeg encodes to `.tmp/<source>-<id>-<run_id>.mp3.partial`
3. Mutagen tags the `.partial` file
4. `os.replace(partial, final_path)` — POSIX-atomic on same filesystem
5. `os.fsync(dirname(final_path))` to durably flush directory entry
6. Append `ManifestEntry` to JSONL
7. Delete raw file from `.tmp/` (unless `keep_raw = true`)

If killed at any step, the final file at `Soft Piano Sleep Music.mp3` either exists fully or doesn't exist at all — never partial.

### `.tmp/` cleanup
- On `shokz doctor` and on each run startup: scan `.tmp/` for files older than 24h with no matching active lock → delete.
- Files for the current `run_id` are owned by that run.

### `.shokz/locks/`
- `flock`-based advisory lock prevents two `shokz` processes from racing on the same `downloads/` dir.
- Use case: user double-clicks the script accidentally → second instance fails fast with `AnotherRunInProgress`.

---

## 4. Configurable Parameters (Updated)

Defaults reflect project-local + title-based decisions.

| Knob | Type | **Default (changed in v2)** | TOML key | CLI flag |
|---|---|---|---|---|
| Output directory | path | **`./downloads`** (was `~/Music/Shokz`) | `general.output_dir` | `-o / --output` |
| Filename template | str | **`{title}`** (was `{uploader} - {title} [{id}]`) | `filenames.template` | (TOML only) |
| Filename override (single URL) | str | none | (CLI only) | **`--name`** (NEW) |
| Collision policy | enum | `suffix` | `filenames.collision` | (TOML only) |
| FAT-safe sanitization | bool | true | `filenames.fat_safe` | (TOML only) |
| Filename max length (bytes) | int | 120 | `filenames.max_length` | (TOML only) |
| Concurrency | int | 3 | `general.concurrency` | `-c / --concurrency` |
| Audio preset | enum | `swim-standard` | `audio.preset` | `-p / --preset` |
| Custom bitrate (kbps) | int | 64 | `audio.bitrate_kbps` | `-b / --bitrate` |
| Channels | 1\|2 | 1 | `audio.channels` | `--channels` |
| Sample rate (Hz) | int | 44100 | `audio.sample_rate_hz` | (TOML only) |
| Bitrate auto-cap | bool | true | `audio.cap_to_source` | `--no-autocap` |
| Cookies from browser | enum\|null | null | `sources.youtube.cookies_from_browser` | `--cookies-from edge\|chrome\|safari` |
| Cookies file | path\|null | null | `sources.youtube.cookies_file` | `--cookies-file` |
| EJS source | str | `ejs:github` | `sources.youtube.ejs_source` | `--ejs-source` |
| yt-dlp request sleep (s) | float | 1.0 | `sources.youtube.sleep_requests` | `--sleep` |
| Retry max attempts | int | 3 | `retry.max_attempts` | `--retries` |
| Retry initial backoff (s) | float | 2.0 | `retry.initial_backoff_s` | `--backoff` |
| Retry multiplier | float | 2.0 | `retry.backoff_multiplier` | (TOML only) |
| Retry jitter | bool | true | `retry.jitter` | (TOML only) |
| Keep raw downloads | bool | false | `general.keep_raw` | `--keep-raw` |
| Skip existing | bool | true | `general.skip_existing` | `--skip-existing / --force` |
| **Disk-space safety multiplier** | float | 2.0 | `general.disk_safety_multiplier` | (TOML only) |
| **Max disk per run (bytes\|null)** | int\|null | null | `general.max_disk_per_run` | `--max-disk` |
| **Max total timeout (s)** | int\|null | null | `general.max_total_timeout_s` | `--timeout` |
| UI mode | enum | `rich` | `ui.progress` | `--ui rich\|plain\|json\|none` |
| Log level | enum | INFO | `logging.level` | `--log-level` |
| Log file | path\|null | null | `logging.file` | `--log-file` |
| Run ID strategy | enum | `timestamp` | `general.run_id_strategy` | (TOML only) |
| Skip doctor | bool | false | (CLI only) | `--skip-doctor` |
| Dry run | bool | false | (CLI only) | `--dry-run` |

### Config precedence (highest wins)
1. CLI flags
2. Env vars `SHOKZ_*` (e.g., `SHOKZ_AUDIO__BITRATE_KBPS=96`)
3. Project-local `./shokz.toml`
4. User `~/.config/shokz/config.toml`
5. Built-in defaults

`shokz config show` annotates each value with its source.

---

## 5. CLI (Typer) Commands

```
shokz download URL [URL...]              # one or more direct URLs
    --name "Custom Filename"             # NEW; only valid with single URL
    -p / --preset swim-low|swim-standard|swim-high|custom
    -b / --bitrate 96                    # used with --preset custom
    -c / --concurrency 3
    -o / --output ./other-folder
    --cookies-from edge|chrome|safari
    --cookies-file PATH
    --ejs-source ejs:github
    --skip-existing / --force
    --keep-raw / --no-keep-raw
    --no-autocap                         # disable bitrate cap-to-source
    --max-disk 5G                        # SI suffixes
    --timeout 1800                       # seconds
    --retries 3
    --backoff 2.0
    --sleep 1.0
    --ui rich|plain|json|none
    --log-level DEBUG|INFO|WARNING|ERROR
    --log-file PATH
    --skip-doctor
    --dry-run

shokz playlist URL                       # explicit playlist semantics
    --playlist-subdir / --no-playlist-subdir
    (otherwise same flags as download, except --name not allowed)

shokz retry [RUN_ID]                     # default: latest run
    --only NETWORK,HTTP_5XX
    (re-uses download flags)

shokz library list                       # table
shokz library show TRACK_ID
shokz library verify                     # check files match manifest

shokz config show                        # effective config + per-key source
shokz config init                        # write commented shokz.toml
shokz config path                        # which files were loaded

shokz doctor                             # preflight checks
```

---

## 6. Use Cases (Refined Sequence)

### `DownloadTrackUseCase.execute(input)`

```
1. Resolve track via VideoSourcePort.resolve(url)
   → Track{id, title, uploader, duration_s, source_bitrate_kbps, source_channels, original_url, thumbnail_url}

2. policies.filename_resolver.resolve(
     template=config.filenames.template,
     override_name=input.name,        # NEW — from --name
     track=track,
     output_dir=config.general.output_dir,
     collision_policy=config.filenames.collision,
     fs=fs,
     manifest=manifest,
   ) → final_path

   - Renders template (or uses override_name)
   - Sanitizes (FAT-safe, unicode-aware, max_len)
   - Path-traversal guard: assert final_path.parent == output_dir.resolve()
   - Resolves collisions via configured policy

3. policies.skip_existing.check(track, manifest, final_path)
   - If manifest says done AND final_path exists AND not input.force → return SKIPPED

4. policies.disk_guard.check(estimated_bytes, output_dir, fs, safety_multiplier)
   - estimated_bytes from yt-dlp -J filesize_approx
   - Raises DiskFull if insufficient

5. cap_bitrate_to_source(target_spec, track.source_bitrate_kbps, track.source_channels) → effective_spec

6. Within retry_policy.run():
     a. raw_path = video_source.download_audio(track, dest=tmp_dir, options, progress)
     b. partial_mp3 = encoder.encode(raw_path, dest=tmp_dir / f"...mp3.partial", spec=effective_spec, progress)
     c. tagger.tag(partial_mp3, track, extra={"source_url": track.original_url, "source_id": track.id})
     d. fs.atomic_move(partial_mp3, final_path)        # os.replace + fsync(dir)
     e. manifest.record(ManifestEntry(...))            # append-only JSONL, schema_version=1
     f. if not config.general.keep_raw: fs.remove(raw_path)
     g. progress.finish(track.id, status=SUCCESS)

7. On any error within retry exhaustion:
     - Translate infra exception → domain error
     - failure_log.record(FailureEntry(...))
     - progress.failed(track.id, error_msg)
     - Do not propagate — return FAILED result for batch use case
```

### `BatchDownloadUseCase.execute(input)`

```
1. Acquire run-level lock via lock.acquire(output_dir / ".shokz/locks/shokz.lock")
2. doctor.run() unless input.skip_doctor
3. For each URL: route to matching VideoSourcePort.can_handle()
   - Resolve all (concurrent, gather)
   - Flatten + dedupe on (source, track_id)
4. If input.name and len(tracks) > 1 → raise NameAmbiguous
5. If input.dry_run → render plan via progress, return
6. asyncio.Semaphore(config.general.concurrency)
7. asyncio.gather(*[bounded(DownloadTrackUseCase.execute(...)) for track in tracks])
   - Each wrapped in error-isolating boundary (per-track failures don't kill batch)
   - asyncio.shield around critical manifest writes
8. On SIGINT/SIGTERM:
     - Cancel in-flight subprocess tasks
     - Wait for current manifest writes to complete (shielded)
     - Write run summary to .shokz/runs/<run_id>.json
     - Release lock
     - Exit code 130 (SIGINT convention)
9. Otherwise:
     - Write run summary
     - Release lock
     - Return BatchDownloadResult{succeeded, skipped, failed, elapsed_s}
```

---

## 7. Error Taxonomy (`domain/errors.py`)

```python
class ShokzError(Exception): ...

# Source / resolution
class SourceUnavailable(ShokzError): ...        # 404, video deleted
class AuthRequired(ShokzError): ...             # age-gated, members-only, region-locked
class FormatUnavailable(ShokzError): ...        # EJS missing, no audio formats
class RateLimited(ShokzError): ...              # 429 / IP throttled

# Download
class DownloadFailed(ShokzError): ...
class NetworkError(ShokzError): ...

# Encoding
class EncodingFailed(ShokzError): ...
class SourceFileCorrupt(ShokzError): ...

# Filesystem / config
class DiskFull(ShokzError): ...
class FilesystemError(ShokzError): ...
class FilenameCollision(ShokzError): ...
class NameOutsideOutputDir(ShokzError): ...
class NameAmbiguous(ShokzError): ...            # --name + multiple URLs

# State
class AnotherRunInProgress(ShokzError): ...
class ManifestCorrupt(ShokzError): ...
class ConfigError(ShokzError): ...
```

`RetryPolicy` classifies errors: `NetworkError`, `RateLimited` → retry; `AuthRequired`, `FormatUnavailable`, `DiskFull`, `NameOutsideOutputDir` → fail immediately.

### 7.1 Error Translation Table (added by GAN — closes silent-failure gap)

The infrastructure layer raises generic exceptions (`yt_dlp.utils.DownloadError`, `OSError`, `subprocess.CalledProcessError`). The adapter layer **must** translate them to domain errors using the table below. **Misclassification is the most common silent failure** — e.g. an `AuthRequired` retried as `NetworkError` wastes 3 attempts then fails opaquely.

| Infra exception | Inspection | Domain error | Retry? |
|---|---|---|---|
| `DownloadError` containing `"Sign in to confirm your age"` | message regex | `AuthRequired` | ❌ no |
| `DownloadError` containing `"Private video"` / `"Video unavailable"` / `"removed"` | message regex | `SourceUnavailable` | ❌ no |
| `DownloadError` containing `"HTTP Error 429"` / `"Too Many Requests"` | message regex | `RateLimited` | ✅ yes (long backoff) |
| `DownloadError` containing `"This video is not available in your country"` | message regex | `AuthRequired` | ❌ no (cookies may help) |
| `DownloadError` containing `"Requested format is not available"` | message regex | `FormatUnavailable` | ❌ no |
| `DownloadError` containing `"HTTP Error 5"` (5xx) | message regex | `NetworkError` | ✅ yes |
| `DownloadError` (other / unrecognized) | default | `DownloadFailed` | ✅ yes (1 attempt) |
| `subprocess.TimeoutExpired` (yt-dlp or ffmpeg) | type | `NetworkError` (yt-dlp) / `EncodingFailed` (ffmpeg) | ✅ yes |
| `OSError` `errno == ENOSPC` during ffmpeg encode | errno + site | `DiskFull` | ❌ no (cleanup `.tmp/` then fail) |
| `OSError` `errno == ENOSPC` during `os.replace` to `downloads/` | errno + site | `DiskFull` | ❌ no (FATAL — partial state in `.tmp/`) |
| `OSError` `errno == ENOSPC` during manifest append | errno + site | `DiskFull` + `ManifestInconsistent` | ❌ no (FATAL — final file exists, manifest doesn't) |
| `OSError` `errno == EACCES` on `downloads/` | errno | `FilesystemError` | ❌ no |
| `filelock.Timeout` and PID in lock file is dead | check `os.kill(pid, 0)` | `StaleLock` (NEW domain error) — emit "remove `.shokz/locks/shokz.lock`" guidance | ❌ no |
| `filelock.Timeout` and PID in lock file is alive | check `os.kill(pid, 0)` | `AnotherRunInProgress` | ❌ no |
| Post-download size check fails (raw < `MIN_RAW_BYTES`) | size check | `SourceFileCorrupt` | ✅ yes (1 attempt) |
| Post-encode duration check fails (>2% deviation from `track.duration_s`) | `ffprobe` | `EncodingFailed` | ✅ yes (1 attempt) |
| `pathvalidate` returns empty string after sanitization | empty check | (no error) — fallback to `untitled-{id}` | n/a |

**DoD requirement:** Sprint 7 ships a unit test that **enumerates every row of this table** and asserts the adapter raises the correct domain error.

Add to errors.py: `class StaleLock(ShokzError): ...`, `class ManifestInconsistent(ShokzError): ...`

---

## 8. Build Order = Sprint Backlog (9 sprints, ½-day each)

> Each sprint = one slice. Morning sprint + afternoon sprint per working day. DoR before start, DoD before close. See §0.5 for process detail.

| Sprint | Slice | **Sprint Goal (one line)** | Tag | Deliverable | Effort |
|---|---|---|---|---|---|
| **0** | Production scaffold | *Empty package builds, lints, type-checks, tests, and CI green — proving the quality bar enforces itself* | `v0.0.0` | `pyproject.toml` (uv, ruff, mypy strict, pytest, coverage), `pre-commit-config.yaml`, `Justfile`, `.github/workflows/ci.yml`, `CHANGELOG.md`, `README.md` skeleton, `.gitignore`, `RETRO.md`, `downloads/.gitkeep`, structlog setup, `tests/test_smoke.py`. | ~½ day |
| **1** (MVP) | POC parity in hexagonal shell | *A swimmer can run `shokz download <URL>` and get a playable MP3 in `./downloads/`* | `v0.1.0` | Minimal `domain/`, `VideoSourcePort` + `AudioEncoderPort` + `ProgressReporterPort` (null), `BatchDownloadUseCase` (no retry/manifest), `ytdlp_source.py` (resolve via `yt_dlp.YoutubeDL` Python API; download via subprocess) + `ffmpeg_encoder.py`, Typer `download` command. | ~1 day |
| **2** | Title-based filenames + `--name` | *Files in `./downloads/` are named after the video title, not the YouTube ID* | `v0.2.0` | `domain/filenames.py` (wrapping `pathvalidate`), `policies/filename_resolver.py`, default template `{title}`, collision suffix policy, `--name` flag with single-URL guard, path-traversal protection. | ~½ day |
| **3** | Configuration | *The swimmer can override every default via `shokz.toml`, env, or CLI flags — and `shokz config show` proves which source won* | `v0.3.0` | Pydantic `AppConfig`, TOML loader, env+CLI merge, `shokz config show/init/path`. All Sprint 1+2 knobs wired through config. | ~½ day |
| **4** | Manifest + atomic writes + integrity checks | *A killed download leaves no partial final files; a successful download is provably the right length and not silently truncated* | `v0.4.0` | `jsonl_manifest.py` (schema v1, with `original_title`), `local_filesystem.py` (atomic `os.replace` + `fsync(file)` + `fsync(dir)`), **post-download size check**, **post-encode `probe_duration` within 2%**, manifest fsync. SIGKILL integration test in DoD. | ~½ day |
| **4.5** | Skip-existing + library list + reconciliation | *Re-running `shokz download` on already-completed URLs is near-instant; orphan files / orphan manifest entries are surfaced (not silently ignored)* | `v0.5.0` | `policies/skip_existing.py` (matches on `(source, track_id)` only — not filename), `--skip-existing/--force`, `library list`, **startup reconciliation scan** (orphan files in `downloads/` not in manifest → WARNING), collision-policy `skip` semantic fix. | ~½ day |
| **5** | Source resolution + playlists | *The swimmer can paste a playlist URL and get one MP3 per video, optionally in a per-playlist subdir* | `v0.6.0` | `yt-dlp` `flat-playlist` resolve path, `expand_playlist` use case, `shokz playlist`, per-playlist subdir option, >50-item confirmation prompt. | ~½ day |
| **6** | Rich progress + ID3 tagging + cookie quality guard | *Concurrent downloads show per-track bars; completed MP3s carry full ID3 tags; degraded cookie sessions warn before silently downgrading audio* | `v0.7.0` | `rich_reporter.py` (multi-bar, throttled), `mutagen_tagger.py`, `null_reporter.py` + `plain_reporter.py` (NOT `json_reporter` — deferred to Sprint 9). Cookie-quality guard: WARNING when `source_bitrate < preset_target × 0.5`. | ~½ day |
| **7** | Retry + bitrate cap + dry-run + error translation | *Transient YouTube errors retry with classified backoff; auth/format errors fail fast (no retry); cap-to-source handles `None` correctly; the swimmer can preview a batch without downloading* | `v0.8.0` | `policies/retry.py` (wrapping `tenacity`) with **explicit yt-dlp `DownloadError` message-pattern → domain-error table** (see new §7.1), `domain/bitrate.py` (with documented `None` contract), `--dry-run`. Failed tracks recorded in manifest with `status=failed` (no separate JSONL). | ~½ day |
| **8** (v1.0 ⭐) | Disk guard + cross-process lock + signal handling | *Out-of-disk fails before download starts (3 ENOSPC sites distinguished); two concurrent runs against same `./downloads/` are prevented with stale-lock guidance; SIGINT cleanly cancels in-flight tasks* | `v1.0.0` | `policies/disk_guard.py` (uses `humanfriendly`, distinguishes ENOSPC at ffmpeg / `os.replace` / manifest append), `file_lock.py` (wraps `filelock`, embeds PID for stale detection), asyncio cancellation + `asyncio.shield` for manifest, SIGINT integration test. **`shokz retry` command** lands here (reads manifest `status=failed`). | ~½ day |
| **9** | Doctor + library verify + JSON observability | *`shokz doctor` validates environment opt-in in <2s; `library verify` reports orphan files AND orphan manifest entries with non-zero exit; `--ui json` emits structured events for piping to a dashboard* | `v1.1.0` | `doctor_run.py`, `doctor_checks.py` (opt-in only — never auto), `library verify` (orphan reconciliation, exits non-zero on mismatch — no `--rebuild-index` flag), `.tmp/` orphan cleanup, `--ui json` event stream documented in README, end-to-end correlation IDs. | ~½ day |

**Total: ~5 focused days = 10 sprints (Sprint 4.5 added by GAN).** Each sprint closes with: tag pushed, CHANGELOG updated, RETRO entry, DoD checklist signed off.

### Releases on this sprint backlog (revised by GAN)

- **v0.1.0 (Sprint 1)** = MVP — `shokz download URL` produces a playable MP3, usable tonight
- **v0.2.0–v0.3.0** (Sprints 2–3) = filenames, configuration
- **v0.4.0 (Sprint 4)** = crash-safe single-process writes (atomic + integrity checks). NOT yet "production-grade".
- **v0.5.0 (Sprint 4.5)** = idempotent re-runs with reconciliation
- **v0.6.0–v0.8.0** (Sprints 5–7) = playlists, progress UX, retry + error classification
- **v1.0.0 (Sprint 8 ⭐)** = production-grade promise fulfilled — disk guard + cross-process lock + signal handling complete. **This is the public release candidate.** Smallest version where overnight 100-URL batches can be killed, resumed, run concurrently with another instance (rejected), and survive disk-full mid-encode.
- **v1.1.0 (Sprint 9)** = doctor + library verify + JSON observability

> **GAN truthfulness note:** The previous v3 plan claimed v1.0 at Sprint 4. Both adversarial critics flagged this as overpromising — without locks, signal handling, and disk guard, "production-grade" is a marketing term, not a technical claim. v3.1 honors the critique: v0.4.0 = crash-safe-single-process, v1.0.0 = the operational safeguards needed for unattended overnight runs.

---

## 9. Risks & Gotchas (Carried Forward + New)

1. **Subprocess buffer deadlocks** — always `await proc.communicate()` not `proc.wait()`. Pipe stdout only for `--print`; redirect stderr to DEVNULL unless debugging.
2. **EJS first-run download** — 2–5s GitHub fetch, cached in `~/.cache/yt-dlp/`. Doctor validates.
3. **ffprobe bitrate** — read JSON from `ffprobe -v quiet -print_format json -show_streams -show_format`, fall back `streams[0].bit_rate` → `format.bit_rate`. Strings in bps.
4. **ffmpeg progress** — use `-progress pipe:1 -nostats` (machine-readable), parse `out_time_ms=`. Never parse stderr.
5. **Signal handling** — asyncio raises `CancelledError` on SIGINT. `asyncio.shield` only manifest writes. Never shield long-running subprocess.
6. **Bitrate auto-cap stereo→mono** — compare per-channel bitrate, not absolute. 49 kbps stereo → per-channel 24.5 → cap mono target accordingly.
7. **m4a→mp3 always re-encodes** — no `-c:a copy` shortcut.
8. **`.part` orphans** — Doctor + startup scan cleans `.tmp/` files older than 24h.
9. **Unicode filenames on FAT/exFAT** — exFAT is fine, FAT32 supports VFAT long names. Don't transliterate; preserve the title.
10. **Path traversal in `--name` and templates** — pure-domain `paths.py` enforces; reject before any I/O.
11. **Two shokz instances same dir** — `flock` lock; second exits fast with `AnotherRunInProgress`.
12. **`os.replace` cross-FS** — only atomic on same filesystem. Tmp dir is *inside* `downloads/` so this always holds.
13. **Disk guard race** — pre-check is best-effort; ffmpeg can still run out of space mid-encode → catch `OSError(ENOSPC)` and translate to `DiskFull`.
14. **Manifest corruption** — JSONL parser must skip+log malformed lines, not crash. Repair via `library verify --rebuild-index`.
15. **Rate limiting** — default `--sleep-requests 1.0` to stay polite; configurable. With concurrency=3 that's effectively 3 req/s burst.
16. **Filename collision when `--force`** — manifest skip bypassed but file collision policy still applies.

---

## 10. Testing Strategy (Production-Tier)

| Layer | What | How | Coverage target |
|---|---|---|---|
| `domain/` (pure) | filenames, bitrate cap, paths, presets, errors | pytest, no fakes, parametrized property tests for sanitizer | **95%** |
| `application/use_cases/` | skip-existing, retry classification, error isolation, dry-run, signal cancellation, lock, disk guard | All ports faked in `tests/fakes.py` | **90%** |
| `application/policies/` | filename resolver, retry backoff, disk math | Pure unit | **95%** |
| `config/` | TOML→env→CLI precedence, validation, mutually-exclusive cookie sources | pytest + pydantic-settings | **90%** |
| `adapters/outbound/ytdlp_source.py` | real yt-dlp subprocess on 1 short public video | `@pytest.mark.integration`, gated by `INTEGRATION=1` | not counted |
| `adapters/outbound/ffmpeg_encoder.py` | locally generated sine wave, no network | runs in CI | full |
| `adapters/outbound/jsonl_manifest.py` | crash + resume scenarios with `tmp_path`; partial-line recovery | always | full |
| `adapters/outbound/local_filesystem.py` | atomic move semantics, fsync, ENOSPC translation | always | full |
| `adapters/outbound/flock_lock.py` | second-process-blocked with subprocess fork | runs in CI | full |
| `adapters/inbound/cli/` | Typer `CliRunner`; assert exit codes, stdout, dry-run no I/O | always | 80% |
| **E2E** | `tests/e2e/cli/` — full CLI run with mocked network (responses cassettes) | always | smoke only |

**Coverage gate in CI:** `pytest --cov=src/shokz --cov-fail-under=80`. Domain + use cases held to higher local thresholds via `.coveragerc` per-package targets.

**Don't test:** Rich rendering, yt-dlp internals, ffmpeg quality, concurrency wall-clock timing.

---

## 11. Library Choices — Library-First, Don't Reinvent

**Principle:** every cross-cutting concern below uses an established library. Custom code is reserved for domain logic (bitrate cap math, source-specific quirks, business orchestration). When a library exists for a problem, we use it — even if writing it ourselves would be 50 lines.

### Runtime dependencies

| Concern | Library | Why this over custom |
|---|---|---|
| **YouTube extraction** | **`yt-dlp`** | the source of truth for YouTube anti-bot |
| ↳ download (per video) | yt-dlp **as subprocess** | process isolation across N concurrent downloads, crash safety |
| ↳ metadata resolution | yt-dlp **as Python module** (`YoutubeDL(quiet=True, skip_download=True).extract_info`) | typed dict instead of parsing `-J` stdout; faster |
| **Audio encoding** | **`ffmpeg`** subprocess + `-progress pipe:1 -nostats` | nothing to reinvent |
| **CLI framework** | **`Typer`** | type-hint-driven; thin Click wrapper |
| **Config (TOML/env/CLI)** | **`pydantic-settings` v2** | precedence layers, validators, JSON-schema export |
| **TOML write** | **`tomli-w`** (read uses stdlib `tomllib`) | minimal dep |
| **Filename sanitization** | **`pathvalidate`** | handles FAT/exFAT/NTFS reserved names + Windows reserved (`CON`, `LPT1`...) cross-platform; replaces my custom slugifier |
| **Retry + backoff + jitter** | **`tenacity`** | declarative `@retry(stop=..., wait=..., retry=retry_if_exception_type(...))`; replaces custom `policies/retry.py` |
| **File locking (cross-process)** | **`filelock`** | cross-platform context-manager API; replaces raw `fcntl.flock` |
| **Structured logging + correlation IDs** | **stdlib `logging` + `RichHandler` + a JSON formatter (~30 lines) using stdlib `contextvars`** for `run_id`/`track_id` injection | GAN audit: structlog was inconsistent with §0's stdlib-logging baseline. Pick one. Stdlib + RichHandler is one less dep, satisfies all our needs. `structlog` only worth it if we hit limitations. |
| **Bytes parsing & humanization** | **`humanfriendly`** | `parse_size("5G") == 5_000_000_000`; `format_size()`; replaces custom regex |
| **Cross-platform user dirs** | **`platformdirs`** | correct `~/.config/`, `~/Library/`, `%APPDATA%`; replaces hardcoded paths |
| **Progress UI** | **`Rich`** | multi-bar `Progress` + `Live` + tracebacks |
| **ID3 tagging** | **`mutagen`** | MIT, no native deps, battle-tested |
| **MIME / file type sniff** | **`puremagic`** *(only if needed)* | pure-Python, no `libmagic` |

### Dev / quality dependencies

| Concern | Library | Why |
|---|---|---|
| Packaging + lockfile | **`uv`** | fast, reproducible, plays with conda |
| Lint + format | **`ruff`** | one tool, replaces black + isort + flake8 |
| Type-check | **`mypy --strict`** + `pydantic` plugin | strictest viable |
| Tests | **`pytest`** + **`pytest-asyncio`** + **`pytest-cov`** | standard |
| Subprocess testing | **`pytest`** built-in `monkeypatch` + `tmp_path` | no extra dep |
| Time freezing | **`time-machine`** | faster than freezegun; for retry/backoff tests |
| Pre-commit | **`pre-commit`** | enforce locally and in CI |
| Task runner | **`just`** (`Justfile`) | self-documenting recipes |

### Explicitly NOT using (and why)

| Wheel rejected | Reason |
|---|---|
| Custom retry implementation | `tenacity` does it better |
| Custom slugifier | `pathvalidate` handles edge cases I'd miss (Windows reserved, NTFS streams) |
| Raw `fcntl.flock` | `filelock` is cross-platform context-managed |
| Hand-rolled JSON logging | `structlog` is the standard |
| `yt-dlp` reimplementation | obviously |
| Custom HTTP client | yt-dlp owns network; we don't touch sockets |
| Custom progress bar | Rich |
| `requests` / `httpx` | nothing in this app makes a non-yt-dlp HTTP call |
| ORM / SQLAlchemy | JSONL is sufficient |
| `dependency-injector` / `injector` | `composition.py` is 50 lines of plain wiring — DI framework would be ceremony |
| `aiofiles` | manifest writes are tiny; sync inside `asyncio.to_thread` is fine |
| `pydantic` for non-config DTOs | `dataclasses(frozen=True)` for use-case I/O is leaner; pydantic only at config + adapter boundaries |

### What this means for the architecture

Several modules in §1's tree become **thin wrappers** instead of bespoke code:

- `domain/filenames.py` → ~30 lines wrapping `pathvalidate.sanitize_filename` + collision suffix logic
- `policies/retry.py` → ~20 lines configuring `tenacity.AsyncRetrying` from `RetryConfig`
- `adapters/outbound/flock_lock.py` → ~15 lines wrapping `filelock.FileLock`
- `observability/logging.py` → ~30 lines configuring `logging.dictConfig` with `RichHandler` + a JSON formatter, with `contextvars` injecting `run_id` and `track_id`
- `adapters/outbound/ytdlp_source.py` → uses `yt_dlp.YoutubeDL(...).extract_info(url, download=False)` for resolve; subprocess only for the download

All of these still live behind ports — **the abstraction is preserved, the implementation is small.**

---

## 12. Open Questions (Reduced — Most Resolved)

Defaults below assume you say "use the recommended choice"; flag any to change.

1. ✅ **Output dir:** `./downloads/` (your decision, locked).
2. ✅ **Filename default:** `{title}.mp3` (your decision, locked).
3. ✅ **`--name` flag:** single-URL only, error otherwise (locked).
4. **Cookie default:** null + clear error when needed. Recommended.
5. **Playlist subdirs:** subdir under `playlist`, flat under `download`. Recommended.
6. **Pin yt-dlp version range** in `pyproject.toml` (`>=2026.3,<2027`)? Recommended yes; nightly CI run against latest catches drift.
7. **Run ID format:** human-readable `2026-04-26T20-30-12`. Recommended.
8. **Concurrency default:** 3 (proven; anti-throttle). Recommended.
9. **Logging file:** stderr only by default; `--log-file` opt-in. Recommended.
10. **Doctor auto-runs:** yes by default; `--skip-doctor` opt-out. Recommended.
11. **README badges:** CI status + coverage + Python version. Recommended.
12. **License:** MIT? Apache-2.0? You decide.

---

## 13. Files to Create — Slice 0 (Production Scaffold)

| Path | Operation | Purpose |
|---|---|---|
| `/Users/xuzhijie/Desktop/ai/shokz/pyproject.toml` | Create | uv project; deps pinned; ruff + mypy + pytest + coverage config; `[project.scripts] shokz = "shokz.adapters.inbound.cli.app:run"` |
| `/Users/xuzhijie/Desktop/ai/shokz/uv.lock` | Generate | `uv lock` output, committed |
| `/Users/xuzhijie/Desktop/ai/shokz/pre-commit-config.yaml` | Create | ruff, mypy, basic hygiene |
| `/Users/xuzhijie/Desktop/ai/shokz/Justfile` | Create | `just install`, `just lint`, `just typecheck`, `just test`, `just integration`, `just clean` |
| `/Users/xuzhijie/Desktop/ai/shokz/.github/workflows/ci.yml` | Create | lint → typecheck → unit → coverage gate |
| `/Users/xuzhijie/Desktop/ai/shokz/.github/workflows/nightly-ytdlp.yml` | Create | weekly run vs latest yt-dlp |
| `/Users/xuzhijie/Desktop/ai/shokz/.gitignore` | Create | `downloads/`, `__pycache__/`, `.mypy_cache`, `.ruff_cache`, `.coverage`, `*.egg-info` |
| `/Users/xuzhijie/Desktop/ai/shokz/CHANGELOG.md` | Create | Keep a Changelog header + `[Unreleased]` section (DoD requires updating per sprint) |
| `/Users/xuzhijie/Desktop/ai/shokz/README.md` | Create | install, usage, config, dev workflow, **Agile process pointer to §0.5** |
| `/Users/xuzhijie/Desktop/ai/shokz/shokz.toml.example` | Create | commented sample config |
| `/Users/xuzhijie/Desktop/ai/shokz/RETRO.md` | Create | append-only sprint retro log (see §0.5 template) |
| `/Users/xuzhijie/Desktop/ai/shokz/docs/sprints/sprint-0.md` | Create | first sprint's User Story + Gherkin AC + DoR checklist |
| `/Users/xuzhijie/Desktop/ai/shokz/docs/sprints/_template.md` | Create | reusable User Story / DoR / DoD template (copy per sprint) |
| `/Users/xuzhijie/Desktop/ai/shokz/.github/PULL_REQUEST_TEMPLATE.md` | Create | embeds Sprint Goal field + DoD checklist |
| `/Users/xuzhijie/Desktop/ai/shokz/tests/acceptance/__init__.py` | Create | acceptance tests directory (Gherkin AC become pytest test names here) |
| `/Users/xuzhijie/Desktop/ai/shokz/src/shokz/__init__.py` | Create | `__version__ = "0.1.0"` |
| `/Users/xuzhijie/Desktop/ai/shokz/src/shokz/observability/logging.py` | Create | stdlib `logging.dictConfig` + RichHandler + JSON formatter; contextvars-based `run_id`/`track_id` correlation IDs (no structlog) |
| `/Users/xuzhijie/Desktop/ai/shokz/tests/__init__.py` | Create | (empty) |
| `/Users/xuzhijie/Desktop/ai/shokz/tests/conftest.py` | Create | shared pytest fixtures |
| `/Users/xuzhijie/Desktop/ai/shokz/tests/test_smoke.py` | Create | one assertion: `__version__` is set — proves CI green |
| `/Users/xuzhijie/Desktop/ai/shokz/poc/` | **Untouched** | reference |

After Slice 0: `just install && just test && just lint && just typecheck` all pass on an effectively-empty package. CI green. Then Slice 1 starts adding feature code.

---

## SESSION_ID (for /ccg:execute)

> Codex/Gemini wrappers (`~/.claude/bin/codeagent-wrapper`) are NOT installed. Multi-plan ran via parallel local Claude Code agents. There are no codex/gemini SESSION_IDs.
>
> Local agent IDs from earlier rounds:
> - Architect (v1, technical design): `a1557a8ece05cbb59`
> - Codex-rescue (v1, technical design): `ae4d97446ce3e19ac`
> - Planner (v3, Agile/Scrum-for-solo overlay): `a6beb6df08ea5832c`
> - GAN Round 1 — Pragmatist critic (v3.1): `a38bfbd6116f535bb`
> - GAN Round 1 — Silent-failure-hunter critic (v3.1): `a348a93272f87d272`
