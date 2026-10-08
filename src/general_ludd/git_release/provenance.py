"""Compatibility exports for the collection-owned provenance implementation.

New integrations should import
``ansible_collections.general_ludd.git_release.plugins.module_utils.provenance``.
This module intentionally contains no second verifier.
"""

from ansible_collections.general_ludd.git_release.plugins.module_utils.provenance import (
    MAX_ARTIFACT_BYTES,
    MAX_LOCK_BYTES,
    READ_CHUNK_BYTES,
    ArtifactVerificationError,
    ArtifactVerificationReceipt,
    Attestation,
    ProvenanceRecord,
    ReceiptPurpose,
    SignatureState,
    VerificationResult,
    build_provenance,
    verify_artifact_receipt,
    verify_provenance,
    verify_release_artifact,
)

__all__ = [
    "MAX_ARTIFACT_BYTES",
    "MAX_LOCK_BYTES",
    "READ_CHUNK_BYTES",
    "ArtifactVerificationError",
    "ArtifactVerificationReceipt",
    "Attestation",
    "ProvenanceRecord",
    "ReceiptPurpose",
    "SignatureState",
    "VerificationResult",
    "build_provenance",
    "verify_artifact_receipt",
    "verify_provenance",
    "verify_release_artifact",
]
