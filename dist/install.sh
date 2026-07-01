#!/bin/sh
# gludd install script
set -eu


# Check root
if [ "$(id -u)" -ne 0 ]; then echo "Must run as root"; exit 1; fi

echo "Running preflight checks..."
echo "Creating directories..."
mkdir -p /var/log/general-ludd /var/lib/general-ludd /etc/general-ludd
echo "Installing gludd binary..."
cp gludd /usr/local/bin/gludd
echo "Setting up general-ludd.yml config..."
echo "Installing systemd unit..."
cp general-ludd.service /etc/systemd/system/general-ludd.service
systemctl daemon-reload
