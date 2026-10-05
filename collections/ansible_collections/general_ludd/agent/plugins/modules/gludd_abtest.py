#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_abtest
  short_description: Crash-isolated A/B test of a candidate code variant in a fresh subprocess
  description:
    - Runs a baseline (A = current C(src)) and a candidate (B = candidate
      worktree) under the SAME workload, each in a FRESH interpreter child
      process via C(general_ludd.abtest.run_ab). The candidate is NEVER imported
      into this process, so a candidate built to crash the whole app
      (C(os._exit), segfault, infinite loop, OOM) CANNOT take down the Ansible
      controller or the daemon.
    - Fail-closed: B is promoted ONLY if A passed and B ran ok, did not crash,
      did not time out, and stayed within a duration slack. Any crash/timeout
      yields C(promote=false).
    - Runs in-process (same venv as C(general_ludd)); does NOT call the daemon.
  options:
    candidate_root:
      description: Candidate worktree root (its C(src) holds the variant).
      type: str
      required: true
    baseline_root:
      description: >
        Baseline root for the A run (its C(src) holds the current code). Defaults
        to C(repo_root) when omitted.
      type: str
    repo_root:
      description: Repo root used as the baseline when C(baseline_root) is unset.
      type: str
      default: "."
    module:
      description: Dotted module name the workload imports from each C(src).
      type: str
      required: true
    expect_attr:
      description: Optional attribute the workload asserts is present on the module.
      type: str
    timeout:
      description: Per-run wall-clock timeout in seconds.
      type: float
      default: 60.0
    mem_limit_mb:
      description: Per-run address-space limit in MB (POSIX, best-effort).
      type: int
      default: 512
    verdict_path:
      description: Optional path to write the ab_verdict JSON artifact to.
      type: str

EXAMPLES:
  - name: A/B test a candidate worktree (crash-isolated)
    general_ludd.agent.gludd_abtest:
      candidate_root: "/tmp/candidate-worktree"
      repo_root: "/repo"
      module: "general_ludd.example.leaf"
      verdict_path: "/tmp/ab_verdict.json"
    register: ab

RETURN:
  verdict:
    description: The A/B verdict (a, b, promote, reason).
    type: dict
    returned: always
  promote:
    description: Whether the candidate may be promoted.
    type: bool
    returned: always
"""

from __future__ import annotations

import json
import os

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import GluddClient


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            candidate_root=dict(type="str", required=True),
            baseline_root=dict(type="str", default=None),
            repo_root=dict(type="str", default="."),
            module=dict(type="str", required=True),
            expect_attr=dict(type="str", default=None),
            timeout=dict(type="float", default=60.0),
            mem_limit_mb=dict(type="int", default=512),
            verdict_path=dict(type="str", default=None),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
        ),
        supports_check_mode=True,
    )

    params = module.params
    baseline = os.path.abspath(params["baseline_root"] or params["repo_root"])
    candidate = os.path.abspath(params["candidate_root"])
    if module.check_mode:
        module.exit_json(changed=False, failed=False, verdict={}, promote=False)
        return
    response = GluddClient(
        base_url=params["daemon_url"],
        psk=params["psk"],
        timeout=max(30, int(params["timeout"] * 2) + 10),
    ).post(
        "/admin/abtest/run",
        {
            "baseline_root": baseline,
            "candidate_root": candidate,
            "module": params["module"],
            "expect_attr": params["expect_attr"],
            "timeout": params["timeout"],
            "mem_limit_mb": params["mem_limit_mb"],
        },
    )
    if response.get("_error") or response.get("_status") != 200:
        module.fail_json(msg=str(response.get("detail") or response.get("_error") or "A/B test failed"))
        return
    verdict_dict = response.get("verdict", {})

    if params["verdict_path"]:
        try:
            with open(params["verdict_path"], "w", encoding="utf-8") as fh:
                json.dump(verdict_dict, fh, indent=2)
        except OSError as exc:
            module.fail_json(msg=f"could not write verdict artifact: {exc}")
            return

    module.exit_json(
        changed=False,
        failed=False,
        verdict=verdict_dict,
        promote=bool(response.get("promote", False)),
    )


if __name__ == "__main__":
    main()
