"""Forensics collection module_utils — chain of custody, materials forensics, and photo forensics utilities."""

from .chain_of_custody import (
    ChainOfCustody,
    EvidenceItem,
    create_chain_of_custody,
    log_transfer,
    verify_chain,
)
from .materials_forensics import (
    FingerprintPattern,
    analyze_trace_evidence,
    classify_fingerprint,
    match_dna_profile,
)
from .photo_forensics import (
    compute_ela,
    detect_ai_generated,
    detect_modifications,
    extract_metadata,
    identify_camera,
)

__all__ = [
    "ChainOfCustody",
    "EvidenceItem",
    "FingerprintPattern",
    "analyze_trace_evidence",
    "classify_fingerprint",
    "compute_ela",
    "create_chain_of_custody",
    "detect_ai_generated",
    "detect_modifications",
    "extract_metadata",
    "identify_camera",
    "log_transfer",
    "match_dna_profile",
    "verify_chain",
]
