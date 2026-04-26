# Sprint 3 — Configuration (TOML + env + CLI)

**Date:** 2026-04-26
**Tag target:** `v0.3.0`
**Effort:** ~½ day

## Sprint Goal

The swimmer can override every Sprint 1+2 default via `shokz.toml`, env (`SHOKZ_*`), or CLI flags — and `shokz config show` proves which source won for each value.

## User Story

```
Title: Configure shokz from TOML, env, or CLI

As a Swimmer who wants stable defaults across runs (and a power user who
wants to override per-invocation), I want to put my preferences in a
shokz.toml file (or env vars), and have CLI flags override that, so I
don't have to type the same -c -p --output flags every time.

Acceptance Criteria (Gherkin -- written BEFORE code):

  Scenario: Built-in defaults apply when no config file exists
    Given no shokz.toml in CWD or ~/.config/shokz/
      And no SHOKZ_* env vars set
    When I run `shokz config show`
    Then exit code is 0
     And output shows general.output_dir = ./downloads (source: built-in)
     And output shows general.concurrency = 3 (source: built-in)
     And output shows audio.preset = swim-standard (source: built-in)
     And output shows filenames.template = {title} (source: built-in)

  Scenario: Project-local shokz.toml overrides built-in defaults
    Given ./shokz.toml contains [general] concurrency = 5
    When I run `shokz config show`
    Then output shows general.concurrency = 5 (source: ./shokz.toml)

  Scenario: Env var overrides project TOML
    Given ./shokz.toml contains [general] concurrency = 5
      And SHOKZ_GENERAL__CONCURRENCY=7 is set
    When I run `shokz config show`
    Then output shows general.concurrency = 7 (source: env SHOKZ_GENERAL__CONCURRENCY)

  Scenario: CLI flag overrides env var
    Given SHOKZ_GENERAL__CONCURRENCY=7 is set
    When I run `shokz download --concurrency 9 URL`
    Then the download proceeds with concurrency=9
     And `shokz config show --concurrency 9` reflects (source: CLI)

  Scenario: shokz config init writes a commented sample TOML
    Given ./shokz.toml does NOT exist
    When I run `shokz config init`
    Then ./shokz.toml is created
     And it contains commented sections [general] [audio] [filenames] [sources.youtube] [logging]
     And exit code is 0

  Scenario: shokz config init refuses to overwrite existing file (no --force)
    Given ./shokz.toml already exists with custom contents
    When I run `shokz config init`
    Then exit code is non-zero
     And stderr says the file exists; pass --force to overwrite
     And the existing file is unchanged

  Scenario: shokz config path lists which config files were loaded
    Given ./shokz.toml exists
      And ~/.config/shokz/config.toml does NOT exist
    When I run `shokz config path`
    Then output lists ./shokz.toml as loaded
     And output marks ~/.config/shokz/config.toml as not present

  Scenario: Invalid TOML produces a clear error, not a Python traceback
    Given ./shokz.toml contains malformed TOML (syntax error)
    When I run `shokz config show`
    Then exit code is non-zero
     And stderr contains "TOML" and the file path
     And stderr does NOT contain "Traceback"

  Scenario: Invalid value (e.g. concurrency = -1) is rejected at load time
    Given ./shokz.toml contains [general] concurrency = -1
    When I run `shokz config show`
    Then exit code is non-zero
     And stderr names the offending key (general.concurrency)
     And stderr explains the constraint (must be >= 1)

  Scenario: Custom audio preset overrides via TOML
    Given ./shokz.toml contains [audio] preset = "custom" and bitrate_kbps = 96
    When I run `shokz download URL`
    Then the resulting MP3 is encoded at 96 kbps (verified via ffprobe)

  Scenario: Config precedence -- unit-level
    Given an in-memory config loader with built-in defaults
    When I supply a TOML file value, an env var value, and a CLI value
    Then the loader returns the CLI value
     And the source-tracking dict reports each key's origin

Non-functional:
  - `shokz config show` runs in < 100 ms (no network, no subprocess)
  - Loader precedence: built-in < ~/.config/shokz/config.toml < ./shokz.toml < env (SHOKZ_*) < CLI flags
  - Validation happens at load time, not at use time (fail fast)
  - Pydantic v2 (already in deps); use pydantic-settings for env binding
  - tomllib (stdlib for Python 3.11+) for read; tomli-w for write

Out of scope (defer to listed sprint):
  - skip_existing flag wiring                 -> Sprint 4.5 (lands with manifest)
  - cap_to_source flag wiring                 -> Sprint 7 (lands with retry+bitrate cap)
  - retry config (max_attempts, backoff)      -> Sprint 7
  - ui.progress = json|rich|plain|none        -> Sprint 6
  - sources.youtube.cookies_*                 -> later (only if needed)
  - disk_safety_multiplier                    -> Sprint 8
  - All v3.1 plan §4 knobs not used by Sprints 1+2 today are STUBBED with
    sensible defaults but produce no behavior change in Sprint 3.

INVEST: Independent (Sprint 2 unblocks), Negotiable, Valuable (every
        sprint after this benefits from config layer), Estimable (½ day),
        Small, Testable (10 Gherkin scenarios above)
```

## Definition of Ready (DoR) — checked

- [x] Sprint Goal written
- [x] User Story with Gherkin AC (10 scenarios)
- [x] Affected files listed (see "Files to land")
- [x] Ports/contracts named: no new outbound port (config is application-level concern, not an external dep). New: `config.schema.AppConfig`, `config.loader.load_config()`.
- [x] Test approach: unit (precedence + validation + source tracking, pure); CLI smoke (config subcommands via Typer CliRunner); acceptance (Gherkin → integration with real TOML files in tmp_path)
- [x] Dependencies on prior sprints: Sprint 2 v0.2.0 ✓; pydantic-settings + tomli-w in deps ✓
- [x] Out-of-scope list written
- [x] Estimated ½ day

## Files to land in Sprint 3

### config/ (new package)
- `src/shokz/config/__init__.py`
- `src/shokz/config/schema.py` — Pydantic `AppConfig` + sub-models
- `src/shokz/config/defaults.py` — built-in defaults (mirroring Sprint 1+2 hardcoded values)
- `src/shokz/config/presets.py` — re-export `SWIM_LOW/STANDARD/HIGH` + preset→AudioSpec resolution
- `src/shokz/config/loader.py` — precedence merge with source tracking

### adapters/inbound/cli/commands/
- `src/shokz/adapters/inbound/cli/commands/config_cmd.py` — `show`, `init`, `path` subcommands
- Update `app.py` — register config subcommand group

### Update existing
- `src/shokz/composition.py` — `build_container(config: AppConfig)` instead of no-arg
- `src/shokz/adapters/inbound/cli/commands/download.py` — load config, layer CLI flags on top, pass to `build_container`

### tests/
- `tests/unit/config/__init__.py`
- `tests/unit/config/test_loader.py` — precedence (5 scenarios), source tracking
- `tests/unit/config/test_schema.py` — validation errors (concurrency<1, channels∉{1,2}, etc.)
- `tests/unit/test_cli_smoke.py` — extended with `config show/init/path` smoke
- `tests/acceptance/test_sprint_3_config.py` — Gherkin scenarios as pytest

### Process
- Use `just sprint-review 3` to verify Gherkin↔test name coverage before tag
- Use `just code-review v0.2.0` to dispatch reviewers before tag (per Sprint 2 retro)

## Definition of Done (DoD) — verify before close

- [ ] All 10 Gherkin AC scenarios pass as pytest tests
- [ ] `just sprint-review 3` passes
- [ ] `just code-review v0.2.0` brief generated; reviewers dispatched; convergent + unique findings either fixed OR explicitly deferred-with-reason **(NEW DoD ratchet item from Sprint 2 retro)**
- [ ] `just lint` clean
- [ ] `just typecheck` clean (mypy --strict)
- [ ] `just test` green; coverage ≥ 80% on touched `src/shokz/{domain,application,config,observability}/`
- [ ] CHANGELOG.md `[Unreleased]` → `[0.3.0]`
- [ ] README.md Use section: TOML + env example added
- [ ] Conventional Commits: `feat(config): TOML + env + CLI override layer (Sprint 3)`
- [ ] Self-demo from clean state: write a minimal `shokz.toml`, run `shokz config show`, verify source-tracking output is honest
- [ ] Self-demo: env var override visible in `shokz config show`
- [ ] Self-demo: CLI flag override visible
- [ ] Git tag pushed: `v0.3.0`
- [ ] Retro entry appended to RETRO.md
