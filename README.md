# shokz

YouTube → MP3 downloader for Shokz swimming headphones.

> **Status:** Sprint 0 scaffold (`v0.0.0`). Not yet usable. See `.claude/plan/shokz-downloader.md` for the 10-sprint roadmap and `RETRO.md` for the running retrospective.

## Why

Shokz waterproof bone-conduction headphones (used for swimming) only support MP3 over USB mass-storage. This tool downloads YouTube videos in parallel, extracts and re-encodes audio to MP3 with sane defaults for the swimming context (mono, modest bitrate, capped to source).

## Install (developer)

Requires Python 3.11, `uv`, `just`, `ffmpeg`.

```bash
git clone <repo> shokz
cd shokz
just install        # uv sync --all-extras
just hooks-install  # one-time pre-commit setup
```

## Use

`shokz download URL [URL ...]` is shipped in v0.1.0:

```bash
shokz download "https://www.youtube.com/watch?v=jNQXAC9IVRw"
# -> downloads/Me at the zoo.mp3   (title-based since v0.2.0; was id-named in v0.1.0)

shokz download --name "Sleep Mix Vol 1" "<URL>"      # custom filename (single-URL only)
shokz download -c 4 URL1 URL2 URL3 URL4              # in-process concurrency 1..4 (v0.7.0+: default 1)
shokz download --keep-raw URL                        # keep .webm in .tmp/
shokz download --output ~/swim-mp3s URL              # custom output dir
```

If two videos resolve to the same filename, the second auto-suffixes:
`Foo.mp3` → `Foo (2).mp3` → `Foo (3).mp3` ...

**Sequential by default (v0.7.0+)**: a bare `shokz download URL_A URL_B URL_C` processes URLs strictly in order. Pass `-c 4` (cap is 4) to enable in-process concurrency. **Do NOT** spawn multiple `shokz` processes against the same `--output` directory; the manifest layer is single-process-safe only until Sprint 8 lands cross-process file locking.

**Classified retry (v0.8.0+)**: transient YouTube failures auto-retry with sensible backoff:

| Class | Trigger (yt-dlp message contains) | Retries | Backoff |
|---|---|---|---|
| `RateLimited` | `HTTP Error 429`, `too many requests` | 3 | 5s, 30s, 120s |
| `NetworkError` | `HTTP Error 5xx`, `connection reset/refused`, DNS, timeout | 2 | 1s linear |
| `SourceFileCorrupt` | post-download size < 1 KB | 1 | 1s |
| `DownloadFailed` (default) | unrecognized | 1 | 1s |
| `AuthRequired` | `Sign in to confirm`, `not available in your country`, members-only | **0** (terminal) | — |
| `FormatUnavailable` | `Requested format not available` | **0** (terminal) | — |
| `SourceUnavailable` | `Private video`, `Video unavailable`, `removed`, premiere/live | **0** (terminal) | — |

After 3 consecutive `RateLimited` outcomes the per-batch circuit breaker trips and the rest of the run skips retries (avoids 60-track playlists turning into 3-hour waits on a bad day). Retry budgets are configurable via `[retry]` in `shokz.toml`.

### Playlists (v0.6.0+)

```bash
shokz playlist <playlist URL>                  # default: tracks under downloads/<playlist title>/
shokz playlist --no-playlist-subdir <URL>      # tracks land flat in downloads/
shokz playlist --yes <URL>                     # bypass the >=50-item confirmation
shokz playlist --confirm-threshold 100 <URL>   # raise the confirmation threshold
```

Playlists with >= the configured threshold (default 50, configurable via
`sources.youtube.playlist_confirm_threshold` or `--confirm-threshold`)
require explicit confirmation via `--yes` to avoid surprise overnight
runs of 200-track playlists.

`shokz library verify` walks subdirectories so per-playlist subfolders are
correctly reconciled (does NOT false-positive each playlist track).

### Skip-existing + library inspection (v0.5.0+)

Re-running `shokz download` on already-completed URLs is near-instant — the
manifest-driven skip short-circuits before any network or encoding work:

```bash
shokz download <URL>                    # first time: real download
shokz download <URL>                    # second time: SKIP in <1s
shokz download --force <URL>            # re-download anyway (collision suffix)

shokz library list                      # table of every manifest entry
shokz library show <track_id>           # full detail for one entry
shokz library verify                    # reconcile manifest <-> disk
                                        # exit 1 + diagnostic on mismatch
```

`library verify` surfaces:
- **orphan files**: `*.mp3` on disk with no manifest entry (likely Sprint 4
  SF-4 orphan window — process killed between os.replace and manifest record)
- **orphan entries**: manifest rows whose `mp3_path` no longer exists on disk
  (manually deleted)

A startup reconciliation scan also warns once per `shokz download` invocation
if orphan files are detected.

### Crash-safe writes + manifest (v0.4.0+)

Every successful download is recorded in `downloads/.shokz/manifest.jsonl`
(append-only JSONL, schema_version=1) with file + parent-dir fsync. Killed
processes leave NO partial `*.mp3` in `downloads/` — only `.tmp/*.partial`
which is auto-cleaned on the next run. Integrity checks reject:
- yt-dlp 0-byte / truncated raw downloads (post-download size check)
- ffmpeg silent truncation (post-encode duration probe within 2%)

Failures are recorded in `downloads/.shokz/failures.jsonl` with stable
`error_class` strings for downstream tooling.

Layout:
```
downloads/
├── <Video Title>.mp3
├── .tmp/                 # in-progress (auto-cleaned)
└── .shokz/
    ├── manifest.jsonl    # successful tracks, fsync'd per row
    └── failures.jsonl    # per-track failures
```

### Configuration (v0.3.0+)

`shokz` reads layered configuration from (low → high precedence):

1. Built-in defaults (`AppConfig` field defaults)
2. `~/.config/shokz/config.toml`
3. `./shokz.toml` (project-local)
4. Env vars: `SHOKZ_GENERAL__CONCURRENCY=7`, `SHOKZ_AUDIO__PRESET=swim-low`, etc.
5. CLI flags

```bash
shokz config init                       # write a commented sample shokz.toml
shokz config show                       # effective config + per-key source
shokz config path                       # which TOML files were loaded
SHOKZ_GENERAL__CONCURRENCY=3 shokz config show   # env override visible (cap is 4 since v0.7.0)
shokz download --concurrency 4 URL      # CLI beats env beats TOML
```

The following commands ship in upcoming sprints (see `.claude/plan/shokz-downloader.md` §8 and `docs/sprints/`):

```bash
shokz playlist "<playlist URL>"                      # Sprint 5
shokz retry [RUN_ID]                                 # Sprint 8
shokz library list|show|verify                       # Sprint 4.5 / 9
shokz config show|init|path                          # Sprint 3
shokz doctor                                         # Sprint 9
```

## Configuration

- Built-in defaults → `~/.config/shokz/config.toml` → `./shokz.toml` → `SHOKZ_*` env → CLI flags
- See `shokz.toml.example` for every available knob.
- `shokz config show` prints the effective config and which file each value came from.

Output goes to `./downloads/`:

```
downloads/
├── <Video Title>.mp3   # final files (title-based)
├── .tmp/               # in-progress (auto-cleaned)
└── .shokz/             # state (manifest, failures, runs, locks)
```

## Development workflow

This project follows **Agile-for-solo** with a strict Definition of Done:

```bash
just lint        # ruff
just typecheck   # mypy --strict
just test        # pytest with coverage ≥80%
just ci          # all of the above (what GitHub Actions runs)
```

See `.claude/plan/shokz-downloader.md` §0.5 for full process details.

## License

MIT
