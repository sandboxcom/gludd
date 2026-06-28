#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_break_glass
  short_description: OpenBao raft snapshot and restore (break-glass backup)
  description:
    - Wraps the OpenBao HTTP API for the two break-glass endpoints
      C(/v1/sys/storage/raft/snapshot) (GET — snapshot) and
      C(/v1/sys/storage/raft/restore) (POST — restore).
    - Mode C(snapshot) fetches the current raft snapshot bytes and writes them
      to C(output_path).
    - Mode C(restore) POSTs the bytes from C(restore_source) back into a running
      OpenBao server.
    - The C(token) argument is marked C(no_log=True); OpenBao tokens MUST NOT
      leak into Ansible task output.
    - This module is safe to call from C(check_mode) for snapshot (it does
      nothing destructive); restore is refused in check_mode.
  options:
    openbao_addr:
      description: Base URL of the OpenBao server (e.g. https://127.0.0.1:8200).
      type: str
      required: true
    token:
      description: OpenBao bearer token with snapshot/restore privileges.
      type: str
      required: true
      no_log: true
    output_path:
      description: Path where the snapshot bytes are written (mode=snapshot).
      type: str
      default: ""
    mode:
      description: Operation mode.
      type: str
      choices: [snapshot, restore]
      default: snapshot
    restore_source:
      description: Path to read bytes from for mode=restore (raw or GPG file).
                   The caller is responsible for GPG-decrypting prior to invocation;
                   this module reads bytes verbatim and POSTs them.
      type: str
      default: ""
  notes:
    - Uses Python stdlib urllib only — no hvac dependency on the Ansible controller.
    - Fails closed on any non-200 response from OpenBao.

EXAMPLES:
  - name: Snapshot OpenBao raft store to a temp file
    general_ludd.agent.gludd_break_glass:
      mode: snapshot
      openbao_addr: "https://127.0.0.1:8200"
      token: "{{ vault_token }}"
      output_path: "/tmp/openbao-snapshot.bin"
    register: snap

  - name: Restore OpenBao from a decrypted snapshot file
    general_ludd.agent.gludd_break_glass:
      mode: restore
      openbao_addr: "https://127.0.0.1:8200"
      token: "{{ vault_token }}"
      restore_source: "/tmp/openbao-snapshot.bin"

RETURN:
  size_bytes:
    description: Size of the snapshot written to output_path (mode=snapshot).
    type: int
    returned: when mode=snapshot
  sha256:
    description: SHA-256 hex digest of the snapshot bytes (mode=snapshot).
    type: str
    returned: when mode=snapshot
  saved_at:
    description: ISO-8601 UTC timestamp the snapshot was written.
    type: str
    returned: when mode=snapshot
  restored_at:
    description: ISO-8601 UTC timestamp the restore POST completed.
    type: str
    returned: when mode=restore
  raft_applied:
    description: True when OpenBao returned 200/204 from the restore endpoint.
    type: bool
    returned: when mode=restore
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import urllib.error
import urllib.request
from typing import Any

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]


_SNAPSHOT_PATH = "/v1/sys/storage/raft/snapshot"
_RESTORE_PATH = "/v1/sys/storage/raft/restore"


def _utc_iso() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _request(
    method: str,
    url: str,
    token: str,
    *,
    data: bytes | None = None,
    timeout: int = 30,
) -> tuple[int, bytes]:
    """Issue an HTTP request to OpenBao. Returns (status, body_bytes).

    Fails closed on any network / non-2xx error.
    """
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "X-Vault-Token": token,
            "Accept": "application/octet-stream",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read() if hasattr(exc, "read") else b""
        raise RuntimeError(
            f"OpenBao {method} {url} failed: HTTP {exc.code} {exc.reason}; "
            f"body={body[:200]!r}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"OpenBao {method} {url} unreachable: {exc.reason}"
        ) from exc


def do_snapshot(module: AnsibleModule, openbao_addr: str, token: str, output_path: str) -> dict[str, Any]:
    if not output_path:
        module.fail_json(msg="output_path is required for mode=snapshot")
    url = openbao_addr.rstrip("/") + _SNAPSHOT_PATH
    status, body = _request("GET", url, token, timeout=30)
    if status != 200:
        module.fail_json(
            msg=f"snapshot failed: HTTP {status}",
            status_code=status,
            body_preview=body[:200].decode(errors="replace"),
        )
    # Write the snapshot bytes atomically (temp + rename) so a partial file
    # is never observable by a downstream GPG encrypt step.
    tmp = output_path + ".partial"
    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, output_path)
    digest = hashlib.sha256(body).hexdigest()
    return {
        "size_bytes": len(body),
        "sha256": digest,
        "saved_at": _utc_iso(),
        "output_path": output_path,
        "changed": True,
    }


def do_restore(module: AnsibleModule, openbao_addr: str, token: str, restore_source: str) -> dict[str, Any]:
    if not restore_source:
        module.fail_json(msg="restore_source is required for mode=restore")
    if module.check_mode:
        module.fail_json(msg="restore mode refuses to run in check_mode (it mutates the OpenBao store)")
    if not os.path.isfile(restore_source):
        module.fail_json(msg=f"restore_source not found: {restore_source}")
    with open(restore_source, "rb") as f:
        payload = f.read()
    url = openbao_addr.rstrip("/") + _RESTORE_PATH
    status, body = _request("POST", url, token, data=payload, timeout=60)
    if status not in (200, 204):
        module.fail_json(
            msg=f"restore failed: HTTP {status}",
            status_code=status,
            body_preview=body[:200].decode(errors="replace"),
        )
    return {
        "restored_at": _utc_iso(),
        "raft_applied": True,
        "size_bytes": len(payload),
        "changed": True,
    }


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            openbao_addr=dict(type="str", required=True),
            token=dict(type="str", required=True, no_log=True),
            output_path=dict(type="str", default=""),
            mode=dict(type="str", choices=["snapshot", "restore"], default="snapshot"),
            restore_source=dict(type="str", default=""),
        ),
        supports_check_mode=True,
    )

    openbao_addr: str = module.params["openbao_addr"]
    token: str = module.params["token"]
    output_path: str = module.params["output_path"]
    mode: str = module.params["mode"]
    restore_source: str = module.params["restore_source"]

    try:
        if mode == "snapshot":
            if module.check_mode:
                module.exit_json(
                    changed=False,
                    msg="check_mode: snapshot would fetch /v1/sys/storage/raft/snapshot",
                    size_bytes=0,
                    sha256="",
                    saved_at=_utc_iso(),
                )
            result = do_snapshot(module, openbao_addr, token, output_path)
            module.exit_json(**result)
        else:  # restore
            result = do_restore(module, openbao_addr, token, restore_source)
            module.exit_json(**result)
    except RuntimeError as exc:
        module.fail_json(msg=str(exc))


if __name__ == "__main__":
    main()
