"""Unit tests for AIML Phase D: vision understanding (AIML-012) and image editing (AIML-013).

Covers docs/specs/FEATURE_AI_ML_EXPERT.md §7.3:

  - Vision understanding supports classification, detection, segmentation,
    OCR, VQA; results include pixel/region grounding, confidence, transform
    history, model/version (AIML-AT-012).
  - Image creation produces a reversible operation graph; original inputs
    are immutable; every edit records masks, seeds, model/adapter versions,
    content-policy decision, and provenance (AIML-AT-012).
  - Medical, forensic, and scientific images must be labeled
    synthetic/modified and cannot be presented as measurements.
"""

from __future__ import annotations

import pytest

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

_SHA_IMG = "a" * 64
_SHA_SOURCE = "b" * 64
_SHA_MASK = "c" * 64
_SHA_OUTPUT = "d" * 64


# ---------------------------------------------------------------------------
# AIML-012 — Vision understanding contract (spec §7.3)
# ---------------------------------------------------------------------------


class TestVisionContract:
    def test_request_carries_image_uri_and_task(self) -> None:
        req = VisionRequest(
            image_artifact_uri="artifact://img/x.png",
            task=VisionTask.CLASSIFY,
        )
        assert req.image_artifact_uri == "artifact://img/x.png"
        assert req.task is VisionTask.CLASSIFY

    def test_result_classification_carries_label_and_confidence(self) -> None:
        cls = Classification(label="cat", confidence=0.93)
        result = VisionResult(
            request_id="r1",
            classifications=(cls,),
            detections=(),
            segmentations=(),
            model_version="vit-2024-06-01",
            transform_history=(),
        )
        assert result.classifications[0].label == "cat"
        assert 0.0 <= result.classifications[0].confidence <= 1.0

    def test_result_detection_has_region_grounding_bbox(self) -> None:
        """Spec §7.3: results include pixel or region grounding."""
        det = Detection(
            label="dog",
            confidence=0.88,
            bbox=BoundingBox(x=10, y=20, width=100, height=80),
        )
        result = VisionResult(
            request_id="r1",
            classifications=(),
            detections=(det,),
            segmentations=(),
            model_version="vit-2024-06-01",
            transform_history=(),
        )
        d = result.detections[0]
        assert d.label == "dog"
        assert d.bbox.x == 10
        assert d.bbox.width == 100

    def test_result_segmentation_has_mask_artifact(self) -> None:
        seg = Segmentation(
            label="road",
            confidence=0.79,
            mask_artifact_uri="artifact://mask/road.png",
        )
        result = VisionResult(
            request_id="r1",
            classifications=(),
            detections=(),
            segmentations=(seg,),
            model_version="vit-2024-06-01",
            transform_history=(),
        )
        assert result.segmentations[0].mask_artifact_uri == "artifact://mask/road.png"

    def test_result_ocr_and_vqa_fields(self) -> None:
        result = VisionResult(
            request_id="r1",
            classifications=(),
            detections=(),
            segmentations=(),
            ocr_text="EXIT",
            vqa_answer="A red stop sign",
            model_version="vit-2024-06-01",
            transform_history=("resize:512x512",),
        )
        assert result.ocr_text == "EXIT"
        assert result.vqa_answer == "A red stop sign"
        assert result.transform_history == ("resize:512x512",)

    def test_classification_rejects_bad_confidence(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            Classification(label="cat", confidence=2.0)


# ---------------------------------------------------------------------------
# AIML-013 — Image edit provenance (spec §7.3, AIML-AT-012)
# ---------------------------------------------------------------------------


class TestImageEditRecord:
    def _ops(self) -> tuple[ImageOperation, ...]:
        return (
            ImageOperation(
                op_type=ImageOperationType.INPAINT,
                params=("strength=0.8",),
            ),
            ImageOperation(
                op_type=ImageOperationType.UPSCALE,
                params=("factor=2",),
            ),
        )

    def test_record_retains_source_mask_seed_model_opgraph(self) -> None:
        """Spec §7.3: every edit produces a reversible operation graph, masks,
        seeds where available, model and adapter versions, content-policy
        decision, and provenance metadata."""
        rec = ImageEditRecord(
            source_artifact_uri="artifact://img/src.png",
            source_sha256=_SHA_SOURCE,
            output_artifact_uri="artifact://img/out.png",
            output_sha256=_SHA_OUTPUT,
            operations=self._ops(),
            mask_artifact_uri="artifact://mask/m.png",
            mask_sha256=_SHA_MASK,
            seed=12345,
            model_version="sdxl-1.0",
            adapter_version="lora-style-v2",
            content_policy_decision="allowed",
            provenance_metadata=("edited", "diffusion"),
            content_domain=ContentDomain.GENERAL,
        )
        assert rec.source_sha256 == _SHA_SOURCE
        assert rec.mask_artifact_uri == "artifact://mask/m.png"
        assert rec.mask_sha256 == _SHA_MASK
        assert rec.seed == 12345
        assert rec.model_version == "sdxl-1.0"
        assert rec.adapter_version == "lora-style-v2"
        assert len(rec.operations) == 2
        assert rec.operations[0].op_type is ImageOperationType.INPAINT

    def test_medical_images_labeled_synthetic(self) -> None:
        """Spec §7.3: medical/forensic/scientific images must be labeled
        synthetic/modified and cannot be presented as measurements."""
        rec = ImageEditRecord(
            source_artifact_uri="artifact://img/mri.png",
            source_sha256=_SHA_SOURCE,
            output_artifact_uri="artifact://img/mri-enhanced.png",
            output_sha256=_SHA_OUTPUT,
            operations=self._ops(),
            mask_artifact_uri=None,
            mask_sha256=None,
            seed=None,
            model_version="med-edit-1",
            adapter_version=None,
            content_policy_decision="allowed-medical",
            provenance_metadata=("edited",),
            content_domain=ContentDomain.MEDICAL,
        )
        assert rec.is_synthetic_modified is True
        assert rec.is_measurement is False

    def test_forensic_images_not_presented_as_measurements(self) -> None:
        rec = ImageEditRecord(
            source_artifact_uri="artifact://img/evidence.png",
            source_sha256=_SHA_SOURCE,
            output_artifact_uri="artifact://img/evidence-upscaled.png",
            output_sha256=_SHA_OUTPUT,
            operations=self._ops(),
            mask_artifact_uri=None,
            mask_sha256=None,
            seed=None,
            model_version="forensic-v1",
            adapter_version=None,
            content_policy_decision="allowed-forensic",
            provenance_metadata=("upscaled",),
            content_domain=ContentDomain.FORENSIC,
        )
        assert rec.is_synthetic_modified is True
        assert rec.is_measurement is False

    def test_general_domain_not_forced_synthetic(self) -> None:
        """A general-domain image with only a non-generative operation (format
        conversion) is NOT forced synthetic; generative ops (inpaint) ARE."""
        convert_only = (
            ImageOperation(
                op_type=ImageOperationType.CONVERT,
                params=("format=png", "color_profile=srgb"),
            ),
        )
        rec = ImageEditRecord(
            source_artifact_uri="artifact://img/src.png",
            source_sha256=_SHA_SOURCE,
            output_artifact_uri="artifact://img/out.png",
            output_sha256=_SHA_OUTPUT,
            operations=convert_only,
            mask_artifact_uri=None,
            mask_sha256=None,
            seed=None,
            model_version="imagemagick-7",
            adapter_version=None,
            content_policy_decision="allowed",
            provenance_metadata=("converted",),
            content_domain=ContentDomain.GENERAL,
        )
        assert rec.is_synthetic_modified is False
