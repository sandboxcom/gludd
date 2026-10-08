#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: pipeline_triage
  short_description: Triage a controller-local JUnit report safely
  description:
    - Reduces one bounded JUnit XML report to content-free outcome evidence.
    - Runs only through the paired controller action plugin.
    - Never returns failure text, captured output, or local testcase identities.
  options:
    root:
      description: Absolute no-symlink controller directory containing the report.
      type: path
      required: true
    report_path:
      description: Relative path to one regular JUnit XML report.
      type: path
      required: true

EXAMPLES:
  - name: Triage a controller-local report
    general_ludd.git_release.pipeline_triage:
      root: /srv/gludd/pipeline/receipts
      report_path: junit.xml
    register: pipeline_triage_result

RETURN:
  triage:
    description: Content-free counts, digests, and bounded actionable failures.
    type: dict
    returned: success
"""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule


def main() -> None:
    """Fail closed when Ansible bypasses the controller-side action plugin."""
    module = AnsibleModule(
        argument_spec={
            "root": {"type": "path", "required": True},
            "report_path": {"type": "path", "required": True},
        },
        supports_check_mode=True,
    )
    module.fail_json(
        changed=False,
        msg="pipeline_triage requires its controller-side action plugin",
    )


if __name__ == "__main__":
    main()
