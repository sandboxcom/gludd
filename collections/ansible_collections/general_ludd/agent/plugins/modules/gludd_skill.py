#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_skill
  short_description: Select and render a skill with Jinja2 variables
  description:
    - Looks up a skill by name or trigger pattern and renders its body
      with Jinja2 C(StrictUndefined) — an unknown variable is an error,
      not silent empty text.
    - Uses the shared C(render_skill) renderer also wired into
      C(execution.engine) so playbook and prompt paths render identically.
    - Frontmatter C(required_vars) list is checked before rendering; missing
      vars fail the task with the variable name in the error message.
  options:
    name:
      description: Skill name (exact match). Mutually exclusive with C(trigger).
      type: str
    trigger:
      description: Trigger text for fuzzy/pattern matching. Mutually exclusive with C(name).
      type: str
    variables:
      description: Dict of template variables to inject.
      type: dict
      default: {}
    skills_path:
      description: >
        Directory to scan for skill markdown files. Defaults to
        C(.opencode/skills) relative to the playbook directory, then
        C(~/.config/general_ludd/skills).
      type: str
      default: ""

EXAMPLES:
  - name: Render a skill
    general_ludd.agent.gludd_skill:
      name: "code-review"
      variables:
        project_name: "myproject"
        language: "python"
    register: skill_result

  - name: Use rendered body as model prompt prefix
    ansible.builtin.set_fact:
      system_prompt: "{{ skill_result.rendered_body }}"

RETURN:
  skill_name:
    description: Resolved skill name.
    type: str
    returned: success
  rendered_body:
    description: Skill body after Jinja2 rendering.
    type: str
    returned: success
  required_vars:
    description: Variables declared in skill frontmatter.
    type: list
    returned: success
"""

from __future__ import annotations

from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
    ok_result,
)


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            name=dict(type="str", default=None),
            trigger=dict(type="str", default=None),
            variables=dict(type="dict", default={}),
            skills_path=dict(type="str", default=""),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=30),
        ),
        mutually_exclusive=[["name", "trigger"]],
        required_one_of=[["name", "trigger"]],
        supports_check_mode=True,
    )

    skill_name_param: str | None = module.params["name"]
    trigger: str | None = module.params["trigger"]
    variables: dict[str, Any] = module.params["variables"] or {}
    skills_path_param: str = module.params["skills_path"]

    response = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    ).post(
        "/admin/skills/render",
        {
            "name": skill_name_param,
            "trigger": trigger,
            "variables": variables,
            "skills_path": skills_path_param or None,
        },
    )
    if response.get("_error") or response.get("_status") != 200:
        module.fail_json(**error_result(str(response.get("detail") or response.get("_error") or "Skill render failed")))
        return

    module.exit_json(**ok_result(
        {
            "skill_name": response.get("skill_name", skill_name_param),
            "rendered_body": response.get("rendered_body", ""),
            "required_vars": response.get("required_vars", []),
        },
        changed=False,
    ))


if __name__ == "__main__":
    main()
