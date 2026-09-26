"""Unit tests for service_discovery_pipeline re-export shim."""

from general_ludd.infra.service_discovery_pipeline import (
    DEFAULT_SEARCH_TERMS,
    DiscoveryReport,
    ServiceDiscoveryPipeline,
)


class TestServiceDiscoveryPipelineExports:
    def test_classes_importable(self):
        assert isinstance(DEFAULT_SEARCH_TERMS, (list, tuple))
        assert callable(ServiceDiscoveryPipeline)
        assert callable(DiscoveryReport)
