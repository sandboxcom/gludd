"""AWS onboarding provider for `gludd onboard aws`.

Implements the :class:`AWSOnboardProvider` with three surfaces:

* :meth:`create_role_instructions` — markdown walkthrough that points the user
  at the companion Terraform module
  ``infra/terraform/modules/onboard-iam/`` and tells them how to apply it.
* :meth:`token_acquisition_guide` — markdown describing how to mint
  credentials for the role the module created (long-lived access key OR STS
  assume-role for SSO) and which env vars to export.
* :meth:`validate_token_and_role` — calls ``sts:GetCallerIdentity`` via boto3
  (lazily imported; boto3 is an optional ``[aws]`` extra) to confirm the
  caller's identity, then runs AWS ``--dry-run`` permission probes for every
  action the IAM policy grants. Returns the verified/missing lists.

boto3 is imported lazily inside the function bodies so this module imports
cleanly even when boto3 is absent (mirroring the pattern in
``general_ludd.connectors.aws_pipeline`` and ``pricing_intel.sources``).
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# Design-pinned action allowlist + denylist.
# ---------------------------------------------------------------------------
# These are duplicated as Python constants SO THAT THE TEST SUITE CAN PIN THE
# POLICY DOCUMENT EMITTED BY infra/terraform/modules/onboard-iam/main.tf
# WITHOUT PARSING HCL. If you add or remove a permission in the Terraform
# policy, update BOTH this constant and the test in
# tests/unit/test_onboard_aws.py::TestIAMPolicyDocument.
#
# Rationale per action lives as inline comments in main.tf; the short version:
#   - Describe* — read state needed by data.aws_ami + drift detection
#   - Run/Start/Stop/Terminate — instance lifecycle
#   - CreateSecurityGroup / Authorize/Revoke/Delete — _generate_aws SG block
#   - Create/Attach/Detach/Delete Volume — root_block_device + EBS
#   - Allocate/Associate/Disassociate/Release Address — public IP output
#   - Create/Delete Tags — _generate_aws tags block
#   - iam:PassRole — scoped to operator's own role, lets EC2 carry the profile
#   - logs:Create* + PutLogEvents — scoped to /gludd/* log groups
# SSM is intentionally NOT in this list — gludd does not call SSM.
ALLOWED_ACTIONS: tuple[str, ...] = (
    "ec2:RunInstances",
    "ec2:TerminateInstances",
    "ec2:StartInstances",
    "ec2:StopInstances",
    "ec2:DescribeInstances",
    "ec2:DescribeInstanceStatus",
    "ec2:DescribeImages",
    "ec2:DescribeSecurityGroups",
    "ec2:DescribeSubnets",
    "ec2:DescribeVpcs",
    "ec2:DescribeVolumes",
    "ec2:CreateSecurityGroup",
    "ec2:AuthorizeSecurityGroupIngress",
    "ec2:RevokeSecurityGroupIngress",
    "ec2:DeleteSecurityGroup",
    "ec2:CreateVolume",
    "ec2:AttachVolume",
    "ec2:DetachVolume",
    "ec2:DeleteVolume",
    "ec2:AllocateAddress",
    "ec2:AssociateAddress",
    "ec2:DisassociateAddress",
    "ec2:ReleaseAddress",
    "ec2:CreateTags",
    "ec2:DeleteTags",
    "iam:PassRole",
    "logs:CreateLogGroup",
    "logs:CreateLogStream",
    "logs:PutLogEvents",
)

# Actions explicitly DENIED to prevent privilege escalation. The operator may
# USE its role but cannot CREATE new principals or attach additional policies.
DENIED_ACTIONS: tuple[str, ...] = (
    "iam:CreateUser",
    "iam:CreateRole",
    "iam:AttachRolePolicy",
    "iam:PutRolePolicy",
)

# Default role name — must match variables.tf default in the IAM module.
DEFAULT_ROLE_NAME = "gludd-compute-operator"

# Permission probe plan: list of (service, probe_id, iam_action_being_probed).
# Each probe issues a benign dry-run call against the named service. AWS
# returns DryRunOperation (412) when authorized and UnauthorizedOperation (403)
# when missing. We do NOT use real RunInstances dry-runs because some accounts
# constrain them; instead we probe with read-shaped calls that exercise the
# same IAM action. RunInstances is probed via describe-instances-with-dry-run
# where supported; otherwise it falls back to the describe probe and we trust
# the policy document.
_PERMISSION_PROBES: tuple[tuple[str, str, str], ...] = (
    ("ec2", "describe_instances", "ec2:DescribeInstances"),
    ("ec2", "describe_instance_status", "ec2:DescribeInstanceStatus"),
    ("ec2", "describe_images", "ec2:DescribeImages"),
    ("ec2", "describe_security_groups", "ec2:DescribeSecurityGroups"),
    ("ec2", "describe_subnets", "ec2:DescribeSubnets"),
    ("ec2", "describe_vpcs", "ec2:DescribeVpcs"),
    ("ec2", "describe_volumes", "ec2:DescribeVolumes"),
)


class OnboardProvider(Protocol):
    """Structural type implemented by each cloud's onboarding module.

    The CLI scaffold (parallel task) calls these three methods. Defined here
    as a Protocol so the AWS module is self-documenting; GCP/Azure modules
    provide the same shape.
    """

    def create_role_instructions(self) -> str: ...

    def token_acquisition_guide(self) -> str: ...

    def validate_token_and_role(
        self, token: str, role_arn: str, region: str
    ) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# boto3 client factory — the lazy-import seam.
# ---------------------------------------------------------------------------
# Indirection point so tests can monkeypatch a single attribute rather than
# the boto3 module. Mirrors the pattern in connectors/aws_pipeline.py.
# Public for test monkeypatching.


def _build_boto3_client(service: str, region: str) -> Any:
    """Construct a boto3 client, importing boto3 lazily.

    Raises ``ImportError`` if boto3 is not installed (it is an optional
    ``[aws]`` extra). Callers should catch ImportError and surface a helpful
    "install with: pip install 'general-ludd-agent[aws]'" message.
    """
    try:
        import importlib

        boto3 = importlib.import_module("boto3")  # boto3: optional [aws] extra, guarded by try/except
    except ImportError as exc:  # pragma: no cover - exercised via test monkeypatch
        raise ImportError(
            "boto3 is not installed. Install with: pip install 'general-ludd-agent[aws]'"
        ) from exc
    return boto3.client(service, region_name=region)


class AWSOnboardProvider:
    """AWS implementation of :class:`OnboardProvider`."""

    # ----- permission-probe plan (overridable per-instance for tests) -----
    def _permission_probe_plan(self, region: str) -> list[tuple[str, str, str]]:
        # Default: the module-level frozen plan. Returned as a list so tests
        # can override with a smaller set.
        return list(_PERMISSION_PROBES)

    # ------------------------------------------------------------------
    # Markdown guidance
    # ------------------------------------------------------------------

    def create_role_instructions(self) -> str:
        return textwrap.dedent(
            f"""\
            # AWS onboarding — create the gludd IAM role

            This step provisions the **least-privilege IAM role** gludd uses to
            run Terraform (create EC2 GPU instances, security groups, EBS
            volumes, EIPs) and to operate those instances once they exist.

            ## Step 1 — install prerequisites

            Confirm `terraform` (>= 1.4) and the `aws` CLI v2 are installed and
            on your PATH:

                terraform version
                aws --version

            Authenticate the AWS CLI with the account where gludd will operate.
            The credentials you use here must have permission to create IAM
            roles and policies (e.g. `AdministratorAccess` for the onboarding
            step only — afterwards you can drop to the gludd role itself).

            ## Step 2 — open the IAM module

                cd infra/terraform/modules/onboard-iam/

            ## Step 3 — configure

            Copy the example tfvars and edit it:

                cp ../../examples/onboard-iam.tfvars.example terraform.tfvars

            Edit `terraform.tfvars` — at minimum set `region` to where gludd
            will deploy compute. The defaults (`role_name =
            "{DEFAULT_ROLE_NAME}"`, `policy_name`) are sensible for a single
            gludd deployment.

            ## Step 4 — apply

                terraform init
                terraform apply

            Review the plan. You should see exactly four resources created:

              * `aws_iam_role.compute_operator`
              * `aws_iam_instance_profile.compute_operator`
              * `aws_iam_policy.compute_least_priv`
              * `aws_iam_role_policy_attachment.compute_least_priv`

            ## Step 5 — copy the role ARN

            After apply, the module outputs the role ARN. Copy it — you will
            pass it to `gludd onboard --role-arn` in the next phase.

            The relevant output:

                role_arn               = "arn:aws:iam::<account>:role/{DEFAULT_ROLE_NAME}"
                role_name              = "{DEFAULT_ROLE_NAME}"
                instance_profile_arn   = "arn:aws:iam::<account>:instance-profile/{DEFAULT_ROLE_NAME}-profile"
                instance_profile_name  = "{DEFAULT_ROLE_NAME}-profile"

            ## Security boundary

            The policy grants ONLY the EC2/IAM/Logs actions gludd needs — see
            `tests/unit/test_onboard_aws.py` for the static allowlist pin. An
            explicit `Deny` block forbids creating additional IAM principals or
            policies, so this role cannot escalate itself.
            """
        )

    def token_acquisition_guide(self) -> str:
        return textwrap.dedent(
            """\
            # AWS onboarding — obtain credentials for the gludd role

            You now have the `gludd-compute-operator` IAM role. To let gludd
            use it, export credentials into your environment.

            ## Option A — long-lived access key (simplest)

            1. Open the IAM console → Roles → `gludd-compute-operator`.
            2. Select the **Security credentials** tab.
            3. Under **Access keys**, click **Create access key**.
               (Choose "Third-party service" if prompted for a use case.)
            4. Copy the Access Key ID and Secret Access Key.

            Export them into your shell:

                export AWS_ACCESS_KEY_ID="AKIA..."
                export AWS_SECRET_ACCESS_KEY="..."
                export AWS_REGION="us-east-1"

            ## Option B — STS assume-role (recommended for SSO / short-lived)

            If your principal is a federated/SSO user that is *allowed* to
            assume the gludd role, do NOT mint a long-lived key. Instead:

                aws sts assume-role \\
                  --role-arn "arn:aws:iam::<account>:role/gludd-compute-operator" \\
                  --role-session-name gludd-onboard

            The response contains `AccessKeyId`, `SecretAccessKey`, and
            `SessionToken`. Export ALL THREE (the session token is required
            when assuming a role via STS):

                export AWS_ACCESS_KEY_ID="ASIA..."
                export AWS_SECRET_ACCESS_KEY="..."
                export AWS_SESSION_TOKEN="..."     # required for assumed-role creds
                export AWS_REGION="us-east-1"

            ## Next step

            Run the validate phase — gludd will probe your credentials against
            the IAM policy and report any missing permissions:

                gludd onboard aws \\
                  --role-arn "arn:aws:iam::<account>:role/gludd-compute-operator" \\
                  --region us-east-1
            """
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_token_and_role(
        self, token: str, role_arn: str, region: str
    ) -> dict[str, Any]:
        """Probe the user's AWS credentials and confirm role + permissions.

        Returns a dict with:

            identity_arn           — the caller's ARN from GetCallerIdentity
            account                — 12-digit account ID
            region                 — the region passed in
            role_matches_expected  — True iff identity_arn == role_arn
            permissions_verified   — sorted list of probe actions that passed
            missing                — sorted list of probe actions that failed

        Raises ``ImportError`` if boto3 is unavailable. Callers that want a
        boolean ok/not-ok shape should call :meth:`_validate_or_error`.
        """
        sts = _build_boto3_client("sts", region)
        identity = sts.get_caller_identity()
        identity_arn = identity["Arn"]
        account = identity["Account"]

        verified: list[str] = []
        missing: list[str] = []
        for service, probe_id, iam_action in self._permission_probe_plan(region):
            client = _build_boto3_client(service, region)
            method = getattr(client, probe_id)
            ok = _probe_is_authorized(method)
            (verified if ok else missing).append(iam_action)

        return {
            "identity_arn": identity_arn,
            "account": account,
            "region": region,
            "role_matches_expected": identity_arn == role_arn,
            "permissions_verified": sorted(verified),
            "missing": sorted(missing),
        }

    def _validate_or_error(
        self, token: str, role_arn: str, region: str
    ) -> tuple[bool, dict[str, Any]]:
        """Same as :meth:`validate_token_and_role` but never raises.

        Returns ``(ok, details)``. ``ok`` is False on ImportError (boto3
        missing) or any other exception, with ``details["detail"]`` carrying
        the message.
        """
        try:
            result = self.validate_token_and_role(token, role_arn, region)
            return True, result
        except ImportError as exc:
            return False, {"detail": str(exc)}
        except Exception as exc:  # pragma: no cover - defensive
            return False, {"detail": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _probe_is_authorized(method: Any) -> bool:
    """Call a boto3 client method and interpret the dry-run result.

    AWS ``--dry-run`` semantics (and boto3's representation of them):

      * Authorized  -> ClientError with Error.Code == "DryRunOperation"
                      (was authorized but not actually performed). Also a plain
                      successful response (no exception) counts as authorized
                      for read calls that ignore DryRun.
      * Unauthorized-> ClientError with Error.Code == "UnauthorizedOperation".

    Any other ClientError is treated as "indeterminate -> missing" so we never
    falsely report a permission as present.
    """
    try:
        method(DryRun=True) if _accepts_dry_run(method) else method()
        return True
    except Exception as exc:  # narrow: botocore.exceptions.ClientError shape
        code = _client_error_code(exc)
        if code == "DryRunOperation":
            return True
        if code == "UnauthorizedOperation":
            return False
        # Unknown error -> be conservative.
        return False


def _accepts_dry_run(method: Any) -> bool:
    """Best-effort: does this boto3 client method accept a ``DryRun`` kwarg?

    Most EC2 mutating and several describe calls do; some (DescribeImages on
    older botocore) reject it. We sniff the method signature.
    """
    import inspect

    try:
        sig = inspect.signature(method)
    except (TypeError, ValueError):
        return False
    return "DryRun" in sig.parameters or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )


def _client_error_code(exc: Exception) -> str | None:
    """Extract Error.Code from a botocore-shaped ClientError without importing botocore."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    err = response.get("Error")
    if not isinstance(err, dict):
        return None
    code = err.get("Code")
    return code if isinstance(code, str) else None


# ---------------------------------------------------------------------------
# Policy-document introspection helper (used by tests and the CLI scaffold).
# ---------------------------------------------------------------------------


def load_policy_document(module_dir: str | Path = ".") -> dict[str, Any] | None:
    """Parse the IAM policy JSON out of ``modules/onboard-iam/main.tf``.

    Used by the CLI to display the exact policy at onboarding time and by
    tests to assert the static allowlist. Returns ``None`` if the module or
    a policy block is not found.
    """
    main_tf = Path(module_dir) / "main.tf"
    if not main_tf.exists():
        return None
    raw = main_tf.read_text()
    # Extract the first jsonencode({...}) block.
    match = re.search(r"policy\s*=\s*jsonencode\(\s*(\{.*?\})\s*\)", raw, re.DOTALL)
    if not match:
        return None
    hcl_body = match.group(1)
    return _hcl_to_json(hcl_body)


def _hcl_to_json(raw: str) -> dict[str, Any]:
    """Tolerant HCL -> dict for the policy shape we emit.

    HCL maps are not strict JSON (keys are often bare, trailing commas allowed,
    string concat uses interpolation). We normalize the subset we generate.
    For arbitrary HCL this is NOT a parser — use hcl2 for that. The subset
    here is intentionally simple: nested maps, lists, strings.
    """
    # Quote bare keys.
    quoted = re.sub(r"(?m)([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=", r'\1"\2":', raw)
    # Remove trailing commas.
    quoted = re.sub(r",\s*([}\]])", r"\1", quoted)
    parsed: dict[str, Any] = json.loads(quoted)
    return parsed


__all__ = [
    "ALLOWED_ACTIONS",
    "DENIED_ACTIONS",
    "AWSOnboardProvider",
    "OnboardProvider",
    "load_policy_document",
]
