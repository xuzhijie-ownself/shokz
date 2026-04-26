# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.0.0] — 2026-04-26

### Added
- Production scaffold: `pyproject.toml` (uv-managed, ruff, mypy --strict, pytest, coverage gate ≥80%).
- `Justfile` task runner (`install`, `lint`, `fmt`, `typecheck`, `test`, `integration`, `ci`, `clean`, `hooks-*`).
- `.pre-commit-config.yaml` with ruff, mypy, conventional-pre-commit, basic hygiene hooks.
- `src/shokz/__init__.py` exposing `__version__ = "0.0.0"`.
- `src/shokz/observability/logging.py` — stdlib logging + RichHandler + JSON formatter, `contextvars`-based `run_id`/`track_id` correlation IDs.
- `tests/test_smoke.py` — one-pass smoke test asserting package import, version, logging setup.
- `tests/conftest.py` — shared `downloads_dir` fixture.
- GitHub Actions: `ci.yml` (lint + typecheck + test on push/PR), `nightly-ytdlp.yml` (weekly run vs latest yt-dlp).
- `.github/PULL_REQUEST_TEMPLATE.md` embedding Sprint Goal field + DoD checklist.
- `RETRO.md` and `docs/sprints/_template.md` for the Agile-for-solo process layer (plan §0.5).
- `shokz.toml.example` (commented sample config; populated incrementally per sprint).

### Notes
- Sprint Goal: "Empty package builds, lints, type-checks, tests, and CI green — proving the quality bar enforces itself."
- DoD ratchet established. Subsequent sprints inherit and extend.
