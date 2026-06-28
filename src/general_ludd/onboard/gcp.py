"""GCP onboarding provider for ``gludd onboard gcp``.

Provisions a least-privilege service account (``gludd-compute-operator``) and
verifies the cloud-side credential before the daemon tries to use it.

The IAM roles granted here are the minimal set that
:func:`general_ludd.infra.terraform.TerraformGenerator._generate_gcp` requires
to materialise its Terraform plan:

* ``roles/compute.instanceAdmin.v1`` — creates/manages ``google_compute_instance``.
  (Not the broader ``compute.admin``.)
* ``roles/compute.securityAdmin`` — gludd creates a ``google_compute_firewall``
  per instance (see ``_generate_gcp``).
* ``roles/iam.serviceAccountUser`` on the SA itself — so gludd can attach the
  operator SA to the instances it provisions.
* ``roles/logging.logWriter`` — runtime log emission.

All Google SDKs (``google-api-python-client``, ``google-auth``) are lazy
imports; install via the ``[gcp]`` extra.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

# The exact least-privilege role set gludd needs on GCP. Mirrored into the
# Terraform module at infra/terraform/modules/onboard-iam-gcp/main.tf and
# asserted by tests/unit/test_onboard_gcp.py::TestTerraformModuleLeastPriv.
EXPECTED_ROLES: tuple[str, ...] = (
    "roles/compute.instanceAdmin.v1",
    "roles/compute.securityAdmin",
    "roles/iam.serviceAccountUser",
    "roles/logging.logWriter",
)

DEFAULT_SERVICE_ACCOUNT_NAME = "gludd-compute-operator"
DEFAULT_DISPLAY_NAME = "Gludd compute operator (ephemeral GPU provisioning)"
MODULE_REL_PATH = "infra/terraform/modules/onboard-iam-gcp"


# ---------------------------------------------------------------------------
# Phase 1 — role / IAM provisioning instructions
# ---------------------------------------------------------------------------

def create_role_instructions(
    *,
    project_id: str,
    service_account_name: str = DEFAULT_SERVICE_ACCOUNT_NAME,
) -> str:
    """Return markdown walking the user through provisioning the IAM role.

    The flow mirrors the AWS onboarding pattern: authenticate via the cloud
    CLI, target the right project/subscription, then ``terraform init/apply``
    the provider's onboard-iam module.
    """
    sa_email = _sa_email(project_id, service_account_name)
    return f"""# GCP onboarding — IAM provisioning

This provisions a least-privilege service account that gludd uses to launch
and tear down ephemeral GPU compute instances in project `{project_id}`.

## 1. Authenticate and select the project

```bash
gcloud auth login
gcloud config set project {project_id}
```

## 2. Provision the IAM resources

Apply the `onboard-iam-gcp` Terraform module. From the repository root:

```bash
cd {MODULE_REL_PATH}
terraform init
terraform apply -var="project_id={project_id}"
```

The module creates:

* `google_service_account` named `{service_account_name}` (email: `{sa_email}`)
* `google_project_iam_member` bindings granting the SA exactly these roles:
  - `roles/compute.instanceAdmin.v1` — create/manage instances
  - `roles/compute.securityAdmin` — create the per-instance firewall rule
  - `roles/iam.serviceAccountUser` — attach the SA to instances it provisions
  - `roles/logging.logWriter` — emit runtime logs

No `roles/owner`, `roles/editor`, or `roles/compute.admin` are granted.

## 3. Capture the service-account email

After `terraform apply` completes, the service account email is emitted as
`service_account_email`. Confirm:

```bash
gcloud iam service-accounts describe {sa_email}
```

Proceed to token acquisition (see `gludd onboard gcp --phase token`).
"""


# ---------------------------------------------------------------------------
# Phase 2 — token / credential acquisition guide
# ---------------------------------------------------------------------------

def token_acquisition_guide() -> str:
    """Return markdown describing how to obtain the JSON key gludd consumes."""
    return f"""# GCP onboarding — token acquisition

gludd authenticates to GCP with a service-account JSON key. Two paths:

## Option A — gcloud (recommended)

```bash
gcloud iam service-accounts keys create gludd-key.json \\
  --iam-account=<SERVICE_ACCOUNT_EMAIL>
```

Use the email output by the IAM step (`service_account_email`), e.g.
`{DEFAULT_SERVICE_ACCOUNT_NAME}@<PROJECT>.iam.gserviceaccount.com`.

## Option B — Cloud Console

1. IAM & Admin → Service Accounts → `{DEFAULT_SERVICE_ACCOUNT_NAME}`.
2. Keys → Add key → Create new key → JSON.
3. Download the JSON file.

## Point gludd at the key

Export the path so the Google client libraries pick it up automatically:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="$(pwd)/gludd-key.json"
```

Then validate:

```bash
gludd onboard gcp --phase validate \\
  --token "$GOOGLE_APPLICATION_CREDENTIALS" \\
  --project-id <PROJECT_ID>
```

The key file is a secret — store it in your secrets manager (OpenBao /
`gludd secrets`), never commit it.
"""


# ---------------------------------------------------------------------------
# Phase 3 — validation (live API probe)
# ---------------------------------------------------------------------------

def validate_token_and_role(
    *,
    token_path: str | None = None,
    project_id: str | None = None,
    service_account_email: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Validate the GCP credential and the SA's IAM roles.

    Loads credentials from ``token_path`` (or ``GOOGLE_APPLICATION_CREDENTIALS``
    if unset), probes ``compute.instances.list`` on the target project, and
    cross-references the SA's IAM bindings against :data:`EXPECTED_ROLES`.

    Returns ``(ok, info)`` where ``info`` contains:
        project_id, service_account_email, roles_verified, missing.
    """
    token_path = token_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not token_path or not Path(token_path).exists():
        raise FileNotFoundError(
            "No GCP service-account key found. Pass token_path or set "
            "GOOGLE_APPLICATION_CREDENTIALS.",
        )

    # Resolve project + SA email from the key file when not supplied.
    key_blob = json.loads(Path(token_path).read_text())
    resolved_project = project_id or key_blob.get("project_id")
    if not resolved_project:
        raise ValueError("project_id not supplied and not present in the key file")
    resolved_sa_email = service_account_email or key_blob.get("client_email")
    if not resolved_sa_email:
        raise ValueError("service_account_email not supplied and not in key file")

    client = _build_gcp_client(token_path=token_path)
    # Probe the live API — this is the real permission check.
    compute = client.build("compute", "v1", cache_discovery=False)
    compute.instances().list(project=resolved_project, zone="-").execute()

    # Cross-reference IAM policy against the expected role set.
    policy = _get_iam_policy(client, resolved_project)
    granted_roles = _roles_for_member(policy, resolved_sa_email)
    roles_verified = sorted(r for r in EXPECTED_ROLES if r in granted_roles)
    missing = sorted(r for r in EXPECTED_ROLES if r not in granted_roles)

    info: dict[str, Any] = {
        "project_id": resolved_project,
        "service_account_email": resolved_sa_email,
        "roles_verified": roles_verified,
        "missing": missing,
    }
    return (len(missing) == 0, info)


# ---------------------------------------------------------------------------
# Internal helpers (lazy-imported SDK boundaries — mockable in tests)
# ---------------------------------------------------------------------------

def _build_gcp_client(*, token_path: str | None = None) -> Any:
    """Build a googleapiclient discovery client lazily.

    Kept as a separate function so tests can patch ``gcp._build_gcp_client``
    without importing the SDK.
    """
    token_path = token_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    try:
        from google.oauth2 import service_account
        from googleapiclient import discovery
    except ImportError as exc:
        raise RuntimeError(
            "GCP SDK not installed. Install with: pip install "
            "'general-ludd-agent[gcp]'",
        ) from exc

    if token_path and Path(token_path).exists():
        creds = service_account.Credentials.from_service_account_file(token_path)
        scopes = ["https://www.googleapis.com/auth/compute.readonly",
                  "https://www.googleapis.com/auth/cloud-platform"]
        creds = creds.with_scopes(scopes)
        return _DiscoveryWrapper(lambda **kw: discovery.build(credentials=creds, **kw))

    # Fall back to Application Default Credentials.
    return _DiscoveryWrapper(lambda **kw: discovery.build(**kw))


def _get_iam_policy(client: Any, project_id: str) -> dict[str, Any]:
    """Fetch the project IAM policy via Cloud Resource Manager."""
    # client is a _DiscoveryWrapper; build the resource-manager v1 API.
    rm = client.build("cloudresourcemanager", "v1", cache_discovery=False)
    policy: dict[str, Any] = dict(rm.projects().getIamPolicy(resource=project_id, body={}).execute())
    return policy


def _roles_for_member(policy: dict[str, Any], sa_email: str) -> set[str]:
    """Return the set of roles granted to ``serviceAccount:<sa_email>``."""
    member = f"serviceAccount:{sa_email}"
    granted: set[str] = set()
    for binding in policy.get("bindings", []):
        if member in binding.get("members", []):
            granted.add(binding["role"])
    return granted


def _sa_email(project_id: str, sa_name: str) -> str:
    return f"{sa_name}@{project_id}.iam.gserviceaccount.com"


class _DiscoveryWrapper:
    """Tiny adapter so the lazy builder can be swapped in tests.

    Wraps a ``build`` callable so callers can do ``client.build(...)`` and
    tests can substitute a Mock that records the API surface used.
    """

    def __init__(self, build_fn: Callable[..., Any]) -> None:
        self._build_fn = build_fn

    def build(self, *args: Any, **kwargs: Any) -> Any:
        return self._build_fn(*args, **kwargs)


if __name__ == "__main__":  # pragma: no cover
    # Allow `python -m general_ludd.onboard.gcp` to print the guide.
    cmd = sys.argv[1] if len(sys.argv) > 1 else "roles"
    if cmd == "roles":
        print(create_role_instructions(project_id="<PROJECT_ID>"))
    elif cmd == "token":
        print(token_acquisition_guide())
