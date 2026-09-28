"""Adversarial contracts for task-general runtime adapter seams."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from general_ludd.execution.adapter_protocols import (
    CallableAdapterProtocol,
    ObservableRunnerProtocol,
    require_runtime_adapter,
)


@dataclass(frozen=True)
class _Result:
    domain: str


class _DomainRunner:
    def __init__(self, domain: str) -> None:
        self.domain = domain

    def run_observable(
        self,
        operation: str,
        parameters: dict[str, str],
        *,
        timeout: float,
    ) -> _Result:
        del operation, parameters, timeout
        return _Result(self.domain)


class _DomainEvaluator:
    def __init__(self, domain: str) -> None:
        self.domain = domain

    def __call__(self, payload: object) -> _Result:
        del payload
        return _Result(self.domain)


@pytest.mark.parametrize("domain", ("chemistry", "code", "hardware", "config"))
def test_core_adapter_protocols_accept_unrelated_task_domains(domain: str) -> None:
    """One core-owned shape must admit capabilities without policy vocabulary."""
    runner = _DomainRunner(domain)
    evaluator = _DomainEvaluator(domain)

    checked_runner = require_runtime_adapter(
        runner,
        ObservableRunnerProtocol,
        boundary="observable runner",
    )
    checked_evaluator = require_runtime_adapter(
        evaluator,
        CallableAdapterProtocol,
        boundary="attempt evaluator",
    )

    assert checked_runner.run_observable("evaluate", {}, timeout=1.0).domain == domain
    assert checked_evaluator(object()).domain == domain


@pytest.mark.parametrize(
    ("candidate", "protocol", "boundary"),
    (
        (object(), ObservableRunnerProtocol, "observable runner"),
        (object(), CallableAdapterProtocol, "attempt evaluator"),
    ),
)
def test_core_adapter_validation_rejects_missing_methods_before_use(
    candidate: object,
    protocol: type[object],
    boundary: str,
) -> None:
    """Runtime admission fails with a stable boundary name, not AttributeError."""
    with pytest.raises(TypeError, match=f"^{boundary} does not satisfy its runtime protocol$"):
        require_runtime_adapter(candidate, protocol, boundary=boundary)


def test_core_protocols_do_not_claim_data_member_validation() -> None:
    """The task-general runtime seams remain strictly method-only."""
    assert getattr(ObservableRunnerProtocol, "_is_runtime_protocol", False)
    assert getattr(CallableAdapterProtocol, "_is_runtime_protocol", False)
    assert getattr(ObservableRunnerProtocol, "__protocol_attrs__", None) == frozenset(
        {"run_observable"}
    )
    assert getattr(CallableAdapterProtocol, "__protocol_attrs__", None) == frozenset(
        {"__call__"}
    )


@pytest.mark.parametrize(
    ("boundary", "protocol", "message"),
    (
        (" ", ObservableRunnerProtocol, "runtime adapter boundary must be non-empty"),
        ("runner", object(), "runtime adapter protocol must be a class"),
    ),
)
def test_core_adapter_validation_rejects_invalid_validation_contract(
    boundary: str,
    protocol: object,
    message: str,
) -> None:
    """The validator itself fails closed on malformed admission metadata."""
    with pytest.raises((TypeError, ValueError), match=message):
        require_runtime_adapter(_DomainRunner("code"), protocol, boundary=boundary)
