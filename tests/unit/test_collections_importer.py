"""Structural tests for collections/importer.py — collection import validator."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from general_ludd.collections.importer import (
    _DENY_REASSIGN_RE,
    ImportIssue,
    TerraformCollectionImporter,
    _find_first,
    _is_floating_version,
    _parse_required_providers,
    _parse_tfvars_keys,
    _parse_variable_names,
    _provider_in_trust_list,
)


class TestImportIssue:
    def test_frozen_dataclass(self) -> None:
        issue = ImportIssue(severity="error", message="bad")
        assert issue.severity == "error"
        assert issue.message == "bad"

    def test_hashable(self) -> None:
        issue = ImportIssue(severity="warn", message="x")
        assert hash(issue) is not None


class TestDenyReassignRegex:
    def test_matches_deny_reassign(self) -> None:
        assert _DENY_REASSIGN_RE.search("deny = {}") is not None

    def test_matches_deny_plus_eq(self) -> None:
        assert _DENY_REASSIGN_RE.search("deny += data") is not None

    def test_matches_deny_minus_eq(self) -> None:
        assert _DENY_REASSIGN_RE.search("deny -= data") is not None

    def test_no_false_positive_deny_rule(self) -> None:
        assert _DENY_REASSIGN_RE.search('deny["x"]') is None
        assert _DENY_REASSIGN_RE.search("deny_foo = 1") is None


class TestParseVariableNames:
    def test_single_variable(self) -> None:
        text = '\nvariable "region" {\n  type = string\n}\n'
        assert _parse_variable_names(text) == ["region"]

    def test_multiple_variables(self) -> None:
        text = '''
variable "region" {
  type = string
}
variable "zone" {
  type = string
}
'''
        result = _parse_variable_names(text)
        assert "region" in result
        assert "zone" in result

    def test_no_variables(self) -> None:
        assert _parse_variable_names("resource 'foo' \"bar\" {}") == []


class TestParseTfvarsKeys:
    def test_simple_assignment(self) -> None:
        keys = _parse_tfvars_keys("region = \"us-east-1\"\nzone = \"a\"\n")
        assert keys == {"region", "zone"}

    def test_ignores_comments(self) -> None:
        keys = _parse_tfvars_keys("# region = \"us-east-1\"\nzone = \"a\"\n")
        assert keys == {"zone"}

    def test_ignores_blank_and_comments(self) -> None:
        keys = _parse_tfvars_keys("\n  \n// cidr = \"10.0.0.0/16\"\n")
        assert keys == set()


class TestParseRequiredProviders:
    def test_single_provider(self) -> None:
        text = """
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
"""
        result = _parse_required_providers(text)
        assert result == {"aws": "~> 5.0"}

    def test_multiple_providers(self) -> None:
        text = """
terraform {
  required_providers {
    aws = {
      version = "~> 5.0"
    }
    gcp = {
      version = ">= 4.0"
    }
  }
}
"""
        result = _parse_required_providers(text)
        assert result["aws"] == "~> 5.0"
        assert result["gcp"] == ">= 4.0"

    def test_no_providers(self) -> None:
        assert _parse_required_providers("resource 'null' \"x\" {}") == {}


class TestIsFloatingVersion:
    def test_pinned_not_floating(self) -> None:
        assert _is_floating_version("~> 5.0") is False
        assert _is_floating_version("= 5.0.1") is False

    def test_unpinned_is_floating(self) -> None:
        assert _is_floating_version(">= 5.0") is True
        assert _is_floating_version("> 4.0") is True

    def test_empty_is_not_floating(self) -> None:
        assert _is_floating_version("") is False


class TestProviderInTrustList:
    def test_exact_match(self) -> None:
        assert _provider_in_trust_list("hashicorp/aws", ["hashicorp/aws"]) is True

    def test_suffix_match(self) -> None:
        assert _provider_in_trust_list("aws", ["hashicorp/aws"]) is True

    def test_no_match(self) -> None:
        assert _provider_in_trust_list("evil/prov", ["hashicorp/aws"]) is False

    def test_empty_trust_list(self) -> None:
        assert _provider_in_trust_list("aws", []) is False


class TestFindFirst:
    def test_empty_iterator(self) -> None:
        assert _find_first(iter([])) is None

    def test_returns_first(self) -> None:
        p = Path("/tmp/test")
        result = _find_first(iter([p, Path("/other")]))
        assert result == p


class TestTerraformCollectionImporter:
    @pytest.fixture
    def empty_collection_dir(self) -> Path:
        tmp = tempfile.mkdtemp()
        collection = Path(tmp) / "test-collection"
        collection.mkdir()
        return collection

    def test_constructs_with_defaults(self) -> None:
        imp = TerraformCollectionImporter(collection_path=Path("/tmp"))
        assert imp.collection_path == Path("/tmp")

    def test_import_missing_terraform_warns(self, empty_collection_dir: Path) -> None:
        imp = TerraformCollectionImporter(collection_path=empty_collection_dir)
        issues = imp._validate_terraform_dirs()
        assert any("not present" in i.message for i in issues)

    def test_import_missing_rego_no_issues(self, empty_collection_dir: Path) -> None:
        imp = TerraformCollectionImporter(collection_path=empty_collection_dir)
        issues = imp._validate_rego_policies()
        assert issues == []

    def test_rego_deny_reassign_detected(self, empty_collection_dir: Path) -> None:
        policies = empty_collection_dir / "plugins" / "terraform" / "policies"
        policies.mkdir(parents=True)
        (policies / "bad.rego").write_text("package test\ndeny -= {\"x\"}\n")
        imp = TerraformCollectionImporter(collection_path=empty_collection_dir)
        issues = imp._validate_rego_policies()
        assert any("deny reassignment forbidden" in i.message for i in issues)

    def test_rego_clean_passes(self, empty_collection_dir: Path) -> None:
        policies = empty_collection_dir / "plugins" / "terraform" / "policies"
        policies.mkdir(parents=True)
        (policies / "ok.rego").write_text("package test\ndeny[msg] { input.x == 1; msg := \"bad\" }\n")
        imp = TerraformCollectionImporter(collection_path=empty_collection_dir)
        issues = imp._validate_rego_policies()
        deny_issues = [i for i in issues if "deny reassignment" in i.message]
        assert deny_issues == []

    def test_tfvars_schema_check_no_variables(self, empty_collection_dir: Path) -> None:
        imp = TerraformCollectionImporter(collection_path=empty_collection_dir)
        issues = imp._tfvars_schema_check()
        assert issues == []

    def test_provider_pin_check_absent_terraform(self, empty_collection_dir: Path) -> None:
        imp = TerraformCollectionImporter(collection_path=empty_collection_dir)
        issues = imp._provider_pin_check()
        assert issues == []
