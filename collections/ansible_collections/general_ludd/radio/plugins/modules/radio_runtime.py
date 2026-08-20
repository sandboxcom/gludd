#!/usr/bin/python
"""Run radio role operations inside Ansible's managed Python boundary."""

from __future__ import annotations

import json
from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.radio.plugins.module_utils.exam_quiz_runtime import (
    _render_md,
    _render_text,
    grade_answers,
    load_questions,
)
from ansible_collections.general_ludd.radio.plugins.module_utils.link_budget_runtime import (
    compute_link_budget,
)
from ansible_collections.general_ludd.radio.plugins.module_utils.propagation_runtime import (
    compute_path_loss,
)
from ansible_collections.general_ludd.radio.plugins.module_utils.regulation_lookup_runtime import (
    lookup,
)
from ansible_collections.general_ludd.radio.plugins.module_utils.signal_identify_runtime import (
    signal_identify,
)

_OPERATIONS = (
    "propagation_model",
    "link_budget",
    "exam_quiz",
    "signal_identify",
    "regulation_lookup",
)

_PARAM_OPTIONS: dict[str, dict[str, Any]] = {
    "model": {"type": "str"},
    "freq_hz": {"type": "int"},
    "distance_m": {"type": "float"},
    "tx_height_m": {"type": "float"},
    "rx_height_m": {"type": "float"},
    "terrain_irregularity_m": {"type": "float"},
    "climate": {"type": "int"},
    "refractivity": {"type": "float"},
    "permittivity": {"type": "float"},
    "conductivity": {"type": "float"},
    "polarization": {"type": "str", "choices": ["horizontal", "vertical"]},
    "rain_rate_mmh": {"type": "float"},
    "tx_power_dbm": {"type": "float"},
    "tx_antenna_type": {"type": "str"},
    "tx_antenna_gain_dbi": {"type": "float"},
    "tx_line_loss_db": {"type": "float"},
    "rx_antenna_type": {"type": "str"},
    "rx_antenna_gain_dbi": {"type": "float"},
    "rx_line_loss_db": {"type": "float"},
    "rx_sensitivity_dbm": {"type": "float"},
    "required_snr_db": {"type": "float"},
    "rain_enabled": {"type": "bool"},
    "rain_polarization": {"type": "str", "choices": ["horizontal", "vertical"]},
    "exam": {"type": "str"},
    "count": {"type": "int"},
    "seed": {"type": "int"},
    "answers": {"type": "dict"},
    "format": {"type": "str", "choices": ["json", "text", "md"]},
    "input_file": {"type": "path"},
    "sample_rate": {"type": "int"},
    "center_freq_hz": {"type": "int"},
    "bandwidth_hz": {"type": "float"},
    "symbol_rate_baud": {"type": "float"},
    "spectrum_shape": {"type": "str"},
    "threshold_db": {"type": "float"},
    "method": {"type": "str", "choices": ["fft", "cyclostationary", "auto"]},
    "country": {"type": "str"},
    "freq_mhz": {"type": "float"},
    "band_name": {"type": "str"},
    "license_class": {"type": "str"},
    "marine_channel": {"type": "int"},
}

ARGUMENT_SPEC: dict[str, dict[str, Any]] = {
    "operation": {"type": "str", "required": True, "choices": list(_OPERATIONS)},
    "params": {"type": "dict", "required": True, "options": _PARAM_OPTIONS},
}


def _required(params: dict[str, Any], name: str) -> Any:
    value = params.get(name)
    if value is None:
        raise ValueError(f"{name} is required")
    return value


def _artifact(result: dict[str, Any], filename: str, content: str | None = None) -> dict[str, Any]:
    return {
        "result": result,
        "artifact_filename": filename,
        "artifact_content": content or json.dumps(result, indent=2, default=str),
    }


def _dispatch_propagation(params: dict[str, Any]) -> dict[str, Any]:
    model = str(_required(params, "model"))
    extras = {
        key: params[key]
        for key in (
            "terrain_irregularity_m",
            "climate",
            "refractivity",
            "permittivity",
            "conductivity",
            "polarization",
            "rain_rate_mmh",
        )
        if params.get(key) is not None
    }
    result = compute_path_loss(
        model=model,
        freq_hz=int(_required(params, "freq_hz")),
        distance_m=float(_required(params, "distance_m")),
        tx_height_m=float(params.get("tx_height_m", 30.0)),
        rx_height_m=float(params.get("rx_height_m", 1.5)),
        **extras,
    ).to_dict()
    return _artifact(result, "propagation_model.json")


def _dispatch_link_budget(params: dict[str, Any]) -> dict[str, Any]:
    result = compute_link_budget(
        tx_power_dbm=float(_required(params, "tx_power_dbm")),
        freq_hz=int(_required(params, "freq_hz")),
        distance_m=float(_required(params, "distance_m")),
        model=str(params.get("model", "free_space")),
        tx_antenna_type=params.get("tx_antenna_type", "dipole_half_wave"),
        tx_antenna_gain_dbi=float(params.get("tx_antenna_gain_dbi", 2.15)),
        tx_line_loss_db=float(params.get("tx_line_loss_db", 1.0)),
        tx_height_m=float(params.get("tx_height_m", 30.0)),
        rx_antenna_type=params.get("rx_antenna_type", "dipole_half_wave"),
        rx_antenna_gain_dbi=float(params.get("rx_antenna_gain_dbi", 2.15)),
        rx_line_loss_db=float(params.get("rx_line_loss_db", 1.0)),
        rx_height_m=float(params.get("rx_height_m", 1.5)),
        rx_sensitivity_dbm=float(params.get("rx_sensitivity_dbm", -120.0)),
        required_snr_db=float(params.get("required_snr_db", 10.0)),
        rain_enabled=bool(params.get("rain_enabled", False)),
        rain_rate_mmh=float(params.get("rain_rate_mmh", 5.0)),
        rain_polarization=str(params.get("rain_polarization", "horizontal")),
    )
    return _artifact(result, "link_budget.json")


def _dispatch_exam(params: dict[str, Any]) -> dict[str, Any]:
    exam = str(_required(params, "exam"))
    count = int(params.get("count", 10))
    seed = params.get("seed")
    answers = params.get("answers") or {}
    verdict = (
        grade_answers(exam, answers, count=count, seed=seed)
        if answers
        else load_questions(exam, count, seed=seed)
    )
    result = verdict.to_dict()
    output_format = str(params.get("format", "json"))
    if output_format == "text":
        return _artifact(result, "exam_quiz.txt", _render_text(result))
    if output_format == "md":
        return _artifact(result, "exam_quiz.md", _render_md(result))
    return _artifact(result, "exam_quiz.json")


def _dispatch_signal(params: dict[str, Any]) -> dict[str, Any]:
    result = signal_identify(
        input_file=params.get("input_file"),
        sample_rate=int(params.get("sample_rate", 2_048_000)),
        center_freq_hz=int(params.get("center_freq_hz", 100_000_000)),
        bandwidth_hz=params.get("bandwidth_hz"),
        symbol_rate_baud=params.get("symbol_rate_baud"),
        spectrum_shape=str(params.get("spectrum_shape", "")),
        threshold_db=float(params.get("threshold_db", 10.0)),
        method=str(params.get("method", "fft")),
    )
    return _artifact(result, "signal_identify.json")


def _dispatch_regulation(params: dict[str, Any]) -> dict[str, Any]:
    result = lookup(
        country=str(_required(params, "country")).upper(),
        freq_mhz=params.get("freq_mhz"),
        band_name=params.get("band_name"),
        license_class=params.get("license_class"),
        marine_channel=params.get("marine_channel"),
    ).to_dict()
    return _artifact(result, "regulation_lookup.json")


_DISPATCHERS = {
    "propagation_model": _dispatch_propagation,
    "link_budget": _dispatch_link_budget,
    "exam_quiz": _dispatch_exam,
    "signal_identify": _dispatch_signal,
    "regulation_lookup": _dispatch_regulation,
}


def dispatch(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one validated operation without mutating its artifact path."""
    dispatcher = _DISPATCHERS.get(operation)
    if dispatcher is None:
        raise ValueError(f"Unsupported radio operation: {operation}")
    return dispatcher(params)


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
