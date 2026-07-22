"""
chain_of_custody -- Forensic evidence chain of custody management.

Exposes EvidenceItem and ChainOfCustody dataclasses, evidence types,
storage conditions, contamination risk assessment, transfer logging,
digital signature verification, and chain-of-custody report generation.

Data tables:
    EVIDENCE_TYPES             -- dict[type_key] -> type metadata
    STORAGE_CONDITIONS         -- dict[condition_key] -> storage specifications
    CONTAMINATION_RISK_LEVELS  -- dict[level_key] -> risk levels and mitigations
    PACKAGING_PROTOCOLS        -- dict[protocol_key] -> packaging specifications
    LABELING_STANDARD          -- dict[field] -> field requirements
    EVIDENCE_STATUSES          -- dict[status_key] -> status metadata

Functions:
    create_chain_of_custody(case_id)                -> ChainOfCustody
    add_evidence_item(coc, type, description, ...)   -> EvidenceItem
    log_transfer(evidence_id, coc, from, to, reason) -> dict
    verify_chain(evidence_id, coc)                   -> dict
    seal_evidence(evidence_id, coc, seal_number)     -> EvidenceItem
    break_seal(evidence_id, coc, reason, auth_by)    -> EvidenceItem
    assess_contamination_risk(evidence)              -> dict
    generate_chain_report(coc)                       -> dict
    verify_digital_signature(coc, evidence_id, sig)  -> bool
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ═══════════════════════════════════════════════════════════════════
# Data tables
# ═══════════════════════════════════════════════════════════════════

EVIDENCE_TYPES: dict[str, dict[str, Any]] = {
    "PHYSICAL": {"description": "Tangible objects: weapons, clothing, tools, documents",
                 "default_packaging": "PAPER_BAG", "default_storage": "EVIDENCE_LOCKER"},
    "DIGITAL": {"description": "Electronic data on digital media or networks",
                "default_packaging": "ANTI_STATIC_BAG", "default_storage": "CLEAN_ROOM"},
    "BIOLOGICAL": {"description": "Organic material: blood, saliva, hair, tissue, DNA",
                   "default_packaging": "STERILE_CONTAINER", "default_storage": "REFRIGERATED"},
    "TRACE": {"description": "Microscopic materials: fibers, residues, paint chips",
              "default_packaging": "STERILE_CONTAINER", "default_storage": "CLEAN_ROOM"},
    "IMPRESSION": {"description": "Patterns: fingerprints, tire tracks, tool marks",
                   "default_packaging": "PLASTIC_BAG", "default_storage": "EVIDENCE_LOCKER"},
}

STORAGE_CONDITIONS: dict[str, dict[str, Any]] = {
    "REFRIGERATED": {"temp": "2-8 C", "humidity": "30-50% RH", "monitor_hours": 4,
                     "backup_power": True, "suitable": ["BIOLOGICAL", "TRACE"]},
    "FROZEN": {"temp": "-20 to -80 C", "humidity": "N/A", "monitor_hours": 8,
               "backup_power": True, "suitable": ["BIOLOGICAL"]},
    "ROOM_TEMP": {"temp": "15-25 C", "humidity": "30-70% RH", "monitor_hours": 24,
                  "backup_power": False, "suitable": ["PHYSICAL", "IMPRESSION"]},
    "CLEAN_ROOM": {"temp": "20-22 C", "humidity": "40-50% RH", "monitor_hours": 1,
                   "backup_power": True, "suitable": ["DIGITAL", "TRACE"]},
    "EVIDENCE_LOCKER": {"temp": "15-25 C", "humidity": "30-70% RH", "monitor_hours": 24,
                        "backup_power": False, "suitable": ["PHYSICAL", "DIGITAL", "IMPRESSION"]},
}

CONTAMINATION_RISK_LEVELS: dict[str, dict[str, Any]] = {
    "NONE": {"level": 0, "description": "No identifiable risk", "requires_mitigation": False},
    "LOW": {"level": 1, "description": "Minor environmental exposure risk", "requires_mitigation": False},
    "MEDIUM": {"level": 2, "description": "Moderate risk; document handling", "requires_mitigation": True,
               "mitigations": ["sterile tools", "limit handlers", "document environment"]},
    "HIGH": {"level": 3, "description": "Significant risk; chain may be challenged", "requires_mitigation": True,
              "mitigations": ["full PPE + sterile kit", "photograph every step",
                              "collect controls", "double-bag and seal"]},
    "CRITICAL": {"level": 4, "description": "Severe risk; evidence may be inadmissible", "requires_mitigation": True,
                  "mitigations": ["all HIGH mitigations", "engage forensic expert",
                                  "video-record collection", "third-party verification",
                                  "prepare admissibility affidavit"]},
}

PACKAGING_PROTOCOLS: dict[str, dict[str, Any]] = {
    "PAPER_BAG": {"material": "kraft paper", "permeable": True,
                  "suitable_for": ["PHYSICAL", "IMPRESSION"],
                  "notes": "Breathable; not for biological evidence"},
    "PLASTIC_BAG": {"material": "polyethylene", "permeable": False,
                    "suitable_for": ["PHYSICAL", "IMPRESSION"],
                    "notes": "Airtight; do NOT use for wet biological evidence"},
    "STERILE_CONTAINER": {"material": "medical-grade sterile plastic/glass", "permeable": False,
                          "suitable_for": ["BIOLOGICAL", "TRACE"],
                          "notes": "Must be DNA/DNase/RNase-free"},
    "ANTI_STATIC_BAG": {"material": "metalized polyester + anti-static coating", "permeable": False,
                        "suitable_for": ["DIGITAL"],
                        "notes": "ESD protection; do not fold or crease"},
    "FARADAY_BAG": {"material": "copper/nickel fabric + RF-blocking liner", "permeable": False,
                    "suitable_for": ["DIGITAL"],
                    "notes": "RF attenuation >80 dB at 800 MHz-6 GHz"},
    "VACUUM_SEALED": {"material": "multi-layer barrier film + aluminum foil", "permeable": False,
                      "suitable_for": ["DIGITAL", "TRACE"],
                      "notes": "Removes oxygen; not for volatile evidence"},
}

LABELING_STANDARD: dict[str, dict[str, str]] = {
    "case_id": {"format": "YYYY-MM-NNNN", "required": "yes", "desc": "Unique case identifier"},
    "evidence_id": {"format": "EVI-NNNNNN", "required": "yes", "desc": "Unique evidence item identifier"},
    "date": {"format": "ISO 8601", "required": "yes", "desc": "Collection date and time"},
    "collector": {"format": "LASTNAME, Firstname; badge#", "required": "yes", "desc": "Collecting person"},
    "type": {"format": "PHYSICAL|DIGITAL|BIOLOGICAL|TRACE|IMPRESSION", "required": "yes", "desc": "Evidence category"},
    "hazard_warnings": {"format": "Free-text; NONE if n/a", "required": "yes", "desc": "Biohazard/chemical/sharps"},
    "storage_condition": {"format": "REFRIGERATED|FROZEN|ROOM_TEMP|CLEAN_ROOM|EVIDENCE_LOCKER",
                          "required": "yes", "desc": "Required storage condition"},
    "packaging": {"format": "PAPER_BAG|PLASTIC_BAG|STERILE_CONTAINER|ANTI_STATIC_BAG|FARADAY_BAG|VACUUM_SEALED",
                  "required": "yes", "desc": "Packaging protocol used"},
}

EVIDENCE_STATUSES: dict[str, dict[str, Any]] = {
    "COLLECTED": {"desc": "Collected at scene; logged into chain", "terminal": False},
    "TRANSFERRED": {"desc": "In transit between custodians", "terminal": False},
    "RECEIVED": {"desc": "Received by new custodian", "terminal": False},
    "SEALED": {"desc": "Sealed with tamper-evident seal", "terminal": False},
    "UNSEALED": {"desc": "Seal broken for examination", "terminal": False},
    "ANALYZED": {"desc": "Undergoing forensic analysis", "terminal": False},
    "RESEALED": {"desc": "Resealed with new seal number", "terminal": False},
    "RELEASED": {"desc": "Released to authorized party", "terminal": True},
    "DESTROYED": {"desc": "Destroyed per retention policy", "terminal": True},
}

_VT: frozenset[str] = frozenset(EVIDENCE_TYPES.keys())
_VS: frozenset[str] = frozenset(STORAGE_CONDITIONS.keys())
_VR: frozenset[str] = frozenset(CONTAMINATION_RISK_LEVELS.keys())
_VP: frozenset[str] = frozenset(PACKAGING_PROTOCOLS.keys())
_VST: frozenset[str] = frozenset(EVIDENCE_STATUSES.keys())

# ═══════════════════════════════════════════════════════════════════
# Dataclasses
# ═══════════════════════════════════════════════════════════════════

@dataclass
class EvidenceItem:
    """A single item of forensic evidence within a chain of custody."""

    id: str
    type: str
    description: str
    collection_date: str
    location: str
    collector: str
    packaging: str = "PAPER_BAG"
    storage_conditions: str = "EVIDENCE_LOCKER"
    contamination_risk: str = "NONE"
    status: str = "COLLECTED"
    seal_number: str | None = None
    seal_history: list[dict[str, str]] = field(default_factory=list)
    hazard_warnings: list[str] = field(default_factory=list)
    photographs: list[str] = field(default_factory=list)
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.description or not self.location or not self.collector:
            raise ValueError("id, description, location, and collector must be non-empty")
        if self.type not in _VT:
            raise ValueError(f"Unknown type '{self.type}'. Valid: {sorted(_VT)}")
        if self.storage_conditions not in _VS:
            raise ValueError(f"Unknown storage '{self.storage_conditions}'. Valid: {sorted(_VS)}")
        if self.contamination_risk not in _VR:
            raise ValueError(f"Unknown risk '{self.contamination_risk}'. Valid: {sorted(_VR)}")
        if self.packaging not in _VP:
            raise ValueError(f"Unknown packaging '{self.packaging}'. Valid: {sorted(_VP)}")
        if self.status not in _VST:
            raise ValueError(f"Unknown status '{self.status}'. Valid: {sorted(_VST)}")


@dataclass
class ChainOfCustody:
    """A forensic chain of custody tracking all evidence items and transfers."""

    case_id: str
    evidence_items: dict[str, EvidenceItem] = field(default_factory=dict)
    transfer_log: list[dict[str, Any]] = field(default_factory=list)
    digital_signatures: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_modified: str = ""

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ValueError("case_id must be non-empty")
        if not self.last_modified:
            self.last_modified = self.created_at

# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def _ts() -> str:
    """Current UTC timestamp (ISO 8601)."""
    return datetime.now(timezone.utc).isoformat()

def _evid() -> str:
    """Generate unique EVI-NNNNNN identifier."""
    return f"EVI-{uuid.uuid4().hex[:6].upper()}"

def _find(evidence_id: str, coc: ChainOfCustody) -> EvidenceItem:
    """Find evidence item by id. Raises ValueError if not found."""
    if evidence_id not in coc.evidence_items:
        raise ValueError(f"Evidence '{evidence_id}' not found in chain '{coc.case_id}'")
    return coc.evidence_items[evidence_id]

def _check(coc: object) -> ChainOfCustody:
    """Raise TypeError if not a ChainOfCustody instance."""
    if not isinstance(coc, ChainOfCustody):
        raise TypeError(f"Expected ChainOfCustody, got {type(coc).__name__}")
    return coc

# ═══════════════════════════════════════════════════════════════════
# Functions
# ═══════════════════════════════════════════════════════════════════

def create_chain_of_custody(case_id: str) -> ChainOfCustody:
    """Create a new chain of custody for a case.

    Args:
        case_id: Unique case identifier (YYYY-MM-NNNN format).

    Returns:
        New ChainOfCustody with empty evidence log.
    """
    if not case_id or not isinstance(case_id, str):
        raise ValueError("case_id must be a non-empty string")
    coc = ChainOfCustody(case_id=case_id)
    coc.transfer_log.append({"timestamp": coc.created_at, "event": "chain_created", "case_id": case_id})
    return coc


def add_evidence_item(
    coc: ChainOfCustody,
    type: str,
    description: str,
    location: str,
    collector: str,
    packaging: str | None = None,
    storage_conditions: str | None = None,
    contamination_risk: str | None = None,
    hazard_warnings: list[str] | None = None,
) -> EvidenceItem:
    """Add a new evidence item to the chain of custody.

    Args:
        coc: The chain of custody.
        type: Evidence type (PHYSICAL, DIGITAL, BIOLOGICAL, TRACE, IMPRESSION).
        description: Free-text description.
        location: Collection location.
        collector: Name and identifier of collecting person.
        packaging: Packaging protocol (default PAPER_BAG).
        storage_conditions: Auto-assigned from type if None.
        contamination_risk: Defaults to LOW if None.
        hazard_warnings: List of hazard warnings.

    Returns:
        The newly created EvidenceItem.
    """
    _check(coc)
    if not description or not isinstance(description, str):
        raise ValueError("description must be a non-empty string")
    if not location or not isinstance(location, str):
        raise ValueError("location must be a non-empty string")
    if not collector or not isinstance(collector, str):
        raise ValueError("collector must be a non-empty string")
    if packaging is None:
        packaging = EVIDENCE_TYPES.get(type, {}).get("default_packaging", "PAPER_BAG")
    if packaging not in _VP:
        raise ValueError(f"Unknown packaging '{packaging}'. Valid: {sorted(_VP)}")
    eid = _evid()
    if storage_conditions is None:
        storage_conditions = EVIDENCE_TYPES.get(type, {}).get("default_storage", "EVIDENCE_LOCKER")
    if contamination_risk is None:
        contamination_risk = "LOW"
    if hazard_warnings is None:
        hazard_warnings = []
    evidence = EvidenceItem(
        id=eid, type=type, description=description, collection_date=_ts(),
        location=location, collector=collector, packaging=packaging,
        storage_conditions=storage_conditions, contamination_risk=contamination_risk,
        hazard_warnings=list(hazard_warnings),
    )
    coc.evidence_items[eid] = evidence
    coc.last_modified = _ts()
    coc.transfer_log.append({
        "timestamp": evidence.collection_date, "event": "evidence_collected",
        "evidence_id": eid, "type": type, "collector": collector, "location": location,
    })
    return evidence


def log_transfer(
    evidence_id: str, coc: ChainOfCustody,
    from_person: str, to_person: str, reason: str,
) -> dict[str, Any]:
    """Log a transfer of evidence custody from one person to another.

    Args:
        evidence_id: Evidence item identifier.
        coc: The chain of custody.
        from_person: Transferring person (name + identifier).
        to_person: Receiving person (name + identifier).
        reason: Reason for transfer.

    Returns:
        Transfer record dict with timestamp, from, to, reason, evidence_id.
    """
    _check(coc)
    for name, val in [("from_person", from_person), ("to_person", to_person), ("reason", reason)]:
        if not val or not isinstance(val, str):
            raise ValueError(f"{name} must be a non-empty string")
    if from_person == to_person:
        raise ValueError("from_person and to_person must be different")
    evidence = _find(evidence_id, coc)
    timestamp = _ts()
    record: dict[str, Any] = {
        "transfer_id": uuid.uuid4().hex[:12], "timestamp": timestamp,
        "evidence_id": evidence_id, "from": from_person, "to": to_person,
        "reason": reason, "evidence_type": evidence.type,
        "previous_status": evidence.status,
    }
    coc.transfer_log.append(record)
    evidence.status = "TRANSFERRED"
    coc.last_modified = timestamp
    return record


def verify_chain(evidence_id: str, coc: ChainOfCustody) -> dict[str, Any]:
    """Verify chain of custody integrity for an evidence item.

    Checks for missing collection records, custody transfer gaps,
    unknown statuses, and missing seal history.

    Args:
        evidence_id: Evidence item to verify.
        coc: Chain of custody to verify against.

    Returns:
        dict with: is_valid, gaps, issues, evidence_id, case_id,
        chain_length, has_seal_history, first_collected, last_event.
    """
    _check(coc)
    evidence = _find(evidence_id, coc)
    gaps: list[dict[str, Any]] = []
    issues: list[str] = []
    relevant = [e for e in coc.transfer_log if isinstance(e, dict) and e.get("evidence_id") == evidence_id]
    collected = [e for e in relevant if e.get("event") == "evidence_collected"]
    if not collected:
        gaps.append({"type": "no_collection_record", "severity": "HIGH"})
        issues.append("No collection event in chain")
    if not relevant:
        gaps.append({"type": "no_events", "severity": "HIGH"})
        issues.append("No events logged for this evidence")
    transfers = [e for e in relevant if e.get("from") and e.get("to")]
    last_to: str | None = None
    for i, t in enumerate(transfers):
        tf, tt = t.get("from", ""), t.get("to", "")
        if i > 0 and last_to and last_to != tf:
            gaps.append({"type": "custody_gap", "severity": "MEDIUM",
                         "transfer_index": i, "expected_from": last_to, "actual_from": tf})
            issues.append(f"Gap at transfer {i}: expected '{last_to}', got '{tf}'")
        last_to = tt
    if evidence.status not in _VST:
        gaps.append({"type": "unknown_status", "severity": "MEDIUM"})
        issues.append(f"Unknown status: {evidence.status}")
    if evidence.seal_number and not evidence.seal_history:
        gaps.append({"type": "seal_without_history", "severity": "LOW"})
        issues.append("Sealed but no seal events recorded")
    is_valid = not any(g.get("severity") in ("HIGH", "CRITICAL") for g in gaps)
    return {
        "is_valid": is_valid, "gaps": gaps, "issues": issues,
        "evidence_id": evidence_id, "case_id": coc.case_id,
        "chain_length": len(relevant), "has_seal_history": bool(evidence.seal_history),
        "first_collected": collected[0]["timestamp"] if collected else None,
        "last_event": relevant[-1]["timestamp"] if relevant else None,
    }


def seal_evidence(
    evidence_id: str, coc: ChainOfCustody,
    seal_number: str, sealed_by: str | None = None,
) -> EvidenceItem:
    """Apply a tamper-evident seal to an evidence item.

    Args:
        evidence_id: Evidence item to seal.
        coc: Chain of custody.
        seal_number: Unique seal number.
        sealed_by: Person applying seal (defaults to 'SYSTEM').

    Returns:
        Updated EvidenceItem with seal applied.
    """
    _check(coc)
    if not seal_number or not isinstance(seal_number, str):
        raise ValueError("seal_number must be a non-empty string")
    evidence = _find(evidence_id, coc)
    if evidence.status == "SEALED":
        raise ValueError(f"Evidence '{evidence_id}' already sealed with '{evidence.seal_number}'")
    ts = _ts()
    sealer = sealed_by or "SYSTEM"
    evidence.seal_history.append({"event": "sealed", "timestamp": ts,
                                  "seal_number": seal_number, "applied_by": sealer})
    evidence.seal_number = seal_number
    evidence.status = "SEALED"
    coc.transfer_log.append({"timestamp": ts, "event": "evidence_sealed",
                             "evidence_id": evidence_id, "seal_number": seal_number, "applied_by": sealer})
    coc.last_modified = ts
    return evidence


def break_seal(
    evidence_id: str, coc: ChainOfCustody,
    reason: str, authorized_by: str,
) -> EvidenceItem:
    """Break a tamper-evident seal on an evidence item.

    Args:
        evidence_id: Evidence item to unseal.
        coc: Chain of custody.
        reason: Reason for breaking seal.
        authorized_by: Person authorizing the seal break.

    Returns:
        Updated EvidenceItem with broken seal recorded.
    """
    _check(coc)
    if not reason or not isinstance(reason, str):
        raise ValueError("reason must be a non-empty string")
    if not authorized_by or not isinstance(authorized_by, str):
        raise ValueError("authorized_by must be a non-empty string")
    evidence = _find(evidence_id, coc)
    if evidence.status != "SEALED":
        raise ValueError(f"Evidence '{evidence_id}' not sealed. Status: {evidence.status}")
    ts = _ts()
    evidence.seal_history.append({
        "event": "seal_broken", "timestamp": ts,
        "seal_number": evidence.seal_number or "UNKNOWN",
        "broken_by": authorized_by, "reason": reason,
    })
    evidence.seal_number = None
    evidence.status = "UNSEALED"
    prev_seal = evidence.seal_history[-2].get("seal_number", "UNKNOWN") if len(evidence.seal_history) >= 2 else "UNKNOWN"
    coc.transfer_log.append({"timestamp": ts, "event": "seal_broken", "evidence_id": evidence_id,
                             "previous_seal": prev_seal, "broken_by": authorized_by, "reason": reason})
    coc.last_modified = ts
    return evidence


def assess_contamination_risk(evidence: EvidenceItem) -> dict[str, Any]:
    """Assess contamination risk of an evidence item.

    Evaluates: evidence type, packaging permeability, storage conditions,
    seal break count, unique handler count.

    Args:
        evidence: Evidence item to assess.

    Returns:
        dict with: risk_level, risk_score, reasoning, requires_mitigation,
        recommended_mitigations, evidence_id.
    """
    if not isinstance(evidence, EvidenceItem):
        raise TypeError(f"Expected EvidenceItem, got {type(evidence).__name__}")
    reasoning: list[str] = []
    risk = 0
    type_r: dict[str, int] = {"PHYSICAL": 0, "DIGITAL": 1, "TRACE": 2, "IMPRESSION": 1, "BIOLOGICAL": 2}
    base = type_r.get(evidence.type, 1)
    risk = max(risk, base)
    if base > 0:
        reasoning.append(f"Type '{evidence.type}': +{base} base risk")
    if evidence.packaging in ("PAPER_BAG",):
        risk += 1
        reasoning.append(f"Permeable packaging '{evidence.packaging}': +1")
    if evidence.storage_conditions in ("ROOM_TEMP",):
        risk += 1
        reasoning.append("Room temp storage: +1")
    breaks = sum(1 for s in evidence.seal_history if s.get("event") == "seal_broken")
    if breaks:
        risk += breaks
        reasoning.append(f"Seal broken {breaks}x: +{breaks}")
    handlers: set[str] = set()
    for s in evidence.seal_history:
        for key in ("broken_by", "applied_by"):
            v = s.get(key, "")
            if v and v != "SYSTEM":
                handlers.add(v)
    if len(handlers) > 2:
        extra = len(handlers) - 2
        risk += extra
        reasoning.append(f"Handler count {len(handlers)}: +{extra}")
    thresholds: list[tuple[int, str]] = [(0, "NONE"), (1, "LOW"), (2, "MEDIUM"), (4, "HIGH"), (100, "CRITICAL")]
    assessed = "NONE"
    for t, name in thresholds:
        if risk >= t:
            assessed = name
        else:
            break
    risk_data = CONTAMINATION_RISK_LEVELS[assessed]
    mitigations: list[str] = list(risk_data.get("mitigations", [])) if risk_data.get("requires_mitigation") else []
    if not reasoning:
        reasoning.append("No elevated risk factors identified")
    return {
        "risk_level": assessed, "risk_score": risk, "reasoning": reasoning,
        "requires_mitigation": bool(risk_data.get("requires_mitigation")),
        "recommended_mitigations": mitigations, "evidence_id": evidence.id,
    }


def generate_chain_report(coc: ChainOfCustody) -> dict[str, Any]:
    """Generate a comprehensive chain of custody report.

    Builds per-item summary with chain verification, contamination risk,
    seal history, and transfer counts, plus aggregate summaries.

    Args:
        coc: Chain of custody to report on.

    Returns:
        dict with: case_id, generated_at, evidence_count, evidence_items,
        total_transfers, chain_integrity, storage_summary, type_summary, risk_summary.
    """
    _check(coc)
    summaries: list[dict[str, Any]] = []
    valid = gapped = issued = 0
    s_cnt: dict[str, int] = {}
    t_cnt: dict[str, int] = {}
    r_cnt: dict[str, int] = {}
    for ev_id, ev in coc.evidence_items.items():
        ver = verify_chain(ev_id, coc)
        ra = assess_contamination_risk(ev)
        if ver["is_valid"]:
            valid += 1
        if ver["gaps"]:
            gapped += 1
        if ver["issues"]:
            issued += 1
        s_cnt[ev.storage_conditions] = s_cnt.get(ev.storage_conditions, 0) + 1
        t_cnt[ev.type] = t_cnt.get(ev.type, 0) + 1
        r_cnt[ra["risk_level"]] = r_cnt.get(ra["risk_level"], 0) + 1
        summaries.append({
            "evidence_id": ev_id, "type": ev.type, "description": ev.description,
            "status": ev.status, "storage": ev.storage_conditions,
            "packaging": ev.packaging, "contamination_risk": ra["risk_level"],
            "risk_score": ra["risk_score"], "collector": ev.collector,
            "collection_date": ev.collection_date, "seal_number": ev.seal_number,
            "seal_events": len(ev.seal_history),
            "transfers": sum(1 for t in coc.transfer_log if isinstance(t, dict) and t.get("evidence_id") == ev_id),
            "chain_verified": ver["is_valid"], "chain_gaps": len(ver["gaps"]),
            "chain_issues": ver["issues"],
        })
    return {
        "case_id": coc.case_id, "generated_at": _ts(),
        "evidence_count": len(coc.evidence_items), "evidence_items": summaries,
        "total_transfers": sum(1 for t in coc.transfer_log if isinstance(t, dict) and t.get("event") != "chain_created"),
        "chain_integrity": {
            "items_valid": valid, "items_with_gaps": gapped,
            "items_with_issues": issued, "total_items": len(coc.evidence_items),
        },
        "storage_summary": s_cnt, "type_summary": t_cnt, "risk_summary": r_cnt,
    }


_SIGNATURE_ALGORITHM = "SHA-256"


def verify_digital_signature(
    coc: ChainOfCustody, evidence_id: str, signature: str
) -> bool:
    """Verify a digital signature for an evidence item.

    Computes SHA-256 of canonical representation (id|type|description|
    collection_date|collector|location) and compares against signature.

    Args:
        coc: Chain of custody.
        evidence_id: Evidence item to verify.
        signature: Expected SHA-256 hex digest.

    Returns:
        True if computed hash matches signature.
    """
    _check(coc)
    if not signature or not isinstance(signature, str):
        raise ValueError("signature must be a non-empty string")
    evidence = _find(evidence_id, coc)
    canonical = "|".join([
        evidence.id, evidence.type, evidence.description,
        evidence.collection_date, evidence.collector, evidence.location,
    ])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest() == signature
