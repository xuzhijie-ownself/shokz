## Sprint Goal
<!-- One sentence — should match the Sprint Goal in docs/sprints/sprint-N.md -->

## Sprint Reference
docs/sprints/sprint-<N>.md

## Changes
<!-- High-level summary -->

---

## Definition of Done — checklist (do not merge until all ticked)

- [ ] All Gherkin AC scenarios pass as executable pytest tests
- [ ] `just lint` clean (ruff)
- [ ] `just typecheck` clean (mypy --strict)
- [ ] `just test` green; coverage ≥ 80% on touched files
- [ ] **(Sprint 4+)** Atomic-write integration test passes (no partial files after kill)
- [ ] **(Sprint 4+)** Integrity check (encoded duration within 2% of source)
- [ ] **(Sprint 4+)** Manifest fsync verified
- [ ] **(Sprint 4.5+)** Reconciliation scan integration test passes
- [ ] **(Sprint 7+)** Error-translation table fully covered
- [ ] CI green
- [ ] `CHANGELOG.md` `[Unreleased]` updated (Keep a Changelog format)
- [ ] `README.md` updated if CLI surface changed
- [ ] Conventional Commits used
- [ ] Self-demo executed against clean `./downloads/`
- [ ] Git tag pushed: `v<x>.<y>.<z>`
- [ ] Retro entry appended to `RETRO.md`

---

## Out of scope (defer to backlog)
<!-- Carry over from sprint-N.md -->
