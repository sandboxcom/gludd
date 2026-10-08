"""Fail-closed contracts for collection-native chemical lot admission."""

from __future__ import annotations

import builtins
import math
import runpy
import socket
import subprocess
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from ansible.module_utils import basic as ansible_basic
from ansible_collections.general_ludd.chemistry.plugins.module_utils import (
    lot_admission,
)
from ansible_collections.general_ludd.chemistry.plugins.modules import (
    chemical_lot_admission,
)

from general_ludd.chemistry import inventory as core_inventory


class _Exit(Exception):
    """Capture an Ansible success exit."""


class _Failure(Exception):
    """Capture an Ansible validation failure."""


class _FakeModule:
    def __init__(self, params: dict[str, Any], *, check_mode: bool = False) -> None:
        self.params = params
        self.check_mode = check_mode
        self.result: dict[str, Any] | None = None
        self.failure: dict[str, Any] | None = None

    def exit_json(self, **kwargs: Any) -> None:
        self.result = kwargs
        raise _Exit

    def fail_json(self, **kwargs: Any) -> None:
        self.failure = kwargs
        raise _Failure


def _params(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "lot": "LOT-2026-0042",
        "purity": 0.999,
        "expiry": "2027-06-30",
        "restrictions": [],
        "required_purity": 0.995,
        "as_of": "2026-10-08",
    }
    params.update(overrides)
    return params


@pytest.mark.parametrize(
    ("overrides", "reason_code"),
    [
        ({"expiry": "2026-10-07"}, "lot_expired"),
        ({"restrictions": ["quarantine"]}, "lot_restricted"),
        ({"purity": 0.99}, "lot_purity_insufficient"),
    ],
)
def test_expired_restricted_and_wrong_purity_lots_are_rejected(
    overrides: dict[str, Any],
    reason_code: str,
) -> None:
    result = lot_admission.evaluate_lot_admission(**_params(**overrides))

    assert result == {
        "schema_version": "1.0",
        "lot": "LOT-2026-0042",
        "admitted": False,
        "suitable": False,
        "requires_review": True,
        "reason_codes": [reason_code],
        "reasons": [{"code": reason_code}],
    }
    assert not ({"replacement", "substitution", "substituted_lot"} & result.keys())


def test_all_rejections_are_bounded_in_canonical_order() -> None:
    result = lot_admission.evaluate_lot_admission(
        **_params(
            expiry="2020-01-01",
            restrictions=["quarantine", "regulated-use"],
            purity=0.25,
        )
    )

    assert result["reason_codes"] == [
        "lot_expired",
        "lot_restricted",
        "lot_purity_insufficient",
    ]
    assert len(result["reason_codes"]) <= len(lot_admission.REASON_CODES) == 3
    assert all(
        reason == {"code": code}
        for reason, code in zip(
            result["reasons"], result["reason_codes"], strict=True
        )
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"lot": ""}, "lot must be a non-empty"),
        ({"lot": "x" * 129}, "lot must be at most 128"),
        ({"restrictions": ["x" * 129]}, "restriction must be at most 128"),
        ({"restrictions": ["x"] * 33}, "at most 32 restrictions"),
        ({"restrictions": "quarantine"}, "restrictions must be a list"),
        ({"purity": math.nan}, "purity must be finite"),
        ({"purity": math.inf}, "purity must be finite"),
        ({"purity": -0.01}, "purity must be between 0 and 1"),
        ({"purity": 1.01}, "purity must be between 0 and 1"),
        ({"required_purity": True}, "required_purity must be a finite number"),
        ({"expiry": "2026-02-29"}, "expiry must be an ISO date"),
        ({"expiry": "2026-1-01"}, "expiry must be an ISO date"),
        ({"as_of": "2026-01-01T00:00:00"}, "as_of must be an ISO date"),
    ],
)
def test_malformed_or_unbounded_lot_input_fails_closed(
    overrides: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(lot_admission.LotAdmissionError, match=message):
        lot_admission.evaluate_lot_admission(**_params(**overrides))


def test_exact_bounds_and_expiry_day_are_admitted_without_normalization() -> None:
    result = lot_admission.evaluate_lot_admission(
        lot="L" * 128,
        purity=1,
        expiry="2026-10-08",
        restrictions=[],
        required_purity=1.0,
        as_of="2026-10-08",
    )

    assert result["admitted"] is True
    assert result["reason_codes"] == []
    assert result["lot"] == "L" * 128


def test_inventory_record_and_core_exports_share_one_collection_implementation() -> None:
    assert core_inventory.InventoryRecord is lot_admission.InventoryRecord
    assert core_inventory.check_lot_suitability is lot_admission.check_lot_suitability
    assert core_inventory.evaluate_lot_admission is lot_admission.evaluate_lot_admission

    record = core_inventory.InventoryRecord(
        lot="LOT-CORE",
        purity=0.95,
        location="cabinet-a",
        expiry="2026-01-01",
        restrictions=["quarantine"],
        chain_of_custody=[{"actor": "reviewer", "action": "quarantine"}],
    )
    result = core_inventory.check_lot_suitability(record, 0.99, "2026-10-08")
    assert result["reason_codes"] == [
        "lot_expired",
        "lot_restricted",
        "lot_purity_insufficient",
    ]

    mapped = core_inventory.check_lot_suitability(
        {
            "lot": "LOT-MAPPING",
            "purity": 1.0,
            "expiry": "2027-01-01",
            "restrictions": [],
        },
        1.0,
        "2026-10-08",
    )
    assert mapped["admitted"] is True


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"location": 42}, "location must be a string"),
        ({"chain_of_custody": "rewritten"}, "chain_of_custody must be a list"),
    ],
)
def test_inventory_record_rejects_malformed_compatibility_fields(
    overrides: dict[str, Any],
    message: str,
) -> None:
    values: dict[str, Any] = {
        "lot": "LOT-COMPAT",
        "purity": 1.0,
        "location": "cabinet-a",
        "expiry": "2027-01-01",
    }
    values.update(overrides)
    with pytest.raises(lot_admission.LotAdmissionError, match=message):
        lot_admission.InventoryRecord(**values)


def test_compatibility_wrapper_rejects_non_record_input() -> None:
    with pytest.raises(lot_admission.LotAdmissionError, match="record must be"):
        lot_admission.check_lot_suitability(
            cast(Any, "LOT-NOT-A-RECORD"), 1.0, "2026-10-08"
        )


def _deny_io(name: str) -> Callable[..., Any]:
    def denied(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(f"unexpected {name}")

    return denied


def test_native_evaluator_performs_no_network_file_or_subprocess_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builtins, "open", _deny_io("file open"))
    monkeypatch.setattr(socket, "socket", _deny_io("socket"))
    monkeypatch.setattr(subprocess, "run", _deny_io("subprocess"))
    monkeypatch.setattr(urllib.request, "urlopen", _deny_io("HTTP"))

    assert lot_admission.evaluate_lot_admission(**_params())["admitted"] is True


@pytest.mark.parametrize("check_mode", [False, True])
def test_ansible_module_is_read_only_in_normal_and_check_mode(check_mode: bool) -> None:
    module = _FakeModule(_params(), check_mode=check_mode)

    with pytest.raises(_Exit):
        chemical_lot_admission.run(module)

    assert module.failure is None
    assert module.result is not None
    assert module.result["changed"] is False
    assert module.result["result"]["admitted"] is True


def test_ansible_module_reports_bounded_validation_failure() -> None:
    module = _FakeModule(_params(expiry="yesterday"))

    with pytest.raises(_Failure):
        chemical_lot_admission.run(module)

    assert module.result is None
    assert module.failure == {
        "changed": False,
        "msg": "expiry must be an ISO date in YYYY-MM-DD form",
    }


def test_ansible_script_entrypoint_declares_check_mode_and_narrow_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _FakeModule(_params())
    captured: dict[str, Any] = {}

    def factory(**kwargs: Any) -> _FakeModule:
        captured.update(kwargs)
        return module

    monkeypatch.setattr(ansible_basic, "AnsibleModule", factory)
    module_path = Path(chemical_lot_admission.__file__)
    with pytest.raises(_Exit):
        runpy.run_path(str(module_path), run_name="__main__")

    assert captured["supports_check_mode"] is True
    assert set(captured["argument_spec"]) == set(_params())
    assert module.result is not None
    assert module.result["changed"] is False
