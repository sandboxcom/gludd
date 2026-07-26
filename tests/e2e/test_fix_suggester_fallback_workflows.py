"""E2E coverage for deterministic fix-suggester fallback behavior.

These workflows exercise the public SLM wiring with completely deterministic
gateway doubles. No model process or network access is required: empty,
``None``, and raising gateway responses must all produce the detector's
remediation patch.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.infra.fix_suggester import FixSuggester, make_fix_suggestion_fn
from general_ludd.infra.model_deploy_check import Finding, MisconfigDetector

DEPLOYMENT = {"engine": "vllm", "gpu_memory_utilization": 0.99}


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content


class _Gateway:
    def __init__(self, behavior: str) -> None:
        self.behavior = behavior
        self.calls = 0

    def call_model(self, _profile: str, **_kwargs: Any) -> _Response | None:
        self.calls += 1
        if self.behavior == "raise":
            raise RuntimeError("deterministic gateway failure")
        if self.behavior == "none":
            return None
        return _Response("")


def _findings() -> list[Finding]:
    findings = MisconfigDetector().check(DEPLOYMENT)
    assert any(f.rule_id == "a" for f in findings), findings
    return findings


def _expected_patch(findings: list[Finding]) -> dict[str, Any]:
    detector = MisconfigDetector()
    expected: dict[str, Any] = {}
    for finding in findings:
        expected.update(detector.remediate(finding)["config_patch"])
    return expected


@pytest.mark.parametrize("behavior", ["empty", "none", "raise"])
def test_gateway_empty_none_or_raise_uses_deterministic_patch(behavior: str) -> None:
    """Every fail-soft gateway result must preserve a usable remediation."""
    gateway = _Gateway(behavior)
    findings = _findings()
    suggester = FixSuggester(
        MisconfigDetector(), make_fix_suggestion_fn(gateway)
    )

    result = suggester.suggest(DEPLOYMENT, findings)

    assert gateway.calls == 1
    assert result == _expected_patch(findings)
    assert result["gpu_memory_utilization"] < DEPLOYMENT["gpu_memory_utilization"]


def test_suggest_fn_raise_uses_deterministic_patch() -> None:
    """The outer suggester also remains fail-soft for injected callables."""
    findings = _findings()

    def _raise(_deployment: dict[str, Any], _findings: list[Finding]) -> dict[str, Any]:
        raise RuntimeError("suggestion callable failure")

    result = FixSuggester(MisconfigDetector(), _raise).suggest(DEPLOYMENT, findings)

    assert result == _expected_patch(findings)
