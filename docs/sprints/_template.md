# Sprint <N> — <slice name>

**Date:** YYYY-MM-DD
**Tag:** `v<x>.<y>.<z>`
**Effort:** ~½ day

## Sprint Goal

<One sentence — swimmer-facing outcome.>

## User Story

```
Title: <verb-noun, ≤8 words>

As a <persona>, I want <capability>, so that <swimming-context outcome>.

Acceptance Criteria (Gherkin — write BEFORE code, they ARE the test names):

  Scenario: <name>
    Given <state>
    When <action>
    Then <observable outcome>

  [more scenarios for edge cases]

Non-functional:
  - <perf / atomicity / idempotency requirements>

Out of scope:
  - <explicit exclusions — defends against creep>

INVEST: Independent, Negotiable, Valuable, Estimable, Small (½ day), Testable
```

## Definition of Ready (DoR) — checked

- [ ] Sprint Goal written
- [ ] ≥1 User Story with Gherkin AC
- [ ] Affected files listed (paths from plan §1 / §13)
- [ ] Ports/contracts named (no new port invented mid-sprint)
- [ ] Test approach noted (unit / integration / e2e split)
- [ ] Dependencies on prior sprints verified merged + green CI
- [ ] Out-of-scope list written
- [ ] Estimated ≤ ½ day

## Definition of Done (DoD) — to verify before close

- [ ] All Gherkin AC scenarios pass as executable pytest tests
- [ ] `just lint` clean
- [ ] `just typecheck` clean (mypy --strict)
- [ ] `just test` green; coverage ≥ 80% on touched files
- [ ] (Sprint 4+) atomic-write + integrity + manifest-fsync verified
- [ ] (Sprint 4.5+) reconciliation scan verified
- [ ] (Sprint 7+) error-translation table tested
- [ ] CI green
- [ ] `CHANGELOG.md` `[Unreleased]` updated
- [ ] `README.md` updated if CLI surface changed
- [ ] Conventional Commits used
- [ ] Self-demo from clean `./downloads/`
- [ ] Git tag pushed
- [ ] Retro entry appended to `RETRO.md`
