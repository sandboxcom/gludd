"""Deep tests for ``general_ludd.chemistry.cheminformatics`` — edge cases,
boundary conditions, internal helpers, and error paths.

Covers the 585-line module beyond the 233-line existing test. Adds: empty/garbage
queries, dict-input paths, normalize=True refusal, fingerprint feature variety,
descriptor edge cases (unknown formulas, zero heteroatoms, ring-free structures),
substructure-search edge cases (empty pattern, bracket matching, empty library),
tautomer empty/dict paths, and internal helper verification.
"""

from __future__ import annotations

import importlib.util
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_CI_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "cheminformatics.py")


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"{name} spec failed"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ci = _load(_CI_PATH, "chemistry_ci_deep_under_test")


# ---------------------------------------------------------------------------
# validate_structure — edge cases
# ---------------------------------------------------------------------------


class TestValidateStructureEdgeCases:
    def test_empty_string_refused(self):
        r = ci.validate_structure("")
        assert r["status"] == "refused"
        assert any("empty_query" in e["code"] for e in r["errors"])

    def test_whitespace_only_refused(self):
        r = ci.validate_structure("   ")
        assert r["status"] == "refused"
        assert any("empty_query" in e["code"] for e in r["errors"])

    def test_garbage_characters_percent_sign_refused(self):
        r = ci.validate_structure("CCO%bad")
        assert r["status"] == "refused"
        assert any("garbage_input" in e["code"] for e in r["errors"])

    def test_garbage_characters_question_mark_refused(self):
        r = ci.validate_structure("CC?O")
        assert r["status"] == "refused"

    def test_garbage_characters_ampersand_refused(self):
        r = ci.validate_structure("CC&O")
        assert r["status"] == "refused"

    def test_dict_input_accepted(self):
        r = ci.validate_structure({"query": "CCO"})
        assert r["status"] == "succeeded"
        assert r["submitted"] == "CCO"

    def test_dict_input_with_no_query_key(self):
        r = ci.validate_structure({"foo": "bar"})
        assert r["status"] == "refused"

    def test_dict_input_with_whitespace_trimmed(self):
        r = ci.validate_structure({"query": "  CCO  "})
        assert r["submitted"] == "CCO"
        assert r["status"] == "succeeded"

    def test_garbage_chars_cause_parse_check_fail(self):
        r = ci.validate_structure("CCO%%bad")
        assert r["status"] == "refused"
        assert any(c["check"] == "parse" and c["status"] == "fail" for c in r["checks"])

    def test_formula_round_trip_check_present(self):
        r = ci.validate_structure("CCO")
        check_names = {c["check"] for c in r["checks"]}
        assert "formula_round_trip" in check_names

    def test_charge_zero_for_neutral(self):
        r = ci.validate_structure("CCCC")
        assert r["charge"] == 0

    def test_non_aromatic_molecule(self):
        r = ci.validate_structure("CCCCCC")
        assert r["aromatic"] is False

    def test_warnings_populated_on_formula_issue(self):
        r = ci.validate_structure("%%")
        assert r["status"] in ("refused", "degraded")
        assert "errors" in r

    def test_run_id_is_unique(self):
        id1 = ci.validate_structure("CCO")["run_id"]
        id2 = ci.validate_structure("CCO")["run_id"]
        assert id1 != id2

    def test_schema_version_always_present(self):
        r = ci.validate_structure("C")
        assert r["schema_version"] == ci.SCHEMA_VERSION

    def test_tool_and_version_always_present(self):
        r = ci.validate_structure("C")
        assert r["tool"] == ci.TOOL
        assert r["version"] == ci.VERSION


# ---------------------------------------------------------------------------
# standardize_structure — edge cases
# ---------------------------------------------------------------------------


class TestStandardizeStructureEdgeCases:
    def test_empty_query_refused(self):
        r = ci.standardize_structure("")
        assert r["status"] == "refused"
        assert r["canonical"] == ""
        assert any("empty_query" in e["code"] for e in r["errors"])

    def test_whitespace_only_refused(self):
        r = ci.standardize_structure("   ")
        assert r["status"] == "refused"

    def test_dict_input_accepted(self):
        r = ci.standardize_structure({"query": "CCO"})
        assert r["submitted"] == "CCO"
        assert r["status"] == "succeeded"

    def test_normalize_true_adds_warning(self):
        r = ci.standardize_structure("CCO", normalize=True)
        assert r["status"] == "succeeded"
        assert any("normalize" in w.lower() for w in r["warnings"])

    def test_charge_preserved(self):
        r = ci.standardize_structure("[NH4+]")
        assert r["charge"] == 1

    def test_stereochemistry_preserved(self):
        r = ci.standardize_structure("C[C@H](N)C(=O)O")
        assert r["stereochemistry"] == "specified"

    def test_isotopes_preserved_when_present(self):
        r = ci.standardize_structure("[13C]")
        assert r["isotopes"] == "specified"

    def test_parameters_record_normalize_flag(self):
        r = ci.standardize_structure("CCO", normalize=False)
        assert r["parameters"]["normalize"] is False
        r2 = ci.standardize_structure("CCO", normalize=True)
        assert r2["parameters"]["normalize"] is True

    def test_relation_is_standardized_to(self):
        r = ci.standardize_structure("CCO")
        assert r["relation"] == "standardized_to"


# ---------------------------------------------------------------------------
# enumerate_tautomers — edge cases
# ---------------------------------------------------------------------------


class TestTautomersEdgeCases:
    def test_empty_query_refused(self):
        r = ci.enumerate_tautomers("")
        assert r["status"] == "refused"
        assert r["tautomers"] == []

    def test_whitespace_only_refused(self):
        r = ci.enumerate_tautomers("   ")
        assert r["status"] == "refused"

    def test_dict_input_accepted(self):
        r = ci.enumerate_tautomers({"query": "CC(=O)C"})
        assert len(r["tautomers"]) >= 1

    def test_acetone_tautomer_relation(self):
        r = ci.enumerate_tautomers("CC(=O)C")
        for t in r["tautomers"]:
            assert t["relation"] == "tautomer_of"
            assert t["kind"] == "keto_to_enol"

    def test_carboxylic_acid_no_keto_enol_on_carboxyl(self):
        r = ci.enumerate_tautomers("CC(=O)O")
        assert r["tautomers"] == []

    def test_warnings_when_no_tautomers_found(self):
        r = ci.enumerate_tautomers("CCCCCC")
        assert any("no keto-enol" in w.lower() for w in r["warnings"])

    def test_parameters_record_classes(self):
        r = ci.enumerate_tautomers("CC(=O)C")
        assert "classes" in r["parameters"]
        assert "keto_enol" in r["parameters"]["classes"]


# ---------------------------------------------------------------------------
# substructure_search — edge cases
# ---------------------------------------------------------------------------


class TestSubstructureSearchEdgeCases:
    def test_empty_pattern_returns_empty(self):
        r = ci.substructure_search(["CCO", "CCN"], "")
        assert r["matches"] == []

    def test_empty_library_returns_empty(self):
        r = ci.substructure_search([], "O")
        assert r["matches"] == []
        assert r["library_size"] == 0

    def test_bracket_pattern_exact_match(self):
        r = ci.substructure_search(["[NH4+]", "CCN", "CCO"], "[NH4+]")
        assert "[NH4+]" in r["matches"]
        assert len(r["matches"]) == 1

    def test_bracket_pattern_no_match(self):
        r = ci.substructure_search(["CCO", "CCC"], "[Fe]")
        assert r["matches"] == []

    def test_single_atom_subsequence_match(self):
        r = ci.substructure_search(["CCO", "CCN", "CCC"], "O")
        assert "CCO" in r["matches"]
        assert "CCC" not in r["matches"]
        assert "CCN" not in r["matches"]

    def test_subsequence_ordering_matters(self):
        r = ci.substructure_search(["CCO", "OCC"], "CO")
        assert "CCO" in r["matches"]
        assert "OCC" not in r["matches"]

    def test_multi_atom_pattern(self):
        r = ci.substructure_search(["CC(=O)O", "CCCC"], "C(=O)O")
        assert "CC(=O)O" in r["matches"]

    def test_library_size_matches_input(self):
        lib = ["A"] * 10
        r = ci.substructure_search(lib, "B")
        assert r["library_size"] == 10


# ---------------------------------------------------------------------------
# compute_descriptors — edge cases
# ---------------------------------------------------------------------------


class TestDescriptorsEdgeCases:
    def test_empty_query_refused(self):
        r = ci.compute_descriptors("")
        assert r["status"] == "refused"
        assert r["values"] == []

    def test_dict_input_accepted(self):
        r = ci.compute_descriptors({"query": "CCO"})
        assert any(v["name"] == "molecular_weight" for v in r["values"])

    def test_methane_descriptors(self):
        d = ci.compute_descriptors("C")
        mw = next(v for v in d["values"] if v["name"] == "molecular_weight")
        assert mw["value"] > 12.0 and mw["value"] < 17.0
        rings = next(v for v in d["values"] if v["name"] == "ring_count")
        assert rings["value"] == 0
        rotatable = next(v for v in d["values"] if v["name"] == "rotatable_bonds")
        assert rotatable["value"] == 0

    def test_zero_heteroatoms_values(self):
        d = ci.compute_descriptors("CCCCCC")
        hba = next(v for v in d["values"] if v["name"] == "h_bond_acceptors")
        assert hba["value"] == 0
        tpsa = next(v for v in d["values"] if v["name"] == "tpsa")
        assert tpsa["value"] == 0.0

    def test_no_ring_structure(self):
        d = ci.compute_descriptors("CCO")
        rings = next(v for v in d["values"] if v["name"] == "ring_count")
        assert rings["value"] == 0

    def test_benzene_ring_count(self):
        d = ci.compute_descriptors("c1ccccc1")
        rings = next(v for v in d["values"] if v["name"] == "ring_count")
        assert rings["value"] == 1

    def test_rotatable_bonds_ethane(self):
        d = ci.compute_descriptors("CC")
        rotatable = next(v for v in d["values"] if v["name"] == "rotatable_bonds")
        assert rotatable["value"] >= 0

    def test_logp_positive_for_alkane(self):
        d = ci.compute_descriptors("CCCCCC")
        logp = next(v for v in d["values"] if v["name"] == "clogp")
        assert logp["value"] > 0

    def test_logp_negative_for_highly_polar(self):
        d = ci.compute_descriptors("O=CO")
        logp = next(v for v in d["values"] if v["name"] == "clogp")
        assert logp["value"] < 0

    def test_all_seven_descriptors_present(self):
        d = ci.compute_descriptors("CCO")
        names = {v["name"] for v in d["values"]}
        assert names == {
            "molecular_weight",
            "h_bond_donors",
            "h_bond_acceptors",
            "tpsa",
            "ring_count",
            "rotatable_bonds",
            "clogp",
        }

    def test_status_succeeded_when_formula_known(self):
        d = ci.compute_descriptors("CCO")
        assert d["status"] == "succeeded"

    def test_formula_field_present(self):
        d = ci.compute_descriptors("CCO")
        assert "formula" in d
        assert d["formula"]

    def test_warnings_include_no_formula_for_unknown(self):
        d = ci.compute_descriptors("made-up-smiles-string")
        assert d["status"] in ("degraded", "refused")
        warnings_text = " ".join(d["warnings"]).lower()
        errors_text = " ".join(e.get("code", "") for e in d.get("errors", []))
        assert "formula" in warnings_text or "empty_query" in errors_text or "garbage_input" in errors_text

    def test_tpsa_for_molecule_with_only_nitrogen(self):
        d = ci.compute_descriptors("[NH4+]")
        tpsa = next(v for v in d["values"] if v["name"] == "tpsa")
        assert tpsa["value"] >= 0

    def test_ring_count_large_polycyclic(self):
        d = ci.compute_descriptors("c1cc2cc3cc4cc5cc6cc1")
        rings = next(v for v in d["values"] if v["name"] == "ring_count")
        assert rings["value"] >= 1

    def test_parameter_record(self):
        d = ci.compute_descriptors("CCO")
        assert d["parameters"]["query"] == "CCO"


# ---------------------------------------------------------------------------
# tanimoto_similarity — edge cases
# ---------------------------------------------------------------------------


class TestTanimotoSimilarityEdgeCases:
    def test_empty_strings_return_one(self):
        r = ci.tanimoto_similarity("", "")
        assert r["similarity"] == 1.0

    def test_one_empty_returns_zero(self):
        r = ci.tanimoto_similarity("CCO", "")
        assert r["similarity"] == 0.0

    def test_fingerprint_features_include_atoms(self):
        r = ci.tanimoto_similarity("CCO", "CCN")
        assert any("atom:" in f for f in r["fingerprint_a"])

    def test_fingerprint_features_include_bigrams(self):
        r = ci.tanimoto_similarity("CCO", "CCN")
        assert any(f.startswith("bi:") for f in r["fingerprint_a"])

    def test_double_bond_includes_bond_double(self):
        r = ci.tanimoto_similarity("C=C", "CCO")
        assert "bond:double" in r["fingerprint_a"]

    def test_triple_bond_includes_bond_triple(self):
        r = ci.tanimoto_similarity("C#C", "CCO")
        assert "bond:triple" in r["fingerprint_a"]

    def test_ring_structure_includes_ring_count(self):
        r = ci.tanimoto_similarity("c1ccccc1", "CCO")
        assert any(f.startswith("ring_count:") for f in r["fingerprint_a"])

    def test_heteroatom_includes_hetero_count(self):
        r = ci.tanimoto_similarity("CCO", "CCCCCC")
        assert any(f.startswith("hetero_count:") for f in r["fingerprint_a"])

    def test_partial_overlap_similarity(self):
        r = ci.tanimoto_similarity("CCO", "CCN")
        # both share CC bi: and similar atoms, so similarity > 0
        assert r["similarity"] > 0.0
        assert r["similarity"] < 1.0

    def test_similarity_is_float(self):
        r = ci.tanimoto_similarity("CCO", "CCC")
        assert isinstance(r["similarity"], float)

    def test_fingerprints_are_sorted_lists(self):
        r = ci.tanimoto_similarity("CCO", "CCN")
        assert r["fingerprint_a"] == sorted(r["fingerprint_a"])
        assert r["fingerprint_b"] == sorted(r["fingerprint_b"])

    def test_same_molecule_with_different_representation(self):
        r = ci.tanimoto_similarity("CCO", "OCC")
        assert 0.0 <= r["similarity"] <= 1.0


# ---------------------------------------------------------------------------
# Internal helpers — _tokenize_atoms, _is_subsequence
# ---------------------------------------------------------------------------


class TestTokenizeAtoms:
    def test_simple_smiles(self):
        tokens = ci._tokenize_atoms("CCO")
        assert tokens == ["C", "C", "O"]

    def test_bracket_atom_kept_whole(self):
        tokens = ci._tokenize_atoms("[NH4+]")
        assert tokens == ["[NH4+]"]

    def test_mixed_bracket_and_bare(self):
        tokens = ci._tokenize_atoms("[Na+]O[Na]")
        assert tokens == ["[Na+]", "O", "[Na]"]

    def test_two_letter_elements(self):
        tokens = ci._tokenize_atoms("NaCl")
        assert tokens == ["Na", "Cl"]

    def test_aromatic_tokens(self):
        tokens = ci._tokenize_atoms("c1ccccc1")
        assert all(t in ("c", "1") for t in tokens)


class TestIsSubsequence:
    def test_exact_match(self):
        assert ci._is_subsequence(["C", "C", "O"], ["C", "C", "O"]) is True

    def test_subset_at_start(self):
        assert ci._is_subsequence(["C", "C"], ["C", "C", "O", "N"]) is True

    def test_subset_in_middle(self):
        assert ci._is_subsequence(["C", "O"], ["N", "C", "O", "S"]) is True

    def test_not_subsequence_wrong_order(self):
        assert ci._is_subsequence(["O", "C"], ["C", "C", "O"]) is False

    def test_longer_needle_than_haystack(self):
        assert ci._is_subsequence(["C", "C", "O", "N"], ["C", "C"]) is False

    def test_empty_needle(self):
        assert ci._is_subsequence([], ["C", "C", "O"]) is True

    def test_empty_haystack(self):
        assert ci._is_subsequence(["C"], []) is False

    def test_both_empty(self):
        assert ci._is_subsequence([], []) is True

    def test_bracket_atom_subsequence(self):
        assert ci._is_subsequence(["[NH4+]", "O"], ["C", "[NH4+]", "O"]) is True


class TestPatternPresent:
    def test_empty_pattern_never_matches(self):
        assert ci._pattern_present("CCO", "") is False

    def test_bracket_pattern_matches_substring(self):
        assert ci._pattern_present("[Na+]CCO", "[Na+]") is True

    def test_bracket_pattern_no_match(self):
        assert ci._pattern_present("CCO", "[Fe]") is False


# ---------------------------------------------------------------------------
# Internal helpers — formula/MW resolution via _SMILES_TO_FORMULA
# ---------------------------------------------------------------------------


class TestSmilesToFormulaResolution:
    def test_water_smiles_resolves_to_formula(self):
        assert "O" in ci._SMILES_TO_FORMULA

    def test_ethanol_smiles_resolves_to_formula(self):
        assert "CCO" in ci._SMILES_TO_FORMULA

    def test_methane_smiles_may_resolve(self):
        d = ci.compute_descriptors("C")
        assert d.get("formula")


# ---------------------------------------------------------------------------
# Import sanity
# ---------------------------------------------------------------------------


class TestDeepImportSanity:
    def test_public_exports(self):
        for name in (
            "TOOL",
            "VERSION",
            "compute_descriptors",
            "enumerate_tautomers",
            "standardize_structure",
            "substructure_search",
            "tanimoto_similarity",
            "validate_structure",
        ):
            assert hasattr(ci, name), f"missing: {name}"

    def test_internal_helpers_accessible(self):
        for name in (
            "_tokenize_atoms",
            "_is_subsequence",
            "_pattern_present",
            "_SMILES_TO_FORMULA",
        ):
            assert hasattr(ci, name), f"missing internal: {name}"
