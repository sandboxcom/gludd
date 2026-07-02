"""Wiring tests for opt-in local-SLM context compaction on AgentCapabilities.

The SLM compaction package (``general_ludd.compaction``) was built and unit-tested
in isolation but only used by the self-improvement arena. These tests pin the new
seam that wires it into the live agent context path (``prepare_messages``):

  (a) DEFAULT construction (no gateway / flag off) keeps the plain
      ``ContextCompactor`` — no behavior change for existing call sites.
  (b) With a gateway AND the opt-in flag, ``prepare_messages`` routes the old
      middle through the ``SLMCompactor`` and FAILS SOFT when the gateway raises.

All gateways here are stubs — no network, no model.
"""

from __future__ import annotations

from general_ludd.agents.capabilities import AgentCapabilities
from general_ludd.agents.context import ContextCompactor
from general_ludd.compaction.slm import SLMCompactor


class _StubResp:
    def __init__(self, content: str) -> None:
        self.content = content


class _StubGateway:
    """Records call_model invocations and returns a fixed summary."""

    def __init__(self, content: str = "COMPACT SUMMARY") -> None:
        self.calls: list[tuple] = []
        self._content = content

    def call_model(
        self, profile_id, *, messages=None, requested_max_output_tokens=None, **kwargs
    ):
        self.calls.append((profile_id, messages, requested_max_output_tokens))
        return _StubResp(self._content)


class _RaisingGateway:
    """A gateway whose call_model always raises — exercises the fail-soft path."""

    def __init__(self) -> None:
        self.calls = 0

    def call_model(
        self, profile_id, *, messages=None, requested_max_output_tokens=None, **kwargs
    ):
        self.calls += 1
        raise RuntimeError("gateway boom")


def _oversized_history() -> list[dict[str, str]]:
    return [
        {"role": "user", "content": "old message one " * 20},
        {"role": "assistant", "content": "old reply two " * 20},
        {"role": "user", "content": "recent question"},
    ]


# --------------------------------------------------------------------------- #
# (a) Default: no gateway -> plain ContextCompactor, no behavior change.
# --------------------------------------------------------------------------- #


def test_default_keeps_plain_compactor():
    caps = AgentCapabilities()
    assert isinstance(caps.compactor, ContextCompactor)
    assert caps._slm_compactor is None


def test_flag_on_without_gateway_stays_plain():
    # The flag alone must not build an SLM compactor — a gateway is required.
    caps = AgentCapabilities(use_slm_compaction=True)
    assert caps._slm_compactor is None


def test_gateway_without_flag_stays_plain():
    caps = AgentCapabilities(model_gateway=_StubGateway())
    assert caps._slm_compactor is None


def test_default_prepare_messages_unchanged():
    # Below threshold -> history passes through untouched (no summary marker),
    # identical to the pre-wiring behavior.
    caps = AgentCapabilities(max_tokens=100000)
    out = caps.prepare_messages("system prompt", _oversized_history())
    assert not any("prior context" in m["content"] for m in out)
    assert any(m["content"] == "recent question" for m in out)


def test_default_oversized_uses_context_compactor_marker():
    # Over threshold, the DEFAULT path uses ContextCompactor's "[prior context]"
    # marker (NOT the SLM "[prior context — compacted for goal]" marker).
    caps = AgentCapabilities(max_tokens=80, compaction_threshold=0.3, preserve_recent_count=1)
    out = caps.prepare_messages("system prompt", _oversized_history())
    assert any("[prior context]" in m["content"] for m in out)
    assert not any("compacted for goal" in m["content"] for m in out)


# --------------------------------------------------------------------------- #
# (b) Opt-in: gateway + flag -> SLMCompactor on the context path.
# --------------------------------------------------------------------------- #


def test_flag_on_with_gateway_builds_slm_compactor():
    caps = AgentCapabilities(model_gateway=_StubGateway(), use_slm_compaction=True)
    assert isinstance(caps._slm_compactor, SLMCompactor)


def test_prepare_messages_routes_through_slm():
    gw = _StubGateway(content="DENSE SUMMARY")
    caps = AgentCapabilities(
        max_tokens=80,
        compaction_threshold=0.3,
        preserve_recent_count=1,
        model_gateway=gw,
        use_slm_compaction=True,
    )
    out = caps.prepare_messages("system prompt", _oversized_history())
    # The SLM was actually called (routed through the compactor profile)...
    assert gw.calls, "expected the stub gateway to be called for summarization"
    assert gw.calls[0][0] == "compactor"
    # ...and its summary landed in the SLM-flavored marker.
    assert any(
        "[prior context — compacted for goal] DENSE SUMMARY" in m["content"] for m in out
    )
    # The most-recent turn is preserved verbatim.
    assert any(m["content"] == "recent question" for m in out)


def test_prepare_messages_fails_soft_when_gateway_raises():
    gw = _RaisingGateway()
    caps = AgentCapabilities(
        max_tokens=80,
        compaction_threshold=0.3,
        preserve_recent_count=1,
        model_gateway=gw,
        use_slm_compaction=True,
    )
    # Must NOT raise even though the gateway errors on every call.
    out = caps.prepare_messages("system prompt", _oversized_history())
    assert gw.calls >= 1, "the gateway should have been attempted"
    # Fail-soft still produces the SLM compaction marker (extractive fallback).
    assert any("[prior context — compacted for goal]" in m["content"] for m in out)
    assert any(m["content"] == "recent question" for m in out)


def test_below_threshold_skips_gateway():
    # Opt-in on, but the conversation fits the budget -> SLM is not invoked.
    gw = _StubGateway()
    caps = AgentCapabilities(
        max_tokens=100000,
        model_gateway=gw,
        use_slm_compaction=True,
    )
    out = caps.prepare_messages("system prompt", _oversized_history())
    assert gw.calls == [], "SLM must not be called when compaction is not needed"
    assert not any("compacted for goal" in m["content"] for m in out)
