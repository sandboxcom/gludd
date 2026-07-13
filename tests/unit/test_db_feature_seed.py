"""Unit tests for db/feature_seed.py — feature seed data structure and evidence grammar."""

from __future__ import annotations

import re

from general_ludd.db.feature_seed import FEATURE_SEED


class TestFeatureSeedStructure:
    def test_seed_is_list(self) -> None:
        assert isinstance(FEATURE_SEED, list)

    def test_seed_not_empty(self) -> None:
        assert len(FEATURE_SEED) > 0

    def test_all_entries_are_dicts(self) -> None:
        for entry in FEATURE_SEED:
            assert isinstance(entry, dict), f"Expected dict, got {type(entry)}"

    def test_all_entries_have_required_keys(self) -> None:
        required = {"name", "category", "description", "status", "evidence", "requested_by"}
        for entry in FEATURE_SEED:
            missing = required - set(entry.keys())
            assert not missing, f"Entry {entry.get('name', 'unknown')} missing {missing}"

    def test_names_are_unique(self) -> None:
        names = [e["name"] for e in FEATURE_SEED]
        assert len(names) == len(set(names)), f"Duplicate names: {names}"

    def test_categories_are_nonempty_strings(self) -> None:
        for entry in FEATURE_SEED:
            assert isinstance(entry["category"], str)
            assert entry["category"].strip(), f"Empty category in {entry['name']}"


class TestFeatureSeedStatus:
    def test_status_values_are_valid(self) -> None:
        valid = {"implemented", "requested", "verified"}
        for entry in FEATURE_SEED:
            assert entry["status"] in valid, (
                f"Invalid status {entry['status']!r} in {entry['name']}"
            )

    def test_at_least_one_implemented(self) -> None:
        implemented = [e for e in FEATURE_SEED if e["status"] == "implemented"]
        assert len(implemented) > 0

    def test_evidence_required_for_implemented(self) -> None:
        for entry in FEATURE_SEED:
            if entry["status"] == "implemented":
                assert entry.get("evidence"), (
                    f"Implemented entry {entry['name']} must have evidence"
                )


class TestFeatureSeedEvidenceGrammar:
    EVIDENCE_PATTERN = re.compile(
        r"^(test|role|module|molecule|file|playbook):"
    )

    def test_evidence_entries_match_grammar(self) -> None:
        for entry in FEATURE_SEED:
            for evidence in entry.get("evidence", []):
                assert self.EVIDENCE_PATTERN.match(evidence), (
                    f"Evidence {evidence!r} in {entry['name']} does not match grammar"
                )

    def test_evidence_is_list(self) -> None:
        for entry in FEATURE_SEED:
            assert isinstance(entry.get("evidence", []), list)

    def test_verifier_kind_present(self) -> None:
        for entry in FEATURE_SEED:
            assert "verifier_kind" in entry

    def test_requested_by_present(self) -> None:
        for entry in FEATURE_SEED:
            assert entry.get("requested_by")


class TestFeatureSeedSpecificEntries:
    def test_feature_db_entry_exists(self) -> None:
        names = {e["name"] for e in FEATURE_SEED}
        assert "feature_db" in names

    def test_facts_api_mq_entry_exists(self) -> None:
        names = {e["name"] for e in FEATURE_SEED}
        assert "facts_api_mq" in names

    def test_message_queue_entry_exists(self) -> None:
        names = {e["name"] for e in FEATURE_SEED}
        assert "message_queue" in names

    def test_dynamic_dispatch_is_requested(self) -> None:
        entry = next(e for e in FEATURE_SEED if e["name"] == "dynamic_dispatch")
        assert entry["status"] == "requested"

    def test_feature_db_entry_has_evidence(self) -> None:
        entry = next(e for e in FEATURE_SEED if e["name"] == "feature_db")
        assert len(entry["evidence"]) > 0

    def test_feature_db_is_implemented(self) -> None:
        entry = next(e for e in FEATURE_SEED if e["name"] == "feature_db")
        assert entry["status"] == "implemented"
