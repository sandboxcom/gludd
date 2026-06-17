"""Provider registry factory.

``build_default_registry`` returns one provider instance per
:class:`DiscoverySource`. Importing this package does NOT eagerly load any cloud
SDK — each provider only lazy-imports its SDK INSIDE ``discover()``.
"""

from __future__ import annotations

from general_ludd.infra.deployment import SecretsResolver
from general_ludd.infra.discovery.base import DiscoveryProvider, DiscoverySource

# Standard env-var -> alias maps. The alias NAMES double as the default alias so
# a deployment that already stores the secret under the env-var name resolves
# without extra config; an operator can override per provider.
_DEFAULT_ALIASES: dict[DiscoverySource, dict[str, str]] = {
    DiscoverySource.AWS: {
        "AWS_ACCESS_KEY_ID": "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY": "AWS_SECRET_ACCESS_KEY",  # pragma: allowlist secret  # env-var name only
    },
    DiscoverySource.GCP: {"GOOGLE_CREDENTIALS": "GOOGLE_CREDENTIALS"},
    DiscoverySource.AZURE: {
        "ARM_CLIENT_ID": "ARM_CLIENT_ID",
        "ARM_CLIENT_SECRET": "ARM_CLIENT_SECRET",  # pragma: allowlist secret  # env-var name only
        "ARM_TENANT_ID": "ARM_TENANT_ID",
        "ARM_SUBSCRIPTION_ID": "ARM_SUBSCRIPTION_ID",
    },
    DiscoverySource.VMWARE: {
        "VSPHERE_USER": "VSPHERE_USER",
        "VSPHERE_PASSWORD": "VSPHERE_PASSWORD",  # pragma: allowlist secret  # env-var name only
        "VSPHERE_SERVER": "VSPHERE_SERVER",
    },
    DiscoverySource.KUBERNETES: {"KUBECONFIG": "KUBECONFIG"},
    DiscoverySource.LOCAL: {},
}


def build_default_registry(
    secrets_resolver: SecretsResolver | None = None,
) -> dict[DiscoverySource, DiscoveryProvider]:
    """Construct one provider per source. SDKs are NOT imported here."""
    from general_ludd.infra.discovery.providers.aws import AWSProvider
    from general_ludd.infra.discovery.providers.azure import AzureProvider
    from general_ludd.infra.discovery.providers.gcp import GCPProvider
    from general_ludd.infra.discovery.providers.kubernetes import KubernetesProvider
    from general_ludd.infra.discovery.providers.local import LocalProvider
    from general_ludd.infra.discovery.providers.vmware import VMwareProvider

    classes: dict[DiscoverySource, type[DiscoveryProvider]] = {
        DiscoverySource.LOCAL: LocalProvider,
        DiscoverySource.KUBERNETES: KubernetesProvider,
        DiscoverySource.VMWARE: VMwareProvider,
        DiscoverySource.AWS: AWSProvider,
        DiscoverySource.GCP: GCPProvider,
        DiscoverySource.AZURE: AzureProvider,
    }
    registry: dict[DiscoverySource, DiscoveryProvider] = {}
    for source, cls in classes.items():
        registry[source] = cls(
            provider_auth_aliases=_DEFAULT_ALIASES.get(source) or None,
            secrets_resolver=secrets_resolver,
        )
    return registry


__all__ = ["build_default_registry"]
