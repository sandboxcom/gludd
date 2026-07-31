"""Unit tests for ``tests/fixtures/chemistry_data.py``.

Verifies the small chemistry dataset fixtures are internally consistent and
match the registry shape required by
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §5. The tests cross-check fixture
formulas and molar masses against the parsing/atomic-weight logic in
``src/general_ludd/chemistry/core.py``.

Tests:

1. ``test_fixture_corpus_shape``       -- schema fields present
2. ``test_at_least_20_chemicals``      -- dataset minimum size
3. ``test_formulas_parse``             -- every Hill formula parses in core
4. ``test_formulas_match_smiles``      -- SMILES-derived formula matches stated
5. ``test_molar_masses_correct``       -- MW derived from atomic weights
6. ``test_cas_numbers_well_formed``    -- format + check digit
7. ``test_pubchem_cids_positive``      -- CIDs are positive integers
8. ``test_hazards_have_risk_tiers``    -- every hazard carries a tier
9. ``test_hazards_cover_minimum``      -- >=10 hazard records
10. ``test_incompatibilities_bidirectional`` -- pairs symmetric
11. ``test_at_least_10_incompatibilities``   -- dataset minimum
12. ``test_reactions_atom_balanced``   -- conservation per element
13. ``test_at_least_5_reactions``      -- dataset minimum
"""

from __future__ import annotations

import importlib.util
import os
import re

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_FIXTURE_PATH = os.path.join(_PROJECT_ROOT, "tests", "fixtures", "chemistry_data.py")
_CORE_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "core.py")


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"spec for {path} failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fixtures = _load_module("chemistry_fixtures_under_test", _FIXTURE_PATH)
core = _load_module("chemistry_core_under_test", _CORE_PATH)


# ---------------------------------------------------------------------------
# 1. Corpus shape
# ---------------------------------------------------------------------------


def test_fixture_corpus_shape():
    corpus = fixtures.all_fixture_records()
    assert corpus["schema_version"] == "1.0"
    assert corpus["source"].endswith("chemistry_data.py")
    assert corpus["license"] == "CC0-1.0"
    for attr in ("chemicals", "hazards", "incompatibilities", "reactions"):
        assert isinstance(corpus[attr], tuple) and len(corpus[attr]) > 0, attr


# ---------------------------------------------------------------------------
# 2-6. Chemical records
# ---------------------------------------------------------------------------


def test_at_least_20_chemicals():
    assert len(fixtures.CHEMICALS) >= 20


def test_formulas_parse():
    for c in fixtures.CHEMICALS:
        if not c.formula:
            continue
        parsed = core.parse_formula(c.formula)
        assert parsed, f"formula parsed empty for {c.key}: {c.formula}"


def test_formulas_match_smiles():
    """Fixture formulas must agree with core.py's known chemistry.

    Two cross-checks, applied per chemical:

    * NAME path -- when one of the chemical's names is registered in
      ``core.COMMON_NAMES``, the registered formula must match the fixture.
    * HEAVY-ATOM path -- for SMILES that carry explicit atoms in brackets
      (ionic salts like ``[Na+].[Cl-]``), the non-hydrogen atoms in the
      fixture formula must match what ``_strip_smiles_to_formula`` yields.

    Organic SMILES encode hydrogens implicitly, so a full formula cannot be
    derived from the SMILES alone without a valence model; the heavy-atom
    check is the strongest invariant ``core.py`` supports for those cases.
    """
    checked_name = 0
    checked_heavy = 0
    for c in fixtures.CHEMICALS:
        if not c.formula:
            continue
        # NAME path
        for nm in c.names:
            if nm.lower() in core.COMMON_NAMES:
                registered = core.COMMON_NAMES[nm.lower()]["formula"]
                assert registered.upper() == c.formula.upper(), (
                    f"{c.key}: COMMON_NAMES entry {nm!r} formula {registered!r} != stated {c.formula!r}"
                )
                checked_name += 1
                break
        # HEAVY-ATOM path (only meaningful for SMILES with bracket atoms)
        if c.smiles and "[" in c.smiles:
            derived = core._strip_smiles_to_formula(c.smiles)
            try:
                derived_counts = core.parse_formula(derived)
            except ValueError:
                continue
            stated_counts = core.parse_formula(c.formula)
            heavy_derived = {el: n for el, n in derived_counts.items() if el != "H"}
            heavy_stated = {el: n for el, n in stated_counts.items() if el != "H"}
            assert heavy_derived == heavy_stated, (
                f"{c.key}: heavy-atom mismatch for SMILES {c.smiles!r} derived={heavy_derived} stated={heavy_stated}"
            )
            checked_heavy += 1
    assert checked_name > 0, "no chemical aligned with COMMON_NAMES (name path broken)"
    assert checked_heavy > 0, "no bracket-SMILES chemical validated (heavy-atom path broken)"


def test_molar_masses_correct():
    tol = 0.05
    for c in fixtures.CHEMICALS:
        recomputed = core.molar_mass(c.formula)["value"]
        assert abs(recomputed - c.molar_mass) <= tol, (
            f"{c.key}: recomputed MW {recomputed:.3f} != stated {c.molar_mass:.3f}"
        )


_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def _cas_check_digit_valid(cas: str) -> bool:
    digits = cas.replace("-", "")
    if len(digits) < 5:
        return False
    check = int(digits[-1])
    total = sum(int(d) * (i + 1) for i, d in enumerate(reversed(digits[:-1])))
    return total % 10 == check


def test_cas_numbers_well_formed():
    for c in fixtures.CHEMICALS:
        assert _CAS_RE.match(c.cas), f"{c.key}: malformed CAS {c.cas!r}"
        assert _cas_check_digit_valid(c.cas), f"{c.key}: CAS {c.cas!r} failed check digit"


def test_pubchem_cids_positive():
    for c in fixtures.CHEMICALS:
        assert isinstance(c.pubchem_cid, int) and c.pubchem_cid > 0, f"{c.key}: invalid PubChem CID {c.pubchem_cid!r}"


# ---------------------------------------------------------------------------
# 7-9. Hazards
# ---------------------------------------------------------------------------


_VALID_TIERS = {"low", "moderate", "high", "prohibited"}


def test_hazards_have_risk_tiers():
    for h in fixtures.HAZARDS:
        assert h.risk_tier in _VALID_TIERS, f"{h.key}: bad tier {h.risk_tier!r}"
        for rating in (h.health, h.flammability, h.reactivity):
            assert 0 <= rating <= 4, f"{h.key}: rating {rating} out of NFPA 0..4 range"


def test_hazards_cover_minimum():
    assert len(fixtures.HAZARDS) >= 10


# ---------------------------------------------------------------------------
# 10-11. Incompatibilities
# ---------------------------------------------------------------------------


def test_incompatibilities_bidirectional():
    """If A→B exists, B→A must also exist (symmetric relationship)."""
    fwd = {(p.a, p.b) for p in fixtures.INCOMPATIBILITIES}
    for a, b in fwd:
        assert (b, a) in fwd, f"incompatibility not bidirectional: {a!r} <-> {b!r}"
    assert len(fwd) == len(fixtures.INCOMPATIBILITIES), "duplicate incompatibility rows"


def test_at_least_10_incompatibilities():
    unique_pairs = {(p.a, p.b) for p in fixtures.INCOMPATIBILITIES if p.a < p.b}
    assert len(unique_pairs) >= 10


# ---------------------------------------------------------------------------
# 12-13. Reactions
# ---------------------------------------------------------------------------


def _reaction_atoms(spec: tuple[tuple[str, int], ...]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for token, coeff in spec:
        atoms = core.parse_formula(token)
        for el, cnt in atoms.items():
            totals[el] = totals.get(el, 0) + cnt * coeff
    return totals


def test_reactions_atom_balanced():
    """Each reaction must conserve every element on both sides."""
    assert len(fixtures.REACTIONS) > 0
    for rxn in fixtures.REACTIONS:
        left = _reaction_atoms(rxn.reactants)
        right = _reaction_atoms(rxn.products)
        assert left == right, f"{rxn.key}: atom imbalance reactants={left} products={right}"


def test_at_least_5_reactions():
    assert len(fixtures.REACTIONS) >= 5
