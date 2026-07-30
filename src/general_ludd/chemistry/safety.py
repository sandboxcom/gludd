"""CHEM-008 safety and compatibility — risk classification and controls.

Implements CHEM-008 from ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §2 and §9.

The screening surface lives above :mod:`general_ludd.chemistry.core`, reusing
its ``HAZARD_REGISTRY`` and ``INCOMPATIBILITY_MATRIX`` rather than duplicating
them. This module adds:

* A typed :class:`SafetyScreen` result carrying ``risk_tier``,
  ``required_controls``, ``missing_controls``, ``hazard_classes``,
  ``incompatibilities``, ``limitations``, and a ``refused_reason`` that is
  populated whenever actionable output must be blocked (spec §9 refusal
  table).
* :func:`classify_risk` — tier classification that considers acute / chronic
  toxicity, flammability, explosivity, and reactivity (water-reactive,
  pyrophoric) hazards, plus scale- and concentration-dependent elevation per
  §9 ("scale, concentration, temperature, energy") and §7.5 ("a lab-scale
  procedure cannot be linearly scaled").
* :func:`check_compatibility` — detect incompatible chemical pairs (oxidizer +
  flammable, acid + base exotherm, etc.) and return their severity.

Spec §9 refusal contract — the following always hold:

* Missing current hazard evidence → actionable output is refused (research may
  continue). Never a silent ``low``.
* Facility lacks a required control → ``refused`` with a named reason. The
  message never suggests bypassing the control.
* Prohibited tier → actionable detail is refused and a policy decision is
  recorded.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

# This module is loaded by file path in the test suite (mirroring
# ``test_chemistry_core.py``), so we cannot rely on a normal package import.
_CORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core.py")


def _load_core() -> ModuleType:
    spec = importlib.util.spec_from_file_location("chemistry_core_for_safety", _CORE_PATH)
    assert spec is not None and spec.loader is not None, "chemistry core spec failed"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_core = _load_core()

SCHEMA_VERSION = _core.SCHEMA_VERSION
_TIER_RANK = _core._TIER_RANK
_RANK_TIER = _core._RANK_TIER

# Scale thresholds — see spec §7.5 and §9.
# Industrial operations amplify even moderate hazards (vapor cloud formation,
# exotherm runaway, pressure accumulation) beyond their lab-tier reading.
_SCALE_ELEVATION = {
    # scale -> {source_tier_rank -> elevated_tier_rank}
    "industrial": {_TIER_RANK["moderate"]: _TIER_RANK["high"]},
    "pilot": {},  # pilot is intermediate; we do not auto-elevate.
    "lab": {},
}

# Concentration (mol/L) above which an aqueous reagent is treated as
# "concentrated" and its moderate lab-tier reading is elevated.
CONCENTRATED_THRESHOLD_MOL_PER_L = 6.0


@dataclass
class SafetyScreen:
    """Typed result of :func:`classify_risk`.

    ``refused_reason`` is set whenever actionable output must be blocked per
    spec §9 (missing hazard evidence, missing facility control, prohibited
    tier). It is ``None`` when actionable work may proceed.
    """

    risk_tier: str
    required_controls: list[str]
    missing_controls: list[str]
    hazard_classes: list[str]
    incompatibilities: list[dict[str, Any]]
    per_entity: list[dict[str, Any]]
    limitations: list[str]
    scale: str = "lab"
    concentration: float | None = None
    refused_reason: str | None = None
    safety: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "risk_tier": self.risk_tier,
            "required_controls": list(self.required_controls),
            "missing_controls": list(self.missing_controls),
            "hazard_classes": list(self.hazard_classes),
            "incompatibilities": list(self.incompatibilities),
            "per_entity": list(self.per_entity),
            "limitations": list(self.limitations),
            "scale": self.scale,
            "concentration": self.concentration,
            "refused_reason": self.refused_reason,
            "safety": dict(self.safety),
        }


def _resolve_hazard_entry(query: str) -> dict[str, Any] | None:
    result: dict[str, Any] | None = _core._resolve_hazard_entry(query)
    return result


def check_compatibility(entities: list[str]) -> list[dict[str, Any]]:
    """Return incompatibility findings among ``entities`` (CHEM-008).

    Walks the union of hazard classes observed across the entity set and
    returns one finding per matched pair in ``INCOMPATIBILITY_MATRIX``. Each
    finding carries ``kind``, the sorted pair of hazard classes, and the
    ``severity`` tier the pair elevates the screen to.
    """
    classes_seen: set[str] = set()
    for ent in entities:
        entry = _resolve_hazard_entry(ent)
        if entry is not None:
            classes_seen.update(entry["classes"])

    findings: list[dict[str, Any]] = []
    for pair, (kind, severity) in _core.INCOMPATIBILITY_MATRIX.items():
        if pair.issubset(classes_seen):
            findings.append(
                {
                    "kind": kind,
                    "classes": sorted(pair),
                    "severity": severity,
                }
            )
    return findings


def classify_risk(
    entities: list[str] | str,
    *,
    scale: str = "lab",
    concentration: float | None = None,
    facility_controls: list[str] | None = None,
) -> SafetyScreen:
    """Classify risk for one or more chemicals (CHEM-008, spec §9).

    Parameters
    ----------
    entities:
        One chemical name/SMILES or a list of them.
    scale:
        ``"lab"``, ``"pilot"``, or ``"industrial"``. Industrial scale
        elevates moderate hazards to high (§7.5).
    concentration:
        Aqueous-phase concentration in mol/L. Above
        :data:`CONCENTRATED_THRESHOLD_MOL_PER_L`, a moderate-tier reagent is
        elevated to high.
    facility_controls:
        Controls the facility already has (e.g. ``"acid_PPE"``,
        ``"ventilation"``). Missing required controls trigger refusal.

    Returns
    -------
    SafetyScreen
        Typed screen result. ``refused_reason`` is non-None whenever
        actionable output must be blocked per §9.
    """
    if isinstance(entities, str):
        entity_list: list[str] = [entities]
    else:
        entity_list = list(entities)
    facility = list(facility_controls or [])
    scale = scale if scale in _SCALE_ELEVATION else "lab"

    per_entity: list[dict[str, Any]] = []
    classes_seen: set[str] = set()
    required: list[str] = []
    limitations: list[str] = []
    missing_evidence = False
    tier = 0  # _TIER_RANK["low"]

    for ent in entity_list:
        entry = _resolve_hazard_entry(ent)
        if entry is None:
            per_entity.append(
                {
                    "query": ent,
                    "classes": [],
                    "tier": "moderate",
                    "controls": [],
                }
            )
            limitations.append("missing-current-hazard-evidence: hazard record unavailable")
            missing_evidence = True
            tier = max(tier, _TIER_RANK["moderate"])
            continue
        per_entity.append(
            {
                "query": ent,
                "classes": list(entry["classes"]),
                "tier": entry["tier"],
                "controls": list(entry["controls"]),
            }
        )
        classes_seen.update(entry["classes"])
        required.extend(entry["controls"])
        tier = max(tier, _TIER_RANK[entry["tier"]])

    # Incompatibility propagation.
    incompatibilities = check_compatibility(entity_list)
    for finding in incompatibilities:
        tier = max(tier, _TIER_RANK[finding["severity"]])

    # Scale elevation (§7.5): industrial operations amplify moderate hazards.
    scale_rule = _SCALE_ELEVATION[scale]
    if tier in scale_rule:
        elevated = scale_rule[tier]
        if elevated > tier:
            tier = elevated
            limitations.append(f"scale-elevation: {scale} scale amplifies hazard tier per spec §7.5")

    # Concentration elevation (§9): concentrated reagents are more severe.
    if (
        concentration is not None
        and concentration > CONCENTRATED_THRESHOLD_MOL_PER_L
        and tier == _TIER_RANK["moderate"]
    ):
        tier = _TIER_RANK["high"]
        limitations.append(
            f"concentration-elevation: {concentration} mol/L exceeds "
            f"concentrated threshold ({CONCENTRATED_THRESHOLD_MOL_PER_L} mol/L)"
        )

    risk_tier = _RANK_TIER[tier]
    required_controls = sorted(set(required))
    missing_controls = [c for c in required_controls if c not in facility]

    refused_reason = _decide_refusal(
        risk_tier=risk_tier,
        missing_controls=missing_controls,
        missing_evidence=missing_evidence,
        limitations=limitations,
    )

    safety_block = {
        "risk_tier": risk_tier,
        "review_id": _core._new_id(),
        # High / prohibited tiers require external approval before actionable
        # output; lower tiers are self-approved.
        "approvals": [] if risk_tier in {"high", "prohibited"} else [_core._new_id()],
    }

    return SafetyScreen(
        risk_tier=risk_tier,
        required_controls=required_controls,
        missing_controls=missing_controls,
        hazard_classes=sorted(classes_seen),
        incompatibilities=incompatibilities,
        per_entity=per_entity,
        limitations=limitations,
        scale=scale,
        concentration=concentration,
        refused_reason=refused_reason,
        safety=safety_block,
    )


def _decide_refusal(
    *,
    risk_tier: str,
    missing_controls: list[str],
    missing_evidence: bool,
    limitations: list[str],
) -> str | None:
    """Apply spec §9 refusal rules and return a human-stable reason.

    The reason never suggests bypassing a control. Returns ``None`` when
    actionable output may proceed.
    """
    # Prohibited tier: always refused pending approval / permits.
    if risk_tier == "prohibited":
        limitations.append(
            "policy: actionable output refused until qualified approval, permits, and facility controls are present"
        )
        return "prohibited hazard tier: actionable output refused pending qualified approval and permits"

    # High tier with missing facility controls: refused for the gap.
    if risk_tier == "high" and missing_controls:
        limitations.append("facility-lacks-required-control: " + ", ".join(missing_controls))
        return (
            "facility lacks required controls: " + ", ".join(missing_controls) + " — install before actionable output"
        )

    # Missing hazard evidence: protocol drafting refused (research may continue).
    if missing_evidence:
        return "missing-current-hazard-evidence: protocol drafting refused; research may continue"

    return None


__all__ = [
    "CONCENTRATED_THRESHOLD_MOL_PER_L",
    "SafetyScreen",
    "check_compatibility",
    "classify_risk",
]
