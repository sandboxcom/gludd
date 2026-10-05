#!/usr/bin/python
"""Analyze local logs through packaged operations collection code."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.operations.plugins.module_utils.log_analyzer import (
    analyze,
)


def run(module: Any) -> None:
    """Execute bounded log analysis and preserve idempotent Ansible semantics."""
    output_dir = Path(module.params["output_dir"])
    report_path = output_dir / "log_analysis_result.json"
    previous = report_path.read_text(encoding="utf-8") if report_path.is_file() else None
    if module.check_mode:
        module.exit_json(changed=False, result={}, artifacts=[])
        return
    try:
        result = analyze(
            module.params["log_dir"],
            module.params["glob_pattern"],
            str(output_dir),
            error_threshold=module.params["error_threshold"],
            cluster_window=module.params["cluster_window"],
            min_cluster_size=module.params["min_cluster_size"],
            max_files=module.params["max_files"],
            max_bytes_per_file=module.params["max_bytes_per_file"],
        )
    except (OSError, ValueError) as exc:
        module.fail_json(msg=f"log analysis failed: {exc}")
        return
    current = json.dumps(result, indent=2, sort_keys=True) + "\n"
    module.exit_json(
        changed=previous != current,
        result=result,
        artifacts=[
            str(report_path),
            str(output_dir / "log_analysis_report.md"),
        ],
    )


def main() -> None:
    """Construct the Ansible argument contract and execute it."""
    module = AnsibleModule(
        argument_spec={
            "log_dir": {"type": "path", "required": True},
            "glob_pattern": {"type": "str", "default": "*.log"},
            "output_dir": {"type": "path", "required": True},
            "error_threshold": {"type": "float", "default": 0.1},
            "cluster_window": {"type": "int", "default": 300},
            "min_cluster_size": {"type": "int", "default": 2},
            "max_files": {"type": "int", "default": 1000},
            "max_bytes_per_file": {"type": "int", "default": 10_000_000},
        },
        supports_check_mode=True,
    )
    run(module)


if __name__ == "__main__":
    main()
