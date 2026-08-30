from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar, cast

import pytest
import yaml

ROLE_ROOT = Path("collections/ansible_collections/general_ludd/agent/roles/local_game_gen")
SCENARIO_ROOT = Path("molecule/playbooks/local_game_gen/default")
MODULE_PATH = Path("collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_local_model.py")
MOCK_DAEMON = Path("molecule/mock_daemon/server.py")


def _yaml(path: Path) -> Any:
    value = yaml.safe_load(path.read_text())
    assert value is not None
    return value


def _tasks(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in items:
        flattened.append(item)
        for key in ("block", "rescue", "always"):
            nested = item.get(key)
            if isinstance(nested, list):
                flattened.extend(_tasks(cast(list[dict[str, Any]], nested)))
    return flattened


def _module() -> ModuleType:
    return importlib.import_module(
        "ansible_collections.general_ludd.agent.plugins.modules.gludd_local_model"
    )


class FakeClient:
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response = response or {"_status": 200}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((path, payload))
        return dict(self.response)


def test_role_delegates_model_lifecycle_to_gludd_daemon() -> None:
    text = (ROLE_ROOT / "tasks/main.yml").read_text()
    lowered = text.lower()
    for forbidden in ("ansible.builtin.pip", "llama_cpp.server", "nohup", "kill -term"):
        assert forbidden not in lowered
    main_tasks = _tasks(
        cast(list[dict[str, Any]], _yaml(ROLE_ROOT / "tasks/main.yml"))
    )
    generate_tasks = _tasks(
        cast(list[dict[str, Any]], _yaml(ROLE_ROOT / "tasks/generate_and_verify.yml"))
    )

    def actions(tasks: list[dict[str, Any]]) -> list[str]:
        return [
            str(cast(dict[str, Any], task["general_ludd.agent.gludd_local_model"])["action"])
            for task in tasks
            if "general_ludd.agent.gludd_local_model" in task
        ]

    assert actions(main_tasks) == ["download", "serve", "shutdown"]
    assert actions(generate_tasks) == ["shutdown", "download", "serve", "consume"]


def test_role_cleanup_is_always_daemon_owned() -> None:
    tasks = cast(list[dict[str, Any]], _yaml(ROLE_ROOT / "tasks/main.yml"))
    always = cast(list[dict[str, Any]], tasks[-1]["always"])
    shutdown = [
        task["general_ludd.agent.gludd_local_model"]
        for task in always
        if "general_ludd.agent.gludd_local_model" in task
    ]
    assert shutdown == [{
        "action": "shutdown",
        "daemon_url": "{{ daemon_url }}",
        "psk": "{{ psk }}",
        "server_id": "{{ _local_model_server.server_id }}",
        "timeout": "{{ daemon_timeout }}",
    }]


def test_skipped_fallback_cannot_overwrite_primary_server_registration() -> None:
    tasks = _tasks(
        cast(
            list[dict[str, Any]],
            _yaml(ROLE_ROOT / "tasks/generate_and_verify.yml"),
        )
    )
    fallback_serve = next(
        task
        for task in tasks
        if task.get("general_ludd.agent.gludd_local_model", {}).get("action")
        == "serve"
    )
    assert fallback_serve["register"] == "_fallback_local_model_server"
    assert any(
        task.get("ansible.builtin.set_fact", {}).get("_local_model_server")
        == "{{ _fallback_local_model_server }}"
        for task in tasks
    )


def test_verification_commands_preserve_python_source_as_argv() -> None:
    tasks = _tasks(
        cast(
            list[dict[str, Any]],
            _yaml(ROLE_ROOT / "tasks/generate_and_verify.yml"),
        )
    )
    verify_tasks = [
        task
        for task in tasks
        if task.get("name", "").startswith("Verify")
        and "acceptance engine" not in task.get("name", "").lower()
        and "ansible.builtin.command" in task
    ]
    assert len(verify_tasks) >= 3
    for task in verify_tasks:
        command = task["ansible.builtin.command"]
        assert isinstance(command, dict)
        argv = command["argv"]
        assert argv[1] == "-c"
        assert "\n" in argv[2]
        compile(argv[2], "<local-game-verify>", "exec")


def test_role_declares_daemon_contract_without_runtime_installer() -> None:
    defaults = cast(dict[str, Any], _yaml(ROLE_ROOT / "defaults/main.yml"))
    assert defaults["daemon_url"] == "http://127.0.0.1:8000"
    assert defaults["psk"] == ""
    assert int(defaults["daemon_timeout"]) > 0
    assert defaults["model_source"] == "huggingface"


def test_molecule_verifier_preserves_python_source_as_argv() -> None:
    plays = cast(list[dict[str, Any]], _yaml(SCENARIO_ROOT / "verify.yml"))
    tasks = _tasks(cast(list[dict[str, Any]], plays[0]["tasks"]))
    verify_commands = [
        task["ansible.builtin.command"]
        for task in tasks
        if "ansible.builtin.command" in task
    ]
    assert len(verify_commands) == 3
    for command in verify_commands:
        assert isinstance(command, dict)
        argv = command["argv"]
        assert argv[1] == "-c"
        compile(argv[2], "<local-game-molecule-verify>", "exec")


def test_molecule_scenario_owns_mock_daemon_and_backstops_cleanup() -> None:
    converge = (SCENARIO_ROOT / "converge.yml").read_text()
    sequence = cast(dict[str, Any], _yaml(SCENARIO_ROOT / "molecule.yml"))["scenario"]["test_sequence"]
    assert "mock_daemon_start.yml" in converge
    assert "mock_daemon_stop.yml" in converge
    assert 'daemon_url: "{{ mock_daemon_url }}"' in converge
    assert 'psk: "{{ molecule_mock_daemon_psk }}"' in converge
    assert "fail_on_rejection: true" in converge
    assert "cleanup" in sequence and "destroy" in sequence
    assert (SCENARIO_ROOT / "cleanup.yml").exists()
    assert (SCENARIO_ROOT / "destroy.yml").exists()


def test_typed_module_and_mock_routes_exist() -> None:
    module_text = MODULE_PATH.read_text()
    mock_text = MOCK_DAEMON.read_text()
    for endpoint in (
        "/admin/models/local/download",
        "/admin/models/local/serve",
        "/admin/models/local/consume",
        "/admin/models/local/shutdown",
    ):
        assert endpoint in module_text
        assert endpoint in mock_text
    assert "from general_ludd" not in module_text


@pytest.mark.parametrize(
    ("action", "expected_path", "params"),
    [
        ("download", "/admin/models/local/download", {"model_id": "org/model", "filename": "m.gguf"}),
        ("serve", "/admin/models/local/serve", {"model_id": "org/model", "model_path": "/m.gguf"}),
        ("consume", "/admin/models/local/consume", {"server_id": "srv-1", "prompt": "code"}),
        ("shutdown", "/admin/models/local/shutdown", {"server_id": "srv-1"}),
    ],
)
def test_execute_action_routes_exact_payload(
    action: str, expected_path: str, params: dict[str, Any]
) -> None:
    module = _module()
    client = FakeClient({"_status": 200, "server_id": "srv-1", "text": "ok"})
    response = module.execute_action(client, action, params)
    assert response["server_id"] == "srv-1"
    assert client.calls == [(expected_path, params)]


def test_execute_action_rejects_unknown_action() -> None:
    with pytest.raises(ValueError, match="unsupported local-model action"):
        _module().execute_action(FakeClient(), "install", {})


class ExitResult(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload


class FakeAnsibleModule:
    params_config: ClassVar[dict[str, Any]] = {}
    check_mode_config: ClassVar[bool] = False

    params: dict[str, Any]
    check_mode: bool

    def __init__(self, **kwargs: Any) -> None:
        self.params = dict(type(self).params_config)
        self.check_mode = type(self).check_mode_config

    def exit_json(self, **kwargs: Any) -> None:
        raise ExitResult(kwargs)

    def fail_json(self, **kwargs: Any) -> None:
        raise ExitResult(kwargs)


def _base_params(action: str) -> dict[str, Any]:
    return {
        "action": action, "daemon_url": "http://127.0.0.1:8765",
        "psk": "secret", "timeout": 45, "model_id": "org/model",
        "model_path": "/models/m.gguf", "filename": "m.gguf",
        "source": "huggingface", "server_id": "srv-1", "prompt": "code",
        "max_tokens": 128, "port": 12001, "startup_timeout": 30,
        "gpu_layers": 0, "context_size": 2048,
    }


def test_main_success_uses_shared_authenticated_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    FakeAnsibleModule.check_mode_config = False
    FakeAnsibleModule.params_config = _base_params("serve")
    client = FakeClient({"_status": 201, "server_id": "srv-1", "status": "running"})
    monkeypatch.setattr(module, "AnsibleModule", FakeAnsibleModule)
    monkeypatch.setattr(module, "GluddClient", lambda **kwargs: client)
    with pytest.raises(ExitResult) as raised:
        module.main()
    assert raised.value.payload["changed"] is True
    assert raised.value.payload["server_id"] == "srv-1"


def test_main_fails_closed_on_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    FakeAnsibleModule.check_mode_config = False
    FakeAnsibleModule.params_config = _base_params("shutdown")
    monkeypatch.setattr(module, "AnsibleModule", FakeAnsibleModule)
    monkeypatch.setattr(
        module, "GluddClient",
        lambda **kwargs: FakeClient({"_status": 0, "_error": "connection refused"}),
    )
    with pytest.raises(ExitResult) as raised:
        module.main()
    assert raised.value.payload["failed"] is True
    assert "connection refused" in raised.value.payload["msg"]


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (
            "download",
            {"model_id": "org/model", "filename": "m.gguf", "source": "huggingface"},
        ),
        (
            "serve",
            {
                "model_id": "org/model",
                "model_path": "/models/m.gguf",
                "host": "127.0.0.1",
                "port": 12001,
                "startup_timeout": 30,
                "gpu_layers": 0,
                "context_size": 2048,
            },
        ),
        (
            "consume",
            {"server_id": "srv-1", "prompt": "code", "max_tokens": 128},
        ),
        ("shutdown", {"server_id": "srv-1"}),
    ],
)
def test_payload_builds_each_strict_action_shape(
    action: str, expected: dict[str, Any]
) -> None:
    params = _base_params(action)
    params["host"] = "127.0.0.1"
    assert _module()._payload(params) == expected


def test_payload_rejects_unknown_action() -> None:
    with pytest.raises(ValueError, match="unsupported local-model action"):
        _module()._payload({"action": "install"})


def test_main_check_mode_does_not_contact_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    FakeAnsibleModule.check_mode_config = True
    FakeAnsibleModule.params_config = _base_params("download")
    monkeypatch.setattr(module, "AnsibleModule", FakeAnsibleModule)
    monkeypatch.setattr(
        module,
        "GluddClient",
        lambda **kwargs: pytest.fail("check mode contacted daemon"),
    )
    with pytest.raises(ExitResult) as raised:
        module.main()
    assert raised.value.payload["changed"] is False
    assert raised.value.payload["check_mode"] is True


def test_main_fails_closed_on_http_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    FakeAnsibleModule.check_mode_config = False
    FakeAnsibleModule.params_config = _base_params("serve")
    monkeypatch.setattr(module, "AnsibleModule", FakeAnsibleModule)
    monkeypatch.setattr(
        module,
        "GluddClient",
        lambda **kwargs: FakeClient({"_status": 409, "detail": "already running"}),
    )
    with pytest.raises(ExitResult) as raised:
        module.main()
    assert raised.value.payload["failed"] is True
    assert raised.value.payload["status"] == 409
    assert "already running" in raised.value.payload["msg"]
