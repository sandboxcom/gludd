#!/usr/bin/env bash
set -euo pipefail

PREFIX="${PREFIX:-/opt/general-ludd}"
BINDIR="${BINDIR:-/usr/local/bin}"
CONFDIR="${CONFDIR:-/etc/general-ludd}"
LOGDIR="${LOGDIR:-/var/log/general-ludd}"
LIBDIR="${LIBDIR:-/var/lib/general-ludd}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"

echo "=== preflight checks ==="
if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "ERROR: install.sh must be run as root"
    exit 1
fi
if ! command -v uv &>/dev/null; then
    echo "ERROR: uv is required to install general-ludd"
    exit 1
fi

echo "installing to ${PREFIX}"

mkdir -p "${PREFIX}"

echo "copying distribution files..."
cp -r src "${PREFIX}/src"
cp -r config "${PREFIX}/config"

echo "setting up config..."
if [ ! -f "${PREFIX}/config/general-ludd.yml" ]; then
    cp config/general-ludd.yml "${PREFIX}/config/general-ludd.yml"
fi

echo "creating directories..."
mkdir -p "${CONFDIR}" "${LOGDIR}" "${LIBDIR}"
touch "${CONFDIR}/env"
chmod 600 "${CONFDIR}/env"

echo "installing binary..."
install -m 755 /usr/local/bin/gludd "${BINDIR}/gludd" 2>/dev/null || cp /usr/local/bin/gludd "${BINDIR}/gludd"

echo "installing systemd unit..."
cp dist/general-ludd.service "${SYSTEMD_DIR}/general-ludd.service"
"${SYSTEMCTL}" daemon-reload || true
"${SYSTEMCTL}" enable general-ludd.service || true
"${SYSTEMCTL}" start general-ludd.service || true

echo "install complete — general-ludd.yml lives at ${PREFIX}/config/general-ludd.yml"
