"""Chemistry expert core — top 5 capabilities from the chemistry spec.

Implements a typed, evidence-grounded surface for CHEM-001 (router),
CHEM-002 (identity), CHEM-005 (reactions), CHEM-007 (stoichiometry), and
CHEM-008 (safety) so the ansible collection, CLI, and skill layer share one
deterministic service API. The functions return plain dict records that
mirror the schemas in ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §4; every
numerical result carries a unit and, where applicable, an uncertainty.

Design choices kept deliberately small for phase A:

* Formula parsing supports Hill notation with one level of nested parens
  (``Ca(OH)2``, ``(NH4)2SO4``). It is not a full molecular parser.
* Identity resolution consults a small knowledge base of common names plus
  marker-based SMILES interpretation (stereo ``@``, isotope ``[13C]``, salt
  disconnection ``.``, bracketed charge ``[NH4+]``). Canonicalization never
  erases the submitted representation.
* Reaction balancing brute-forces the smallest integer coefficient vector
  in ``1..MAX_BALANCE_COEFF`` — sufficient for textbook inorganic reactions
  and the spec acceptance fixtures, not for redox matrices.
* Safety screening maps known names/SMILES to hazard classes and applies an
  incompatibility matrix. Unknown entities return ``moderate`` with a
  ``missing-current-hazard-evidence`` limitation rather than a silent low.
"""

from __future__ import annotations

import math
import re
import uuid
from typing import Any

SCHEMA_VERSION = "1.0"
CANONICALIZER = "chemistry-core@0.1.0"
MAX_BALANCE_COEFF = 12
_HOMOELEMENTAL_TOKEN_RE = re.compile(r"^(?P<element>[A-Z][a-z]?)(?P=element)*$")
_STRUCTURAL_SMILES_MARKER_RE = re.compile(r"[\[\]@()/\\=#.]")

ATOMIC_WEIGHTS: dict[str, float] = {
    "H": 1.008,
    "He": 4.0026,
    "Li": 6.94,
    "Be": 9.0122,
    "B": 10.81,
    "C": 12.011,
    "N": 14.007,
    "O": 15.999,
    "F": 18.998,
    "Ne": 20.180,
    "Na": 22.990,
    "Mg": 24.305,
    "Al": 26.982,
    "Si": 28.085,
    "P": 30.974,
    "S": 32.06,
    "Cl": 35.45,
    "Ar": 39.948,
    "K": 39.098,
    "Ca": 40.078,
    "Ti": 47.867,
    "Cr": 51.996,
    "Mn": 54.938,
    "Fe": 55.845,
    "Co": 58.933,
    "Ni": 58.693,
    "Cu": 63.546,
    "Zn": 65.38,
    "As": 74.922,
    "Se": 78.971,
    "Br": 79.904,
    "Kr": 83.798,
    "Rb": 85.468,
    "Sr": 87.62,
    "Ag": 107.87,
    "Cd": 112.41,
    "Sn": 118.71,
    "I": 126.904,
    "Ba": 137.33,
    "Au": 196.97,
    "Hg": 200.59,
    "Pb": 207.2,
    "Bi": 208.98,
    "U": 238.03,
}

ATOMIC_WEIGHT_UNCERTAINTY: dict[str, float] = {
    "H": 0.0002,
    "C": 0.002,
    "N": 0.002,
    "O": 0.001,
    "Na": 0.002,
    "Cl": 0.01,
    "S": 0.02,
    "K": 0.001,
    "Ca": 0.004,
    "Fe": 0.003,
}

ELEMENT_RE = re.compile(r"^[A-Z][a-z]?$")


def _new_id() -> str:
    return str(uuid.uuid4())


def _err(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "retryable": retryable, "message": message}


def _name_record(value: str, kind: str = "preferred") -> dict[str, Any]:
    return {
        "value": value,
        "type": kind,
        "source_id": _new_id(),
    }


def _value_record(
    name: str,
    value: float,
    unit: str,
    uncertainty: float = 0.0,
    method_id: str = CANONICALIZER,
) -> dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "uncertainty": uncertainty,
        "method_id": method_id,
    }


# ---------------------------------------------------------------------------
# Formula parsing (shared by reactions + stoichiometry)
# ---------------------------------------------------------------------------


def parse_formula(formula: str) -> dict[str, int]:
    """Parse a Hill-notation formula into ``{element: atom_count}``.

    Supports one level of nested parentheses and multi-digit counts. Ionic
    charge suffixes (``+``/``-``) are ignored here; charge is handled by
    :func:`_extract_charge`. Raises ``ValueError`` on malformed input or an
    unknown element symbol.
    """
    cleaned = formula.strip()
    if not cleaned:
        raise ValueError("empty formula")

    cleaned = cleaned.rstrip("+-")
    cleaned = cleaned.replace("[", "").replace("]", "")

    stack: list[dict[str, int]] = [{}]
    i = 0
    n = len(cleaned)
    while i < n:
        ch = cleaned[i]
        if ch == "(":
            stack.append({})
            i += 1
            continue
        if ch == ")":
            if len(stack) < 2:
                raise ValueError(f"unbalanced ')' in formula: {formula}")
            top = stack.pop()
            i += 1
            j = i
            while j < n and cleaned[j].isdigit():
                j += 1
            mult = int(cleaned[i:j] or "1")
            for el, cnt in top.items():
                stack[-1][el] = stack[-1].get(el, 0) + cnt * mult
            i = j
            continue
        if ch.isupper():
            j = i + 1
            if j < n and cleaned[j].islower():
                j += 1
            element = cleaned[i:j]
            if not ELEMENT_RE.match(element) or element not in ATOMIC_WEIGHTS:
                raise ValueError(f"unknown element symbol: {element!r}")
            k = j
            while k < n and cleaned[k].isdigit():
                k += 1
            count = int(cleaned[j:k] or "1")
            stack[-1][element] = stack[-1].get(element, 0) + count
            i = k
            continue
        raise ValueError(f"unexpected character {ch!r} in formula: {formula}")

    if len(stack) != 1:
        raise ValueError(f"unbalanced '(' in formula: {formula}")
    return stack[0]


def _extract_charge(token: str) -> int:
    """Return net formal charge encoded in a formula/SMILES token.

    For SMILES (token contains ``[``) only bracketed ion notation such as
    ``[NH4+]`` or ``[O-]`` is counted. For formula notation, a trailing
    ``+``/``-`` (optionally with a count, e.g. ``Fe+3``) is recognized. This
    avoids double-counting the same sign via both paths.
    """
    if "[" in token:
        net = 0
        for inner in re.findall(r"\[([^\]]*)\]", token):
            m = re.search(r"([+-])(\d*)$", inner)
            if m:
                net += (1 if m.group(1) == "+" else -1) * (int(m.group(2)) if m.group(2) else 1)
        return net
    m = re.search(r"([+-])(\d*)$", token)
    if m:
        return (1 if m.group(1) == "+" else -1) * (int(m.group(2)) if m.group(2) else 1)
    return 0


# ---------------------------------------------------------------------------
# CHEM-002 chemical identity
# ---------------------------------------------------------------------------


COMMON_NAMES: dict[str, dict[str, str]] = {
    "water": {"smiles": "O", "formula": "H2O", "systematic": "oxidane"},
    "ice": {"smiles": "O", "formula": "H2O", "systematic": "oxidane"},
    "ethanol": {"smiles": "CCO", "formula": "C2H6O", "systematic": "ethanol"},
    "methanol": {"smiles": "CO", "formula": "CH4O", "systematic": "methanol"},
    "acetone": {
        "smiles": "CC(=O)C",
        "formula": "C3H6O",
        "systematic": "propan-2-one",
    },
    "benzene": {"smiles": "c1ccccc1", "formula": "C6H6", "systematic": "benzene"},
    "toluene": {
        "smiles": "Cc1ccccc1",
        "formula": "C7H8",
        "systematic": "methylbenzene",
    },
    "glucose": {
        "smiles": "OC[C@H]1OC(O)[C@H](O)[C@@H](O)[C@@H]1O",
        "formula": "C6H12O6",
        "systematic": "(2R,3S,4R,5R)-2,3,4,5,6-pentahydroxyhexanal",
    },
    "sucrose": {
        "smiles": "C(C1C(C(C(C(O1)OC2(C(C(C(O2)CO)O)O)CO)O)O)O)O",
        "formula": "C12H22O11",
        "systematic": "alpha-D-glucopyranosyl-(1->2)-beta-D-fructofuranoside",
    },
    "aspirin": {
        "smiles": "CC(=O)OC1=CC=CC=C1C(=O)O",
        "formula": "C9H8O4",
        "systematic": "2-acetoxybenzoic acid",
    },
    "acetylsalicylic acid": {
        "smiles": "CC(=O)OC1=CC=CC=C1C(=O)O",
        "formula": "C9H8O4",
        "systematic": "2-acetoxybenzoic acid",
    },
    "caffeine": {
        "smiles": "CN1C=NC2=C1C(=O)N(C(=O)N2C)C",
        "formula": "C8H10N4O2",
        "systematic": "1,3,7-trimethylxanthine",
    },
    "methane": {"smiles": "C", "formula": "CH4", "systematic": "methane"},
    "ethane": {"smiles": "CC", "formula": "C2H6", "systematic": "ethane"},
    "ammonia": {"smiles": "N", "formula": "NH3", "systematic": "azane"},
    "hydrogen": {"smiles": "[H][H]", "formula": "H2", "systematic": "dihydrogen"},
    "oxygen": {"smiles": "O=O", "formula": "O2", "systematic": "dioxygen"},
    "nitrogen": {"smiles": "N#N", "formula": "N2", "systematic": "dinitrogen"},
    "carbon dioxide": {"smiles": "O=C=O", "formula": "CO2", "systematic": "carbon dioxide"},
    "carbon monoxide": {"smiles": "[C-]#[O+]", "formula": "CO", "systematic": "carbon monoxide"},
    "sodium chloride": {"smiles": "[Na+].[Cl-]", "formula": "NaCl", "systematic": "sodium chloride"},
    "salt": {"smiles": "[Na+].[Cl-]", "formula": "NaCl", "systematic": "sodium chloride"},
    "NaCl": {"smiles": "[Na+].[Cl-]", "formula": "NaCl", "systematic": "sodium chloride"},
    "sodium hydroxide": {"smiles": "[Na+].[OH-]", "formula": "NaOH", "systematic": "sodium hydroxide"},
    "potassium hydroxide": {"smiles": "[K+].[OH-]", "formula": "KOH", "systematic": "potassium hydroxide"},
    "sulfuric acid": {"smiles": "OS(=O)(=O)O", "formula": "H2SO4", "systematic": "tetraoxosulfuric acid"},
    "hydrochloric acid": {"smiles": "Cl", "formula": "HCl", "systematic": "hydrogen chloride"},
    "nitric acid": {"smiles": "[N+](=O)(O)[O-]", "formula": "HNO3", "systematic": "nitric acid"},
    "acetic acid": {"smiles": "CC(=O)O", "formula": "C2H4O2", "systematic": "ethanoic acid"},
    "hydrogen peroxide": {"smiles": "OO", "formula": "H2O2", "systematic": "dioxidane"},
    "potassium permanganate": {
        "smiles": "[K+].[O-][Mn](=O)(=O)=O",
        "formula": "KMnO4",
        "systematic": "potassium tetraoxomanganate(VII)",
    },
    "potassium chlorate": {"smiles": "[K+].[O-][Cl](=O)=O", "formula": "KClO3", "systematic": "potassium chlorate"},
    "sodium metal": {"smiles": "[Na]", "formula": "Na", "systematic": "sodium"},
    "potassium metal": {"smiles": "[K]", "formula": "K", "systematic": "potassium"},
    "lithium aluminium hydride": {
        "smiles": "[Li+].[AlH4-]",
        "formula": "LiAlH4",
        "systematic": "lithium tetrahydridoaluminate",
    },
    "picric acid": {
        "smiles": "[O-][N+](=O)c1cc([N+](=O)[O-])c(N+)c([N+](=O)[O-])c1",
        "formula": "C6H3N3O7",
        "systematic": "2,4,6-trinitrophenol",
    },
    "tnt": {"smiles": "Cc1c(N(c1C)C)C", "formula": "C7H5N3O6", "systematic": "2,4,6-trinitrotoluene"},
    "nitroglycerin": {
        "smiles": "O[N+](=O)[O-].O[N+](=O)[O-].O[N+](=O)[O-]",
        "formula": "C3H5N3O9",
        "systematic": "propane-1,2,3-triyl trinitrate",
    },
    "hydrogen cyanide": {"smiles": "C#N", "formula": "HCN", "systematic": "formonitrile"},
    "phosgene": {"smiles": "O=C(Cl)Cl", "formula": "COCl2", "systematic": "carbonyl dichloride"},
    "white phosphorus": {"smiles": "P", "formula": "P4", "systematic": "tetraphosphorus"},
    "ether": {"smiles": "CCOCC", "formula": "C4H10O", "systematic": "diethyl ether"},
    "diethyl ether": {"smiles": "CCOCC", "formula": "C4H10O", "systematic": "diethyl ether"},
    "ethanoic acid": {"smiles": "CC(=O)O", "formula": "C2H4O2", "systematic": "ethanoic acid"},
}


def _looks_like_smiles(query: str) -> bool:
    return bool(re.search(r"[A-Z]+\d?|[\[\]@()/\\]|=|#|\.", query)) and (
        not query.strip().isalpha() or any(c.isdigit() for c in query)
    )


def _stereo_status(smiles: str) -> str:
    """Return a conservative stereo-evidence status for a SMILES token."""
    if "@" in smiles or "/" in smiles or "\\" in smiles:
        return "specified"
    if "=" in smiles:
        return "partial"
    return "unknown"


def _isotope_status(smiles: str) -> str:
    """Return isotope evidence without assuming ambiguous bare tokens are natural."""
    if re.search(r"\[\d+[A-Z]", smiles):
        return "specified"
    if _HOMOELEMENTAL_TOKEN_RE.fullmatch(smiles) or _STRUCTURAL_SMILES_MARKER_RE.search(smiles):
        return "natural"
    return "unknown"


def resolve_identity(query: dict[str, Any] | str) -> dict[str, Any]:
    """Resolve a chemical identity request (CHEM-002).

    ``query`` is either a dict with ``{"query": str}`` or a bare string. The
    submitted representation is never erased: stereochemistry, isotopes,
    salts/solvates, and mixtures remain as distinct records rather than being
    silently canonicalized away.
    """
    q = str(query.get("query", "")).strip() if isinstance(query, dict) else str(query).strip()

    validation: list[dict[str, str]] = []
    names: list[dict[str, Any]] = []
    structure_value = q
    representation = "smiles"
    formula = ""

    if not q:
        return _identity_failed(q, validation, reason="empty query")

    lower = q.lower()
    if lower in COMMON_NAMES:
        entry = COMMON_NAMES[lower]
        names = [
            _name_record(q, "preferred"),
            _name_record(entry["systematic"], "systematic"),
        ]
        structure_value = entry["smiles"]
        formula = entry["formula"]
        validation.append({"check": "name_lookup", "status": "pass"})
    else:
        try:
            parsed = parse_formula(_strip_smiles_to_formula(q))
            formula = _hill_formula(parsed)
            validation.append({"check": "formula_parse", "status": "pass"})
        except ValueError:
            validation.append(
                {"check": "formula_parse", "status": "warning", "note": "unrecognized name and unparsable formula"}
            )

    kind = "compound"
    components: list[dict[str, Any]] = []
    if "." in structure_value and not structure_value.startswith("O="):
        pieces = _split_smiles_components(structure_value)
        if len(pieces) > 1:
            kind = "mixture"
            for piece in pieces:
                components.append(
                    {
                        "entity_id": _new_id(),
                        "fraction": 1.0 / len(pieces),
                        "basis": "mole",
                        "uncertainty": 0.0,
                        "smiles": piece,
                    }
                )

    stereo = _stereo_status(structure_value)
    isotopes = _isotope_status(structure_value)
    charge = _extract_charge(structure_value)

    if formula:
        try:
            parse_formula(formula)
            validation.append({"check": "formula_round_trip", "status": "pass"})
        except ValueError:
            validation.append({"check": "formula_round_trip", "status": "fail"})
    else:
        validation.append({"check": "formula_round_trip", "status": "warning", "note": "no formula resolved"})

    if not names:
        names = [_name_record(q, "preferred")]

    return {
        "schema_version": SCHEMA_VERSION,
        "entity_id": _new_id(),
        "kind": kind,
        "names": names,
        "structure": {
            "representation": representation,
            "value": structure_value,
            "submitted": q,
            "canonicalizer": CANONICALIZER,
            "stereochemistry": stereo,
            "isotopes": isotopes,
            "charge": charge,
            "formula": formula,
        },
        "components": components,
        "identifiers": [{"scheme": "formula", "value": formula, "source_id": _new_id()}] if formula else [],
        "validation": validation,
    }


def _identity_failed(q: str, validation: list[dict[str, str]], reason: str) -> dict[str, Any]:
    validation.append({"check": "identity_resolution", "status": "fail", "note": reason})
    return {
        "schema_version": SCHEMA_VERSION,
        "entity_id": _new_id(),
        "kind": "compound",
        "names": [],
        "structure": {
            "representation": "unknown",
            "value": "",
            "submitted": q,
            "canonicalizer": CANONICALIZER,
            "stereochemistry": "unknown",
            "isotopes": "unknown",
            "charge": 0,
            "formula": "",
        },
        "components": [],
        "identifiers": [],
        "validation": validation,
    }


def _strip_smiles_to_formula(smiles: str) -> str:
    """Best-effort reduction of a SMILES token to a formula string.

    Drops bonds, parens, ring-closure digits, and bracket charge markers; the
    result is accepted by :func:`parse_formula` for the common cases the
    identity path needs. Anything exotic raises ``ValueError`` upstream.
    """
    out = smiles
    out = re.sub(r"\[[^\]]*\]", lambda m: _bracket_atom_to_symbol(m.group(0)), out)
    out = re.sub(r"[=#/()0-9\\.\-d]", "", out)
    out = re.sub(r"([A-Z])([a-z]?)(?=[A-Z])", r"\1\2", out)
    return out


def _bracket_atom_to_symbol(bracket: str) -> str:
    inner = bracket.strip("[]")
    m = re.match(r"\d*([A-Z][a-z]?)", inner)
    return m.group(1) if m else ""


def _split_smiles_components(smiles: str) -> list[str]:
    pieces: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in smiles:
        if ch == "[":
            depth += 1
            buf.append(ch)
        elif ch == "]":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "." and depth == 0:
            if buf:
                pieces.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
    if buf:
        pieces.append("".join(buf))
    return [p for p in pieces if p]


def _hill_formula(counts: dict[str, int]) -> str:
    """Render an atom-count dict as a Hill-notation formula string."""
    if "C" in counts:
        order = ["C"]
        if "H" in counts:
            order.append("H")
        order += sorted(el for el in counts if el not in {"C", "H"})
    else:
        order = sorted(counts)
    out = []
    for el in order:
        cnt = counts[el]
        out.append(el + (str(cnt) if cnt > 1 else ""))
    return "".join(out)


# ---------------------------------------------------------------------------
# CHEM-005 reaction reasoning
# ---------------------------------------------------------------------------


def _species_atoms(token: str) -> dict[str, int]:
    try:
        return parse_formula(token)
    except ValueError:
        return parse_formula(_strip_smiles_to_formula(token))


def analyze_reaction(reaction: dict[str, Any]) -> dict[str, Any]:
    """Balance and validate a reaction (CHEM-005).

    ``reaction`` has ``reactants`` and ``products`` as lists of formula/SMILES
    tokens. Returns coefficients (when an integer balance exists in the
    brute-force window), per-atom mass balance, charge balance, and a list of
    structured verification checks.
    """
    reactants = list(reaction.get("reactants", []))
    products = list(reaction.get("products", []))
    if not reactants or not products:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": _new_id(),
            "status": "refused",
            "summary": "reaction missing reactants or products",
            "coefficients": {"reactants": {}, "products": {}},
            "balanced": False,
            "verification": [{"check": "species_present", "status": "fail", "note": "both sides required"}],
            "errors": [_err("chem.empty_reaction", "empty reactants or products")],
        }

    reactant_atoms = [_species_atoms(t) for t in reactants]
    product_atoms = [_species_atoms(t) for t in products]
    elements = sorted(set().union(*reactant_atoms, *product_atoms))

    species = reactants + products
    matrix: list[list[int]] = []
    for el in elements:
        row = []
        for i, atoms in enumerate(reactant_atoms + product_atoms):
            sign = 1 if i < len(reactant_atoms) else -1
            row.append(sign * atoms.get(el, 0))
        matrix.append(row)

    coeffs = _solve_integer_balance(matrix, len(species))
    balanced = coeffs is not None
    coefficients: dict[str, dict[str, int]] = {"reactants": {}, "products": {}}
    if balanced and coeffs is not None:
        for i, token in enumerate(reactants):
            coefficients["reactants"][token] = coeffs[i]
        for j, token in enumerate(products):
            coefficients["products"][token] = coeffs[len(reactants) + j]

    reactant_mass = sum(_formula_mass(a) for a in reactant_atoms)
    product_mass = sum(_formula_mass(a) for a in product_atoms)
    mass_ok = math.isclose(reactant_mass, product_mass, rel_tol=1e-9) or (balanced and coeffs is not None)

    reactant_charge = sum(_extract_charge(t) for t in reactants)
    product_charge = sum(_extract_charge(t) for t in products)
    charge_ok = reactant_charge == product_charge

    element_ok = (
        all(
            sum(reactant_atoms[i].get(el, 0) for i in range(len(reactants)))
            == sum(product_atoms[j].get(el, 0) for j in range(len(products)))
            for el in elements
        )
        and set(_hill_formula({})).issubset(elements)
    ) or _all_atoms_conserved(reactant_atoms, product_atoms)

    verification = [
        {
            "check": "mass_balance",
            "status": "pass" if (mass_ok or balanced) else "fail",
            "artifact_uri": "",
        },
        {
            "check": "atom_balance",
            "status": "pass" if (element_ok or balanced) else "fail",
            "artifact_uri": "",
        },
        {
            "check": "charge_balance",
            "status": "pass" if charge_ok else "fail",
            "artifact_uri": "",
        },
    ]

    status = "succeeded" if balanced else "degraded"
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": _new_id(),
        "status": status,
        "summary": ("balanced integer stoichiometry found" if balanced else "no integer balance in search window"),
        "species": species,
        "elements": elements,
        "coefficients": coefficients,
        "balanced": balanced,
        "verification": verification,
        "errors": []
        if balanced
        else [
            _err(
                "chem.unbalanced",
                "could not find integer stoichiometric coefficients",
            )
        ],
    }


def _all_atoms_conserved(
    reactant_atoms: list[dict[str, int]],
    product_atoms: list[dict[str, int]],
) -> bool:
    left: dict[str, int] = {}
    for atoms in reactant_atoms:
        for el, cnt in atoms.items():
            left[el] = left.get(el, 0) + cnt
    right: dict[str, int] = {}
    for atoms in product_atoms:
        for el, cnt in atoms.items():
            right[el] = right.get(el, 0) + cnt
    return left == right


def _solve_integer_balance(matrix: list[list[int]], n_species: int) -> list[int] | None:
    """Brute-force the smallest positive integer coefficient vector.

    Searches compositions in increasing total coefficient up to
    :data:`MAX_BALANCE_COEFF` per species. Returns ``None`` when no vector in
    the window zeroes the residual.
    """
    for total in range(n_species, (MAX_BALANCE_COEFF * n_species) + 1):
        for cand in _compositions(total, n_species):
            residual = [sum(matrix[row][col] * cand[col] for col in range(n_species)) for row in range(len(matrix))]
            if all(r == 0 for r in residual):
                g = _gcd_list(list(cand))
                return [c // g for c in cand]
    return None


def _compositions(total: int, n: int) -> list[tuple[int, ...]]:
    """Return all ordered n-tuples of positive integers summing to ``total``."""
    if n == 1:
        return [(total,)] if total >= 1 else []
    out: list[tuple[int, ...]] = []
    for head in range(1, total - (n - 2)):
        for tail in _compositions(total - head, n - 1):
            out.append((head, *tail))
    return out


def _gcd_list(values: list[int]) -> int:
    from math import gcd

    g = values[0]
    for v in values[1:]:
        g = gcd(g, v)
    return g or 1


def _formula_mass(atoms: dict[str, int]) -> float:
    return sum(ATOMIC_WEIGHTS.get(el, 0.0) * cnt for el, cnt in atoms.items())


# ---------------------------------------------------------------------------
# CHEM-007 stoichiometry
# ---------------------------------------------------------------------------


def molar_mass(formula: str) -> dict[str, Any]:
    """Return molar mass (g/mol) with propagated atomic-weight uncertainty."""
    atoms = parse_formula(formula)
    mass = 0.0
    unc_sq = 0.0
    for el, cnt in atoms.items():
        weight = ATOMIC_WEIGHTS[el]
        el_unc = ATOMIC_WEIGHT_UNCERTAINTY.get(el, 0.001)
        mass += weight * cnt
        unc_sq += (el_unc * cnt) ** 2
    return _value_record(
        "molar_mass",
        mass,
        "g/mol",
        uncertainty=math.sqrt(unc_sq),
    )


def stoichiometry_moles(
    mass_g: float,
    formula: str,
    mass_uncertainty: float = 0.0,
) -> dict[str, Any]:
    """Convert a measured mass to amount of substance (mol)."""
    mm = molar_mass(formula)
    moles = mass_g / mm["value"]
    mm_unc = mm["uncertainty"] if mm["uncertainty"] else 0.0
    rel_x = (mass_uncertainty / mass_g) if mass_g else 0.0
    rel_y = (mm_unc / mm["value"]) if mm["value"] else 0.0
    rel = math.sqrt(rel_x**2 + rel_y**2)
    return _value_record(
        "amount_substance",
        moles,
        "mol",
        uncertainty=moles * rel if (mass_uncertainty or mm_unc) else 0.0,
    )


def stoichiometry_dilution(
    c1: float | None,
    v1: float | None,
    c2: float | None,
    v2: float | None,
) -> dict[str, Any]:
    """Solve ``M1*V1 = M2*V2`` for the missing variable.

    Exactly one of the four arguments must be ``None``; concentrations use any
    consistent unit (typically mol/L) and volumes likewise.
    """
    given = [x for x in (c1, v1, c2, v2) if x is not None]
    if len(given) != 3:
        raise ValueError("exactly one of c1, v1, c2, v2 must be None for dilution")
    result: dict[str, Any] = {}
    if c1 is None:
        assert v1 is not None and c2 is not None and v2 is not None
        result["c1"] = (c2 * v2) / v1
        result["unit_c"] = "mol/L"
    if v1 is None:
        assert c1 is not None and c2 is not None and v2 is not None
        result["v1"] = (c2 * v2) / c1
        result["unit_v"] = "L"
    if c2 is None:
        assert c1 is not None and v1 is not None and v2 is not None
        result["c2"] = (c1 * v1) / v2
        result["unit_c"] = "mol/L"
    if v2 is None:
        assert c1 is not None and v1 is not None and c2 is not None
        result["v2"] = (c1 * v1) / c2
        result["unit_v"] = "L"
    return result


def stoichiometry_yield(
    actual_g: float,
    theoretical_g: float,
    actual_unc: float = 0.0,
    theoretical_unc: float = 0.0,
) -> dict[str, Any]:
    """Return percent yield ``100 * actual / theoretical`` with uncertainty."""
    if theoretical_g <= 0:
        raise ValueError("theoretical yield must be positive")
    pct = 100.0 * actual_g / theoretical_g
    rel = 0.0
    if actual_unc or theoretical_unc:
        rel_x = (actual_unc / actual_g) if actual_g else 0.0
        rel_y = (theoretical_unc / theoretical_g) if theoretical_g else 0.0
        rel = math.sqrt(rel_x**2 + rel_y**2)
    return _value_record(
        "yield",
        pct,
        "percent",
        uncertainty=pct * rel if (actual_unc or theoretical_unc) else 0.0,
    )


# ---------------------------------------------------------------------------
# CHEM-008 safety and compatibility
# ---------------------------------------------------------------------------


HAZARD_REGISTRY: dict[str, dict[str, Any]] = {
    "water": {"classes": [], "tier": "low", "controls": []},
    "ice": {"classes": [], "tier": "low", "controls": []},
    "sodium chloride": {"classes": [], "tier": "low", "controls": []},
    "salt": {"classes": [], "tier": "low", "controls": []},
    "NaCl": {"classes": [], "tier": "low", "controls": []},
    "glucose": {"classes": [], "tier": "low", "controls": []},
    "sucrose": {"classes": [], "tier": "low", "controls": []},
    "ethanol": {
        "classes": ["flammable"],
        "tier": "moderate",
        "controls": ["ventilation", "no_ignition_sources"],
    },
    "methanol": {
        "classes": ["flammable", "acute_toxic"],
        "tier": "moderate",
        "controls": ["ventilation", "no_ignition_sources", "gloves"],
    },
    "acetone": {
        "classes": ["flammable", "irritant"],
        "tier": "moderate",
        "controls": ["ventilation", "no_ignition_sources"],
    },
    "diethyl ether": {
        "classes": ["flammable", "peroxide_former"],
        "tier": "high",
        "controls": ["ventilation", "no_ignition_sources", "peroxide_test"],
    },
    "ether": {
        "classes": ["flammable", "peroxide_former"],
        "tier": "high",
        "controls": ["ventilation", "no_ignition_sources", "peroxide_test"],
    },
    "benzene": {
        "classes": ["flammable", "carcinogen"],
        "tier": "high",
        "controls": ["ventilation", "gloves", "carcinogen_plan"],
    },
    "sulfuric acid": {
        "classes": ["corrosive_strong_acid", "oxidizer"],
        "tier": "high",
        "controls": ["acid_PPE", "emergency_shower", "slow_add_to_water"],
    },
    "H2SO4": {
        "classes": ["corrosive_strong_acid", "oxidizer"],
        "tier": "high",
        "controls": ["acid_PPE", "emergency_shower", "slow_add_to_water"],
    },
    "hydrochloric acid": {
        "classes": ["corrosive_strong_acid"],
        "tier": "moderate",
        "controls": ["acid_PPE", "ventilation"],
    },
    "nitric acid": {
        "classes": ["corrosive_strong_acid", "oxidizer"],
        "tier": "high",
        "controls": ["acid_PPE", "ventilation", "slow_add_to_water"],
    },
    "sodium hydroxide": {
        "classes": ["strong_base", "corrosive"],
        "tier": "moderate",
        "controls": ["base_PPE", "emergency_shower"],
    },
    "potassium hydroxide": {
        "classes": ["strong_base", "corrosive"],
        "tier": "moderate",
        "controls": ["base_PPE", "emergency_shower"],
    },
    "hydrogen peroxide": {
        "classes": ["oxidizer"],
        "tier": "high",
        "controls": ["ventilation", "no_organics"],
    },
    "potassium permanganate": {
        "classes": ["oxidizer"],
        "tier": "high",
        "controls": ["no_organics", "ventilation"],
    },
    "potassium chlorate": {
        "classes": ["oxidizer", "explosive_sensitizer"],
        "tier": "high",
        "controls": ["no_organics", "grounding"],
    },
    "picric acid": {
        "classes": ["explosive", "corrosive_strong_acid"],
        "tier": "prohibited",
        "controls": ["blast_shield", "permits", "hydrated_storage"],
    },
    "tnt": {
        "classes": ["explosive"],
        "tier": "prohibited",
        "controls": ["blast_shield", "permits"],
    },
    "nitroglycerin": {
        "classes": ["explosive", "shock_sensitive"],
        "tier": "prohibited",
        "controls": ["blast_shield", "permits", "no_transport"],
    },
    "white phosphorus": {
        "classes": ["pyrophoric", "acute_toxic"],
        "tier": "prohibited",
        "controls": ["inert_atmosphere", "under_water_storage"],
    },
    "sodium metal": {
        "classes": ["water_reactive", "corrosive"],
        "tier": "high",
        "controls": ["inert_atmosphere", "no_water", "class_D_extinguisher"],
    },
    "potassium metal": {
        "classes": ["water_reactive", "corrosive"],
        "tier": "high",
        "controls": ["inert_atmosphere", "no_water", "class_D_extinguisher"],
    },
    "lithium aluminium hydride": {
        "classes": ["water_reactive", "pyrophoric"],
        "tier": "high",
        "controls": ["inert_atmosphere", "no_water"],
    },
    "hydrogen cyanide": {
        "classes": ["acute_toxic"],
        "tier": "prohibited",
        "controls": ["ventilation", "cyanide_kit", "two_person_rule"],
    },
    "phosgene": {
        "classes": ["acute_toxic"],
        "tier": "prohibited",
        "controls": ["ventilation", "two_person_rule"],
    },
}

INCOMPATIBILITY_MATRIX: dict[frozenset[str], tuple[str, str]] = {
    frozenset({"oxidizer", "flammable"}): (
        "oxidizer_flammable",
        "prohibited",
    ),
    frozenset({"oxidizer", "peroxide_former"}): (
        "oxidizer_peroxide",
        "prohibited",
    ),
    frozenset({"corrosive_strong_acid", "strong_base"}): (
        "acid_base_exotherm",
        "moderate",
    ),
    frozenset({"water_reactive", "aqueous"}): (
        "water_reactive_water",
        "prohibited",
    ),
    frozenset({"pyrophoric", "air"}): (
        "pyrophoric_air",
        "high",
    ),
}

_TIER_RANK = {"low": 0, "moderate": 1, "high": 2, "prohibited": 3}
_RANK_TIER = {v: k for k, v in _TIER_RANK.items()}


def _resolve_hazard_entry(query: dict[str, Any] | str) -> dict[str, Any] | None:
    q = query.get("query") if isinstance(query, dict) else str(query)
    if q is None:
        return None
    for key in (q, q.lower()):
        if key in HAZARD_REGISTRY:
            return HAZARD_REGISTRY[key]
    identity = resolve_identity({"query": q})
    formula = identity["structure"].get("formula", "")
    if formula in HAZARD_REGISTRY:
        return HAZARD_REGISTRY[formula]
    return None


def screen_hazards(request: dict[str, Any] | str) -> dict[str, Any]:
    """Classify risk, flag incompatibilities, and list required controls.

    ``request`` is either a dict (``{"query": str}`` or
    ``{"entities": [str, ...]}``) or a bare string. Unknown entities resolve
    to ``moderate`` with a ``missing-current-hazard-evidence`` limitation per
    spec §9 row "Missing current hazard or incompatibility evidence".
    """
    if isinstance(request, str):
        entities = [request]
    else:
        entities = request.get("entities") or ([request.get("query")] if request.get("query") else [])
    facility_controls = (request.get("facility_controls") if isinstance(request, dict) else None) or []

    per_entity: list[dict[str, Any]] = []
    classes_seen: set[str] = set()
    required_controls: list[str] = []
    tier = 0
    limitations: list[str] = []

    for ent in entities:
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
            tier = max(tier, _TIER_RANK["moderate"])
            continue
        per_entity.append(
            {
                "query": ent,
                "classes": entry["classes"],
                "tier": entry["tier"],
                "controls": entry["controls"],
            }
        )
        classes_seen.update(entry["classes"])
        required_controls.extend(entry["controls"])
        tier = max(tier, _TIER_RANK[entry["tier"]])

    incompatibilities: list[dict[str, Any]] = []
    for pair, (kind, severity) in INCOMPATIBILITY_MATRIX.items():
        if pair.issubset(classes_seen):
            incompatibilities.append(
                {
                    "kind": kind,
                    "classes": sorted(pair),
                    "severity": severity,
                }
            )
            tier = max(tier, _TIER_RANK[severity])

    risk_tier = _RANK_TIER[tier]
    required_controls = sorted(set(required_controls))
    missing_controls = [c for c in required_controls if c not in facility_controls]

    if missing_controls and risk_tier in {"high", "prohibited"}:
        limitations.append("facility-lacks-required-control: " + ", ".join(missing_controls))
        if risk_tier == "prohibited":
            limitations.append(
                "policy: actionable output refused until facility controls and qualified approval are present"
            )

    safety = {
        "risk_tier": risk_tier,
        "review_id": _new_id(),
        "approvals": [] if risk_tier in {"high", "prohibited"} else [_new_id()],
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "succeeded" if risk_tier in {"low", "moderate"} else "refused",
        "risk_tier": risk_tier,
        "hazard_classes": sorted(classes_seen),
        "incompatibilities": incompatibilities,
        "per_entity": per_entity,
        "required_controls": required_controls,
        "missing_controls": missing_controls,
        "limitations": limitations,
        "safety": safety,
        "errors": []
        if risk_tier in {"low", "moderate"}
        else [
            _err(
                "chem.high_risk_uncontrolled",
                "hazard tier exceeds facility controls",
            )
        ],
    }


# ---------------------------------------------------------------------------
# CHEM-001 expert router
# ---------------------------------------------------------------------------


TASK_CAPABILITY: dict[str, str] = {
    "identity": "identity_resolve",
    "research": "chemistry_research",
    "property": "property_lookup",
    "reaction": "reaction_analyze",
    "protocol": "protocol_draft",
    "stoichiometry": "stoichiometry",
    "hazard": "hazard_review",
    "inventory": "inventory_check",
    "compute": "quantum_workflow",
    "spectra": "spectra_analyze",
    "analytical": "analytical_validate",
    "electrochemistry": "electrochemistry",
    "process": "process_scaleup",
}


def route_chemistry_task(request: dict[str, Any]) -> dict[str, Any]:
    """Route a chemistry request (CHEM-001).

    Maps ``task`` to the owning capability, screens entities for risk, and
    signals whether ``hazard_review`` must run before any actionable artifact
    is produced. Unknown / missing tasks return ``refused``.
    """
    task = request.get("task")
    if not task:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "refused",
            "capability": None,
            "requires_hazard_review": False,
            "risk_tier": "low",
            "errors": [_err("chem.missing_task", "no task field provided")],
        }
    capability = TASK_CAPABILITY.get(str(task))
    if capability is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "refused",
            "capability": None,
            "requires_hazard_review": False,
            "risk_tier": "low",
            "errors": [
                _err(
                    "chem.unknown_task",
                    f"no capability registered for task '{task}'",
                )
            ],
        }

    entities = request.get("entities") or []
    worst_tier = 0
    requires_hazard = False
    for ent in entities:
        screen = screen_hazards(ent)
        ent_tier = _TIER_RANK.get(screen["risk_tier"], 0)
        worst_tier = max(worst_tier, ent_tier)
        if ent_tier >= _TIER_RANK["high"]:
            requires_hazard = True

    if (
        capability
        in {
            "protocol_draft",
            "quantum_workflow",
            "molecular_simulation",
            "process_scaleup",
        }
        and worst_tier >= _TIER_RANK["moderate"]
    ):
        requires_hazard = True

    risk_tier = _RANK_TIER[worst_tier]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "succeeded",
        "capability": capability,
        "requires_hazard_review": requires_hazard,
        "risk_tier": risk_tier,
        "errors": [],
    }


__all__ = [
    "ATOMIC_WEIGHTS",
    "COMMON_NAMES",
    "HAZARD_REGISTRY",
    "INCOMPATIBILITY_MATRIX",
    "TASK_CAPABILITY",
    "analyze_reaction",
    "molar_mass",
    "parse_formula",
    "resolve_identity",
    "route_chemistry_task",
    "screen_hazards",
    "stoichiometry_dilution",
    "stoichiometry_moles",
    "stoichiometry_yield",
]
