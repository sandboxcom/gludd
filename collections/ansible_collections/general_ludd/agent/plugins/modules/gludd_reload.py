#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_reload
  short_description: Hot-rotate a validated leaf code module with health-gated auto-rollback
  description:
    - Wraps C(general_ludd.reload.hot_reloader.HotReloader.reload_code_module):
      snapshots the live module bytes, C(os.replace)s the candidate source over
      the live path, C(importlib.reload)s the module, then runs a health gate
      (a C(/readyz) poll). If the health gate fails or the reload raises, the
      original bytes are restored and the module is reloaded again — the live
      module ends up exactly as it started.
    - Fail-closed: a missing/non-importable target, a missing candidate, or a
      failed health gate all yield C(success=false).
    - Runs in-process (same venv as C(general_ludd)).
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

import json
import urllib.error
import urllib.request


def _make_health_check(health_url: str | None, timeout: float):  # type: ignore[no-untyped-def]
    """Build a health_check callable that polls ``health_url``.

    Returns True (healthy) when omitted. Otherwise returns False (unhealthy) on
    any non-200 status, a transport error, or a JSON body reporting
    ``degraded=true`` — so a degraded daemon triggers rollback.
    """
    if not health_url:
        return None

    def _check() -> bool:
        try:
            req = urllib.request.Request(health_url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    return False
                raw = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError):
            return False
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            return True  # 200 with non-JSON body counts as healthy
        if isinstance(body, dict) and body.get("degraded") is True:
            return False
        return True

    return _check


def main() -> None:
    from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]

    module = AnsibleModule(
        argument_spec=dict(
            module_name=dict(type="str", required=True),
            candidate_source_path=dict(type="str", required=True),
            health_url=dict(type="str", default=None),
            health_timeout=dict(type="float", default=5.0),
            config_dir=dict(type="str", default="config"),
            result_path=dict(type="str", default=None),
        ),
        supports_check_mode=False,
    )

    try:
        from general_ludd.reload.hot_reloader import HotReloader
    except ImportError as exc:
        module.fail_json(msg=f"general_ludd.reload not importable: {exc}")
        return

    params = module.params
    health_check = _make_health_check(params["health_url"], params["health_timeout"])

    reloader = HotReloader(config_dir=params["config_dir"])
    result = reloader.reload_code_module(
        module_name=params["module_name"],
        candidate_source_path=params["candidate_source_path"],
        health_check=health_check,
    )

    result_dict = {
        "success": result.success,
        "rolled_back": bool(result.details.get("rolled_back")),
        "module": params["module_name"],
        "error": result.error,
        "details": result.details,
    }

    if params["result_path"]:
        try:
            with open(params["result_path"], "w", encoding="utf-8") as fh:
                json.dump(result_dict, fh, indent=2)
        except OSError as exc:
            module.fail_json(msg=f"could not write promotion result: {exc}")
            return

    module.exit_json(
        changed=result.success,
        failed=False,
        success=result.success,
        rolled_back=result_dict["rolled_back"],
        result=result_dict,
    )


if __name__ == "__main__":
    main()
