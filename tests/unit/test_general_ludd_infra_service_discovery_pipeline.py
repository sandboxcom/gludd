from __future__ import annotations

from general_ludd.infra.service_discovery_pipeline import (
    DEFAULT_SEARCH_TERMS,
    DiscoveryReport,
    ServiceDiscoveryPipeline,
)


class TestReExports:
    def test_default_search_terms_is_list(self) -> None:
        assert isinstance(DEFAULT_SEARCH_TERMS, list)
        assert len(DEFAULT_SEARCH_TERMS) > 0

    def test_default_search_terms_elements_are_tuples(self) -> None:
        assert all(isinstance(e, tuple) for e in DEFAULT_SEARCH_TERMS)

    def test_discovery_report_is_importable(self) -> None:
        assert DiscoveryReport is not None

    def test_service_discovery_pipeline_is_importable(self) -> None:
        assert ServiceDiscoveryPipeline is not None

    def test_re_exports_match_original(self) -> None:
        from general_ludd.service_discovery.pipeline import (
            DEFAULT_SEARCH_TERMS as original_terms,
        )
        from general_ludd.service_discovery.pipeline import (
            DiscoveryReport as original_report,
        )
        from general_ludd.service_discovery.pipeline import (
            ServiceDiscoveryPipeline as original_pipeline,
        )

        assert DEFAULT_SEARCH_TERMS is original_terms
        assert DiscoveryReport is original_report
        assert ServiceDiscoveryPipeline is original_pipeline

    def test_discovery_report_is_class(self) -> None:
        assert isinstance(DiscoveryReport, type)

    def test_pipeline_is_class(self) -> None:
        assert isinstance(ServiceDiscoveryPipeline, type)

    def test_every_search_term_has_two_elements(self) -> None:
        for term in DEFAULT_SEARCH_TERMS:
            assert len(term) == 2, f"Expected tuple of 2, got {term}"

    def test_search_terms_contain_strings(self) -> None:
        for term in DEFAULT_SEARCH_TERMS:
            assert isinstance(term[0], str)
            assert isinstance(term[1], str)

    def test_no_duplicate_search_terms(self) -> None:
        lowered = [(k.lower(), v.lower()) for k, v in DEFAULT_SEARCH_TERMS]
        assert len(lowered) == len(set(lowered)), "Duplicate search terms found"
