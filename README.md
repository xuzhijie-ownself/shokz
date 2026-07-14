# shokz

YouTube → MP3 downloader for Shokz swimming headphones.

> **Status:** `v1.2.0` — shipped. Seven commands across the full
> download / retry / split / inspect / diagnose lifecycle. 296 unit + 27
> INTEGRATION tests, ruff + mypy clean. See `CHANGELOG.md` for the
> per-version history and `RETRO.md` for sprint-by-sprint lessons.

## Why

Shokz waterproof bone-conduction headphones (used for swimming) only
support MP3 over USB mass-storage. This tool downloads YouTube videos,
extracts and re-encodes audio to MP3 with sane defaults for the
swimming context (mono, modest bitrate, capped to source).

## Install

Requires **Python 3.11+** and **ffmpeg** (provides `ffmpeg` and
`ffprobe` on PATH).

```bash
git clone <repo> shokz
cd shokz
pip install -e .            # editable install
shokz doctor                # verify ffmpeg / yt-dlp / disk are healthy
```

Dev tooling adds `pytest` / `ruff` / `mypy`:

```bash
pip install pytest pytest-asyncio pytest-cov time-machine ruff mypy
pytest                      # 282 tests, no skips when INTEGRATION=1
```

(The repo also ships a `Justfile` and `uv.lock` if you prefer
`just install` + `uv sync`.)

## Use

Seven commands. Run `shokz <cmd> --help` for full flag listings.

### download — single URL or batch

```bash
shokz download "https://www.youtube.com/watch?v=jNQXAC9IVRw"
# -> downloads/Me at the zoo.mp3

shokz download --name "Sleep Mix Vol 1" "<URL>"      # custom filename (single-URL only)
shokz download -c 4 URL1 URL2 URL3 URL4              # in-process concurrency 1..4 (default 1)
shokz download --keep-raw URL                        # keep .webm in .tmp/
shokz download --output ~/swim-mp3s URL              # custom output dir
shokz download --force URL                           # re-download even if in manifest
```

Filename collisions auto-suffix: `Foo.mp3` → `Foo (2).mp3` → `Foo (3).mp3`.

**Multi-process safety (v1.0.0+):** spawning two `shokz` processes
against the same `--output` is safe — the cross-process file lock
detects contention and the second invocation exits cleanly with
`AnotherRunInProgress` naming the holder PID + start time + lock path.

**Classified retry (v0.8.0+):** transient YouTube failures auto-retry
with sensible backoff:

| Class | Trigger (yt-dlp message) | Retries | Backoff |
|---|---|---|---|
| `RateLimited` | `HTTP Error 429`, `too many requests` | 3 | 5s, 30s, 120s |
| `NetworkError` | `HTTP Error 5xx`, connection reset/refused, DNS, timeout | 2 | 1s linear |
| `SourceFileCorrupt` | post-download size < 1 KB | 1 | 1s |
| `DownloadFailed` | unrecognized | 1 | 1s |
| `AuthRequired` | `Sign in to confirm`, region-locked, members-only | **0** (terminal) | — |
| `FormatUnavailable` | `Requested format not available` | **0** (terminal) | — |
| `SourceUnavailable` | `Private video`, `Video unavailable`, removed | **0** (terminal) | — |

After 3 consecutive `RateLimited` the per-batch circuit breaker trips
and the rest of the batch skips retries.

**SIGINT-shielded manifest (v1.0.0+):** Ctrl+C mid-download cancels
in-flight tasks but `asyncio.shield` drains pending manifest writes
before propagating, so an interrupted batch leaves a consistent
manifest. Press Ctrl+C again to force-exit.

### playlist — expand a YouTube playlist URL and download every video

```bash
shokz playlist <playlist URL>                  # default: tracks under downloads/<playlist title>/
shokz playlist --no-playlist-subdir <URL>      # tracks land flat in downloads/
shokz playlist --yes <URL>                     # bypass the >=50-item confirmation
shokz playlist --confirm-threshold 100 <URL>   # raise the confirmation threshold
```

Playlists with ≥ the configured threshold (default 50) require explicit
`--yes` to avoid surprise overnight runs of 200-track playlists.
`shokz library verify` walks subdirectories so per-playlist subfolders
are correctly reconciled.

### retry — re-process `failures.jsonl`

```bash
shokz retry                                    # re-process retryable rows
shokz retry --since 2d                         # only failures from last 2 days
shokz retry --error-class RATE_LIMITED         # explicit class filter (repeatable)
shokz retry --all                              # include terminal classes (AUTH_REQUIRED, ...)
shokz retry --dry-run                          # preview the plan; no downloads
```

Defaults: filters to `NETWORK_ERROR / RATE_LIMITED / SOURCE_FILE_CORRUPT
/ DOWNLOAD_FAILED`; dedupes per `(source, track_id)` newest-wins;
respects skip-existing. Acquires the same cross-process lock as
`shokz download`, so concurrent retry + download against the same
`--output` are safe.

Does NOT mutate `failures.jsonl` (append-only audit log); the manifest
is the source of truth for "what's downloaded". NOT a force-reencode
tool — for that, use `shokz download <url> --force`.

When `--since` is omitted and the candidate set spans > 7 days OR > 50
rows, a WARNING names the count + oldest date so first-run scope blast
is visible.

### split — chop a long file into hour-sized parts (v1.2.0+)

An 11-hour audiobook downloads as one 312 MB MP3. Underwater you get
next/previous track and little else — no useful way to seek within a
track. Splitting turns that into 12 files you can skip between.

```bash
shokz split "downloads/Long Book.mp3"                    # hourly parts, alongside the source
shokz split "Long Book.mp3" --hours 0.5                  # half-hourly
shokz split "Long Book.mp3" --output ./parts             # parts in their own dir
shokz split "Long Book.mp3" --force                      # re-split, overwriting
```

Produces `Long Book (part 01).mp3` … `(part 12).mp3` — 1-indexed and
zero-padded so they sort correctly on the device.

**Lossless and fast.** Stream-copies with `ffmpeg -c copy`; no re-encode,
no quality loss. Measured on a real 312 MB / 11.35-hour source: **4.25
seconds**, output totalling exactly 11.35 hours at the original bitrate.

Split is a *post-processing tool*, not a download mode — it reads and
writes nothing under `.shokz/`. Two honest consequences:

- `shokz library verify` will report the part files as **orphans**,
  because they genuinely are unmanaged files. That's correct, not a bug.
- Split takes no cross-process lock (it can't race a download: different
  filenames, no manifest write).

It refuses to overwrite a previous split's parts unless you pass
`--force`.

### library — inspect manifest + reconcile vs disk

```bash
shokz library list                      # table of every manifest entry
shokz library show <track_id>           # full detail for one entry
shokz library verify                    # reconcile manifest <-> disk; exit 1 on mismatch
```

`verify` surfaces:
- **orphan files**: `*.mp3` on disk with no manifest entry (likely
  killed mid-write before manifest record)
- **orphan entries**: manifest rows whose `mp3_path` no longer exists
  on disk (manually deleted)

A startup reconciliation scan also warns once per `shokz download`
invocation if orphan files are detected.

### config — inspect/initialize layered configuration

`shokz` reads layered configuration from (low → high precedence):

1. Built-in defaults (`AppConfig` field defaults)
2. `~/.config/shokz/config.toml`
3. `./shokz.toml` (project-local)
4. Env vars: `SHOKZ_GENERAL__CONCURRENCY=7`, `SHOKZ_AUDIO__PRESET=swim-low`, ...
5. CLI flags

```bash
shokz config init                       # write a commented sample shokz.toml
shokz config show                       # effective config + per-key source
shokz config path                       # which TOML files were loaded
SHOKZ_GENERAL__CONCURRENCY=3 shokz config show   # env override visible
shokz download --concurrency 4 URL      # CLI beats env beats TOML
```

### doctor — read-only environment diagnostics (v1.1.0+)

```bash
shokz doctor                            # six checks; exit 0 unless any FAIL
shokz doctor --output ~/swim-mp3s       # check writability of a specific dir
```

Checks: `ffmpeg` / `ffprobe` on PATH, `yt-dlp` version, `output_dir`
not symlinked + writable, sufficient disk free vs the `[disk]
safety_multiplier`. WARN entries are informational (don't trip exit 1);
only FAIL does.

Sample output:

```
shokz doctor:
  PASS  ffmpeg                 found at /opt/homebrew/bin/ffmpeg
  PASS  ffprobe                found at /opt/homebrew/bin/ffprobe
  PASS  yt-dlp                 version 2026.04.30
  PASS  output_dir             /Users/me/swim-mp3s is not symlinked
  PASS  output_dir_writable    /Users/me/swim-mp3s is writable
  PASS  disk_free              42 GiB free

All checks passed (WARN entries informational).
```

## Crash-safe writes + manifest

Every successful download is recorded in `downloads/.shokz/manifest.jsonl`
(append-only JSONL, schema_version=1) with file + parent-dir fsync.
Killed processes leave NO partial `*.mp3` in `downloads/` — only
`.tmp/*.partial` which is auto-cleaned on the next run. Integrity
checks reject:
- yt-dlp 0-byte / truncated raw downloads (post-download size check)
- ffmpeg silent truncation (post-encode duration probe within 2%)

Failures are recorded in `downloads/.shokz/failures.jsonl` with stable
`error_class` strings for downstream tooling (and as input for
`shokz retry`).

Layout:

```
downloads/
├── <Video Title>.mp3                       # final files (title-based filenames)
├── .tmp/                                   # in-progress (auto-cleaned)
└── .shokz/
    ├── manifest.jsonl                      # successful tracks, fsync'd per row
    ├── failures.jsonl                      # per-track failures (input to `shokz retry`)
    └── locks/
        └── shokz.lock + shokz.lock.meta    # cross-process lock + holder metadata
```

## Architecture

Hexagonal (Ports & Adapters):

- `src/shokz/domain/` — pure data + errors, zero framework imports
- `src/shokz/application/` — use cases + ports + policies (lock, retry, disk-guard, skip-existing, reconciliation, file-name resolution)
- `src/shokz/adapters/inbound/cli/` — Typer-based CLI surface (one file per command)
- `src/shokz/adapters/outbound/` — `yt-dlp` source, `ffmpeg` encoder, JSONL manifest, local filesystem, null progress reporter
- `src/shokz/composition.py` — single composition root that wires every port to its concrete adapter

The dependency direction is always inward: adapters → application → domain.
The domain layer has zero `import yt_dlp / ffmpeg / typer / asyncio`.

## Development

```bash
ruff check src tests        # lint
mypy src                    # type-check (strict)
pytest                      # unit + acceptance tests
INTEGRATION=1 pytest        # also run network-dependent INTEGRATION tests (~6 min)
```

See `RETRO.md` for sprint-by-sprint lessons learned and
`docs/sprints/sprint-N.md` for per-sprint specs (Gherkin-style AC,
GAN-fix manifests, definition-of-ready, definition-of-done).

## License

MIT — see `LICENSE`.
