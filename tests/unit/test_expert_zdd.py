"""ZDD (zero-downtime delivery) and rollback test stubs across the 4 expert
collections.

Pins the spec-mandated ZDD invariants from:

* MATE §9 (MATE-ZDD-001..005) — ``docs/specs/FEATURE_MATERIALS_ENGINEER.md``.
  Materials has no dedicated promotion module yet, so the MATE suite tests the
  §9 promotion-ladder concept (BASELINE -> ... -> PRODUCTION, immutable
  digest-addressed baseline, automatic hold, revert) via a small in-test
  state machine that mirrors the spec vocabulary.
* CHEM §11 (CHEM-022) — ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md``. Covers
  :mod:`general_ludd.chemistry.promotion` (immutable snapshots, shadow read,
  stable-hash canary, atomic alias swap, 60s rollback, recoverable history,
  safety-policy direction).
* AIML §12 (AIML-AT-004/005) — ``docs/specs/FEATURE_AI_ML_EXPERT.md``. Covers
  :mod:`general_ludd.ai_ml.promotion` (canary budgets, atomic alias swap with
  in-flight drain, rollback within 60s, two-version retention, no-dropped-
  requests promotion).
* GRC §7 (GRC-ZDD-001..005) — ``docs/specs/FEATURE_GIT_RELEASE_CAPTAIN_EXPERT.md``.
  Covers :mod:`general_ludd.git_release.release_state` (build-once-by-digest,
  health gates, controlled promotion, release-page closure, rollback from
  canary/promote, terminal RELEASED).

Run: ``make test-specific TESTFILE='tests/unit/test_expert_zdd.py'``.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import os
import sys
from dataclasses import dataclass, field

import pytest

# ---------------------------------------------------------------------------
# Load promotion modules by file path (worktree-robust, matches existing tests).
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_CHEM_PROMO = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "promotion.py")
_AIML_PROMO = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "ai_ml", "promotion.py")
_GRC_STATE = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "git_release", "release_state.py")


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"{name} spec failed"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


chem = _load(_CHEM_PROMO, "zdd_chem_promotion")
aiml = _load(_AIML_PROMO, "zdd_aiml_promotion")
grc = _load(_GRC_STATE, "zdd_grc_release_state")


# ---------------------------------------------------------------------------
# MATE §9 — in-test promotion ladder (no production module yet; tests concept).
# ---------------------------------------------------------------------------


_MATE_STATES = (
    "BASELINE",
    "OFFLINE_MODEL",
    "COUPON",
    "PILOT",
    "SHADOW_INSPECTION",
    "CONTROLLED_RAMP",
    "PRODUCTION",
    "REVERT",
)


@dataclass
class _MateRoute:
    """Digest-addressed immutable route baseline (MATE-ZDD-001)."""

    route_id: str
    version: int
    digest: str
    steps: tuple[str, ...] = ()


@dataclass
class _MatePromotionLadder:
    """In-test stub of the §9 promotion ladder.

    Encodes the spec §9 forward edges plus REVERT from RAMP/PRODUCTION. The
    ladder holds the current serving route, the prior known-good route, and an
    automatic-hold flag (MATE-ZDD-004).
    """

    current: _MateRoute
    prior: _MateRoute | None = None
    state: str = "BASELINE"
    hold: bool = False
    hold_reason: str = ""
    quarantined_lots: list[str] = field(default_factory=list)

    _FORWARD: dict[str, str] = field(
        default_factory=lambda: {
            "BASELINE": "OFFLINE_MODEL",
            "OFFLINE_MODEL": "COUPON",
            "COUPON": "PILOT",
            "PILOT": "SHADOW_INSPECTION",
            "SHADOW_INSPECTION": "CONTROLLED_RAMP",
            "CONTROLLED_RAMP": "PRODUCTION",
        },
        repr=False,
    )

    def advance(self, *, target: str, out_of_control: bool = False) -> str:
        if target not in _MATE_STATES:
            raise ValueError(f"unknown MATE state {target!r}")
        if self.hold:
            return self.state
        # MATE-ZDD-004: out-of-control measurements stop new-route promotion.
        if out_of_control and target in ("CONTROLLED_RAMP", "PRODUCTION"):
            self.hold = True
            self.hold_reason = "out-of-control measurement"
            return self.state
        expected = self._FORWARD.get(self.state)
        if target != expected:
            raise ValueError(f"{self.state} -> {target} is not in spec §9 ladder")
        if target == "CONTROLLED_RAMP":
            # Entering ramp: capture prior for revert.
            self.prior = copy.copy(self.current)
        self.state = target
        return self.state

    def revert(self, *, nonconforming_lots: list[str] | None = None) -> _MateRoute:
        # MATE-ZDD-005: revert allowed from RAMP / PRODUCTION only.
        if self.state not in ("CONTROLLED_RAMP", "PRODUCTION"):
            raise RuntimeError(f"revert not permitted from {self.state}")
        if self.prior is None:
            raise RuntimeError("no prior route to revert to")
        # Quarantine parts produced since the last conforming evidence.
        self.quarantined_lots.extend(nonconforming_lots or [])
        self.current, self.prior = self.prior, None
        self.state = "REVERT"
        return self.current


# ---------------------------------------------------------------------------
# CHEM §11 (CHEM-022) — chemistry promotion pipeline.
# ---------------------------------------------------------------------------


def _chem_snapshot(label: str, version: int):
    return chem.ChemistrySnapshot(
        {
            "entities": {label + "_water": {"formula": "H2O"}},
            "properties": {label + "_bp": {"value": 373.15, "unit": "K"}},
            "reactions": {label + "_rxn": {"reactants": ["CH4", "O2"]}},
            "hazards": {label + "_ethanol": {"tier": "moderate"}},
        },
        version,
    )


class TestChemZDD:
    """CHEM-022 zero-downtime delivery invariants (spec §11)."""

    def test_snapshot_is_immutable_and_versioned(self) -> None:
        # CHEM-022 / MATE-ZDD-001 analogue: snapshot is versioned + immutable.
        payload = {"entities": {"water": {"formula": "H2O"}}}
        snap = chem.ChemistrySnapshot(payload, version=3)
        assert snap.version == 3
        # Mutation of source dict must not bleed into the snapshot.
        payload["entities"]["water"]["formula"] = "H2O2"
        assert snap.entities["water"]["formula"] == "H2O"
        # Each snapshot has a unique id (versioned identity).
        other = chem.ChemistrySnapshot({"entities": {}}, version=3)
        assert snap.snapshot_id != other.snapshot_id

    def test_shadow_read_does_not_affect_production(self) -> None:
        # CHEM-022 §11: shadow read returns shadow snapshot while production
        # read continues to return the current version.
        pipe = chem.PromotionPipeline()
        prod = _chem_snapshot("prod", 1)
        shadow = _chem_snapshot("shadow", 2)
        pipe.register_alias("chemistry", prod)
        pipe.start_shadow("chemistry", shadow)
        assert pipe.read("chemistry", "r1").version == 1
        assert pipe.read_shadow("chemistry", "r2").version == 2
        # Production read is unchanged after shadow started.
        assert pipe.read("chemistry", "r3").version == 1

    def test_canary_routing_is_stable_by_request_hash(self) -> None:
        # CHEM-022 §11: stable-hash canary routing — same request, same bucket.
        pipe = chem.PromotionPipeline()
        prod = _chem_snapshot("prod", 1)
        canary = _chem_snapshot("canary", 2)
        pipe.register_alias("chemistry", prod)
        pipe.start_canary("chemistry", canary, fraction=0.5)
        req = {"compound": "ethanol", "action": "lookup"}
        route_a = pipe.route_canary("chemistry", req)
        route_b = pipe.route_canary("chemistry", req)
        assert route_a == route_b
        # Hash itself is order-independent and stable.
        h1 = chem.canary_hash({"b": 2, "a": 1})
        h2 = chem.canary_hash({"a": 1, "b": 2})
        assert h1 == h2

    def test_atomic_swap_drops_no_accepted_request(self) -> None:
        # CHEM-022 §11: no accepted request is dropped during promotion.
        pipe = chem.PromotionPipeline()
        v1 = _chem_snapshot("v1", 1)
        v2 = _chem_snapshot("v2", 2)
        pipe.register_alias("chemistry", v1)
        # Admit a request on v1, then swap to v2 mid-flight.
        admitted_version = pipe.admit("chemistry", "req-1")
        assert admitted_version == 1
        swap = pipe.atomic_swap("chemistry", v2)
        assert swap["dropped_requests"] == 0
        assert swap["new_version"] == 2
        # The admitted request finishes on its recorded version (v1).
        result = pipe.finish("chemistry", "req-1")
        assert result["admitted_version"] == 1
        # New reads now see v2.
        assert pipe.read("chemistry", "r-next").version == 2

    def test_rollback_completes_within_60_seconds_of_breach(self) -> None:
        # CHEM-022 §11: rollback begins within 60s of a hard threshold breach.
        pipe = chem.PromotionPipeline()
        v1 = _chem_snapshot("v1", 1)
        v2 = _chem_snapshot("v2", 2)
        pipe.register_alias("chemistry", v1)
        pipe.atomic_swap("chemistry", v2)
        import time as _time

        breach_at = _time.monotonic()
        result = pipe.rollback("chemistry", breach_at=breach_at)
        assert result["rolled_back_to"] == 1
        assert result["within_seconds"] <= chem.ROLLBACK_SLA_SECONDS
        assert chem.ROLLBACK_SLA_SECONDS == 60

    def test_prior_two_versions_remain_recoverable(self) -> None:
        # CHEM-022 §11: the prior two known-good versions remain recoverable.
        pipe = chem.PromotionPipeline()
        v1 = _chem_snapshot("v1", 1)
        v2 = _chem_snapshot("v2", 2)
        v3 = _chem_snapshot("v3", 3)
        pipe.register_alias("chemistry", v1)
        pipe.atomic_swap("chemistry", v2)
        pipe.atomic_swap("chemistry", v3)
        recoverable = pipe.recoverable_versions("chemistry")
        # Current (3) plus at least the prior two (1 and 2) are warm.
        assert 3 in recoverable
        assert 2 in recoverable
        assert 1 in recoverable
        assert len(recoverable) >= 3

    def test_safety_policy_tighten_immediate_loosen_requires_approval(self) -> None:
        # CHEM-022 §11: tighten immediate; loosen requires approval + evidence.
        pipe = chem.PromotionPipeline()
        v1 = _chem_snapshot("v1", 1)
        pipe.register_alias("chemistry", v1)
        old = {"ethanol": {"tier": "moderate"}}
        tighten = {"ethanol": {"tier": "high"}}
        loosen = {"ethanol": {"tier": "low"}}
        # Tightening applies immediately with no approval.
        t = pipe.apply_safety_policy("chemistry", old, tighten, approval=None)
        assert t["applied"] is True
        assert t["direction"] == "tighten"
        assert t["requires_approval"] is False
        # Loosening without approval is blocked.
        l_no = pipe.apply_safety_policy("chemistry", tighten, loosen, approval=None)
        assert l_no["applied"] is False
        assert l_no["direction"] == "loosen"
        assert l_no["requires_approval"] is True
        # Loosening with approval + canary evidence applies.
        l_yes = pipe.apply_safety_policy(
            "chemistry",
            tighten,
            loosen,
            approval={"approver": "op", "canary_evidence": "delta=ok"},
        )
        assert l_yes["applied"] is True
        assert l_yes["direction"] == "loosen"


# ---------------------------------------------------------------------------
# AIML §12 (AIML-AT-004/005) — AIML promotion gate.
# ---------------------------------------------------------------------------


def _aiml_budgets():
    return aiml.CanaryBudgets(
        quality_floor=0.9,
        safety_floor=0.95,
        latency_p99_ceiling_ms=500.0,
        error_rate_ceiling=0.01,
        cost_ceiling_usd_per_kreq=2.0,
    )


class TestAimlZDD:
    """AIML-AT-004/005 zero-downtime promotion invariants (spec §12)."""

    def test_canary_budget_breach_detected(self) -> None:
        # AIML §12 step 6: a breach of ANY budget makes the verdict unhealthy.
        gate = aiml.PromotionGate(
            budgets=_aiml_budgets(),
            current_version="v1",
            prior_versions=("v0", "v-1"),
        )
        healthy = aiml.CanaryMetrics(
            quality=0.95,
            safety=0.99,
            latency_p99_ms=200.0,
            error_rate=0.001,
            cost_usd_per_kreq=1.0,
        )
        assert gate.canary_check(healthy).healthy is True
        breached = aiml.CanaryMetrics(
            quality=0.8,  # below floor 0.9
            safety=0.99,
            latency_p99_ms=200.0,
            error_rate=0.001,
            cost_usd_per_kreq=1.0,
        )
        verdict = gate.canary_check(breached)
        assert verdict.healthy is False
        assert "quality" in verdict.breached_budgets

    def test_alias_swap_is_atomic_with_in_flight_drain(self) -> None:
        # AIML §12 step 7: atomic swap; in-flight finish on original version.
        gate = aiml.PromotionGate(
            budgets=_aiml_budgets(),
            current_version="v1",
            prior_versions=("v0",),
        )
        swap = gate.alias_swap(alias="production", to_version="v2", in_flight_requests=5)
        assert swap.from_version == "v1"
        assert swap.to_version == "v2"
        assert swap.in_flight_requests == 5
        assert swap.drained is False
        # New requests resolve to v2 immediately (atomic linearization).
        assert gate.resolve_alias("production") == "v2"
        # Drain completes the in-flight requests.
        drained = gate.drain_in_flight(swap)
        assert drained.drained is True
        assert drained.in_flight_requests == 0

    def test_rollback_initiated_within_60_seconds(self) -> None:
        # AIML-AT-005: rollback within 60s of a hard threshold breach.
        gate = aiml.PromotionGate(
            budgets=_aiml_budgets(),
            current_version="v1",
            prior_versions=("v0", "v-1"),
        )
        gate.alias_swap(alias="production", to_version="v2")
        result = gate.rollback(breach_time_s=30.0)
        assert result.swapped_back_to == "v1"
        assert result.initiated_within_60s is True
        assert result.seconds_to_initiate == 30.0
        # A late rollback still executes but flags the SLO miss.
        gate.alias_swap(alias="production", to_version="v2b")
        late = gate.rollback(breach_time_s=90.0)
        assert late.initiated_within_60s is False
        assert aiml.ROLLBACK_SLO_SECONDS == 60

    def test_retention_policy_requires_two_prior_versions(self) -> None:
        # AIML §12 step 8: retain at least the prior two known-good versions.
        with pytest.raises(ValueError, match="retention policy"):
            aiml.PromotionGate(
                budgets=_aiml_budgets(),
                current_version="v1",
                prior_versions=("v0",),  # only one prior
                enforce_retention=True,
            )
        gate = aiml.PromotionGate(
            budgets=_aiml_budgets(),
            current_version="v1",
            prior_versions=("v0", "v-1"),
            enforce_retention=True,
        )
        assert gate.current_version == "v1"

    def test_failing_canary_leaves_alias_unchanged(self) -> None:
        # AIML-AT-004: a failing regression never changes the active alias.
        gate = aiml.PromotionGate(
            budgets=_aiml_budgets(),
            current_version="v1",
            prior_versions=("v0", "v-1"),
        )
        breached = aiml.CanaryMetrics(
            quality=0.5,
            safety=0.5,
            latency_p99_ms=999.0,
            error_rate=0.5,
            cost_usd_per_kreq=10.0,
        )
        verdict = gate.canary_check(breached)
        assert verdict.healthy is False
        # Alias was never swapped — production still points at v1.
        assert gate.resolve_alias("production") == "v1"


# ---------------------------------------------------------------------------
# GRC §7 (GRC-ZDD-001..005) — release state machine.
# ---------------------------------------------------------------------------


_SOURCE_SHA = "a" * 40
_OTHER_SHA = "b" * 40
_ARTIFACT_DIGEST = "sha256:aaa"
_OTHER_DIGEST = "sha256:zzz"


def _grc_machine():
    return grc.ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_ARTIFACT_DIGEST)


def _advance_to(
    machine,
    target,
    **kwargs,
):
    """Walk forward one edge at a time using the spec §7 default path."""
    order = [
        grc.ReleaseState.PLAN,
        grc.ReleaseState.BUILD_ONCE,
        grc.ReleaseState.VERIFY_OFFLINE,
        grc.ReleaseState.STAGE,
        grc.ReleaseState.CANARY,
        grc.ReleaseState.PROMOTE,
        grc.ReleaseState.VERIFY_RELEASE_PAGE,
        grc.ReleaseState.RELEASED,
    ]
    if target not in order:
        raise ValueError(f"cannot walk to {target}")
    idx = order.index(target)
    # Provide cumulative defaults so callers can override per-edge.
    defaults: dict = {
        "gate_evidence": [("lint", "pass", "0")],
        "artifact_digest": _ARTIFACT_DIGEST,
        "observed_source_sha": _SOURCE_SHA,
        "health_gate_passed": True,
        "prior_digest": "sha256:prior",
        "release_page_proven": True,
    }
    defaults.update(kwargs)
    for state in order[: idx + 1]:
        res = machine.advance(target=state, **defaults)
        assert not res.blocked, f"blocked advancing to {state}: {res.reasons}"
    return grc.AdvanceResult(blocked=False, reasons=[], state=machine.state)


class TestGrcZDD:
    """GRC-ZDD-001..005 zero-downtime release invariants (spec §7)."""

    def test_build_once_promote_by_digest(self) -> None:
        # GRC-ZDD-001: STAGE must consume the same digest produced at build.
        machine = _grc_machine()
        _advance_to(machine, grc.ReleaseState.VERIFY_OFFLINE)
        # A different digest is rejected and state is held.
        blocked = machine.advance(
            target=grc.ReleaseState.STAGE,
            artifact_digest=_OTHER_DIGEST,
            observed_source_sha=_SOURCE_SHA,
        )
        assert blocked.blocked is True
        assert "GRC-ZDD-001" in blocked.reasons
        assert machine.state is grc.ReleaseState.VERIFY_OFFLINE
        # The pinned digest advances.
        ok = machine.advance(
            target=grc.ReleaseState.STAGE,
            artifact_digest=_ARTIFACT_DIGEST,
            observed_source_sha=_SOURCE_SHA,
        )
        assert not ok.blocked
        assert machine.state is grc.ReleaseState.STAGE

    def test_health_gate_required_for_canary_and_promote(self) -> None:
        # GRC-ZDD-003: canary + promote require a passed health gate.
        machine = _grc_machine()
        _advance_to(machine, grc.ReleaseState.STAGE)
        blocked = machine.advance(
            target=grc.ReleaseState.CANARY,
            health_gate_passed=False,
            prior_digest="sha256:prior",
            observed_source_sha=_SOURCE_SHA,
        )
        assert blocked.blocked is True
        assert "GRC-ZDD-003" in blocked.reasons
        # With the gate passed, canary proceeds.
        ok = machine.advance(
            target=grc.ReleaseState.CANARY,
            health_gate_passed=True,
            prior_digest="sha256:prior",
            observed_source_sha=_SOURCE_SHA,
        )
        assert not ok.blocked
        assert machine.state is grc.ReleaseState.CANARY

    def test_rollback_from_canary_restores_prior_digest(self) -> None:
        # GRC-ZDD-004: rollback target is the captured prior known-good digest.
        machine = _grc_machine()
        _advance_to(
            machine,
            grc.ReleaseState.CANARY,
            prior_digest="sha256:prior-good",
        )
        assert machine.serving_digest == _ARTIFACT_DIGEST
        machine.rollback(reason="canary regression")
        assert machine.state is grc.ReleaseState.ROLLBACK
        # Prior known-good restored.
        assert machine.serving_digest == "sha256:prior-good"

    def test_released_is_terminal_cannot_rollback(self) -> None:
        # RELEASED is terminal: recovery is a fresh plan, not a rollback.
        machine = _grc_machine()
        _advance_to(machine, grc.ReleaseState.RELEASED)
        with pytest.raises(grc.TransitionError, match="terminal"):
            machine.rollback(reason="late regression")

    def test_release_page_closure_required_before_released(self) -> None:
        # GRC-ZDD-005: VERIFY_RELEASE_PAGE requires a proven release page.
        machine = _grc_machine()
        _advance_to(machine, grc.ReleaseState.PROMOTE)
        blocked = machine.advance(
            target=grc.ReleaseState.VERIFY_RELEASE_PAGE,
            release_page_proven=False,
            observed_source_sha=_SOURCE_SHA,
        )
        assert blocked.blocked is True
        assert "GRC-ZDD-005" in blocked.reasons
        assert machine.state is grc.ReleaseState.PROMOTE
        ok = machine.advance(
            target=grc.ReleaseState.VERIFY_RELEASE_PAGE,
            release_page_proven=True,
            observed_source_sha=_SOURCE_SHA,
        )
        assert not ok.blocked

    def test_moving_source_sha_blocks_after_build(self) -> None:
        # GRC-SEC-004: a moving source SHA blocks every post-build stage.
        machine = _grc_machine()
        _advance_to(machine, grc.ReleaseState.BUILD_ONCE)
        blocked = machine.advance(
            target=grc.ReleaseState.VERIFY_OFFLINE,
            gate_evidence=[("lint", "pass", "0")],
            observed_source_sha=_OTHER_SHA,
        )
        assert blocked.blocked is True
        assert "GRC-SEC-004" in blocked.reasons


# ---------------------------------------------------------------------------
# MATE §9 — promotion ladder concept (no production module yet).
# ---------------------------------------------------------------------------


def _digest(route_id: str, version: int) -> str:
    return hashlib.sha256(f"{route_id}:{version}".encode()).hexdigest()


def _mate_route(version: int = 1) -> _MateRoute:
    rid = f"route-{version}"
    return _MateRoute(
        route_id=rid,
        version=version,
        digest=_digest(rid, version),
        steps=("forming", "joining", "finishing"),
    )


class TestMateZDD:
    """MATE-ZDD-001..005 promotion-ladder concept tests (spec §9)."""

    def test_baseline_is_immutable_and_digest_addressed(self) -> None:
        # MATE-ZDD-001: baseline is versioned and digest-addressed; promotion
        # must not mutate the prior baseline.
        baseline = _mate_route(version=1)
        assert baseline.version == 1
        assert len(baseline.digest) == 64
        ladder = _MatePromotionLadder(current=baseline)
        original_digest = ladder.current.digest
        # A new route (v2) is a separate object; baseline digest is untouched.
        v2 = _mate_route(version=2)
        ladder.current = v2
        assert baseline.digest == original_digest
        assert v2.digest != baseline.digest

    def test_promotion_ladder_follows_spec_order(self) -> None:
        # Spec §9 forward edges: BASELINE -> ... -> PRODUCTION.
        ladder = _MatePromotionLadder(current=_mate_route(1))
        for target in (
            "OFFLINE_MODEL",
            "COUPON",
            "PILOT",
            "SHADOW_INSPECTION",
            "CONTROLLED_RAMP",
            "PRODUCTION",
        ):
            assert ladder.advance(target=target) == target
        # An out-of-order jump is rejected.
        ladder2 = _MatePromotionLadder(current=_mate_route(1))
        with pytest.raises(ValueError, match="not in spec"):
            ladder2.advance(target="PRODUCTION")

    def test_automatic_hold_on_out_of_control_measurement(self) -> None:
        # MATE-ZDD-004: out-of-control measurements stop new-route promotion
        # and retain the last approved route.
        ladder = _MatePromotionLadder(current=_mate_route(1))
        # Walk to SHADOW_INSPECTION (one step before RAMP).
        for target in ("OFFLINE_MODEL", "COUPON", "PILOT", "SHADOW_INSPECTION"):
            ladder.advance(target=target)
        held = ladder.advance(target="CONTROLLED_RAMP", out_of_control=True)
        assert held == "SHADOW_INSPECTION"  # did not advance
        assert ladder.hold is True
        assert "out-of-control" in ladder.hold_reason
        # Subsequent advance is also blocked while hold is active.
        again = ladder.advance(target="CONTROLLED_RAMP")
        assert again == "SHADOW_INSPECTION"

    def test_revert_restores_prior_route_and_quarantines_parts(self) -> None:
        # MATE-ZDD-005: revert restores the prior digest-addressed route and
        # quarantines parts produced since the last conforming evidence.
        v1 = _mate_route(1)
        v2 = _mate_route(2)
        ladder = _MatePromotionLadder(current=v1)
        for target in ("OFFLINE_MODEL", "COUPON", "PILOT", "SHADOW_INSPECTION"):
            ladder.advance(target=target)
        ladder.advance(target="CONTROLLED_RAMP")
        ladder.current = v2  # new route went live during ramp
        reverted = ladder.revert(nonconforming_lots=["LOT-9", "LOT-10"])
        assert reverted.version == 1  # prior route restored
        assert reverted.digest == v1.digest
        assert ladder.state == "REVERT"
        assert "LOT-9" in ladder.quarantined_lots
        assert "LOT-10" in ladder.quarantined_lots

    def test_revert_forbidden_from_baseline(self) -> None:
        # MATE-ZDD-005: revert is only allowed from RAMP / PRODUCTION.
        ladder = _MatePromotionLadder(current=_mate_route(1))
        with pytest.raises(RuntimeError, match="not permitted"):
            ladder.revert()
