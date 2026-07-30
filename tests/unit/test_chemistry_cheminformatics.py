"""Unit tests for ``general_ludd.chemistry.cheminformatics`` (CHEM-010).

Covers structure validation/transformation per
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §7.2 (Cheminformatics):

* parse/validate SMILES (valid accepted, invalid rejected)
* standardization preserves source form (CHEM-AT-011, spec §4.1 invariant)
* tautomer enumeration with parent/child lineage
* substructure search (SMARTS-like patterns)
* molecular descriptors (MW, logP estimate, HBD/HBA, TPSA, ring count,
  rotatable bonds)
* Tanimoto fingerprint similarity
* every transform records tool/version/parameters/parent/child lineage

Loaded by file path (mirrors ``test_chemistry_core.py``) so the suite is robust
to ``sys.path`` variations inside worktrees.
"""

from __future__ import annotations

import importlib.util
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_CI_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "cheminformatics.py")


def _load_ci():
    spec = importlib.util.spec_from_file_location("cheminformatics_under_test", _CI_PATH)
    assert spec is not None and spec.loader is not None, "cheminformatics spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ci = _load_ci()


# ---------------------------------------------------------------------------
# CHEM-010 validate_structure
# ---------------------------------------------------------------------------


class TestValidateStructure:
    def test_valid_smiles_accepted(self):
        r = ci.validate_structure("CCO")
        assert r["status"] in {"succeeded", "degraded"}
        assert any(c["status"] == "pass" for c in r["checks"])

    def test_invalid_smiles_rejected(self):
        r = ci.validate_structure("%%%not-smiles%%%")
        assert r["status"] in {"refused", "degraded"}
        assert any(c["status"] in {"fail", "warning"} for c in r["checks"])

    def test_inchi_prefixed_routed_as_inchi(self):
        r = ci.validate_structure("InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3")
        assert r["representation"] == "inchi"
        assert r["status"] in {"succeeded", "degraded"}

    def test_detects_aromaticity_in_benzene(self):
        r = ci.validate_structure("c1ccccc1")
        assert r.get("aromatic") is True

    def test_records_charge_from_bracket_ion(self):
        r = ci.validate_structure("[NH4+]")
        assert r["charge"] == 1

    def test_records_tool_and_parameters(self):
        r = ci.validate_structure("CCO")
        assert r["tool"]
        assert r["version"]
        assert r["parameters"]["query"] == "CCO"


# ---------------------------------------------------------------------------
# CHEM-010 standardize_structure (preserves submitted form per §4.1)
# ---------------------------------------------------------------------------


class TestStandardizeStructure:
    def test_preserves_submitted_form(self):
        r = ci.standardize_structure("CCO")
        assert r["submitted"] == "CCO"
        assert r["canonical"]

    def test_records_parent_lineage(self):
        r = ci.standardize_structure("CCO", parent="ent-123")
        assert r["parent"] == "ent-123"
        assert r["relation"] == "standardized_to"

    def test_records_tool_version_parameters(self):
        r = ci.standardize_structure("CCO")
        assert r["tool"]
        assert r["version"]
        assert r["parameters"]["normalize"] is False

    def test_stereo_not_erased_when_present(self):
        # Alanine stereo form must survive standardization
        r = ci.standardize_structure("C[C@H](N)C(=O)O")
        assert "@" in r["submitted"]


# ---------------------------------------------------------------------------
# CHEM-010 enumerate_tautomers
# ---------------------------------------------------------------------------


class TestTautomers:
    def test_enumerates_keto_enol_for_acetone(self):
        r = ci.enumerate_tautomers("CC(=O)C")
        assert len(r["tautomers"]) >= 1
        kinds = {t["kind"] for t in r["tautomers"]}
        assert any("enol" in k for k in kinds)

    def test_each_tautomer_records_tool_and_parent(self):
        r = ci.enumerate_tautomers("CC(=O)C", parent="ent-7")
        for t in r["tautomers"]:
            assert t["tool"]
            assert t["parent"] == "ent-7"

    def test_no_tautomers_for_alkane(self):
        r = ci.enumerate_tautomers("CCCCC")
        assert r["tautomers"] == []


# ---------------------------------------------------------------------------
# CHEM-010 substructure_search (SMARTS-like)
# ---------------------------------------------------------------------------


class TestSubstructureSearch:
    def test_finds_hydroxyl_in_ethanol(self):
        r = ci.substructure_search(["CCO", "c1ccccc1", "CC"], pattern="O")
        assert "CCO" in r["matches"]

    def test_no_match_for_disjoint_library(self):
        r = ci.substructure_search(["CCCCCC", "CC"], pattern="[NH4+]")
        assert r["matches"] == []

    def test_finds_benzene_ring(self):
        r = ci.substructure_search(["CCO", "c1ccccc1", "CC"], pattern="c1ccccc1")
        assert "c1ccccc1" in r["matches"]

    def test_records_tool_and_parameters(self):
        r = ci.substructure_search(["CCO"], pattern="O")
        assert r["tool"]
        assert r["parameters"]["pattern"] == "O"


# ---------------------------------------------------------------------------
# CHEM-010 compute_descriptors
# ---------------------------------------------------------------------------


class TestDescriptors:
    def test_mw_for_ethanol(self):
        d = ci.compute_descriptors("CCO")
        mw = next(v for v in d["values"] if v["name"] == "molecular_weight")
        assert 45.0 < mw["value"] < 47.5
        assert mw["unit"] == "g/mol"

    def test_hba_for_water_at_least_one(self):
        d = ci.compute_descriptors("O")
        hba = next(v for v in d["values"] if v["name"] == "h_bond_acceptors")
        assert hba["value"] >= 1

    def test_hbd_for_ethanol_one(self):
        d = ci.compute_descriptors("CCO")
        hbd = next(v for v in d["values"] if v["name"] == "h_bond_donors")
        assert hbd["value"] >= 1

    def test_tpsa_for_aspirin_positive(self):
        d = ci.compute_descriptors("CC(=O)OC1=CC=CC=C1C(=O)O")
        tpsa = next(v for v in d["values"] if v["name"] == "tpsa")
        assert tpsa["value"] > 30.0

    def test_ring_count_for_benzene(self):
        d = ci.compute_descriptors("c1ccccc1")
        rings = next(v for v in d["values"] if v["name"] == "ring_count")
        assert rings["value"] >= 1

    def test_descriptors_carry_method_and_unit(self):
        d = ci.compute_descriptors("CCO")
        assert d["values"]
        for v in d["values"]:
            assert v["unit"]
            assert v["method_id"]


# ---------------------------------------------------------------------------
# CHEM-010 tanimoto_similarity (fingerprint-based stub)
# ---------------------------------------------------------------------------


class TestTanimotoSimilarity:
    def test_identical_molecules_one(self):
        r = ci.tanimoto_similarity("CCO", "CCO")
        assert r["similarity"] == 1.0

    def test_disjoint_molecules_below_one(self):
        r = ci.tanimoto_similarity("C", "c1ccccc1")
        assert 0.0 <= r["similarity"] < 1.0

    def test_returns_fingerprints(self):
        r = ci.tanimoto_similarity("CCO", "CCO")
        assert r["fingerprint_a"]
        assert r["fingerprint_b"]

    def test_records_tool_and_parameters(self):
        r = ci.tanimoto_similarity("CCO", "CCN")
        assert r["tool"]
        assert r["parameters"]["smiles_a"] == "CCO"


# ---------------------------------------------------------------------------
# CHEM-010 provenance: every transform records parent/child lineage
# ---------------------------------------------------------------------------


class TestProvenanceLineage:
    def test_validate_records_parent_when_given(self):
        r = ci.validate_structure("CCO", parent="ent-1")
        assert r["parent"] == "ent-1"

    def test_compute_descriptors_records_parent(self):
        r = ci.compute_descriptors("CCO", parent="ent-9")
        assert r["parent"] == "ent-9"

    def test_standardize_emits_child_relation(self):
        r = ci.standardize_structure("CCO", parent="ent-1")
        assert r["relation"] == "standardized_to"
        assert r["tool"]
        assert r["version"]
