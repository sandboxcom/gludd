#!/usr/bin/env bash
# setup.sh — Wire the E2E test project to the main repo's enforcement plugins.
#
# Usage (from _test_project/ dir):
#   bash setup.sh                    # auto-detect main repo
#   bash setup.sh /path/to/gludd     # explicit main repo path
#
# Creates symlinks for: .opencode/plugin/, .opencode/plugins/, .opencode/lib/,
# .opencode/impl/, and node_modules/@opencode-ai/plugin.
# When deploying to a temp dir, run: bash setup.sh --copy /tmp/test/

set -euo pipefail

MODE="link"
MAIN_REPO=""
TARGET_DIR=""

for arg in "$@"; do
    case "$arg" in
        --copy) MODE="copy";;
        --link) MODE="link";;
        /*) 
            if [ -z "$MAIN_REPO" ]; then MAIN_REPO="$arg"
            elif [ -z "$TARGET_DIR" ]; then TARGET_DIR="$arg"; fi
            ;;
        *) echo "Unknown arg: $arg"; exit 1;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
[ -z "$MAIN_REPO" ] && MAIN_REPO="$(cd "$SCRIPT_DIR/../../.." && pwd)"
DEST="$SCRIPT_DIR"

echo "Mode: $MODE"
echo "Main repo: $MAIN_REPO"
echo "Dest: $DEST"

link_or_copy() {
    local src="$1"
    local dst="$2"
    mkdir -p "$(dirname "$dst")"
    rm -rf "$dst"
    if [ "$MODE" = "copy" ]; then
        if [ -d "$src" ]; then
            cp -RP "$src" "$dst"
        else
            cp -P "$src" "$dst"
        fi
        echo "  copied: $dst"
    else
        ln -sf "$src" "$dst"
        echo "  linked: $dst -> $src"
    fi
}

# 1. Plugin files
for f in "$MAIN_REPO/.opencode/plugin"/*.ts "$MAIN_REPO/.opencode/plugin"/*.mjs; do
    [ -f "$f" ] || continue
    link_or_copy "$f" "$DEST/.opencode/plugin/$(basename "$f")"
done

# 2. Impl directory
if [ -d "$MAIN_REPO/.opencode/plugin/impl" ]; then
    for f in "$MAIN_REPO/.opencode/plugin/impl"/*.ts "$MAIN_REPO/.opencode/plugin/impl"/*.mjs "$MAIN_REPO/.opencode/plugin/impl"/*.json; do
        [ -f "$f" ] || continue
        link_or_copy "$f" "$DEST/.opencode/plugin/impl/$(basename "$f")"
    done
fi

# 3. Plugins (watchdog)
for f in "$MAIN_REPO/.opencode/plugins"/*.ts "$MAIN_REPO/.opencode/plugins"/*.mjs; do
    [ -f "$f" ] || continue
    link_or_copy "$f" "$DEST/.opencode/plugins/$(basename "$f")"
done

# 4. Lib directory (shared.ts, hot_reload.ts, multitask_config.ts, etc.)
if [ -d "$MAIN_REPO/.opencode/lib" ]; then
    for f in "$MAIN_REPO/.opencode/lib"/*.ts; do
        [ -f "$f" ] || continue
        link_or_copy "$f" "$DEST/.opencode/lib/$(basename "$f")"
    done
fi

# 5. Node modules (just @opencode-ai/plugin, which all plugins import)
OPN_PKG="$MAIN_REPO/.opencode/node_modules/@opencode-ai"
if [ -d "$OPN_PKG" ]; then
    link_or_copy "$OPN_PKG" "$DEST/.opencode/node_modules/@opencode-ai"
fi

# 6. .opencode/agent/ directory (if it exists)
if [ -d "$MAIN_REPO/.opencode/agent" ]; then
    link_or_copy "$MAIN_REPO/.opencode/agent" "$DEST/.opencode/agent"
fi

echo ""
echo "Setup complete. Plugin count: $(ls "$DEST/.opencode/plugin"/*.ts 2>/dev/null | wc -l | tr -d ' ')"
echo "Lib count: $(ls "$DEST/.opencode/lib"/*.ts 2>/dev/null | wc -l | tr -d ' ')"
