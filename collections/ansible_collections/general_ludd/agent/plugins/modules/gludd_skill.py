#!/usr/bin/python
# -*- coding: utf-8 -*-
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

import os

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]

try:
    from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
        error_result,
        ok_result,
    )
except ImportError:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "module_utils"))
    from gludd import error_result, ok_result  # type: ignore[import]


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            name=dict(type="str", default=None),
            trigger=dict(type="str", default=None),
            variables=dict(type="dict", default={}),
            skills_path=dict(type="str", default=""),
        ),
        mutually_exclusive=[["name", "trigger"]],
        required_one_of=[["name", "trigger"]],
        supports_check_mode=True,
    )

    skill_name_param: str | None = module.params["name"]
    trigger: str | None = module.params["trigger"]
    variables: dict = module.params["variables"] or {}
    skills_path_param: str = module.params["skills_path"]

    try:
        from general_ludd.skills.loader import discover_skills  # type: ignore[import]
        from general_ludd.skills.renderer import render_skill  # type: ignore[import]
    except ImportError as exc:
        module.fail_json(**error_result(f"general_ludd not importable: {exc}"))
        return

    # Build search paths
    search_paths: list[str] = []
    if skills_path_param:
        search_paths.append(skills_path_param)
    # Common fallbacks
    for candidate in [
        os.path.join(os.getcwd(), ".opencode", "skills"),
        os.path.expanduser("~/.config/general_ludd/skills"),
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "..", ".opencode", "skills"),
    ]:
        if os.path.isdir(candidate) and candidate not in search_paths:
            search_paths.append(candidate)

    skills = discover_skills(*search_paths)

    if not skills:
        module.fail_json(**error_result("No skills found in search paths", search_paths=search_paths))
        return

    # Resolve the skill
    found = None
    if skill_name_param:
        for s in skills:
            if s.name == skill_name_param:
                found = s
                break
        if found is None:
            module.fail_json(**error_result(
                f"Skill not found: {skill_name_param}",
                available=[s.name for s in skills],
            ))
            return
    else:  # trigger
        import re
        for s in skills:
            for pattern in s.trigger_patterns:
                if re.search(pattern, trigger or "", re.IGNORECASE):
                    found = s
                    break
            if found:
                break
        if found is None:
            module.fail_json(**error_result(
                f"No skill matched trigger: {trigger!r}",
                available=[s.name for s in skills],
            ))
            return

    # Render — render_skill raises on missing StrictUndefined vars
    try:
        rendered = render_skill(found.body, variables)
    except Exception as exc:  # noqa: BLE001
        module.fail_json(**error_result(f"Skill render failed: {exc}", skill_name=found.name))
        return

    module.exit_json(**ok_result(
        {
            "skill_name": found.name,
            "rendered_body": rendered,
            "required_vars": getattr(found, "required_vars", []),
        },
        changed=False,
    ))


if __name__ == "__main__":
    main()
