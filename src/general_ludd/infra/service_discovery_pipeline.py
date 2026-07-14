"""Re-export shim — use `general_ludd.service_discovery.pipeline` directly."""

from general_ludd.service_discovery.pipeline import (
    DEFAULT_SEARCH_TERMS,
    DiscoveryReport,
    ServiceDiscoveryPipeline,
)

__all__ = [
    "DEFAULT_SEARCH_TERMS",
    "DiscoveryReport",
    "ServiceDiscoveryPipeline",
]
