"""AIML Phase C — adapter training: LoRA, QLoRA, and adapter fine-tuning.

Implements docs/specs/FEATURE_AI_ML_EXPERT.md §6.4 (LoRA, QLoRA, adapters,
distillation) for capability AIML-008:

  - ``AdapterManifest`` records every field required to reproduce a
    fine-tuning run: base-model digest, adapter method, target modules,
    rank, alpha, dropout, quantization, optimizer, seed, dataset manifest,
    tokenizer, precision, and dependency lock digest (spec §6.4).
  - ``validate_adapter`` makes serving an adapter against a different base
    digest a hard failure (spec §6.4: "Serving an adapter against a different
    base digest is a hard failure"; AIML-AT-008).
  - ``plan_adaptation`` generates a restartable ``TrainingPlan`` with a
    checkpoint/resume strategy (spec §6.4: "Training jobs are restartable
    from verified checkpoints"; spec §10: "Preemption resumes from the last
    verified checkpoint; it does not restart spending from zero without
    approval").
  - ``safe_stop`` translates the §6.4/§11 stop conditions (OOM, NaN/Inf,
    divergent loss, budget overrun, corrupt checkpoint) into a typed
    ``SafeStopResult`` that preserves diagnostic artifacts and a retry plan
    so a crashed job leaves an auditable trail, never a silent partial.

The ``adapter_train`` ansible role wraps these typed entry points; this
module holds the contract the role plugs into and never shells out.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from general_ludd.ai_ml.schemas import _require_nonempty_str, _require_sha256

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AdapterMethod(enum.StrEnum):
    """Adapter fine-tuning methods (spec §6.4).

    ``LORA`` is full-precision LoRA. ``QLORA`` requires 4-bit NF4
    quantization of the base weights. ``PROMPT_TUNING`` / ``PREFIX_TUNING``
    cover prompt-only adapters that share the digest-binding contract.
    """

    LORA = "lora"
    QLORA = "qlora"
    PROMPT_TUNING = "prompt_tuning"
    PREFIX_TUNING = "prefix_tuning"


class Quantization(enum.StrEnum):
    """Base-model weight quantization schemes used by QLoRA-family adapters.

    QLoRA uses NF4 (NormalFloat 4-bit) with double quantization by default;
    FP4 and INT8 are alternate precision profiles.
    """

    NF4 = "nf4"
    FP4 = "fp4"
    INT8 = "int8"


class TrainingStopReason(enum.StrEnum):
    """Reasons a training run stopped before natural completion (spec §6.4, §11).

    Each maps to a documented safe-stop behavior:
      - ``OOM``            -> retryable; preserve checkpoint.
      - ``NAN_INF``        -> not retryable with the same config; lower LR / clip.
      - ``DIVERGENT_LOSS`` -> not retryable; plan must change.
      - ``BUDGET_OVERRUN`` -> terminal; returns ``awaiting_approval``.
      - ``CORRUPT_CHECKPOINT`` -> terminal; restart from prior verified ckpt.
      - ``COMPLETED``      -> normal completion; not a fault.
    """

    OOM = "oom"
    NAN_INF = "nan_inf"
    DIVERGENT_LOSS = "divergent_loss"
    BUDGET_OVERRUN = "budget_overrun"
    CORRUPT_CHECKPOINT = "corrupt_checkpoint"
    COMPLETED = "completed"


# ---------------------------------------------------------------------------
# AdapterManifest — the reproducibility contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdapterManifest:
    """A reproducible LoRA/QLoRA/adapter training manifest (spec §6.4).

    ``adapter_train`` records: base-model digest, adapter method, target
    modules, rank, alpha, dropout, quantization, optimizer, seed, dataset
    manifest, tokenizer, precision, hardware, dependency lock, checkpoints,
    and evaluation results. This manifest is the immutable contract a serving
    shard validates against before loading the adapter weights.

    Serving an adapter against a different base digest is a HARD failure
    (spec §6.4) — see :func:`validate_adapter`.
    """

    base_model_digest: str
    method: AdapterMethod
    target_modules: tuple[str, ...]
    rank: int
    alpha: int
    dropout: float
    optimizer: str
    seed: int
    dataset_manifest_sha256: str
    tokenizer: str
    precision: str
    dependency_lock_sha256: str
    base_model_record_id: str
    quantization: Quantization | None = None
    hardware: tuple[str, ...] = ()
    checkpoint_uris: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_sha256(self.base_model_digest, "base_model_digest")
        object.__setattr__(
            self,
            "method",
            _coerce_enum(self.method, AdapterMethod, "method"),
        )
        if not self.target_modules or any(not isinstance(m, str) or not m.strip() for m in self.target_modules):
            raise ValueError("target_modules must be a non-empty tuple of non-empty strings")
        if not isinstance(self.rank, int) or self.rank <= 0:
            raise ValueError(f"rank must be a positive int, got {self.rank!r}")
        if not isinstance(self.alpha, int) or self.alpha <= 0:
            raise ValueError(f"alpha must be a positive int, got {self.alpha!r}")
        if not (0.0 <= self.dropout < 1.0):
            raise ValueError(f"dropout must be in [0.0, 1.0), got {self.dropout}")
        _require_nonempty_str(self.optimizer, "optimizer")
        if not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError(f"seed must be a non-negative int, got {self.seed!r}")
        _require_sha256(self.dataset_manifest_sha256, "dataset_manifest_sha256")
        _require_nonempty_str(self.tokenizer, "tokenizer")
        _require_nonempty_str(self.precision, "precision")
        _require_sha256(self.dependency_lock_sha256, "dependency_lock_sha256")
        _require_nonempty_str(self.base_model_record_id, "base_model_record_id")
        if self.quantization is not None and not isinstance(self.quantization, Quantization):
            object.__setattr__(
                self,
                "quantization",
                _coerce_enum(self.quantization, Quantization, "quantization"),
            )
        # QLoRA requires a quantization profile (spec §6.4).
        if self.method is AdapterMethod.QLORA and self.quantization is None:
            raise ValueError("QLoRA adapters require a non-None quantization profile (e.g. Quantization.NF4)")


# ---------------------------------------------------------------------------
# Checkpoint / resume strategy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckpointStrategy:
    """Checkpoint and resume strategy for a training plan (spec §6.4, §10).

    Spec §6.4: "Training jobs are restartable from verified checkpoints.
    Partial outputs remain quarantined." Spec §10: "Preemption resumes from
    the last verified checkpoint; it does not restart spending from zero
    without approval."
    """

    checkpoint_dir: str
    checkpoint_interval_steps: int = 500
    verify_checkpoints: bool = True
    resume_from: str | None = None
    start_step: int = 0

    def __post_init__(self) -> None:
        _require_nonempty_str(self.checkpoint_dir, "checkpoint_dir")
        if self.checkpoint_interval_steps <= 0:
            raise ValueError(f"checkpoint_interval_steps must be > 0, got {self.checkpoint_interval_steps}")
        if self.start_step < 0:
            raise ValueError(f"start_step must be >= 0, got {self.start_step}")
        if self.resume_from is not None and not self.resume_from.strip():
            raise ValueError("resume_from, when set, must be a non-empty URI")


@dataclass(frozen=True)
class TrainingPlan:
    """A restartable training plan: manifest + checkpoint strategy (spec §6.4)."""

    manifest: AdapterManifest
    checkpoint: CheckpointStrategy
    max_steps: int = 10000
    budget_usd: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, AdapterManifest):
            raise ValueError("manifest must be an AdapterManifest instance")
        if not isinstance(self.checkpoint, CheckpointStrategy):
            raise ValueError("checkpoint must be a CheckpointStrategy instance")
        if self.max_steps <= 0:
            raise ValueError(f"max_steps must be > 0, got {self.max_steps}")
        if self.budget_usd < 0:
            raise ValueError(f"budget_usd must be >= 0, got {self.budget_usd}")


# ---------------------------------------------------------------------------
# Safe-stop result
# ---------------------------------------------------------------------------


# Reasons that mean the job cannot be retried as-is — the plan must change.
_HARD_STOP_REASONS: frozenset[TrainingStopReason] = frozenset(
    {
        TrainingStopReason.NAN_INF,
        TrainingStopReason.DIVERGENT_LOSS,
        TrainingStopReason.CORRUPT_CHECKPOINT,
    }
)


@dataclass(frozen=True)
class SafeStopResult:
    """Typed safe-stop outcome preserving diagnostic artifacts (spec §6.4, §11).

    When OOM/NaN/budget-overrun fires, the trainer MUST:
      - preserve the last verified checkpoint (if any) so preemption can resume;
      - quarantine partial outputs (never expose a half-written adapter);
      - emit diagnostic artifacts (spec §11: "preserve bounded diagnostics");
      - return a retry verdict (retryable, awaiting_approval, or terminal).
    """

    reason: TrainingStopReason
    terminal_step: int
    preserved_checkpoint: str | None
    diagnostics: tuple[str, ...]
    retryable: bool
    awaiting_approval: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reason",
            _coerce_enum(self.reason, TrainingStopReason, "reason"),
        )
        if self.terminal_step < 0:
            raise ValueError(f"terminal_step must be >= 0, got {self.terminal_step}")
        if self.preserved_checkpoint is not None and not self.preserved_checkpoint.strip():
            raise ValueError("preserved_checkpoint, when set, must be a non-empty URI")


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def validate_adapter(manifest: AdapterManifest, *, serving_base_digest: str) -> None:
    """Hard-fail when an adapter is served against the wrong base model.

    Spec §6.4: "Serving an adapter against a different base digest is a hard
    failure." AIML-AT-008: "Adapter load fails on a one-byte base-model digest
    mismatch and succeeds reproducibly with the pinned digest."
    """
    if not isinstance(serving_base_digest, str) or not serving_base_digest.strip():
        raise ValueError("serving_base_digest must be a non-empty sha256 hex digest")
    if serving_base_digest != manifest.base_model_digest:
        raise ValueError(
            "base_model_digest mismatch: adapter pinned to "
            f"{manifest.base_model_digest!r} but serving base digest is "
            f"{serving_base_digest!r} — refusing to load adapter"
        )


def plan_adaptation(
    *,
    base_model_digest: str,
    method: AdapterMethod,
    target_modules: tuple[str, ...] | list[str],
    rank: int,
    alpha: int,
    dataset_manifest_sha256: str,
    tokenizer: str,
    dependency_lock_sha256: str,
    seed: int,
    checkpoint_dir: str,
    base_model_record_id: str = "base-model",
    dropout: float = 0.0,
    quantization: Quantization | None = None,
    optimizer: str = "adamw",
    precision: str = "bf16",
    hardware: tuple[str, ...] = (),
    checkpoint_interval_steps: int = 500,
    resume_from: str | None = None,
    start_step: int = 0,
    max_steps: int = 10000,
    budget_usd: float = 0.0,
) -> TrainingPlan:
    """Generate an :class:`AdapterManifest` + :class:`CheckpointStrategy` plan.

    The manifest is fully reproducible: identical inputs produce identical
    training plans (modulo wall-clock timestamps the manifest itself does
    not store). ``resume_from`` wires preemption recovery: a resumed plan
    starts at ``start_step`` instead of restarting spend from zero.
    """
    if quantization is None and method is AdapterMethod.QLORA:
        quantization = Quantization.NF4

    modules = tuple(target_modules) if not isinstance(target_modules, tuple) else target_modules

    manifest = AdapterManifest(
        base_model_digest=base_model_digest,
        method=method,
        target_modules=modules,
        rank=rank,
        alpha=alpha,
        dropout=dropout,
        quantization=quantization,
        optimizer=optimizer,
        seed=seed,
        dataset_manifest_sha256=dataset_manifest_sha256,
        tokenizer=tokenizer,
        precision=precision,
        dependency_lock_sha256=dependency_lock_sha256,
        base_model_record_id=base_model_record_id,
        hardware=hardware,
    )

    parsed_start = start_step
    if resume_from is not None and parsed_start == 0:
        parsed_start = _parse_step_from_uri(resume_from)

    checkpoint = CheckpointStrategy(
        checkpoint_dir=checkpoint_dir,
        checkpoint_interval_steps=checkpoint_interval_steps,
        verify_checkpoints=True,
        resume_from=resume_from,
        start_step=parsed_start,
    )

    return TrainingPlan(
        manifest=manifest,
        checkpoint=checkpoint,
        max_steps=max_steps,
        budget_usd=budget_usd,
    )


def safe_stop(
    *,
    reason: TrainingStopReason,
    step: int,
    preserved_checkpoint: str | None,
    extra_diagnostics: tuple[str, ...] = (),
) -> SafeStopResult:
    """Translate a §6.4/§11 stop condition into a typed SafeStopResult.

    Maps each stop reason to its documented behavior:
      - ``OOM``: retryable, checkpoint preserved, no approval needed.
      - ``NAN_INF`` / ``DIVERGENT_LOSS`` / ``CORRUPT_CHECKPOINT``: NOT
        retryable as-is — the training plan must change (LR, gradient
        clipping, restoring a prior verified checkpoint).
      - ``BUDGET_OVERRUN``: terminal, ``awaiting_approval=True`` per spec §11
        ("Stop before overrun and return awaiting_approval or failed").
      - ``COMPLETED``: not a fault; no diagnostics required.
    """
    coerced_reason = reason if isinstance(reason, TrainingStopReason) else TrainingStopReason(reason)

    diagnostics: list[str] = list(extra_diagnostics)
    if coerced_reason is TrainingStopReason.OOM:
        retryable = True
        awaiting = False
        if not diagnostics:
            diagnostics.append(
                f"GPU OOM at step {step}; checkpoint {preserved_checkpoint!r} preserved; "
                "release resources and retry from checkpoint"
            )
    elif coerced_reason is TrainingStopReason.BUDGET_OVERRUN:
        retryable = False
        awaiting = True
        if not diagnostics:
            diagnostics.append(
                f"budget/quota exhaustion at step {step}; stopped before overrun; "
                "awaiting approval to continue or escalate"
            )
    elif coerced_reason is TrainingStopReason.NAN_INF:
        retryable = False
        awaiting = False
        if not diagnostics:
            diagnostics.append(
                f"NaN/Inf in loss at step {step}; training plan must change "
                "(lower LR, add gradient clipping) before retry"
            )
    elif coerced_reason is TrainingStopReason.DIVERGENT_LOSS:
        retryable = False
        awaiting = False
        if not diagnostics:
            diagnostics.append(f"divergent loss at step {step}; training plan must change before retry")
    elif coerced_reason is TrainingStopReason.CORRUPT_CHECKPOINT:
        retryable = False
        awaiting = False
        if not diagnostics:
            diagnostics.append(f"corrupt checkpoint at step {step}; restart from prior verified checkpoint")
    elif coerced_reason is TrainingStopReason.COMPLETED:
        retryable = False
        awaiting = False
    else:  # defensive — enum exhaustiveness
        retryable = False
        awaiting = False

    return SafeStopResult(
        reason=coerced_reason,
        terminal_step=step,
        preserved_checkpoint=preserved_checkpoint,
        diagnostics=tuple(diagnostics),
        retryable=retryable,
        awaiting_approval=awaiting,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _coerce_enum(value: object, enum_cls: type[enum.Enum], field_name: str) -> enum.Enum:
    """Coerce a string or enum member; raise ValueError on miss."""
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError as exc:
            raise ValueError(f"invalid {field_name}: {value!r}") from exc
    raise ValueError(f"invalid {field_name}: {value!r}")


def _parse_step_from_uri(uri: str) -> int:
    """Extract an integer step suffix from a checkpoint URI.

    ``artifacts://ckpts/run-1/step-1000`` -> ``1000``. Returns ``0`` when
    no numeric suffix is present (the caller must then set ``start_step``
    explicitly via ``plan_adaptation``).
    """
    tail = uri.rstrip("/").rsplit("/", 1)[-1]
    if "-" in tail:
        _, _, num = tail.rpartition("-")
        if num.isdigit():
            return int(num)
    if tail.isdigit():
        return int(tail)
    return 0


__all__ = [
    "AdapterManifest",
    "AdapterMethod",
    "CheckpointStrategy",
    "Quantization",
    "SafeStopResult",
    "TrainingPlan",
    "TrainingStopReason",
    "plan_adaptation",
    "safe_stop",
    "validate_adapter",
]
