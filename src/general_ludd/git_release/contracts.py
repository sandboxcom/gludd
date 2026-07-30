"""Data contracts for the Git Release Captain Expert (spec GRC-001 §5).

All contracts are versioned, JSON-serializable pydantic models. The spec
requires:

- "Unknown required fields SHALL fail validation" — every spec-named field
  without an explicit default is required; omitting it raises ValidationError.
- "Additive optional fields SHALL preserve backward compatibility" — unknown
  input keys are silently dropped (``extra="ignore"``) so a newer producer can
  emit additional fields without breaking an older consumer.

The nested record shapes (upstreams, worktrees, dirty_paths, gates, artifacts,
provenance, deployment, rollback, release_page, …) mirror spec §5.1–§5.4
verbatim. Consumers import the top-level models; the underscore-prefixed
sub-records are module-private but fully introspectable via pydantic.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "HelperAuthority",
    "HelperCandidate",
    "ReleasePlan",
    "ReleaseVerdict",
    "ReleaseVerdictState",
    "RepoEvidence",
]

_SCHEMA_VERSION = "1"
# A full git commit SHA — 40 lowercase hex chars. Empty/short/mixed-case SHAs
# are rejected so downstream planners cannot accidentally compare truncated SHAs.
_SHA_PATTERN = r"^[0-9a-f]{40}$"
# Semver-ish: optional leading "v", MAJOR.MINOR.PATCH, optional prerelease and
# build metadata. The captured group becomes the normalized form (no "v").
_VERSION_RE = re.compile(r"^v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)$")
# RFC3339 timestamp prefix (date-time). Evidence collectors emit UTC ISO-8601.
_RFC3339_RE = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"


class _Record(BaseModel):
    """Base for every contract record: tolerate additive future fields.

    Pydantic v2 defaults to ``extra="ignore"``; we set it explicitly so the
    forward-compatibility guarantee is structural, not incidental.
    """

    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# RepoEvidence (spec §5.1)
# ---------------------------------------------------------------------------


class _Upstream(_Record):
    local_ref: str = Field(min_length=1)
    remote_ref: str = Field(min_length=1)
    ahead: int = Field(ge=0)
    behind: int = Field(ge=0)


class _Worktree(_Record):
    path: str = Field(min_length=1)
    branch: str | None = None
    head_sha: str
    dirty: bool


class _Operation(_Record):
    kind: str = Field(min_length=1)
    state: str = Field(min_length=1)
    recovery_command_id: str | None = None


class _DirtyPath(_Record):
    path: str = Field(min_length=1)
    index_state: str | None = None
    worktree_state: str | None = None
    untracked: bool = False


class _Policy(_Record):
    source: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    text_digest: str = Field(min_length=1)


class RepoEvidence(_Record):
    """Read-only snapshot of repository facts (spec §5.1).

    Captures HEAD SHA, branch, upstream divergence, linked worktrees, in-flight
    git operations, dirty paths, and policy sources. The record is consumed by
    planners to gate mutating actions (GRC-SEC-001) and to fail closed when
    required evidence is missing (GRC-SEC-004).
    """

    schema_version: str = Field(default=_SCHEMA_VERSION)
    repo_root: str = Field(min_length=1)
    head_sha: str = Field(pattern=_SHA_PATTERN)
    branch: str | None = None
    upstreams: list[_Upstream] = Field(default_factory=list)
    worktrees: list[_Worktree] = Field(default_factory=list)
    operations: list[_Operation] = Field(default_factory=list)
    dirty_paths: list[_DirtyPath] = Field(default_factory=list)
    policies: list[_Policy] = Field(default_factory=list)
    evidence_time: str = Field(pattern=_RFC3339_RE)


# ---------------------------------------------------------------------------
# HelperCandidate (spec §5.2)
# ---------------------------------------------------------------------------


class HelperAuthority(StrEnum):
    """Authority ranking for helper selection (spec §4.3).

    Serialized value matches the spec text exactly ("ci-used" preserves the
    dash). ``rank()`` returns 0 for the highest authority; lower rank wins.
    """

    REPOSITORY = "repository"
    CI_USED = "ci-used"
    ECOSYSTEM = "ecosystem"
    GENERATED = "generated"

    def rank(self) -> int:
        order = {
            HelperAuthority.REPOSITORY: 0,
            HelperAuthority.CI_USED: 1,
            HelperAuthority.ECOSYSTEM: 2,
            HelperAuthority.GENERATED: 3,
        }
        return order[self]


class _HelperInput(_Record):
    name: str = Field(min_length=1)
    required: bool = False
    secret: bool = False
    default: Any = None


class _HelperOutput(_Record):
    name: str = Field(min_length=1)
    path_or_channel: str = Field(min_length=1)
    digestible: bool = False


class _ScoreEvidence(_Record):
    criterion: str = Field(min_length=1)
    value: Any
    source: str = Field(min_length=1)


class HelperCandidate(_Record):
    """A discovered or generated release/build/deploy helper (spec §5.2).

    ``score`` is bounded 0..100 and each score component MUST be backed by a
    ``score_evidence`` entry naming the criterion, value, and source —
    popularity alone SHALL NOT authorize a tool (spec §4.3).
    """

    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    authority: HelperAuthority
    invocation_id: str = Field(min_length=1)
    inputs: list[_HelperInput] = Field(default_factory=list)
    outputs: list[_HelperOutput] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)
    supports_dry_run: bool = False
    supports_rollback: bool = False
    observability: list[str] = Field(default_factory=list)
    score: int = Field(ge=0, le=100)
    score_evidence: list[_ScoreEvidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ReleasePlan (spec §5.3)
# ---------------------------------------------------------------------------


class _Gate(_Record):
    id: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    timeout_s: int = Field(gt=0)
    success_contract: str = Field(min_length=1)


class _Artifact(_Record):
    id: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    format: str = Field(min_length=1)
    expected_name: str = Field(min_length=1)
    verification: str = Field(min_length=1)


class _Provenance(_Record):
    sbom: Any = None
    signature: Any = None
    attestation: Any = None
    builder_identity: str = Field(min_length=1)


class _Deployment(_Record):
    strategy: str = Field(min_length=1)
    stages: list[Any] = Field(default_factory=list)
    health_gates: list[Any] = Field(default_factory=list)
    pause_points: list[Any] = Field(default_factory=list)


class _Rollback(_Record):
    trigger: str = Field(min_length=1)
    target: str = Field(min_length=1)
    data_compatibility: str = Field(min_length=1)
    command_id: str = Field(min_length=1)


class _Approval(_Record):
    scope: str = Field(min_length=1)
    approver_class: str = Field(min_length=1)
    state: str = Field(min_length=1)
    expires_at: str | None = None


class ReleasePlan(_Record):
    """A derived release plan (spec §5.3).

    ``version`` is normalized: a leading ``v`` is stripped so downstream
    comparisons operate on the canonical form. ``provenance.builder_identity``
    is required (non-null) — anonymous artifacts cannot satisfy GRC-SEC-005.
    """

    release_id: str = Field(min_length=1)
    source_sha: str = Field(pattern=_SHA_PATTERN)
    version: str = Field(min_length=1)
    change_set: list[str] = Field(default_factory=list)
    required_gates: list[_Gate] = Field(default_factory=list)
    artifacts: list[_Artifact] = Field(default_factory=list)
    provenance: _Provenance
    deployment: _Deployment
    rollback: _Rollback
    approvals: list[_Approval] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def _normalize_version(cls, v: str) -> str:
        match = _VERSION_RE.match(v)
        if not match:
            raise ValueError(f"not a valid semantic version: {v!r}")
        return match.group(1)


# ---------------------------------------------------------------------------
# ReleaseVerdict (spec §5.4)
# ---------------------------------------------------------------------------


class ReleaseVerdictState(StrEnum):
    """High-level release outcome (spec §5.4 state field)."""

    BLOCKED = "blocked"
    READY = "ready"
    DEPLOYING = "deploying"
    ROLLED_BACK = "rolled_back"
    RELEASED = "released"


_ALLOWED_TRANSITIONS: dict[ReleaseVerdictState, frozenset[ReleaseVerdictState]] = {
    ReleaseVerdictState.BLOCKED: frozenset({ReleaseVerdictState.READY}),
    ReleaseVerdictState.READY: frozenset({ReleaseVerdictState.DEPLOYING, ReleaseVerdictState.BLOCKED}),
    ReleaseVerdictState.DEPLOYING: frozenset({ReleaseVerdictState.RELEASED, ReleaseVerdictState.ROLLED_BACK}),
    # RELEASED is terminal: a shipped release cannot transition out; a new
    # release gets a new verdict record.
    ReleaseVerdictState.RELEASED: frozenset(),
    ReleaseVerdictState.ROLLED_BACK: frozenset({ReleaseVerdictState.READY, ReleaseVerdictState.BLOCKED}),
}


class _GateResult(_Record):
    id: str = Field(min_length=1)
    state: str = Field(min_length=1)
    evidence_uri: str | None = None
    digest: str | None = None


class _ArtifactResult(_Record):
    id: str = Field(min_length=1)
    digest: str | None = None
    signature_state: str = Field(min_length=1)
    install_state: str = Field(min_length=1)


class _DeploymentResult(_Record):
    stage: str = Field(min_length=1)
    health: str = Field(min_length=1)
    traffic_percent: int = Field(ge=0, le=100)
    evidence_uri: str | None = None


class _ReleasePage(_Record):
    url: str
    asset_names: list[str] = Field(default_factory=list)
    asset_digests: dict[str, str] = Field(default_factory=dict)


class ReleaseVerdict(_Record):
    """Machine-verifiable release outcome (spec §5.4).

    The state machine is encoded in ``_ALLOWED_TRANSITIONS`` and exposed via
    ``can_transition_to``. ``reasons`` carries stable reason codes (e.g.
    ``GRC-SEC-004``) so an operator can diagnose a ``blocked`` verdict without
    re-reading the gate output.
    """

    release_id: str = Field(min_length=1)
    source_sha: str = Field(pattern=_SHA_PATTERN)
    tag_target_sha: str = Field(pattern=_SHA_PATTERN)
    gate_results: list[_GateResult] = Field(default_factory=list)
    artifact_results: list[_ArtifactResult] = Field(default_factory=list)
    deployment_results: list[_DeploymentResult] = Field(default_factory=list)
    release_page: _ReleasePage
    state: ReleaseVerdictState
    reasons: list[str] = Field(default_factory=list)

    def can_transition_to(self, new_state: ReleaseVerdictState) -> bool:
        return new_state in _ALLOWED_TRANSITIONS.get(self.state, frozenset())
