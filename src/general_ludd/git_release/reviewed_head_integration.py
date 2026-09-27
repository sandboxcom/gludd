"""Fail-closed contract for integrating independently reviewed Git heads.

The contract deliberately separates cheap attribution from expensive release
validation. Reviewed heads are applied one at a time so a conflict and its
review receipt remain attributable to one source. The resulting tip receives
one unioned focused validation and one exact final gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "AppliedHeadEvidence",
    "ExactGateEvidence",
    "FocusedValidationEvidence",
    "HeadApplicationMode",
    "IntegrationStep",
    "IntegrationStepKind",
    "ReviewedHead",
    "ReviewedHeadIntegrationPlan",
    "ReviewedHeadIntegrationReceipt",
    "build_reviewed_head_integration_plan",
]

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}\Z")
_COMMAND_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/=-]{0,255}\Z")
_MAX_REVIEWED_HEADS = 32
_MAX_FOCUSED_VALIDATIONS = 128


def _require_match(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _require_sha(value: object, label: str) -> str:
    return _require_match(value, _SHA, label)


def _require_sha256(value: object, label: str) -> str:
    return _require_match(value, _SHA256, label)


def _require_command_id(value: object, label: str) -> str:
    return _require_match(value, _COMMAND_ID, label)


def _is_full_gate(command_id: str) -> bool:
    return command_id == "gate" or command_id.startswith(
        ("gate:", "gate-all", "gate-full", "ci-gate-exact")
    )


def _stable_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


class IntegrationStepKind(StrEnum):
    """Only the three legal phases of reviewed-head integration."""

    APPLY_ONE_HEAD = "apply_one_head"
    FOCUSED_VALIDATION = "focused_validation"
    EXACT_GATE = "exact_gate"


class HeadApplicationMode(StrEnum):
    """Auditable single-head application shapes."""

    CHERRY_PICK = "cherry_pick"
    MERGE_TWO_PARENT = "merge_two_parent"


@dataclass(frozen=True, slots=True)
class ReviewedHead:
    """One reviewed source head and its narrow validation provenance."""

    source_ref: str
    source_sha: str
    reviewed_base_sha: str
    review_receipt_sha256: str
    focused_validation_ids: tuple[str, ...]
    application_mode: HeadApplicationMode

    def __post_init__(self) -> None:
        """Validate reviewed-head identity and focused-check provenance."""
        _require_match(self.source_ref, _SOURCE_REF, "source_ref")
        if any(
            fragment in self.source_ref
            for fragment in ("..", "//", "@{", "\\")
        ) or self.source_ref.endswith(("/", ".", ".lock")):
            raise ValueError("source_ref is invalid")
        _require_sha(self.source_sha, "source_sha")
        _require_sha(self.reviewed_base_sha, "reviewed_base_sha")
        _require_sha256(self.review_receipt_sha256, "review_receipt_sha256")
        if not isinstance(self.application_mode, HeadApplicationMode):
            raise ValueError("application_mode is invalid")
        if (
            not isinstance(self.focused_validation_ids, tuple)
            or not self.focused_validation_ids
            or len(self.focused_validation_ids) > _MAX_FOCUSED_VALIDATIONS
        ):
            raise ValueError("focused validations must be a bounded nonempty tuple")
        for command_id in self.focused_validation_ids:
            _require_command_id(command_id, "focused_validation_id")
            if _is_full_gate(command_id):
                raise ValueError("per-head full gates are forbidden")
        if len(set(self.focused_validation_ids)) != len(self.focused_validation_ids):
            raise ValueError("focused validation IDs must be distinct per head")


@dataclass(frozen=True, slots=True)
class IntegrationStep:
    """One canonical integration-plan step.

    Application steps carry a tuple solely so untrusted plans can be rejected
    explicitly when they attempt a multi-parent octopus application.
    """

    kind: IntegrationStepKind
    ordinal: int
    source_shas: tuple[str, ...]
    command_ids: tuple[str, ...]
    application_mode: HeadApplicationMode | None

    def __post_init__(self) -> None:
        """Validate the shape allowed for this integration-step kind."""
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 1:
            raise ValueError("integration step ordinal must be a positive integer")
        if not isinstance(self.source_shas, tuple) or not isinstance(self.command_ids, tuple):
            raise ValueError("integration step sources and commands must be tuples")
        for source_sha in self.source_shas:
            _require_sha(source_sha, "step source_sha")
        for command_id in self.command_ids:
            _require_command_id(command_id, "step command_id")

        if self.kind is IntegrationStepKind.APPLY_ONE_HEAD:
            if len(self.source_shas) != 1:
                raise ValueError("apply steps require exactly one reviewed head")
            if self.command_ids:
                raise ValueError("apply steps cannot execute validation commands")
            if not isinstance(self.application_mode, HeadApplicationMode):
                raise ValueError("apply steps require one application mode")
            return
        if self.application_mode is not None:
            raise ValueError("validation and gate steps cannot select an application mode")
        if self.source_shas:
            raise ValueError("validation and gate steps cannot apply source heads")
        if self.kind is IntegrationStepKind.FOCUSED_VALIDATION:
            if not self.command_ids:
                raise ValueError("focused validation requires at least one command")
            if any(_is_full_gate(command_id) for command_id in self.command_ids):
                raise ValueError("per-head full gates are forbidden")
            return
        if self.kind is IntegrationStepKind.EXACT_GATE:
            if len(self.command_ids) != 1 or not _is_full_gate(self.command_ids[0]):
                raise ValueError("exact gate step requires one full-gate command ID")
            return
        raise ValueError("unsupported integration step kind")


@dataclass(frozen=True, slots=True)
class ReviewedHeadIntegrationPlan:
    """Canonical sequence for applying reviewed heads and validating the tip."""

    base_sha: str
    heads: tuple[ReviewedHead, ...]
    focused_validation_ids: tuple[str, ...]
    exact_gate_id: str
    steps: tuple[IntegrationStep, ...]

    def __post_init__(self) -> None:
        """Validate the canonical apply, focus, then exact-gate sequence."""
        _require_sha(self.base_sha, "base_sha")
        if (
            not isinstance(self.heads, tuple)
            or not self.heads
            or len(self.heads) > _MAX_REVIEWED_HEADS
        ):
            raise ValueError("reviewed heads must be a bounded nonempty tuple")
        identities = tuple(
            (head.source_ref, head.source_sha, head.review_receipt_sha256)
            for head in self.heads
        )
        if (
            len({ref for ref, _, _ in identities}) != len(identities)
            or len({sha for _, sha, _ in identities}) != len(identities)
            or len({digest for _, _, digest in identities}) != len(identities)
        ):
            raise ValueError("integration requires distinct reviewed heads and receipts")
        if any(head.source_sha == self.base_sha for head in self.heads):
            raise ValueError("a reviewed head cannot equal the integration base")

        expected_focused = _stable_unique(
            tuple(
                command_id
                for head in self.heads
                for command_id in head.focused_validation_ids
            )
        )
        if self.focused_validation_ids != expected_focused:
            raise ValueError("focused validation must be the stable union of every head")
        _require_command_id(self.exact_gate_id, "exact_gate_id")
        if not _is_full_gate(self.exact_gate_id):
            raise ValueError("exact_gate_id must identify the final full gate")

        expected_steps = (
            *(
                IntegrationStep(
                    kind=IntegrationStepKind.APPLY_ONE_HEAD,
                    ordinal=index,
                    source_shas=(head.source_sha,),
                    command_ids=(),
                    application_mode=head.application_mode,
                )
                for index, head in enumerate(self.heads, start=1)
            ),
            IntegrationStep(
                kind=IntegrationStepKind.FOCUSED_VALIDATION,
                ordinal=len(self.heads) + 1,
                source_shas=(),
                command_ids=expected_focused,
                application_mode=None,
            ),
            IntegrationStep(
                kind=IntegrationStepKind.EXACT_GATE,
                ordinal=len(self.heads) + 2,
                source_shas=(),
                command_ids=(self.exact_gate_id,),
                application_mode=None,
            ),
        )
        if self.steps != expected_steps:
            raise ValueError(
                "reviewed heads must follow the canonical integration sequence"
            )


def build_reviewed_head_integration_plan(
    *,
    base_sha: str,
    heads: tuple[ReviewedHead, ...],
    exact_gate_id: str,
) -> ReviewedHeadIntegrationPlan:
    """Build the sole legal plan: N single-head applies, focus, exact gate."""
    focused_validation_ids = _stable_unique(
        tuple(
            command_id
            for head in heads
            for command_id in head.focused_validation_ids
        )
    )
    steps = (
        *(
            IntegrationStep(
                kind=IntegrationStepKind.APPLY_ONE_HEAD,
                ordinal=index,
                source_shas=(head.source_sha,),
                command_ids=(),
                application_mode=head.application_mode,
            )
            for index, head in enumerate(heads, start=1)
        ),
        IntegrationStep(
            kind=IntegrationStepKind.FOCUSED_VALIDATION,
            ordinal=len(heads) + 1,
            source_shas=(),
            command_ids=focused_validation_ids,
            application_mode=None,
        ),
        IntegrationStep(
            kind=IntegrationStepKind.EXACT_GATE,
            ordinal=len(heads) + 2,
            source_shas=(),
            command_ids=(exact_gate_id,),
            application_mode=None,
        ),
    )
    return ReviewedHeadIntegrationPlan(
        base_sha=base_sha,
        heads=heads,
        focused_validation_ids=focused_validation_ids,
        exact_gate_id=exact_gate_id,
        steps=steps,
    )


@dataclass(frozen=True, slots=True)
class AppliedHeadEvidence:
    """Two-parent evidence for one reviewed head application."""

    ordinal: int
    source_ref: str
    source_sha: str
    review_receipt_sha256: str
    application_mode: HeadApplicationMode
    before_sha: str
    after_sha: str
    parent_shas: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate one-head application identity and parent attribution."""
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 1:
            raise ValueError("applied-head ordinal must be a positive integer")
        _require_match(self.source_ref, _SOURCE_REF, "source_ref")
        _require_sha(self.source_sha, "source_sha")
        _require_sha256(self.review_receipt_sha256, "review_receipt_sha256")
        if not isinstance(self.application_mode, HeadApplicationMode):
            raise ValueError("application_mode is invalid")
        _require_sha(self.before_sha, "before_sha")
        _require_sha(self.after_sha, "after_sha")
        expected_parents = (
            (self.before_sha,)
            if self.application_mode is HeadApplicationMode.CHERRY_PICK
            else (self.before_sha, self.source_sha)
        )
        if self.parent_shas != expected_parents:
            raise ValueError(
                "reviewed-head application requires exact one-head parent attribution"
            )
        if self.after_sha in {self.before_sha, self.source_sha}:
            raise ValueError("applied-head result must be a new integration commit")


@dataclass(frozen=True, slots=True)
class FocusedValidationEvidence:
    """One bulk validation over the final integrated tip."""

    tip_sha: str
    source_shas: tuple[str, ...]
    command_ids: tuple[str, ...]
    run_count: int
    passed: bool

    def __post_init__(self) -> None:
        """Validate one bulk focused-validation evidence record."""
        _require_sha(self.tip_sha, "focused tip_sha")
        if not self.source_shas or len(set(self.source_shas)) != len(self.source_shas):
            raise ValueError("focused validation source SHAs must be nonempty and distinct")
        for source_sha in self.source_shas:
            _require_sha(source_sha, "focused source_sha")
        if not self.command_ids or len(set(self.command_ids)) != len(self.command_ids):
            raise ValueError("focused validation command IDs must be nonempty and distinct")
        for command_id in self.command_ids:
            _require_command_id(command_id, "focused command_id")
            if _is_full_gate(command_id):
                raise ValueError("per-head full gates are forbidden")
        if isinstance(self.run_count, bool) or not isinstance(self.run_count, int):
            raise ValueError("focused validation run_count must be an integer")
        if not isinstance(self.passed, bool):
            raise ValueError("focused validation passed must be boolean")


@dataclass(frozen=True, slots=True)
class ExactGateEvidence:
    """The sole full gate, bound to the final integrated tip."""

    tip_sha: str
    command_id: str
    run_count: int
    passed: bool

    def __post_init__(self) -> None:
        """Validate the sole exact-gate evidence record."""
        _require_sha(self.tip_sha, "gate tip_sha")
        _require_command_id(self.command_id, "gate command_id")
        if not _is_full_gate(self.command_id):
            raise ValueError("gate command_id must identify a full gate")
        if isinstance(self.run_count, bool) or not isinstance(self.run_count, int):
            raise ValueError("gate run_count must be an integer")
        if not isinstance(self.passed, bool):
            raise ValueError("gate passed must be boolean")


@dataclass(frozen=True, slots=True)
class ReviewedHeadIntegrationReceipt:
    """Successful evidence for the complete reviewed-head integration plan."""

    plan: ReviewedHeadIntegrationPlan
    applied_heads: tuple[AppliedHeadEvidence, ...]
    focused_validation: FocusedValidationEvidence
    exact_gate: ExactGateEvidence

    def __post_init__(self) -> None:
        """Bind applications and validation evidence to the reviewed plan."""
        if len(self.applied_heads) != len(self.plan.heads):
            raise ValueError("receipt requires one application per reviewed head")

        expected_before = self.plan.base_sha
        for ordinal, (head, evidence) in enumerate(
            zip(self.plan.heads, self.applied_heads, strict=True),
            start=1,
        ):
            if evidence.ordinal != ordinal or (
                evidence.source_ref,
                evidence.source_sha,
            ) != (head.source_ref, head.source_sha):
                raise ValueError("receipt does not preserve reviewed-head order")
            if evidence.review_receipt_sha256 != head.review_receipt_sha256:
                raise ValueError("applied head does not match its review receipt")
            if evidence.application_mode is not head.application_mode:
                raise ValueError("applied head does not match its planned mode")
            if evidence.before_sha != expected_before:
                raise ValueError("applied-head integration chain is discontinuous")
            expected_before = evidence.after_sha

        final_sha = expected_before
        expected_sources = tuple(head.source_sha for head in self.plan.heads)
        if self.focused_validation.source_shas != expected_sources:
            raise ValueError("focused validation must cover all reviewed heads")
        if self.focused_validation.command_ids != self.plan.focused_validation_ids:
            raise ValueError("focused validation does not match the planned union")
        if self.focused_validation.tip_sha != final_sha:
            raise ValueError("focused validation must target the final integrated tip")
        if self.focused_validation.run_count != 1:
            raise ValueError("focused validation must run exactly once")
        if not self.focused_validation.passed:
            raise ValueError("focused validation must pass")

        if self.exact_gate.tip_sha != final_sha:
            raise ValueError("exact gate must target the final integrated tip")
        if self.exact_gate.command_id != self.plan.exact_gate_id:
            raise ValueError("exact gate does not match the planned command")
        if self.exact_gate.run_count != 1:
            raise ValueError("exact gate must run exactly once")
        if not self.exact_gate.passed:
            raise ValueError("exact gate must pass")

    @property
    def final_sha(self) -> str:
        """Return the exact integrated tip covered by both validations."""
        return self.applied_heads[-1].after_sha
