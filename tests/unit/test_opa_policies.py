"""OPA/conftest policy tests.

Covers:
- infra/terraform/policies/ (conftest-based Terraform policies)
- config/opa/ (OPA Rego policies: config_policy, terraform_policy, iam_policy)

Skips cleanly when `conftest` or `opa` are not on PATH so the suite remains
green in environments without those binaries installed.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_PROJECT = Path(__file__).resolve().parent.parent.parent
_POLICIES = _PROJECT / "infra" / "terraform" / "policies"
_FIXTURES = _PROJECT / "tests" / "fixtures" / "terraform"


# ---------------------------------------------------------------------------
# Binary availability — pytest.skip cleanly when conftest/opa are absent.
# ---------------------------------------------------------------------------

def _require_conftest() -> None:
    if shutil.which("conftest") is None:
        pytest.skip("conftest not installed")
    if shutil.which("opa") is None:
        pytest.skip("opa not installed")


def _skip_without_conftest() -> None:
    _require_conftest()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_conftest(fixture_path: Path) -> subprocess.CompletedProcess[str]:
    """Run `conftest test -p <policies> <fixture>` and return the completed process."""
    return subprocess.run(
        ["conftest", "test", "-p", str(_POLICIES), str(fixture_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def _fixture(name: str) -> Path:
    return _FIXTURES / name / "tfplan.json"


# ---------------------------------------------------------------------------
# Provider version pinning
# ---------------------------------------------------------------------------

def test_provider_version_pinned_pass() -> None:
    _skip_without_conftest()
    proc = _run_conftest(_fixture("provider_pinned_pass"))
    assert proc.returncode == 0, f"expected pass, got:\n{proc.stdout}\n{proc.stderr}"


def test_provider_version_unpinned_fail() -> None:
    _skip_without_conftest()
    proc = _run_conftest(_fixture("provider_unpinned_fail"))
    assert proc.returncode != 0, "expected denials, got exit 0"
    assert "not pinned" in proc.stdout, f"missing 'not pinned' in:\n{proc.stdout}"


# ---------------------------------------------------------------------------
# S3 ACL
# ---------------------------------------------------------------------------

def test_s3_public_read_fail() -> None:
    _skip_without_conftest()
    proc = _run_conftest(_fixture("s3_public_read_fail"))
    assert proc.returncode != 0, "expected denials, got exit 0"
    assert "public-read" in proc.stdout, f"missing 'public-read' in:\n{proc.stdout}"


# ---------------------------------------------------------------------------
# Security group open-port rules
# ---------------------------------------------------------------------------

def test_sg_ssh_open_fail() -> None:
    _skip_without_conftest()
    proc = _run_conftest(_fixture("sg_ssh_open_fail"))
    assert proc.returncode != 0, "expected denials, got exit 0"
    assert "0.0.0.0/0 on port 22" in proc.stdout, (
        f"missing '0.0.0.0/0 on port 22' in:\n{proc.stdout}"
    )


def test_sg_http_open_pass() -> None:
    _skip_without_conftest()
    proc = _run_conftest(_fixture("sg_http_open_pass"))
    assert proc.returncode == 0, f"expected pass, got:\n{proc.stdout}\n{proc.stderr}"


# ---------------------------------------------------------------------------
# Required tags
# ---------------------------------------------------------------------------

def test_missing_tags_fail() -> None:
    _skip_without_conftest()
    proc = _run_conftest(_fixture("missing_tags_fail"))
    assert proc.returncode != 0, "expected denials, got exit 0"
    assert "missing tags.Project" in proc.stdout, (
        f"missing 'missing tags.Project' in:\n{proc.stdout}"
    )


# ---------------------------------------------------------------------------
# Secret leak in user_data
# ---------------------------------------------------------------------------

def test_aws_key_leak_in_attribute_fail() -> None:
    _skip_without_conftest()
    proc = _run_conftest(_fixture("aws_key_leak_fail"))
    assert proc.returncode != 0, "expected denials, got exit 0"
    assert "AWS access key id" in proc.stdout, (
        f"missing 'AWS access key id' in:\n{proc.stdout}"
    )


# ---------------------------------------------------------------------------
# Provider trust list
# ---------------------------------------------------------------------------

def test_untrusted_provider_fail() -> None:
    _skip_without_conftest()
    proc = _run_conftest(_fixture("untrusted_provider_fail"))
    assert proc.returncode != 0, "expected denials, got exit 0"
    assert "not in the operator trust list" in proc.stdout, (
        f"missing 'not in the operator trust list' in:\n{proc.stdout}"
    )


# ---------------------------------------------------------------------------
# Whole-stack smoke — vsphere-vllm must be compliant
# ---------------------------------------------------------------------------

_VSPHERE_STACK = _PROJECT / "infra" / "terraform" / "stacks" / "vsphere-vllm"
_DUMMY_TFVARS = """
vsphere_user = "x"
vsphere_password = "x"
vsphere_server = "x"
datacenter = "x"
cluster = "x"
datastore = "x"
network = "x"
model_name = "x"
""".strip()


def test_core_policies_do_not_block_compliant_stack(tmp_path: Path) -> None:
    _skip_without_conftest()
    if shutil.which("terraform") is None:
        pytest.skip("terraform not installed")

    stack = _VSPHERE_STACK
    (stack / "testing.auto.tfvars").write_text(_DUMMY_TFVARS, encoding="utf-8")
    try:
        init = subprocess.run(
            ["terraform", "init", "-backend=false", "-input=false"],
            cwd=str(stack),
            capture_output=True,
            text=True,
            check=False,
        )
        if init.returncode != 0:
            pytest.skip(f"terraform init failed (no provider cache / offline):\n{init.stderr}")
        plan_path = tmp_path / "vs.tfplan"
        plan = subprocess.run(
            ["terraform", "plan", f"-out={plan_path}", "-input=false"],
            cwd=str(stack),
            capture_output=True,
            text=True,
            check=False,
        )
        if plan.returncode != 0:
            pytest.skip(
                f"terraform plan failed (no vSphere credentials in CI):\n{plan.stderr}"
            )
        json_path = tmp_path / "vs.tfplan.json"
        show = subprocess.run(
            ["terraform", "show", "-json", str(plan_path)],
            capture_output=True,
            text=True,
            check=True,
        )
        json_path.write_text(show.stdout, encoding="utf-8")
    finally:
        with contextlib.suppress(FileNotFoundError):
            (stack / "testing.auto.tfvars").unlink()

    proc = _run_conftest(json_path)
    assert proc.returncode == 0, (
        f"compliant stack produced denials:\n{proc.stdout}\n{proc.stderr}"
    )


# ---------------------------------------------------------------------------
# Fixture sanity — independent of conftest availability
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "scenario",
    [
        "provider_pinned_pass",
        "provider_unpinned_fail",
        "s3_public_read_fail",
        "sg_ssh_open_fail",
        "sg_http_open_pass",
        "missing_tags_fail",
        "aws_key_leak_fail",
        "untrusted_provider_fail",
    ],
)
def test_fixture_is_valid_json(scenario: str) -> None:
    """All hand-written fixtures must parse as valid JSON (no conftest needed)."""
    data: Any = json.loads(_fixture(scenario).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "planned_values" in data
    assert "configuration" in data


# ---------------------------------------------------------------------------
# OPA Rego policy tests — config/opa/
# ---------------------------------------------------------------------------

_OPA_POLICIES = _PROJECT / "config" / "opa"


def _opa_available() -> bool:
    return shutil.which("opa") is not None


def _run_opa_test() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["opa", "test", str(_OPA_POLICIES), "-v"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_OPA_POLICIES),
    )


def _opa_pass_markers(output: str) -> set[str]:
    """Extract passed rule names from both supported OPA verbose formats."""
    markers: set[str] = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("PASS: data."):
            markers.add(line.removeprefix("PASS: ").split()[0])
        elif line.startswith("data.") and ": PASS" in line:
            markers.add(line.split(": PASS", maxsplit=1)[0])
    return markers


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("PASS: data.example.test_rule\n", "data.example.test_rule"),
        ("data.example.test_rule: PASS (1.2ms)\n", "data.example.test_rule"),
    ],
)
def test_opa_pass_markers_supports_verbose_formats(output: str, expected: str) -> None:
    assert _opa_pass_markers(output) == {expected}


def test_opa_all_policies_pass() -> None:
    """opa test config/opa/ must exit 0 with all tests passing."""
    if not _opa_available():
        pytest.skip("opa not installed")
    proc = _run_opa_test()
    assert proc.returncode == 0, (
        f"opa test failed (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
    )


def test_opa_config_policy_tests_pass() -> None:
    """config_policy_test.rego tests must all pass."""
    if not _opa_available():
        pytest.skip("opa not installed")
    proc = _run_opa_test()
    passed = _opa_pass_markers(proc.stdout)
    for marker in [
        "data.hottentot.config.test_guardrail_layers_valid",
        "data.hottentot.config.test_tdd_enforced",
        "data.hottentot.config.test_commit_after_green",
        "data.hottentot.config.test_command_patterns_valid",
        "data.hottentot.config.test_stop_conditions_valid",
    ]:
        assert marker in passed, (
            f"Expected PASS for {marker} not in:\n{proc.stdout}"
        )


def test_opa_terraform_policy_tests_pass() -> None:
    """terraform_policy_test.rego tests must all pass."""
    if not _opa_available():
        pytest.skip("opa not installed")
    proc = _run_opa_test()
    passed = _opa_pass_markers(proc.stdout)
    terraform_tests = [
        "data.terraform_test.test_deny_untagged_resource",
        "data.terraform_test.test_allow_tagged_resource",
        "data.terraform_test.test_allow_existing_resource_no_tags",
        "data.terraform_test.test_deny_security_group_rule_no_description",
        "data.terraform_test.test_allow_security_group_rule_with_description",
        "data.terraform_test.test_deny_s3_public_acls_not_blocked",
        "data.terraform_test.test_allow_s3_block_public_acls",
        "data.terraform_test.test_deny_rds_no_encryption",
        "data.terraform_test.test_allow_rds_encrypted",
        "data.terraform_test.test_deny_iam_wildcard_action",
        "data.terraform_test.test_allow_iam_scoped_policy",
    ]
    for marker in terraform_tests:
        assert marker in passed, (
            f"Expected PASS for {marker} not in:\n{proc.stdout}"
        )


def test_opa_iam_policy_tests_pass() -> None:
    """iam_policy_test.rego tests must all pass."""
    if not _opa_available():
        pytest.skip("opa not installed")
    proc = _run_opa_test()
    iam_tests = [
        "data.iam_test.test_deny_admin_access",
        "data.iam_test.test_allow_scoped_policy",
        "data.iam_test.test_deny_wildcard_rds",
        "data.iam_test.test_deny_mfa_missing_for_create_user",
        "data.iam_test.test_allow_create_user_with_mfa",
        "data.iam_test.test_azure_scope_is_required",
        "data.iam_test.test_azure_scoped_assignment_is_valid",
        "data.iam_test.test_azure_data_plane_denied",
        "data.iam_test.test_azure_custom_role_valid",
    ]
    passed = _opa_pass_markers(proc.stdout)
    for marker in iam_tests:
        assert marker in passed, (
            f"Expected PASS for {marker} not in:\n{proc.stdout}"
        )


def test_opa_total_pass_count() -> None:
    """Verify total test pass count across all OPA test files."""
    if not _opa_available():
        pytest.skip("opa not installed")
    proc = _run_opa_test()
    passed = _opa_pass_markers(proc.stdout)
    assert len(passed) >= 21, (
        f"Expected >=21 passed rules, got {len(passed)}:\n{proc.stdout}"
    )
