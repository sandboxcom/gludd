"""Credential acquisition lifecycles for configured Azure self-improvement."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Protocol

from general_ludd.azure.accelerator_credential_source import (
    AzureAcceleratorCredentialLease,
)
from general_ludd.azure.accelerator_credential_store import (
    load_preserved_azure_accelerator_credentials as load_azure_accelerator_credentials,
)
from general_ludd.azure.accelerator_credentials import (
    AzureAcceleratorAuthentication,
    AzureAcceleratorCredentials,
    AzureAcceleratorWorkloadIdentity,
    build_azure_workload_identity,
)


class AzureCredentialProvider(Protocol):
    """Acquire exact Azure credentials and a paired release callback."""

    def acquire(self) -> AzureCredentialAcquisition:
        """Acquire one exact subscription credential and its release callback."""
        ...


class _LeaseSource(Protocol):
    def acquire(self) -> AzureAcceleratorCredentialLease: ...

    def release(self, lease: AzureAcceleratorCredentialLease) -> None: ...


@dataclass(frozen=True, slots=True)
class AzureCredentialAcquisition:
    """One credential acquisition whose release action is deliberately opaque."""

    credentials: AzureAcceleratorAuthentication
    release: Callable[[], None] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        """Validate the credential and opaque release boundary."""
        if not isinstance(
            self.credentials,
            (AzureAcceleratorCredentials, AzureAcceleratorWorkloadIdentity),
        ):
            raise ValueError("credentials must use the accelerator credential contract")
        if not callable(self.release):
            raise ValueError("credential release must be callable")


class FileAzureCredentialProvider:
    """Lazily load one private Azure CLI JSON credential for every session."""

    def __init__(
        self,
        path: Path,
        subscription_id: str,
        *,
        loader: Callable[..., AzureAcceleratorCredentials] = (
            load_azure_accelerator_credentials
        ),
    ) -> None:
        """Initialize a private credential path bound to one subscription."""
        self._path = path
        self._subscription_id = subscription_id
        self._loader = loader

    def acquire(self) -> AzureCredentialAcquisition:
        """Load and validate a fresh credential acquisition."""
        credentials = self._loader(
            self._path,
            expected_subscription_id=self._subscription_id,
        )
        return AzureCredentialAcquisition(credentials, lambda: None)


class WorkloadIdentityAzureCredentialProvider:
    """Lazily bind one private GitHub assertion to an Azure workload identity."""

    def __init__(
        self,
        *,
        client_id: str,
        subscription_id: str,
        tenant_id: str,
        federated_token_file: Path,
        builder: Callable[..., AzureAcceleratorWorkloadIdentity] = (
            build_azure_workload_identity
        ),
    ) -> None:
        """Capture only public identifiers and the private assertion path."""
        self._client_id = client_id
        self._subscription_id = subscription_id
        self._tenant_id = tenant_id
        self._federated_token_file = federated_token_file
        self._builder = builder

    def acquire(self) -> AzureCredentialAcquisition:
        """Validate a fresh assertion immediately before Azure effects."""
        credentials = self._builder(
            client_id=self._client_id,
            subscription_id=self._subscription_id,
            tenant_id=self._tenant_id,
            federated_token_file=self._federated_token_file,
            expected_subscription_id=self._subscription_id,
        )
        return AzureCredentialAcquisition(credentials, lambda: None)


class OpenBaoAzureCredentialProvider:
    """Adapt an operator-configured OpenBao Azure role to the same lifecycle."""

    def __init__(self, source: _LeaseSource) -> None:
        """Initialize an exact dynamic-lease source."""
        if not callable(getattr(source, "acquire", None)) or not callable(
            getattr(source, "release", None)
        ):
            raise ValueError("OpenBao source must support exact acquire and release")
        self._source = source

    def acquire(self) -> AzureCredentialAcquisition:
        """Acquire one lease and bind its exact revocation callback."""
        lease = self._source.acquire()
        return AzureCredentialAcquisition(
            lease.credentials,
            lambda: self._source.release(lease),
        )


def release_once(release: Callable[[], None]) -> Callable[[], None]:
    """Make one credential-release callback safe for overlapping owners."""
    lock = RLock()
    released = False

    def run() -> None:
        nonlocal released
        with lock:
            if released:
                return
            released = True
        release()

    return run


__all__ = (
    "AzureCredentialAcquisition",
    "AzureCredentialProvider",
    "FileAzureCredentialProvider",
    "OpenBaoAzureCredentialProvider",
    "WorkloadIdentityAzureCredentialProvider",
    "release_once",
)
