#!/usr/bin/python
"""Run binary_re role operations inside Ansible's managed Python boundary."""

from __future__ import annotations

import json
from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.binary_re.plugins.module_utils.deobfuscate_runtime import (
    deobfuscate_strings,
    detect_cfg_flattening,
    detect_opaque_predicates,
    detect_packing,
)
from ansible_collections.general_ludd.binary_re.plugins.module_utils.fuzz_runtime import (
    run_coverage_guided,
    run_mutation_fuzzing,
    triage_crashes,
)
from ansible_collections.general_ludd.binary_re.plugins.module_utils.prompt_injection_runtime import (
    render_scan,
    run_scan,
    scan_payload,
)

_OPERATIONS = (
    "packing",
    "cfg_flattening",
    "strings",
    "opaque_predicates",
    "coverage_guided",
    "mutation",
    "crash_triage",
    "prompt_injection_scan",
)

_PARAM_OPTIONS: dict[str, dict[str, Any]] = {
    "binary_path": {"type": "path"},
    "key_hint": {"type": "int"},
    "fuzzer": {"type": "str", "choices": ["afl++", "libfuzzer", "honggfuzz"]},
    "fuzzer_path": {"type": "path"},
    "target_binary": {"type": "path"},
    "corpus_dir": {"type": "path"},
    "output_dir": {"type": "path"},
    "timeout_seconds": {"type": "int"},
    "fuzz_dict": {"type": "path"},
    "mutations": {"type": "int"},
    "mutation_ratio": {"type": "float"},
    "crash_dir": {"type": "path"},
    "retention_days": {"type": "int"},
    "target_path": {"type": "path"},
    "input_text": {"type": "str"},
    "output_format": {"type": "str", "choices": ["json", "text"]},
    "min_severity": {
        "type": "str",
        "choices": ["info", "low", "medium", "high", "critical"],
    },
    "scan_obfuscation": {"type": "bool"},
}

ARGUMENT_SPEC: dict[str, dict[str, Any]] = {
    "operation": {"type": "str", "required": True, "choices": list(_OPERATIONS)},
    "params": {"type": "dict", "required": True, "options": _PARAM_OPTIONS},
}


def _required(params: dict[str, Any], name: str) -> Any:
    value = params.get(name)
    if value is None or value == "":
        raise ValueError(f"{name} is required")
    return value


def _artifact(result: dict[str, Any], filename: str, content: str | None = None) -> dict[str, Any]:
    return {
        "result": result,
        "artifact_filename": filename,
        "artifact_content": content or json.dumps(result, indent=2, default=str),
    }


_DEOBFUSCATE_DISPATCH = {
    "packing": ("packing_detection.json", detect_packing),
    "cfg_flattening": ("cfg_flattening.json", detect_cfg_flattening),
    "strings": ("string_deobfuscation.json", deobfuscate_strings),
    "opaque_predicates": ("opaque_predicates.json", detect_opaque_predicates),
}


def _dispatch_deobfuscate(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    filename, function = _DEOBFUSCATE_DISPATCH[operation]
    kwargs = {}
    if operation == "strings" and params.get("key_hint") is not None:
        kwargs["key_hint"] = params["key_hint"]
    binary_path = str(_required(params, "binary_path"))
    result = function(binary_path, **kwargs)
    result["mode"] = operation
    result["binary"] = binary_path
    return _artifact(result, filename)


def _dispatch_fuzz(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    if operation == "coverage_guided":
        result = run_coverage_guided(
            fuzzer=str(params.get("fuzzer", "afl++")),
            fuzzer_path=str(params.get("fuzzer_path", "/usr/local/bin/afl-fuzz")),
            target_binary=str(_required(params, "target_binary")),
            corpus_dir=str(_required(params, "corpus_dir")),
            output_dir=str(_required(params, "output_dir")),
            timeout_seconds=int(params.get("timeout_seconds", 300)),
            fuzz_dict=str(params.get("fuzz_dict", "")),
        )
        filename = "coverage_guided.json"
    elif operation == "mutation":
        result = run_mutation_fuzzing(
            target_binary=str(_required(params, "target_binary")),
            corpus_dir=str(_required(params, "corpus_dir")),
            output_dir=str(_required(params, "output_dir")),
            mutations=int(params.get("mutations", 1000)),
            mutation_ratio=params.get("mutation_ratio"),
        )
        filename = "mutation.json"
    else:
        result = triage_crashes(
            crash_dir=str(_required(params, "crash_dir")),
            retention_days=params.get("retention_days"),
        )
        filename = "crash_triage.json"
    result["mode"] = operation
    return _artifact(result, filename)


def _dispatch_prompt(params: dict[str, Any]) -> dict[str, Any]:
    input_text = str(params.get("input_text", ""))
    report, obfuscation = run_scan(
        file_path="" if input_text else str(params.get("target_path", "")),
        input_text=input_text,
        min_severity=str(params.get("min_severity", "medium")),
        scan_obfuscation=bool(params.get("scan_obfuscation", False)),
    )
    output_format = str(params.get("output_format", "json"))
    result = scan_payload(report, obfuscation)
    return _artifact(
        result,
        "prompt_injection_scan.json",
        render_scan(report, obfuscation, output_format),
    )


def dispatch(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one validated operation without mutating its artifact path."""
    if operation in _DEOBFUSCATE_DISPATCH:
        return _dispatch_deobfuscate(operation, params)
    if operation in {"coverage_guided", "mutation", "crash_triage"}:
        return _dispatch_fuzz(operation, params)
    if operation == "prompt_injection_scan":
        return _dispatch_prompt(params)
    raise ValueError(f"Unsupported binary_re operation: {operation}")


def main() -> None:
    """Ansible module entry point."""
    module = AnsibleModule(argument_spec=ARGUMENT_SPEC, supports_check_mode=True)
    try:
        payload = dispatch(module.params["operation"], module.params["params"])
    except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
        module.fail_json(msg=str(exc))
    module.exit_json(changed=False, **payload)


if __name__ == "__main__":
    main()
