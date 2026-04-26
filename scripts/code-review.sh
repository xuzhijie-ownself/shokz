#!/usr/bin/env bash
# code-review.sh — print a code-review brief for the current sprint diff.
#
# Born from Sprint 2 retro: I shipped a TOCTOU bug in v0.2.0 that two
# parallel reviewers caught. Code review is now a non-skippable pre-tag
# DoD step. This script just produces the brief; the actual reviewers
# (silent-failure-hunter + python-reviewer) are dispatched from Claude
# Code itself, which has the Agent tool.
#
# Usage:
#   bash scripts/code-review.sh <prev-tag>          # diff prev-tag..HEAD
#   bash scripts/code-review.sh v0.2.0
#   just code-review v0.2.0
#
# Output: a markdown brief on stdout listing files changed + line counts,
# suitable for pasting into the agent prompt.

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <prev-tag>" >&2
    echo "  example: $0 v0.2.0  -> brief for diff v0.2.0..HEAD" >&2
    exit 2
fi

PREV="$1"

if ! git rev-parse "$PREV" >/dev/null 2>&1; then
    echo "error: tag/ref not found: $PREV" >&2
    exit 2
fi

cat <<HEADER
# Code Review Brief — diff ${PREV}..HEAD

Generated: $(date +%Y-%m-%dT%H:%M:%S)

## Files changed

\`\`\`
HEADER

git diff "${PREV}..HEAD" --stat | tail -n +1

cat <<MIDDLE
\`\`\`

## Files (paths only, for reviewer prompt)

\`\`\`
MIDDLE

git diff "${PREV}..HEAD" --name-only

cat <<FOOTER
\`\`\`

## Reviewer checklist (from RETRO)

Two reviewers in parallel:

1. \`everything-claude-code:python-reviewer\` — score quality 1-10 on
   (Pythonic / typing / testability / security / maintainability), then
   list top 5 concrete issues + hidden bugs + idiom smells + test quality.
2. \`everything-claude-code:silent-failure-hunter\` — find >=5 silent
   failure modes, bad fallback patterns, error-translation gaps.

Both produce an honest critique; the human resolves convergent + unique
findings before tag.

## Diff stats

- Insertions/deletions: $(git diff "${PREV}..HEAD" --shortstat | sed 's/^ //')
- Commits in this range: $(git rev-list --count "${PREV}..HEAD")
FOOTER
