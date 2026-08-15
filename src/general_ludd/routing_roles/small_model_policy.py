"""Fail-closed capability policy for constrained and locally served models.

The policy deliberately does not infer capability from a model name, parameter
count, provider, or price.  A model may receive a bounded task only after a
deterministic local evaluation suite proves the exact task-kind, role,
collection, and acceptance contract.  High-impact work remains ineligible even
when a caller supplies otherwise-valid evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from general_ludd.schemas.benchmark import TaskRole

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_TASK_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_COLLECTION_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_CHECK_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _stable_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _require_pattern(name: str, value: str, pattern: re.Pattern[str]) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{name} has an invalid format")


def _require_digest(name: str, value: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _validate_checks(checks: Collection[str]) -> None:
    if not checks:
        raise ValueError("acceptance checks must not be empty")
    for check in checks:
        _require_pattern("acceptance check", check, _CHECK_RE)


class TaskImpact(StrEnum):
    """Observable effects a delegated task may have."""

    READ_SOURCE = "read_source"
    WRITE_ARTIFACT = "write_artifact"
    MUTATE_REPOSITORY = "mutate_repository"
    EXECUTE_COMMAND = "execute_command"
    NETWORK_WRITE = "network_write"
    CREDENTIAL_ACCESS = "credential_access"
    DEPLOYMENT = "deployment"
    RELEASE = "release"
    SECURITY_DECISION = "security_decision"


FORBIDDEN_IMPACTS = frozenset(
    {
        TaskImpact.MUTATE_REPOSITORY,
        TaskImpact.EXECUTE_COMMAND,
        TaskImpact.NETWORK_WRITE,
        TaskImpact.CREDENTIAL_ACCESS,
        TaskImpact.DEPLOYMENT,
        TaskImpact.RELEASE,
        TaskImpact.SECURITY_DECISION,
    }
)
_BOUNDED_IMPACTS = frozenset({TaskImpact.READ_SOURCE, TaskImpact.WRITE_ARTIFACT})


def _canonical_role(role: object) -> TaskRole:
    """Return the canonical TaskRole for a member, rejecting raw strings.

    The package is importable from two roots (``src/`` and the installed
    distribution), which can materialize two distinct ``TaskRole`` class
    objects; identity checks then fail even for genuine members. Members
    from a foreign class copy are canonicalized by their ``.value`` while
    raw strings and unknown values stay fail-closed.
    """
    if isinstance(role, str):
        raise ValueError("role must be a TaskRole value")
    value = getattr(role, "value", None)
    if isinstance(value, str):
        try:
            return TaskRole(value)
        except ValueError:
            pass
    raise ValueError("role must be a TaskRole value")


@dataclass(frozen=True)
class TaskContract:
    """Static eligibility contract for one bounded task kind."""

    task_kind: str
    allowed_roles: frozenset[TaskRole]
    allowed_impacts: frozenset[TaskImpact]
    required_acceptance_checks: frozenset[str]

    def __post_init__(self) -> None:
        """Validate the contract's fields fail-closed."""
        _require_pattern("task_kind", self.task_kind, _TASK_KIND_RE)
        if not isinstance(self.allowed_roles, frozenset) or not all(
            isinstance(role, TaskRole) for role in self.allowed_roles
        ):
            raise ValueError("allowed_roles must be a frozenset of TaskRole values")
        if not self.allowed_roles:
            raise ValueError("allowed_roles must not be empty")
        if not isinstance(self.allowed_impacts, frozenset) or not all(
            isinstance(impact, TaskImpact) for impact in self.allowed_impacts
        ):
            raise ValueError("allowed_impacts must be a frozenset of TaskImpact values")
        if not self.allowed_impacts:
            raise ValueError("allowed_impacts must not be empty")
        if self.allowed_impacts & FORBIDDEN_IMPACTS:
            raise ValueError("task contracts cannot grant high-impact operations")
        _validate_checks(self.required_acceptance_checks)


def _contract(
    task_kind: str,
    role: TaskRole,
    *required_checks: str,
) -> TaskContract:
    return TaskContract(
        task_kind=task_kind,
        allowed_roles=frozenset({role}),
        allowed_impacts=_BOUNDED_IMPACTS,
        required_acceptance_checks=frozenset(required_checks),
    )


DEFAULT_TASK_CONTRACTS: Mapping[str, TaskContract] = MappingProxyType(
    {
        "bounded_enumeration": _contract(
            "bounded_enumeration",
            TaskRole.ENUMERATOR,
            "coverage_bounded",
            "no_duplicates",
            "schema_valid",
        ),
        "coding": _contract(
            "coding",
            TaskRole.CODER,
            "syntax_valid",
            "import_ok",
            "run_without_crash",
        ),
        "context_compaction": _contract(
            "context_compaction",
            TaskRole.COMPACTOR,
            "facts_preserved",
            "token_budget_met",
            "schema_valid",
        ),
        "documentation_draft": _contract(
            "documentation_draft",
            TaskRole.EDITOR,
            "facts_traceable",
            "links_valid",
            "schema_valid",
        ),
        "failure_classification": _contract(
            "failure_classification",
            TaskRole.REVIEWER,
            "evidence_cited",
            "label_in_taxonomy",
            "schema_valid",
        ),
        "format_normalization": _contract(
            "format_normalization",
            TaskRole.EDITOR,
            "idempotent",
            "schema_valid",
            "semantic_equivalence",
        ),
        "game_logic": _contract(
            "game_logic",
            TaskRole.CODER,
            "lifecycle_initial_state",
            "lifecycle_start",
            "lifecycle_restart",
            "lifecycle_game_over",
        ),
        "schema_extraction": _contract(
            "schema_extraction",
            TaskRole.EDITOR,
            "all_required_fields",
            "schema_valid",
            "source_traceable",
        ),
    }
)


@dataclass(frozen=True)
class PolicyConfig:
    """Operator-tunable bounds; security exclusions are not configurable."""

    max_attempts: int = 2
    min_evaluation_cases: int = 20

    def __post_init__(self) -> None:
        """Validate the tunable bounds fail-closed."""
        if isinstance(self.max_attempts, bool) or not 1 <= self.max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if isinstance(self.min_evaluation_cases, bool) or not 1 <= self.min_evaluation_cases <= 10_000:
            raise ValueError("min_evaluation_cases must be between 1 and 10000")


@dataclass(frozen=True)
class SmallModelTaskSpec:
    """Content-free task metadata used for authorization and deduplication."""

    task_id: str
    task_kind: str
    role: TaskRole
    collection: str
    input_digest: str
    impacts: frozenset[TaskImpact]
    acceptance_checks: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate the task metadata fail-closed."""
        _require_pattern("task_id", self.task_id, _IDENTIFIER_RE)
        _require_pattern("task_kind", self.task_kind, _TASK_KIND_RE)
        if not isinstance(self.role, TaskRole):
            object.__setattr__(self, "role", _canonical_role(self.role))
        _require_pattern("collection", self.collection, _COLLECTION_RE)
        _require_digest("input_digest", self.input_digest)
        if not isinstance(self.impacts, frozenset) or not all(
            isinstance(impact, TaskImpact) for impact in self.impacts
        ):
            raise ValueError("impacts must be a frozenset of TaskImpact values")
        if not self.impacts:
            raise ValueError("impacts must not be empty")
        if not isinstance(self.acceptance_checks, tuple):
            raise ValueError("acceptance_checks must be an immutable tuple")
        _validate_checks(self.acceptance_checks)
        if len(set(self.acceptance_checks)) != len(self.acceptance_checks):
            raise ValueError("acceptance_checks must not contain duplicates")

    @property
    def acceptance_contract_digest(self) -> str:
        """The digest of the acceptance contract for this task."""
        return _stable_digest(
            {
                "acceptance_checks": sorted(self.acceptance_checks),
                "collection": self.collection,
                "role": self.role.value,
                "task_kind": self.task_kind,
            }
        )

    @property
    def fingerprint(self) -> str:
        """The deduplication fingerprint for this task."""
        return _stable_digest(
            {
                "acceptance_contract_digest": self.acceptance_contract_digest,
                "impacts": sorted(impact.value for impact in self.impacts),
                "input_digest": self.input_digest,
                "task_id": self.task_id,
            }
        )


@dataclass(frozen=True)
class ModelIdentity:
    """Immutable identity of the evaluated weights, runtime, and prompt contract."""

    model_profile_id: str
    model_artifact_digest: str
    runtime_config_digest: str
    prompt_contract_digest: str

    def __post_init__(self) -> None:
        """Validate the model identity fields fail-closed."""
        _require_pattern("model_profile_id", self.model_profile_id, _IDENTIFIER_RE)
        _require_digest("model_artifact_digest", self.model_artifact_digest)
        _require_digest("runtime_config_digest", self.runtime_config_digest)
        _require_digest("prompt_contract_digest", self.prompt_contract_digest)

    @property
    def fingerprint(self) -> str:
        """The deduplication fingerprint for this model identity."""
        return _stable_digest(
            {
                "model_artifact_digest": self.model_artifact_digest,
                "model_profile_id": self.model_profile_id,
                "prompt_contract_digest": self.prompt_contract_digest,
                "runtime_config_digest": self.runtime_config_digest,
            }
        )


@dataclass(frozen=True)
class CapabilityEvidence:
    """Evidence from a deterministic, API-free local capability suite."""

    model_profile_id: str
    model_identity_digest: str
    task_kind: str
    role: TaskRole
    collection: str
    suite_id: str
    suite_revision: str
    acceptance_contract_digest: str
    passed_cases: int
    total_cases: int
    collection_ok: bool
    local_only: bool
    evidence_digest: str

    def __post_init__(self) -> None:
        """Validate the capability evidence fail-closed."""
        _require_pattern("model_profile_id", self.model_profile_id, _IDENTIFIER_RE)
        _require_digest("model_identity_digest", self.model_identity_digest)
        _require_pattern("task_kind", self.task_kind, _TASK_KIND_RE)
        if not isinstance(self.role, TaskRole):
            object.__setattr__(self, "role", _canonical_role(self.role))
        _require_pattern("collection", self.collection, _COLLECTION_RE)
        _require_pattern("suite_id", self.suite_id, _IDENTIFIER_RE)
        _require_pattern("suite_revision", self.suite_revision, _IDENTIFIER_RE)
        _require_digest("acceptance_contract_digest", self.acceptance_contract_digest)
        _require_digest("evidence_digest", self.evidence_digest)
        if not isinstance(self.collection_ok, bool):
            raise ValueError("collection_ok must be a boolean")
        if not isinstance(self.local_only, bool):
            raise ValueError("local_only must be a boolean")
        if isinstance(self.total_cases, bool) or self.total_cases < 1:
            raise ValueError("total_cases must be a positive integer")
        if isinstance(self.passed_cases, bool) or self.passed_cases < 0 or self.passed_cases > self.total_cases:
            raise ValueError("passed_cases must be between 0 and total_cases")


class DispatchAction(StrEnum):
    """Action returned by the policy when authorizing a dispatch."""

    LOCAL = "local"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class DispatchDecision:
    """Authorization outcome for a proposed dispatch."""

    action: DispatchAction
    task_fingerprint: str
    reason: str
    max_attempts: int

    @property
    def approved(self) -> bool:
        """Whether this decision authorizes local dispatch."""
        return self.action is DispatchAction.LOCAL


class CompletionAction(StrEnum):
    """Action returned by the policy when verifying completion evidence."""

    ACCEPT = "accept"
    RETRY = "retry"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class CompletionEvidence:
    """Evidence that a bounded task completed and passed acceptance checks."""

    task_fingerprint: str
    attempt: int
    artifact_digest: str
    acceptance_results: Mapping[str, bool]
    collection_ok: bool
    evidence_digest: str

    def __post_init__(self) -> None:
        """Validate the completion evidence fail-closed."""
        _require_digest("task_fingerprint", self.task_fingerprint)
        _require_digest("artifact_digest", self.artifact_digest)
        _require_digest("evidence_digest", self.evidence_digest)
        if isinstance(self.attempt, bool) or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")
        if not self.acceptance_results:
            raise ValueError("acceptance_results must not be empty")
        _validate_checks(self.acceptance_results)
        if any(not isinstance(value, bool) for value in self.acceptance_results.values()):
            raise ValueError("acceptance_results values must be booleans")
        if not isinstance(self.collection_ok, bool):
            raise ValueError("collection_ok must be a boolean")
        object.__setattr__(self, "acceptance_results", MappingProxyType(dict(self.acceptance_results)))


@dataclass(frozen=True)
class CompletionDecision:
    """Verification outcome for a completion claim."""

    action: CompletionAction
    reason: str
    attempts_used: int


@dataclass
class _Claim:
    task: SmallModelTaskSpec
    model_identity: ModelIdentity
    attempts_used: int = 0
    accepted_evidence_digest: str | None = None
    seen: dict[str, CompletionDecision] = field(default_factory=dict)


class SmallModelTaskPolicy:
    """Authorize bounded work and verify completion before it is accepted.

    Instances are request-scope registries.  Keeping claims in one registry
    prevents duplicate dispatch within that scope; durable callers should
    persist the task fingerprint alongside their own work-item lease.
    """

    def __init__(
        self,
        config: PolicyConfig | None = None,
        contracts: Mapping[str, TaskContract] | None = None,
    ) -> None:
        """Create a request-scope policy with the given config and contracts."""
        self.config = config or PolicyConfig()
        source = contracts if contracts is not None else DEFAULT_TASK_CONTRACTS
        self._contracts = dict(source)
        if any(key != contract.task_kind for key, contract in self._contracts.items()):
            raise ValueError("contract keys must match TaskContract.task_kind")
        self._claims_by_id: dict[str, str] = {}
        self._claims: dict[str, _Claim] = {}

    def authorize(
        self,
        task: SmallModelTaskSpec,
        model_identity: ModelIdentity,
        evidence: Sequence[CapabilityEvidence],
    ) -> DispatchDecision:
        """Return LOCAL only when exact, adequate capability evidence exists."""
        fingerprint = task.fingerprint
        contract = self._contracts.get(task.task_kind)
        if contract is None:
            return self._escalate(fingerprint, "task_kind_not_proven_safe")
        if task.impacts & FORBIDDEN_IMPACTS:
            return self._escalate(fingerprint, "impact_requires_stronger_model")
        if task.role not in contract.allowed_roles:
            return self._escalate(fingerprint, "role_not_allowed_for_task")
        if not task.impacts <= contract.allowed_impacts:
            return self._escalate(fingerprint, "impact_not_allowed_for_task")
        if not contract.required_acceptance_checks <= set(task.acceptance_checks):
            return self._escalate(fingerprint, "acceptance_contract_incomplete")
        if task.task_id in self._claims_by_id:
            return self._escalate(fingerprint, "duplicate_task_claim")

        exact = [
            proof
            for proof in evidence
            if proof.model_profile_id == model_identity.model_profile_id
            and proof.model_identity_digest == model_identity.fingerprint
            and proof.task_kind == task.task_kind
            and proof.role is task.role
            and proof.collection == task.collection
            and proof.acceptance_contract_digest == task.acceptance_contract_digest
        ]
        if not exact:
            return self._escalate(fingerprint, "capability_evidence_missing")

        failure_reasons: list[str] = []
        for proof in exact:
            reason = self._proof_failure_reason(proof)
            if reason is None:
                self._claims_by_id[task.task_id] = fingerprint
                self._claims[fingerprint] = _Claim(task=task, model_identity=model_identity)
                return DispatchDecision(
                    action=DispatchAction.LOCAL,
                    task_fingerprint=fingerprint,
                    reason="capability_proven",
                    max_attempts=self.config.max_attempts,
                )
            failure_reasons.append(reason)
        return self._escalate(fingerprint, failure_reasons[0])

    def record_completion(self, evidence: CompletionEvidence) -> CompletionDecision:
        """Accept, retry, or escalate using exact acceptance evidence."""
        claim = self._claims.get(evidence.task_fingerprint)
        if claim is None:
            return CompletionDecision(CompletionAction.ESCALATE, "task_not_authorized", 0)
        replay = claim.seen.get(evidence.evidence_digest)
        if replay is not None:
            return CompletionDecision(
                replay.action,
                "duplicate_completion_evidence",
                replay.attempts_used,
            )
        if claim.accepted_evidence_digest is not None:
            return CompletionDecision(
                CompletionAction.ESCALATE,
                "task_already_completed",
                claim.attempts_used,
            )
        if evidence.attempt != claim.attempts_used + 1:
            return CompletionDecision(
                CompletionAction.ESCALATE,
                "attempt_out_of_sequence",
                claim.attempts_used,
            )

        claim.attempts_used += 1
        expected_checks = set(claim.task.acceptance_checks)
        supplied_checks = set(evidence.acceptance_results)
        complete = (
            evidence.collection_ok and supplied_checks == expected_checks and all(evidence.acceptance_results.values())
        )
        if complete:
            decision = CompletionDecision(
                CompletionAction.ACCEPT,
                "acceptance_evidence_complete",
                claim.attempts_used,
            )
            claim.accepted_evidence_digest = evidence.evidence_digest
        elif claim.attempts_used < self.config.max_attempts:
            decision = CompletionDecision(
                CompletionAction.RETRY,
                "acceptance_evidence_failed",
                claim.attempts_used,
            )
        else:
            decision = CompletionDecision(
                CompletionAction.ESCALATE,
                "retry_budget_exhausted",
                claim.attempts_used,
            )
        claim.seen[evidence.evidence_digest] = decision
        return decision

    def _proof_failure_reason(self, proof: CapabilityEvidence) -> str | None:
        if not proof.collection_ok:
            return "evaluation_collection_failed"
        if not proof.local_only:
            return "evaluation_not_local"
        if proof.total_cases < self.config.min_evaluation_cases:
            return "evaluation_suite_too_small"
        if proof.passed_cases != proof.total_cases:
            return "evaluation_suite_failed"
        return None

    def _escalate(self, fingerprint: str, reason: str) -> DispatchDecision:
        return DispatchDecision(
            action=DispatchAction.ESCALATE,
            task_fingerprint=fingerprint,
            reason=reason,
            max_attempts=0,
        )


__all__ = [
    "DEFAULT_TASK_CONTRACTS",
    "FORBIDDEN_IMPACTS",
    "CapabilityEvidence",
    "CompletionAction",
    "CompletionDecision",
    "CompletionEvidence",
    "DispatchAction",
    "DispatchDecision",
    "ModelIdentity",
    "PolicyConfig",
    "SmallModelTaskPolicy",
    "SmallModelTaskSpec",
    "TaskContract",
    "TaskImpact",
]
