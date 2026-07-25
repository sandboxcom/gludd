#!/bin/bash
# clean-root.sh — remove leaked API keys, SSH keys, debug artifacts,
# and misplaced files from the project root.
#
# Usage: make clean-root
#
# Run before each session or after any agent work that may have left
# artifacts in the root directory.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

removed=0

# ── SECURITY: leaked API keys and SSH keys (gitignored, must delete) ────────

for f in .deepseek.key .zai.key sandboxcom_github_rsa; do
    if [ -f "$f" ]; then
        rm -f "$f"
        echo "SECURITY-REMOVED: $f"
        removed=$((removed + 1))
    fi
done

# ── JUNK: debug/test files that should never be in root ─────────────────────

JUNK_FILES=(
    check_file.mk read_log.mk read_log2.mk read_log3.mk
    test_read.mk test_restore.mk
    test_bash.sh
    _diag_e2e.py _diag_e2e.ts _diag_multitask.ts
    str
)

for f in "${JUNK_FILES[@]}"; do
    if [ -f "$f" ]; then
        rm -f "$f"
        echo "JUNK-REMOVED: $f"
        removed=$((removed + 1))
    fi
done

# ── MISPLACED: files that belong in subdirectories ──────────────────────────

declare -A MOVES=(
    ["messages.pot"]="locale/messages.pot"
    ["coverage.json"]="build/coverage.json"
    ["Makefile.pushwait"]="scripts/Makefile.pushwait"
)

for src in "${!MOVES[@]}"; do
    dst="${MOVES[$src]}"
    if [ -f "$src" ]; then
        mkdir -p "$(dirname "$dst")"
        mv "$src" "$dst"
        echo "MISPLACED-MOVED: $src -> $dst"
        removed=$((removed + 1))
    fi
done

echo ""
echo "clean-root: $removed file(s) cleaned."
