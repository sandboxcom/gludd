"""Behavioral contracts for beta4's isolated small-collection runtimes."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from ansible_collections.general_ludd.azure.plugins.module_utils import azure
from ansible_collections.general_ludd.chat.plugins.module_utils import session_export
from ansible_collections.general_ludd.chat.plugins.modules import chat_export
from ansible_collections.general_ludd.chemistry.plugins.module_utils import (
    chemistry_dispatch,
)
from ansible_collections.general_ludd.chemistry.plugins.modules import (
    chemistry_operation,
)
from ansible_collections.general_ludd.formal.plugins.module_utils import tla_trace
from ansible_collections.general_ludd.formal.plugins.modules import tla_trace_interpret
from ansible_collections.general_ludd.operations.plugins.module_utils import (
    log_analyzer,
)
from ansible_collections.general_ludd.operations.plugins.modules import log_analyze
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers import experts


class _FakeModule:
    def __init__(self, params: dict[str, Any], *, check_mode: bool = False) -> None:
        self.params = params
        self.check_mode = check_mode
        self.exited: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None

    def exit_json(self, **kwargs: Any) -> None:
        self.exited = kwargs

    def fail_json(self, **kwargs: Any) -> None:
        self.failed = kwargs


class _FakeClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.init: dict[str, Any] = {}

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.posts.append((path, body))
        return self.response


def _session_file(tmp_path: Path) -> Path:
    path = tmp_path / "session.ndjson"
    path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "role": "user",
                        "timestamp": "2026-08-20T00:00:00Z",
                        "content": "<request>",
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "```py\nprint('<safe>')\n```",
                    }
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_azure_collection_helpers_preserve_supported_contracts() -> None:
    valid = azure.validate_rbac_role_definition(
        ["Microsoft.Storage/storageAccounts/read"],
        [],
        ["/subscriptions/sub-a"],
    )
    invalid = azure.validate_rbac_role_definition(
        [
            "",
            "Microsoft.Storage/storageAccounts/listkeys/action",
            "Microsoft.KeyVault/vaults/secrets/read",
        ],
        [],
        [],
    )
    known = azure.audit_iam_assignments("sub", "rg", "runtime_execution")
    unknown = azure.audit_iam_assignments("sub", "rg", "root")

    assert len(azure.AZURE_EXPERT_ROLES) == 8
    assert valid == {"status": "valid", "issues": []}
    assert invalid["status"] == "invalid"
    assert len(invalid["issues"]) == 4
    assert known["result"][0]["scope"].endswith("/resourceGroups/rg")
    assert unknown["status"] == "error"


def test_azure_collection_helpers_cover_deployment_plans() -> None:
    network = azure.design_azure_network("eastus", "gludd")
    premium = azure.acr_registry_config("registry", "Premium", "eastus")
    invalid = azure.acr_registry_config("registry", "Unknown", "eastus")
    large = azure.container_app_config("H100", "org/Model", "eastus")
    unknown = azure.container_app_config("Mystery", "org/Model", "eastus")
    query = azure.query_log_analytics("workspace", "Heartbeat | take 1")
    inventory = azure.inventory_resources(["sub-a", "sub-b"])
    cost = azure.optimize_cost("container_apps", "eastus", "T4")
    missing_cost = azure.optimize_cost("vm", "eastus", "T4")

    assert network["result"]["vnet_name"] == "gludd-vnet-eastus"
    assert len(network["result"]["subnets"]) == 4
    assert premium["result"]["geo_replication"] is True
    assert invalid["status"] == "error"
    assert large["result"]["memory"] == "32Gi"
    assert unknown["warnings"]
    assert query["result"]["timespan"] == "P1D"
    assert inventory["result"]["subscription_count"] == 2
    assert cost["result"]["monthly_estimate"] == pytest.approx(452.6)
    assert missing_cost["warnings"]


def test_chat_renderers_are_deterministic_and_escape_html(tmp_path: Path) -> None:
    session = _session_file(tmp_path)

    markdown = session_export.render_session(session, "md")
    json_text = session_export.render_session(session, "json")
    html_text = session_export.render_session(session, "html")

    assert "# Chat Session Export" in markdown
    assert "2026-08-20T00:00:00Z" in markdown
    assert json.loads(json_text)["messages"][0]["role"] == "user"
    assert "&lt;request&gt;" in html_text
    assert 'class="language-py"' in html_text
    assert "&lt;safe&gt;" in html_text
    assert session_export.export_session(session, "md") == markdown
    with pytest.raises(ValueError, match="Unsupported"):
        session_export.render_session(session, cast(Any, "txt"))


def test_chat_loader_rejects_missing_and_malformed_records(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Session file not found"):
        session_export.load_messages(tmp_path / "missing.ndjson")

    corrupt = tmp_path / "corrupt.ndjson"
    corrupt.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="Corrupt session file"):
        session_export.load_messages(corrupt)

    non_object = tmp_path / "list.ndjson"
    non_object.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON objects"):
        session_export.load_messages(non_object)


def test_chat_export_is_atomic_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session_file(tmp_path)
    destination = tmp_path / "nested" / "export.md"
    replaces: list[tuple[object, object]] = []
    real_replace = session_export.os.replace

    def record_replace(source: object, target: object) -> None:
        replaces.append((source, target))
        real_replace(source, target)

    monkeypatch.setattr(session_export.os, "replace", record_replace)
    assert session_export.export_session(session, "md", destination) == destination
    assert replaces and replaces[-1][1] == destination
    assert not list(destination.parent.glob(".export.md.*"))

    params = {
        "session_file": str(session),
        "format": "md",
        "output_file": str(destination),
    }
    module = _FakeModule(params)
    monkeypatch.setattr(chat_export, "AnsibleModule", lambda **_: module)
    chat_export.run_module()
    assert module.failed is None
    assert module.exited == {"changed": False, "output": str(destination)}


def test_chat_module_supports_check_mode_and_inline_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session_file(tmp_path)
    destination = tmp_path / "not-created.md"
    check = _FakeModule(
        {
            "session_file": str(session),
            "format": "md",
            "output_file": str(destination),
        },
        check_mode=True,
    )
    monkeypatch.setattr(chat_export, "AnsibleModule", lambda **_: check)
    chat_export.run_module()
    assert check.exited == {"changed": True, "output": str(destination)}
    assert not destination.exists()

    inline = _FakeModule(
        {"session_file": str(session), "format": "json", "output_file": None}
    )
    monkeypatch.setattr(chat_export, "AnsibleModule", lambda **_: inline)
    chat_export.run_module()
    assert inline.exited is not None
    assert json.loads(inline.exited["output"])["messages"]
    assert inline.exited["changed"] is False


def _chemistry_params() -> dict[str, Any]:
    return {
        "operation": "route",
        "request": {"task": "identity"},
        "daemon_url": "http://daemon:8000",
        "psk": "secret",
        "timeout": 9,
        "idempotency_key": "",
    }


def test_chemistry_module_uses_authenticated_bounded_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient({"_status": 200, "workflow": "identity"})

    def factory(**kwargs: Any) -> _FakeClient:
        fake.init = kwargs
        return fake

    monkeypatch.setattr(chemistry_operation, "GluddClient", factory)
    module = _FakeModule(_chemistry_params())
    chemistry_operation.run(module)

    assert module.failed is None
    assert module.exited == {
        "changed": False,
        "result": {"workflow": "identity"},
        "operation": "route",
    }
    assert fake.init == {
        "base_url": "http://daemon:8000",
        "psk": "secret",
        "timeout": 9,
    }
    path, body = fake.posts[0]
    assert path == "/api/chemistry/resolve"
    assert body["idempotency_key"].startswith("chemistry:")
    assert body["timeout_seconds"] == 9.0


def test_chemistry_module_check_failure_and_timeout_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def forbidden_client(**_kwargs: Any) -> _FakeClient:
        nonlocal called
        called = True
        return _FakeClient({})

    monkeypatch.setattr(chemistry_operation, "GluddClient", forbidden_client)
    check = _FakeModule(_chemistry_params(), check_mode=True)
    chemistry_operation.run(check)
    assert check.exited == {"changed": False, "result": {}, "operation": "route"}
    assert called is False

    invalid_params = _chemistry_params()
    invalid_params["timeout"] = 0
    invalid = _FakeModule(invalid_params)
    chemistry_operation.run(invalid)
    assert invalid.failed is not None
    assert "timeout" in invalid.failed["msg"]
    assert called is False

    fake = _FakeClient({"_status": 503, "detail": "chemistry unavailable"})
    monkeypatch.setattr(chemistry_operation, "GluddClient", lambda **_: fake)
    failed = _FakeModule(_chemistry_params())
    chemistry_operation.run(failed)
    assert failed.exited is None
    assert failed.failed is not None
    assert failed.failed["status"] == 503
    assert "unavailable" in failed.failed["msg"]


def test_chemistry_compatibility_cli_success_and_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _FakeClient({"_status": 200, "workflow": "identity"})
    monkeypatch.setattr(chemistry_dispatch, "GluddClient", lambda **_: fake)
    monkeypatch.setenv("CHEMISTRY_ACTION", "route")
    monkeypatch.setenv("CHEMISTRY_INPUT", '{"task":"identity"}')
    monkeypatch.setenv("GLUDD_TIMEOUT", "99")
    assert chemistry_dispatch.main() == 0
    assert json.loads(capsys.readouterr().out)["workflow"] == "identity"
    assert fake.posts[0][1]["timeout_seconds"] == 30.0

    monkeypatch.setenv("CHEMISTRY_ACTION", "unknown")
    assert chemistry_dispatch.main() == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"

    monkeypatch.setenv("CHEMISTRY_ACTION", "route")
    monkeypatch.setenv("CHEMISTRY_INPUT", "[]")
    assert chemistry_dispatch.main() == 2
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "chem.bad_json"

    monkeypatch.setenv("CHEMISTRY_INPUT", "{}")
    monkeypatch.setattr(
        chemistry_dispatch,
        "GluddClient",
        lambda **_: _FakeClient({"_status": 504, "detail": "timed out"}),
    )
    assert chemistry_dispatch.main() == 1
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "chem.daemon_error"


def test_chemistry_dispatch_covers_all_allowlisted_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import general_ludd.chemistry as chemistry

    monkeypatch.setattr(chemistry, "route_chemistry_task", lambda value: {"op": "route", **value})
    monkeypatch.setattr(chemistry, "resolve_identity", lambda value: {"op": "identity", **value})
    monkeypatch.setattr(chemistry, "analyze_reaction", lambda value: {"op": "reaction", **value})
    monkeypatch.setattr(chemistry, "molar_mass", lambda formula: {"op": "mass", "formula": formula})
    monkeypatch.setattr(chemistry, "stoichiometry_moles", lambda **value: {"op": "moles", **value})
    monkeypatch.setattr(chemistry, "stoichiometry_dilution", lambda *value: {"op": "dilution", "values": value})
    monkeypatch.setattr(chemistry, "stoichiometry_yield", lambda **value: {"op": "yield", **value})
    monkeypatch.setattr(chemistry, "screen_hazards", lambda value: {"op": "hazard", **value})

    assert experts._dispatch_chemistry("route", {"task": "x"})["op"] == "route"
    assert experts._dispatch_chemistry("identity", {"query": "water"})["op"] == "identity"
    assert experts._dispatch_chemistry("reaction", {"reactants": []})["op"] == "reaction"
    assert experts._dispatch_chemistry("molar_mass", {"formula": "H2O"})["formula"] == "H2O"
    assert experts._dispatch_chemistry("moles", {"mass_g": 1, "formula": "H2O"})["op"] == "moles"
    assert experts._dispatch_chemistry("dilution", {"c1": 1, "v1": 2, "c2": 3})["op"] == "dilution"
    assert experts._dispatch_chemistry("yield", {"actual_g": 1, "theoretical_g": 2})["op"] == "yield"
    assert experts._dispatch_chemistry("hazard", {"query": "water"})["op"] == "hazard"
    with pytest.raises(experts.ChemistryRequestError, match="invalid"):
        experts._dispatch_chemistry("moles", {"mass_g": "not-a-number"})
    with pytest.raises(experts.ChemistryRequestError, match="unsupported"):
        experts._dispatch_chemistry("other", {})


def test_chemistry_endpoint_replays_and_rejects_key_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def dispatch(_operation: str, request: dict[str, Any]) -> dict[str, Any]:
        calls.append(request)
        return {"value": request["formula"]}

    monkeypatch.setattr(experts, "_dispatch_chemistry", dispatch)
    app = FastAPI()
    experts.register(app, {})
    client = TestClient(app)
    payload = {
        "operation": "molar_mass",
        "request": {"formula": "H2O"},
        "idempotency_key": "chem-1",
    }
    first = client.post("/api/chemistry/resolve", json=payload)
    replay = client.post("/api/chemistry/resolve", json=payload)
    payload["request"] = {"formula": "CO2"}
    conflict = client.post("/api/chemistry/resolve", json=payload)

    assert first.json() == {"value": "H2O"}
    assert replay.json() == {"value": "H2O", "idempotent_replay": True}
    assert calls == [{"formula": "H2O"}]
    assert conflict.status_code == 409


def test_chemistry_endpoint_bounds_and_maps_typed_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    experts.register(app, {})
    client = TestClient(app)
    too_large = {str(index): index for index in range(129)}
    assert client.post(
        "/api/chemistry/resolve",
        json={"operation": "route", "request": too_large},
    ).status_code == 422
    assert client.post(
        "/api/chemistry/resolve",
        json={"operation": "arbitrary", "request": {}},
    ).status_code == 422

    monkeypatch.setattr(
        experts,
        "_dispatch_chemistry",
        lambda *_: (_ for _ in ()).throw(experts.ChemistryRequestError("bad input")),
    )
    response = client.post(
        "/api/chemistry/resolve",
        json={"operation": "route", "request": {}},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "bad input"}


def test_chemistry_endpoint_enforces_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def time_out(awaitable: Any, *, timeout: float) -> dict[str, Any]:
        assert timeout == 0.1
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", time_out)
    app = FastAPI()
    experts.register(app, {})
    response = TestClient(app).post(
        "/api/chemistry/resolve",
        json={"operation": "route", "request": {}, "timeout_seconds": 0.1},
    )
    assert response.status_code == 504
    assert response.json() == {"detail": "chemistry operation timed out"}


def test_log_helpers_bound_discovery_parsing_and_clustering(tmp_path: Path) -> None:
    assert log_analyzer.discover_logs(str(tmp_path / "missing"), "*.log") == []
    with pytest.raises(ValueError, match="glob pattern"):
        log_analyzer.discover_logs(str(tmp_path), "")
    for index in range(3):
        (tmp_path / f"{index}.log").write_text("INFO ready\n", encoding="utf-8")
    assert len(log_analyzer.discover_logs(str(tmp_path), "../*.log", max_files=2)) == 2

    entries = log_analyzer.parse_log_lines(
        "2026-08-20 12:00:00 ERROR [database] failed\n"
        "08/20/2026 12:00:01 warning from worker.pool retry\n"
        "[12:00:02] Traceback at service.call\n\n"
    )
    assert entries[0]["severity"] == "ERROR"
    assert entries[0]["category"] == "database"
    assert entries[1]["category"] == "worker.pool"
    assert entries[2]["is_error"] is True
    clusters = log_analyzer.cluster_errors([*entries, entries[0]], min_size=2)
    assert clusters[0]["count"] == 2
    assert log_analyzer.cluster_errors(entries, min_size=5) == []


def test_log_analysis_and_module_are_atomic_and_idempotent(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    output = tmp_path / "reports"
    logs.mkdir()
    (logs / "app.log").write_text(
        "INFO ready\nERROR [db] unavailable\nERROR [db] unavailable\n",
        encoding="utf-8",
    )
    result = log_analyzer.analyze(
        str(logs),
        "*.log",
        str(output),
        error_threshold=0.2,
    )
    assert result["verdict"] == "anomalies_detected"
    assert result["cluster_count"] == 1
    assert json.loads((output / "log_analysis_result.json").read_text())["error_lines"] == 2
    assert "[ERROR] db" in (output / "log_analysis_report.md").read_text()
    assert not list(output.glob(".log_analysis*"))

    params = {
        "log_dir": str(logs),
        "glob_pattern": "*.log",
        "output_dir": str(output),
        "error_threshold": 0.2,
        "cluster_window": 300,
        "min_cluster_size": 2,
        "max_files": 10,
        "max_bytes_per_file": 1024,
    }
    module = _FakeModule(params)
    log_analyze.run(module)
    assert module.exited is not None
    assert module.exited["changed"] is False
    assert len(module.exited["artifacts"]) == 2


def test_log_module_check_mode_and_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    params = {
        "log_dir": str(tmp_path),
        "glob_pattern": "*.log",
        "output_dir": str(tmp_path / "out"),
        "error_threshold": 0.1,
        "cluster_window": 300,
        "min_cluster_size": 2,
        "max_files": 10,
        "max_bytes_per_file": 1024,
    }
    check = _FakeModule(params, check_mode=True)
    log_analyze.run(check)
    assert check.exited == {"changed": False, "result": {}, "artifacts": []}

    monkeypatch.setattr(log_analyze, "analyze", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk")))
    failed = _FakeModule(params)
    log_analyze.run(failed)
    assert failed.failed == {"msg": "log analysis failed: disk"}


def test_legacy_log_cli_delegates_to_collection_utility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "collections/ansible_collections/general_ludd/operations/roles"
        / "log_analyzer/files/analyze_logs.py"
    )
    spec = importlib.util.spec_from_file_location("operations_log_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls: list[dict[str, Any]] = []

    def analyze(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"verdict": "clean"}

    monkeypatch.setattr(module, "analyze", analyze)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            "--log-dir",
            str(tmp_path),
            "--glob",
            "*.log",
            "--output-dir",
            str(tmp_path / "out"),
            "--error-threshold",
            "0.25",
            "--cluster-window",
            "60",
            "--min-cluster-size",
            "3",
        ],
    )
    with pytest.raises(SystemExit) as stopped:
        module.main()

    assert stopped.value.code == 0
    assert json.loads(capsys.readouterr().out) == {"verdict": "clean"}
    assert calls == [
        {
            "log_dir": str(tmp_path),
            "glob_pattern": "*.log",
            "output_dir": str(tmp_path / "out"),
            "error_threshold": 0.25,
            "cluster_window": 60,
            "min_cluster_size": 3,
        }
    ]

    main_spec = importlib.util.spec_from_file_location("__main__", script)
    assert main_spec is not None and main_spec.loader is not None
    main_module = importlib.util.module_from_spec(main_spec)
    with pytest.raises(SystemExit) as direct_stop:
        main_spec.loader.exec_module(main_module)
    assert direct_stop.value.code == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "clean"


def test_tla_parser_and_module_publish_stable_trace(tmp_path: Path) -> None:
    raw = (
        "Error: Invariant NeverNegative is violated\n"
        "The behavior up to this point is:\n"
        "State 1: <Initial predicate>\n"
        "/\\ n = 0\n"
        "State 2: <Next>\n"
        "/\\ n = -1\n"
    )
    parsed = tla_trace.parse_tlc_trace(raw)
    assert parsed["invariant"] == "NeverNegative"
    assert parsed["step_count"] == 2
    assert parsed["steps"][1]["vars"] == {"n": "-1"}
    assert tla_trace.parse_tlc_trace("no trace")["invariant"] == "UnknownInvariant"

    output = tmp_path / "trace.json"
    params = {"tlc_output": raw, "trace_path": "", "output_path": str(output)}
    first = _FakeModule(params)
    tla_trace_interpret.run(first)
    assert first.exited is not None and first.exited["changed"] is True
    assert json.loads(output.read_text())["step_count"] == 2
    assert not list(tmp_path.glob(".trace.json.*"))

    second = _FakeModule(params)
    tla_trace_interpret.run(second)
    assert second.exited is not None and second.exited["changed"] is False

    check = _FakeModule(params, check_mode=True)
    tla_trace_interpret.run(check)
    assert check.exited is not None and check.exited["changed"] is False


def test_tla_module_supports_file_input_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_file = tmp_path / "tlc.txt"
    trace_file.write_text(
        "The behavior up to this point is:\nState 1: initial\n/\\ x = 1\n",
        encoding="utf-8",
    )
    file_module = _FakeModule(
        {
            "tlc_output": "",
            "trace_path": str(trace_file),
            "output_path": str(tmp_path / "trace.json"),
        }
    )
    tla_trace_interpret.run(file_module)
    assert file_module.exited is not None
    assert file_module.exited["trace"]["step_count"] == 1

    missing = _FakeModule(
        {"tlc_output": "", "trace_path": "", "output_path": str(tmp_path / "x")}
    )
    tla_trace_interpret.run(missing)
    assert missing.failed == {"msg": "tlc_output or trace_path is required"}

    unreadable = _FakeModule(
        {
            "tlc_output": "",
            "trace_path": str(tmp_path / "missing"),
            "output_path": str(tmp_path / "x"),
        }
    )
    tla_trace_interpret.run(unreadable)
    assert unreadable.failed is not None
    assert "unable to read" in unreadable.failed["msg"]

    monkeypatch.setattr(
        tla_trace_interpret,
        "_atomic_write",
        lambda *_: (_ for _ in ()).throw(OSError("disk full")),
    )
    publish_failure = _FakeModule(
        {"tlc_output": "trace", "trace_path": "", "output_path": str(tmp_path / "x")}
    )
    tla_trace_interpret.run(publish_failure)
    assert publish_failure.failed == {"msg": "unable to publish TLC trace: disk full"}


def test_roles_use_packaged_cross_collection_calls_and_managed_python() -> None:
    root = Path(__file__).resolve().parents[2] / "collections/ansible_collections/general_ludd"
    chemistry_roles = (
        "chemistry_router",
        "identity_resolve",
        "reaction_analyze",
        "stoichiometry",
        "hazard_review",
    )
    for role in chemistry_roles:
        tasks = (root / "chemistry" / "roles" / role / "tasks/main.yml").read_text()
        assert "general_ludd.chemistry.chemistry_operation:" in tasks
        assert "ansible.builtin.shell:" not in tasks
        assert "ansible.builtin.command:" not in tasks

    operations = (root / "operations/roles/log_analyzer/tasks/main.yml").read_text()
    formal = (root / "formal/roles/tla_trace_interpret/tasks/main.yml").read_text()
    assert "general_ludd.operations.log_analyze:" in operations
    assert "general_ludd.agent.gludd_model_call:" in operations
    assert "general_ludd.formal.tla_trace_interpret:" in formal
    assert "general_ludd.agent.gludd_message:" in formal

    managed_tasks = (
        root / "security/roles/audit_framework/tasks/audit_parse.yml",
        root / "web_server/roles/cgi_wsgi/tasks/main.yml",
        root / "web_server/roles/logging_middleware/tasks/main.yml",
        root / "web_server/roles/ssl_config/tasks/main.yml",
    )
    for path in managed_tasks:
        text = path.read_text(encoding="utf-8")
        assert "general_ludd.agent.managed_python_preflight" in text
        assert "public: false" in text
        assert "gludd_managed_python_runtime.interpreter" in text
        assert "python3" not in text
        assert "ignore_errors: true" not in text
        assert "default(true)" not in text
        assert "failed_when: false" not in text

    for collection in ("chemistry", "web_server"):
        galaxy = (root / collection / "galaxy.yml").read_text(encoding="utf-8")
        assert "general_ludd.agent" in galaxy
