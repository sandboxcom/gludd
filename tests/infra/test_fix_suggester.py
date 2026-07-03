"""Tests for the SLM fix-suggester (SLICE C).

Covers the gateway-wired ``make_fix_suggestion_fn`` (spying on ``call_model``
kwargs, fail-soft on raise / non-JSON) and :class:`FixSuggester`'s guaranteed
deterministic fallback to ``remediate()``.
"""

from __future__ import annotations

from typing import Any

from general_ludd.infra.fix_suggester import FixSuggester, make_fix_suggestion_fn
from general_ludd.infra.model_deploy_check import Finding, MisconfigDetector

# A vLLM deployment that trips rule "a" (gpu_memory_utilization > 0.95, critical).
# Its remediate() patch is a {"gpu_memory_utilization": ...} overlay.
DEPLOYMENT = {"engine": "vllm", "gpu_memory_utilization": 0.99}


class _StubResp:
    def __init__(self, content: str) -> None:
        self.content = content


class _SpyGateway:
    """Records the last call_model kwargs and returns a canned response."""

    def __init__(self, *, content: str | None = None, raises: bool = False) -> None:
        self._content = content
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    def call_model(self, profile_id: str, **kwargs: Any) -> _StubResp:
        self.calls.append({"profile_id": profile_id, **kwargs})
        if self._raises:
            raise RuntimeError("gateway boom")
        return _StubResp(self._content or "")


def _rule_a_findings() -> list[Finding]:
    findings = MisconfigDetector().check(DEPLOYMENT)
    assert any(f.rule_id == "a" and f.severity == "critical" for f in findings), findings
    return findings


def test_make_fix_suggestion_fn_returns_parsed_dict_and_forwards_kwargs() -> None:
    gw = _SpyGateway(content='{"gpu_memory_utilization":0.9}')
    fn = make_fix_suggestion_fn(gw, "compactor", max_output_tokens=256)
    findings = _rule_a_findings()

    out = fn(DEPLOYMENT, findings)

    assert out == {"gpu_memory_utilization": 0.9}
    assert len(gw.calls) == 1
    call = gw.calls[0]
    assert call["profile_id"] == "compactor"
    assert call["work_type"] == "misconfig_fix"
    assert call["requested_max_output_tokens"] == 256
    assert "guided_json" in call
    assert isinstance(call["messages"], list)


def test_make_fix_suggestion_fn_fails_soft_on_gateway_raise() -> None:
    gw = _SpyGateway(raises=True)
    fn = make_fix_suggestion_fn(gw)
    # NEVER raises; returns {} so the caller's deterministic fallback engages.
    assert fn(DEPLOYMENT, _rule_a_findings()) == {}


def test_make_fix_suggestion_fn_fails_soft_on_non_json() -> None:
    gw = _SpyGateway(content="not json at all {")
    fn = make_fix_suggestion_fn(gw)
    assert fn(DEPLOYMENT, _rule_a_findings()) == {}


def test_make_fix_suggestion_fn_ignores_non_dict_json() -> None:
    gw = _SpyGateway(content="[1, 2, 3]")
    fn = make_fix_suggestion_fn(gw)
    assert fn(DEPLOYMENT, _rule_a_findings()) == {}


def test_suggester_uses_slm_output_when_present() -> None:
    detector = MisconfigDetector()

    def suggest_fn(deployment: dict[str, Any], findings: list[Finding]) -> dict[str, Any]:
        return {"gpu_memory_utilization": 0.88, "extra_slm_key": True}

    suggester = FixSuggester(detector, suggest_fn)
    out = suggester.suggest(DEPLOYMENT, _rule_a_findings())
    assert out == {"gpu_memory_utilization": 0.88, "extra_slm_key": True}


def test_suggester_falls_back_to_remediate_on_gateway_raise() -> None:
    detector = MisconfigDetector()
    gw = _SpyGateway(raises=True)
    fn = make_fix_suggestion_fn(gw)
    suggester = FixSuggester(detector, fn)

    findings = _rule_a_findings()
    out = suggester.suggest(DEPLOYMENT, findings)

    # SLM returned {}, so we get the deterministic merge of remediate() patches.
    assert "gpu_memory_utilization" in out
    # matches the detector's own remediation for rule "a" critical.
    expected = {}
    for f in findings:
        expected.update(detector.remediate(f)["config_patch"])
    assert out == expected


def test_suggester_falls_back_on_non_json_garbage() -> None:
    detector = MisconfigDetector()
    gw = _SpyGateway(content="garbage-not-json")
    suggester = FixSuggester(detector, make_fix_suggestion_fn(gw))

    findings = _rule_a_findings()
    out = suggester.suggest(DEPLOYMENT, findings)
    assert "gpu_memory_utilization" in out


def test_suggester_falls_back_when_no_suggest_fn() -> None:
    detector = MisconfigDetector()
    suggester = FixSuggester(detector, None)
    out = suggester.suggest(DEPLOYMENT, _rule_a_findings())
    assert "gpu_memory_utilization" in out
