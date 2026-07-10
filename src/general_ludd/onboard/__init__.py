"""Onboarding scaffold for cloud IAM role + API token setup.

This package exposes the ``OnboardProvider`` Protocol and wires
``SUPPORTED_PROVIDERS``/``get_provider()`` to the real, unit-tested
per-cloud implementations in ``general_ludd.onboard.{aws,gcp,azure}``.

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

# NOTE: these module-level imports are safe at ``gludd`` startup because
# ``onboard.aws`` / ``onboard.gcp`` / ``onboard.azure`` only import their
# respective cloud SDKs (boto3 / google-* / azure-*) LAZILY, inside the
# functions that actually make a network call. Importing the modules
# themselves never touches boto3/google/azure, so the optional
# ``[aws]``/``[gcp]``/``[azure]`` extras remain optional at install time.
from general_ludd.onboard.aws import AWSOnboardProvider as _AWSProviderImpl
from general_ludd.onboard.azure import AzureOnboardProvider as AzureOnboardProvider
from general_ludd.onboard.gcp import GCPOnboardProvider as GCPOnboardProvider


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


class AWSOnboardProvider:
    """Real AWS onboarding provider.

    Wraps (by composition, NOT inheritance) the real
    :class:`general_ludd.onboard.aws.AWSOnboardProvider` to adapt its
    ``validate_token_and_role`` — which returns a bare dict and raises
    ``ImportError``/other exceptions when boto3 is missing or the call
    fails — to the non-raising ``(ok, details)`` shape the
    :class:`OnboardProvider` Protocol (and the CLI scaffold) expect.

    Composition (rather than subclassing + overriding
    ``validate_token_and_role``) is deliberate: the real class's own
    :meth:`~general_ludd.onboard.aws.AWSOnboardProvider._validate_or_error`
    helper calls ``self.validate_token_and_role(...)`` internally, so
    subclassing and overriding that same method name would make
    ``_validate_or_error`` call straight back into the override —
    infinite recursion. Composition keeps the two method-resolution
    orders separate.
    """

    name = "aws"

    def __init__(self) -> None:
        self._impl = _AWSProviderImpl()

    def create_role_instructions(self) -> str:
        return self._impl.create_role_instructions()

    def token_acquisition_guide(self) -> str:
        return self._impl.token_acquisition_guide()

    def validate_token_and_role(
        self, token: str, role_arn: str, region: str
    ) -> tuple[bool, dict[str, Any]]:
        return self._impl._validate_or_error(token, role_arn, region)


SUPPORTED_PROVIDERS: dict[str, type[OnboardProvider]] = {
    "aws": AWSOnboardProvider,
    "gcp": GCPOnboardProvider,
    "azure": AzureOnboardProvider,
}


# Which optional keyword arguments each provider's __init__ accepts. Explicit
# per-provider mapping (rather than inspect.signature) so the passthrough
# contract is auditable at a glance and can't silently widen if a provider
# grows kwargs.
_PROVIDER_INIT_KWARGS: dict[str, frozenset[str]] = {
    "aws": frozenset(),
    "gcp": frozenset({"project_id"}),
    "azure": frozenset({"subscription_id"}),
}


def get_provider(
    name: str,
    *,
    project_id: str | None = None,
    subscription_id: str | None = None,
) -> OnboardProvider:
    """Instantiate a provider handler by canonical name.

    Optional cloud-specific identifiers are forwarded only to the providers
    whose constructors accept them (``project_id`` -> gcp,
    ``subscription_id`` -> azure); other providers silently ignore them.
    ``None`` values are never forwarded, so each provider's own env-var
    fallback (``GOOGLE_CLOUD_PROJECT`` / ``AZURE_SUBSCRIPTION_ID``) still
    applies when the caller has nothing to pass.
    """
    if name not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unknown onboard provider '{name}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_PROVIDERS))}."
        )
    offered: dict[str, str | None] = {
        "project_id": project_id,
        "subscription_id": subscription_id,
    }
    accepted = _PROVIDER_INIT_KWARGS[name]
    kwargs = {k: v for k, v in offered.items() if k in accepted and v is not None}
    return SUPPORTED_PROVIDERS[name](**kwargs)


__all__ = [
    "SUPPORTED_PROVIDERS",
    "AWSOnboardProvider",
    "AzureOnboardProvider",
    "GCPOnboardProvider",
    "OnboardProvider",
    "get_provider",
]
