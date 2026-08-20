#!/usr/bin/python
"""Parse TLC counterexamples through packaged formal collection code."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.formal.plugins.module_utils.tla_trace import (
    parse_tlc_trace,
)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def run(module: Any) -> None:
    """Execute trace parsing with check-mode and idempotence semantics."""
    raw = module.params["tlc_output"]
    trace_path = module.params["trace_path"]
    output_path = Path(module.params["output_path"])
    if trace_path:
        try:
            raw = Path(trace_path).read_text(encoding="utf-8")
        except OSError as exc:
            module.fail_json(msg=f"unable to read TLC trace: {exc}")
            return
    if not raw:
        module.fail_json(msg="tlc_output or trace_path is required")
        return
    result = parse_tlc_trace(raw)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    previous = output_path.read_text(encoding="utf-8") if output_path.is_file() else None
    if not module.check_mode:
        try:
            _atomic_write(output_path, rendered)
        except OSError as exc:
            module.fail_json(msg=f"unable to publish TLC trace: {exc}")
            return
    module.exit_json(
        changed=not module.check_mode and previous != rendered,
        trace=result,
        output_path=str(output_path),
    )


def main() -> None:
    """Construct the Ansible argument contract and execute it."""
    module = AnsibleModule(
        argument_spec={
            "tlc_output": {"type": "str", "default": ""},
            "trace_path": {"type": "path", "default": ""},
            "output_path": {"type": "path", "required": True},
        },
        mutually_exclusive=(("tlc_output", "trace_path"),),
        required_one_of=(("tlc_output", "trace_path"),),
        supports_check_mode=True,
    )
    run(module)


if __name__ == "__main__":
    main()
