#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${GLUDD_INSTALL_DIR:-/usr/local}"
BIN_DIR="${INSTALL_DIR}/bin"
SHARE_DIR="${INSTALL_DIR}/share/gludd"

echo "Installing gludd to ${INSTALL_DIR} ..."

mkdir -p "${BIN_DIR}" "${SHARE_DIR}"

cp gludd "${BIN_DIR}/gludd"
chmod 755 "${BIN_DIR}/gludd"

if [ -d config ]; then cp -r config "${SHARE_DIR}/config"; fi
if [ -d templates ]; then cp -r templates "${SHARE_DIR}/templates"; fi
if [ -d playbooks ]; then cp -r playbooks "${SHARE_DIR}/playbooks"; fi

echo "gludd installed to ${BIN_DIR}/gludd"
