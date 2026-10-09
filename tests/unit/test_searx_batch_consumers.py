"""Collection-native SearXNG batch-consumer contracts."""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from ansible_collections.general_ludd.travel.plugins.action import (
    searxng_batch as batch_action,
)
from ansible_collections.general_ludd.travel.plugins.action.searxng_batch import (
    execute_action,
)
from ansible_collections.general_ludd.travel.plugins.modules import searxng_batch

_ROOT = Path(__file__).resolve().parents[2]
_CONSUMER_FILES = (
    "collections/ansible_collections/general_ludd/business/roles/"
    "entity_research/tasks/assets.yml",
    "collections/ansible_collections/general_ludd/business/roles/"
    "entity_research/tasks/exposure.yml",
    "collections/ansible_collections/general_ludd/business/roles/"
    "entity_research/tasks/risks.yml",
    "collections/ansible_collections/general_ludd/business/roles/"
    "entity_research/tasks/associations.yml",
    "collections/ansible_collections/general_ludd/business/roles/"
    "entity_research/tasks/searx_monitor.yml",
    "collections/ansible_collections/general_ludd/business/roles/"
    "entity_research/tasks/discover.yml",
    "collections/ansible_collections/general_ludd/business/roles/"
    "entity_research/tasks/demographics.yml",
    "collections/ansible_collections/general_ludd/security/roles/"
    "audit_framework/tasks/searx_update.yml",
    "collections/ansible_collections/general_ludd/infrastructure/roles/"
    "service_discovery/tasks/search_term.yml",
)
_SEARX_URL_MARKERS = (
    "entity_data_sources.searx",
    "_sx_search_url",
    "searx_url }}/search",
)
_SHARED_ENTITY_BATCH = (
    "collections/ansible_collections/general_ludd/business/roles/"
    "entity_research/tasks/searx_batch.yml"
)
_SERVICE_DISCOVERY_MAIN = (
    "collections/ansible_collections/general_ludd/infrastructure/roles/"
    "service_discovery/tasks/main.yml"
)
_EXPECTED_LEGACY_RESULTS = {
    _CONSUMER_FILES[0]: {
        "searx_network_result": "searx_network_result",
        "searx_patent_result": "searx_patent_result",
        "searx_tech_result": "searx_tech_result",
        "searx_funding_result": "searx_funding_result",
    },
    _CONSUMER_FILES[1]: {
        "searx_breach_result": "searx_breach_result",
        "searx_social_result": "searx_social_result",
    },
    _CONSUMER_FILES[2]: {
        "searx_financial_risk": "searx_financial_risk",
        "searx_security_risk": "searx_security_risk",
        "searx_personnel_risk": "searx_personnel_risk",
        "searx_market_risk": "searx_market_risk",
        "searx_legal_risk": "searx_legal_risk",
        "searx_glassdoor_risk": "searx_glassdoor_risk",
    },
    _CONSUMER_FILES[3]: {
        "searx_board_result": "searx_board_result",
        "searx_investor_result": "searx_investor_result",
        "searx_competitor_result": "searx_competitor_result",
        "searx_corporate_result": "searx_corporate_result",
    },
    _CONSUMER_FILES[4]: {
        "searx_monitor_scan": "searx_monitor_scan",
        "searx_acquisition_scan": "searx_acquisition_scan",
        "searx_breach_scan": "searx_breach_scan",
        "searx_funding_scan": "searx_funding_scan",
        "searx_layoff_scan": "searx_layoff_scan",
        "searx_leadership_scan": "searx_leadership_scan",
        "searx_lawsuit_scan": "searx_lawsuit_scan",
        "searx_bankruptcy_scan": "searx_bankruptcy_scan",
    },
    _CONSUMER_FILES[5]: {"searx_discovery_result": "searx_discovery_result"},
    _CONSUMER_FILES[6]: {
        "searx_market_result": "searx_market_result",
        "searx_acquisition_result": "searx_acquisition_result",
        "searx_geo_result": "searx_geo_result",
        "searx_bizmodel_result": "searx_bizmodel_result",
    },
    _CONSUMER_FILES[7]: {"search_response": "_sx_search_response"},
    _CONSUMER_FILES[8]: {"search_response": "_sd_response"},
}


def _raw_searx_uri_consumers() -> list[str]:
    consumers: list[str] = []
    for relative_path in (*_CONSUMER_FILES, _SHARED_ENTITY_BATCH):
        tasks = yaml.safe_load((_ROOT / relative_path).read_text(encoding="utf-8"))
        assert isinstance(tasks, list), relative_path
        for task in tasks:
            if not isinstance(task, dict):
                continue
            uri_args = task.get("ansible.builtin.uri")
            if not isinstance(uri_args, dict):
                continue
            url = uri_args.get("url", "")
            if any(marker in str(url) for marker in _SEARX_URL_MARKERS):
                consumers.append(f"{relative_path}: {task.get('name', '<unnamed>')}")
    return consumers


def _task_batch_requests(task: dict[str, object]) -> list[dict[str, object]]:
    batch = task.get("general_ludd.travel.searxng_batch")
    if isinstance(batch, dict):
        return cast(list[dict[str, object]], batch.get("requests", []))
    variables = task.get("vars")
    if isinstance(variables, dict):
        return cast(
            list[dict[str, object]],
            variables.get("entity_research_searx_batch_requests", []),
        )
    return []


def test_roles_have_no_raw_searx_uri_consumers() -> None:
    """All 31 legacy HTTP searches must cross the controller action boundary."""
    consumers = _raw_searx_uri_consumers()

    assert consumers == [], "raw SearXNG URI consumers remain:\n" + "\n".join(consumers)


def test_roles_preserve_all_31_legacy_result_schemas() -> None:
    """Every removed URI register remains available with its prior shape."""
    preserved_count = 0

    for relative_path, expected in _EXPECTED_LEGACY_RESULTS.items():
        tasks = yaml.safe_load((_ROOT / relative_path).read_text(encoding="utf-8"))
        batch_ids = {
            request["id"]
            for task in tasks
            if isinstance(task, dict)
            for request in _task_batch_requests(task)
        }
        assert batch_ids == set(expected), relative_path
        if "entity_research" in relative_path:
            preserved_count += len(expected)
            continue

        facts = {
            key: value
            for task in tasks
            if isinstance(task, dict)
            for fact_block in [task.get("ansible.builtin.set_fact")]
            if isinstance(fact_block, dict)
            for key, value in fact_block.items()
        }
        for response_id, legacy_variable in expected.items():
            assert legacy_variable in facts, relative_path
            assert f".responses.{response_id}" in str(facts[legacy_variable])
            preserved_count += 1

    assert preserved_count == 31

    shared_tasks = yaml.safe_load(
        (_ROOT / _SHARED_ENTITY_BATCH).read_text(encoding="utf-8")
    )
    assert len(shared_tasks) == 2
    assert "general_ludd.travel.searxng_batch" in shared_tasks[0]
    preservation = shared_tasks[1]["ansible.builtin.set_fact"]
    assert list(preservation) == ["{{ entity_research_batch_request.id }}"]
    assert "responses[entity_research_batch_request.id]" in next(
        iter(preservation.values())
    )


def test_service_discovery_requires_url_only_for_explicit_remote_transport() -> None:
    """Native service discovery must not depend on an implicit HTTP endpoint."""
    tasks = yaml.safe_load((_ROOT / _SERVICE_DISCOVERY_MAIN).read_text(encoding="utf-8"))
    validation = tasks[0]["ansible.builtin.assert"]["that"]

    assert "searx_transport in ['native', 'remote']" in validation
    assert "searx_transport != 'remote' or searx_url is defined" in validation
    assert "searx_url is defined" not in validation


class _Runtime:
    def __init__(self, *, namespace: str = "batch-tests") -> None:
        self.namespace = namespace
        self.instance_uri = f"searx+python://{namespace}"
        self.process_pid = None
        self.started = 0
        self.stopped = 0
        self.searches: list[tuple[str, dict[str, object]]] = []
        self.raise_on: str | None = None
        self.invalid_results = False
        self.result_count = 1
        self.payload_override: object | None = None

    def start(self) -> bool:
        self.started += 1
        return True

    def stop(self) -> bool:
        self.stopped += 1
        return True

    def search(self, query: str, **kwargs: object) -> dict[str, object]:
        self.searches.append((query, kwargs))
        if query == self.raise_on:
            raise RuntimeError("bounded search failure")
        if self.payload_override is not None:
            return cast(dict[str, object], self.payload_override)
        if self.invalid_results:
            return {"results": "invalid"}
        return {
            "query": query,
            "results": [
                {"title": query, "url": f"https://{query}-{index}.test"}
                for index in range(self.result_count)
            ],
            "number_of_results": self.result_count,
        }


def _native_factory(created: list[_Runtime]) -> Any:
    def create(*, settings_path: str | None, namespace: str) -> _Runtime:
        assert settings_path is None
        runtime = _Runtime(namespace=namespace)
        created.append(runtime)
        return runtime

    return create


def test_batch_reuses_one_native_runtime_and_preserves_uri_result_schema() -> None:
    created: list[_Runtime] = []

    result = execute_action(
        {
            "namespace": "entity-research",
            "requests": [
                {
                    "id": "network",
                    "query": "network assets",
                    "categories": ["general", "it"],
                    "max_results": 12,
                },
                {
                    "id": "news",
                    "query": "security news",
                    "engines": ["duckduckgo"],
                    "page": 2,
                },
            ],
        },
        registry={},
        runtime_factory=_native_factory(created),
    )

    assert result["changed"] is False
    assert result["query_count"] == 2
    assert result["result_count"] == 2
    assert result["transport"] == "native"
    assert result["instance_uri"] == "searx+python://entity-research"
    assert result["responses"] == {
        "network": {
            "changed": False,
            "failed": False,
            "status": 200,
            "json": {
                "query": "network assets",
                "results": [
                    {
                        "title": "network assets",
                        "url": "https://network assets-0.test",
                    }
                ],
                "number_of_results": 1,
            },
        },
        "news": {
            "changed": False,
            "failed": False,
            "status": 200,
            "json": {
                "query": "security news",
                "results": [
                    {
                        "title": "security news",
                        "url": "https://security news-0.test",
                    }
                ],
                "number_of_results": 1,
            },
        },
    }
    assert created[0].started == 1
    assert created[0].stopped == 1
    assert created[0].searches[0][1]["categories"] == ("general", "it")
    assert created[0].searches[1][1]["engines"] == ("duckduckgo",)


@pytest.mark.parametrize(
    "requests",
    [
        [],
        [{"id": f"q{index}", "query": "q"} for index in range(33)],
        [{"id": "duplicate", "query": "one"}, {"id": "duplicate", "query": "two"}],
        [{"id": "unsafe-id", "query": "q"}],
        [{"id": "query", "query": "x" * 2049}],
        [{"id": "query", "query": "q", "max_results": 101}],
        [{"id": "query", "query": "q", "categories": [f"c{n}" for n in range(21)]}],
        [{"id": "query", "query": "q", "engines": [f"e{n}" for n in range(21)]}],
        [
            {"id": f"q{index}", "query": "q", "max_results": 100}
            for index in range(6)
        ],
    ],
)
def test_batch_rejects_bounds_before_allocating(
    requests: list[dict[str, object]],
) -> None:
    created: list[_Runtime] = []

    with pytest.raises((TypeError, ValueError)):
        execute_action(
            {"requests": requests},
            registry={},
            runtime_factory=_native_factory(created),
        )

    assert created == []


@pytest.mark.parametrize(
    "raw_request",
    [
        "not-a-mapping",
        {"id": "query", "query": "q", "unsupported": True},
        {"id": "query", "query": "q", "categories": ""},
        {"id": "query", "query": "q", "categories": [1]},
        {"id": "query", "query": "q", "categories": 1},
    ],
)
def test_batch_rejects_malformed_requests_before_allocating(
    raw_request: object,
) -> None:
    created: list[_Runtime] = []

    with pytest.raises((TypeError, ValueError)):
        execute_action(
            {"requests": [raw_request]},
            registry={},
            runtime_factory=_native_factory(created),
        )

    assert created == []


@pytest.mark.parametrize(
    "extra",
    [
        {"transport": "socket"},
        {"transport": "native", "remote_url": "https://search.test"},
        {"transport": "remote"},
        {"searxng_url": "https://legacy.test"},
        {"timeout": 0},
        {"settings_path": 7},
        {"settings_path": False},
        {"namespace": 7},
        {"unexpected": True},
    ],
)
def test_batch_rejects_ambiguous_transport_before_allocating(
    extra: dict[str, object],
) -> None:
    created: list[_Runtime] = []

    with pytest.raises((TypeError, ValueError)):
        execute_action(
            {"requests": [{"id": "query", "query": "q"}], **extra},
            registry={},
            runtime_factory=_native_factory(created),
        )

    assert created == []


def test_batch_remote_transport_is_explicit_and_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[tuple[str, float]] = []
    instances: list[_Runtime] = []

    class Adapter(_Runtime):
        def __init__(self, *, base_url: str, timeout: float) -> None:
            super().__init__(namespace="remote")
            created.append((base_url, timeout))
            instances.append(self)

    monkeypatch.setattr(batch_action, "RemoteSearxAdapter", Adapter)

    result = execute_action(
        {
            "transport": "remote",
            "remote_url": "https://search.test",
            "timeout": 5,
            "requests": [{"id": "query", "query": "remote query"}],
        },
    )

    assert result["transport"] == "remote"
    assert created == [("https://search.test", 5.0)]
    assert instances[0].started == 1
    assert instances[0].stopped == 1


def test_batch_reuses_registry_owned_runtime_without_releasing_it() -> None:
    runtime = _Runtime(namespace="retained")

    result = execute_action(
        {
            "namespace": "retained",
            "requests": [
                {"id": "query", "query": "registry", "categories": "general"}
            ],
        },
        registry={"retained": runtime},
    )

    assert result["result_count"] == 1
    assert runtime.started == 0
    assert runtime.stopped == 0


def test_batch_check_mode_validates_without_allocating() -> None:
    created: list[_Runtime] = []

    result = execute_action(
        {"requests": [{"id": "query", "query": "planned"}]},
        check_mode=True,
        registry={},
        runtime_factory=_native_factory(created),
    )

    assert result == {
        "changed": False,
        "check_mode": True,
        "query_count": 1,
        "result_count": 0,
        "responses": {
            "query": {
                "changed": False,
                "failed": False,
                "skipped": True,
                "status": 0,
                "json": {
                    "query": "planned",
                    "results": [],
                    "number_of_results": 0,
                },
            }
        },
        "transport": "native",
    }
    assert created == []


def test_batch_failure_is_atomic_and_releases_owned_runtime() -> None:
    runtime = _Runtime()
    runtime.raise_on = "fail"

    with pytest.raises(RuntimeError, match="bounded search failure"):
        execute_action(
            {
                "requests": [
                    {"id": "first", "query": "ok"},
                    {"id": "second", "query": "fail"},
                ]
            },
            registry={},
            runtime_factory=lambda **_kwargs: runtime,
        )

    assert runtime.started == 1
    assert runtime.stopped == 1


def test_batch_rejects_invalid_runtime_schema_and_releases_runtime() -> None:
    runtime = _Runtime()
    runtime.invalid_results = True

    with pytest.raises(ValueError, match="results must be a list"):
        execute_action(
            {"requests": [{"id": "query", "query": "invalid"}]},
            registry={},
            runtime_factory=lambda **_kwargs: runtime,
        )

    assert runtime.stopped == 1


@pytest.mark.parametrize(
    "payload, message",
    [
        (42, "response must be a mapping"),
        ({"results": ["not-a-mapping"]}, "must contain only mappings"),
        ({"results": [], "number_of_results": True}, "must be an integer"),
        ({"results": [], "number_of_results": -1}, "must not be negative"),
    ],
)
def test_batch_rejects_malformed_runtime_payloads(
    payload: object,
    message: str,
) -> None:
    runtime = _Runtime()
    runtime.payload_override = payload

    with pytest.raises(ValueError, match=message):
        execute_action(
            {"requests": [{"id": "query", "query": "invalid"}]},
            registry={},
            runtime_factory=lambda **_kwargs: runtime,
        )

    assert runtime.stopped == 1


def test_batch_fills_missing_number_of_results() -> None:
    runtime = _Runtime()
    runtime.payload_override = {"query": "bounded", "results": []}

    result = execute_action(
        {"requests": [{"id": "query", "query": "bounded"}]},
        registry={},
        runtime_factory=lambda **_kwargs: runtime,
    )

    assert result["responses"]["query"]["json"]["number_of_results"] == 0


def test_batch_rejects_runtime_results_above_requested_ceiling() -> None:
    runtime = _Runtime()
    runtime.result_count = 11

    with pytest.raises(ValueError, match="per-query result count exceeds 10"):
        execute_action(
            {"requests": [{"id": "query", "query": "overflow"}]},
            registry={},
            runtime_factory=lambda **_kwargs: runtime,
        )

    assert runtime.stopped == 1


def test_batch_module_stub_fails_closed_without_action_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Module:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["supports_check_mode"] is True
            assert ("transport", "remote", ("remote_url",)) in kwargs["required_if"]

        def fail_json(self, **kwargs: object) -> None:
            raise RuntimeError(str(kwargs["msg"]))

    monkeypatch.setattr(searxng_batch, "AnsibleModule", Module)

    with pytest.raises(RuntimeError, match="controller-side action plugin"):
        searxng_batch.main()


def test_batch_action_plugin_delegates_to_pure_executor() -> None:
    action = object.__new__(batch_action.ActionModule)
    result = action._execute(
        {"requests": [{"id": "query", "query": "check"}]},
        check_mode=True,
    )

    assert result["check_mode"] is True


def test_batch_module_main_guard_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Module:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def fail_json(self, **kwargs: object) -> None:
            raise RuntimeError(str(kwargs["msg"]))

    monkeypatch.setattr("ansible.module_utils.basic.AnsibleModule", Module)

    with pytest.raises(RuntimeError, match="controller-side action plugin"):
        runpy.run_path(str(Path(searxng_batch.__file__)), run_name="__main__")
