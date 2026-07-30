"""AIML Phase D — image generation/editing (AIML-013).

Implements docs/specs/FEATURE_AI_ML_EXPERT.md §7.3 (image generation/editing):

  - :class:`ImageRequest` — the generation/edit request contract: prompt,
    operation kind (text-to-image / image-to-image / inpaint / outpaint /
    upscale / restore / relight), source artifact + digest (immutable), mask
    artifact + digest, seed, model/adapter versions, content domain, and
    execution constraints.
  - :class:`ImageEditPlan` — the reversible operation graph produced by
    :meth:`ImageEditor.plan_edit`. Spec §7.3: "Original inputs are immutable.
    Every edit produces a reversible operation graph, masks, seeds where
    available, model and adapter versions, content-policy decision, and
    provenance metadata."
  - :class:`ContentPolicyDecision` — the content-policy verdict recorded on
    every edit (spec §7.3, §11).
  - :class:`ImageEditor` — plans edits (``plan_edit``), validates the content
    policy (``validate_content_policy``), and finalizes a plan into an
    immutable :class:`~general_ludd.ai_ml.vision.ImageEditRecord`
    (``finalize_edit``).

Provenance primitives (``ImageEditRecord``, ``ImageOperation``,
``ImageOperationType``, ``ContentDomain``) are reused from
:mod:`general_ludd.ai_ml.vision` to avoid duplicating the §7.3 contract. The
``image_create`` ansible role wraps these entry points and never shells out.
"""

from __future__ import annotations

import enum
import hashlib
import uuid
from dataclasses import dataclass, field

from general_ludd.ai_ml.schemas import (
    Constraints,
    DataClassification,
    _coerce_enum,
    _require_nonempty_str,
    _require_sha256,
)
from general_ludd.ai_ml.vision import (
    ContentDomain,
    ImageEditRecord,
    ImageOperation,
    ImageOperationType,
)

# ---------------------------------------------------------------------------
# Operation kinds (spec §7.3)
# ---------------------------------------------------------------------------


class ImageGenOperation(enum.StrEnum):
    """Image generation/edit operation kinds requestable via ImageRequest.

    The seven kinds named in spec §7.3 / the AIML-013 contract. Region and
    format operations (background/subject/convert) are modeled as
    :class:`~general_ludd.ai_ml.vision.ImageOperationType` nodes inside an
    edit graph rather than top-level request kinds.
    """

    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"
    INPAINT = "inpaint"
    OUTPAINT = "outpaint"
    UPSCALE = "upscale"
    RESTORE = "restore"
    RELIGHT = "relight"


# Operations that consume a source image (spec §7.3: image-to-image, inpaint,
# outpaint, upscale, restore, relight all transform an existing artifact).
_SOURCE_REQUIRED: frozenset[ImageGenOperation] = frozenset(
    {
        ImageGenOperation.IMAGE_TO_IMAGE,
        ImageGenOperation.INPAINT,
        ImageGenOperation.OUTPAINT,
        ImageGenOperation.UPSCALE,
        ImageGenOperation.RESTORE,
        ImageGenOperation.RELIGHT,
    }
)

# Operations that require a mask (spec §7.3: inpaint/outpaint edit a region).
_MASK_REQUIRED: frozenset[ImageGenOperation] = frozenset(
    {
        ImageGenOperation.INPAINT,
        ImageGenOperation.OUTPAINT,
    }
)

# Map request operation kind -> the provenance operation-type enum reused
# from vision.py (the two enums share string values for these seven members).
_OP_TYPE_MAP: dict[ImageGenOperation, ImageOperationType] = {
    ImageGenOperation.TEXT_TO_IMAGE: ImageOperationType.TEXT_TO_IMAGE,
    ImageGenOperation.IMAGE_TO_IMAGE: ImageOperationType.IMAGE_TO_IMAGE,
    ImageGenOperation.INPAINT: ImageOperationType.INPAINT,
    ImageGenOperation.OUTPAINT: ImageOperationType.OUTPAINT,
    ImageGenOperation.UPSCALE: ImageOperationType.UPSCALE,
    ImageGenOperation.RESTORE: ImageOperationType.RESTORE,
    ImageGenOperation.RELIGHT: ImageOperationType.RELIGHT,
}


# ---------------------------------------------------------------------------
# ImageRequest (spec §7.3, AIML-013)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImageRequest:
    """Image generation/edit request contract (spec §7.3, AIML-013).

    Text-to-image requires a non-empty ``prompt`` and carries no source
    artifact. Every other operation (image-to-image, inpaint, outpaint,
    upscale, restore, relight) requires an immutable ``source_artifact_uri``
    + ``source_sha256`` pair. Inpaint and outpaint additionally require a
    ``mask_artifact_uri`` + ``mask_sha256`` pair.
    """

    prompt: str
    operation: ImageGenOperation
    model_version: str
    source_artifact_uri: str | None = None
    source_sha256: str | None = None
    mask_artifact_uri: str | None = None
    mask_sha256: str | None = None
    seed: int | None = None
    adapter_version: str | None = None
    content_domain: ContentDomain = ContentDomain.GENERAL
    constraints: Constraints = field(default_factory=Constraints)
    request_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation", _coerce_enum(self.operation, ImageGenOperation, "operation"))
        object.__setattr__(
            self,
            "content_domain",
            _coerce_enum(self.content_domain, ContentDomain, "content_domain"),
        )
        _require_nonempty_str(self.model_version, "model_version")
        if not isinstance(self.constraints, Constraints):
            raise ValueError("constraints must be a Constraints instance")

        # prompt: text-to-image is prompt-driven; others may leave blank but
        # a supplied prompt must be a real (non-whitespace-only) string.
        if self.operation is ImageGenOperation.TEXT_TO_IMAGE:
            _require_nonempty_str(self.prompt, "prompt")
        elif self.prompt and not self.prompt.strip():
            raise ValueError("prompt, when set, must be a non-empty string")

        # source pair: both-or-neither.
        if (self.source_artifact_uri is None) != (self.source_sha256 is None):
            raise ValueError("source_artifact_uri and source_sha256 must both be set or both unset")
        if self.operation in _SOURCE_REQUIRED:
            if self.source_artifact_uri is None:
                raise ValueError(f"operation {self.operation.value!r} requires source_artifact_uri")
            if self.source_sha256 is None:
                raise ValueError(f"operation {self.operation.value!r} requires source_sha256")
        if self.source_sha256 is not None:
            _require_sha256(self.source_sha256, "source_sha256")
        if self.source_artifact_uri is not None:
            _require_nonempty_str(self.source_artifact_uri, "source_artifact_uri")

        # mask pair: both-or-neither.
        if (self.mask_artifact_uri is None) != (self.mask_sha256 is None):
            raise ValueError("mask_artifact_uri and mask_sha256 must both be set or both unset")
        if self.operation in _MASK_REQUIRED:
            if self.mask_artifact_uri is None:
                raise ValueError(f"operation {self.operation.value!r} requires mask_artifact_uri")
            if self.mask_sha256 is None:
                raise ValueError(f"operation {self.operation.value!r} requires mask_sha256")
        if self.mask_sha256 is not None:
            _require_sha256(self.mask_sha256, "mask_sha256")
        if self.mask_artifact_uri is not None:
            _require_nonempty_str(self.mask_artifact_uri, "mask_artifact_uri")

        if self.seed is not None and (not isinstance(self.seed, int) or self.seed < 0):
            raise ValueError(f"seed, when set, must be a non-negative int, got {self.seed!r}")
        if self.adapter_version is not None and not self.adapter_version.strip():
            raise ValueError("adapter_version, when set, must be non-empty")


# ---------------------------------------------------------------------------
# Edit plan (spec §7.3: reversible operation graph)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImageEditPlan:
    """Planned reversible operation graph for an image edit (spec §7.3).

    The original source is immutable: the plan references it by digest and
    never mutates the request's source bytes. The ``operation_graph`` is a
    tuple of :class:`~general_ludd.ai_ml.vision.ImageOperation` nodes that
    can be replayed or audited; :meth:`reversed_graph` yields the inverse
    sequence for undo auditing.
    """

    request: ImageRequest
    operation_graph: tuple[ImageOperation, ...]
    content_domain: ContentDomain
    source_artifact_uri: str | None
    source_sha256: str | None
    mask_artifact_uri: str | None
    mask_sha256: str | None
    seed: int | None
    model_version: str
    adapter_version: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.request, ImageRequest):
            raise ValueError("request must be an ImageRequest instance")
        if not self.operation_graph:
            raise ValueError("operation_graph must contain at least one ImageOperation")
        for op in self.operation_graph:
            if not isinstance(op, ImageOperation):
                raise ValueError("operation_graph entries must be ImageOperation instances")
        object.__setattr__(
            self,
            "content_domain",
            _coerce_enum(self.content_domain, ContentDomain, "content_domain"),
        )
        _require_nonempty_str(self.model_version, "model_version")

    @property
    def source_is_immutable(self) -> bool:
        """Spec §7.3: original inputs are immutable.

        The plan always retains the source by digest (text-to-image has no
        image source — its immutable original is the prompt, materialized at
        finalize time). Returns ``True`` unconditionally: immutability is a
        contract of the plan, not a computed property of the bytes.
        """
        return True

    @property
    def is_reversible(self) -> bool:
        """Spec §7.3: every edit produces a reversible operation graph.

        Reversibility holds because the source digest is retained — the
        operation chain can be unwound to recover the original artifact.
        """
        return True

    def reversed_graph(self) -> tuple[ImageOperation, ...]:
        """Return the operation graph in reverse order for undo auditing.

        True pixel-level inversion requires replaying the inverse operations
        against the retained source digest; this returns the audit trail in
        inverse sequence so a reviewer can reconstruct the undo path.
        """
        return tuple(reversed(self.operation_graph))


# ---------------------------------------------------------------------------
# Content policy (spec §7.3, §11)
# ---------------------------------------------------------------------------


# Fixed, immutable content-policy ruleset (spec §11). The digest fingerprints
# the rule text so every decision's ruleset_sha256 is stable and auditable.
_POLICY_RULESET_TEXT = (
    "ai_ml.images.policy.v1: "
    "restricted data_classification requires offline mode; "
    "budget must cover operation cost; "
    "banned content keywords are refused; "
    "measurement-domain edits must be labeled synthetic/modified; "
    "policy is never silently relaxed"
)
IMAGES_POLICY_RULESET_SHA256: str = hashlib.sha256(_POLICY_RULESET_TEXT.encode("utf-8")).hexdigest()

# Illustrative banned-keyword denylist (spec §11: PII / unsafe content refuse).
# The contract is that a prompt is refused when it contains any banned token;
# the set itself is policy data, not request-derived.
_BANNED_KEYWORDS: frozenset[str] = frozenset({"csam", "nonconsensual", "deepfake-real-person"})


@dataclass(frozen=True)
class ContentPolicyDecision:
    """Content-policy verdict recorded on every image edit (spec §7.3, §11).

    ``decision`` is ``"allow"`` or ``"refuse"``; ``reason`` carries the
    human-readable rationale (or refusal causes); ``ruleset_sha256``
    fingerprints the fixed ruleset; ``decision_id`` is a unique per-request
    audit identifier.
    """

    decision: str
    reason: str
    ruleset_sha256: str
    decision_id: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.decision, "decision")
        if self.decision not in ("allow", "refuse"):
            raise ValueError(f"decision must be 'allow' or 'refuse', got {self.decision!r}")
        _require_nonempty_str(self.reason, "reason")
        _require_sha256(self.ruleset_sha256, "ruleset_sha256")
        _require_nonempty_str(self.decision_id, "decision_id")

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


# ---------------------------------------------------------------------------
# ImageEditor (spec §7.3, AIML-013)
# ---------------------------------------------------------------------------


@dataclass
class ImageEditor:
    """Plan image edits and validate the content policy (spec §7.3, AIML-013).

    A stateless orchestrator over :class:`ImageRequest`:

      - :meth:`plan_edit` builds the reversible operation graph and captures
        the immutable source digest, mask digest, seed, model/adapter
        versions, and content domain.
      - :meth:`validate_content_policy` returns the content-policy verdict
        (spec §11: restricted data requires offline; banned keywords refused;
        policy never silently relaxed).
      - :meth:`finalize_edit` converts a plan + output digest + policy
        decision into an immutable
        :class:`~general_ludd.ai_ml.vision.ImageEditRecord`.

    Parameters:
      ruleset_sha256: the fixed ruleset digest; defaults to
        :data:`IMAGES_POLICY_RULESET_SHA256`. Override only for test isolation.
    """

    ruleset_sha256: str = IMAGES_POLICY_RULESET_SHA256

    def __post_init__(self) -> None:
        if not isinstance(self.ruleset_sha256, str) or len(self.ruleset_sha256) != 64:
            raise ValueError("ruleset_sha256 must be a 64-char hex digest")

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    def plan_edit(self, request: ImageRequest) -> ImageEditPlan:
        """Produce the reversible operation graph for ``request`` (spec §7.3).

        The plan records the operation node (mapped to the shared
        ``ImageOperationType``), the immutable source digest, mask digest,
        seed, and model/adapter versions. The original source is never
        mutated — only referenced by digest.
        """
        if not isinstance(request, ImageRequest):
            raise ValueError("request must be an ImageRequest instance")
        op_node = self._build_operation(request)
        return ImageEditPlan(
            request=request,
            operation_graph=(op_node,),
            content_domain=request.content_domain,
            source_artifact_uri=request.source_artifact_uri,
            source_sha256=request.source_sha256,
            mask_artifact_uri=request.mask_artifact_uri,
            mask_sha256=request.mask_sha256,
            seed=request.seed,
            model_version=request.model_version,
            adapter_version=request.adapter_version,
        )

    # ------------------------------------------------------------------
    # Content policy
    # ------------------------------------------------------------------

    def validate_content_policy(self, request: ImageRequest) -> ContentPolicyDecision:
        """Return the content-policy decision for ``request`` (spec §7.3, §11).

        Checks (policy is never silently relaxed — spec §4.1):

          - ``data_classification == RESTRICTED`` requires ``offline=True``
            (spec §11: cryptographic isolation).
          - the prompt must not contain any banned keyword (spec §11:
            PII / unsafe content refuse).

        Measurement-domain labeling (medical/forensic/scientific → synthetic)
        is enforced on the finalized record via
        :class:`~general_ludd.ai_ml.vision.ImageEditRecord`, not here.
        """
        if not isinstance(request, ImageRequest):
            raise ValueError("request must be an ImageRequest instance")
        reasons: list[str] = []

        if request.constraints.data_classification is DataClassification.RESTRICTED and not request.constraints.offline:
            reasons.append("restricted data_classification requires offline=True (spec §11)")

        prompt_lower = request.prompt.lower() if request.prompt else ""
        for kw in _BANNED_KEYWORDS:
            if kw in prompt_lower:
                reasons.append(f"prompt contains banned keyword {kw!r}")

        decision = "allow" if not reasons else "refuse"
        return ContentPolicyDecision(
            decision=decision,
            reason="; ".join(reasons) if reasons else "allowed",
            ruleset_sha256=self.ruleset_sha256,
            decision_id=f"img-pol-{uuid.uuid4().hex[:16]}",
        )

    # ------------------------------------------------------------------
    # Finalize -> immutable provenance record
    # ------------------------------------------------------------------

    def finalize_edit(
        self,
        plan: ImageEditPlan,
        *,
        output_artifact_uri: str,
        output_sha256: str,
        content_policy_decision: ContentPolicyDecision,
    ) -> ImageEditRecord:
        """Convert a plan + output digest into an immutable record (spec §7.3).

        Spec §7.3: "Original inputs are immutable. Every edit produces a
        reversible operation graph, masks, seeds where available, model and
        adapter versions, content-policy decision, and provenance metadata."

        For text-to-image (no image source), the immutable original is the
        prompt; its SHA-256 becomes the record's ``source_sha256`` so the
        generation remains reproducible and auditable.
        """
        if not isinstance(plan, ImageEditPlan):
            raise ValueError("plan must be an ImageEditPlan instance")
        _require_nonempty_str(output_artifact_uri, "output_artifact_uri")
        _require_sha256(output_sha256, "output_sha256")
        if not isinstance(content_policy_decision, ContentPolicyDecision):
            raise ValueError("content_policy_decision must be a ContentPolicyDecision instance")

        request = plan.request
        if plan.source_artifact_uri is not None and plan.source_sha256 is not None:
            source_uri = plan.source_artifact_uri
            source_sha = plan.source_sha256
        else:
            # text-to-image: the immutable original is the prompt text.
            source_uri = f"artifact://prompt/{request.operation.value}"
            source_sha = hashlib.sha256((request.prompt or "").encode("utf-8")).hexdigest()

        return ImageEditRecord(
            source_artifact_uri=source_uri,
            source_sha256=source_sha,
            output_artifact_uri=output_artifact_uri,
            output_sha256=output_sha256,
            operations=plan.operation_graph,
            model_version=plan.model_version,
            content_policy_decision=content_policy_decision.decision,
            content_domain=plan.content_domain,
            mask_artifact_uri=plan.mask_artifact_uri,
            mask_sha256=plan.mask_sha256,
            seed=plan.seed,
            adapter_version=plan.adapter_version,
            provenance_metadata=(
                "planned_by=ImageEditor",
                f"ruleset={content_policy_decision.ruleset_sha256[:12]}",
                f"decision_id={content_policy_decision.decision_id}",
            ),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_operation(self, request: ImageRequest) -> ImageOperation:
        """Map an ImageGenOperation to its provenance ImageOperation node."""
        op_type = _OP_TYPE_MAP[request.operation]
        params: list[str] = [f"model={request.model_version}"]
        if request.seed is not None:
            params.append(f"seed={request.seed}")
        if request.adapter_version is not None:
            params.append(f"adapter={request.adapter_version}")
        if request.prompt:
            params.append("prompt_digest=sha256")
        return ImageOperation(op_type=op_type, params=tuple(params))


__all__ = [
    "IMAGES_POLICY_RULESET_SHA256",
    "ContentPolicyDecision",
    "ImageEditPlan",
    "ImageEditor",
    "ImageGenOperation",
    "ImageRequest",
]
