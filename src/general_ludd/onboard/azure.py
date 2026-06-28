"""Azure onboarding provider for ``gludd onboard azure``.

Provisions a least-privilege user-assigned managed identity
(``gludd-compute-operator``) and verifies the cloud-side credential before the
daemon tries to use it.

The role set here is the minimal set that
:func:`general_ludd.infra.terraform.TerraformGenerator._generate_azure` requires
to materialise its Terraform plan (resource group, vnet/subnet/NSG, public IP,
NIC, and the VM itself):

* ``Virtual Machine Contributor`` — create/manage VMs (NOT the broader ``Contributor``).
* ``Managed Identity Operator`` — so the operator can assign its own identity
  to the VMs it creates.

All Azure SDKs (``azure-identity``, ``azure-mgmt-compute``,
``azure-mgmt-authorization``) are lazy imports; install via the ``[azure]``
extra.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Any

# The exact least-privilege role set gludd needs on Azure. Mirrored into the
# Terraform module at infra/terraform/modules/onboard-iam-azure/main.tf and
# asserted by tests/unit/test_onboard_azure.py::TestTerraformModuleLeastPriv.
EXPECTED_ROLES: tuple[str, ...] = (
    "Virtual Machine Contributor",
    "Managed Identity Operator",
)

DEFAULT_IDENTITY_NAME = "gludd-compute-operator"
MODULE_REL_PATH = "infra/terraform/modules/onboard-iam-azure"


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
    return f"""# Azure onboarding — IAM provisioning

This provisions a least-privilege user-assigned managed identity that gludd
uses to launch and tear down ephemeral GPU compute VMs in subscription
`{subscription_id}`.

## 1. Authenticate and select the subscription

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
* `azurerm_role_assignment` granting the identity exactly these built-in roles:
  - `Virtual Machine Contributor` — create/manage VMs (NOT `Contributor`)
  - `Managed Identity Operator` — assign its own identity to the VMs it creates

No `Owner` or `Contributor` is granted.

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
            "Azure SDK not installed. Install with: pip install "
            "'general-ludd-agent[azure]'",
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
            "Azure SDK not installed. Install with: pip install "
            "'general-ludd-agent[azure]'",
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
        out.append({
            "principal_id": a.principal_id,
            "role_definition_id": role_def_id,
            "role_definition_name": role_name,
        })
    return out


def _resolve_role_definition_name(auth_client: Any, role_def_id: str) -> str:
    """Best-effort lookup of a role-definition display name from its id."""
    if not role_def_id:
        return ""
    try:
        scope = role_def_id.rsplit("/", 1)[0]
        name = role_def_id.rsplit("/", 1)[-1]
        rd = auth_client.role_definitions.get(scope=scope, role_definition_id=name)
        return getattr(getattr(rd, "role_name", None), "replace", lambda *_: "")() or getattr(rd, "role_name", "") or ""
    except Exception:  # pragma: no cover — best effort
        return ""


def _noop(_x: Callable[..., Any]) -> Any:  # pragma: no cover
    return _x


if __name__ == "__main__":  # pragma: no cover
    cmd = sys.argv[1] if len(sys.argv) > 1 else "roles"
    if cmd == "roles":
        print(create_role_instructions(subscription_id="<SUBSCRIPTION_ID>"))
    elif cmd == "token":
        print(token_acquisition_guide())
