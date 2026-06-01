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

echo "Installing General Ludd Agent daemon..."

cp "$BINARY" /usr/local/bin/gludd
chmod 755 /usr/local/bin/gludd

mkdir -p /etc/general-ludd/config
mkdir -p /etc/general-ludd/templates
mkdir -p /var/log/general-ludd
mkdir -p /var/lib/general-ludd

if [ -d "$SCRIPT_DIR/config" ]; then
    cp -r "$SCRIPT_DIR/config/"* /etc/general-ludd/config/
fi

if [ -d "$SCRIPT_DIR/templates" ]; then
    cp -r "$SCRIPT_DIR/templates/"* /etc/general-ludd/templates/
fi

if [ -f "$SCRIPT_DIR/general-ludd.service" ]; then
    cp "$SCRIPT_DIR/general-ludd.service" /etc/systemd/system/general-ludd.service
    systemctl daemon-reload
fi

systemctl enable general-ludd
systemctl start general-ludd

echo ""
echo "General Ludd Agent installed successfully."
echo "Check status with: systemctl status general-ludd"
