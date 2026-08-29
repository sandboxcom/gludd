"""Branch-floor contracts for the beta4 AI/ML capability surface."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from general_ludd.ai_ml.accelerators import (
    AcceleratorKind,
    AcceleratorPlanner,
    CheckpointRef,
    DryRunResult,
    ExecutionPlan,
    HardwareDescriptor,
    ResumeResult,
    TeardownProof,
    _as_float,
    _as_int,
    _as_optional_str,
    _as_str,
)
from general_ludd.ai_ml.adaptation import (
    AdapterManifest,
    AdapterMethod,
    CheckpointStrategy,
    SafeStopResult,
    TrainingPlan,
    TrainingStopReason,
    _parse_step_from_uri,
    plan_adaptation,
    safe_stop,
    validate_adapter,
)
from general_ludd.ai_ml.distillation import (
    DataFilterRule,
    DistillationPlan,
    DistillationType,
    FilterAction,
    RetentionThreshold,
    StudentValidation,
    validate_student,
)
from general_ludd.ai_ml.reasoning import (
    IndependentCheck,
    NumericalAnswer,
    ReasoningEngine,
    ReasoningResult,
    StepArtifact,
)
from general_ludd.ai_ml.registries import Registry, Source
from general_ludd.ai_ml.schemas import ResultStatus, Uncertainty, VerificationStatus
from general_ludd.ai_ml.speech import (
    ASRRequest,
    ASRResult,
    ASRSegment,
    PrivacyClass,
    TTSRequest,
    VoiceConsent,
    check_voice_consent,
    word_error_rate,
)
from general_ludd.ai_ml.speech import (
    _coerce_enum as speech_coerce_enum,
)
from general_ludd.ai_ml.vision import (
    BoundingBox,
    Classification,
    ContentDomain,
    Detection,
    ImageEditRecord,
    ImageOperation,
    ImageOperationType,
    Segmentation,
    VisionRequest,
    VisionResult,
    VisionTask,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64


def _invoke(call: Callable[..., object], *args: Any, **kwargs: Any) -> object:
    """Invoke a typed boundary with intentionally malformed test input."""
    return call(*args, **kwargs)


def _execution_kwargs() -> dict[str, Any]:
    return {
        "sku": "local-cpu",
        "region": "local",
        "quota_evidence": "local-owned",
        "image_digest": _SHA_A,
        "driver_version": "none",
        "runtime_version": "3.14",
        "interconnect": "memory",
        "storage_gb": 1,
        "network_mbps": 1,
        "budget_usd": 0.0,
        "timeout_s": 1,
        "checkpoint_uri": None,
        "teardown_behavior": "release",
    }


def _hardware() -> HardwareDescriptor:
    return HardwareDescriptor(
        kind=AcceleratorKind.CPU,
        name="local",
        sku="local-cpu",
        region="local",
        provider="local",
        approved=True,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("storage_gb", -1),
        ("network_mbps", -1),
        ("budget_usd", -1.0),
        ("timeout_s", 0),
        ("checkpoint_uri", ""),
        ("teardown_behavior", "retain"),
        ("fallback_sku", ""),
        ("approval_token", ""),
    ],
)
def test_execution_plan_rejects_every_bounded_resource_violation(field: str, value: object) -> None:
    """Execution plans fail closed before allocating invalid resources."""
    kwargs = _execution_kwargs()
    kwargs[field] = value
    with pytest.raises(ValueError):
        ExecutionPlan(**kwargs)


def test_accelerator_value_objects_reject_invalid_lifecycle_state() -> None:
    """Hardware, dry-run, teardown, checkpoint, and resume invariants are strict."""
    hardware = _hardware()
    plan = ExecutionPlan(**_execution_kwargs())
    checkpoint = CheckpointRef(uri="artifact://checkpoint", step=1, sha256=_SHA_A, verified=True)
    cases: tuple[Callable[[], object], ...] = (
        lambda: HardwareDescriptor(**{**hardware.__dict__, "approved": "yes"}),
        lambda: HardwareDescriptor(**{**hardware.__dict__, "cuda_compute_capability": ""}),
        lambda: DryRunResult(hardware=(hardware,), plan=plan, provisioned=True),
        lambda: _invoke(TeardownProof, resources_released=["sku"], timestamp=1),
        lambda: TeardownProof(resources_released=(), timestamp=-1),
        lambda: CheckpointRef(uri="artifact://x", step=-1, sha256=_SHA_A, verified=True),
        lambda: _invoke(CheckpointRef, uri="artifact://x", step=1, sha256=_SHA_A, verified="yes"),
        lambda: _invoke(ResumeResult, resume_from="bad", remaining_budget_usd=1, resume_step=1),
        lambda: ResumeResult(resume_from=checkpoint, remaining_budget_usd=-1, resume_step=1),
        lambda: ResumeResult(resume_from=checkpoint, remaining_budget_usd=1, resume_step=-1),
    )
    for construct in cases:
        with pytest.raises(ValueError):
            construct()


def test_accelerator_planner_and_scalar_helpers_fail_closed() -> None:
    """Planner collections and dynamically supplied plan scalars are type checked."""
    with pytest.raises(ValueError):
        _invoke(AcceleratorPlanner, approved_cloud_skus={"sku"})
    with pytest.raises(ValueError):
        _invoke(AcceleratorPlanner, approved_cloud_skus=frozenset(), local_hardware=("bad",))
    with pytest.raises(ValueError):
        _invoke(AcceleratorPlanner, approved_cloud_skus=frozenset(), cloud_catalog=("bad",))

    for helper, kwargs in (
        (_as_str, ({"value": 1}, "value")),
        (_as_int, ({"value": True}, "value")),
        (_as_float, ({"value": "1"}, "value")),
    ):
        with pytest.raises(ValueError):
            helper(*kwargs)
    with pytest.raises(ValueError):
        _as_optional_str(1, "value")
    assert _as_optional_str(None, "value") is None
    assert _as_optional_str("ok", "value") == "ok"


def test_accelerator_resume_rejects_wrong_types_and_spend() -> None:
    """Resume cannot reset spend or accept an unowned checkpoint type."""
    planner = AcceleratorPlanner(approved_cloud_skus=frozenset(), local_hardware=(_hardware(),))
    checkpoint = CheckpointRef(uri="artifact://checkpoint", step=1, sha256=_SHA_A, verified=True)
    with pytest.raises(ValueError):
        _invoke(
            planner.resume_from_checkpoint,
            checkpoint="bad",
            spend_already_incurred_usd=0,
            original_budget_usd=1,
        )
    with pytest.raises(ValueError):
        planner.resume_from_checkpoint(checkpoint=checkpoint, spend_already_incurred_usd=-1, original_budget_usd=1)
    with pytest.raises(ValueError):
        planner.resume_from_checkpoint(checkpoint=checkpoint, spend_already_incurred_usd=0, original_budget_usd=-1)


def _manifest_kwargs() -> dict[str, Any]:
    return {
        "base_model_digest": _SHA_A,
        "method": AdapterMethod.LORA,
        "target_modules": ("q_proj",),
        "rank": 8,
        "alpha": 16,
        "dropout": 0.0,
        "optimizer": "adamw",
        "seed": 1,
        "dataset_manifest_sha256": _SHA_B,
        "tokenizer": "tokenizer",
        "precision": "bf16",
        "dependency_lock_sha256": _SHA_A,
        "base_model_record_id": "base",
    }


def _manifest() -> AdapterManifest:
    return AdapterManifest(**_manifest_kwargs())


@pytest.mark.parametrize(("field", "value"), [("alpha", 0), ("seed", -1), ("quantization", "bad")])
def test_adapter_manifest_rejects_remaining_invalid_fields(field: str, value: object) -> None:
    """Adapter metadata rejects invalid alpha, seed, and quantization."""
    kwargs = _manifest_kwargs()
    kwargs[field] = value
    with pytest.raises(ValueError):
        AdapterManifest(**kwargs)


def test_adaptation_lifecycle_objects_reject_invalid_state() -> None:
    """Checkpoint, plan, and safe-stop lifecycle objects fail closed."""
    manifest = _manifest()
    checkpoint = CheckpointStrategy(checkpoint_dir="artifact://checkpoints")
    cases: tuple[Callable[[], object], ...] = (
        lambda: CheckpointStrategy(checkpoint_dir="x", checkpoint_interval_steps=0),
        lambda: CheckpointStrategy(checkpoint_dir="x", start_step=-1),
        lambda: CheckpointStrategy(checkpoint_dir="x", resume_from=""),
        lambda: _invoke(TrainingPlan, manifest="bad", checkpoint=checkpoint),
        lambda: _invoke(TrainingPlan, manifest=manifest, checkpoint="bad"),
        lambda: TrainingPlan(manifest=manifest, checkpoint=checkpoint, max_steps=0),
        lambda: TrainingPlan(manifest=manifest, checkpoint=checkpoint, budget_usd=-1),
        lambda: SafeStopResult(
            reason=TrainingStopReason.OOM,
            terminal_step=-1,
            preserved_checkpoint=None,
            diagnostics=(),
            retryable=True,
        ),
        lambda: SafeStopResult(
            reason=TrainingStopReason.OOM,
            terminal_step=1,
            preserved_checkpoint="",
            diagnostics=(),
            retryable=True,
        ),
    )
    for construct in cases:
        with pytest.raises(ValueError):
            construct()


def test_adaptation_resume_and_stop_reason_branches() -> None:
    """Resume suffixes and every terminal stop disposition remain deterministic."""
    assert _parse_step_from_uri("artifact://run/step-12") == 12
    assert _parse_step_from_uri("artifact://run/13") == 13
    assert _parse_step_from_uri("artifact://run/latest") == 0
    plan = plan_adaptation(
        **_manifest_kwargs(),
        checkpoint_dir="artifact://checkpoints",
        resume_from="artifact://run/step-12",
        start_step=0,
    )
    assert plan.checkpoint.start_step == 12
    with pytest.raises(ValueError):
        validate_adapter(_manifest(), serving_base_digest="")
    assert safe_stop(
        reason=TrainingStopReason.DIVERGENT_LOSS,
        step=3,
        preserved_checkpoint=None,
    ).retryable is False
    assert safe_stop(
        reason=TrainingStopReason.COMPLETED,
        step=4,
        preserved_checkpoint=None,
        extra_diagnostics=("complete",),
    ).diagnostics == ("complete",)


def _distillation_plan() -> DistillationPlan:
    return DistillationPlan(
        teacher_model_digest=_SHA_A,
        teacher_license="Apache-2.0",
        teacher_distillation_permitted=True,
        student_architecture="tiny",
        student_model_digest=_SHA_B,
        distillation_type=DistillationType.RESPONSE,
        data_filter_rules=(DataFilterRule("secret", "token", FilterAction.DROP),),
        retention_thresholds=(RetentionThreshold("accuracy", 0.8),),
        safety_tests_required=("toxicity",),
        capability_loss_notes=("capacity",),
    )


def test_distillation_objects_reject_malformed_policy_evidence() -> None:
    """Filter, threshold, plan, and validation evidence reject malformed entries."""
    plan = _distillation_plan()
    plan_kwargs = dict(plan.__dict__)
    cases: tuple[Callable[[], object], ...] = (
        lambda: _invoke(DataFilterRule, "rule", "pattern", "invalid"),
        lambda: _invoke(RetentionThreshold, "metric", "bad"),
        lambda: DistillationPlan(**{**plan_kwargs, "teacher_distillation_permitted": "yes"}),
        lambda: DistillationPlan(**{**plan_kwargs, "distillation_type": "bad"}),
        lambda: DistillationPlan(**{**plan_kwargs, "data_filter_rules": ("bad",)}),
        lambda: DistillationPlan(**{**plan_kwargs, "retention_thresholds": [RetentionThreshold("x", 1)]}),
        lambda: DistillationPlan(**{**plan_kwargs, "safety_tests_required": ("",)}),
        lambda: DistillationPlan(**{**plan_kwargs, "capability_loss_notes": ("",)}),
        lambda: _invoke(StudentValidation, False, (("bad", 1, 1),), True, True, False, True),
        lambda: _invoke(StudentValidation, "yes", (), True, True, False, True),
        lambda: StudentValidation(True, (), True, True, False, True, ("blocked",)),
    )
    for construct in cases:
        with pytest.raises(ValueError):
            construct()


def test_distillation_validation_handles_missing_and_lower_is_better_metrics() -> None:
    """Promotion blocks missing scores and honors lower-is-better thresholds."""
    lower = RetentionThreshold("ece", 0.1, lower_is_better=True)
    assert lower.is_met(0.05) is True
    assert lower.is_met(-1) is False
    plan = _distillation_plan()
    result = validate_student(
        plan,
        retention_scores={},
        calibration_met=True,
        safety_met=True,
        contamination_detected=False,
        capability_loss_notes=("capacity",),
    )
    assert result.passed is False
    assert "no measured score" in result.blocked_reasons[0]
    with pytest.raises(ValueError):
        _invoke(
            validate_student,
            "bad",
            retention_scores={},
            calibration_met=True,
            safety_met=True,
            contamination_detected=False,
        )
    with pytest.raises(ValueError):
        _invoke(
            validate_student,
            plan,
            retention_scores=[],
            calibration_met=True,
            safety_met=True,
            contamination_detected=False,
        )


def test_reasoning_value_objects_and_engine_reject_invalid_state() -> None:
    """Reasoning artifacts, enum evidence, and bounded engines fail closed."""
    uncertainty = Uncertainty(score=0.1, method="test", limitations=())
    cases: tuple[Callable[[], object], ...] = (
        lambda: _invoke(StepArtifact, "plan", -1, "tool", "why"),
        lambda: _invoke(StepArtifact, "plan", 0, "", "why"),
        lambda: _invoke(StepArtifact, "plan", 0, "tool", ""),
        lambda: NumericalAnswer(1, "", 1, 0),
        lambda: _invoke(IndependentCheck, "bad", VerificationStatus.PASS),
        lambda: _invoke(IndependentCheck, "benchmark", "bad"),
        lambda: ReasoningResult(ResultStatus.SUCCEEDED, None, "", (), (), "query", (), uncertainty),
        lambda: ReasoningResult(ResultStatus.SUCCEEDED, None, "why", (), (), "", (), uncertainty),
        lambda: ReasoningEngine(max_steps=0),
    )
    for construct in cases:
        with pytest.raises(ValueError):
            construct()
    engine = ReasoningEngine(max_steps=2)
    assert engine.phase.value == "plan"
    assert engine.steps == ()
    engine.plan("plan")
    with pytest.raises(ValueError):
        engine.act(tool="", rationale="act")


def _source(record_id: str, **overrides: Any) -> Source:
    kwargs: dict[str, Any] = {
        "record_id": record_id,
        "kind": "source",
        "version": "1.0.0",
        "sha256": _SHA_A,
        "creator": "tester",
        "license": "Apache-2.0",
        "origin_uri": "artifact://source",
        "dependency_lock_sha256": _SHA_B,
    }
    kwargs.update(overrides)
    return Source(**kwargs)


def test_registry_transition_guards_are_fail_closed_and_idempotent() -> None:
    """Alias, tombstone, and supersede mutations require exact owned records."""
    with pytest.raises(ValueError):
        _source("bad", tombstone=True)
    with pytest.raises(ValueError):
        _source("bad", supersedes="")
    registry = Registry()
    original = registry.publish(_source("old"))
    with pytest.raises(ValueError):
        registry.set_alias("", "old")
    with pytest.raises(KeyError):
        registry.set_alias("production", "missing")
    with pytest.raises(ValueError):
        registry.tombstone("old", reason="")
    with pytest.raises(KeyError):
        registry.tombstone("missing", reason="cleanup")
    tombstoned = registry.tombstone("old", reason="retired")
    assert registry.tombstone("old", reason="again") is tombstoned
    assert registry.list_all() == [tombstoned]
    with pytest.raises(KeyError):
        registry.supersede("missing", _source("new"))
    with pytest.raises(ValueError):
        registry.supersede("old", _source("new", supersedes="different"))
    assert original.tombstone is False


def test_speech_contract_rejects_invalid_enums_segments_and_results() -> None:
    """ASR parsing rejects invalid enum, timing, speaker, and result state."""
    cases: tuple[Callable[[], object], ...] = (
        lambda: _invoke(ASRRequest, "artifact://audio", timestamp_granularity="bad"),
        lambda: _invoke(ASRRequest, "artifact://audio", privacy_class="bad"),
        lambda: ASRRequest("artifact://audio", audio_retention_seconds=-1),
        lambda: ASRRequest("artifact://audio", speaker_count_bounds=(2, 1)),
        lambda: _invoke(ASRSegment, 1, 0, 1, None, "en", 1),
        lambda: _invoke(ASRSegment, "text", "bad", 1, None, "en", 1),
        lambda: ASRSegment("text", 0, 1, "", "en", 1),
        lambda: ASRResult(" ", (), "en", True, 0),
        lambda: ASRResult("id", (), "en", True, -1),
        lambda: ASRResult("id", (), "en", True, 0, retained_artifact_uri=""),
    )
    for construct in cases:
        with pytest.raises(ValueError):
            construct()
    with pytest.raises(ValueError):
        speech_coerce_enum(object(), PrivacyClass, "privacy")


def test_speech_consent_tts_and_wer_edges() -> None:
    """Consent is voice-bound and TTS/WER edge inputs remain bounded."""
    consent = VoiceConsent("custom:alice", _SHA_A, "demo", 100, "audit")
    assert check_voice_consent(voice_id="stock:default", consent=consent, now=0).audit_id == "audit"
    assert check_voice_consent(voice_id="custom:bob", consent=consent, now=0).allowed is False
    for kwargs in (
        {"pace": 0},
        {"sample_rate_hz": 0},
        {"pronunciation_lexicon_uri": ""},
    ):
        with pytest.raises(ValueError):
            TTSRequest(text_or_ssml="hello", language="en", voice_id="stock:default", **kwargs)
    assert word_error_rate("", "inserted") == 1.0
    assert word_error_rate("one two", "one two three") == 0.5


def test_vision_grounding_and_request_result_guards() -> None:
    """Vision input and grounded output validation reject malformed evidence."""
    bbox = BoundingBox(0, 0, 1, 1)
    cases: tuple[Callable[[], object], ...] = (
        lambda: BoundingBox(-1, 0, 1, 1),
        lambda: Classification("", 0.5),
        lambda: Detection("object", 2, bbox),
        lambda: _invoke(Detection, "object", 0.5, "bad"),
        lambda: Segmentation("object", 2, "artifact://mask"),
        lambda: Segmentation("object", 0.5, ""),
        lambda: _invoke(VisionRequest, "artifact://image", "bad"),
        lambda: VisionRequest("artifact://image", VisionTask.VQA, confidence_threshold=2),
        lambda: VisionRequest("artifact://image", VisionTask.VQA, prompt=""),
        lambda: VisionResult(" ", (), (), (), "model"),
        lambda: VisionResult("id", (), (), (), "model", ocr_text=""),
        lambda: VisionResult("id", (), (), (), "model", vqa_answer=""),
        lambda: VisionResult("id", (), (), (), "model", embedding_artifact_uri=""),
    )
    for construct in cases:
        with pytest.raises(ValueError):
            construct()


def _image_record_kwargs() -> dict[str, Any]:
    return {
        "source_artifact_uri": "artifact://source",
        "source_sha256": _SHA_A,
        "output_artifact_uri": "artifact://output",
        "output_sha256": _SHA_B,
        "operations": (ImageOperation(ImageOperationType.UPSCALE),),
        "model_version": "model",
        "content_policy_decision": "allow",
    }


def test_image_operation_and_provenance_guards() -> None:
    """Edit graphs reject invalid enum, parameter, mask, operation, and seed state."""
    cases: tuple[Callable[[], object], ...] = (
        lambda: _invoke(ImageOperation, "bad"),
        lambda: ImageOperation(ImageOperationType.UPSCALE, ("",)),
        lambda: ImageEditRecord(**{**_image_record_kwargs(), "operations": ()}),
        lambda: ImageEditRecord(**{**_image_record_kwargs(), "operations": ("bad",)}),
        lambda: ImageEditRecord(**{**_image_record_kwargs(), "mask_artifact_uri": "artifact://mask"}),
        lambda: ImageEditRecord(**{**_image_record_kwargs(), "mask_sha256": _SHA_A}),
        lambda: ImageEditRecord(**{**_image_record_kwargs(), "seed": -1}),
        lambda: ImageEditRecord(**{**_image_record_kwargs(), "content_domain": "bad"}),
    )
    for construct in cases:
        with pytest.raises(ValueError):
            construct()
    generated = ImageEditRecord(
        **{
            **_image_record_kwargs(),
            "operations": (ImageOperation(ImageOperationType.TEXT_TO_IMAGE),),
            "content_domain": ContentDomain.GENERAL,
        }
    )
    assert generated.is_synthetic_modified is True
    assert generated.is_measurement is False
