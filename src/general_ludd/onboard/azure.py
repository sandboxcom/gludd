"""Azure onboarding provider for ``gludd onboard azure``.

Provisions a least-privilege user-assigned managed identity
(``gludd-compute-operator``) and verifies the cloud-side credential before the
daemon tries to use it.

The custom ``General Ludd Accelerator Deployer`` role is limited to SKU/quota
preflight reads and the resource-group, network, VM, disk, and GPU-driver
extension operations used by the release Terraform stacks.  It can be assigned
either to the managed identity created by the onboarding module or to an
existing app/service-principal object id used outside Azure.

All Azure SDKs (``azure-identity``, ``azure-mgmt-compute``,
``azure-mgmt-authorization``) are lazy imports; install via the ``[azure]``
extra.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from typing import Any

# The exact least-privilege role set gludd needs on Azure. Mirrored into the
# Terraform module at infra/terraform/modules/onboard-iam-azure/main.tf and
# asserted by tests/unit/test_onboard_azure.py::TestTerraformModuleLeastPriv.
EXPECTED_ROLES: tuple[str, ...] = (
    "General Ludd Accelerator Deployer",
)

DEFAULT_IDENTITY_NAME = "gludd-compute-operator"
MODULE_REL_PATH = "infra/terraform/modules/onboard-iam-azure"
_INSTRUCTION_VALUE_RE = re.compile(r"^[A-Za-z0-9._@:/()<>-]+$")


def _validate_instruction_values(**values: str) -> None:
    """Reject characters that could escape generated shell command arguments."""

    for name, value in values.items():
        if not value or _INSTRUCTION_VALUE_RE.fullmatch(value) is None:
            raise ValueError(f"unsafe {name} for Azure onboarding instructions")


# ---------------------------------------------------------------------------
# Phase 1 — role / IAM provisioning instructions
# ---------------------------------------------------------------------------


def create_role_instructions(
    *,
    subscription_id: str,
    resource_group_name: str = "gludd-rg",
    location: str = "eastus",
    identity_name: str = DEFAULT_IDENTITY_NAME,
) -> str:
    """Return markdown walking the user through provisioning the managed identity."""
    _validate_instruction_values(
        subscription_id=subscription_id,
        resource_group_name=resource_group_name,
        location=location,
        identity_name=identity_name,
    )
    return f"""# Azure onboarding — IAM provisioning

This provisions a least-privilege user-assigned managed identity that gludd
uses to launch and tear down ephemeral GPU compute VMs in subscription
`{subscription_id}`.

## 1. Authenticate and target the subscription

```bash
az login
az account set --subscription {subscription_id}
```

## 2. Provision the IAM resources

Apply the `onboard-iam-azure` Terraform module. From the repository root:

```bash
cd {MODULE_REL_PATH}
terraform init
terraform apply \\
  -var="subscription_id={subscription_id}" \\
  -var="resource_group_name={resource_group_name}" \\
  -var="location={location}"
```

The module creates:

* `azurerm_user_assigned_identity` named `{identity_name}`
* the `General Ludd Accelerator Deployer` custom role, limited to SKU/quota
  reads plus the resource group, network, VM, disk, and NVIDIA driver extension
  operations used by gludd
* `azurerm_role_assignment` granting that role to the managed identity

No `Owner` or `Contributor` is granted.

For an existing app/service principal used outside Azure, pass its **object
id** (not application/client id) so the same deployment role is assigned:

```bash
terraform apply \\
  -var="subscription_id={subscription_id}" \\
  -var="operator_principal_id=<SERVICE_PRINCIPAL_OBJECT_ID>"
```

## 3. Capture the principal / client id

After `terraform apply` completes, the module emits `principal_id`, `client_id`,
and `tenant_id`:

```bash
az identity show \\
  --name {identity_name} \\
  --resource-group {resource_group_name}
```

Proceed to token acquisition (see `gludd onboard azure --phase token`).
"""


# ---------------------------------------------------------------------------
# Phase 2 — token / credential acquisition guide
# ---------------------------------------------------------------------------


def token_acquisition_guide() -> str:
    """Return markdown describing how to obtain credentials gludd consumes."""
    return """# Azure onboarding — token acquisition

gludd authenticates to Azure either via a service principal (app registration
+ client secret) or via the managed identity provisioned above.

## Option A — App registration + client secret (recommended for CI)

```bash
az ad app create --display-name gludd-operator
# capture the appId -> AZURE_CLIENT_ID

az ad sp create --id <AZURE_CLIENT_ID>

az ad app credential reset --id <AZURE_CLIENT_ID> \\
  --append
# capture the password -> AZURE_CLIENT_SECRET

# capture the service principal object id (different from the client id)
az ad sp show --id <AZURE_CLIENT_ID> --query id --output tsv
```

Reapply the onboarding module with that **object id**. This assigns the
`General Ludd Accelerator Deployer` role to the credential Terraform and gludd
will actually use:

```bash
cd infra/terraform/modules/onboard-iam-azure
terraform apply \\
  -var="subscription_id=<SUBSCRIPTION_ID>" \\
  -var="operator_principal_id=<SERVICE_PRINCIPAL_OBJECT_ID>"
```

## Option B — Managed identity (recommended for in-Azure runs)

Use the user-assigned identity created in the IAM step. Export its client id:

```bash
export AZURE_CLIENT_ID="<identity client id from terraform output>"
```

## Set the environment

gludd's Azure client reads the standard SDK env vars:

```bash
export AZURE_CLIENT_ID="<app or identity client id>"
export AZURE_CLIENT_SECRET="<app secret>"        # omit for managed identity
export AZURE_TENANT_ID="<tenant id>"
export AZURE_SUBSCRIPTION_ID="<subscription id>"
```

Then validate:

```bash
gludd onboard azure --phase validate \\
  --subscription-id "$AZURE_SUBSCRIPTION_ID" \\
  --resource-group gludd-rg
```

The client secret is a secret — store it in your secrets manager (OpenBao /
`gludd secrets`), never commit it.
"""


# ---------------------------------------------------------------------------
# Phase 3 — validation (live API probe)
# ---------------------------------------------------------------------------


def validate_token_and_role(
    *,
    subscription_id: str | None = None,
    resource_group_name: str | None = None,
    principal_id: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Validate the Azure credential and the identity's role assignments.

    Uses :class:`azure.identity.DefaultAzureCredential` to authenticate, probes
    ``compute.virtual_machines.list`` on the target subscription/resource group,
    and cross-references the principal's role assignments against
    :data:`EXPECTED_ROLES`.

    Returns ``(ok, info)`` where ``info`` contains:
        subscription, principal_id, roles_verified, missing.
    """
    subscription_id = subscription_id or os.environ.get("AZURE_SUBSCRIPTION_ID")
    if not subscription_id:
        raise ValueError(
            "subscription_id not supplied and AZURE_SUBSCRIPTION_ID is unset.",
        )
    resource_group_name = resource_group_name or "gludd-rg"
    if not principal_id:
        raise ValueError("principal_id is required to verify role assignments.")

    compute_client = _build_azure_client(subscription_id=subscription_id)
    # Probe the live API — this is the real permission check.
    list(compute_client.virtual_machines.list(resource_group_name=resource_group_name))

    # Cross-reference role assignments against the expected role set.
    assignments = _get_role_assignments(subscription_id, principal_id)
    granted = {
        a["role_definition_name"]
        for a in assignments
        if a.get("principal_id") == principal_id and a.get("role_definition_name")
    }
    roles_verified = sorted(r for r in EXPECTED_ROLES if r in granted)
    missing = sorted(r for r in EXPECTED_ROLES if r not in granted)

    info: dict[str, Any] = {
        "subscription": subscription_id,
        "principal_id": principal_id,
        "roles_verified": roles_verified,
        "missing": missing,
    }
    return (len(missing) == 0, info)


# ---------------------------------------------------------------------------
# Internal helpers (lazy-imported SDK boundaries — mockable in tests)
# ---------------------------------------------------------------------------


def _build_azure_client(*, subscription_id: str) -> Any:
    """Build an azure-mgmt-compute ComputeManagementClient lazily.

    Kept as a separate function so tests can patch ``azure._build_azure_client``
    without importing the SDK.
    """
    try:
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.compute import ComputeManagementClient
    except ImportError as exc:
        raise RuntimeError(
            "Azure SDK not installed. Install with: pip install 'general-ludd-agent[azure]'",
        ) from exc

    creds = DefaultAzureCredential()
    return ComputeManagementClient(credential=creds, subscription_id=subscription_id)


def _get_role_assignments(subscription_id: str, principal_id: str) -> list[dict[str, Any]]:
    """List role assignments for the principal, normalised to a flat list."""
    try:
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.authorization import AuthorizationManagementClient
    except ImportError as exc:
        raise RuntimeError(
            "Azure SDK not installed. Install with: pip install 'general-ludd-agent[azure]'",
        ) from exc

    client = AuthorizationManagementClient(
        credential=DefaultAzureCredential(),
        subscription_id=subscription_id,
    )
    out: list[dict[str, Any]] = []
    for a in client.role_assignments.list(filter=f"principalId eq '{principal_id}'"):
        # Resolve the role definition name from its id.
        role_def_id = a.role_definition_id or ""
        role_name = _resolve_role_definition_name(client, role_def_id)
        out.append(
            {
                "principal_id": a.principal_id,
                "role_definition_id": role_def_id,
                "role_definition_name": role_name,
            }
        )
    return out


def _resolve_role_definition_name(auth_client: Any, role_def_id: str) -> str:
    """Best-effort lookup of a role-definition display name from its id."""
    if not role_def_id:
        return ""
    try:
        scope = role_def_id.rsplit("/", 1)[0]
        name = role_def_id.rsplit("/", 1)[-1]
        rd = auth_client.role_definitions.get(scope=scope, role_definition_id=name)
        return str(getattr(rd, "role_name", "") or "")
    except Exception:  # pragma: no cover — best effort
        return ""


def _noop(_x: Callable[..., Any]) -> Any:  # pragma: no cover
    return _x


# ---------------------------------------------------------------------------
# Provider class — adapts the module-level functions above to the generic
# ``OnboardProvider`` shape the CLI scaffold expects (mirrors
# ``general_ludd.onboard.aws.AWSOnboardProvider``).
# ---------------------------------------------------------------------------


class AzureOnboardProvider:
    """Azure implementation of :class:`general_ludd.onboard.OnboardProvider`.

    The CLI's ``gludd onboard`` scaffold calls every provider with the same
    generic three surfaces: ``create_role_instructions()``,
    ``token_acquisition_guide()``, and
    ``validate_token_and_role(token, role_arn, region)``. Azure's own concepts
    don't map 1:1 onto AWS's ARN-shaped world, so this adapter documents the
    mapping it uses:

    * ``subscription_id`` — resolved at construction time from the
      ``subscription_id`` kwarg, else ``AZURE_SUBSCRIPTION_ID``, else a
      ``<SUBSCRIPTION_ID>`` placeholder (only for the printed instructions —
      :meth:`validate_token_and_role` requires a real value, same as the
      module-level function it wraps).
    * ``token`` (CLI arg) -> unused. Azure auth goes through
      ``DefaultAzureCredential``, which is environment-variable driven
      (``AZURE_CLIENT_ID`` / ``AZURE_CLIENT_SECRET`` / ``AZURE_TENANT_ID``),
      not a single bearer token the CLI can pass positionally.
    * ``role_arn`` (CLI arg) -> the managed identity's ``principal_id`` to
      verify role assignments against (kept generically named ``role_arn``
      for cross-provider consistency).
    * ``region`` (CLI arg) -> unused; Azure role assignments here are
      subscription/resource-group scoped, not region-scoped.
    """

    name = "azure"

    def __init__(
        self,
        *,
        subscription_id: str | None = None,
        resource_group_name: str = "gludd-rg",
        location: str = "eastus",
        identity_name: str = DEFAULT_IDENTITY_NAME,
    ) -> None:
        self.subscription_id = subscription_id or os.environ.get("AZURE_SUBSCRIPTION_ID")
        self.resource_group_name = resource_group_name
        self.location = location
        self.identity_name = identity_name

    def create_role_instructions(self) -> str:
        return create_role_instructions(
            subscription_id=self.subscription_id or "<SUBSCRIPTION_ID>",
            resource_group_name=self.resource_group_name,
            location=self.location,
            identity_name=self.identity_name,
        )

    def token_acquisition_guide(self) -> str:
        return token_acquisition_guide()

    def validate_token_and_role(self, token: str, role_arn: str, region: str) -> tuple[bool, dict[str, Any]]:
        """Non-raising adapter over the module-level probe.

        Returns ``(False, {"detail": ...})`` instead of raising when the SDK
        is missing or required identifiers can't be resolved, so the CLI can
        report a clean failure rather than an uncaught traceback.
        """
        del token, region  # unused: see class docstring.
        try:
            return validate_token_and_role(
                subscription_id=self.subscription_id,
                resource_group_name=self.resource_group_name,
                principal_id=role_arn or None,
            )
        except Exception as exc:  # RuntimeError (SDK missing), ValueError, ...
            return False, {"detail": f"{type(exc).__name__}: {exc}"}


if __name__ == "__main__":  # pragma: no cover
    cmd = sys.argv[1] if len(sys.argv) > 1 else "roles"
    if cmd == "roles":
        print(create_role_instructions(subscription_id="<SUBSCRIPTION_ID>"))
    elif cmd == "token":
        print(token_acquisition_guide())
