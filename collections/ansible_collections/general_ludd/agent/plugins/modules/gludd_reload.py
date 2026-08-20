#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_reload
  short_description: Hot-rotate a validated leaf code module with health-gated auto-rollback
  description:
    - Calls the authenticated Gludd daemon reload endpoint, which wraps
      C(general_ludd.reload.hot_reloader.HotReloader.reload_code_module):
      snapshots the live module bytes, C(os.replace)s the candidate source over
      the live path, C(importlib.reload)s the module, then runs a health gate
      (a C(/readyz) poll). If the health gate fails or the reload raises, the
      original bytes are restored and the module is reloaded again — the live
      module ends up exactly as it started.
    - Fail-closed: a missing/non-importable target, a missing candidate, or a
      failed health gate all yield C(success=false).
    - The collection validates and hashes the candidate but never imports the
      Gludd core runtime; daemon authentication is explicit.
  options:
    module_name:
      description: Dotted name of the live leaf module to rotate.
      type: str
      required: true
    candidate_source_path:
      description: Path to the candidate source file to swap over the live path.
      type: str
      required: true
    health_url:
      description: >
        Optional health endpoint polled after reload (e.g. a C(/readyz) URL). A
        non-200 response — or a JSON body with C(degraded=true) — fails the
        health gate and triggers rollback. When omitted the reload is treated as
        healthy (the caller is responsible for the health contract).
      type: str
    health_timeout:
      description: Timeout in seconds for the health probe.
      type: float
      default: 5.0
    config_dir:
      description: Config dir for the HotReloader (unused for code rotation).
      type: str
      default: "config"
    result_path:
      description: Optional path to write the promotion_result JSON artifact to.
      type: str

EXAMPLES:
  - name: Hot-rotate a validated leaf module
    general_ludd.agent.gludd_reload:
      module_name: "general_ludd.example.leaf"
      candidate_source_path: "/tmp/candidate/src/general_ludd/example/leaf.py"
      health_url: "http://127.0.0.1:8000/readyz"
      result_path: "/tmp/promotion_result.json"
    register: reload_result

RETURN:
  success:
    description: Whether the hot-rotation succeeded and passed the health gate.
    type: bool
    returned: always
  rolled_back:
    description: Whether the reload was rolled back after a failed health gate.
    type: bool
    returned: always
"""

from __future__ import annotations

import hashlib
import json
import os

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import GluddClient


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            module_name=dict(type="str", required=True),
            candidate_source_path=dict(type="str", required=True),
            health_url=dict(type="str", default=None),
            health_timeout=dict(type="float", default=5.0),
            config_dir=dict(type="str", default="config"),
            base_source_path=dict(type="str", default=None),
            role=dict(type="str", default=None),
            expected_sha256=dict(type="str", default=None),
            idempotency_key=dict(type="str", default=None),
            result_path=dict(type="str", default=None),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
        ),
        supports_check_mode=False,
    )

    params = module.params
    candidate_source_path = os.path.abspath(params["candidate_source_path"])
    try:
        with open(candidate_source_path, "rb") as candidate_file:
            candidate_sha256 = hashlib.sha256(candidate_file.read()).hexdigest()
    except OSError as exc:
        module.fail_json(msg=f"could not read candidate source: {exc}")
        return
    if params["expected_sha256"] and params["expected_sha256"] != candidate_sha256:
        module.fail_json(msg="candidate source digest does not match expected_sha256")
        return
    idempotency_key = params["idempotency_key"] or (
        f"reload:{params['module_name']}:{candidate_sha256}"
    )
    result = GluddClient(
        base_url=params["daemon_url"],
        psk=params["psk"],
        timeout=max(1, int(params["health_timeout"]) + 30),
    ).post(
        "/admin/reload/code",
        {
            "module_name": params["module_name"],
            "candidate_source_path": candidate_source_path,
            "expected_sha256": candidate_sha256,
            "base_source_path": (
                os.path.abspath(params["base_source_path"])
                if params["base_source_path"]
                else None
            ),
            "role": params["role"],
            "health_url": params["health_url"],
            "health_timeout": params["health_timeout"],
            "idempotency_key": idempotency_key,
        },
    )
    result_dict = {
        "success": bool(result.get("success", False)),
        "rolled_back": bool(result.get("rolled_back", False)),
        "module": params["module_name"],
        "error": result.get("error") or result.get("_error"),
        "details": result.get("details", {}),
    }

    if result.get("_error") or result.get("_status") != 200:
        module.fail_json(
            msg=str(result.get("detail") or result.get("_error") or "code reload failed"),
            success=False,
            rolled_back=result_dict["rolled_back"],
            result=result_dict,
        )
        return

    if params["result_path"]:
        try:
            with open(params["result_path"], "w", encoding="utf-8") as fh:
                json.dump(result_dict, fh, indent=2)
        except OSError as exc:
            module.fail_json(msg=f"could not write promotion result: {exc}")
            return

    module.exit_json(
        changed=result_dict["success"],
        failed=False,
        success=result_dict["success"],
        rolled_back=result_dict["rolled_back"],
        result=result_dict,
    )


if __name__ == "__main__":
    main()
