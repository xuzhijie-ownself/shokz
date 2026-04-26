#!/usr/bin/env bash
# kill-test.sh — start a shokz download, SIGKILL it mid-encode, assert
# no partial *.mp3 files survived in downloads/.
#
# Born from Sprint 3 retro -- atomic-write verification becomes a permanent
# DoD ratchet item from Sprint 4 onward (plan §0.5 DoD checklist).
#
# Usage:
#   bash scripts/kill-test.sh <URL>        (uses ./downloads_kt as scratch)
#   just kill-test <URL>
#
# Exit:
#   0 — kill caught at safe state, no partial *.mp3 in downloads_kt/
#   1 — partial *.mp3 file survived (atomicity broken)
#   2 — usage / process never started
#   3 — process exited cleanly before kill (try a longer URL)

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <YouTube URL>" >&2
    echo "  Pick a URL whose audio takes >= 4s to download+encode." >&2
    exit 2
fi

URL="$1"
SCRATCH=$(mktemp -d -t shokz_kt.XXXXXX)
trap 'rm -rf "$SCRATCH"' EXIT

LOG="$SCRATCH/shokz.log"
SHOKZ_BIN=$(realpath "$(pwd)/.venv/bin/shokz" 2>/dev/null || command -v shokz)

if [[ -z "${SHOKZ_BIN:-}" ]] || [[ ! -x "$SHOKZ_BIN" ]]; then
    echo "error: shokz binary not found (looked at .venv/bin and PATH)" >&2
    exit 2
fi

echo "kill-test: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  scratch:  $SCRATCH"
echo "  shokz:    $SHOKZ_BIN"
echo "  URL:      $URL"
echo ""

mkdir -p "$SCRATCH/downloads"
# Ensure yt-dlp / ffmpeg from the venv are on PATH for the spawned shokz process.
VENV_BIN=$(dirname "$SHOKZ_BIN")
PATH="$VENV_BIN:$PATH" "$SHOKZ_BIN" download -o "$SCRATCH/downloads" "$URL" >"$LOG" 2>&1 &
PID=$!
echo "spawned pid=$PID; sleeping 4s to let yt-dlp+ffmpeg warm up..."
sleep 4

if ! kill -0 "$PID" 2>/dev/null; then
    echo "error: shokz already exited before kill (try a slower URL)" >&2
    cat "$LOG" >&2
    exit 3
fi

echo "sending SIGKILL to pid=$PID"
kill -KILL "$PID" 2>/dev/null || true
wait "$PID" 2>/dev/null || true
echo "  process exited"
echo ""

# Inspect downloads/
echo "post-kill state of $SCRATCH/downloads/:"
find "$SCRATCH/downloads" -mindepth 1 2>/dev/null | head -20

# Atomicity check: NO *.mp3 files in the top-level downloads/ dir
# (.tmp/*.partial is OK -- it's the staging area)
PARTIAL_MP3S=$(find "$SCRATCH/downloads" -maxdepth 1 -name "*.mp3" -type f 2>/dev/null | wc -l | tr -d ' ')
echo ""
echo "summary: $PARTIAL_MP3S top-level *.mp3 file(s) in downloads/"

if [[ "$PARTIAL_MP3S" -gt 0 ]]; then
    echo "FAIL: atomic-write broken -- partial *.mp3 survived after SIGKILL" >&2
    find "$SCRATCH/downloads" -maxdepth 1 -name "*.mp3" -type f -exec ls -la {} \; >&2
    exit 1
fi

echo "PASS: no partial *.mp3 in downloads/ (atomic-write protocol holds)"
exit 0
