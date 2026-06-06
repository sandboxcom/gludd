#!/usr/bin/env bash
set -e

echo "=== General Ludd Agent Installer ==="
echo ""

# Preflight checks
echo "[preflight] Checking system requirements..."

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: This script must be run as root (or with sudo)."
    echo "       Use: sudo ./install.sh"
    exit 1
fi

ARCH=$(uname -m)
OS=$(uname -s)
if [[ "$OS" != "Linux" && "$OS" != "Darwin" ]]; then
    echo "WARNING: Unsupported OS: $OS. Continuing anyway..."
fi

echo "  OS:     $OS"
echo "  Arch:   $ARCH"

# Find the gludd binary
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GLUDD_BINARY="${SCRIPT_DIR}/general-ludd-agent-"*"/gludd"

if [[ ! -f "$GLUDD_BINARY" ]]; then
    # Try flat layout
    GLUDD_BINARY="${SCRIPT_DIR}/gludd"
fi

if [[ ! -f "$GLUDD_BINARY" ]]; then
    echo "ERROR: gludd binary not found in $SCRIPT_DIR"
    exit 1
fi

echo "  Binary: $GLUDD_BINARY"

# Create directories
echo "[preflight] Creating directories..."
mkdir -p /var/log/general-ludd
mkdir -p /var/lib/general-ludd
mkdir -p /etc/general-ludd

# Copy binary
echo "Installing gludd binary to /usr/local/bin/gludd..."
install -m 755 "$GLUDD_BINARY" /usr/local/bin/gludd

# Install systemd unit (Linux only)
if [[ "$OS" == "Linux" ]]; then
    echo "Installing general-ludd.service systemd unit..."

    UNIT_SRC="${SCRIPT_DIR}/general-ludd-agent-"*"/general-ludd.service"
    if [[ ! -f "$UNIT_SRC" ]]; then
        UNIT_SRC="${SCRIPT_DIR}/general-ludd.service"
    fi

    if [[ -f "$UNIT_SRC" ]]; then
        cp "$UNIT_SRC" /etc/systemd/system/general-ludd.service
        systemctl daemon-reload
        echo "  Unit installed. To start manually: sudo systemctl start general-ludd"
    else
        echo "  WARNING: general-ludd.service not found. Creating default unit..."

        cat > /etc/systemd/system/general-ludd.service << 'UNIT'
[Unit]
Description=General Ludd Agent Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=general-ludd
EnvironmentFile=-/etc/general-ludd/env
ExecStart=/usr/local/bin/gludd daemon --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5s
NoNewPrivileges=true
ProtectSystem=strict
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
        systemctl daemon-reload
    fi
fi

echo ""
echo "=== Configuration ==="
echo "The daemon reads configuration from:"
echo "  ~/.config/general-ludd/general-ludd.yml"
echo "  /etc/general-ludd/general-ludd.yml (fallback)"
echo ""
echo "Copy the example config to get started:"
echo "  cp /path/to/config/general-ludd.yml ~/.config/general-ludd/"
echo ""
echo "=== Installation complete! ==="
if [[ "$OS" == "Linux" ]]; then
    echo "To start the daemon: sudo systemctl start general-ludd"
    echo "To enable at boot:  sudo systemctl enable general-ludd"
fi
echo ""
