#!/bin/sh
set -e

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root. Use sudo."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BINARY="$SCRIPT_DIR/hottentot"

if [ ! -f "$BINARY" ]; then
    echo "ERROR: hottentot binary not found at $BINARY"
    exit 1
fi

echo "Installing hottentot daemon..."

cp "$BINARY" /usr/local/bin/hottentot
chmod 755 /usr/local/bin/hottentot

mkdir -p /etc/hottentot/config
mkdir -p /etc/hottentot/templates
mkdir -p /var/log/hottentot
mkdir -p /var/lib/hottentot

if [ -d "$SCRIPT_DIR/config" ]; then
    cp -r "$SCRIPT_DIR/config/"* /etc/hottentot/config/
fi

if [ -d "$SCRIPT_DIR/templates" ]; then
    cp -r "$SCRIPT_DIR/templates/"* /etc/hottentot/templates/
fi

if [ -f "$SCRIPT_DIR/hottentot.service" ]; then
    cp "$SCRIPT_DIR/hottentot.service" /etc/systemd/system/hottentot.service
    systemctl daemon-reload
fi

systemctl enable hottentot
systemctl start hottentot

echo ""
echo "Hottentot agent installed successfully."
echo "Check status with: systemctl status hottentot"
