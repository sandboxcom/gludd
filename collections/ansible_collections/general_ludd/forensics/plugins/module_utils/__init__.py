"""Forensics collection module_utils — chain of custody, materials forensics, and photo forensics utilities."""

from .chain_of_custody import (
    EvidenceItem,
    ChainOfCustody,
    create_chain_of_custody,
    log_transfer,
    verify_chain,
)

from .materials_forensics import (
    FingerprintPattern,
    classify_fingerprint,
    match_dna_profile,
    analyze_trace_evidence,
)

from .photo_forensics import (
    extract_metadata,
    detect_modifications,
    detect_ai_generated,
    compute_ela,
    identify_camera,
)

__all__ = [
    "EvidenceItem",
    "ChainOfCustody",
    "create_chain_of_custody",
    "log_transfer",
    "verify_chain",
    "FingerprintPattern",
    "classify_fingerprint",
    "match_dna_profile",
    "analyze_trace_evidence",
    "extract_metadata",
    "detect_modifications",
    "detect_ai_generated",
    "compute_ela",
    "identify_camera",
]
