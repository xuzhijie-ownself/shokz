#!/usr/bin/env bash
# sprint-review.sh — verify every Gherkin Scenario in docs/sprints/sprint-N.md
# has at least one matching pytest test name. Born from Sprint 1's audit pain
# (RETRO entry 2026-04-26). Mechanical pre-tag DoD check.
#
# Usage:
#   bash scripts/sprint-review.sh <sprint-number>
#   just sprint-review <N>
#
# Exit:
#   0 — every Scenario has a matching test name (substring slug match)
#   1 — at least one Scenario lacks a test (offenders listed)
#   2 — usage / file-missing error

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <sprint-number>" >&2
    exit 2
fi

N="$1"
SPRINT_FILE="docs/sprints/sprint-${N}.md"

if [[ ! -f "$SPRINT_FILE" ]]; then
    echo "error: sprint file not found: $SPRINT_FILE" >&2
    exit 2
fi

# Extract Scenario titles from the spec (portable across bash 3+ and 4+).
SCENARIOS=()
while IFS= read -r line; do
    SCENARIOS+=("$line")
done < <(grep -E '^  Scenario:' "$SPRINT_FILE" | sed -E 's/^  Scenario:[[:space:]]*//' || true)

if [[ ${#SCENARIOS[@]} -eq 0 ]]; then
    echo "warning: no 'Scenario:' lines found in $SPRINT_FILE" >&2
    exit 0
fi

# Concatenate every test name we have (def test_* and async def test_*) across tests/.
TEST_NAMES=$(grep -hE '^(async )?def test_[A-Za-z0-9_]+' tests/ -r --include='*.py' \
    | sed -E 's/^(async )?def (test_[A-Za-z0-9_]+).*/\2/' \
    | sort -u || true)

# Build a regex from the first 3 SIGNIFICANT words of a scenario title.
# Stopwords are dropped so "Multiple URLs download concurrently" and
# "Mixed valid + invalid URLs — partial success" still match flexible test
# names like test_mixed_valid_and_invalid_urls_partial_success.
SCENARIO_STOPWORDS="a|an|the|and|or|with|of|on|in|to|for|is|are|be|by"

build_match_regex() {
    local s="$1"
    s=$(echo "$s" | tr '[:upper:]' '[:lower:]')
    s=$(echo "$s" | sed -E 's/[^a-z0-9]+/ /g')
    s=$(echo "$s" | sed -E "s/\\b($SCENARIO_STOPWORDS)\\b//g")
    s=$(echo "$s" | tr -s ' ')
    s=$(echo "$s" | sed -E 's/^ +| +$//g')
    # Take first 3 tokens, joined by .* so intervening words in test names pass.
    echo "$s" | awk '{
        n = (NF < 3 ? NF : 3)
        for (i = 1; i <= n; i++) {
            printf "%s%s", $i, (i < n ? ".*" : "")
        }
        print ""
    }'
}

MISSING=()
PRESENT=()

for sc in "${SCENARIOS[@]}"; do
    regex=$(build_match_regex "$sc")
    if [[ -n "$regex" ]] && echo "$TEST_NAMES" | grep -qE "$regex"; then
        PRESENT+=("$sc")
    else
        MISSING+=("$sc  (expected test name matching: /$regex/)")
    fi
done

echo "Sprint $N review — $(date +%Y-%m-%d)"
echo "  scenarios in spec: ${#SCENARIOS[@]}"
echo "  scenarios covered: ${#PRESENT[@]}"
echo "  scenarios missing: ${#MISSING[@]}"
echo ""

if [[ ${#PRESENT[@]} -gt 0 ]]; then
    echo "COVERED:"
    for s in "${PRESENT[@]}"; do echo "  + $s"; done
    echo ""
fi

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "MISSING TESTS:" >&2
    for s in "${MISSING[@]}"; do echo "  - $s" >&2; done
    echo ""
    echo "Add a pytest test whose name contains the slug, then re-run." >&2
    exit 1
fi

echo "All Sprint $N scenarios have at least one matching pytest test."
exit 0
