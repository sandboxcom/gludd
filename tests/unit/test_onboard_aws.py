"""Unit tests for the AWS onboarding provider (`gludd onboard aws`).

These tests pin three surfaces:

1. The markdown guidance strings (role-creation instructions + token
   acquisition guide) that the user reads when running `gludd onboard aws`.
2. The `validate_token_and_role` entry point — its boto3 call shape and its
   dry-run permission-probe result aggregation.
3. The IAM policy document emitted by the companion Terraform module at
   ``infra/terraform/modules/onboard-iam/main.tf`` — asserting least-privilege
   (allowlist of actions, explicit deny on role creation).
"""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from general_ludd.onboard.aws import (
    ALLOWED_ACTIONS,
    DENIED_ACTIONS,
    AWSOnboardProvider,
    _accepts_dry_run,
    _build_boto3_client,
    _client_error_code,
    _probe_is_authorized,
    load_policy_document,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_DIR = REPO_ROOT / "infra" / "terraform" / "modules" / "onboard-iam"
MAIN_TF = MODULE_DIR / "main.tf"
POLICY_JSON = MODULE_DIR / "policy.json"
ASSUME_ROLE_JSON = MODULE_DIR / "assume-role-policy.json"


# ---------------------------------------------------------------------------
# Guidance strings
# ---------------------------------------------------------------------------


class TestCreateRoleInstructions:
    def test_mentions_terraform_apply(self) -> None:
        text = AWSOnboardProvider().create_role_instructions()
        assert "terraform apply" in text.lower()

    def test_mentions_role_arn_output(self) -> None:
        text = AWSOnboardProvider().create_role_instructions()
        assert "role_arn" in text
        assert "output" in text.lower() or "outputs" in text.lower()

    def test_mentions_module_path(self) -> None:
        text = AWSOnboardProvider().create_role_instructions()
        assert "infra/terraform/modules/onboard-iam" in text

    def test_mentions_tfvars_example(self) -> None:
        text = AWSOnboardProvider().create_role_instructions()
        assert "onboard-iam.tfvars.example" in text


class TestTokenAcquisitionGuide:
    def test_mentions_env_vars(self) -> None:
        text = AWSOnboardProvider().token_acquisition_guide()
        for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"):
            assert var in text, f"missing {var} in token guide"

    def test_mentions_sts_assume_role(self) -> None:
        text = AWSOnboardProvider().token_acquisition_guide()
        assert "aws sts assume-role" in text
        assert "--role-arn" in text

    def test_mentions_session_token_for_sts(self) -> None:
        text = AWSOnboardProvider().token_acquisition_guide()
        assert "AWS_SESSION_TOKEN" in text


def test_build_boto3_client_uses_requested_service_and_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_client = object()
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = expected_client
    monkeypatch.setattr(importlib, "import_module", lambda _name: fake_boto3)

    assert _build_boto3_client("ec2", "us-east-1") is expected_client
    fake_boto3.client.assert_called_once_with("ec2", region_name="us-east-1")


# ---------------------------------------------------------------------------
# validate_token_and_role — boto3 call shape + permission probe
# ---------------------------------------------------------------------------


class _ClientErrorFallback(Exception):
    """Stand-in for ``botocore.exceptions.ClientError`` when botocore is absent.

    Botocore ships no inline type stubs; the ``import-not-found`` suppression
    on the lazy import below is unavoidable until ``types-botocore`` is added.
    Defining the fallback at module scope (rather than inline in the ``except``
    branch) means the ``ClientError`` name is bound by an assignment in the
    fallback path, not a class definition — so there is no ``no-redef``.
    """

    def __init__(
        self,
        error_response: dict[str, dict[str, str]],
        operation_name: str,
    ) -> None:
        super().__init__(error_response.get("Error", {}).get("Message", ""))
        self.response = error_response
        self.operation_name = operation_name


class _ResponseError(RuntimeError):
    def __init__(self, response: object) -> None:
        super().__init__("malformed")
        self.response = response


def _fake_client_error(code: str, message: str = "probe") -> Exception:
    """Build a botocore-shaped ClientError for tests (without importing botocore)."""
    return _ClientErrorFallback(
        {"Error": {"Code": code, "Message": message}}, "DryRun"
    )


@pytest.fixture()
def fake_sts_client() -> MagicMock:
    client = MagicMock()
    client.get_caller_identity.return_value = {
        "UserId": "AIDAEXAMPLE",
        "Account": "123456789012",
        "Arn": "arn:aws:iam::123456789012:role/gludd-compute-operator",
    }
    return client


class TestValidateToken:
    def test_calls_sts_get_caller_identity(
        self, fake_sts_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.onboard.aws._build_boto3_client",
            lambda service, region: fake_sts_client if service == "sts" else MagicMock(),
        )

        result = AWSOnboardProvider().validate_token_and_role(
            token="ignored",
            role_arn="arn:aws:iam::123456789012:role/gludd-compute-operator",
            region="us-east-1",
        )

        fake_sts_client.get_caller_identity.assert_called_once()
        assert result["identity_arn"].startswith("arn:aws:iam::")
        assert result["account"] == "123456789012"
        assert result["region"] == "us-east-1"

    def test_returns_missing_permissions_list(
        self, fake_sts_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Probes return DryRunOperation (authorized) or UnauthorizedOperation (missing)."""
        ec2_client = MagicMock()
        ec2_client.describe_instances.side_effect = [
            {"Reservations": []},  # first probe: success -> authorized
            _fake_client_error("UnauthorizedOperation", "not authorized"),  # second: missing
        ]

        def _factory(service: str, region: str) -> MagicMock:
            if service == "sts":
                return fake_sts_client
            if service == "ec2":
                return ec2_client
            return MagicMock()

        monkeypatch.setattr("general_ludd.onboard.aws._build_boto3_client", _factory)

        provider = AWSOnboardProvider()
        # Two probes, both routed through describe_instances via side_effect.
        monkeypatch.setattr(
            provider,
            "_permission_probe_plan",
            lambda region: [
                ("ec2", "describe_instances", "ec2:DescribeInstances"),
                ("ec2", "describe_instances", "ec2:RunInstances"),
            ],
        )

        result = provider.validate_token_and_role(
            token="ignored",
            role_arn="arn:aws:iam::123456789012:role/gludd-compute-operator",
            region="us-east-1",
        )

        assert "ec2:RunInstances" in result["missing"]
        assert "ec2:DescribeInstances" in result["permissions_verified"]
        assert isinstance(result["missing"], list)
        assert result["role_matches_expected"] is True

    def test_role_mismatch_detected(
        self, fake_sts_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_sts_client.get_caller_identity.return_value = {
            "UserId": "AIDAEXAMPLE",
            "Account": "123456789012",
            "Arn": "arn:aws:iam::123456789012:role/some-other-role",
        }
        monkeypatch.setattr(
            "general_ludd.onboard.aws._build_boto3_client",
            lambda service, region: fake_sts_client,
        )

        result = AWSOnboardProvider().validate_token_and_role(
            token="ignored",
            role_arn="arn:aws:iam::123456789012:role/gludd-compute-operator",
            region="us-east-1",
        )
        assert result["role_matches_expected"] is False

    def test_boto3_unavailable_returns_ok_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(service: str, region: str) -> object:
            raise ImportError("boto3 unavailable")

        monkeypatch.setattr("general_ludd.onboard.aws._build_boto3_client", _raise)

        ok, details = AWSOnboardProvider()._validate_or_error(
            token="x", role_arn="arn:aws:iam::1:role/x", region="us-east-1"
        )
        assert ok is False
        assert "boto3" in details["detail"].lower()


class TestPermissionProbeHelpers:
    def test_dry_run_operation_is_authorized(self) -> None:
        def _method(**_kwargs: object) -> None:
            raise _fake_client_error("DryRunOperation")

        assert _probe_is_authorized(_method) is True

    def test_unknown_client_error_fails_closed(self) -> None:
        def _method(**_kwargs: object) -> None:
            raise _fake_client_error("Throttled")

        assert _probe_is_authorized(_method) is False

    def test_uninspectable_callable_has_no_dry_run_contract(self) -> None:
        assert _accepts_dry_run(object()) is False

    @pytest.mark.parametrize(
        "response",
        [None, {"Error": "not-a-map"}, {"Error": {"Code": 403}}],
    )
    def test_malformed_client_error_code_is_rejected(self, response: object) -> None:
        assert _client_error_code(_ResponseError(response)) is None


class TestLoadPolicyDocument:
    def test_missing_module_returns_none(self, tmp_path: Path) -> None:
        assert load_policy_document(tmp_path) is None

    def test_module_without_jsonencode_policy_returns_none(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "main.tf").write_text('resource "aws_iam_policy" "empty" {}')

        assert load_policy_document(tmp_path) is None

    def test_hcl_policy_subset_is_normalized(self, tmp_path: Path) -> None:
        (tmp_path / "main.tf").write_text(
            'policy = jsonencode({ Version = "2012-10-17", Statement = [], })'
        )

        assert load_policy_document(tmp_path) == {
            "Version": "2012-10-17",
            "Statement": [],
        }


# ---------------------------------------------------------------------------
# IAM policy document (Terraform module) — least-privilege static checks
# ---------------------------------------------------------------------------


def _load_policy() -> dict[str, Any]:
    """Load the IAM policy JSON shipped with the module.

    The policy document lives in ``policy.json`` (pure JSON, not HCL) so it
    is statically machine-auditable without parsing Terraform. main.tf reads
    the same file at apply time and substitutes the operator role ARN.
    """
    return cast(dict[str, Any], json.loads(POLICY_JSON.read_text()))


def _all_actions(policy: dict[str, Any]) -> set[str]:
    actions: set[str] = set()
    for stmt in policy.get("Statement", []):
        a = stmt.get("Action", [])
        if isinstance(a, str):
            actions.add(a)
        else:
            actions.update(a)
    return actions


class TestIAMPolicyDocument:
    def test_module_files_exist(self) -> None:
        for name in ("main.tf", "variables.tf", "outputs.tf", "policy.json", "assume-role-policy.json"):
            assert (MODULE_DIR / name).exists(), f"missing {name}"

    def test_iam_policy_matches_design_allowlist(self) -> None:
        policy = _load_policy()

        allow_actions: set[str] = set()
        for stmt in policy.get("Statement", []):
            if stmt.get("Effect") == "Allow":
                acts = stmt.get("Action", [])
                acts = [acts] if isinstance(acts, str) else acts
                allow_actions.update(acts)

        expected = set(ALLOWED_ACTIONS)
        assert allow_actions == expected, (
            f"policy actions diverge from design.\n"
            f"  missing (in design, not in policy): {expected - allow_actions}\n"
            f"  extra (in policy, not in design):   {allow_actions - expected}"
        )

    def test_no_wildcard_actions(self) -> None:
        policy = _load_policy()
        for action in _all_actions(policy):
            assert "*" not in action, (
                f"least-privilege violation: wildcard action {action!r}"
            )

    def test_explicit_deny_on_role_creation(self) -> None:
        policy = _load_policy()
        deny_actions: set[str] = set()
        for stmt in policy.get("Statement", []):
            if stmt.get("Effect") == "Deny":
                deny_actions |= _all_actions({"Statement": [stmt]})
        for required in DENIED_ACTIONS:
            assert required in deny_actions, (
                f"missing explicit Deny for {required}; "
                "operator could escalate privileges"
            )

    def test_passrole_scoped_to_operator_role_only(self) -> None:
        policy = _load_policy()
        for stmt in policy.get("Statement", []):
            acts = stmt.get("Action", [])
            acts = [acts] if isinstance(acts, str) else acts
            if "iam:PassRole" in acts:
                resources = stmt.get("Resource", [])
                resources = [resources] if isinstance(resources, str) else resources
                # Resource must NOT be a bare wildcard — must scope to operator
                # role. The placeholder is substituted at apply time in main.tf;
                # here we assert the static contract.
                for r in resources:
                    assert r != "*", (
                        "iam:PassRole Resource is bare '*' — operator could "
                        "pass ANY role and escalate. Got: "
                        f"{resources!r}"
                    )

    def test_policy_placeholders_are_rendered_by_terraform(self) -> None:
        policy_text = POLICY_JSON.read_text()
        main_tf = MAIN_TF.read_text()
        assert "${operator_role_arn}" in policy_text
        assert "${operator_region}" in policy_text
        assert "templatefile(" in main_tf
        assert "operator_role_arn = aws_iam_role.compute_operator.arn" in main_tf
        assert "operator_region   = var.region" in main_tf

    def test_outputs_export_role_arn_and_profile(self) -> None:
        outputs_tf = (MODULE_DIR / "outputs.tf").read_text()
        for needle in ("role_arn", "instance_profile_arn", "instance_profile_name"):
            assert f'output "{needle}"' in outputs_tf, f"missing output {needle}"

    def test_role_has_ec2_assume_role_policy(self) -> None:
        # Trust policy is in assume-role-policy.json (pure JSON).
        trust = json.loads(ASSUME_ROLE_JSON.read_text())
        principals = trust["Statement"][0]["Principal"]
        assert "ec2.amazonaws.com" in principals.get("Service", ""), (
            "assume-role policy must allow ec2.amazonaws.com"
        )
        # Instance profile resource must exist in main.tf.
        assert "aws_iam_instance_profile" in MAIN_TF.read_text()

    def test_policy_json_is_valid_json(self) -> None:
        # Sanity: the policy file parses and has the IAM-document shape.
        policy = _load_policy()
        assert policy["Version"] == "2012-10-17"
        assert isinstance(policy["Statement"], list)
        assert len(policy["Statement"]) >= 8

    @pytest.mark.skipif(
        shutil.which("terraform") is None,
        reason="terraform binary not installed",
    )
    def test_iam_module_terraform_validate(self) -> None:
        # `terraform validate` requires init for provider-backed modules; the
        # IAM module only uses the aws provider, so init is cheap. Run inside
        # a copy so we don't litter the repo with .terraform/.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for name in (
                "main.tf", "variables.tf", "outputs.tf",
                "policy.json", "assume-role-policy.json",
            ):
                shutil.copy(MODULE_DIR / name, tmp_path / name)
            init = subprocess.run(
                ["terraform", "init", "-backend=false"],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert init.returncode == 0, f"terraform init failed:\n{init.stderr}"
            validate = subprocess.run(
                ["terraform", "validate"],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert validate.returncode == 0, (
                f"terraform validate failed:\n{validate.stderr}\n{validate.stdout}"
            )
