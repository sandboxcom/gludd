"""Verify no import breakage after dedup consolidation (D.20)."""
from __future__ import annotations


def test_connectors_util_imports() -> None:
    from general_ludd.connectors._util import parse_timestamp, validate_base_url
    assert callable(validate_base_url)
    assert callable(parse_timestamp)


def test_routers_util_imports() -> None:
    from general_ludd.routers._util import get_session_factory
    assert callable(get_session_factory)


def test_connector_circleci_imports() -> None:
    from general_ludd.connectors.circleci import CircleCiSource
    assert CircleCiSource is not None


def test_connector_gitlab_ci_imports() -> None:
    from general_ludd.connectors.gitlab_ci import GitlabCiSource
    assert GitlabCiSource is not None


def test_connector_azure_devops_imports() -> None:
    from general_ludd.connectors.azure_devops import AzureDevOpsSource
    assert AzureDevOpsSource is not None


def test_connector_github_actions_imports() -> None:
    from general_ludd.connectors.github_actions import GitHubActionsSource
    assert GitHubActionsSource is not None


def test_router_benchmark_imports() -> None:
    import general_ludd.routers.benchmark
    assert general_ludd.routers.benchmark is not None


def test_router_features_imports() -> None:
    import general_ludd.routers.features
    assert general_ludd.routers.features is not None


def test_router_account_imports() -> None:
    import general_ludd.routers.account
    assert general_ludd.routers.account is not None


def test_router_facts_imports() -> None:
    import general_ludd.routers.facts
    assert general_ludd.routers.facts is not None


def test_missing_init_py_added() -> None:
    import general_ludd.cli
    import general_ludd.observe
    import general_ludd.orchestration
    import general_ludd.receiver
    import general_ludd.renderers.templates
    import general_ludd.templates
    import general_ludd.templates.render
    import general_ludd.templates.render.sections
    assert general_ludd.cli is not None
    assert general_ludd.observe is not None
    assert general_ludd.orchestration is not None
    assert general_ludd.receiver is not None
    assert general_ludd.templates is not None
    assert general_ludd.templates.render is not None
    assert general_ludd.templates.render.sections is not None
    assert general_ludd.renderers.templates is not None


def test_validate_base_url_blocks_loopback() -> None:
    from general_ludd.connectors._util import validate_base_url
    try:
        validate_base_url("http://127.0.0.1:8080/")
        raise AssertionError("Should have raised")
    except ValueError:
        pass


def test_validate_base_url_allows_public() -> None:
    from general_ludd.connectors._util import validate_base_url
    result = validate_base_url("https://api.example.com/")
    assert result == "https://api.example.com"


def test_parse_timestamp_returns_float() -> None:
    from general_ludd.connectors._util import parse_timestamp
    result = parse_timestamp("2024-01-15T10:30:00Z")
    assert isinstance(result, float)


def test_parse_timestamp_none_for_empty() -> None:
    from general_ludd.connectors._util import parse_timestamp
    assert parse_timestamp(None) is None
    assert parse_timestamp("") is None
