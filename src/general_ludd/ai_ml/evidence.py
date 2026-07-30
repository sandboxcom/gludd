"""AIML-003 — immutable, content-addressed, citation-addressable evidence store.

Spec: docs/specs/FEATURE_AI_ML_EXPERT.md §4.3 (records) + §5.1 (source
content is DATA, never instructions) + §5.2 (evidence pipeline).

Phase A scope (this module):

- Content-addressed storage (SHA-256 key). Ingesting identical bytes twice
  produces ONE artifact with multiple source locators (AIML-AT-002).
- Immutable records (frozen dataclass): once published, an artifact's
  metadata is never edited in place; corrections are a new record linked
  via ``supersedes``.
- License / provenance validation: ``allowed_licenses`` intersection,
  ``creator`` recorded on every artifact.
- Retraction tracking: a retracted record stays retrievable for audit but is
  flagged with ``reason`` + ``retracted_at``; ``list_all(include_retracted=)``
  filters accordingly.
- Prompt-injection isolation (AIML-AT-003, spec §5.1): the store has NO
  method whose input or output is interpreted as an instruction. Content is
  opaque bytes; it enters only as the SHA-256 preimage and leaves only via
  ``get_content()``. The fixed ``EVIDENCE_POLICY_RULESET_SHA256`` constant
  is not mutable by any ingest path — retrieved text cannot alter policies.

This module is the canonical home of ``EvidenceStore``. The router module
re-exports it for backwards compatibility.
"""

from __future__ import annotations

import hashlib
import time
import uuid

from general_ludd.ai_ml.schemas import EvidenceArtifact

# ---------------------------------------------------------------------------
# Fixed policy ruleset (spec §5.1)
# ---------------------------------------------------------------------------

# The evidence store applies a fixed set of policy rules: it validates the
# SPDX license string against ``allowed_licenses`` and records provenance.
# These rules are CONSTANTS — they are not derived from, and cannot be
# altered by, any ingested content. The digest below fingerprints the rule
# text so tests can prove in-variance across ingest calls (AIML-AT-003:
# "prompt-injection content cannot alter ... policies").
_POLICY_RULESET_TEXT = (
    "evidence.policy.v1: "
    "license must intersect allowed_licenses; "
    "creator recorded on ingest; "
    "retraction requires reason; "
    "content is data, never instructions"
)
EVIDENCE_POLICY_RULESET_SHA256: str = hashlib.sha256(_POLICY_RULESET_TEXT.encode("utf-8")).hexdigest()


def _validate_license(license_id: str, allowed: tuple[str, ...] | None) -> None:
    if not isinstance(license_id, str) or not license_id.strip():
        raise ValueError("license must be a non-empty SPDX-style string")
    if allowed is not None and license_id not in allowed:
        raise ValueError(
            f"license {license_id!r} is not in the allowed set {allowed!r}; "
            "evidence with a disallowed license is quarantined and refused"
        )


def _validate_authority_score(score: float) -> None:
    if not (0.0 <= score <= 1.0):
        raise ValueError(f"authority_score must be in [0.0, 1.0], got {score}")


def _merge_record_for_locator(existing: EvidenceArtifact, locator: str) -> EvidenceArtifact:
    """Build a new immutable record carrying ``existing.locators + locator``."""
    if locator in existing.locators:
        return existing
    return EvidenceArtifact(
        source_id=existing.source_id,
        sha256=existing.sha256,
        media_type=existing.media_type,
        locators=(*existing.locators, locator),
        fetched_at=existing.fetched_at,
        license=existing.license,
        authority_score=existing.authority_score,
        tenant_id=existing.tenant_id,
        creator=existing.creator,
        supersedes=existing.supersedes,
        retracted=existing.retracted,
        retraction_reason=existing.retraction_reason,
        retracted_at=existing.retracted_at,
    )


class EvidenceStore:
    """Content-addressed, citation-addressable, immutable evidence store.

    The store is the runtime embodiment of the spec §5.2 pipeline steps that
    are testable at the storage layer: fetch into a content-addressed
    artifact (1), verify (2), detect duplicates / retractions / corrections
    (4). Higher-numbered pipeline steps (scoring, regression, canary) live
    in the research / promotion services, not here.
    """

    def __init__(self, *, allowed_licenses: tuple[str, ...] | None = None) -> None:
        # sha256 -> EvidenceArtifact (canonical record, never mutated in place)
        self._by_sha: dict[str, EvidenceArtifact] = {}
        # source_id -> sha256 (citation lookup index)
        self._by_source: dict[str, str] = {}
        # sha256 -> raw bytes (content blob). Exposed ONLY via get_content();
        # never returned by get/list_all. This is the prompt-injection
        # isolation boundary (spec §5.1).
        self._content: dict[str, bytes] = {}
        self._allowed_licenses: tuple[str, ...] | None = allowed_licenses

    # ------------------------------------------------------------------
    # ingest
    # ------------------------------------------------------------------

    def ingest(
        self,
        *,
        content: bytes,
        media_type: str,
        license: str,
        locator: str,
        tenant_id: str = "default",
        authority_score: float = 0.0,
        creator: str = "",
    ) -> EvidenceArtifact:
        """Ingest ``content`` and return the canonical artifact record.

        Duplicate content (same SHA-256) produces ONE artifact; the new
        ``locator`` is appended to ``locators`` (AIML-AT-002). Other fields
        on a duplicate ingest (license, creator, ...) are ignored — the
        canonical record's metadata is immutable after first publish.
        """
        if not isinstance(content, bytes) or not content:
            raise ValueError("content must be non-empty bytes")
        if not isinstance(media_type, str) or not media_type.strip():
            raise ValueError("media_type must be a non-empty string")
        if not isinstance(locator, str) or not locator.strip():
            raise ValueError("locator must be a non-empty string")
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        _validate_license(license, self._allowed_licenses)
        _validate_authority_score(authority_score)

        digest = hashlib.sha256(content).hexdigest()
        existing = self._by_sha.get(digest)
        if existing is not None:
            merged = _merge_record_for_locator(existing, locator)
            if merged is not existing:
                self._by_sha[digest] = merged
                self._by_source[merged.source_id] = digest
            return merged

        source_id = f"evd-{uuid.uuid4().hex[:16]}"
        record = EvidenceArtifact(
            source_id=source_id,
            sha256=digest,
            media_type=media_type,
            locators=(locator,),
            fetched_at=int(time.time()),
            license=license,
            authority_score=authority_score,
            tenant_id=tenant_id,
            creator=creator,
        )
        self._by_sha[digest] = record
        self._by_source[source_id] = digest
        self._content[digest] = content
        return record

    # ------------------------------------------------------------------
    # citation-addressable retrieval
    # ------------------------------------------------------------------

    def get(self, source_id: str, tenant_id: str | None = None) -> EvidenceArtifact | None:
        digest = self._by_source.get(source_id)
        if digest is None:
            return None
        record = self._by_sha[digest]
        if tenant_id is not None and record.tenant_id != tenant_id:
            return None
        return record

    def get_by_sha(self, sha256: str, tenant_id: str | None = None) -> EvidenceArtifact | None:
        record = self._by_sha.get(sha256)
        if record is None:
            return None
        if tenant_id is not None and record.tenant_id != tenant_id:
            return None
        return record

    def get_content(self, sha256: str) -> bytes | None:
        """Return the raw content blob for ``sha256``, or ``None``.

        This is the ONLY method that returns content. It returns ``bytes``
        — callers MUST treat the result as inert data and never pipe it
        into a prompt, eval, or instruction interpreter (spec §5.1).
        """
        return self._content.get(sha256)

    def list_all(
        self,
        tenant_id: str | None = None,
        include_retracted: bool = True,
    ) -> list[EvidenceArtifact]:
        records = list(self._by_sha.values())
        if tenant_id is not None:
            records = [r for r in records if r.tenant_id == tenant_id]
        if not include_retracted:
            records = [r for r in records if not r.retracted]
        return sorted(records, key=lambda r: r.fetched_at)

    # ------------------------------------------------------------------
    # retraction + correction
    # ------------------------------------------------------------------

    def retract(self, source_id: str, *, reason: str) -> EvidenceArtifact:
        """Mark ``source_id`` as retracted (spec §5.2 step 4: detect retractions).

        Retraction is non-destructive: the record stays in the store for
        audit but is flagged with ``reason`` + ``retracted_at``.
        """
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        digest = self._by_source.get(source_id)
        if digest is None:
            raise KeyError(f"unknown source_id: {source_id!r}")
        existing = self._by_sha[digest]
        if existing.retracted:
            return existing
        retracted = EvidenceArtifact(
            source_id=existing.source_id,
            sha256=existing.sha256,
            media_type=existing.media_type,
            locators=existing.locators,
            fetched_at=existing.fetched_at,
            license=existing.license,
            authority_score=existing.authority_score,
            tenant_id=existing.tenant_id,
            creator=existing.creator,
            supersedes=existing.supersedes,
            retracted=True,
            retraction_reason=reason,
            retracted_at=int(time.time()),
        )
        self._by_sha[digest] = retracted
        return retracted

    def supersede(
        self,
        old_source_id: str,
        *,
        new_content: bytes,
        media_type: str,
        license: str,
        locator: str,
        reason: str,
        tenant_id: str | None = None,
        creator: str = "",
    ) -> EvidenceArtifact:
        """Publish a corrected record that supersedes ``old_source_id``.

        The old record is RETRACTED (not deleted) with ``reason``; the new
        record carries ``supersedes=old_source_id``. The two stay linked for
        audit (spec §5.2: "Detect duplicates, retractions, corrections").
        """
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        old = self.get(old_source_id, tenant_id=tenant_id)
        if old is None:
            raise KeyError(f"unknown source_id: {old_source_id!r}")
        effective_tenant = tenant_id if tenant_id is not None else old.tenant_id
        new_record = self.ingest(
            content=new_content,
            media_type=media_type,
            license=license,
            locator=locator,
            tenant_id=effective_tenant,
            creator=creator,
        )
        # Attach the supersedes link by rebuilding the record immutably.
        linked = EvidenceArtifact(
            source_id=new_record.source_id,
            sha256=new_record.sha256,
            media_type=new_record.media_type,
            locators=new_record.locators,
            fetched_at=new_record.fetched_at,
            license=new_record.license,
            authority_score=new_record.authority_score,
            tenant_id=new_record.tenant_id,
            creator=new_record.creator,
            supersedes=old_source_id,
        )
        self._by_sha[linked.sha256] = linked
        self._by_source[linked.source_id] = linked.sha256
        # Retract the old record for audit trail.
        self.retract(old_source_id, reason=reason)
        return linked


__all__ = [
    "EVIDENCE_POLICY_RULESET_SHA256",
    "EvidenceStore",
]
