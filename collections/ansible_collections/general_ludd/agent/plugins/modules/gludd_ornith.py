#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_ornith
  short_description: Pull rejected Ornith training pairs and invoke improvement rollouts
  description:
    - Bidirectional seam for the gludd x Ornith symbiotic loop. Two states:
    - C(state=pairs) — fetch the most-recent training pairs whose outcome
      matches a comma-separated status list (e.g.
      C(rejected_by_gate,rejected_by_review,reverted)). These are the
      artifacts that NEED improvement. Hits the daemon's
      C(GET /admin/ornith/pairs) endpoint.
    - C(state=improve) — invoke the daemon's model gateway
      (C(POST /admin/models/call)) with a structured "improve this artifact"
      prompt and return the proposed diff. The caller is responsible for
      writing the diff to disk, opening a PR, and filing a human-todo for
      review. The PR is NEVER auto-merged — the human-todo is the gate.
    - Check mode skips both the network call and the model invocation.
  options:
    state:
      description: Desired state.
      type: str
      default: improve
      choices: [pairs, improve]
    status:
      description: Comma-separated outcome statuses to filter on (state=pairs).
      type: str
      default: "rejected_by_gate,rejected_by_review,reverted"
    limit:
      description: Maximum pairs to return (state=pairs).
      type: int
      default: 10
    lookback_days:
      description: Restrict to pairs invoked within the last N days (state=pairs).
      type: int
      default: 14
    project_id:
      description: Optional project filter (state=pairs).
      type: str
      default: ""
    task_description:
      description: >
        The structured task prompt for the improvement rollout (state=improve).
        The role passes a templated string that names the artifact and cites
        the failure outcomes.
      type: str
      default: ""
    target_files:
      description: List of artifact paths to improve (state=improve).
      type: list
      elements: str
      default: []
    max_iterations:
      description: Max tool-call iterations for the improvement rollout.
      type: int
      default: 5
    model_profile:
      description: Model profile to route the improvement call through.
      type: str
      default: ""
    agent_id:
      description: Filing agent identifier (recorded in the daemon audit row).
      type: str
      default: "ornith_self_improve"
    daemon_url:
      description: Base URL of the daemon.
      type: str
      default: "http://localhost:8000"
    psk:
      description: Pre-shared key for daemon auth.
      type: str
      no_log: true
      default: ""
    timeout:
      description: Request timeout in seconds.
      type: int
      default: 120

EXAMPLES:
  - name: Pull the rejected training pairs
    general_ludd.agent.gludd_ornith:
      state: pairs
      status: "rejected_by_gate,rejected_by_review,reverted"
      limit: 10
      lookback_days: 14
    register: pairs

  - name: Invoke Ornith to improve an artifact
    general_ludd.agent.gludd_ornith:
      state: improve
      task_description: "Improve this playbook based on the 3 gate failures attached."
      target_files:
        - "playbooks/agent_orchestrate.yml"
      max_iterations: 5
    register: rollout

RETURN:
  pairs:
    description: List of matching training pairs (state=pairs).
    type: list
    returned: when state=pairs
  count:
    description: Number of pairs returned (state=pairs).
    type: int
    returned: when state=pairs
  patch:
    description: The proposed improvement text (state=improve).
    type: str
    returned: when state=improve
  usage:
    description: Token usage from the model call (state=improve).
    type: dict
    returned: when state=improve
"""

from __future__ import annotations

from typing import Any, cast

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
    ok_result,
)


def _fetch_pairs(
    client: GluddClient,
    status: str,
    limit: int,
    lookback_days: int,
    project_id: str,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "status": status,
        "limit": limit,
        "lookback_days": lookback_days,
    }
    if project_id:
        params["project_id"] = project_id
    return cast(
        dict[str, Any],
        client.get("/admin/ornith/pairs", params=params),
    )


def _run_improve(
    client: GluddClient,
    task_description: str,
    target_files: list[str],
    max_iterations: int,
    model_profile: str,
    agent_id: str,
) -> dict[str, Any]:
    """Invoke /admin/models/call with a structured improvement prompt.

    The prompt names the target files + the task description verbatim so the
    model sees the failure context the role attached. We DO NOT record a
    fresh training pair here — that is the role's responsibility after it
    writes the patch to disk (so the recorded scaffold matches what was
    actually proposed, not what was requested).
    """
    files_block = "\n".join(f"- {f}" for f in target_files) or "(none)"
    prompt = (
        f"Improve the following artifact(s) based on the failure outcomes "
        f"cited below. Preserve the documented contract (public task names, "
        f"module args, return fields). Output ONLY a unified diff.\n\n"
        f"Target artifacts:\n{files_block}\n\n"
        f"Task description / failure context:\n{task_description}\n"
    )
    payload: dict[str, Any] = {
        "prompt": prompt,
        "max_tokens": 4096,
        "agent_id": agent_id,
    }
    if model_profile:
        payload["model_profile"] = model_profile
    # max_iterations is informational here — the daemon's gateway is single-shot.
    # We surface it in the audit metadata so the trainer can correlate.
    payload["metadata"] = {"max_iterations": max_iterations}
    return cast(
        dict[str, Any],
        client.post("/admin/models/call", payload),
    )


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            state=dict(type="str", default="improve", choices=["pairs", "improve"]),
            status=dict(type="str", default="rejected_by_gate,rejected_by_review,reverted"),
            limit=dict(type="int", default=10),
            lookback_days=dict(type="int", default=14),
            project_id=dict(type="str", default=""),
            task_description=dict(type="str", default=""),
            target_files=dict(type="list", elements="str", default=[]),
            max_iterations=dict(type="int", default=5),
            model_profile=dict(type="str", default=""),
            agent_id=dict(type="str", default="ornith_self_improve"),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=120),
        ),
        required_if=[
            ("state", "improve", ["task_description"]),
        ],
        supports_check_mode=True,
    )

    state: str = module.params["state"]
    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    )

    if module.check_mode:
        if state == "pairs":
            module.exit_json(**ok_result(
                {"pairs": [], "count": 0}, changed=False,
            ))
        else:
            module.exit_json(**ok_result(
                {"patch": "[check-mode: improvement skipped]",
                 "usage": {}, "iterations": 0},
                changed=False,
            ))
        return

    if state == "pairs":
        resp = _fetch_pairs(
            client,
            status=module.params["status"],
            limit=module.params["limit"],
            lookback_days=module.params["lookback_days"],
            project_id=module.params["project_id"],
        )
    else:
        if not module.params["task_description"]:
            module.fail_json(**error_result("state=improve requires task_description"))
            return
        resp = _run_improve(
            client,
            task_description=module.params["task_description"],
            target_files=module.params["target_files"],
            max_iterations=module.params["max_iterations"],
            model_profile=module.params["model_profile"],
            agent_id=module.params["agent_id"],
        )

    if resp.get("_error"):
        module.fail_json(**error_result(f"daemon error: {resp['_error']}"))
        return
    status_code = resp.get("_status", 0)
    if status_code not in (200, 201):
        msg = resp.get("detail") or f"HTTP {status_code}"
        module.fail_json(**error_result(
            f"gludd_ornith {state} failed: {msg}", status=status_code,
        ))
        return

    if state == "pairs":
        pairs = resp.get("pairs", []) or []
        module.exit_json(**ok_result(
            {"pairs": pairs, "count": len(pairs)}, changed=False,
        ))
    else:
        module.exit_json(**ok_result(
            {
                "patch": resp.get("text", ""),
                "usage": resp.get("usage", {}),
                "iterations": 1,
                "model_profile_id": resp.get("model_profile_id", ""),
            },
            changed=True,
        ))


if __name__ == "__main__":
    main()
