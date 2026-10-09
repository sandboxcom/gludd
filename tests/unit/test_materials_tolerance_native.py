"""Collection-native, fail-closed materials tolerance analysis contracts."""

from __future__ import annotations

import builtins
import json
import math
import runpy
import socket
import subprocess
import urllib.request
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from ansible_collections.general_ludd.materials.plugins.action import tolerance_model
from ansible_collections.general_ludd.materials.plugins.action.tolerance_model import (
    ActionModule,
)
from ansible_collections.general_ludd.materials.plugins.module_utils import (
    tolerance_model as runtime,
)
from ansible_collections.general_ludd.materials.plugins.modules import (
    tolerance_model as module_stub,
)
from scripts.check_ansible_executable_stubs import scan_collection_tree

from general_ludd.materials import operations
from general_ludd.materials import tolerance as core_tolerance

ROOT = Path(__file__).resolve().parents[2]
COLLECTION = ROOT / "collections/ansible_collections/general_ludd/materials"


def test_native_tolerance_action_computes_stack_without_daemon() -> None:
    result = tolerance_model.execute_action(
        {
            "operation": "worst_case",
            "request": {
                "dims": [[10.0, 0.1], [5.0, 0.2]],
                "unit": "mm",
            },
        }
    )

    assert result == {
        "changed": False,
        "result": {
            "state": "ok",
            "nominal": 15.0,
            "band": 0.30000000000000004,
            "upper": 15.3,
            "lower": 14.7,
            "unit": "mm",
            "equation_id": "worst-case: band = sum(|t_i|)",
            "inputs": {
                "dims": [
                    {"nominal": 10.0, "tolerance": 0.1, "unit": "mm"},
                    {"nominal": 5.0, "tolerance": 0.2, "unit": "mm"},
                ],
                "unit": "mm",
            },
            "assumptions": [
                "all contributors at their worst-case extreme simultaneously",
                "linear (one-dimensional) chain",
                "bilateral ± tolerances",
            ],
        },
    }
    assert "daemon" not in tolerance_model.__file__


@pytest.mark.parametrize(
    ("operation", "payload", "expected_key"),
    [
        ("worst_case", {"dims": [[1.0, 0.1]], "unit": "mm"}, "band"),
        ("rss", {"dims": [[1.0, 0.1]], "unit": "mm"}, "sigma_band"),
        (
            "thermal",
            {
                "dims": [[10.0, 0.1]],
                "unit": "mm",
                "alpha_per_K": 0.000012,
                "delta_T_K": 20.0,
            },
            "delta",
        ),
        (
            "thermal_compensation",
            {
                "dims": [[10.0, 0.1]],
                "unit": "mm",
                "alpha_per_K": 0.000012,
                "delta_T_K": 20.0,
            },
            "compensation",
        ),
        (
            "process_capability",
            {"spec_lower": 9.8, "spec_upper": 10.2, "sigma": 0.02},
            "Cpk",
        ),
        (
            "assembly",
            {
                "hole_nominal": 10.0,
                "hole_tol": 0.1,
                "shaft_nominal": 9.8,
                "shaft_tol": 0.05,
                "unit": "mm",
            },
            "fit_class",
        ),
    ],
)
def test_six_operation_allowlist_and_check_mode_parity(
    operation: str,
    payload: dict[str, object],
    expected_key: str,
) -> None:
    args = {"operation": operation, "request": payload}
    normal = tolerance_model.execute_action(args, check_mode=False)
    check = tolerance_model.execute_action(args, check_mode=True)

    assert normal == check
    assert normal["changed"] is False
    assert expected_key in normal["result"]
    assert {
        "assembly",
        "process_capability",
        "rss",
        "thermal",
        "thermal_compensation",
        "worst_case",
    } == runtime.TOLERANCE_OPERATIONS


@pytest.mark.parametrize(
    ("operation", "payload", "message"),
    [
        ("monte_carlo", {}, "unsupported tolerance operation"),
        ("rss", {"dims": [], "unit": "mm", "covariance": []}, "covariance"),
        ("rss", {"dims": [], "unit": "mm", "correlation": 0.0}, "correlation"),
        ("rss", {"dims": [], "unit": "mm", "uncertainty": 0.1}, "unsupported"),
        ("worst_case", {"dims": [[1.0]], "unit": "mm"}, "nominal and tolerance"),
        ("worst_case", {"dims": "1, 0.1", "unit": "mm"}, "dims must be a list"),
        ("worst_case", {"dims": [[math.nan, 0.1]], "unit": "mm"}, "finite"),
        ("worst_case", {"dims": [[1.0, True]], "unit": "mm"}, "finite"),
        ("worst_case", {"dims": [], "unit": " padded "}, "padding"),
        ("process_capability", {"spec_lower": 0, "spec_upper": 1, "sigma": math.inf}, "finite"),
    ],
)
def test_malformed_unsupported_or_nonfinite_input_fails_closed(
    operation: str,
    payload: object,
    message: str,
) -> None:
    with pytest.raises(runtime.ToleranceModelError, match=message):
        runtime.evaluate_tolerance_model(operation, payload)


def test_dimension_unit_and_payload_hard_limits() -> None:
    exact = runtime.evaluate_tolerance_model(
        "rss",
        {"dims": [[1.0, 0.1]] * 256, "unit": "u" * 32},
    )
    assert len(exact["inputs"]["dims"]) == 256

    with pytest.raises(runtime.ToleranceModelError, match="at most 256"):
        runtime.evaluate_tolerance_model(
            "rss",
            {"dims": [[1.0, 0.1]] * 257, "unit": "mm"},
        )
    with pytest.raises(runtime.ToleranceModelError, match="at most 32"):
        runtime.evaluate_tolerance_model(
            "worst_case",
            {"dims": [], "unit": "u" * 33},
        )
    with pytest.raises(runtime.ToleranceModelError, match="65536-byte"):
        runtime.evaluate_tolerance_model(
            "worst_case",
            {"dims": [], "unit": "mm", "padding": "x" * 65_536},
        )


def test_nonfinite_computed_outputs_fail_closed() -> None:
    with pytest.raises(runtime.ToleranceModelError, match="non-finite output"):
        runtime.evaluate_tolerance_model(
            "thermal",
            {
                "dims": [[1e308, 0.0]],
                "unit": "mm",
                "alpha_per_K": 1e308,
                "delta_T_K": 1e308,
            },
        )


def _deny_io(name: str) -> Callable[..., Any]:
    def denied(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(f"unexpected {name}")

    return denied


def test_native_evaluator_has_no_network_file_process_or_state_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builtins, "open", _deny_io("file open"))
    monkeypatch.setattr(socket, "socket", _deny_io("socket"))
    monkeypatch.setattr(subprocess, "run", _deny_io("subprocess"))
    monkeypatch.setattr(urllib.request, "urlopen", _deny_io("HTTP"))

    result = runtime.evaluate_tolerance_model(
        "rss",
        {"dims": [[2.0, 0.2]], "unit": "mm"},
    )
    assert result["sigma_band"] == 0.2


def test_core_surface_is_identity_reexport_and_service_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert core_tolerance.ToleranceChain is runtime.ToleranceChain
    assert core_tolerance.process_capability is runtime.process_capability
    assert core_tolerance.assess_assembly is runtime.assess_assembly
    assert core_tolerance.evaluate_tolerance_model is runtime.evaluate_tolerance_model

    calls: list[tuple[object, object]] = []

    def evaluate(operation: object, request: object) -> dict[str, object]:
        calls.append((operation, request))
        return {"state": "ok"}

    monkeypatch.setattr(operations, "evaluate_tolerance_model", evaluate)
    assert operations.dispatch_materials_operation(
        "tolerance_model",
        {"analysis": "worst_case", "dims": [[1.0, 0.1]], "unit": "mm"},
    ) == {"state": "ok"}
    assert calls == [
        ("worst_case", {"dims": [[1.0, 0.1]], "unit": "mm"})
    ]


def test_action_rejects_extra_or_missing_arguments_without_echo() -> None:
    with pytest.raises(runtime.ToleranceModelError, match="requires"):
        tolerance_model.execute_action({"operation": "rss"})
    with pytest.raises(runtime.ToleranceModelError, match="requires"):
        tolerance_model.execute_action(
            {"operation": "rss", "request": {}, "daemon_url": "https://bad"}
        )


def test_action_module_returns_bounded_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tolerance_model._ControllerActionBase,
        "run",
        lambda *_args, **_kwargs: {},
    )
    action = object.__new__(ActionModule)
    action._task = SimpleNamespace(
        args={"operation": "rss", "request": {"dims": [], "unit": "mm", "correlation": 1}},
        check_mode=True,
    )

    assert action.run() == {
        "changed": False,
        "failed": True,
        "msg": (
            "covariance and correlation inputs are unsupported; RSS requires "
            "explicitly independent contributors"
        ),
    }


def test_companion_module_is_executable_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    class Module:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["supports_check_mode"] is True
            argument_spec = kwargs["argument_spec"]
            assert isinstance(argument_spec, dict)
            assert set(argument_spec) == {"operation", "request"}

        def fail_json(self, **kwargs: object) -> None:
            captured.append(kwargs)

    monkeypatch.setattr(module_stub, "AnsibleModule", Module)
    module_stub.main()
    monkeypatch.setattr("ansible.module_utils.basic.AnsibleModule", Module)
    runpy.run_path(str(Path(module_stub.__file__)), run_name="__main__")
    assert captured == [
        {
            "changed": False,
            "msg": "tolerance_model requires its controller-side action plugin",
        },
        {
            "changed": False,
            "msg": "tolerance_model requires its controller-side action plugin",
        },
    ]


def test_role_and_unique_molecule_scenario_use_native_action() -> None:
    role = COLLECTION / "roles/tolerance_model"
    defaults = yaml.safe_load((role / "defaults/main.yml").read_text(encoding="utf-8"))
    tasks_text = (role / "tasks/main.yml").read_text(encoding="utf-8")
    tasks = yaml.safe_load(tasks_text)
    scenario = ROOT / "molecule/playbooks/materials_tolerance_model"
    converge = (scenario / "default/converge.yml").read_text(encoding="utf-8")
    verify = (scenario / "default/verify.yml").read_text(encoding="utf-8")

    assert defaults["tolerance_model_enabled"] is False
    assert defaults["tolerance_model_operation"] == ""
    assert len(tasks) == 3
    assert "general_ludd.materials.tolerance_model" in tasks_text
    assert "service_request" not in tasks_text
    assert "ansible.builtin.uri" not in tasks_text
    assert "check_mode: true" in converge
    assert "independent contributors" in verify


def test_native_surface_has_no_transport_or_implicit_uncertainty_dependency() -> None:
    files = (
        Path(tolerance_model.__file__),
        Path(runtime.__file__),
    )
    forbidden = (
        "GluddClient",
        "httpx",
        "requests",
        "socket",
        "subprocess",
        "uncertainties",
        "urlopen",
    )
    for path in files:
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source
    assert scan_collection_tree(COLLECTION) == []


def test_result_is_strict_json_and_below_hard_limit() -> None:
    result = runtime.evaluate_tolerance_model(
        "worst_case",
        {"dims": [[1.0, 0.1]] * 256, "unit": "mm"},
    )
    encoded = json.dumps(result, allow_nan=False, sort_keys=True).encode()
    assert len(encoded) <= runtime.MAX_PAYLOAD_BYTES
