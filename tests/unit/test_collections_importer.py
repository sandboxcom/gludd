"""Unit tests for collections importer helper functions."""

from __future__ import annotations

import json
from pathlib import Path

from general_ludd.collections.importer import (
    ImportIssue,
    TerraformCollectionImporter,
    _find_first,
    _is_floating_version,
    _iter_child_dirs,
    _parse_required_providers,
    _parse_tfvars_keys,
    _parse_variable_names,
    _provider_in_trust_list,
)


class TestImportIssue:
    def test_import_issue_is_frozen_dataclass(self):
        issue = ImportIssue(severity="error", message="test")
        assert issue.severity == "error"
        assert issue.message == "test"

    def test_import_issue_slots(self):
        issue = ImportIssue(severity="warn", message="hello")
        assert not hasattr(issue, "__dict__")


class TestParseVariableNames:
    def test_extracts_variable_names(self):
        text = 'variable "foo" {\n  default = "bar"\n}\nvariable "baz" {\n}'
        result = _parse_variable_names(text)
        assert result == ["foo", "baz"]

    def test_handles_no_variables(self):
        result = _parse_variable_names('resource "aws_s3" "b" {}')
        assert result == []

    def test_ignores_commented_variables(self):
        text = '# variable "commented" {\nvariable "real" {\n}'
        result = _parse_variable_names(text)
        assert result == ["real"]


class TestParseTfvarsKeys:
    def test_extracts_keys(self):
        text = "foo = 1\nbar = 2\nbaz = 3"
        result = _parse_tfvars_keys(text)
        assert result == {"foo", "bar", "baz"}

    def test_ignores_comments(self):
        text = "foo = 1\n# bar = 2\n// baz = 3\n"
        result = _parse_tfvars_keys(text)
        assert result == {"foo"}

    def test_ignores_blank_lines(self):
        text = "\nfoo = 1\n\nbar = 2\n"
        result = _parse_tfvars_keys(text)
        assert result == {"foo", "bar"}

    def test_ignores_hcl_blocks(self):
        text = 'resource "aws_s3" "bucket" {\n  name = "test"\n}'
        result = _parse_tfvars_keys(text)
        assert result == {"name"}


class TestParseRequiredProviders:
    def test_extracts_version(self):
        text = """
        terraform {
          required_providers {
            aws = {
              source  = "hashicorp/aws"
              version = "~> 2.8"
            }
            gcp = {
              source  = "hashicorp/google"
              version = "= 3.0.0"
            }
          }
        }
        """
        result = _parse_required_providers(text)
        assert result == {"aws": "~> 2.8", "gcp": "= 3.0.0"}

    def test_no_required_providers_block(self):
        result = _parse_required_providers('resource "foo" "bar" {}')
        assert result == {}

    def test_nested_braces_handled(self):
        text = """
        required_providers {
          aws = {
            version = "~> 2.8"
            configuration {
              region = "us-east-1"
            }
          }
        }
        """
        result = _parse_required_providers(text)
        assert result == {"aws": "~> 2.8"}


class TestIsFloatingVersion:
    def test_pinned_tilde_not_floating(self):
        assert _is_floating_version("~> 2.8") is False

    def test_pinned_equals_not_floating(self):
        assert _is_floating_version("= 2.8.0") is False

    def test_gt_is_floating(self):
        assert _is_floating_version(">= 2.0") is True

    def test_lt_is_floating(self):
        assert _is_floating_version("< 2.0") is True

    def test_empty_is_not_floating(self):
        assert _is_floating_version("") is False


class TestProviderInTrustList:
    def test_exact_match(self):
        assert _provider_in_trust_list("hashicorp/aws", ["hashicorp/aws", "hashicorp/gcp"]) is True

    def test_suffix_match(self):
        assert _provider_in_trust_list("aws", ["hashicorp/aws"]) is True

    def test_no_match(self):
        assert _provider_in_trust_list("unknown", ["hashicorp/aws"]) is False

    def test_empty_trust_list(self):
        assert _provider_in_trust_list("aws", []) is False


class TestFindFirst:
    def test_returns_first(self):
        assert _find_first(iter([1, 2, 3])) == 1

    def test_empty_returns_none(self):
        assert _find_first(iter([])) is None


class TestIterChildDirs:
    def test_returns_child_dirs(self, tmp_path: Path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / "c.txt").write_text("")
        result = _iter_child_dirs(tmp_path)
        assert len(result) == 2
        assert all(p.is_dir() for p in result)

    def test_nonexistent_dir(self, tmp_path: Path):
        result = _iter_child_dirs(tmp_path / "nope")
        assert result == []


class TestTerraformCollectionImporterSmoke:
    def test_init_stores_paths(self, tmp_path: Path):
        importer = TerraformCollectionImporter(collection_path=tmp_path)
        assert importer.collection_path == tmp_path

    def test_import_collection_no_dirs_returns_warn(self, tmp_path: Path):
        importer = TerraformCollectionImporter(collection_path=tmp_path)
        issues = importer.import_collection()
        assert any(i.severity == "warn" for i in issues)

    def test_load_operator_trust_list_valid(self, tmp_path: Path):
        data = {"gludd": {"provider_trust_list": ["hashicorp/aws", "hashicorp/gcp"]}}
        trust_path = tmp_path / "data.json"
        trust_path.write_text(json.dumps(data))
        importer = TerraformCollectionImporter(
            collection_path=tmp_path, operator_trust_data_path=trust_path
        )
        trust = importer._load_operator_trust_list()
        assert trust == ["hashicorp/aws", "hashicorp/gcp"]

    def test_load_operator_trust_list_missing_raises(self, tmp_path: Path):
        trust_path = tmp_path / "nonexistent.json"
        importer = TerraformCollectionImporter(
            collection_path=tmp_path, operator_trust_data_path=trust_path
        )
        import pytest
        with pytest.raises(RuntimeError, match="could not read operator trust list"):
            importer._load_operator_trust_list()

    def test_load_operator_trust_list_empty_data(self, tmp_path: Path):
        trust_path = tmp_path / "data.json"
        trust_path.write_text("{}")
        importer = TerraformCollectionImporter(
            collection_path=tmp_path, operator_trust_data_path=trust_path
        )
        trust = importer._load_operator_trust_list()
        assert trust == []

    def test_read_galaxy_metadata_missing(self, tmp_path: Path):
        importer = TerraformCollectionImporter(collection_path=tmp_path)
        meta = importer._read_galaxy_metadata()
        assert meta == {}

    def test_read_providers_yaml_missing(self, tmp_path: Path):
        importer = TerraformCollectionImporter(collection_path=tmp_path)
        providers = importer._read_providers_yaml()
        assert providers == []
