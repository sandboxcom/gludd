"""Unit tests for AIML Phase D: image generation/editing (AIML-013).

Covers docs/specs/FEATURE_AI_ML_EXPERT.md §7.3 (image generation/editing):

  - :class:`ImageRequest` validates prompt + constraints per operation kind.
  - :class:`ImageEditor.plan_edit` produces a reversible operation graph;
    original source images are immutable (retained by digest).
  - Masks, seeds, model/adapter versions are tracked on every plan.
  - :class:`ImageEditor.validate_content_policy` records a content-policy
    decision (spec §7.3, §11).
  - Medical / forensic images are labeled synthetic/modified and cannot be
    presented as measurements (AIML-AT-012).

Provenance primitives (``ImageEditRecord`` / ``ImageOperation``) are reused
from :mod:`general_ludd.ai_ml.vision`; these tests exercise them through the
``images`` module's plan → finalize → record chain.
"""

from __future__ import annotations

import pytest

from general_ludd.ai_ml.images import (
    ContentPolicyDecision,
    ImageEditor,
    ImageEditPlan,
    ImageGenOperation,
    ImageRequest,
)
from general_ludd.ai_ml.schemas import Constraints, DataClassification
from general_ludd.ai_ml.vision import ContentDomain, ImageEditRecord, ImageOperationType

_SHA_SOURCE = "a" * 64
_SHA_MASK = "b" * 64
_SHA_OUTPUT = "c" * 64


def _src_request(operation: ImageGenOperation = ImageGenOperation.IMAGE_TO_IMAGE, **kw) -> ImageRequest:
    """Generic source-consuming request (image-to-image by default — needs a
    source but not a mask). Tests that exercise inpaint/outpaint pass a mask
    explicitly or override ``operation``."""
    base = dict(
        prompt="enhance the photo",
        operation=operation,
        model_version="sdxl-1.0",
        source_artifact_uri="artifact://img/src.png",
        source_sha256=_SHA_SOURCE,
    )
    base.update(kw)
    return ImageRequest(**base)


# ---------------------------------------------------------------------------
# ImageRequest contract validation (spec §7.3)
# ---------------------------------------------------------------------------


class TestImageRequestContract:
    def test_text_to_image_requires_prompt(self) -> None:
        """Spec §7.3: text-to-image generation is prompt-driven."""
        req = ImageRequest(
            prompt="a calm lake at dawn",
            operation=ImageGenOperation.TEXT_TO_IMAGE,
            model_version="sdxl-1.0",
        )
        assert req.prompt == "a calm lake at dawn"
        assert req.operation is ImageGenOperation.TEXT_TO_IMAGE
        assert req.source_artifact_uri is None

    def test_text_to_image_rejects_empty_prompt(self) -> None:
        with pytest.raises(ValueError, match="prompt"):
            ImageRequest(
                prompt="   ",
                operation=ImageGenOperation.TEXT_TO_IMAGE,
                model_version="sdxl-1.0",
            )

    def test_text_to_image_retains_constraints(self) -> None:
        constraints = Constraints(budget_usd=1.5, deadline_s=120)
        req = ImageRequest(
            prompt="a calm lake",
            operation=ImageGenOperation.TEXT_TO_IMAGE,
            model_version="sdxl-1.0",
            constraints=constraints,
        )
        assert req.constraints is constraints
        assert req.constraints.budget_usd == 1.5

    def test_inpaint_requires_source_and_mask(self) -> None:
        with pytest.raises(ValueError, match="source_artifact_uri"):
            ImageRequest(
                prompt="remove the sign",
                operation=ImageGenOperation.INPAINT,
                model_version="sdxl-1.0",
                mask_artifact_uri="artifact://mask/m.png",
                mask_sha256=_SHA_MASK,
            )
        with pytest.raises(ValueError, match="mask_artifact_uri"):
            ImageRequest(
                prompt="remove the sign",
                operation=ImageGenOperation.INPAINT,
                model_version="sdxl-1.0",
                source_artifact_uri="artifact://img/src.png",
                source_sha256=_SHA_SOURCE,
            )

    def test_outpaint_requires_source_and_mask(self) -> None:
        with pytest.raises(ValueError, match="source_artifact_uri"):
            ImageRequest(
                prompt="extend the background",
                operation=ImageGenOperation.OUTPAINT,
                model_version="sdxl-1.0",
            )

    def test_upscale_requires_source_but_not_mask(self) -> None:
        req = ImageRequest(
            prompt="",
            operation=ImageGenOperation.UPSCALE,
            model_version="esrgan-4x",
            source_artifact_uri="artifact://img/src.png",
            source_sha256=_SHA_SOURCE,
        )
        assert req.operation is ImageGenOperation.UPSCALE
        assert req.mask_artifact_uri is None

    def test_upscale_rejects_missing_source(self) -> None:
        with pytest.raises(ValueError, match="source_artifact_uri"):
            ImageRequest(
                prompt="",
                operation=ImageGenOperation.UPSCALE,
                model_version="esrgan-4x",
            )

    def test_source_uri_and_digest_must_both_be_set(self) -> None:
        with pytest.raises(ValueError, match="source_artifact_uri and source_sha256"):
            ImageRequest(
                prompt="x",
                operation=ImageGenOperation.IMAGE_TO_IMAGE,
                model_version="m",
                source_artifact_uri="artifact://img/src.png",
                source_sha256=None,
            )

    def test_mask_uri_and_digest_must_both_be_set(self) -> None:
        with pytest.raises(ValueError, match="mask_artifact_uri and mask_sha256"):
            _src_request(
                operation=ImageGenOperation.INPAINT,
                mask_artifact_uri="artifact://mask/m.png",
                mask_sha256=None,
            )

    def test_invalid_operation_enum_rejected(self) -> None:
        with pytest.raises(ValueError, match="operation"):
            ImageRequest(
                prompt="x",
                operation="not-a-real-op",  # type: ignore[arg-type]
                model_version="m",
            )

    def test_negative_seed_rejected(self) -> None:
        with pytest.raises(ValueError, match="seed"):
            _src_request(seed=-7)

    def test_invalid_source_digest_rejected(self) -> None:
        with pytest.raises(ValueError, match="source_sha256"):
            ImageRequest(
                prompt="x",
                operation=ImageGenOperation.IMAGE_TO_IMAGE,
                model_version="m",
                source_artifact_uri="artifact://img/src.png",
                source_sha256="not-a-sha",
            )


# ---------------------------------------------------------------------------
# ImageEditor.plan_edit — operation graph, immutability, tracking (spec §7.3)
# ---------------------------------------------------------------------------


class TestImageEditorPlan:
    def test_plan_edit_produces_nonempty_operation_graph(self) -> None:
        """Spec §7.3: every edit produces a reversible operation graph."""
        editor = ImageEditor()
        plan = editor.plan_edit(
            _src_request(
                operation=ImageGenOperation.INPAINT,
                mask_artifact_uri="artifact://mask/m.png",
                mask_sha256=_SHA_MASK,
            )
        )
        assert isinstance(plan, ImageEditPlan)
        assert len(plan.operation_graph) >= 1
        node = plan.operation_graph[0]
        assert node.op_type is ImageOperationType.INPAINT

    def test_source_image_is_immutable(self) -> None:
        """Spec §7.3: original inputs are immutable. The plan retains the
        source by digest and never mutates the request's source reference."""
        editor = ImageEditor()
        req = _src_request()
        plan = editor.plan_edit(req)
        assert plan.source_is_immutable is True
        assert plan.source_sha256 == _SHA_SOURCE
        assert plan.source_artifact_uri == "artifact://img/src.png"
        # request object untouched
        assert req.source_sha256 == _SHA_SOURCE

    def test_plan_tracks_mask_seed_model_adapter(self) -> None:
        """Spec §7.3: every edit records masks, seeds, model and adapter versions."""
        editor = ImageEditor()
        req = _src_request(
            operation=ImageGenOperation.INPAINT,
            mask_artifact_uri="artifact://mask/m.png",
            mask_sha256=_SHA_MASK,
            seed=42,
            adapter_version="lora-style-v3",
        )
        plan = editor.plan_edit(req)
        assert plan.mask_sha256 == _SHA_MASK
        assert plan.mask_artifact_uri == "artifact://mask/m.png"
        assert plan.seed == 42
        assert plan.model_version == "sdxl-1.0"
        assert plan.adapter_version == "lora-style-v3"

    def test_plan_is_reversible_and_reverse_graph_inverts_order(self) -> None:
        """Spec §7.3: operation graph is reversible. reversed_graph returns
        the inverse sequence for undo auditing."""
        editor = ImageEditor()
        plan = editor.plan_edit(_src_request())
        assert plan.is_reversible is True
        assert tuple(reversed(plan.operation_graph)) == plan.reversed_graph()

    def test_plan_carries_content_domain(self) -> None:
        editor = ImageEditor()
        req = _src_request()
        req_medical = ImageRequest(
            prompt="denoise",
            operation=ImageGenOperation.RESTORE,
            model_version="med-restore-1",
            source_artifact_uri="artifact://img/mri.png",
            source_sha256=_SHA_SOURCE,
            content_domain=ContentDomain.MEDICAL,
        )
        assert editor.plan_edit(req).content_domain is ContentDomain.GENERAL
        assert editor.plan_edit(req_medical).content_domain is ContentDomain.MEDICAL

    def test_plan_edit_rejects_non_request(self) -> None:
        with pytest.raises(ValueError, match="ImageRequest"):
            ImageEditor().plan_edit("not-a-request")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Content policy (spec §7.3, §11)
# ---------------------------------------------------------------------------


class TestContentPolicy:
    def test_clean_request_is_allowed(self) -> None:
        decision = ImageEditor().validate_content_policy(_src_request())
        assert isinstance(decision, ContentPolicyDecision)
        assert decision.allowed is True
        assert decision.decision == "allow"

    def test_restricted_data_without_offline_is_refused(self) -> None:
        """Spec §11: restricted data_classification requires offline mode."""
        req = _src_request(
            constraints=Constraints(
                data_classification=DataClassification.RESTRICTED,
                offline=False,
            ),
        )
        decision = ImageEditor().validate_content_policy(req)
        assert decision.allowed is False
        assert "restricted" in decision.reason.lower()

    def test_restricted_data_with_offline_is_allowed(self) -> None:
        req = _src_request(
            constraints=Constraints(
                data_classification=DataClassification.RESTRICTED,
                offline=True,
            ),
        )
        assert ImageEditor().validate_content_policy(req).allowed is True

    def test_banned_keyword_prompt_is_refused(self) -> None:
        req = ImageRequest(
            prompt="create a deepfake-real-person image",
            operation=ImageGenOperation.TEXT_TO_IMAGE,
            model_version="sdxl-1.0",
        )
        decision = ImageEditor().validate_content_policy(req)
        assert decision.allowed is False
        assert "banned" in decision.reason.lower()

    def test_decision_records_id_and_ruleset_digest(self) -> None:
        """Spec §4.2: every policy block carries decision_id + ruleset_sha256."""
        decision = ImageEditor().validate_content_policy(_src_request())
        assert decision.decision_id.startswith("img-pol-")
        assert len(decision.ruleset_sha256) == 64

    def test_validate_rejects_non_request(self) -> None:
        with pytest.raises(ValueError, match="ImageRequest"):
            ImageEditor().validate_content_policy(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# finalize_edit → ImageEditRecord provenance (spec §7.3, AIML-AT-012)
# ---------------------------------------------------------------------------


class TestFinalizeEditRecord:
    def test_finalize_produces_edit_record_with_policy_decision(self) -> None:
        editor = ImageEditor()
        req = _src_request(
            operation=ImageGenOperation.INPAINT,
            mask_artifact_uri="artifact://mask/m.png",
            mask_sha256=_SHA_MASK,
            seed=9,
        )
        plan = editor.plan_edit(req)
        policy = editor.validate_content_policy(req)
        record = editor.finalize_edit(
            plan,
            output_artifact_uri="artifact://img/out.png",
            output_sha256=_SHA_OUTPUT,
            content_policy_decision=policy,
        )
        assert isinstance(record, ImageEditRecord)
        assert record.content_policy_decision == "allow"
        assert record.output_sha256 == _SHA_OUTPUT
        assert record.source_sha256 == _SHA_SOURCE
        assert record.mask_sha256 == _SHA_MASK
        assert record.seed == 9
        assert record.model_version == "sdxl-1.0"
        assert record.operations == plan.operation_graph

    def test_medical_image_finalized_as_synthetic(self) -> None:
        """Spec §7.3: medical images must be labeled synthetic/modified and
        cannot be presented as measurements."""
        editor = ImageEditor()
        req = ImageRequest(
            prompt="enhance",
            operation=ImageGenOperation.RESTORE,
            model_version="med-1",
            source_artifact_uri="artifact://img/mri.png",
            source_sha256=_SHA_SOURCE,
            content_domain=ContentDomain.MEDICAL,
        )
        plan = editor.plan_edit(req)
        policy = editor.validate_content_policy(req)
        record = editor.finalize_edit(
            plan,
            output_artifact_uri="artifact://img/mri-out.png",
            output_sha256=_SHA_OUTPUT,
            content_policy_decision=policy,
        )
        assert record.is_synthetic_modified is True
        assert record.is_measurement is False

    def test_forensic_image_finalized_as_synthetic(self) -> None:
        editor = ImageEditor()
        req = ImageRequest(
            prompt="",
            operation=ImageGenOperation.UPSCALE,
            model_version="forensic-1",
            source_artifact_uri="artifact://img/evidence.png",
            source_sha256=_SHA_SOURCE,
            content_domain=ContentDomain.FORENSIC,
        )
        plan = editor.plan_edit(req)
        record = editor.finalize_edit(
            plan,
            output_artifact_uri="artifact://img/evidence-up.png",
            output_sha256=_SHA_OUTPUT,
            content_policy_decision=editor.validate_content_policy(req),
        )
        assert record.is_synthetic_modified is True

    def test_text_to_image_finalize_uses_prompt_as_immutable_source(self) -> None:
        """Spec §7.3: original inputs are immutable. For text-to-image the
        immutable original is the prompt; its digest becomes the source_sha256."""
        editor = ImageEditor()
        req = ImageRequest(
            prompt="a calm lake at dawn",
            operation=ImageGenOperation.TEXT_TO_IMAGE,
            model_version="sdxl-1.0",
            seed=100,
        )
        plan = editor.plan_edit(req)
        assert plan.source_artifact_uri is None  # no image source
        record = editor.finalize_edit(
            plan,
            output_artifact_uri="artifact://img/gen.png",
            output_sha256=_SHA_OUTPUT,
            content_policy_decision=editor.validate_content_policy(req),
        )
        assert record.is_synthetic_modified is True  # generative op
        assert len(record.source_sha256) == 64  # prompt digest
        assert record.seed == 100

    def test_finalize_rejects_bad_output_digest(self) -> None:
        editor = ImageEditor()
        plan = editor.plan_edit(_src_request())
        policy = editor.validate_content_policy(_src_request())
        with pytest.raises(ValueError, match="output_sha256"):
            editor.finalize_edit(
                plan,
                output_artifact_uri="artifact://img/out.png",
                output_sha256="short",
                content_policy_decision=policy,
            )
