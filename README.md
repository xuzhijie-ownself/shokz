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

```bash
shokz download "https://www.youtube.com/watch?v=..."   # Sprint 1
shokz download --name "Sleep Mix" "<URL>"              # Sprint 2
shokz playlist "<playlist URL>"                        # Sprint 5
```

(Commands above land in their respective sprints. See `docs/sprints/`.)

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
