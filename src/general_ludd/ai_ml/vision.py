"""AIML Phase D — vision understanding (AIML-012) and image editing (AIML-013).

Implements docs/specs/FEATURE_AI_ML_EXPERT.md §7.3:

  - :class:`VisionRequest` / :class:`VisionResult` — the vision understanding
    contract: classification, detection, segmentation, OCR, captioning, VQA,
    similarity, document layout. Results include pixel/region grounding,
    confidence, transform history, and model/version (spec §7.3, AIML-AT-012).
  - :class:`Classification` / :class:`Detection` / :class:`Segmentation` /
    :class:`BoundingBox` — grounded region results.
  - :class:`ImageEditRecord` / :class:`ImageOperation` /
    :class:`ImageOperationType` / :class:`ContentDomain` — the image edit
    provenance contract. Original inputs are immutable. Every edit produces a
    reversible operation graph, masks, seeds where available, model and adapter
    versions, content-policy decision, and provenance metadata. Medical,
    forensic, and scientific images are labeled synthetic/modified and cannot
    be presented as measurements (spec §7.3).

This module holds the typed contract; the ``vision_understand`` and
``image_create`` ansible roles wrap these entry points and never shell out.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from general_ludd.ai_ml.schemas import _require_nonempty_str, _require_sha256

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class VisionTask(enum.StrEnum):
    """Vision understanding task kinds (spec §7.3)."""

    CLASSIFY = "classify"
    DETECT = "detect"
    SEGMENT = "segment"
    OCR = "ocr"
    CAPTION = "caption"
    VQA = "vqa"
    EMBED = "embed"
    SIMILARITY = "similarity"
    DOC_LAYOUT = "doc_layout"


class ImageOperationType(enum.StrEnum):
    """Reversible image operations supported by ``image_create`` (spec §7.3).

    Each operation records its parameters so the edit graph can be inverted or
    audited. Generation from text is a leaf op; inpaint/outpaint/etc. consume
    a source + mask.
    """

    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"
    INPAINT = "inpaint"
    OUTPAINT = "outpaint"
    UPSCALE = "upscale"
    RESTORE = "restore"
    RELIGHT = "relight"
    BACKGROUND = "background"
    SUBJECT = "subject"
    CONVERT = "convert"


class ContentDomain(enum.StrEnum):
    """Content domain of the source image (spec §7.3).

    Images in MEDICAL, FORENSIC, or SCIENTIFIC domains MUST be labeled
    synthetic/modified after any edit and cannot be presented as measurements.
    """

    GENERAL = "general"
    MEDICAL = "medical"
    FORENSIC = "forensic"
    SCIENTIFIC = "scientific"


# Domains whose edits must be labeled synthetic/modified (spec §7.3).
_MEASUREMENT_DOMAINS: frozenset[ContentDomain] = frozenset(
    {ContentDomain.MEDICAL, ContentDomain.FORENSIC, ContentDomain.SCIENTIFIC}
)


# ---------------------------------------------------------------------------
# Grounded vision result elements (spec §7.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundingBox:
    """Axis-aligned region grounding in source-image pixels (spec §7.3).

    ``(x, y)`` is the top-left corner; ``width``/``height`` extend right/down.
    All values are in pixels and must be non-negative.
    """

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        for name in ("x", "y", "width", "height"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"BoundingBox.{name} must be >= 0, got {value!r}")


@dataclass(frozen=True)
class Classification:
    """A whole-image classification with confidence (spec §7.3)."""

    label: str
    confidence: float

    def __post_init__(self) -> None:
        _require_nonempty_str(self.label, "label")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence}")


@dataclass(frozen=True)
class Detection:
    """A region-grounded detection (spec §7.3: "results include pixel or region grounding")."""

    label: str
    confidence: float
    bbox: BoundingBox

    def __post_init__(self) -> None:
        _require_nonempty_str(self.label, "label")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence}")
        if not isinstance(self.bbox, BoundingBox):
            raise ValueError("bbox must be a BoundingBox instance")


@dataclass(frozen=True)
class Segmentation:
    """A mask-grounded segmentation (spec §7.3 region grounding)."""

    label: str
    confidence: float
    mask_artifact_uri: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.label, "label")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence}")
        _require_nonempty_str(self.mask_artifact_uri, "mask_artifact_uri")


# ---------------------------------------------------------------------------
# Vision request / result (spec §7.3, AIML-AT-012)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VisionRequest:
    """Vision understanding contract input (spec §7.3)."""

    image_artifact_uri: str
    task: VisionTask
    prompt: str | None = None
    confidence_threshold: float = 0.0
    requested_classes: tuple[str, ...] = ()
    request_id: str = ""

    def __post_init__(self) -> None:
        _require_nonempty_str(self.image_artifact_uri, "image_artifact_uri")
        object.__setattr__(self, "task", _coerce_enum(self.task, VisionTask, "task"))
        if not (0.0 <= self.confidence_threshold <= 1.0):
            raise ValueError(f"confidence_threshold must be in [0.0, 1.0], got {self.confidence_threshold}")
        if self.prompt is not None and not self.prompt.strip():
            raise ValueError("prompt, when set, must be non-empty")


@dataclass(frozen=True)
class VisionResult:
    """Vision understanding contract output (spec §7.3).

    Results include pixel/region grounding, confidence, transform history, and
    model/version. OCR text, VQA answer, and caption surface as top-level
    fields when the task produces them.
    """

    request_id: str
    classifications: tuple[Classification, ...]
    detections: tuple[Detection, ...]
    segmentations: tuple[Segmentation, ...]
    model_version: str
    transform_history: tuple[str, ...] = ()
    ocr_text: str | None = None
    vqa_answer: str | None = None
    caption: str | None = None
    embedding_artifact_uri: str | None = None

    def __post_init__(self) -> None:
        if self.request_id and not self.request_id.strip():
            raise ValueError("request_id, when set, must be non-empty")
        _require_nonempty_str(self.model_version, "model_version")
        if self.ocr_text is not None and not self.ocr_text.strip():
            raise ValueError("ocr_text, when set, must be non-empty")
        if self.vqa_answer is not None and not self.vqa_answer.strip():
            raise ValueError("vqa_answer, when set, must be non-empty")
        if self.embedding_artifact_uri is not None and not self.embedding_artifact_uri.strip():
            raise ValueError("embedding_artifact_uri, when set, must be non-empty")


# ---------------------------------------------------------------------------
# Image edit provenance (spec §7.3, AIML-AT-012)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImageOperation:
    """One node in the reversible image-edit operation graph (spec §7.3).

    ``op_type`` identifies the operation (inpaint, outpaint, upscale, restore,
    relight, convert, etc.); ``params`` is a tuple of ``key=value`` strings
    capturing every parameter needed to reproduce or invert the operation.
    """

    op_type: ImageOperationType
    params: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "op_type", _coerce_enum(self.op_type, ImageOperationType, "op_type"))
        for p in self.params:
            if not isinstance(p, str) or not p.strip():
                raise ValueError(f"params entries must be non-empty strings, got {p!r}")


@dataclass(frozen=True)
class ImageEditRecord:
    """Provenance record for a generated or edited image (spec §7.3).

    Spec §7.3: "Original inputs are immutable. Every edit produces a reversible
    operation graph, masks, seeds where available, model and adapter versions,
    content-policy decision, and provenance metadata. Medical, forensic, and
    scientific images must be labeled synthetic/modified and cannot be
    presented as measurements."
    """

    source_artifact_uri: str
    source_sha256: str
    output_artifact_uri: str
    output_sha256: str
    operations: tuple[ImageOperation, ...]
    model_version: str
    content_policy_decision: str
    content_domain: ContentDomain = ContentDomain.GENERAL
    mask_artifact_uri: str | None = None
    mask_sha256: str | None = None
    seed: int | None = None
    adapter_version: str | None = None
    provenance_metadata: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty_str(self.source_artifact_uri, "source_artifact_uri")
        _require_sha256(self.source_sha256, "source_sha256")
        _require_nonempty_str(self.output_artifact_uri, "output_artifact_uri")
        _require_sha256(self.output_sha256, "output_sha256")
        _require_nonempty_str(self.model_version, "model_version")
        _require_nonempty_str(self.content_policy_decision, "content_policy_decision")
        object.__setattr__(
            self,
            "content_domain",
            _coerce_enum(self.content_domain, ContentDomain, "content_domain"),
        )
        if not self.operations:
            raise ValueError("operations graph must contain at least one ImageOperation")
        for op in self.operations:
            if not isinstance(op, ImageOperation):
                raise ValueError("operations entries must be ImageOperation instances")
        if self.mask_artifact_uri is not None:
            _require_nonempty_str(self.mask_artifact_uri, "mask_artifact_uri")
            if self.mask_sha256 is None:
                raise ValueError("mask_sha256 is required when mask_artifact_uri is set")
            _require_sha256(self.mask_sha256, "mask_sha256")
        if self.mask_sha256 is not None and self.mask_artifact_uri is None:
            raise ValueError("mask_artifact_uri is required when mask_sha256 is set")
        if self.seed is not None and (not isinstance(self.seed, int) or self.seed < 0):
            raise ValueError(f"seed, when set, must be a non-negative int, got {self.seed!r}")

    @property
    def is_synthetic_modified(self) -> bool:
        """True when the output MUST be labeled synthetic/modified (spec §7.3).

        Medical, forensic, and scientific images are always synthetic/modified
        after an edit regardless of operation kind. General-domain images are
        only flagged when the operation graph includes generative edits.
        """
        if self.content_domain in _MEASUREMENT_DOMAINS:
            return True
        generative = {
            ImageOperationType.TEXT_TO_IMAGE,
            ImageOperationType.IMAGE_TO_IMAGE,
            ImageOperationType.INPAINT,
            ImageOperationType.OUTPAINT,
        }
        return any(op.op_type in generative for op in self.operations)

    @property
    def is_measurement(self) -> bool:
        """False for any edited image in a measurement domain (spec §7.3).

        Spec: medical/forensic/scientific images "cannot be presented as
        measurements" once edited. General-domain edits are not measurements
        either; this property exists so downstream code can gate on it
        uniformly.
        """
        return False


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


__all__ = [
    "BoundingBox",
    "Classification",
    "ContentDomain",
    "Detection",
    "ImageEditRecord",
    "ImageOperation",
    "ImageOperationType",
    "Segmentation",
    "VisionRequest",
    "VisionResult",
    "VisionTask",
]
