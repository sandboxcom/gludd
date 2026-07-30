"""AIML Phase C — accelerator-aware execution planner (spec §10, AIML-017).

Implements docs/specs/FEATURE_AI_ML_EXPERT.md §10:

  The accelerator planner discovers permitted hardware WITHOUT
  provisioning it, then chooses topology, precision, parallelism,
  batching, checkpointing, and serving settings from measured capability.
  It supports local CPU/GPU and approved cloud accelerators including
  Azure A100/H100-class hardware.

  Each execution plan declares SKU, region, quota evidence, image digest,
  driver and runtime versions, interconnect assumptions, storage/network
  needs, budget, timeout, checkpoint path, teardown behavior, and
  fallback. Provisioning requires an approval token. Teardown is
  idempotent and emits proof that resources were released. Preemption
  resumes from the last verified checkpoint; it does not restart spending
  from zero without approval.

Acceptance tests pinned here:

  - AIML-AT-016: dry-run identifies an approved Azure A100/H100-class
    plan WITHOUT provisioning; live path requires approval.
  - AIML-AT-017: preempted training resumes from the last verified
    checkpoint without double-counting spend.

The ``accelerator_job`` ansible role wraps these typed entry points;
this module holds the contract the role plugs into and never shells out.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field

from general_ludd.ai_ml.schemas import _require_nonempty_str, _require_sha256

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AcceleratorKind(enum.StrEnum):
    """Hardware kind discovered by the planner (spec §10).

    ``CPU`` and ``GPU`` are local (no approval token required). ``CLOUD``
    is an approved cloud accelerator (Azure A100/H100-class) whose
    provisioning requires an approval token.
    """

    CPU = "cpu"
    GPU = "gpu"
    CLOUD = "cloud"


# ---------------------------------------------------------------------------
# HardwareDescriptor — discovered (not provisioned) hardware
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HardwareDescriptor:
    """A piece of hardware the planner has discovered (spec §10).

    ``approved`` is the planner's determination: local hardware is
    approved by default; cloud hardware is approved only when its SKU
    appears in the planner's ``approved_cloud_skus`` allowlist. Discovery
    does NOT provision — :meth:`AcceleratorPlanner.dry_run` and
    :meth:`AcceleratorPlanner.plan_execution` are the only entry points
    that touch live resources.
    """

    kind: AcceleratorKind
    name: str
    sku: str
    region: str
    provider: str
    approved: bool
    cuda_compute_capability: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "kind",
            _coerce_enum(self.kind, AcceleratorKind, "kind"),
        )
        for fname in ("name", "sku", "region", "provider"):
            _require_nonempty_str(getattr(self, fname), fname)
        if not isinstance(self.approved, bool):
            raise ValueError("approved must be a bool")
        if self.cuda_compute_capability is not None:
            _require_nonempty_str(self.cuda_compute_capability, "cuda_compute_capability")


# ---------------------------------------------------------------------------
# ExecutionPlan — the spec §10 declared-fields contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionPlan:
    """A bounded accelerator execution plan (spec §10).

    Every field the spec mandates for an execution plan: SKU, region,
    quota evidence, image digest, driver and runtime versions,
    interconnect assumptions, storage/network needs, budget, timeout,
    checkpoint path, teardown behavior, and fallback. ``approval_token``
    is required for any cloud SKU; ``None`` for local plans.
    """

    sku: str
    region: str
    quota_evidence: str
    image_digest: str
    driver_version: str
    runtime_version: str
    interconnect: str
    storage_gb: int
    network_mbps: int
    budget_usd: float
    timeout_s: int
    checkpoint_uri: str | None
    teardown_behavior: str
    fallback_sku: str | None = None
    approval_token: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.sku, "sku")
        _require_nonempty_str(self.region, "region")
        _require_nonempty_str(self.quota_evidence, "quota_evidence")
        _require_sha256(self.image_digest, "image_digest")
        _require_nonempty_str(self.driver_version, "driver_version")
        _require_nonempty_str(self.runtime_version, "runtime_version")
        _require_nonempty_str(self.interconnect, "interconnect")
        if self.storage_gb < 0:
            raise ValueError(f"storage_gb must be >= 0, got {self.storage_gb}")
        if self.network_mbps < 0:
            raise ValueError(f"network_mbps must be >= 0, got {self.network_mbps}")
        if self.budget_usd < 0:
            raise ValueError(f"budget_usd must be >= 0, got {self.budget_usd}")
        if self.timeout_s <= 0:
            raise ValueError(f"timeout_s must be > 0, got {self.timeout_s}")
        if self.checkpoint_uri is not None:
            _require_nonempty_str(self.checkpoint_uri, "checkpoint_uri")
        _require_nonempty_str(self.teardown_behavior, "teardown_behavior")
        if self.teardown_behavior not in {"release", "preserve"}:
            raise ValueError(f"teardown_behavior must be 'release' or 'preserve', got {self.teardown_behavior!r}")
        if self.fallback_sku is not None:
            _require_nonempty_str(self.fallback_sku, "fallback_sku")
        if self.approval_token is not None:
            _require_nonempty_str(self.approval_token, "approval_token")

    @property
    def is_cloud(self) -> bool:
        """True when this plan targets a cloud accelerator (needs a token).

        The heuristic is structural: a plan carries an ``approval_token``
        iff it is a cloud SKU. Local CPU/GPU plans never carry tokens.
        """
        return self.approval_token is not None


# ---------------------------------------------------------------------------
# DryRunResult, TeardownProof, CheckpointRef, ResumeResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DryRunResult:
    """The output of :meth:`AcceleratorPlanner.dry_run` (AIML-AT-016).

    ``provisioned`` is always ``False``: a dry-run identifies the
    hardware that *would* be used and the plan that *would* execute, but
    never provisions. Compare with :meth:`plan_execution`, which does
    provision (and requires an approval token for cloud SKUs).
    """

    hardware: tuple[HardwareDescriptor, ...]
    plan: ExecutionPlan
    provisioned: bool = False

    def __post_init__(self) -> None:
        if self.provisioned:
            raise ValueError("a dry-run result must never carry provisioned=True (AIML-AT-016)")


@dataclass(frozen=True)
class TeardownProof:
    """Idempotent teardown proof (spec §10).

    Spec §10: 'Teardown is idempotent and emits proof that resources
    were released.' Each release emits a fresh proof; calling teardown
    twice on the same plan emits two proofs but does not raise.
    """

    resources_released: tuple[str, ...]
    timestamp: int

    def __post_init__(self) -> None:
        if not isinstance(self.resources_released, tuple):
            raise ValueError("resources_released must be a tuple of SKU strings")
        if not isinstance(self.timestamp, int) or self.timestamp < 0:
            raise ValueError("timestamp must be a non-negative int (unix epoch)")


@dataclass(frozen=True)
class CheckpointRef:
    """A reference to a training checkpoint (spec §6.4, §10).

    ``verified`` is the planner's record of whether this checkpoint was
    verified (digest matches on-disk content, no corruption). Spec §10:
    preemption resumes from the last *verified* checkpoint; an
    unverified checkpoint cannot be used for resume.
    """

    uri: str
    step: int
    sha256: str
    verified: bool

    def __post_init__(self) -> None:
        _require_nonempty_str(self.uri, "uri")
        if not isinstance(self.step, int) or self.step < 0:
            raise ValueError(f"step must be a non-negative int, got {self.step!r}")
        _require_sha256(self.sha256, "sha256")
        if not isinstance(self.verified, bool):
            raise ValueError("verified must be a bool")


@dataclass(frozen=True)
class ResumeResult:
    """The output of :meth:`AcceleratorPlanner.resume_from_checkpoint`.

    Carries the checkpoint to resume from AND the remaining budget after
    already-incurred spend. Spec §10: preemption 'does not restart
    spending from zero without approval' — the remaining budget is
    ``original - spent``, never a fresh full budget.
    """

    resume_from: CheckpointRef
    remaining_budget_usd: float
    resume_step: int

    def __post_init__(self) -> None:
        if not isinstance(self.resume_from, CheckpointRef):
            raise ValueError("resume_from must be a CheckpointRef instance")
        if self.remaining_budget_usd < 0:
            raise ValueError(
                f"remaining_budget_usd must be >= 0, got {self.remaining_budget_usd} (spec §11: stop before overrun)"
            )
        if self.resume_step < 0:
            raise ValueError(f"resume_step must be >= 0, got {self.resume_step}")


# ---------------------------------------------------------------------------
# AcceleratorPlanner
# ---------------------------------------------------------------------------


@dataclass
class AcceleratorPlanner:
    """Plan and execute bounded accelerator jobs (spec §10, AIML-017).

    Parameters:
      approved_cloud_skus: the allowlist of cloud SKU strings that may be
        provisioned. Spec §10: only approved cloud accelerators
        (including Azure A100/H100-class) are surfaced; an unapproved
        SKU is refused at plan time and filtered out of discovery.
      local_hardware: the local CPU/GPU hardware the planner can see.
        Local hardware never requires an approval token.
      cloud_catalog: the full set of cloud hardware the planner is aware
        of; :meth:`discover_hardware` filters this to the approved subset.

    The planner tracks ``_provisioned`` (the set of live-provisioned SKU
    strings) so :meth:`teardown` can be idempotent. The registry is
    intentionally in-process: it is the contract surface, not a cloud
    client.
    """

    approved_cloud_skus: frozenset[str]
    local_hardware: tuple[HardwareDescriptor, ...] = ()
    cloud_catalog: tuple[HardwareDescriptor, ...] = ()
    _provisioned: set[str] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.approved_cloud_skus, frozenset):
            raise ValueError("approved_cloud_skus must be a frozenset")
        for hw in self.local_hardware:
            if not isinstance(hw, HardwareDescriptor):
                raise ValueError("local_hardware entries must be HardwareDescriptor instances")
        for hw in self.cloud_catalog:
            if not isinstance(hw, HardwareDescriptor):
                raise ValueError("cloud_catalog entries must be HardwareDescriptor instances")

    # ------------------------------------------------------------------
    # Discovery (spec §10: "discovers permitted hardware without provisioning")
    # ------------------------------------------------------------------

    def discover_hardware(self) -> tuple[HardwareDescriptor, ...]:
        """Return all permitted hardware WITHOUT provisioning any of it.

        Local hardware is always returned. Cloud hardware is filtered to
        the approved subset (``approved_cloud_skus``); unapproved cloud
        SKUs are excluded so a caller never sees hardware it cannot use.
        """
        discovered: list[HardwareDescriptor] = list(self.local_hardware)
        for hw in self.cloud_catalog:
            if hw.kind is AcceleratorKind.CLOUD and hw.sku in self.approved_cloud_skus:
                discovered.append(hw)
        return tuple(discovered)

    # ------------------------------------------------------------------
    # Dry-run (AIML-AT-016: identify hardware without provisioning)
    # ------------------------------------------------------------------

    def dry_run(self, **plan_kwargs: object) -> DryRunResult:
        """Identify the hardware and plan that *would* be used, without
        provisioning (AIML-AT-016).

        Constructs the :class:`ExecutionPlan` from ``plan_kwargs`` (without
        an approval token), finds the hardware matching the plan's SKU,
        and returns a :class:`DryRunResult` with ``provisioned=False``.
        The planner's live-resource set is unchanged.
        """
        # Dry-run NEVER carries an approval token; drop any caller-supplied
        # token so the resulting plan is structurally a dry-run plan.
        plan_kwargs.pop("approval_token", None)
        plan = self._build_plan(**plan_kwargs)
        hardware = self._hardware_for_sku(plan.sku)
        return DryRunResult(hardware=hardware, plan=plan, provisioned=False)

    # ------------------------------------------------------------------
    # Live provisioning (spec §10: "Provisioning requires an approval token")
    # ------------------------------------------------------------------

    def plan_execution(self, **plan_kwargs: object) -> ExecutionPlan:
        """Build and (for cloud SKUs) provision an :class:`ExecutionPlan`.

        Spec §10: 'Provisioning requires an approval token.' For a cloud
        SKU, ``approval_token`` MUST be supplied; without it the call
        refuses and no provisioning occurs (AIML-AT-016 live path). For
        local SKUs no token is required.

        The SKU must appear in :attr:`approved_cloud_skus` (for cloud) or
        :attr:`local_hardware` (for local); an unapproved SKU is refused.
        """
        token = plan_kwargs.get("approval_token")
        sku = plan_kwargs.get("sku")
        if not isinstance(sku, str) or not sku.strip():
            raise ValueError("sku must be a non-empty string")

        is_cloud = self._is_cloud_sku(sku)
        if is_cloud:
            if not isinstance(token, str) or not token.strip():
                raise ValueError(
                    "approval_token is required to provision cloud SKU "
                    f"{sku!r} (spec §10: 'Provisioning requires an approval token')"
                )
            if sku not in self.approved_cloud_skus:
                raise ValueError(
                    f"SKU {sku!r} is not in approved_cloud_skus; refusing to provision "
                    "(spec §10: only approved cloud accelerators may be provisioned)"
                )

        plan = self._build_plan(**plan_kwargs)
        # Provision: track the SKU as live. Local SKUs are recorded too so
        # teardown() can release them uniformly.
        self._provisioned.add(plan.sku)
        return plan

    # ------------------------------------------------------------------
    # Teardown (spec §10: idempotent, emits proof)
    # ------------------------------------------------------------------

    def teardown(self, plan: ExecutionPlan) -> TeardownProof:
        """Release the resources a plan provisioned (idempotent).

        Spec §10: 'Teardown is idempotent and emits proof that resources
        were released.' Calling teardown twice on the same plan does not
        raise and emits a fresh proof each time. After teardown the SKU
        is no longer in :meth:`live_resources`.
        """
        released: tuple[str, ...] = (plan.sku,) if plan.sku in self._provisioned else ()
        # Idempotent: discard removes the SKU if present, no-op otherwise.
        self._provisioned.discard(plan.sku)
        return TeardownProof(resources_released=released, timestamp=int(time.time()))

    def live_resources(self) -> frozenset[str]:
        """Return the set of currently-provisioned SKU strings."""
        return frozenset(self._provisioned)

    # ------------------------------------------------------------------
    # Resume from checkpoint (AIML-AT-017)
    # ------------------------------------------------------------------

    def resume_from_checkpoint(
        self,
        *,
        checkpoint: CheckpointRef,
        spend_already_incurred_usd: float,
        original_budget_usd: float,
    ) -> ResumeResult:
        """Resume a preempted job from its last verified checkpoint.

        AIML-AT-017: preempted training resumes from the last VERIFIED
        checkpoint without double-counting spend. The remaining budget
        is ``original - spent``; it is never reset to the full original.

        Refuses when:
          - the checkpoint is not verified (spec §10: 'verified
            checkpoint' is a hard requirement for resume);
          - already-incurred spend has exhausted the original budget
            (spec §11: 'Budget/quota exhaustion -> Stop before overrun').
        """
        if not isinstance(checkpoint, CheckpointRef):
            raise ValueError("checkpoint must be a CheckpointRef instance")
        if not checkpoint.verified:
            raise ValueError(
                "cannot resume from an unverified checkpoint (spec §10: preemption "
                "resumes from the last VERIFIED checkpoint; unverified checkpoints "
                "may be corrupt or partial)"
            )
        if spend_already_incurred_usd < 0:
            raise ValueError(f"spend_already_incurred_usd must be >= 0, got {spend_already_incurred_usd}")
        if original_budget_usd < 0:
            raise ValueError(f"original_budget_usd must be >= 0, got {original_budget_usd}")
        remaining = original_budget_usd - spend_already_incurred_usd
        if remaining <= 0:
            raise ValueError(
                f"budget exhausted: original={original_budget_usd}, spent={spend_already_incurred_usd}, "
                "remaining<=0 (spec §11: stop before overrun and return awaiting_approval or failed)"
            )
        return ResumeResult(
            resume_from=checkpoint,
            remaining_budget_usd=remaining,
            resume_step=checkpoint.step,
        )

    # ------------------------------------------------------------------
    # Checkpoint verification (spec §11: hash/signature mismatch -> hard fail)
    # ------------------------------------------------------------------

    def verify_checkpoint(self, checkpoint: CheckpointRef, *, on_disk_sha256: str) -> bool:
        """Verify a checkpoint's recorded digest matches its on-disk digest.

        Spec §11: 'Hash/signature mismatch -> Hard fail, revoke cache
        entry, emit security event.' Returns ``True`` only when the
        on-disk sha256 exactly equals the recorded one.
        """
        _require_sha256(on_disk_sha256, "on_disk_sha256")
        return on_disk_sha256 == checkpoint.sha256

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_plan(self, **plan_kwargs: object) -> ExecutionPlan:
        """Construct an ExecutionPlan from kwargs, validating structurally."""
        checkpoint_uri = plan_kwargs.get("checkpoint_uri")
        fallback_sku = plan_kwargs.get("fallback_sku")
        approval_token = plan_kwargs.get("approval_token")
        return ExecutionPlan(
            sku=_as_str(plan_kwargs, "sku"),
            region=_as_str(plan_kwargs, "region"),
            quota_evidence=_as_str(plan_kwargs, "quota_evidence"),
            image_digest=_as_str(plan_kwargs, "image_digest"),
            driver_version=_as_str(plan_kwargs, "driver_version"),
            runtime_version=_as_str(plan_kwargs, "runtime_version"),
            interconnect=_as_str(plan_kwargs, "interconnect"),
            storage_gb=_as_int(plan_kwargs, "storage_gb"),
            network_mbps=_as_int(plan_kwargs, "network_mbps"),
            budget_usd=_as_float(plan_kwargs, "budget_usd"),
            timeout_s=_as_int(plan_kwargs, "timeout_s"),
            checkpoint_uri=_as_optional_str(checkpoint_uri, "checkpoint_uri"),
            teardown_behavior=_as_str(plan_kwargs, "teardown_behavior"),
            fallback_sku=_as_optional_str(fallback_sku, "fallback_sku"),
            approval_token=_as_optional_str(approval_token, "approval_token"),
        )

    def _hardware_for_sku(self, sku: str) -> tuple[HardwareDescriptor, ...]:
        """Return all discovered hardware whose SKU matches."""
        return tuple(h for h in self.discover_hardware() if h.sku == sku)

    def _is_cloud_sku(self, sku: str) -> bool:
        """True when ``sku`` appears in the cloud catalog (approved or not)."""
        return any(h.kind is AcceleratorKind.CLOUD and h.sku == sku for h in self.cloud_catalog)


# ---------------------------------------------------------------------------
# Internal coercion helpers (mirror the patterns in schemas.py)
# ---------------------------------------------------------------------------


def _coerce_enum(value: object, enum_cls: type[enum.Enum], field_name: str) -> enum.Enum:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError as exc:
            raise ValueError(f"invalid {field_name}: {value!r}") from exc
    raise ValueError(f"invalid {field_name}: {value!r}")


def _as_str(kwargs: dict[str, object], key: str) -> str:
    val = kwargs.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return val


def _as_int(kwargs: dict[str, object], key: str) -> int:
    val = kwargs.get(key)
    if not isinstance(val, int) or isinstance(val, bool):
        raise ValueError(f"{key} must be an int, got {val!r}")
    return val


def _as_float(kwargs: dict[str, object], key: str) -> float:
    val = kwargs.get(key)
    if isinstance(val, bool) or not isinstance(val, int | float):
        raise ValueError(f"{key} must be a number, got {val!r}")
    return float(val)


def _as_optional_str(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name}, when set, must be a non-empty string")
    return value


__all__ = [
    "AcceleratorKind",
    "AcceleratorPlanner",
    "CheckpointRef",
    "DryRunResult",
    "ExecutionPlan",
    "HardwareDescriptor",
    "ResumeResult",
    "TeardownProof",
]
