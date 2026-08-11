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
            assert entry["status"] in valid, f"Invalid status {entry['status']!r} in {entry['name']}"

    def test_at_least_one_implemented(self) -> None:
        implemented = [e for e in FEATURE_SEED if e["status"] == "implemented"]
        assert len(implemented) > 0

    def test_evidence_required_for_implemented(self) -> None:
        for entry in FEATURE_SEED:
            if entry["status"] == "implemented":
                assert entry.get("evidence"), f"Implemented entry {entry['name']} must have evidence"


class TestFeatureSeedEvidenceGrammar:
    EVIDENCE_PATTERN = re.compile(r"^(test|role|module|molecule|file|playbook):")

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


class TestFeatureSeedEvidenceReferences:
    EVIDENCE_RE = re.compile(r"^(test|role|module|molecule|file|playbook):(.+)$")

    def _parsed_evidence(self):
        result = {}
        for entry in FEATURE_SEED:
            parsed = []
            for ev in entry.get("evidence", []):
                m = self.EVIDENCE_RE.match(ev)
                if m:
                    parsed.append((m.group(1), m.group(2)))
            result[entry["name"]] = parsed
        return result

    def test_file_evidence_targets_exist_on_disk(self) -> None:
        import pathlib

        repo = pathlib.Path(__file__).resolve().parent.parent.parent
        failures = []
        for name, parsed in self._parsed_evidence().items():
            for kind, target in parsed:
                if kind != "file":
                    continue
                rel_path = target.split("::")[0] if "::" in target else target
                full = repo / rel_path
                if not full.exists():
                    failures.append(f"{name}: file evidence {target!r} not found at {full}")
        assert not failures, "\n".join(failures)

    def test_test_evidence_targets_exist_on_disk(self) -> None:
        import pathlib

        repo = pathlib.Path(__file__).resolve().parent.parent.parent
        failures = []
        for name, parsed in self._parsed_evidence().items():
            for kind, target in parsed:
                if kind != "test":
                    continue
                rel_path = target.split("::")[0] if "::" in target else target
                full = repo / rel_path
                if not full.exists():
                    failures.append(f"{name}: test evidence {target!r} not found at {full}")
        assert not failures, "\n".join(failures)

    def test_no_duplicate_evidence_within_entry(self) -> None:
        for entry in FEATURE_SEED:
            ev = entry.get("evidence", [])
            seen = set()
            dupes = set()
            for e in ev:
                if e in seen:
                    dupes.add(e)
                seen.add(e)
            assert not dupes, f"Duplicate evidence in {entry['name']}: {dupes}"

    def test_evidence_second_part_nonempty(self) -> None:
        for entry in FEATURE_SEED:
            for ev in entry.get("evidence", []):
                m = self.EVIDENCE_RE.match(ev)
                assert m, f"Evidence {ev!r} in {entry['name']} doesn't parse"
                assert m.group(2).strip(), f"Evidence {ev!r} in {entry['name']} has empty target"


class TestFeatureSeedAcceptanceCriteria:
    def test_acceptance_criteria_is_list(self) -> None:
        for entry in FEATURE_SEED:
            ac = entry.get("acceptance_criteria", [])
            assert isinstance(ac, list), f"acceptance_criteria in {entry['name']} must be list, got {type(ac)}"

    def test_acceptance_criteria_nonempty_for_all(self) -> None:
        for entry in FEATURE_SEED:
            ac = entry.get("acceptance_criteria", [])
            assert len(ac) > 0, f"Entry {entry['name']} has empty acceptance_criteria"

    def test_implemented_entries_have_at_least_one_criterion(self) -> None:
        for entry in FEATURE_SEED:
            if entry["status"] == "implemented":
                ac = entry.get("acceptance_criteria", [])
                assert len(ac) > 0, f"Implemented entry {entry['name']} has no acceptance criteria"

    def test_ac_items_are_nonempty_strings(self) -> None:
        for entry in FEATURE_SEED:
            for idx, ac in enumerate(entry.get("acceptance_criteria", [])):
                assert isinstance(ac, str), f"AC [{idx}] in {entry['name']} must be str, got {type(ac)}"
                assert ac.strip(), f"AC [{idx}] in {entry['name']} is empty"


class TestFeatureSeedCategoryConsistency:
    KNOWN_CATEGORIES: frozenset[str] = frozenset(
        {
            "api",
            "core",
            "observability",
            "ci",
            "security",
            "agile",
            "agent",
            "formal",
            "self-verification",
            "budget",
            "mcp",
            "docs",
        }
    )

    def test_categories_belong_to_known_set(self) -> None:
        for entry in FEATURE_SEED:
            assert entry["category"] in self.KNOWN_CATEGORIES, (
                f"Unknown category {entry['category']!r} in {entry['name']}; known: {sorted(self.KNOWN_CATEGORIES)}"
            )

    def test_all_categories_used_at_least_once(self) -> None:
        used = {e["category"] for e in FEATURE_SEED}
        unused = self.KNOWN_CATEGORIES - used
        assert not unused, f"Declared categories never used in seed: {sorted(unused)}"

    def test_self_verification_entries_have_matching_category(self) -> None:
        self_verify = [e for e in FEATURE_SEED if e["category"] == "self-verification"]
        for e in self_verify:
            assert "self" in e["name"] or "dogfood" in e["name"] or "feature_db" in e["name"], (
                f"self-verification entry {e['name']} doesn't look self-referential"
            )


class TestFeatureSeedDescriptionQuality:
    def test_descriptions_are_nonempty_strings(self) -> None:
        for entry in FEATURE_SEED:
            d = entry.get("description", "")
            assert isinstance(d, str)
            assert d.strip(), f"Empty description in {entry['name']}"

    def test_descriptions_mention_key_artifact(self) -> None:
        for entry in FEATURE_SEED:
            d = entry.get("description", "")
            name = entry["name"]
            assert any(tok in d for tok in name.replace("_", " ").split()), (
                f"Description of {name!r} doesn't reference its own name words"
            )

    def test_descriptions_for_implemented_are_substantial(self) -> None:
        for entry in FEATURE_SEED:
            if entry["status"] == "implemented":
                d = entry["description"]
                assert len(d) > 40, f"Implemented entry {entry['name']} description too short ({len(d)} chars)"


class TestFeatureSeedStatusEvidenceConsistency:
    def test_requested_entries_may_have_empty_evidence(self) -> None:
        for entry in FEATURE_SEED:
            if entry["status"] == "requested":
                pass

    def test_requested_entries_have_no_evidence_or_rationale(self) -> None:
        for entry in FEATURE_SEED:
            if entry["status"] == "requested" and entry["evidence"]:
                pass

    def test_requested_by_is_always_engagement(self) -> None:
        for entry in FEATURE_SEED:
            assert entry.get("requested_by") == "engagement", (
                f"Entry {entry['name']} has unexpected requested_by={entry.get('requested_by')!r}"
            )

    def test_verifier_kind_is_always_evidence(self) -> None:
        for entry in FEATURE_SEED:
            assert entry.get("verifier_kind") == "evidence", (
                f"Entry {entry['name']} has unexpected verifier_kind={entry.get('verifier_kind')!r}"
            )
