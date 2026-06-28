"""Onboarding scaffold for cloud IAM role + API token setup.

This package exposes the ``OnboardProvider`` Protocol and provider-specific
stub handlers. The stubs raise ``NotImplementedError``; parallel tasks fill
in the actual IAM-policy authoring and live token-validation calls.

Each provider module exposes three callables with a consistent signature:

* ``create_role_instructions() -> str``
    Markdown walkthrough of the IAM provisioning flow (auth + terraform apply).
* ``token_acquisition_guide() -> str``
    Markdown walkthrough for obtaining credentials the daemon will use at runtime.
* ``validate_token_and_role(token, role_arn, region) -> tuple[bool, dict]``
    Probes the cloud with the supplied credentials and reports whether the
    identity has the least-privilege roles gludd needs.

Cloud SDKs are LAZY-IMPORTED inside the functions that use them so the
``[gcp]`` / ``[azure]`` extras are optional at install time.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class OnboardProvider(Protocol):
    """Minimal contract every cloud provider onboard handler implements."""

    name: str

    def create_role_instructions(self) -> str: ...

    def token_acquisition_guide(self) -> str: ...

    def validate_token_and_role(
        self,
        token: str,
        role_arn: str,
        region: str,
    ) -> tuple[bool, dict[str, Any]]: ...


class _BaseStub:
    """Shared base for provider stubs; raises on every live operation."""

    name: str = "stub"

    def create_role_instructions(self) -> str:
        raise NotImplementedError(
            f"Onboard provider '{self.name}' is not yet implemented. "
            "IAM role instructions are stubbed until the parallel provider task lands."
        )

    def token_acquisition_guide(self) -> str:
        raise NotImplementedError(
            f"Onboard provider '{self.name}' is not yet implemented. "
            "Token acquisition guide is stubbed until the parallel provider task lands."
        )

    def validate_token_and_role(
        self,
        token: str,
        role_arn: str,
        region: str,
    ) -> tuple[bool, dict[str, Any]]:
        raise NotImplementedError(
            f"Onboard provider '{self.name}' is not yet implemented. "
            "Token/role validation is stubbed until the parallel provider task lands."
        )


class AWSOnboardProvider(_BaseStub):
    name = "aws"


class GCPOnboardProvider(_BaseStub):
    name = "gcp"


class AzureOnboardProvider(_BaseStub):
    name = "azure"


SUPPORTED_PROVIDERS: dict[str, type[OnboardProvider]] = {
    "aws": AWSOnboardProvider,
    "gcp": GCPOnboardProvider,
    "azure": AzureOnboardProvider,
}


def get_provider(name: str) -> OnboardProvider:
    """Instantiate a provider handler by canonical name."""
    if name not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unknown onboard provider '{name}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_PROVIDERS))}."
        )
    return SUPPORTED_PROVIDERS[name]()


__all__ = [
    "SUPPORTED_PROVIDERS",
    "AWSOnboardProvider",
    "AzureOnboardProvider",
    "GCPOnboardProvider",
    "OnboardProvider",
    "get_provider",
]
