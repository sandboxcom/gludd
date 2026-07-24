#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${1:-/usr/local/bin}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Installing gludd to ${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/gludd" "${INSTALL_DIR}/gludd"
chmod +x "${INSTALL_DIR}/gludd"

echo "Done. Run 'gludd --help' to get started."
