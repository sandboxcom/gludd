#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_environment
  short_description: Inject the consolidated environment + optimization brief as ansible_facts
  description:
    - Queries the daemon's read-only C(GET /api/environment) endpoint and returns
      the consolidated environment brief under C(ansible_facts.gludd_environment)
      so a playbook (or the model running a job) can see the environment it runs
      inside and how to optimize for the task.
    - Read-only and check-mode safe — it performs no writes.
    - Exposes C(models) (roster, NO secrets), C(routing), C(budget), C(compute),
      C(tools), C(skills), C(queues), C(system), and C(optimization) (advisor
      hints + per-work-type recommended profiles).
    - The model roster NEVER contains api keys, tokens, or credential aliases.
  options:
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
      default: 30
    work_type:
      description:
        - When set, ALSO query C(GET /api/environment/advise?work_type=...) and
          merge the per-task recommendation under
          C(ansible_facts.gludd_environment.advice). Free-form work kind, e.g.
          C(feature), C(bugfix), C(refactor), C(review), C(docs), C(chat).
        - When unset (the default) no advise call is made and no C(advice) key
          is added.
      type: str
    prompt_tokens:
      description:
        - Optional prompt size (tokens) forwarded to the advise endpoint so the
          cost projection and context-fit hint are accurate. Only used when
          C(work_type) is set.
      type: int
    priority:
      description:
        - Optimization priority forwarded to the advise endpoint. Only used when
          C(work_type) is set.
      type: str
      choices: [cost, quality, latency]
      default: quality

EXAMPLES:
  - name: Load gludd environment brief
    general_ludd.agent.gludd_environment:
    register: env

  - name: Prefer the recommended profile for mechanical work
    ansible.builtin.debug:
      msg: >-
        Use {{ ansible_facts.gludd_environment.optimization.recommended_profile_for.mechanical }}

  - name: Warn when budget pressure is flagged
    ansible.builtin.debug:
      msg: "Budget pressure detected"
    when: >-
      ansible_facts.gludd_environment.optimization.hints
      | selectattr('signal', 'equalto', 'budget') | list | length > 0

RETURN:
  ansible_facts:
    description: Facts dict containing the C(gludd_environment) snapshot.
    type: dict
    returned: always
    contains:
      gludd_environment:
        description:
          - Environment brief with C(models), C(routing), C(budget), C(compute),
            C(tools), C(skills), C(queues), C(system), and C(optimization).
        type: dict
        returned: always
"""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
    ok_result,
)


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=30),
            work_type=dict(type="str"),
            prompt_tokens=dict(type="int"),
            priority=dict(
                type="str",
                default="quality",
                choices=["cost", "quality", "latency"],
            ),
        ),
        supports_check_mode=True,
    )

    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    )

    resp = client.get("/api/environment")
    if resp.get("_error"):
        module.fail_json(**error_result(f"daemon error: {resp['_error']}"))
        return
    status_code = resp.get("_status", 0)
    if status_code == 401:
        module.fail_json(**error_result("unauthorized (bad or missing PSK)", status=401))
        return
    if status_code not in (200, 201):
        module.fail_json(
            **error_result(
                f"gludd_environment failed (HTTP {status_code})", status=status_code
            )
        )
        return

    snapshot = {k: v for k, v in resp.items() if not k.startswith("_")}

    # When work_type is set, ALSO fetch the per-task advice and merge it under
    # ``advice``. Read-only and check-mode safe — a second GET, never a write.
    work_type = module.params.get("work_type")
    if work_type:
        params: dict[str, object] = {
            "work_type": work_type,
            "priority": module.params.get("priority") or "quality",
        }
        prompt_tokens = module.params.get("prompt_tokens")
        if prompt_tokens is not None:
            params["prompt_tokens"] = prompt_tokens
        advise_resp = client.get("/api/environment/advise", params=params)
        if advise_resp.get("_error"):
            module.fail_json(
                **error_result(f"daemon error (advise): {advise_resp['_error']}")
            )
            return
        advise_status = advise_resp.get("_status", 0)
        if advise_status == 401:
            module.fail_json(
                **error_result(
                    "unauthorized (bad or missing PSK)", status=401
                )
            )
            return
        if advise_status not in (200, 201):
            module.fail_json(
                **error_result(
                    f"gludd_environment advise failed (HTTP {advise_status})",
                    status=advise_status,
                )
            )
            return
        snapshot["advice"] = {
            k: v for k, v in advise_resp.items() if not k.startswith("_")
        }

    module.exit_json(
        **ok_result({"ansible_facts": {"gludd_environment": snapshot}}, changed=False)
    )


if __name__ == "__main__":
    main()
