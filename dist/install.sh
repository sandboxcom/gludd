#!/bin/sh
set -e

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root. Use sudo."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BINARY="$SCRIPT_DIR/gludd"

if [ ! -f "$BINARY" ]; then
    echo "ERROR: gludd binary not found at $BINARY"
    exit 1
fi

echo "=== General Ludd Agent Installer ==="
echo ""

# ── Pre-flight checks ──────────────────────────────────────────────────────
echo "Running pre-flight checks..."

ERRORS=0
WARNINGS=0

if command -v psql >/dev/null 2>&1; then
    if psql -U postgres -c "SELECT 1" >/dev/null 2>&1; then
        echo "  [OK] PostgreSQL is running"
    else
        echo "  [WARN] PostgreSQL not reachable (daemon will use SQLite fallback)"
        WARNINGS=$((WARNINGS + 1))
    fi
else
    echo "  [WARN] psql not found (PostgreSQL may not be installed)"
    WARNINGS=$((WARNINGS + 1))
fi

if [ -f /etc/general-ludd/general-ludd.yml ]; then
    echo "  [WARN] Existing config found at /etc/general-ludd/ (will NOT overwrite)"
    WARNINGS=$((WARNINGS + 1))
fi

if [ -z "$ZAI_API_KEY" ] && [ -z "$OPENAI_API_KEY" ] && [ ! -f /etc/general-ludd/env ]; then
    echo "  [WARN] No API key detected. Set ZAI_API_KEY or OPENAI_API_KEY before starting."
    WARNINGS=$((WARNINGS + 1))
fi

if [ "$WARNINGS" -gt 0 ]; then
    echo ""
    echo "  $WARNINGS warning(s). The daemon may not start until these are resolved."
fi

if [ "$ERRORS" -gt 0 ]; then
    echo ""
    echo "  $ERRORS error(s). Fix these before proceeding."
    exit 1
fi

echo ""

# ── Install binary ─────────────────────────────────────────────────────────
echo "Installing gludd binary..."
cp "$BINARY" /usr/local/bin/gludd
chmod 755 /usr/local/bin/gludd

# ── Create directories ─────────────────────────────────────────────────────
mkdir -p /etc/general-ludd/config
mkdir -p /etc/general-ludd/templates
mkdir -p /var/log/general-ludd
mkdir -p /var/lib/general-ludd

# ── Install config (only if no existing config) ────────────────────────────
if [ ! -f /etc/general-ludd/general-ludd.yml ]; then
    if [ -f "$SCRIPT_DIR/config/general-ludd.yml" ]; then
        cp "$SCRIPT_DIR/config/general-ludd.yml" /etc/general-ludd/general-ludd.yml
        echo "Installed /etc/general-ludd/general-ludd.yml"
    fi
else
    echo "Preserving existing /etc/general-ludd/general-ludd.yml"
fi

if [ ! -f /etc/general-ludd/env ]; then
    cat > /etc/general-ludd/env << 'ENVEOF'
# General Ludd Agent — Secrets and Environment
# =============================================
# API keys go here (mode 600, owner-only readable).
# NEVER put actual keys in config/*.yml files.
# Model profiles reference these via credential_alias.
#
# Resolution order: OpenBao/Vault -> env vars below -> error
#
# ── Z.AI (default provider) ──
# ZAI_API_KEY=your-zai-api-key
# ZAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
#
# ── OpenAI ──
# OPENAI_API_KEY=sk-your-openai-key
#
# ── OpenRouter ──
# OPENROUTER_API_KEY=your-key
# OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
#
# ── Database ──
# DATABASE_URL=postgresql://gludd:password@localhost:5432/gludd
#
# ── HuggingFace (local model download) ──
# HF_TOKEN=your-hf-token
ENVEOF
    echo "Created /etc/general-ludd/env (edit to add your API keys)"
else
    echo "Preserving existing /etc/general-ludd/env"
fi

if [ -d "$SCRIPT_DIR/config" ]; then
    for f in "$SCRIPT_DIR/config/"*; do
        basename=$(basename "$f")
        if [ "$basename" != "general-ludd.yml" ] && [ ! -f "/etc/general-ludd/config/$basename" ]; then
            cp -r "$f" "/etc/general-ludd/config/"
        fi
    done
    echo "Installed config files to /etc/general-ludd/config/"
fi

if [ -d "$SCRIPT_DIR/templates" ]; then
    cp -r "$SCRIPT_DIR/templates/"* /etc/general-ludd/templates/ 2>/dev/null || true
fi

chmod 600 /etc/general-ludd/env
chmod 700 /etc/general-ludd

# ── Install systemd unit (Linux only) ──────────────────────────────────────
if command -v systemctl >/dev/null 2>&1; then
    if [ -f "$SCRIPT_DIR/general-ludd.service" ]; then
        cp "$SCRIPT_DIR/general-ludd.service" /etc/systemd/system/general-ludd.service
        systemctl daemon-reload
        echo "Installed systemd service unit"
    fi
else
    echo "NOTE: systemctl not found. Skipping systemd service installation."
    echo "      On macOS, start the daemon manually: gludd daemon"
fi

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit /etc/general-ludd/env to add your API keys"
echo "  2. Edit /etc/general-ludd/general-ludd.yml to configure model and database"
echo "  3. Start the daemon:"
echo "       sudo systemctl start general-ludd   (Linux)"
echo "       gludd daemon                         (macOS)"
echo "  4. Verify: gludd health"
echo ""
echo "Documentation:"
echo "  See docs/ in this archive for quickstart, configuration, and architecture guides."
