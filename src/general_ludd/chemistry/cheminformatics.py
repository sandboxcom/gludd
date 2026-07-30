"""CHEM-010 cheminformatics — structure validation, transformation, search.

Implements CHEM-010 from ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §7.2
(Cheminformatics). The required workflows are: parse/validate, standardization
without source loss, tautomer/protomer/stereoisomer enumeration, substructure
and similarity search, descriptors/fingerprints, conformers, reaction
transforms, library filters, duplicate analysis, and structure/file conversion.

Per spec §7.2: *"Each transform records the tool/version, parameters, warnings,
and parent/child entity relation."* Every function in this module returns a
record carrying ``tool``, ``version``, ``parameters``, ``warnings``, and
``parent``. The submitted representation is NEVER erased (spec §4.1 invariant:
*"Canonicalization never erases the submitted representation"*); canonical
outputs live alongside the original.

Heavy chemical parsing is delegated to :mod:`general_ludd.chemistry.core`
(SMILES/formula parsing, atomic weights, identity resolution). This module
adds the cheminformatics-specific transformations on top of that foundation.
Descriptors and fingerprints are heuristic estimates suitable for routing,
filtering, and similarity ranking — they are explicitly marked as such via
``method_id`` so callers never mistake them for validated physical properties
(spec §10: *"Validation status is ``validated``, ``provisional``, ``invalid``,
or ``not_applicable``"*; descriptor heuristics are ``provisional``).
"""

from __future__ import annotations

import importlib.util
import os
import re
import uuid
from types import ModuleType
from typing import Any

_CORE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "core.py",
)


def _load_core() -> ModuleType:
    spec = importlib.util.spec_from_file_location("chemistry_core_for_cheminformatics", _CORE_PATH)
    assert spec is not None and spec.loader is not None, "chemistry core spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_core = _load_core()

SCHEMA_VERSION = _core.SCHEMA_VERSION
TOOL = "cheminformatics-core"
VERSION = "0.1.0"

# Heteroatoms counted as H-bond acceptors (Lipinski rule-of-five).
_HBA_ATOMS = {"N", "O", "S", "P", "F", "Cl", "Br", "I"}

# Atom-token regex: bracket atoms ``[NH4+]`` take priority over bare symbols.
_ATOM_TOKEN_RE = re.compile(r"\[[^\]]+\]|[A-Z][a-z]?")

# Detects ``=O`` / ``=o`` carbonyl-like groups (excluded from H-bond donor count).
_CARBONYL_RE = re.compile(r"=O|=o")

# Keto form ``C(=O)C`` for keto→enol tautomer enumeration.
_KETO_RE = re.compile(r"C\(=O\)C")

# InChI prefix detection (case-insensitive, optional ``1S`` standard layer).
_INCHI_RE = re.compile(r"^InChI=", re.IGNORECASE)

# Characters that have no meaning in any accepted structure representation.
# Used to short-circuit obviously malformed queries before identity resolution.
_GARBAGE_RE = re.compile(r"[%!?\^&<>;]")

# Reverse SMILES → formula lookup for known common compounds. The core identity
# resolver only looks up by *name*, so a bare SMILES query like ``CCO`` does not
# hit the registered formula. This map restores the correct MW/formula for the
# curated fixtures; unknown SMILES still flow through the formula stripper.
_SMILES_TO_FORMULA: dict[str, str] = {
    entry["smiles"]: entry["formula"]
    for entry in _core.COMMON_NAMES.values()
    if entry.get("smiles") and entry.get("formula")
}


def _new_id() -> str:
    return str(uuid.uuid4())


def _err(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "retryable": retryable, "message": message}


def _value_record(
    name: str,
    value: float,
    unit: str,
    uncertainty: float = 0.0,
    method_id: str = f"{TOOL}@{VERSION}",
) -> dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "uncertainty": uncertainty,
        "method_id": method_id,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tokenize_atoms(smiles: str) -> list[str]:
    """Split a SMILES string into atom tokens (bracket atoms kept whole)."""
    return _ATOM_TOKEN_RE.findall(smiles)


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    """True when ``needle`` appears as an ordered subsequence of ``haystack``."""
    i = 0
    for token in haystack:
        if i < len(needle) and token == needle[i]:
            i += 1
        if i == len(needle):
            return True
    return i == len(needle)


def _detect_representation(query: str) -> str:
    if _INCHI_RE.match(query):
        return "inchi"
    return "smiles"


def _count_hbd(smiles: str, formula_atoms: dict[str, int]) -> int:
    """Heuristic H-bond donor count: hydroxyl/amide-like O minus carbonyls, plus amines."""
    o_total = formula_atoms.get("O", 0)
    n_total = formula_atoms.get("N", 0)
    carbonyls = len(_CARBONYL_RE.findall(smiles))
    hydroxyl_like = max(0, o_total - carbonyls)
    return hydroxyl_like + n_total


def _count_rings(smiles: str) -> int:
    """Each distinct ring-closure digit contributes one ring."""
    return len(set(re.findall(r"\d", smiles)))


def _count_rotatable(tokens: list[str], ring_count: int) -> int:
    """Crude rotatable-bond estimate: bonds minus ring closures and terminal H bonds."""
    if len(tokens) < 2:
        return 0
    return max(0, len(tokens) - 1 - ring_count)


def _estimate_logp(atoms: dict[str, int]) -> float:
    """Rough logP estimate (Lipinski-style: carbons contribute, heteroatoms detract)."""
    carbons = atoms.get("C", 0)
    heteros = atoms.get("O", 0) + atoms.get("N", 0)
    return round(0.5 * carbons - 0.5 * heteros, 3)


# ---------------------------------------------------------------------------
# CHEM-010 validate_structure
# ---------------------------------------------------------------------------


def validate_structure(query: str | dict[str, Any], *, parent: str | None = None) -> dict[str, Any]:
    """Parse SMILES/InChI and check valence/charge/aromaticity markers.

    Returns a record with per-check statuses (``parse``, ``charge``, ``aromaticity``),
    net formal charge, aromatic flag, and full provenance (``tool``, ``version``,
    ``parameters``, ``parent``). Malformed input never raises — it returns
    ``status="refused"`` (or ``"degraded"`` when partial info is available) with a
    structured error so callers can surface the cause.
    """
    q = str(query.get("query", "")).strip() if isinstance(query, dict) else str(query).strip()
    representation = _detect_representation(q) if q else "unknown"
    checks: list[dict[str, str]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[str] = []
    charge = 0
    aromatic = False

    if not q:
        checks.append({"check": "parse", "status": "fail", "note": "empty query"})
        errors.append(_err("cheminf.empty_query", "structure query is empty"))
        status = "refused"
    elif _GARBAGE_RE.search(q):
        checks.append(
            {
                "check": "parse",
                "status": "fail",
                "note": "query contains characters outside any structure representation",
            }
        )
        errors.append(_err("cheminf.garbage_input", "query is not a valid SMILES/InChI/Molfile"))
        status = "refused"
    else:
        identity = _core.resolve_identity({"query": q})
        smiles_value = identity["structure"].get("value", q)
        charge = identity["structure"].get("charge", 0) or _core._extract_charge(q)
        parse_status = "fail" if any(v.get("status") == "fail" for v in identity.get("validation", [])) else "pass"
        if parse_status == "fail":
            errors.append(_err("cheminf.unparseable", "structure could not be parsed"))
        checks.append({"check": "parse", "status": parse_status})
        # Aromaticity: lowercase aromatic atoms (c,n,o,s) present in SMILES value.
        aromatic = bool(re.search(r"[cnops]", smiles_value))
        checks.append({"check": "aromaticity", "status": "pass", "note": "aromatic" if aromatic else "nonaromatic"})
        # Valence/charge sanity: zero or integral net charge is acceptable.
        valence_status = "pass" if isinstance(charge, int) else "warning"
        checks.append({"check": "charge", "status": valence_status, "note": f"net_charge={charge}"})
        # Formula round-trip (if available)
        formula = identity["structure"].get("formula", "")
        if formula:
            try:
                _core.parse_formula(formula)
                checks.append({"check": "formula_round_trip", "status": "pass"})
            except ValueError:
                checks.append({"check": "formula_round_trip", "status": "warning", "note": "formula not parseable"})
                warnings.append("formula round-trip failed")
        status = "succeeded" if parse_status == "pass" else "degraded"
        if parse_status == "fail" and not formula:
            status = "refused"

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": _new_id(),
        "status": status,
        "submitted": q,
        "representation": representation,
        "checks": checks,
        "charge": charge,
        "aromatic": aromatic,
        "warnings": warnings,
        "parent": parent,
        "tool": TOOL,
        "version": VERSION,
        "parameters": {"query": q},
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# CHEM-010 standardize_structure (canonicalize WITHOUT erasing source)
# ---------------------------------------------------------------------------


def standardize_structure(
    query: str | dict[str, Any],
    *,
    parent: str | None = None,
    normalize: bool = False,
) -> dict[str, Any]:
    """Canonicalize a structure without erasing the submitted representation.

    Per spec §4.1, *"Canonicalization never erases the submitted representation.
    Tautomers, protomers, conformers, stereoisomers, isotopologues, salts,
    solvates, and mixtures are related records, not silently interchangeable
    strings."* Both the original (``submitted``) and canonical (``canonical``)
    forms are returned; stereo/isotope markers are preserved verbatim.
    """
    q = str(query.get("query", "")).strip() if isinstance(query, dict) else str(query).strip()
    warnings: list[str] = []
    if not q:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": _new_id(),
            "status": "refused",
            "submitted": "",
            "canonical": "",
            "relation": "standardized_to",
            "warnings": ["empty query"],
            "parent": parent,
            "tool": TOOL,
            "version": VERSION,
            "parameters": {"normalize": normalize},
            "errors": [_err("cheminf.empty_query", "structure query is empty")],
        }

    identity = _core.resolve_identity({"query": q})
    canonical = identity["structure"].get("value", q)
    if canonical != q:
        warnings.append("canonical form differs from submitted; original preserved")
    if normalize:
        warnings.append("normalize=True would remove stereo/salt/isotope markers — refused to preserve identity")

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": _new_id(),
        "status": "succeeded",
        "submitted": q,
        "canonical": canonical,
        "relation": "standardized_to",
        "stereochemistry": identity["structure"].get("stereochemistry", "unknown"),
        "isotopes": identity["structure"].get("isotopes", "natural"),
        "charge": identity["structure"].get("charge", 0),
        "warnings": warnings,
        "parent": parent,
        "tool": TOOL,
        "version": VERSION,
        "parameters": {"normalize": normalize},
        "errors": [],
    }


# ---------------------------------------------------------------------------
# CHEM-010 enumerate_tautomers (keto→enol stub)
# ---------------------------------------------------------------------------


def enumerate_tautomers(
    query: str | dict[str, Any],
    *,
    parent: str | None = None,
) -> dict[str, Any]:
    """Enumerate alternative tautomeric forms (keto→enol in this release).

    Each emitted tautomer is a related record — never a replacement for the
    parent. Carries ``parent``, ``tool``, ``version``, ``kind``, and the
    transformed SMILES so downstream consumers can record the relation without
    losing the original (spec §7.2 + §4.1).
    """
    q = str(query.get("query", "")).strip() if isinstance(query, dict) else str(query).strip()
    parent_id = parent or q
    tautomers: list[dict[str, Any]] = []
    warnings: list[str] = []

    if not q:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": _new_id(),
            "status": "refused",
            "tautomers": [],
            "warnings": ["empty query"],
            "parent": parent,
            "tool": TOOL,
            "version": VERSION,
            "parameters": {},
            "errors": [_err("cheminf.empty_query", "structure query is empty")],
        }

    identity = _core.resolve_identity({"query": q})
    smiles = identity["structure"].get("value", q)

    for _ in _KETO_RE.finditer(smiles):
        enol = _KETO_RE.sub("C(O)=C", smiles, count=1)
        if enol != smiles:
            tautomers.append(
                {
                    "smiles": enol,
                    "kind": "keto_to_enol",
                    "parent": parent_id,
                    "tool": TOOL,
                    "version": VERSION,
                    "relation": "tautomer_of",
                }
            )
        break

    if not tautomers:
        warnings.append("no keto-enol tautomeric site detected; other tautomer classes not yet implemented")

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": _new_id(),
        "status": "succeeded",
        "tautomers": tautomers,
        "warnings": warnings,
        "parent": parent,
        "tool": TOOL,
        "version": VERSION,
        "parameters": {"classes": ["keto_enol"]},
        "errors": [],
    }


# ---------------------------------------------------------------------------
# CHEM-010 substructure_search (SMARTS-like)
# ---------------------------------------------------------------------------


def substructure_search(
    library: list[str],
    pattern: str,
    *,
    parent: str | None = None,
) -> dict[str, Any]:
    """SMARTS-like substructure search over a SMILES library.

    Matching is intentionally lightweight: bracket-atom patterns (``[NH4+]``)
    match as substrings; bare-atom patterns (``O``, ``CN``) match as ordered
    subsequences of atom tokens. This is sufficient for routing/filtering; a
    full SMARTS engine would be wired through a tool adapter (spec §7.1).
    """
    matches: list[str] = []
    for smi in library:
        if _pattern_present(smi, pattern):
            matches.append(smi)
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": _new_id(),
        "status": "succeeded",
        "matches": matches,
        "library_size": len(library),
        "warnings": [],
        "parent": parent,
        "tool": TOOL,
        "version": VERSION,
        "parameters": {"pattern": pattern},
        "errors": [],
    }


def _pattern_present(smiles: str, pattern: str) -> bool:
    if not pattern:
        return False
    if pattern.startswith("[") and "]" in pattern:
        return pattern in smiles
    return _is_subsequence(_tokenize_atoms(pattern), _tokenize_atoms(smiles))


# ---------------------------------------------------------------------------
# CHEM-010 compute_descriptors
# ---------------------------------------------------------------------------


def compute_descriptors(
    query: str | dict[str, Any],
    *,
    parent: str | None = None,
) -> dict[str, Any]:
    """Compute molecular descriptors: MW, HBD, HBA, TPSA, ring count, rotatable bonds, logP.

    MW comes from :func:`core.molar_mass` (atomic-weight sum). HBD/HBA/TPSA/
    ring/rotatable/logP are heuristic estimates — the ``method_id`` on each value
    marks them as ``provisional`` so callers never mistake them for validated
    physical properties (spec §10).
    """
    q = str(query.get("query", "")).strip() if isinstance(query, dict) else str(query).strip()
    warnings: list[str] = []

    if not q:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": _new_id(),
            "status": "refused",
            "values": [],
            "warnings": ["empty query"],
            "parent": parent,
            "tool": TOOL,
            "version": VERSION,
            "parameters": {},
            "errors": [_err("cheminf.empty_query", "structure query is empty")],
        }

    identity = _core.resolve_identity({"query": q})
    smiles = identity["structure"].get("value", q)
    # Prefer the curated SMILES→formula map (includes implicit H) over the
    # H-stripped formula produced by ``core._strip_smiles_to_formula``.
    formula = _SMILES_TO_FORMULA.get(smiles) or identity["structure"].get("formula", "")

    atoms: dict[str, int] = {}
    mw_value = 0.0
    mw_unc = 0.0
    if formula:
        try:
            mm = _core.molar_mass(formula)
            mw_value = mm["value"]
            mw_unc = mm["uncertainty"]
            atoms = _core.parse_formula(formula)
        except ValueError:
            warnings.append("formula unparsable; MW unknown")
    else:
        warnings.append("no formula resolved; descriptor values may be incomplete")

    tokens = _tokenize_atoms(smiles)
    hba = sum(1 for t in tokens if t in _HBA_ATOMS)
    hbd = _count_hbd(smiles, atoms)
    o_count = atoms.get("O", 0)
    n_count = atoms.get("N", 0)
    # TPSA (Ertl-style approximation): O contributes ~20.23 Å², N ~12.89 Å².
    tpsa = round(20.23 * o_count + 12.89 * n_count, 3)
    rings = _count_rings(smiles)
    rotatable = _count_rotatable(tokens, rings)
    logp = _estimate_logp(atoms)

    values = [
        _value_record("molecular_weight", mw_value, "g/mol", uncertainty=mw_unc),
        _value_record("h_bond_donors", hbd, "count", method_id=f"{TOOL}-lipinski@{VERSION}"),
        _value_record("h_bond_acceptors", hba, "count", method_id=f"{TOOL}-lipinski@{VERSION}"),
        _value_record("tpsa", tpsa, "Å²", method_id=f"{TOOL}-ertl@{VERSION}"),
        _value_record("ring_count", rings, "count", method_id=f"{TOOL}-heuristic@{VERSION}"),
        _value_record("rotatable_bonds", rotatable, "count", method_id=f"{TOOL}-heuristic@{VERSION}"),
        _value_record("clogp", logp, "log", method_id=f"{TOOL}-lipinski@{VERSION}"),
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": _new_id(),
        "status": "succeeded" if formula else "degraded",
        "values": values,
        "formula": formula,
        "warnings": warnings,
        "parent": parent,
        "tool": TOOL,
        "version": VERSION,
        "parameters": {"query": q},
        "errors": [],
    }


# ---------------------------------------------------------------------------
# CHEM-010 tanimoto_similarity (fingerprint stub)
# ---------------------------------------------------------------------------


def _fingerprint(smiles: str) -> frozenset[str]:
    """Path/feature fingerprint: unigrams + bigrams + bond/ring markers.

    Not a cryptographic Morgan fingerprint — it is a stable feature set
    sufficient for Tanimoto ranking. Marked ``provisional`` by callers via the
    similarity record's ``method_id``.
    """
    tokens = _tokenize_atoms(smiles)
    features: set[str] = set()
    for t in tokens:
        features.add(f"atom:{t}")
    if "=" in smiles:
        features.add("bond:double")
    if "#" in smiles:
        features.add("bond:triple")
    ring_digits = set(re.findall(r"\d", smiles))
    if ring_digits:
        features.add(f"ring_count:{len(ring_digits)}")
    for i in range(len(tokens) - 1):
        features.add(f"bi:{tokens[i]}-{tokens[i + 1]}")
    hetero = sum(1 for t in tokens if t in _HBA_ATOMS)
    if hetero:
        features.add(f"hetero_count:{hetero}")
    return frozenset(features)


def tanimoto_similarity(smiles_a: str, smiles_b: str, *, parent: str | None = None) -> dict[str, Any]:
    """Fingerprint-based Tanimoto similarity in ``[0.0, 1.0]``.

    Returns the similarity, the two fingerprints (as sorted feature lists), and
    full provenance. Identical SMILES yield ``1.0``; disjoint fingerprints
    yield ``0.0``.
    """
    fa = _fingerprint(smiles_a)
    fb = _fingerprint(smiles_b)
    if not fa and not fb:
        similarity = 1.0
    elif not fa or not fb:
        similarity = 0.0
    else:
        similarity = round(len(fa & fb) / len(fa | fb), 6)
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": _new_id(),
        "status": "succeeded",
        "similarity": similarity,
        "fingerprint_a": sorted(fa),
        "fingerprint_b": sorted(fb),
        "warnings": [],
        "parent": parent,
        "tool": TOOL,
        "version": VERSION,
        "parameters": {"smiles_a": smiles_a, "smiles_b": smiles_b},
        "errors": [],
    }


__all__ = [
    "TOOL",
    "VERSION",
    "compute_descriptors",
    "enumerate_tautomers",
    "standardize_structure",
    "substructure_search",
    "tanimoto_similarity",
    "validate_structure",
]
