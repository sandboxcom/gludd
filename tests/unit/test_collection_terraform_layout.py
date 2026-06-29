"""TDD for the gludd collection importer (terraform + OPA content).

Validates a user-submitted ansible-galaxy collection at IMPORT time. The
importer checks:
  * plugins/terraform/modules|stacks layout
  * plugins/terraform/policies/*.rego (additive deny rules only)
  * plugins/terraform/providers.yaml trust cross-check
  * galaxy.yml terraform_provider_trust field

`terraform validate` and `opa check` are invoked via subprocess.run; the tests
monkeypatch subprocess.run so they pass without those binaries installed. The
combined-policy test (test 3) skips cleanly when conftest or the s3 fixture is
absent.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

from general_ludd.collections.importer import ImportIssue, TerraformCollectionImporter

_PROJECT = Path(__file__).resolve().parent.parent.parent
_TRUST_DATA = _PROJECT / "infra" / "terraform" / "policies" / "data.json"
_CORE_POLICIES = _PROJECT / "infra" / "terraform" / "policies"
_S3_FIXTURE = _PROJECT / "tests" / "fixtures" / "terraform" / "s3_public_read_fail" / "tfplan.json"
_EXAMPLE_POLICY = (
    _PROJECT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "plugins"
    / "terraform"
    / "policies"
    / "example_tag_enforcement.rego"
)
_EXAMPLE_TAG_FIXTURE = (
    _PROJECT / "tests" / "fixtures" / "terraform" / "example_tag_enforcement" / "tfplan.json"
)

_VALID_MAIN_TF = textwrap.dedent("""\
    terraform {
      required_providers {
        aws = {
          source  = "hashicorp/aws"
          version = "~> 2.8"
        }
      }
    }
    """)

_EXTRA_REGO = textwrap.dedent("""\
    package main

    deny[level] {
      input.planned_values.root_module.resources[_].type == "aws_s3_bucket"
      level := "user-policy: s3 found"
    }
    """)


def _ok_completed(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess[str](args=[], returncode=0, stdout="", stderr="")


def _build_collection(
    root: Path,
    *,
    provider_trust: list[str],
    providers_yaml: list[str],
    extra_rego: str | None = _EXTRA_REGO,
    main_tf: str = _VALID_MAIN_TF,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)

    galaxy_lines = ["namespace: acme", "name: obs", "version: 1.0.0", "terraform_provider_trust:"]
    galaxy_lines.extend(f'  - "{p}"' for p in provider_trust)
    root.joinpath("galaxy.yml").write_text("\n".join(galaxy_lines) + "\n", encoding="utf-8")

    modules_dir = root / "plugins" / "terraform" / "modules" / "x"
    modules_dir.mkdir(parents=True)
    modules_dir.joinpath("main.tf").write_text(main_tf, encoding="utf-8")

    policies_dir = root / "plugins" / "terraform" / "policies"
    policies_dir.mkdir(parents=True)
    if extra_rego is not None:
        policies_dir.joinpath("extra.rego").write_text(extra_rego, encoding="utf-8")

    providers_lines = ["providers:"]
    providers_lines.extend(f"  - {p}" for p in providers_yaml)
    providers_file = root / "plugins" / "terraform" / "providers.yaml"
    providers_file.write_text("\n".join(providers_lines) + "\n", encoding="utf-8")
    return root


def test_collection_with_terraform_dir_passes_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _ok_completed)
    root = _build_collection(
        tmp_path / "acme-obs",
        provider_trust=["hashicorp/aws"],
        providers_yaml=["hashicorp/aws"],
    )
    issues = TerraformCollectionImporter(root, operator_trust_data_path=_TRUST_DATA).import_collection()
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], f"expected no errors, got: {errors}"


def test_collection_with_untrusted_provider_fails_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _ok_completed)
    root = _build_collection(
        tmp_path / "acme-obs",
        provider_trust=["somecorp/some-provider"],
        providers_yaml=["somecorp/some-provider"],
    )
    issues = TerraformCollectionImporter(root, operator_trust_data_path=_TRUST_DATA).import_collection()
    messages = " ".join(i.message for i in issues)
    assert "somecorp/some-provider" in messages, (
        f"expected untrusted-provider error mentioning somecorp/some-provider; got {issues}"
    )
    assert any(i.severity == "error" for i in issues), issues


def test_collection_policy_extends_core_deny_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("conftest") is None or shutil.which("opa") is None:
        pytest.skip("conftest/opa not installed")
    if not _S3_FIXTURE.is_file():
        pytest.skip("s3_public_read_fail fixture not yet created")

    monkeypatch.setattr(subprocess, "run", _ok_completed)
    root = _build_collection(
        tmp_path / "acme-obs",
        provider_trust=["hashicorp/aws"],
        providers_yaml=["hashicorp/aws"],
    )
    issues = TerraformCollectionImporter(root, operator_trust_data_path=_TRUST_DATA).import_collection()
    assert not any(i.severity == "error" for i in issues), issues

    user_policies = root / "plugins" / "terraform" / "policies"
    combined = subprocess.run(
        [
            "conftest",
            "test",
            "-p",
            str(_CORE_POLICIES),
            "-p",
            str(user_policies),
            str(_S3_FIXTURE),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert combined.returncode != 0, "expected denials from combined policy run"
    stdout = combined.stdout + combined.stderr
    assert "user-policy: s3 found" in stdout, f"missing user-policy denial in:\n{stdout}"


def test_collection_policy_cannot_override_core_deny(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _ok_completed)
    bad_rego = 'package main\n\ndeny -= "core: provider not trusted"\n'
    root = _build_collection(
        tmp_path / "acme-obs",
        provider_trust=["hashicorp/aws"],
        providers_yaml=["hashicorp/aws"],
        extra_rego=bad_rego,
    )
    issues = TerraformCollectionImporter(root, operator_trust_data_path=_TRUST_DATA).import_collection()
    assert any("deny reassignment" in i.message for i in issues), (
        f"expected 'deny reassignment' error; got {issues}"
    )
    assert any(i.severity == "error" for i in issues), issues


def test_collection_providers_yaml_trust_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _ok_completed)
    root = _build_collection(
        tmp_path / "acme-obs",
        provider_trust=["hashicorp/aws"],
        providers_yaml=["hashicorp/aws", "somecorp/evil"],
    )
    issues = TerraformCollectionImporter(root, operator_trust_data_path=_TRUST_DATA).import_collection()
    messages = " ".join(i.message for i in issues)
    assert "somecorp/evil" in messages, f"expected providers.yaml untrusted error; got {issues}"
    assert any(i.severity == "error" for i in issues), issues


def test_import_issue_is_dataclass() -> None:
    issue = ImportIssue(severity="error", message="boom")
    assert issue.severity == "error"
    assert issue.message == "boom"


def test_example_tag_enforcement_policy_uses_plan_json_shape() -> None:
    """example_tag_enforcement.rego MUST use the terraform-plan JSON input path
    (input.planned_values.root_module.resources[_]) — NOT input.resource[_].
    Guards against the regression where the example diverged from core.rego's
    shape and silently never matched anything."""
    text = _EXAMPLE_POLICY.read_text(encoding="utf-8")
    assert "input.resource[" not in text, (
        "example_tag_enforcement.rego must not use the bogus `input.resource[_]` path; "
        "it must use input.planned_values.root_module.resources[_] (and child_modules) "
        "to match core.rego's shape."
    )
    assert "input.planned_values.root_module.resources[_]" in text, (
        "example_tag_enforcement.rego must reference the canonical plan-JSON path."
    )


def test_example_tag_enforcement_policy_fires_on_missing_environment_tag() -> None:
    """Runs the shipped example policy against the example_tag_enforcement fixture.

    Skips cleanly when conftest/opa are absent. The fixture contains one bucket
    WITH Environment (passes) and one WITHOUT (denied), so a correct policy
    emits exactly one denial naming the missing tag.
    """
    if shutil.which("conftest") is None or shutil.which("opa") is None:
        pytest.skip("conftest/opa not installed")
    if not _EXAMPLE_TAG_FIXTURE.is_file():
        pytest.skip("example_tag_enforcement fixture not yet created")

    example_policy_dir = _EXAMPLE_POLICY.parent
    proc = subprocess.run(
        [
            "conftest",
            "test",
            "-p",
            str(example_policy_dir),
            str(_EXAMPLE_TAG_FIXTURE),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0, (
        f"expected the example policy to deny the missing-Environment bucket; "
        f"got exit 0.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    combined = proc.stdout + proc.stderr
    assert "missing tags.Environment" in combined, (
        f"expected 'missing tags.Environment' denial in:\n{combined}"
    )
    assert "aws_s3_bucket.with_env" not in combined, (
        f"the compliant bucket should NOT be denied:\n{combined}"
    )
