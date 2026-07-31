"""CHEM-AT-004: Citation corpus — every claim maps to source + method locator.

Per spec §2, the chemistry expert must surface every factual claim with a
complete provenance chain (source, method, conditions, code, raw_artifact).
``general_ludd.chemistry.provenance`` provides :class:`ProvenanceChain`,
:func:`build_chain`, and :func:`verify_chain` — the building blocks for
the 100-case citation corpus CHEM-AT-004 requires.

This module exercises the provenenace primitives that underpin the corpus;
the full 100-case golden corpus lives in ``tests/fixtures/chemistry/golden/``
(not yet populated).  0 of 100 golden cases today; the tests below prove the
building blocks are correct so the corpus can be populated mechanically.
"""

from __future__ import annotations

import importlib.util
import os

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_PROVENANCE_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "provenance.py")


def _load_mod(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


provenance = _load_mod(_PROVENANCE_PATH, "chem_provenance_at004")

# ---------------------------------------------------------------------------
# Citation corpus helpers
# ---------------------------------------------------------------------------

_CITATION_FIXTURE = {
    "source": {
        "locator": "doi:10.1021/acs.jced.9b00000",
        "citation": "J. Chem. Eng. Data 2020, 65, 1234-1245",
        "access_date": "2025-01-15",
        "publisher": "ACS Publications",
    },
    "method": "differential-scanning-calorimetry",
    "conditions": {
        "temperature_K": 298.15,
        "pressure_bar": 1.01325,
        "instrument": "TA Q2000",
    },
    "code": {
        "repo": "github.com/example/chem-lab",
        "commit": "abc123def",
        "module": "parse_dsc",
    },
    "raw_artifact": {
        "uri": "s3://chem-data/dsc-run-42.raw",
        "digest": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    },
}


def _full_chain(**overrides):
    rec = {**_CITATION_FIXTURE}
    for k, v in overrides.items():
        rec[k] = v
    return rec


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProvenanceChainConstruction:
    """ProvenanceChain represents a single claim's end-to-end provenance."""

    def test_chain_from_full_record(self):
        chain = provenance.ProvenanceChain(_full_chain())
        assert chain.source["citation"] is not None
        assert chain.method == "differential-scanning-calorimetry"

    def test_chain_source_locator_is_verifiable(self):
        chain = provenance.ProvenanceChain(_full_chain())
        assert chain.source.get("locator", "").startswith("doi:")

    def test_chain_has_code_commit_reference(self):
        chain = provenance.ProvenanceChain(_full_chain())
        assert "commit" in chain.code
        assert "module" in chain.code

    def test_chain_raw_artifact_has_content_digest(self):
        chain = provenance.ProvenanceChain(_full_chain())
        raw = chain.raw_artifact
        assert raw.get("digest", "").startswith("sha256:")

    def test_empty_record_defaults_to_empty_struts(self):
        chain = provenance.ProvenanceChain({})
        assert chain.source == {}
        assert chain.method == ""
        assert chain.code == {}
        assert chain.raw_artifact == {}


class TestBuildChain:
    """build_chain extracts ProvenanceChain from result shapes."""

    def test_build_from_top_level_provenance_block(self):
        result = {"value": 1.23, "provenance": _CITATION_FIXTURE}
        chains = provenance.build_chain(result)
        assert not isinstance(chains, list) or len(chains) > 0
        chain = chains[0] if isinstance(chains, list) else chains
        assert isinstance(chain, provenance.ProvenanceChain)

    def test_build_from_nested_records(self):
        result = {
            "melting_point": {"value": 373.15, "provenance": _CITATION_FIXTURE},
            "boiling_point": {"value": 423.15, "provenance": _CITATION_FIXTURE},
        }
        chains = provenance.build_chain(result)
        assert len(chains) == 2
        for ch in chains:
            assert isinstance(ch, provenance.ProvenanceChain)

    def test_result_without_provenance_returns_empty(self):
        result = {"value": 1.23, "note": "no provenance"}
        chains = provenance.build_chain(result)
        assert chains == []

    def test_build_from_raw_provenance_dict(self):
        chains = provenance.build_chain(_CITATION_FIXTURE)
        chain = (chains[0] if chains else None) if isinstance(chains, list) else chains
        assert isinstance(chain, provenance.ProvenanceChain)


class TestVerifyChain:
    """verify_chain checks completeness of all five required links."""

    def test_full_chain_is_complete(self):
        chain = provenance.ProvenanceChain(_full_chain())
        report = provenance.verify_chain(chain)
        assert report["complete"] is True
        assert report["missing"] == []

    def test_missing_source_fails_verification(self):
        rec = _full_chain(source={})
        chain = provenance.ProvenanceChain(rec)
        report = provenance.verify_chain(chain)
        assert report["complete"] is False
        assert "source" in report["missing"]

    def test_missing_method_fails_verification(self):
        rec = _full_chain(method="")
        chain = provenance.ProvenanceChain(rec)
        report = provenance.verify_chain(chain)
        assert report["complete"] is False
        assert "method" in report["missing"]

    def test_orphan_artifact_fails_verification(self):
        rec = _full_chain(raw_artifact={"uri": "s3://missing", "digest": "abc", "orphan": True})
        chain = provenance.ProvenanceChain(rec)
        report = provenance.verify_chain(chain)
        assert report["complete"] is False
        assert len(report["orphan_artifacts"]) > 0

    def test_verification_of_non_chain_returns_not_complete(self):
        report = provenance.verify_chain(42)
        assert report["complete"] is False
        assert report["chain_id"] is None


class TestCitationPrecision:
    """CHEM-AT-004: citation must map to a verifiable source + method locator."""

    def test_citation_locator_is_parseable_doi(self):
        chain = provenance.ProvenanceChain(_full_chain())
        locator = chain.source.get("locator", "")
        assert "doi:" in locator or "http" in locator or "://" in locator

    def test_citation_publisher_traceable(self):
        chain = provenance.ProvenanceChain(_full_chain())
        assert chain.source.get("publisher", "") != ""

    def test_method_value_is_non_empty_identifiable_string(self):
        chain = provenance.ProvenanceChain(_full_chain())
        assert isinstance(chain.method, str) and len(chain.method) > 0

    def test_conditions_are_typed_not_generic(self):
        chain = provenance.ProvenanceChain(_full_chain())
        cond = chain.conditions
        assert isinstance(cond, dict)
        # At least temperature or pressure should be specified
        has_typed = any(k.startswith("temperature") or k.startswith("pressure") for k in cond)
        assert has_typed or "instrument" in cond

    @pytest.mark.skip(
        "CHEM-AT-004: 100-case golden citation corpus not yet populated. "
        "The building blocks (ProvenanceChain, build_chain, verify_chain) are "
        "correct; the corpus fixtures live under tests/fixtures/chemistry/golden/ "
        "and will be populated in a follow-on PR."
    )
    def test_hundred_case_corpus_loads(self):
        """All 100 golden citation cases load and verify as complete.

        Skipped: corpus fixtures not yet populated.  This stub proves the
        concept; populate tests/fixtures/chemistry/golden/*.json and remove
        the skip marker.
        """
        pass  # pragma: no cover
