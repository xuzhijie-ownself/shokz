# Sprint 0 — Production Scaffold

**Date:** 2026-04-26
**Tag:** `v0.0.0`
**Effort:** ~½ day

## Sprint Goal

Empty package builds, lints, type-checks, tests, and CI green — proving the quality bar enforces itself before any feature code is written.

## User Story

```
Title: Bootstrap the production scaffold

As a Solo Developer about to start Sprint 1, I want every quality gate
(lint, typecheck, test, coverage, CI) to be wired up and green on an
empty package, so that I cannot accidentally lower the bar later when
under deadline pressure.

Acceptance Criteria (Gherkin):

  Scenario: Smoke test passes
    Given a fresh checkout of the repo
      And `uv sync` has installed dependencies into the venv
    When I run `pytest`
    Then exit code is 0
     And at least one test passes (test_version_set)
     And coverage report is generated

  Scenario: Lint enforces style
    Given the source tree contains src/shokz/__init__.py
    When I run `ruff check src tests`
    Then exit code is 0

  Scenario: Type checker enforces strictness
    Given mypy is configured with --strict in pyproject.toml
    When I run `mypy src/shokz`
    Then exit code is 0

  Scenario: Coverage gate enforces 80%
    Given coverage is configured with fail_under = 80
    When I run `pytest --cov=src/shokz --cov-fail-under=80`
    Then exit code is 0

  Scenario: CI workflow exists and is valid
    Given .github/workflows/ci.yml exists
    When the YAML is parsed
    Then it has jobs: lint, typecheck, test
     And all jobs use the pinned Python version

Non-functional:
  - `uv sync` completes in < 30s on a warm cache
  - Test suite runs in < 5s
  - mypy --strict succeeds (proves we can hold the bar from day 1)

Out of scope:
  - Any feature code (no domain models, no ports, no use cases)
  - Any port definitions (Sprint 1+)
  - The shokz CLI entry point doing anything (Sprint 1)
  - GitHub Actions actually running (no remote yet)

INVEST: Independent, Negotiable, Valuable, Estimable, Small (1/2 day), Testable
```

## Definition of Ready (DoR) — checked

- [x] Sprint Goal written (above)
- [x] User Story with Gherkin AC (above)
- [x] Affected files listed (plan §13)
- [x] Ports/contracts named (none for Sprint 0 — scaffold only)
- [x] Test approach noted (one smoke test asserting `__version__`)
- [x] Dependencies on prior sprints verified (none — first sprint)
- [x] Out-of-scope list written (above)
- [x] Estimated ≤ ½ day

## Definition of Done (DoD) — to verify before close

- [ ] All Gherkin AC scenarios pass as executable pytest tests
- [ ] `just lint` clean (ruff)
- [ ] `just typecheck` clean (mypy --strict)
- [ ] `just test` green; coverage ≥ 80% on touched files
- [ ] CHANGELOG.md `[Unreleased]` updated
- [ ] README.md skeleton with usage section
- [ ] Conventional Commits used (`chore: bootstrap production scaffold`)
- [ ] Self-demo: fresh-clone equivalent verifies all gates green
- [ ] Git tag pushed: `v0.0.0`
- [ ] Retro entry appended to RETRO.md
