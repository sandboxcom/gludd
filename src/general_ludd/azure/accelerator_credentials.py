"""Race-safe loading of Azure CLI ``--json-auth`` credential files.

The loader is intentionally Azure-Public-Cloud-only and never logs, renders, or
persists credential values.  Callers receive an immutable value whose secret is
excluded from ``repr`` and can create an isolated child-process environment.
"""

from __future__ import annotations

import json
import os
import stat
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

_MAX_CREDENTIAL_BYTES: Final = 16 * 1024
_REQUIRED_FIELDS: Final = (
    "clientId",
    "clientSecret",
    "subscriptionId",
    "tenantId",
)
_PUBLIC_CLOUD_ENDPOINTS: Final = {
    "activeDirectoryEndpointUrl": "https://login.microsoftonline.com",
    "resourceManagerEndpointUrl": "https://management.azure.com",
}


class AzureAcceleratorCredentialError(ValueError):
    """Report a fixed-context credential-file refusal without sensitive data."""


@dataclass(frozen=True, slots=True)
class AzureAcceleratorCredentials:
    """Validated Azure service-principal credentials for one subscription."""

    client_id: str
    client_secret: str = field(repr=False)
    subscription_id: str
    tenant_id: str

    def arm_environment(self) -> dict[str, str]:
        """Return the isolated Azure SDK and Terraform authentication mapping."""
        return {
            "ARM_CLIENT_ID": self.client_id,
            "ARM_CLIENT_SECRET": self.client_secret,
            "ARM_TENANT_ID": self.tenant_id,
            "ARM_SUBSCRIPTION_ID": self.subscription_id,
            "AZURE_CLIENT_ID": self.client_id,
            "AZURE_CLIENT_SECRET": self.client_secret,
            "AZURE_TENANT_ID": self.tenant_id,
            "AZURE_SUBSCRIPTION_ID": self.subscription_id,
        }


def _reject_duplicate_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AzureAcceleratorCredentialError(
                "credential JSON contains a duplicate field"
            )
        result[key] = value
    return result


def _open_private_regular_file(path: Path) -> tuple[int, os.stat_result]:
    try:
        before = path.lstat()
    except OSError:
        raise AzureAcceleratorCredentialError(
            "credential input must be an owned private regular file"
        ) from None
    if not stat.S_ISREG(before.st_mode):
        raise AzureAcceleratorCredentialError(
            "credential input must be an owned private regular file"
        )

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise AzureAcceleratorCredentialError(
            "credential input must be an owned private regular file"
        ) from None

    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (
            before.st_dev,
            before.st_ino,
        ) != (opened.st_dev, opened.st_ino):
            raise AzureAcceleratorCredentialError(
                "credential input must be an owned private regular file"
            )
        if hasattr(os, "getuid") and opened.st_uid != os.getuid():
            raise AzureAcceleratorCredentialError(
                "credential input must be owned by the current user"
            )
        if os.name != "nt" and stat.S_IMODE(opened.st_mode) != 0o600:
            raise AzureAcceleratorCredentialError(
                "credential input must have mode 0600"
            )
        if opened.st_size > _MAX_CREDENTIAL_BYTES:
            raise AzureAcceleratorCredentialError("credential input is too large")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, opened


def _read_bounded(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    remaining = _MAX_CREDENTIAL_BYTES + 1
    while remaining > 0:
        chunk = os.read(descriptor, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > _MAX_CREDENTIAL_BYTES:
        raise AzureAcceleratorCredentialError("credential input is too large")
    return payload


def _parse_payload(raw: bytes) -> dict[str, object]:
    try:
        decoded = raw.decode("utf-8")
        payload = json.loads(decoded, object_pairs_hook=_reject_duplicate_fields)
    except AzureAcceleratorCredentialError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        raise AzureAcceleratorCredentialError(
            "credential input must contain valid JSON"
        ) from None
    if not isinstance(payload, dict):
        raise AzureAcceleratorCredentialError(
            "credential input must contain a JSON object"
        )
    return payload


def _required_string(payload: dict[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value or value.strip() != value:
        raise AzureAcceleratorCredentialError(
            "credential JSON is missing valid required fields"
        )
    return value


def _canonical_uuid(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise AzureAcceleratorCredentialError(
            "credential identifiers must be canonical UUID values"
        ) from None
    canonical = str(parsed)
    if value != canonical:
        raise AzureAcceleratorCredentialError(
            "credential identifiers must be canonical UUID values"
        )
    return canonical


def _validate_secret(value: str) -> None:
    if len(value) > 4096 or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise AzureAcceleratorCredentialError(
            "credential client secret has an invalid shape"
        )


def _validate_public_cloud_endpoints(payload: dict[str, object]) -> None:
    for field_name, required in _PUBLIC_CLOUD_ENDPOINTS.items():
        supplied = payload.get(field_name)
        if supplied is None:
            continue
        if not isinstance(supplied, str) or supplied.rstrip("/") != required:
            raise AzureAcceleratorCredentialError(
                "credential endpoints must target Azure public cloud"
            )


def load_azure_accelerator_credentials(
    path: str | os.PathLike[str],
    *,
    expected_subscription_id: str | None = None,
) -> AzureAcceleratorCredentials:
    """Load one private Azure CLI JSON file through a race-safe descriptor."""
    descriptor, _opened = _open_private_regular_file(Path(path))
    try:
        payload = _parse_payload(_read_bounded(descriptor))
    finally:
        os.close(descriptor)

    values = {field_name: _required_string(payload, field_name) for field_name in _REQUIRED_FIELDS}
    client_id = _canonical_uuid(values["clientId"])
    subscription_id = _canonical_uuid(values["subscriptionId"])
    tenant_id = _canonical_uuid(values["tenantId"])
    _validate_secret(values["clientSecret"])
    _validate_public_cloud_endpoints(payload)
    if expected_subscription_id is not None:
        expected = _canonical_uuid(expected_subscription_id)
        if subscription_id != expected:
            raise AzureAcceleratorCredentialError(
                "credential subscription mismatch"
            )

    return AzureAcceleratorCredentials(
        client_id=client_id,
        client_secret=values["clientSecret"],
        subscription_id=subscription_id,
        tenant_id=tenant_id,
    )


__all__ = [
    "AzureAcceleratorCredentialError",
    "AzureAcceleratorCredentials",
    "load_azure_accelerator_credentials",
]
