"""Controller-side Ansible contracts for native SearXNG."""

from __future__ import annotations

import runpy
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from scripts.check_collection_python_boundary import scan_collections

_COLLECTIONS_DIR = str(Path(__file__).resolve().parents[2] / "collections")
if _COLLECTIONS_DIR not in sys.path:
    sys.path.insert(0, _COLLECTIONS_DIR)

from ansible_collections.general_ludd.travel.plugins.action import (  # noqa: E402
    _searxng as action_core,
)
from ansible_collections.general_ludd.travel.plugins.action.searxng_index import (  # noqa: E402
    ActionModule as IndexActionModule,
)
from ansible_collections.general_ludd.travel.plugins.action.searxng_index import (  # noqa: E402
    execute_action as execute_index_action,
)
from ansible_collections.general_ludd.travel.plugins.action.searxng_instance import (  # noqa: E402
    execute_action as execute_instance_action,
)
from ansible_collections.general_ludd.travel.plugins.action.searxng_search import (  # noqa: E402
    execute_action as execute_search_action,
)
from ansible_collections.general_ludd.travel.plugins.modules import (  # noqa: E402
    searxng_index as module_index,
)
from ansible_collections.general_ludd.travel.plugins.modules import (  # noqa: E402
    searxng_instance as module_instance,
)
from ansible_collections.general_ludd.travel.plugins.modules import (  # noqa: E402
    searxng_search as module_search,
)


class _Runtime:
    def __init__(self, *, settings_path: str | None, namespace: str) -> None:
        self.settings_path = settings_path
        self.namespace = namespace
        self.instance_uri = f"searx+python://{namespace}"
        self.process_pid = None
        self.running = False
        self.starts = 0
        self.stops = 0
        self.restarts = 0
        self.searches: list[dict[str, Any]] = []

    def start(self) -> bool:
        if self.running:
            return False
        self.running = True
        self.starts += 1
        return True

    def stop(self) -> bool:
        if not self.running:
            return False
        self.running = False
        self.stops += 1
        return True

    def restart(self) -> bool:
        if not self.running:
            return self.start()
        self.restarts += 1
        return True

    def is_running(self) -> bool:
        return self.running

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.searches.append({"query": query, **kwargs})
        return {
            "query": query,
            "results": [{"title": "native", "url": "https://example.test"}],
            "number_of_results": 1,
        }


def _factory(created: list[_Runtime]):
    def create(*, settings_path: str | None, namespace: str) -> _Runtime:
        runtime = _Runtime(settings_path=settings_path, namespace=namespace)
        created.append(runtime)
        return runtime

    return create


def test_instance_started_and_stopped_are_idempotent() -> None:
    registry: dict[str, _Runtime] = {}
    created: list[_Runtime] = []
    args = {"state": "started", "namespace": "ansible-tests", "settings_path": ""}

    first = execute_instance_action(args, registry=registry, runtime_factory=_factory(created))
    second = execute_instance_action(args, registry=registry, runtime_factory=_factory(created))
    stopped = execute_instance_action(
        {**args, "state": "stopped"},
        registry=registry,
        runtime_factory=_factory(created),
    )
    stopped_again = execute_instance_action(
        {**args, "state": "stopped"},
        registry=registry,
        runtime_factory=_factory(created),
    )

    assert first["changed"] is True
    assert second["changed"] is False
    assert stopped["changed"] is True
    assert stopped_again["changed"] is False
    assert len(created) == 1
    assert created[0].starts == 1
    assert created[0].stops == 1


def test_instance_check_mode_predicts_without_starting() -> None:
    registry: dict[str, _Runtime] = {}
    created: list[_Runtime] = []

    result = execute_instance_action(
        {"state": "started", "namespace": "check-mode"},
        check_mode=True,
        registry=registry,
        runtime_factory=_factory(created),
    )

    assert result["changed"] is True
    assert result["running"] is False
    assert result["check_mode"] is True
    assert created == []
    assert registry == {}


def test_instance_rejects_url_and_terraform_project_glue() -> None:
    for forbidden in ("searxng_url", "project_path", "terraform_project_path"):
        with pytest.raises(ValueError, match=forbidden):
            execute_instance_action(
                {"state": "started", "namespace": "boundary", forbidden: "unsafe"},
                registry={},
            )


def test_native_search_reuses_started_runtime_without_url() -> None:
    runtime = _Runtime(settings_path=None, namespace="shared")
    runtime.start()
    registry = {"shared": runtime}

    result = execute_search_action(
        {
            "query": "native query",
            "namespace": "shared",
            "category": "general",
            "max_results": 5,
        },
        registry=registry,
    )

    assert result["changed"] is False
    assert result["transport"] == "native"
    assert result["instance_uri"] == "searx+python://shared"
    assert result["results"][0]["title"] == "native"
    assert "search_url" not in result
    assert runtime.stops == 0


def test_ephemeral_native_search_always_cleans_up() -> None:
    created: list[_Runtime] = []

    result = execute_search_action(
        {"query": "ephemeral", "namespace": "ephemeral"},
        registry={},
        runtime_factory=_factory(created),
    )

    assert result["result_count"] == 1
    assert created[0].starts == 1
    assert created[0].stops == 1


def test_search_check_mode_has_no_runtime_or_network_side_effects() -> None:
    created: list[_Runtime] = []

    result = execute_search_action(
        {"query": "planned", "namespace": "check-search"},
        check_mode=True,
        registry={},
        runtime_factory=_factory(created),
    )

    assert result == {
        "changed": False,
        "check_mode": True,
        "query": "planned",
        "result_count": 0,
        "results": [],
        "transport": "native",
    }
    assert created == []


def test_index_action_injects_exact_controller_backend_results() -> None:
    manager = module_index.TravelIndexManager()
    created = execute_index_action(
        {
            "state": "present",
            "name": "travel-meta",
            "engines": "booking,tripadvisor",
        },
        manager=manager,
    )
    expected = [
        {
            "title": "Exact controller result",
            "url": "https://travel.test/offers/42",
            "engine": "booking",
            "score": 0.95,
        }
    ]
    calls: list[tuple[dict[str, Any], bool]] = []

    def search_executor(
        args: dict[str, Any],
        *,
        check_mode: bool = False,
    ) -> dict[str, Any]:
        calls.append((args, check_mode))
        return {"results": expected}

    queried = execute_index_action(
        {
            "state": "query",
            "name": "travel-meta",
            "query": "hotels in Tokyo",
            "max_results": 3,
        },
        manager=manager,
        search_executor=search_executor,
    )

    assert created["changed"] is True
    assert queried["results"] == expected
    assert queried["results"] is not expected
    assert queried["result_count"] == 1
    assert calls == [
        (
            {
                "query": "hotels in Tokyo",
                "engines": ["booking", "tripadvisor"],
                "max_results": 3,
                "transport": "native",
                "namespace": "gludd-travel",
            },
            False,
        )
    ]


def test_index_action_check_mode_has_no_mutation_or_backend_work() -> None:
    manager = module_index.TravelIndexManager()
    calls: list[dict[str, Any]] = []

    def search_executor(args: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        calls.append(args)
        return {"results": []}

    planned = execute_index_action(
        {"state": "present", "name": "planned"},
        check_mode=True,
        manager=manager,
        search_executor=search_executor,
    )

    assert planned == {
        "changed": True,
        "check_mode": True,
        "name": "planned",
        "state": "present",
    }
    assert manager.list_all() == []
    assert calls == []


def test_index_action_lifecycle_check_query_and_validation() -> None:
    manager = module_index.TravelIndexManager()

    assert execute_index_action(
        {"state": "list"}, manager=manager
    )["indices"] == []
    execute_index_action({"state": "present", "name": "bounded"}, manager=manager)
    assert execute_index_action(
        {"state": "present", "name": "bounded"}, manager=manager
    )["changed"] is False
    assert execute_index_action(
        {"state": "absent", "name": "bounded"},
        check_mode=True,
        manager=manager,
    )["changed"] is True
    assert manager.has("bounded") is True
    assert execute_index_action(
        {"state": "absent", "name": "bounded"}, manager=manager
    )["changed"] is True
    assert execute_index_action(
        {"state": "absent", "name": "bounded"}, manager=manager
    )["changed"] is False

    execute_index_action(
        {"state": "present", "name": "queryable", "engines": ["booking"]},
        manager=manager,
    )
    calls: list[tuple[dict[str, Any], bool]] = []

    def search_executor(
        args: dict[str, Any], *, check_mode: bool = False
    ) -> dict[str, Any]:
        calls.append((args, check_mode))
        return {"results": []}

    planned = execute_index_action(
        {
            "state": "query",
            "name": "queryable",
            "query": "planned",
            "remote_url": "https://search.test",
            "transport": "remote",
        },
        check_mode=True,
        manager=manager,
        search_executor=search_executor,
    )
    assert planned["check_mode"] is True
    assert calls[0][1] is True

    invalid_args = [
        {"name": 7},
        {"state": "invalid"},
        {"engines": 7},
        {"engines": ",,"},
        {"state": "query", "name": "queryable", "query": ""},
    ]
    for args in invalid_args:
        with pytest.raises((TypeError, ValueError)):
            execute_index_action(args, manager=manager)


def test_index_action_rejects_invalid_backend_and_action_module_delegates() -> None:
    manager = module_index.TravelIndexManager()
    execute_index_action({"state": "present", "name": "queryable"}, manager=manager)

    with pytest.raises(ValueError, match="invalid results"):
        execute_index_action(
            {"state": "query", "name": "queryable", "query": "invalid"},
            manager=manager,
            search_executor=lambda *_args, **_kwargs: {"results": "invalid"},
        )

    action = object.__new__(IndexActionModule)
    result = action._execute(
        {"state": "list"},
        check_mode=True,
    )
    assert result["check_mode"] is True


def test_index_module_stub_fails_closed_without_action_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Module:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["supports_check_mode"] is True
            assert ("transport", "remote", ("remote_url",)) in kwargs["required_if"]

        def fail_json(self, **kwargs: Any) -> None:
            raise RuntimeError(kwargs["msg"])

    monkeypatch.setattr(module_index, "AnsibleModule", Module)

    with pytest.raises(RuntimeError, match="controller-side action plugin"):
        module_index.main()


def test_remote_search_is_an_explicit_compatibility_mode() -> None:
    with pytest.raises(ValueError, match="remote_url is required"):
        execute_search_action({"query": "remote", "transport": "remote"}, registry={})
    with pytest.raises(ValueError, match="remote_url is only valid"):
        execute_search_action(
            {"query": "native", "transport": "native", "remote_url": "https://search.example"},
            registry={},
        )


def test_role_has_no_raw_url_docker_or_terraform_task_dependency() -> None:
    role = (
        Path(__file__).resolve().parents[2]
        / "collections/ansible_collections/general_ludd/travel/roles/searxng_setup/tasks/main.yml"
    ).read_text(encoding="utf-8")

    assert "general_ludd.travel.searxng_instance:" in role
    assert "ansible.builtin.uri:" not in role
    assert "docker compose" not in role.lower()
    assert "terraform" not in role.lower()
    assert "project_path" not in role


def test_native_make_targets_and_molecule_have_no_container_or_raw_http_escape() -> None:
    root = Path(__file__).resolve().parents[2]
    makefile = (root / "make/90-infrastructure-and-services.mk").read_text(
        encoding="utf-8"
    )
    section = makefile.split("# SearXNG research backend", 1)[1].split(
        "# --- Service Discovery ---", 1
    )[0]
    converge = (
        root
        / "collections/ansible_collections/general_ludd/travel/roles/searxng_setup/molecule/default/converge.yml"
    ).read_text(encoding="utf-8")

    assert "docker" not in section.lower()
    assert "curl" not in section.lower()
    assert "SEARXNG_URL" not in section
    assert "searx-up: searx-start" in section
    assert "searx-down: searx-stop" in section
    assert "searx-molecule:" in section
    assert "ignore_errors" not in converge
    assert "check_mode: true" in converge
    assert "ansible.builtin.assert:" in converge


def test_obsolete_searx_container_artifacts_are_removed() -> None:
    root = Path(__file__).resolve().parents[2]

    assert not (root / "infra/searxng/docker-compose.yml").exists()
    assert not (root / "infra/searxng/settings.yml").exists()
    assert not (
        root
        / "collections/ansible_collections/general_ludd/travel/roles/searxng_setup/templates/docker-compose.yml.j2"
    ).exists()


def test_travel_collection_has_zero_python_boundary_findings() -> None:
    """The shipped collection must not depend on the Gludd source checkout."""
    collection_root = (
        Path(__file__).resolve().parents[2]
        / "collections/ansible_collections/general_ludd/travel"
    )

    assert scan_collections(collection_root) == []


def test_instance_status_restart_and_validation_paths() -> None:
    registry: dict[str, _Runtime] = {}
    created: list[_Runtime] = []

    status = execute_instance_action(
        {"state": "status", "namespace": "lifecycle"},
        registry=registry,
        runtime_factory=_factory(created),
    )
    restarted = execute_instance_action(
        {"state": "restarted", "namespace": "lifecycle"},
        registry=registry,
        runtime_factory=_factory(created),
    )
    restarted_again = execute_instance_action(
        {"state": "restarted", "namespace": "lifecycle"},
        registry=registry,
        runtime_factory=_factory(created),
    )

    assert status["changed"] is False
    assert restarted["changed"] is True
    assert restarted_again["changed"] is True
    assert created[0].restarts == 1

    with pytest.raises(ValueError, match="state must"):
        execute_instance_action({"state": "invalid"}, registry={})
    with pytest.raises(ValueError, match="namespace"):
        execute_instance_action({"namespace": ""}, registry={})
    with pytest.raises(ValueError, match="namespace"):
        execute_instance_action(
            {"namespace": "../escape"},
            check_mode=True,
            registry={},
        )
    with pytest.raises(TypeError, match="settings_path"):
        execute_instance_action({"settings_path": 7}, registry={})


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"query": ""}, "non-empty"),
        ({"query": "q", "transport": "socket"}, "native or remote"),
        ({"query": "q", "searxng_url": "https://old.example"}, "retired"),
        ({"query": "q", "timeout": 0}, "between 0 and 120"),
        ({"query": "q", "namespace": ""}, "namespace"),
        ({"query": "q", "settings_path": 7}, "settings_path"),
        ({"query": "q", "category": ""}, "category"),
        ({"query": "q", "engines": 7}, "engines"),
    ],
)
def test_search_action_rejects_ambiguous_or_invalid_inputs(
    args: dict[str, object],
    message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        execute_search_action(args, registry={}, runtime_factory=_factory([]))


def test_search_action_explicit_remote_transport_and_engine_parsing() -> None:
    remote = _Runtime(settings_path=None, namespace="remote")
    created: list[tuple[str, float]] = []

    def remote_factory(*, base_url: str, timeout: float) -> _Runtime:
        created.append((base_url, timeout))
        return remote

    result = execute_search_action(
        {
            "query": "remote rail",
            "transport": "remote",
            "remote_url": "https://search.example",
            "timeout": 4,
            "engines": "duckduckgo, brave",
        },
        registry={},
        remote_factory=remote_factory,
    )

    assert result["transport"] == "remote"
    assert created == [("https://search.example", 4.0)]
    assert remote.searches[0]["engines"] == ("duckduckgo", "brave")
    assert remote.stops == 1


def test_search_action_rejects_invalid_result_schema_and_cleans_up() -> None:
    class InvalidRuntime(_Runtime):
        def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
            del query, kwargs
            return {"results": "not-a-list"}

    created: list[InvalidRuntime] = []

    def factory(*, settings_path: str | None, namespace: str) -> InvalidRuntime:
        runtime = InvalidRuntime(settings_path=settings_path, namespace=namespace)
        created.append(runtime)
        return runtime

    with pytest.raises(ValueError, match="results must be a list"):
        execute_search_action(
            {"query": "invalid schema"},
            registry={},
            runtime_factory=factory,
        )

    assert created[0].stops == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"category": ""},
        {"max_results": 0},
        {"max_results": 101},
        {"max_results": True},
        {"safe_search": 3},
        {"page": 0},
        {"page": 101},
        {"language": "../unsafe"},
        {"engines": [f"engine-{index}" for index in range(21)]},
    ],
)
def test_search_action_rejects_bounded_inputs_before_allocating(
    extra: dict[str, object],
) -> None:
    created: list[_Runtime] = []

    with pytest.raises((TypeError, ValueError)):
        execute_search_action(
            {"query": "bounded", **extra},
            registry={},
            runtime_factory=_factory(created),
        )

    assert created == []


@dataclass
class _LegacyResult:
    title: str = "result"
    url: str = "https://example.test"
    content: str = "content"
    engine: str = "engine"


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("flights", "parsed-flights"),
        ("hotels", "parsed-hotels"),
        ("events", "parsed-events"),
        ("general", "result"),
    ],
)
def test_legacy_remote_helper_structured_parser_branches(
    monkeypatch: pytest.MonkeyPatch,
    category: str,
    expected: str,
) -> None:
    class Client:
        def __init__(self, *, base_url: str, timeout: int) -> None:
            del timeout
            self.base_url = base_url

        def search(self, **_kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(results=[_LegacyResult()])

    class Parser:
        def parse_flights(self, *_args: Any) -> list[dict[str, str]]:
            return [{"title": "parsed-flights"}]

        def parse_hotels(self, *_args: Any) -> list[dict[str, str]]:
            return [{"title": "parsed-hotels"}]

        def parse_events(self, *_args: Any) -> list[dict[str, str]]:
            return [{"title": "parsed-events"}]

    monkeypatch.setattr(module_search, "SearXNGClient", Client)
    monkeypatch.setattr(module_search, "JsonOutputParser", Parser)

    parsed, raw, search_url = module_search.search_searxng(
        query="query",
        category=category,
        searxng_url="https://search.example",
        engines="",
        max_results=1,
        safe_search=1,
        language="en",
        timeout=3,
        structured=True,
    )

    assert parsed[0]["title"] == expected
    assert raw[0]["title"] == "result"
    assert search_url.startswith("https://search.example/search")


def test_module_stub_fails_closed_without_action_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Module:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["supports_check_mode"] is True

        def fail_json(self, **kwargs: Any) -> None:
            raise RuntimeError(kwargs["msg"])

    monkeypatch.setattr(module_search, "AnsibleModule", Module)

    with pytest.raises(RuntimeError, match="controller-side action plugin"):
        module_search.main()


def test_instance_module_stub_fails_closed_when_executed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ansible.module_utils.basic as ansible_basic

    class Module:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["supports_check_mode"] is True

        def fail_json(self, **kwargs: Any) -> None:
            raise RuntimeError(kwargs["msg"])

    monkeypatch.setattr(ansible_basic, "AnsibleModule", Module)

    with pytest.raises(RuntimeError, match="controller-side action plugin"):
        module_instance.main()
    with pytest.raises(RuntimeError, match="controller-side action plugin"):
        runpy.run_path(module_instance.__file__, run_name="__main__")


def test_shared_action_boundary_merges_success_and_expected_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SuccessfulAction(action_core.ControllerSearxAction):
        def _execute(
            self,
            args: dict[str, Any],
            *,
            check_mode: bool,
        ) -> dict[str, Any]:
            return {"changed": not check_mode, "value": args["value"]}

    class FailedAction(action_core.ControllerSearxAction):
        def _execute(
            self,
            args: dict[str, Any],
            *,
            check_mode: bool,
        ) -> dict[str, Any]:
            del args, check_mode
            raise ValueError("bounded failure")

    monkeypatch.setattr(
        action_core._ActionBase,
        "run",
        lambda _self, _tmp=None, _task_vars=None: {"base": True},
    )
    successful = object.__new__(SuccessfulAction)
    successful._task = SimpleNamespace(args={"value": 7}, check_mode=False)
    failed = object.__new__(FailedAction)
    failed._task = SimpleNamespace(args={}, check_mode=False)

    assert successful.run() == {"base": True, "changed": True, "value": 7}
    assert failed.run() == {
        "base": True,
        "changed": False,
        "failed": True,
        "msg": "bounded failure",
    }


def test_shared_action_base_requires_plugin_execution() -> None:
    action = object.__new__(action_core.ControllerSearxAction)

    with pytest.raises(NotImplementedError):
        action._execute({}, check_mode=False)
