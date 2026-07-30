"""CHEM-005 reaction reasoning — balance, classify, and compare reactions.

Implements CHEM-005 from ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §2.

* ``balance_reaction`` — verify atom / mass / charge conservation. An
  unaccounted imbalance cannot return ``succeeded`` (CHEM-AT-006). When an
  integer stoichiometry exists in a small search window, returns the
  coefficients; otherwise the result is ``failed`` with a structural error.
* ``classify_reaction`` — categorize a reaction as synthesis / decomposition /
  single-displacement / acid-base / combustion / redox / metathesis.
* ``compare_reactions`` — Jaccard similarity over reactant and product sets.

This module reuses the formula parser, atomic weights, charge extractor, and
integer-balancer already shipped in ``general_ludd.chemistry.core``; it does
not re-implement them.
"""

from __future__ import annotations

import importlib.util
import os
from types import ModuleType
from typing import Any

# This module is loaded by file path in the test suite (mirroring
# ``test_chemistry_core.py``), so we cannot rely on a normal package import.
# Resolve ``core`` from its absolute source path at runtime.
_CORE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "core.py",
)


def _load_core() -> ModuleType:
    spec = importlib.util.spec_from_file_location("chemistry_core_for_reactions", _CORE_PATH)
    assert spec is not None and spec.loader is not None, "chemistry core spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_core = _load_core()

SCHEMA_VERSION = _core.SCHEMA_VERSION


def _err(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "retryable": retryable, "message": message}


def _species_atoms(token: str) -> dict[str, int]:
    """Return ``{element: atom_count}`` for a formula or SMILES-like token."""
    try:
        parsed: dict[str, int] = _core.parse_formula(token)
        return parsed
    except ValueError:
        fallback: dict[str, int] = _core.parse_formula(_core._strip_smiles_to_formula(token))
        return fallback


def _hill_formula(atoms: dict[str, int]) -> str:
    result: str = _core._hill_formula(atoms)
    return result


def _formula_mass(atoms: dict[str, int]) -> float:
    result: float = _core._formula_mass(atoms)
    return result


def _extract_charge(token: str) -> int:
    result: int = _core._extract_charge(token)
    return result


# ---------------------------------------------------------------------------
# balance_reaction
# ---------------------------------------------------------------------------


def balance_reaction(reaction: dict[str, Any]) -> dict[str, Any]:
    """Verify atom / mass / charge conservation for ``reaction``.

    ``reaction`` has ``reactants`` and ``products`` as lists of formula or
    SMILES tokens. Returns coefficients when a small-window integer balance
    exists, plus per-atom mass / atom / charge verification checks.

    CHEM-AT-006 contract: an unaccounted imbalance cannot return ``succeeded``.
    If no integer coefficient vector zeros the conservation residual, the
    result is ``failed`` (not ``degraded``) with a structural error.
    """
    reactants = list(reaction.get("reactants", []))
    products = list(reaction.get("products", []))
    if not reactants or not products:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": _core._new_id(),
            "status": "refused",
            "summary": "reaction missing reactants or products",
            "coefficients": {"reactants": {}, "products": {}},
            "balanced": False,
            "verification": [
                {
                    "check": "species_present",
                    "status": "fail",
                    "note": "both reactants and products required",
                }
            ],
            "errors": [_err("chem.empty_reaction", "empty reactants or products")],
        }

    reactant_atoms = [_species_atoms(t) for t in reactants]
    product_atoms = [_species_atoms(t) for t in products]

    left: dict[str, int] = {}
    for atoms in reactant_atoms:
        for el, cnt in atoms.items():
            left[el] = left.get(el, 0) + cnt
    right: dict[str, int] = {}
    for atoms in product_atoms:
        for el, cnt in atoms.items():
            right[el] = right.get(el, 0) + cnt

    elements = sorted(set(left) | set(right))

    # Atom balance on the raw, un-scaled reaction (necessary condition for
    # any balanced solution: each element must appear on both sides).
    atoms_present_both_sides = all((el in left) and (el in right) for el in elements)
    raw_atom_balanced = left == right

    # Build the linear system and brute-force integer coefficients.
    species = reactants + products
    matrix: list[list[int]] = []
    for el in elements:
        row = []
        for i, atoms in enumerate(reactant_atoms + product_atoms):
            sign = 1 if i < len(reactant_atoms) else -1
            row.append(sign * atoms.get(el, 0))
        matrix.append(row)

    coeffs = _core._solve_integer_balance(matrix, len(species))
    balanced = coeffs is not None

    coefficients: dict[str, dict[str, int]] = {"reactants": {}, "products": {}}
    if balanced and coeffs is not None:
        for i, token in enumerate(reactants):
            coefficients["reactants"][token] = coeffs[i]
        for j, token in enumerate(products):
            coefficients["products"][token] = coeffs[len(reactant_atoms) + j]

    # Mass balance: weighted by coefficients when balanced, otherwise raw.
    if balanced and coeffs is not None:
        left_mass = sum(_formula_mass(reactant_atoms[i]) * coeffs[i] for i in range(len(reactants)))
        right_mass = sum(
            _formula_mass(product_atoms[j]) * coeffs[len(reactant_atoms) + j] for j in range(len(products))
        )
    else:
        left_mass = sum(_formula_mass(a) for a in reactant_atoms)
        right_mass = sum(_formula_mass(a) for a in product_atoms)
    mass_ok = abs(left_mass - right_mass) < 1e-6 * max(1.0, left_mass)

    # Charge balance: weighted by coefficients when balanced, otherwise raw.
    if balanced and coeffs is not None:
        left_charge = sum(_extract_charge(t) * coeffs[i] for i, t in enumerate(reactants))
        right_charge = sum(_extract_charge(t) * coeffs[len(reactant_atoms) + j] for j, t in enumerate(products))
    else:
        left_charge = sum(_extract_charge(t) for t in reactants)
        right_charge = sum(_extract_charge(t) for t in products)
    charge_ok = left_charge == right_charge

    # Atom balance verdict: pass only when the scaled counts agree.
    if balanced and coeffs is not None:
        scaled_left = {
            el: sum(reactant_atoms[i].get(el, 0) * coeffs[i] for i in range(len(reactants))) for el in elements
        }
        scaled_right = {
            el: sum(product_atoms[j].get(el, 0) * coeffs[len(reactant_atoms) + j] for j in range(len(products)))
            for el in elements
        }
        atom_ok = scaled_left == scaled_right
    else:
        atom_ok = raw_atom_balanced

    # CHEM-AT-006: an unaccounted imbalance cannot return succeeded.
    # status ladder:
    #   * balanced + all checks pass -> succeeded
    #   * any check fails AND no integer balance -> failed (not degraded)
    #   * missing species -> refused (handled above)
    if balanced and atom_ok and mass_ok and charge_ok:
        status = "succeeded"
        summary = "balanced integer stoichiometry with mass/atom/charge conservation"
        errors: list[dict[str, Any]] = []
    else:
        # Unaccounted imbalance — never succeeded.
        status = "failed"
        missing = [el for el in elements if not ((el in left) and (el in right))]
        if missing and not atoms_present_both_sides:
            summary = f"unaccounted atom imbalance: elements {missing} absent from one side"
            errors = [
                _err(
                    "chem.atom_imbalance",
                    f"elements missing on one side: {','.join(missing)}",
                )
            ]
        else:
            summary = "no integer stoichiometry satisfying conservation"
            errors = [
                _err(
                    "chem.unbalanced",
                    "could not find integer stoichiometric coefficients satisfying conservation",
                )
            ]

    verification = [
        {
            "check": "mass_balance",
            "status": "pass" if (balanced and mass_ok) else "fail",
            "artifact_uri": "",
        },
        {
            "check": "atom_balance",
            "status": "pass" if (balanced and atom_ok) else "fail",
            "artifact_uri": "",
        },
        {
            "check": "charge_balance",
            "status": "pass" if (balanced and charge_ok) else "fail",
            "artifact_uri": "",
        },
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": _core._new_id(),
        "status": status,
        "summary": summary,
        "species": species,
        "elements": elements,
        "coefficients": coefficients,
        "balanced": balanced,
        "verification": verification,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# classify_reaction
# ---------------------------------------------------------------------------


def _is_acid(token: str) -> bool:
    """Heuristic: an acid donates H+ in aqueous solution."""
    t = token.lower()
    if t in {"hcl", "h2so4", "hno3", "h3po4", "hbr", "hi", "hf", "hcooh", "ch3cooh"}:
        return True
    if t.endswith("acid") or "acid" in t:
        return True
    # formula starting with H and containing a nonmetal beyond H
    try:
        atoms = _species_atoms(token)
        return "H" in atoms and len(atoms) > 1 and not _is_strong_base(token)
    except ValueError:
        return False


def _is_base(token: str) -> bool:
    return _is_strong_base(token)


def _is_strong_base(token: str) -> bool:
    t = token.lower()
    if t in {"naoh", "koh", "lioh", "ca(oh)2", "ba(oh)2", "sr(oh)2", "nh3", "ammonia"}:
        return True
    try:
        atoms = _species_atoms(token)
        return atoms.get("O", 0) > 0 and "H" in atoms and len(atoms) <= 2
    except ValueError:
        return False


def _contains_oxygen(tokens: list[str]) -> bool:
    for t in tokens:
        try:
            if "O" in _species_atoms(t):
                return True
        except ValueError:
            pass
    return False


def classify_reaction(reaction: dict[str, Any]) -> dict[str, Any]:
    """Classify a reaction (CHEM-005).

    Recognized classes (primary, first match wins):

    * ``combustion`` — hydrocarbon + O2 -> CO2 + H2O
    * ``acid_base`` — acid + base -> salt + water
    * ``synthesis`` — multiple reactants -> single product
    * ``decomposition`` — single reactant -> multiple products
    * ``single_displacement`` — element + compound -> element + compound
    * ``double_displacement`` / ``metathesis`` — AB + CD -> AD + CB
    * ``redox`` — fall-back when an element changes oxidation character
    """
    reactants = list(reaction.get("reactants", []))
    products = list(reaction.get("products", []))
    n_r = len(reactants)
    n_p = len(products)

    r_set = {t for t in reactants}
    p_set = {t for t in products}

    # Combustion: a carbon/hydrogen source + O2 -> CO2 + H2O.
    if "O2" in r_set and "CO2" in p_set and "H2O" in p_set:
        return _classification_record("combustion", reaction)

    # Acid-base: acid + base on reactants, water + salt on products.
    has_acid = any(_is_acid(t) for t in reactants)
    has_base = any(_is_base(t) for t in reactants)
    if has_acid and has_base and "H2O" in p_set:
        return _classification_record("acid_base", reaction)

    # Synthesis: 2+ reactants -> 1 product.
    if n_r >= 2 and n_p == 1:
        return _classification_record("synthesis", reaction)

    # Decomposition: 1 reactant -> 2+ products.
    if n_r == 1 and n_p >= 2:
        return _classification_record("decomposition", reaction)

    # Single displacement: element + compound -> element + compound.
    if n_r == 2 and n_p == 2:
        r_single = [t for t in reactants if _is_elemental(t)]
        p_single = [t for t in products if _is_elemental(t)]
        if r_single and p_single:
            return _classification_record("single_displacement", reaction)
        return _classification_record("double_displacement", reaction)

    # Redox fall-back: oxygen transfer or 2 species on each side without
    # a clearer match.
    if _contains_oxygen(reactants) and _contains_oxygen(products):
        return _classification_record("redox", reaction)

    return _classification_record("other", reaction)


def _is_elemental(token: str) -> bool:
    """Heuristic: token is an elemental form (single element, diatomic, or metal)."""
    t = token.strip()
    elemental_names = {
        "Na",
        "K",
        "Ca",
        "Mg",
        "Fe",
        "Cu",
        "Zn",
        "Al",
        "H2",
        "O2",
        "N2",
        "Cl2",
        "F2",
        "Br2",
        "I2",
        "C",
        "P4",
        "S8",
        "[Na]",
        "[K]",
        "[Fe]",
        "[Cu]",
        "[Zn]",
    }
    if t in elemental_names:
        return True
    try:
        atoms = _species_atoms(t)
        return len(atoms) == 1
    except ValueError:
        return False


def _classification_record(kind: str, reaction: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "classification": kind,
        "reactants": list(reaction.get("reactants", [])),
        "products": list(reaction.get("products", [])),
    }


# ---------------------------------------------------------------------------
# compare_reactions
# ---------------------------------------------------------------------------


def compare_reactions(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Jaccard similarity between two reactions over their species sets."""
    set_a_r = set(a.get("reactants", []))
    set_a_p = set(a.get("products", []))
    set_b_r = set(b.get("reactants", []))
    set_b_p = set(b.get("products", []))

    def _jaccard(s1: set[str], s2: set[str]) -> float:
        union = s1 | s2
        if not union:
            return 1.0
        return len(s1 & s2) / len(union)

    jac_r = _jaccard(set_a_r, set_b_r)
    jac_p = _jaccard(set_a_p, set_b_p)
    similarity = 0.5 * (jac_r + jac_p)

    return {
        "schema_version": SCHEMA_VERSION,
        "similarity": similarity,
        "same_reactants": set_a_r == set_b_r,
        "same_products": set_a_p == set_b_p,
        "shared_reactants": sorted(set_a_r & set_b_r),
        "shared_products": sorted(set_a_p & set_b_p),
    }


__all__ = [
    "balance_reaction",
    "classify_reaction",
    "compare_reactions",
]
