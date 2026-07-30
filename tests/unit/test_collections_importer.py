"""Structural unit tests for src/general_ludd/collections/importer.py."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from general_ludd.collections.importer import (
    ImportIssue,
    TerraformCollectionImporter,
    _find_first,
    _iter_child_dirs,
    _provider_in_trust_list,
)


class TestImportIssue:
    def test_instantiation_fields(self) -> None:
        issue = ImportIssue(severity="error", message="something broke")

        assert issue.severity == "error"
        assert issue.message == "something broke"

    def test_is_frozen(self) -> None:
        issue = ImportIssue(severity="warn", message="test")

        with contextlib.suppress(Exception):
            issue.severity = "error"
        assert issue.severity == "warn"

    def test_is_slots_dataclass(self) -> None:
        issue = ImportIssue(severity="warn", message="test")

        assert "__slots__" in type(issue).__dict__


class TestTerraformCollectionImporterInstantiation:
    def test_default_trust_data_path(self, tmp_path: Path) -> None:
        importer = TerraformCollectionImporter(collection_path=tmp_path)

        assert importer.collection_path == tmp_path
        assert importer.operator_trust_data_path == Path("infra/terraform/policies/data.json")

    def test_custom_trust_data_path(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom" / "trust.json"

        importer = TerraformCollectionImporter(
            collection_path=tmp_path,
            operator_trust_data_path=custom,
        )

        assert importer.operator_trust_data_path == custom


class TestValidateTerraformDirs:
    def test_missing_terraform_dir_returns_warn(self, tmp_path: Path) -> None:
        importer = TerraformCollectionImporter(collection_path=tmp_path)
        issues = importer._validate_terraform_dirs()

        assert len(issues) == 1
        assert issues[0].severity == "warn"
        assert "plugins/terraform/ not present" in issues[0].message

    def test_empty_modules_and_stacks_do_not_error(self, tmp_path: Path) -> None:
        tf_root = tmp_path / "plugins" / "terraform"
        tf_root.mkdir(parents=True)
        (tf_root / "modules").mkdir()
        (tf_root / "stacks").mkdir()

        importer = TerraformCollectionImporter(collection_path=tmp_path)
        issues = importer._validate_terraform_dirs()

        assert len(issues) == 0


class TestValidateRegoPolicies:
    def test_missing_policies_dir_returns_no_issues(self, tmp_path: Path) -> None:
        importer = TerraformCollectionImporter(collection_path=tmp_path)
        issues = importer._validate_rego_policies()

        assert len(issues) == 0

    def test_deny_reassign_triggers_error(self, tmp_path: Path) -> None:
        policies_dir = tmp_path / "plugins" / "terraform" / "policies"
        policies_dir.mkdir(parents=True)
        (policies_dir / "bad.rego").write_text(
            "package gludd.terraform.checks\n\ndeny -= {\"foo\"}\n"
        )

        importer = TerraformCollectionImporter(collection_path=tmp_path)
        issues = importer._validate_rego_policies()

        assert len(issues) >= 1
        deny_issue = next(i for i in issues if "deny reassignment forbidden" in i.message)
        assert deny_issue.severity == "error"
        assert "bad.rego" in deny_issue.message

    def test_additive_rego_passes(self, tmp_path: Path) -> None:
        policies_dir = tmp_path / "plugins" / "terraform" / "policies"
        policies_dir.mkdir(parents=True)
        rego = (
            'package gludd.terraform.checks\n\n'
            'resource_missing_tags[resource] {\n'
            '  resources[resource]\n'
            '  not resource.tags.Environment\n}\n'
        )
        (policies_dir / "good.rego").write_text(rego)

        importer = TerraformCollectionImporter(collection_path=tmp_path)
        issues = importer._validate_rego_policies()

        deny_issues = [i for i in issues if "deny reassignment forbidden" in i.message]
        assert len(deny_issues) == 0


class TestCheckProviderTrust:
    def test_structured_provider_manifest_uses_source_for_trust(
        self, tmp_path: Path
    ) -> None:
        trust_data = {"gludd": {"provider_trust_list": ["hashicorp/aws"]}}
        trust_file = tmp_path / "trust.json"
        trust_file.write_text(json.dumps(trust_data))

        prov_dir = tmp_path / "plugins" / "terraform"
        prov_dir.mkdir(parents=True)
        (prov_dir / "providers.yaml").write_text(
            "providers:\n"
            "  - name: aws\n"
            "    source: hashicorp/aws\n"
            '    version: "~> 5.0"\n'
        )

        importer = TerraformCollectionImporter(
            collection_path=tmp_path,
            operator_trust_data_path=trust_file,
        )

        assert importer._read_providers_yaml() == ["hashicorp/aws"]
        assert importer._check_provider_trust() == []

    def test_trusted_provider_passes(self, tmp_path: Path) -> None:
        trust_data = {"gludd": {"provider_trust_list": ["hashicorp/aws"]}}
        trust_file = tmp_path / "trust.json"
        trust_file.write_text(json.dumps(trust_data))

        prov_dir = tmp_path / "plugins" / "terraform"
        prov_dir.mkdir(parents=True)
        (prov_dir / "providers.yaml").write_text(
            "providers:\n"
            "  - name: aws\n"
            "    source: hashicorp/aws\n"
            '    version: "~> 5.0"\n'
        )

        importer = TerraformCollectionImporter(
            collection_path=tmp_path,
            operator_trust_data_path=trust_file,
        )
        issues = importer._check_provider_trust()

        assert len(issues) == 0

    def test_untrusted_provider_generates_error(self, tmp_path: Path) -> None:
        trust_data = {"gludd": {"provider_trust_list": ["hashicorp/aws"]}}
        trust_file = tmp_path / "trust.json"
        trust_file.write_text(json.dumps(trust_data))

        prov_dir = tmp_path / "plugins" / "terraform"
        prov_dir.mkdir(parents=True)
        (prov_dir / "providers.yaml").write_text(
            "providers:\n"
            "  - name: shells\n"
            "    source: evilcorp/shells\n"
            '    version: "~> 1.0"\n'
        )

        importer = TerraformCollectionImporter(
            collection_path=tmp_path,
            operator_trust_data_path=trust_file,
        )
        issues = importer._check_provider_trust()

        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert "evilcorp/shells" in issues[0].message

    def test_untrusted_provider_from_providers_yaml(self, tmp_path: Path) -> None:
        trust_data = {"gludd": {"provider_trust_list": ["hashicorp/aws"]}}
        trust_file = tmp_path / "trust.json"
        trust_file.write_text(json.dumps(trust_data))

        prov_dir = tmp_path / "plugins" / "terraform"
        prov_dir.mkdir(parents=True)
        (prov_dir / "providers.yaml").write_text(
            "providers:\n"
            "  - name: shells\n"
            "    source: evilcorp/shells\n"
            '    version: "~> 1.0"\n'
        )

        importer = TerraformCollectionImporter(
            collection_path=tmp_path,
            operator_trust_data_path=trust_file,
        )
        issues = importer._check_provider_trust()

        assert len(issues) == 1
        assert "providers.yaml" in issues[0].message

    def test_trust_list_matches_suffix(self, tmp_path: Path) -> None:
        trust_data = {
            "gludd": {"provider_trust_list": ["registry.example.com/hashicorp/aws"]}
        }
        trust_file = tmp_path / "trust.json"
        trust_file.write_text(json.dumps(trust_data))

        prov_dir = tmp_path / "plugins" / "terraform"
        prov_dir.mkdir(parents=True)
        (prov_dir / "providers.yaml").write_text(
            "providers:\n"
            "  - name: aws\n"
            "    source: hashicorp/aws\n"
            '    version: "~> 5.0"\n'
        )

        importer = TerraformCollectionImporter(
            collection_path=tmp_path,
            operator_trust_data_path=trust_file,
        )
        issues = importer._check_provider_trust()

        assert len(issues) == 0


class TestImportCollection:
    def test_aggregate_empty_collection(self, tmp_path: Path) -> None:
        trust_data = {"gludd": {"provider_trust_list": []}}
        trust_file = tmp_path / "trust.json"
        trust_file.write_text(json.dumps(trust_data))

        importer = TerraformCollectionImporter(
            collection_path=tmp_path,
            operator_trust_data_path=trust_file,
        )
        issues = importer.import_collection()

        warn_messages = [i.message for i in issues]
        assert any("plugins/terraform/ not present" in m for m in warn_messages)

    def test_aggregate_with_deny_reassign_and_untrusted(self, tmp_path: Path) -> None:
        trust_data = {"gludd": {"provider_trust_list": ["hashicorp/aws"]}}
        trust_file = tmp_path / "trust.json"
        trust_file.write_text(json.dumps(trust_data))

        policies_dir = tmp_path / "plugins" / "terraform" / "policies"
        policies_dir.mkdir(parents=True)
        (policies_dir / "bad.rego").write_text(
            "package gludd.terraform.checks\n\ndeny = {}\n"
        )

        (policies_dir.parent / "providers.yaml").write_text(
            "providers:\n"
            "  - name: shells\n"
            "    source: evilcorp/shells\n"
            '    version: "~> 1.0"\n'
        )

        importer = TerraformCollectionImporter(
            collection_path=tmp_path,
            operator_trust_data_path=trust_file,
        )
        issues = importer.import_collection()

        assert any("deny reassignment forbidden" in i.message for i in issues)
        assert any("evilcorp/shells" in i.message for i in issues)


class TestIterChildDirs:
    def test_missing_parent_returns_empty(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent"

        result = _iter_child_dirs(missing)

        assert result == []

    def test_returns_dirs_only(self, tmp_path: Path) -> None:
        parent = tmp_path / "parent"
        parent.mkdir()
        (parent / "child_a").mkdir()
        (parent / "child_b").mkdir()
        (parent / "file.txt").write_text("hello")

        result = _iter_child_dirs(parent)

        assert len(result) == 2
        assert all(p.is_dir() for p in result)

    def test_returns_sorted(self, tmp_path: Path) -> None:
        parent = tmp_path / "parent"
        parent.mkdir()
        (parent / "zeta").mkdir()
        (parent / "alpha").mkdir()
        (parent / "gamma").mkdir()

        result = _iter_child_dirs(parent)

        names = [p.name for p in result]
        assert names == ["alpha", "gamma", "zeta"]

    def test_empty_directory(self, tmp_path: Path) -> None:
        parent = tmp_path / "parent"
        parent.mkdir()

        result = _iter_child_dirs(parent)

        assert result == []


class TestProviderInTrustList:
    def test_exact_match(self) -> None:
        trust_list = ["hashicorp/aws", "hashicorp/gcp"]

        assert _provider_in_trust_list("hashicorp/aws", trust_list) is True

    def test_suffix_match(self) -> None:
        trust_list = ["registry.example.com/hashicorp/aws"]

        assert _provider_in_trust_list("hashicorp/aws", trust_list) is True

    def test_no_match(self) -> None:
        trust_list = ["hashicorp/aws"]

        assert _provider_in_trust_list("evilcorp/shells", trust_list) is False

    def test_empty_trust_list(self) -> None:
        assert _provider_in_trust_list("hashicorp/aws", []) is False


class TestFindFirst:
    def test_empty_iterator(self) -> None:
        result = _find_first(iter([]))

        assert result is None

    def test_returns_first(self) -> None:
        result = _find_first(iter([Path("a"), Path("b"), Path("c")]))

        assert result == Path("a")
