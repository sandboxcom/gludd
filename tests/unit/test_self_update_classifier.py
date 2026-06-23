"""Unit tests for the NL self-update classifier (issue #81).

Covers :mod:`general_ludd.self_update.classifier`:

* :func:`classify` returns a well-formed :class:`SelfUpdatePlan` whose fields
  (subsystem, change_kind, apply_tier, target_files, confidence,
  requires_approval) are consistent with each other.
* The keyword/heuristic routing maps representative requests to the expected
  subsystem and change-kind, and :func:`llm_route` is a no-op stub today.
* Edge cases: empty input, malformed/garbage input, and unroutable input all
  fall through to the default-DENY sink (``Subsystem.UNKNOWN`` /
  ``ChangeKind.UNKNOWN``) rather than silently landing somewhere plausible.

The classifier is pure + deterministic, so every test is a plain assertion on
the returned plan — no fixtures, no I/O.
"""

from __future__ import annotations

import pytest

from general_ludd.self_update.classifier import (
    classify,
    classify_change_kind,
    classify_subsystem,
    llm_route,
    strip_prefix,
)
from general_ludd.self_update.model import (
    ApplyTier,
    ChangeKind,
    SelfUpdatePlan,
    SelfUpdateRequest,
    Subsystem,
)


def _req(text: str) -> SelfUpdateRequest:
    """Build a request with default requested_by/approval_token."""
    return SelfUpdateRequest(raw_text=text)


# ---------------------------------------------------------------------------
# classify() — well-formed SelfUpdatePlan
# ---------------------------------------------------------------------------
class TestClassifyReturnsValidPlan:
    def test_returns_self_update_plan_instance(self) -> None:
        plan = classify(_req("increase the spend budget to 100"))
        assert isinstance(plan, SelfUpdatePlan)

    def test_plan_fields_are_consistent_for_value_edit(self) -> None:
        plan = classify(_req("set the spend budget cap to 200"))
        assert plan.subsystem is Subsystem.SPEND
        assert plan.change_kind is ChangeKind.VALUE_EDIT
        assert plan.apply_tier is ApplyTier.CONFIG
        # VALUE_EDIT on a known subsystem pins a config file hint.
        assert plan.target_files == ("config/general-ludd.yml",)
        # VALUE_EDIT on a non-SECURITY, non-CODE surface does not force approval.
        assert plan.requires_approval is False
        assert 0.0 < plan.confidence <= 1.0
        assert plan.rationale

    def test_scaffold_request_routes_to_scaffold_tier(self) -> None:
        plan = classify(_req("add a new connector for webhook integration"))
        assert plan.subsystem is Subsystem.CONNECTORS
        assert plan.change_kind is ChangeKind.SCAFFOLD
        assert plan.apply_tier is ApplyTier.SCAFFOLD

    def test_code_change_request_routes_to_code_tier_and_requires_approval(self) -> None:
        plan = classify(_req("refactor the function that computes scoring weights"))
        assert plan.subsystem is Subsystem.SCORING
        assert plan.change_kind is ChangeKind.CODE_CHANGE
        assert plan.apply_tier is ApplyTier.CODE
        # CODE tier always proposes approval.
        assert plan.requires_approval is True
        # CODE_CHANGE never guesses a Python file from keywords alone.
        assert plan.target_files == ()

    def test_rationale_mentions_subsystem_kind_and_tier(self) -> None:
        plan = classify(_req("tune the scoring weight for priority"))
        assert "subsystem=" in plan.rationale
        assert "change_kind=" in plan.rationale
        assert "proposed_tier=" in plan.rationale

    def test_security_subsystem_forces_approval_even_for_value_edit(self) -> None:
        plan = classify(_req("adjust the security guardrail permissions"))
        assert plan.subsystem is Subsystem.SECURITY
        # SECURITY forces requires_approval regardless of tier/kind.
        assert plan.requires_approval is True

    def test_prefix_is_stripped_before_routing(self) -> None:
        plan_with_prefix = classify(_req("update gludd: bump the spend cap"))
        plan_without_prefix = classify(_req("bump the spend cap"))
        assert plan_with_prefix.subsystem is Subsystem.SPEND
        assert plan_with_prefix.subsystem == plan_without_prefix.subsystem
        assert plan_with_prefix.change_kind == plan_without_prefix.change_kind


# ---------------------------------------------------------------------------
# NL routing — keyword/heuristic + llm_route stub
# ---------------------------------------------------------------------------
class TestNLRouting:
    @pytest.mark.parametrize(
        ("text", "expected_subsystem"),
        [
            ("add a new role persona for code review", Subsystem.ROLES),
            ("register a slack webhook connector", Subsystem.CONNECTORS),
            ("change the anthropic provider model profile", Subsystem.PROFILES),
            ("update the prompt template wording", Subsystem.PROMPTS),
            ("tune the priority scoring weighting heuristic", Subsystem.SCORING),
            ("change the model routing fallback in the gateway", Subsystem.GATEWAY),
            ("lower the spend budget cap", Subsystem.SPEND),
            ("increase the observability metrics heartbeat interval", Subsystem.OBSERVABILITY),
            ("raise the scheduler concurrency parallelism", Subsystem.SCHEDULING),
            ("tighten the security permissions policy", Subsystem.SECURITY),
            ("set the yaml config timeout value", Subsystem.CONFIG),
        ],
    )
    def test_subsystem_routing(self, text: str, expected_subsystem: Subsystem) -> None:
        subsystem, score = classify_subsystem(text)
        assert subsystem is expected_subsystem
        assert score >= 1

    @pytest.mark.parametrize(
        ("text", "expected_kind", "expected_tier"),
        [
            # VALUE_EDIT (default safest tier)
            ("set the spend budget to 100", ChangeKind.VALUE_EDIT, ApplyTier.CONFIG),
            ("bump the timeout limit", ChangeKind.VALUE_EDIT, ApplyTier.CONFIG),
            # SCAFFOLD
            ("add a new role for triage", ChangeKind.SCAFFOLD, ApplyTier.SCAFFOLD),
            ("scaffold a fresh connector", ChangeKind.SCAFFOLD, ApplyTier.SCAFFOLD),
            # CODE_CHANGE
            ("rewrite the function that ranks agents", ChangeKind.CODE_CHANGE, ApplyTier.CODE),
            ("fix the bug in the scoring class", ChangeKind.CODE_CHANGE, ApplyTier.CODE),
        ],
    )
    def test_change_kind_to_tier_mapping(
        self, text: str, expected_kind: ChangeKind, expected_tier: ApplyTier
    ) -> None:
        plan = classify(_req(text))
        assert plan.change_kind is expected_kind
        assert plan.apply_tier is expected_tier

    def test_specific_subsystem_wins_over_config_catchall_on_tie(self) -> None:
        # "scoring" hits SCORING once and "heuristic" once (2 hits); CONFIG keywords
        # also appear ("value" is not here, but "weighting" isn't a CONFIG kw).
        # Construct a request that ties a specific subsystem with CONFIG hits.
        text = "update the scoring config"
        subsystem, _ = classify_subsystem(text)
        # SCORING and CONFIG both score on this text; SCORING must win.
        assert subsystem is Subsystem.SCORING

    def test_llm_route_stub_returns_none(self) -> None:
        """The LLM-assisted hook is intentionally a no-op today."""
        result = llm_route(_req("anything at all"))
        assert result is None

    def test_classify_change_kind_unknown_for_blank(self) -> None:
        assert classify_change_kind("") is ChangeKind.UNKNOWN
        assert classify_change_kind("   ") is ChangeKind.UNKNOWN

    def test_strip_prefix_handles_both_separators_and_is_idempotent(self) -> None:
        assert strip_prefix("update gludd: do thing") == "do thing"
        assert strip_prefix("update gludd - do thing") == "do thing"
        assert strip_prefix("UPDATE GLUDD: Do Thing") == "Do Thing"
        # No prefix -> unchanged (just stripped).
        assert strip_prefix("plain text") == "plain text"
        # None / empty -> empty.
        assert strip_prefix("") == ""


# ---------------------------------------------------------------------------
# Edge cases: empty / malformed / unknown
# ---------------------------------------------------------------------------
class TestEdgeCases:
    def test_empty_request_routes_to_unknown_with_zero_confidence(self) -> None:
        plan = classify(_req(""))
        assert plan.subsystem is Subsystem.UNKNOWN
        assert plan.change_kind is ChangeKind.UNKNOWN
        assert plan.apply_tier is ApplyTier.CODE  # UNKNOWN -> most cautious tier
        assert plan.target_files == ()
        assert plan.confidence == 0.0
        # UNKNOWN kind forces approval.
        assert plan.requires_approval is True

    def test_whitespace_only_request_routes_to_unknown(self) -> None:
        plan = classify(_req("   \t   \n  "))
        assert plan.subsystem is Subsystem.UNKNOWN
        assert plan.change_kind is ChangeKind.UNKNOWN
        assert plan.confidence == 0.0

    def test_garbage_input_with_no_keywords_routes_to_unknown(self) -> None:
        plan = classify(_req("xyzzy frobnicate quux blargh"))
        assert plan.subsystem is Subsystem.UNKNOWN
        # No scaffold/code verbs -> VALUE_EDIT (safest non-unknown default).
        assert plan.change_kind is ChangeKind.VALUE_EDIT
        # UNKNOWN subsystem => zero confidence regardless of kind.
        assert plan.confidence == 0.0

    def test_punctuation_only_input_does_not_crash(self) -> None:
        plan = classify(_req("!@#$%^&*()_+"))
        assert isinstance(plan, SelfUpdatePlan)
        assert plan.subsystem is Subsystem.UNKNOWN

    def test_very_long_input_is_handled(self) -> None:
        # A huge repetitive input must not blow up the regex/keyword scan.
        text = "budget " * 5000
        plan = classify(_req(text))
        assert plan.subsystem is Subsystem.SPEND

    def test_unicode_input_is_handled(self) -> None:
        plan = classify(_req("增 加 预 算 spend budget cap"))
        # "spend" and "budget" are ASCII keywords that still match.
        assert plan.subsystem is Subsystem.SPEND

    def test_unknown_tier_corresponds_to_unknown_kind_only(self) -> None:
        # UNKNOWN subsystem + non-UNKNOWN kind still yields a usable plan;
        # only when BOTH are unknown does confidence collapse to 0.
        plan = classify(_req("scoring"))
        # "scoring" routes to SCORING subsystem with VALUE_EDIT kind.
        assert plan.subsystem is Subsystem.SCORING
        assert plan.change_kind is ChangeKind.VALUE_EDIT
        assert plan.confidence > 0.0
