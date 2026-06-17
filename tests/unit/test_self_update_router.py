"""Tests for the UpdateRequestRouter (self-update issue #81 part 1).

The router turns a natural-language "update gludd: <text>" request into a
deterministic :class:`UpdatePlan` describing WHAT to change, WHERE, the
capability required to do it safely, a priority, and a risk level.

Design contract under test:

* Most updates are config/yaml edits -> high priority, low risk, fast-track.
* Role/playbook edits -> collections self-modify -> medium priority/risk.
* Behavioural code changes -> code self-modify -> low auto-priority, high risk
  (they need human review before landing).
* An unmatchable request -> FAIL-SAFE: kind='config', risk='high', a
  "needs human routing" rationale, and never a guessed code write.
* Only paths that actually exist (per the injected ``path_exists``) are kept.

The router is pure/deterministic and fully injectable, so every test pins a
fake ``path_exists`` rather than touching the real filesystem.
"""

from __future__ import annotations

import pytest

from general_ludd.self_update import (
    DEFAULT_SUBSYSTEM_MAP,
    UpdatePlan,
    UpdateRequest,
    UpdateRequestRouter,
    UpdateTarget,
)


def _always_exists(_path: str) -> bool:
    return True


def _never_exists(_path: str) -> bool:
    return False


def _route(text: str, path_exists=_always_exists, subsystem_map=None) -> UpdatePlan:
    router = UpdateRequestRouter(subsystem_map=subsystem_map, path_exists=path_exists)
    return router.route(text)


# ---------------------------------------------------------------------------
# Dataclass shape
# ---------------------------------------------------------------------------
class TestDataclasses:
    def test_update_request_holds_text(self) -> None:
        req = UpdateRequest(text="update gludd: do a thing")
        assert req.text == "update gludd: do a thing"

    def test_update_target_fields(self) -> None:
        tgt = UpdateTarget(kind="config", paths=["config/ratchet.yml"], subsystem="lint")
        assert tgt.kind == "config"
        assert tgt.paths == ["config/ratchet.yml"]
        assert tgt.subsystem == "lint"

    def test_update_plan_fields(self) -> None:
        tgt = UpdateTarget(kind="config", paths=["config/ratchet.yml"], subsystem="lint")
        plan = UpdatePlan(
            target=tgt,
            change_summary="x",
            capability_required="config_write",
            priority=8,
            risk="low",
            rationale="because",
        )
        assert plan.target is tgt
        assert plan.capability_required == "config_write"
        assert plan.priority == 8
        assert plan.risk == "low"
        assert plan.rationale == "because"


# ---------------------------------------------------------------------------
# Canonical routing cases from the task spec
# ---------------------------------------------------------------------------
class TestCanonicalRoutes:
    def test_spend_window_is_config_high_priority(self) -> None:
        plan = _route("update gludd: increase the spend window to 2h")
        assert plan.target.subsystem == "budget"
        assert plan.target.kind in ("config", "yaml")
        assert plan.capability_required == "config_write"
        assert plan.priority == 8
        assert plan.risk == "low"

    def test_role_verify_step_is_role_yaml_target(self) -> None:
        plan = _route("update gludd: add a verify step to the security_review role")
        assert plan.target.kind in ("role", "yaml")
        assert plan.target.subsystem == "role"
        assert plan.capability_required == "collections_self_modify"
        assert plan.priority == 5
        assert plan.risk == "medium"
        # The named role must be resolved into its real role directory path.
        assert any("security_review" in p for p in plan.target.paths)

    def test_scheduler_behaviour_change_is_code_target(self) -> None:
        plan = _route("update gludd: change how the scheduler picks the next task")
        assert plan.target.kind == "code"
        assert plan.target.subsystem == "scheduler"
        assert plan.capability_required == "code_self_modify"
        assert plan.priority == 3
        assert plan.risk == "high"

    def test_unmatchable_request_is_fail_safe(self) -> None:
        plan = _route("update gludd: please frobnicate the wombat quux")
        assert plan.target.kind == "config"
        assert plan.risk == "high"
        assert "needs human routing" in plan.rationale.lower()
        assert plan.target.paths == []


# ---------------------------------------------------------------------------
# Subsystem classification breadth
# ---------------------------------------------------------------------------
class TestSubsystemClassification:
    @pytest.mark.parametrize(
        ("text", "subsystem"),
        [
            ("update gludd: raise the monthly budget cap", "budget"),
            ("update gludd: lower the cost ceiling", "budget"),
            ("update gludd: add a new model profile for claude", "model"),
            ("update gludd: switch the default provider api base", "model"),
            ("update gludd: tighten the lint gate ratchet", "lint"),
            ("update gludd: add an xdist worker to ci", "lint"),
            ("update gludd: rotate the vault secret alias", "secret"),
            ("update gludd: add a new log source connector", "connector"),
            ("update gludd: improve observability tracing", "connector"),
        ],
    )
    def test_keyword_routes_to_subsystem(self, text: str, subsystem: str) -> None:
        plan = _route(text)
        assert plan.target.subsystem == subsystem

    def test_config_subsystems_are_high_priority_low_risk(self) -> None:
        for text in (
            "update gludd: raise the budget cap",
            "update gludd: add a model profile",
            "update gludd: tighten the lint ratchet",
            "update gludd: rotate a vault secret",
        ):
            plan = _route(text)
            assert plan.capability_required == "config_write"
            assert plan.priority == 8
            assert plan.risk == "low"


# ---------------------------------------------------------------------------
# Path existence verification
# ---------------------------------------------------------------------------
class TestPathVerification:
    def test_nonexistent_paths_are_dropped(self) -> None:
        # budget maps to several candidate paths; if none exist, fail-safe.
        plan = _route("update gludd: raise the budget cap", path_exists=_never_exists)
        assert plan.target.paths == []
        assert plan.target.kind == "config"
        assert plan.risk == "high"
        assert "needs human routing" in plan.rationale.lower()

    def test_only_existing_paths_kept(self) -> None:
        kept = "config/ratchet.yml"

        def only_ratchet(path: str) -> bool:
            return path == kept

        plan = _route("update gludd: tighten the lint ratchet", path_exists=only_ratchet)
        assert plan.target.paths == [kept]

    def test_custom_subsystem_map_overrides_default(self) -> None:
        custom = {
            "budget": {
                "kind": "config",
                "paths": ["config/ratchet.yml"],
                "keywords": ["spend"],
            }
        }
        plan = _route(
            "update gludd: change the spend window",
            subsystem_map=custom,
        )
        assert plan.target.subsystem == "budget"
        assert plan.target.paths == ["config/ratchet.yml"]


# ---------------------------------------------------------------------------
# Code-vs-config decision boundary
# ---------------------------------------------------------------------------
class TestKindDecision:
    def test_default_is_config_not_code(self) -> None:
        # A budget tweak is expressible in config -> never escalate to code.
        plan = _route("update gludd: raise the budget cap")
        assert plan.target.kind != "code"

    def test_behaviour_change_escalates_to_code(self) -> None:
        plan = _route("update gludd: change how the scheduler dispatches work")
        assert plan.target.kind == "code"
        assert plan.capability_required == "code_self_modify"

    def test_code_target_never_auto_high_priority(self) -> None:
        plan = _route("update gludd: rewrite the scheduler parallel dispatch logic")
        assert plan.priority <= 3
        assert plan.risk == "high"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
class TestDeterminism:
    def test_same_input_same_plan(self) -> None:
        a = _route("update gludd: increase the spend window to 2h")
        b = _route("update gludd: increase the spend window to 2h")
        assert a == b

    def test_default_map_is_nonempty_and_well_formed(self) -> None:
        assert DEFAULT_SUBSYSTEM_MAP
        for name, spec in DEFAULT_SUBSYSTEM_MAP.items():
            assert isinstance(name, str)
            assert spec["kind"] in ("config", "yaml", "role", "code")
            assert isinstance(spec["paths"], list)
            assert isinstance(spec["keywords"], list)
            assert spec["keywords"], f"{name} must have keywords"
