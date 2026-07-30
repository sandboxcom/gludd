"""Observability contract stubs and tests for the 4 expert collections.

Codifies the observability/metrics requirements from:
- MATE §10 (FEATURE_MATERIALS_ENGINEER.md): required events, 30s heartbeat
- CHEM §12 (FEATURE_CHEMISTRY_EXPERT.md): required metrics, 30s progress, bounded labels
- AIML §13 (FEATURE_AI_ML_EXPERT.md): OpenTelemetry traces, bounded labels
- GRC §9 (FEATURE_GIT_RELEASE_CAPTAIN_EXPERT.md): events, heartbeats, bounded labels

For collections that do not yet ship event/metric dataclasses, the stubs below
pin the spec's shape so the real implementations can be validated against them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

# ---------------------------------------------------------------------------
# Shared contract: every expert event carries correlation + timing fields
# ---------------------------------------------------------------------------

# Label keys forbidden across all four collections' metrics.
# Per CHEM §12: chemical structures, formulas, lot IDs, protocol text, source
# URLs, sample names, and artifact digests are NOT metric labels.
# Per AIML §13: source URLs, prompts, and artifact digests are NOT labels.
# These are unbounded-cardinality or sensitive values.
FORBIDDEN_LABEL_KEYS: frozenset[str] = frozenset(
    {
        "source_url",
        "artifact_digest",
        "formula",
        "structure",
        "smiles",
        "lot_id",
        "protocol_text",
        "sample_name",
        "prompt",
        "secret",
        "token",
        "credential",
        "command_args",
    }
)

# Fragments that must never appear in serialized log/metric values.
SENSITIVE_VALUE_FRAGMENTS: tuple[str, ...] = (
    "secret",
    "password",
    "token",
    "credential",
    "api_key",
    "private_key",
)


@dataclass(frozen=True)
class ExpertEvent:
    """Minimal event contract shared by all four expert collections.

    Every emitted event MUST carry:
    - trace_id: correlates the event across the full request lifecycle
    - request_id: the inbound request that originated this work
    - started_at / ended_at: timing for latency analysis
    - reason_code: stable machine-readable error code (never a raw exception)
    """

    event_type: str
    trace_id: str
    request_id: str
    started_at: datetime
    ended_at: datetime | None = None
    reason_code: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def is_complete(self) -> bool:
        return self.ended_at is not None

    def duration_seconds(self) -> float:
        end = self.ended_at or datetime.now(UTC)
        return (end - self.started_at).total_seconds()


@dataclass(frozen=True)
class HeartbeatMarker:
    """A heartbeat emitted by a long-running operation.

    Per MATE §10 / CHEM §12 / AIML §13 / GRC §9: long operations SHALL emit
    a heartbeat at least every 30 seconds.
    """

    trace_id: str
    request_id: str
    operation_id: str
    emitted_at: datetime
    phase: str = "running"
    sequence: int = 0


@dataclass(frozen=True)
class MetricSample:
    """A single metric observation with bounded labels.

    Label keys MUST be drawn from a fixed, low-cardinality set. The
    forbidden-label check enforces the spec's bounded-label rule.
    """

    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)

    def has_forbidden_labels(self) -> bool:
        return bool(FORBIDDEN_LABEL_KEYS & self.labels.keys())


def heartbeat_intervals_ok(heartbeats: list[HeartbeatMarker], max_gap_seconds: float = 30.0) -> bool:
    """Return True if no gap between consecutive heartbeats exceeds max_gap."""
    if len(heartbeats) < 2:
        return True
    times = sorted(h.emitted_at for h in heartbeats)
    for prev, curr in zip(times, times[1:]):
        if (curr - prev).total_seconds() > max_gap_seconds:
            return False
    return True


def sanitize_error(exc: BaseException) -> str:
    """Map a raw exception to a stable reason code.

    Per all four specs: error codes MUST be stable strings, not raw exceptions.
    The raw exception message may contain secrets/formulas and is unbounded.
    """
    return type(exc).__name__.lower()


def contains_sensitive_value(blob: str) -> bool:
    """Return True if a serialized blob leaks a sensitive fragment."""
    lowered = blob.lower()
    return any(frag in lowered for frag in SENSITIVE_VALUE_FRAGMENTS)


# ---------------------------------------------------------------------------
# Per-collection required-event registries (verbatim from the specs)
# ---------------------------------------------------------------------------

MATE_REQUIRED_EVENTS: frozenset[str] = frozenset(
    {
        "materials.requirements.normalized",
        "materials.candidate.screened",
        "materials.candidate.ranked",
        "materials.process.planned",
        "materials.joining.planned",
        "materials.model.created",
        "materials.model.verified",
        "materials.test.requested",
        "materials.test.recorded",
        "materials.uncertainty.updated",
        "materials.route.promoted",
        "materials.route.held",
        "materials.route.reverted",
    }
)

GRC_REQUIRED_EVENTS: frozenset[str] = frozenset(
    {
        "git.repo.assessed",
        "git.operation.planned",
        "git.operation.applied",
        "git.operation.recovered",
        "helper.candidate.discovered",
        "helper.candidate.selected",
        "helper.generated",
        "release.plan.created",
        "release.gate.completed",
        "release.artifact.built",
        "release.artifact.verified",
        "release.deployment.stage",
        "release.rollback",
        "release.page.verified",
    }
)


# ---------------------------------------------------------------------------
# Per-collection minimal event dataclass stubs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MaterialsEvent(ExpertEvent):
    """MATE §10 — adds material_id, lot_id, evidence_uri."""

    material_id: str | None = None
    lot_id: str | None = None
    evidence_uri: str | None = None


@dataclass(frozen=True)
class ChemistryEvent(ExpertEvent):
    """CHEM §12 — adds entity_id, hazard_tier, approval_state."""

    entity_id: str | None = None
    hazard_tier: str | None = None
    approval_state: str | None = None


@dataclass(frozen=True)
class AIMLEvent(ExpertEvent):
    """AIML §13 — adds run_id, tenant_id, model_id, accelerator, cost_usd."""

    run_id: str | None = None
    tenant_id: str | None = None
    model_id: str | None = None
    accelerator: str | None = None
    cost_usd: float = 0.0


@dataclass(frozen=True)
class GitReleaseEvent(ExpertEvent):
    """GRC §9 — adds release_id, operation_id, repo_id, source_sha, outcome."""

    release_id: str | None = None
    operation_id: str | None = None
    repo_id: str | None = None
    source_sha: str | None = None
    outcome: str | None = None


# ===========================================================================
# Tests
# ===========================================================================


class TestEventCorrelationFields:
    """Each collection's events include trace_id, request_id, timestamps."""

    def test_materials_event_has_correlation_and_timestamps(self) -> None:
        start = datetime.now(UTC)
        end = start + timedelta(seconds=1.5)
        ev = MaterialsEvent(
            event_type="materials.candidate.ranked",
            trace_id="trace-mate-1",
            request_id="req-mate-1",
            started_at=start,
            ended_at=end,
            material_id="mat-42",
        )
        assert ev.trace_id == "trace-mate-1"
        assert ev.request_id == "req-mate-1"
        assert ev.started_at == start
        assert ev.ended_at == end
        assert ev.is_complete()
        assert ev.duration_seconds() == pytest.approx(1.5)

    def test_chemistry_event_has_correlation_and_timestamps(self) -> None:
        start = datetime.now(UTC)
        ev = ChemistryEvent(
            event_type="chem.safety.classified",
            trace_id="trace-chem-1",
            request_id="req-chem-1",
            started_at=start,
            entity_id="ent-7",
            hazard_tier="2",
        )
        assert ev.trace_id == "trace-chem-1"
        assert ev.request_id == "req-chem-1"
        assert ev.started_at == start
        assert ev.entity_id == "ent-7"
        assert ev.hazard_tier == "2"
        assert not ev.is_complete()

    def test_aiml_event_has_correlation_and_timestamps(self) -> None:
        start = datetime.now(UTC)
        ev = AIMLEvent(
            event_type="aiml.training.checkpoint",
            trace_id="trace-aiml-1",
            request_id="req-aiml-1",
            started_at=start,
            run_id="run-9",
            tenant_id="tenant-a",
            model_id="llm-v3",
            cost_usd=1.25,
        )
        assert ev.trace_id == "trace-aiml-1"
        assert ev.request_id == "req-aiml-1"
        assert ev.started_at == start
        assert ev.run_id == "run-9"
        assert ev.tenant_id == "tenant-a"
        assert ev.cost_usd == 1.25

    def test_grc_event_has_correlation_and_timestamps(self) -> None:
        start = datetime.now(UTC)
        end = start + timedelta(seconds=10)
        ev = GitReleaseEvent(
            event_type="release.gate.completed",
            trace_id="trace-grc-1",
            request_id="req-grc-1",
            started_at=start,
            ended_at=end,
            release_id="rel-5",
            operation_id="op-3",
            repo_id="repo-1",
            source_sha="abc1234",
            outcome="success",
        )
        assert ev.trace_id == "trace-grc-1"
        assert ev.request_id == "req-grc-1"
        assert ev.started_at == start
        assert ev.ended_at == end
        assert ev.outcome == "success"
        assert ev.is_complete()


class TestHeartbeatEmission:
    """Long operations emit heartbeat markers at least every 30 seconds."""

    def test_heartbeat_sequence_under_30s_is_valid(self) -> None:
        base = datetime.now(UTC)
        beats = [HeartbeatMarker("t", "r", "op", base + timedelta(seconds=i * 20)) for i in range(5)]
        assert heartbeat_intervals_ok(beats, max_gap_seconds=30)

    def test_heartbeat_sequence_with_gap_over_30s_is_invalid(self) -> None:
        base = datetime.now(UTC)
        beats = [
            HeartbeatMarker("t", "r", "op", base),
            HeartbeatMarker("t", "r", "op", base + timedelta(seconds=45)),
        ]
        assert not heartbeat_intervals_ok(beats, max_gap_seconds=30)

    def test_heartbeat_marker_carries_phase_and_sequence(self) -> None:
        hb = HeartbeatMarker(
            trace_id="t",
            request_id="r",
            operation_id="op",
            emitted_at=datetime.now(UTC),
            phase="solving",
            sequence=3,
        )
        assert hb.phase == "solving"
        assert hb.sequence == 3
        assert hb.trace_id == "t"


class TestBoundedMetricLabels:
    """Metric labels are bounded — no source URLs, artifact digests, etc."""

    def test_clean_metric_labels_pass(self) -> None:
        sample = MetricSample(
            name="materials.solver.failure_class",
            value=1.0,
            labels={"solver": "fenics", "failure_class": "non_convergence"},
        )
        assert not sample.has_forbidden_labels()

    @pytest.mark.parametrize(
        "bad_key,value",
        [
            ("source_url", "https://example.com/data.csv"),
            ("artifact_digest", "sha256:abcd1234"),
            ("formula", "C8H10N4O2"),
            ("structure", "CC(=O)O"),
            ("smiles", "CCO"),
            ("lot_id", "LOT-2024-001"),
            ("protocol_text", "dissolve 5g in 100ml"),
            ("sample_name", "sample-A"),
            ("prompt", "translate this"),
        ],
    )
    def test_forbidden_label_keys_detected(self, bad_key: str, value: str) -> None:
        sample = MetricSample(
            name="chem.metric",
            value=1.0,
            labels={bad_key: value},
        )
        assert sample.has_forbidden_labels(), f"label key {bad_key!r} must be flagged as forbidden"

    def test_mixed_clean_and_forbidden_labels_flagged(self) -> None:
        sample = MetricSample(
            name="aiml.spend",
            value=0.5,
            labels={"tenant": "t1", "artifact_digest": "sha256:deadbeef"},
        )
        assert sample.has_forbidden_labels()


class TestStableErrorCodes:
    """Error codes are stable strings, not raw exceptions."""

    def test_sanitize_error_returns_class_name(self) -> None:
        code = sanitize_error(ValueError("boom"))
        assert code == "valueerror"

    def test_sanitize_error_does_not_leak_message(self) -> None:
        code = sanitize_error(RuntimeError("secret=password123"))
        assert "secret" not in code
        assert "password123" not in code
        assert code == "runtimeerror"

    def test_event_reason_code_is_optional_string(self) -> None:
        ev = MaterialsEvent(
            event_type="materials.route.held",
            trace_id="t",
            request_id="r",
            started_at=datetime.now(UTC),
            reason_code="inspection_escape",
        )
        assert ev.reason_code == "inspection_escape"
        assert isinstance(ev.reason_code, str)


class TestNoSensitiveMaterialInLogs:
    """Secrets/formulas/structures are not in logs or metrics."""

    def test_clean_serialized_blob_passes(self) -> None:
        assert not contains_sensitive_value('{"event": "materials.route.promoted", "material_id": "mat-1"}')

    def test_blob_with_secret_fragment_flagged(self) -> None:
        assert contains_sensitive_value('{"token": "abc"}')
        assert contains_sensitive_value("api_key=xyz")
        assert contains_sensitive_value("password=hunter2")


class TestRequiredEventRegistries:
    """The per-collection required-event lists match the specs verbatim."""

    def test_mate_required_events_cover_lifecycle(self) -> None:
        # Spot-check a few critical lifecycle phases from MATE §10.
        assert "materials.requirements.normalized" in MATE_REQUIRED_EVENTS
        assert "materials.model.verified" in MATE_REQUIRED_EVENTS
        assert "materials.route.promoted" in MATE_REQUIRED_EVENTS
        assert "materials.route.reverted" in MATE_REQUIRED_EVENTS
        assert len(MATE_REQUIRED_EVENTS) >= 13

    def test_grc_required_events_cover_lifecycle(self) -> None:
        # Spot-check the release-pipeline lifecycle from GRC §9.
        assert "git.repo.assessed" in GRC_REQUIRED_EVENTS
        assert "release.artifact.verified" in GRC_REQUIRED_EVENTS
        assert "release.rollback" in GRC_REQUIRED_EVENTS
        assert "release.page.verified" in GRC_REQUIRED_EVENTS
        assert len(GRC_REQUIRED_EVENTS) >= 14
