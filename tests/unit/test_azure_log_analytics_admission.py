"""Fail-closed native Azure Log Analytics query admission contracts."""

from __future__ import annotations

import hashlib
import runpy
import sys
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from importlib.metadata import version
from inspect import signature
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import yaml
from scripts.check_collection_python_boundary import scan_collections

ROOT = Path(__file__).resolve().parents[2]
COLLECTIONS = ROOT / "collections"
if str(COLLECTIONS) not in sys.path:
    sys.path.insert(0, str(COLLECTIONS))

from ansible_collections.general_ludd.azure.plugins.action import (  # noqa: E402
    log_analytics_query as action_plugin,
)
from ansible_collections.general_ludd.azure.plugins.action.log_analytics_query import (  # noqa: E402
    ActionModule,
    execute_action,
)
from ansible_collections.general_ludd.azure.plugins.module_utils import (  # noqa: E402
    azure as collection_core,
)
from ansible_collections.general_ludd.azure.plugins.module_utils import (  # noqa: E402
    log_analytics_admission as admission,
)
from ansible_collections.general_ludd.azure.plugins.modules import (  # noqa: E402
    log_analytics_query as module_stub,
)

from general_ludd.azure.core import query_log_analytics as compatibility_query  # noqa: E402

WORKSPACE = "00000000-0000-4000-8000-000000000001"
QUERY = "print gludd_canary=1"


def _args(query: str = QUERY, **overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "workspace_id": WORKSPACE,
        "query": query,
        "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
        "timespan_minutes": 5,
        "endpoint": "public",
        "credential_mode": "managed",
        "server_timeout_seconds": 15,
    }
    result.update(overrides)
    return result


class _Credential:
    def __init__(self, *, close_fails: bool = False) -> None:
        self.closed = False
        self.close_fails = close_fails

    def close(self) -> None:
        self.closed = True
        if self.close_fails:
            raise RuntimeError("credential detail")


class _Client:
    def __init__(self, response: object, *, close_fails: bool = False) -> None:
        self.response = response
        self.closed = False
        self.close_fails = close_fails

    def query_workspace(self, *_args: object, **_kwargs: object) -> object:
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    def close(self) -> None:
        self.closed = True
        if self.close_fails:
            raise RuntimeError("client detail")


def _table(
    *,
    name: str = "PrimaryResult",
    columns: list[object] | None = None,
    rows: list[list[object]] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        columns=["Value"] if columns is None else columns,
        rows=[[1]] if rows is None else rows,
    )


def _execute_response(response: object, **kwargs: object) -> dict[str, object]:
    credential = _Credential()
    client = _Client(response)
    return execute_action(
        _args(**kwargs),
        credential_factory=lambda _config: credential,
        client_factory=lambda _credential, _endpoint: client,
    )


def test_native_action_executes_digest_bound_query_and_rejects_partial_results() -> None:
    query = "Heartbeat | project TimeGenerated, Computer | take 10"
    digest = hashlib.sha256(query.encode()).hexdigest()
    table = SimpleNamespace(
        name="PrimaryResult",
        columns=[
            SimpleNamespace(name="TimeGenerated", type="datetime"),
            SimpleNamespace(name="Computer", type="string"),
        ],
        rows=[["2026-10-09T00:00:00Z", "canary-1"]],
    )

    class Credential:
        closed = False

        def close(self) -> None:
            self.closed = True

    class Client:
        def __init__(self) -> None:
            self.closed = False
            self.calls: list[dict[str, Any]] = []

        def query_workspace(self, workspace_id: str, effective_query: str, **kwargs: Any) -> Any:
            self.calls.append(
                {
                    "workspace_id": workspace_id,
                    "query": effective_query,
                    **kwargs,
                }
            )
            return SimpleNamespace(tables=[table])

        def close(self) -> None:
            self.closed = True

    credential = Credential()
    client = Client()
    result = execute_action(
        {
            "workspace_id": "00000000-0000-4000-8000-000000000001",
            "query": query,
            "query_sha256": digest,
            "timespan_minutes": 5,
            "endpoint": "public",
            "credential_mode": "managed",
            "server_timeout_seconds": 15,
        },
        credential_factory=lambda _config: credential,
        client_factory=lambda _credential, _endpoint: client,
    )

    assert result["executed"] is True
    assert result["query_sha256"] == digest
    assert result["tables"][0]["rows"] == [["2026-10-09T00:00:00Z", "canary-1"]]
    assert client.calls == [
        {
            "workspace_id": "00000000-0000-4000-8000-000000000001",
            "query": (
                "set truncationmaxrecords=1001;\n"
                "set truncationmaxsize=4194304;\n"
                f"{query}"
            ),
            "timespan": timedelta(minutes=5),
            "server_timeout": 15,
            "include_statistics": False,
            "include_visualization": False,
            "additional_workspaces": None,
        }
    ]
    assert client.closed is True
    assert credential.closed is True

    partial = SimpleNamespace(
        partial_data=SimpleNamespace(tables=[table]),
        partial_error=SimpleNamespace(code="PartialError", message="provider detail"),
    )

    class PartialClient(Client):
        def query_workspace(self, *_args: object, **_kwargs: object) -> Any:
            return partial

    with pytest.raises(ValueError, match="partial") as failure:
        execute_action(
            {
                "workspace_id": "00000000-0000-4000-8000-000000000001",
                "query": query,
                "query_sha256": digest,
                "timespan_minutes": 5,
                "endpoint": "public",
                "credential_mode": "managed",
                "server_timeout_seconds": 15,
            },
            credential_factory=lambda _config: Credential(),
            client_factory=lambda _credential, _endpoint: PartialClient(),
        )
    assert "provider detail" not in str(failure.value)


def test_core_compatibility_is_validation_only_and_never_fake_success() -> None:
    workspace = "00000000-0000-4000-8000-000000000001"
    query = "print gludd_canary=1"
    digest = hashlib.sha256(query.encode()).hexdigest()

    for compatibility in (compatibility_query, collection_core.query_log_analytics):
        result = compatibility(workspace, query)
        assert result == {
            "status": "validated",
            "result": {
                "workspace_id": workspace,
                "query_sha256": digest,
                "timespan": "PT5M",
                "executed": False,
            },
            "warnings": [
                "validation only; execute with general_ludd.azure.log_analytics_query"
            ],
        }
        with pytest.raises(ValueError, match="canonical UUID"):
            compatibility("workspace", query)
        with pytest.raises(ValueError, match="non-empty"):
            compatibility(workspace, "")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("workspace_id", "workspace", "canonical UUID"),
        (
            "workspace_id",
            "ABCDEF00-0000-4000-8000-000000000001",
            "canonical UUID",
        ),
        ("query_sha256", "0" * 64, "digest mismatch"),
        ("query_sha256", "A" * 64, "lowercase SHA-256"),
        ("timespan_minutes", 0, "between 1 and 1440"),
        ("timespan_minutes", 1441, "between 1 and 1440"),
        ("timespan_minutes", True, "must be an integer"),
        ("server_timeout_seconds", 0, "between 1 and 30"),
        ("server_timeout_seconds", 31, "between 1 and 30"),
        ("server_timeout_seconds", False, "must be an integer"),
        ("endpoint", "custom", "public, government, or china"),
        ("credential_mode", "default", "managed, workload, or environment"),
        ("validate_only", "yes", "must be a boolean"),
    ],
)
def test_input_bounds_fail_before_credentials(
    field: str,
    value: object,
    message: str,
) -> None:
    args = _args(validate_only=True)
    args[field] = value
    with pytest.raises((TypeError, ValueError), match=message):
        execute_action(args)


def test_query_bounds_and_truncation_overrides_fail_closed() -> None:
    for query, message in (
        ("", "non-empty"),
        ("x" * (32 * 1024 + 1), "32 KiB"),
        ("Heartbeat\nset notruncation;", "truncation policy"),
        ("SET truncationmaxrecords=9999;\nHeartbeat", "truncation policy"),
        ("Heartbeat; set truncationmaxsize=9999;", "truncation policy"),
    ):
        with pytest.raises(ValueError, match=message):
            execute_action(_args(query, validate_only=True))

    unicode_query = "é" * (16 * 1024 + 1)
    with pytest.raises(ValueError, match="32 KiB"):
        execute_action(_args(unicode_query, validate_only=True))


def test_validate_and_check_modes_never_create_transport() -> None:
    def forbidden(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("transport must not be created")

    for endpoint in ("public", "government", "china"):
        result = execute_action(
            _args(endpoint=endpoint, validate_only=True),
            credential_factory=forbidden,
            client_factory=forbidden,
        )
        assert result["validated"] is True
        assert result["executed"] is False
        assert result["tables"] == []

    checked = execute_action(
        _args(),
        check_mode=True,
        credential_factory=forbidden,
        client_factory=forbidden,
    )
    assert checked["executed"] is False
    assert checked["changed"] is False


def test_exact_credential_modes_reject_cross_mode_fields() -> None:
    identity = "00000000-0000-4000-8000-000000000002"
    tenant = "00000000-0000-4000-8000-000000000003"
    workload = execute_action(
        _args(
            credential_mode="workload",
            workload_tenant_id=tenant,
            workload_client_id=identity,
            workload_token_file="/var/run/secrets/azure/tokens/identity-token",
            validate_only=True,
        )
    )
    assert workload["validated"] is True

    invalid = (
        {"credential_mode": "workload"},
        {
            "credential_mode": "managed",
            "workload_tenant_id": tenant,
        },
        {
            "credential_mode": "environment",
            "managed_identity_client_id": identity,
        },
        {
            "credential_mode": "workload",
            "workload_tenant_id": tenant,
            "workload_client_id": identity,
            "workload_token_file": "relative-token",
        },
    )
    for overrides in invalid:
        with pytest.raises(ValueError):
            execute_action(_args(validate_only=True, **overrides))


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (SimpleNamespace(tables=None), "ambiguous table shape"),
        (
            SimpleNamespace(tables=[_table(name=f"T{index}") for index in range(9)]),
            "8-table",
        ),
        (
            SimpleNamespace(tables=[_table(columns=[f"C{index}" for index in range(65)])]),
            "64-column",
        ),
        (
            SimpleNamespace(tables=[_table(rows=[[1] for _index in range(1001)])]),
            "1000-row",
        ),
        (
            SimpleNamespace(tables=[_table(rows=[["x" * (16 * 1024)]])]),
            "16 KiB cell",
        ),
        (
            SimpleNamespace(tables=[_table(columns=["A", "B"], rows=[[1]])]),
            "row width",
        ),
        (
            SimpleNamespace(tables=[_table(name="T"), _table(name="T")]),
            "table names",
        ),
        (
            SimpleNamespace(tables=[_table(columns=["A", "A"], rows=[[1, 2]])]),
            "column names",
        ),
        (SimpleNamespace(tables=[_table(rows=[[float("nan")]])]), "non-finite"),
        (SimpleNamespace(tables=[_table(rows=[[object()]])]), "unsupported"),
        (SimpleNamespace(tables=[_table()], truncated=True), "truncated"),
        (SimpleNamespace(tables=[_table()], continuation_token="next"), "truncated"),
        (SimpleNamespace(tables=[_table()], status="PartialError"), "partial"),
    ],
)
def test_response_shape_and_size_bounds_are_atomic(
    response: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _execute_response(response)


def test_canonical_cell_types_and_total_output_bound() -> None:
    response = SimpleNamespace(
        tables=[
            _table(
                columns=[
                    SimpleNamespace(name="When", type="datetime"),
                    SimpleNamespace(name="Payload", type="dynamic"),
                    SimpleNamespace(name="Amount", type="decimal"),
                ],
                rows=[
                    [
                        datetime(2026, 10, 9, tzinfo=UTC),
                        {"date": date(2026, 10, 9), "time": time(1, 2, 3)},
                        Decimal("1.25"),
                    ]
                ],
            )
        ]
    )
    result = _execute_response(response)
    assert result["row_count"] == 1
    assert result["tables"][0]["rows"][0] == [
        "2026-10-09T00:00:00+00:00",
        {"date": "2026-10-09", "time": "01:02:03"},
        "1.25",
    ]

    oversized = SimpleNamespace(
        tables=[_table(rows=[["x" * 12_000] for _index in range(200)])]
    )
    with pytest.raises(ValueError, match="2 MiB"):
        _execute_response(oversized)


def test_provider_failures_are_redacted_and_resources_close() -> None:
    credential = _Credential()
    client = _Client(RuntimeError("secret provider body"))
    with pytest.raises(ValueError, match="query failed") as failure:
        execute_action(
            _args(),
            credential_factory=lambda _config: credential,
            client_factory=lambda _credential, _endpoint: client,
        )
    assert "secret" not in str(failure.value)
    assert credential.closed is True
    assert client.closed is True

    second_credential = _Credential()
    with pytest.raises(ValueError, match="query failed"):
        execute_action(
            _args(),
            credential_factory=lambda _config: second_credential,
            client_factory=lambda _credential, _endpoint: (_ for _ in ()).throw(
                RuntimeError("factory secret")
            ),
        )
    assert second_credential.closed is True

    with pytest.raises(ValueError, match="cleanup failed"):
        execute_action(
            _args(),
            credential_factory=lambda _config: _Credential(close_fails=True),
            client_factory=lambda _credential, _endpoint: _Client(
                SimpleNamespace(tables=[])
            ),
        )

    third_credential = _Credential()
    third_client = _Client(SimpleNamespace(tables=[]), close_fails=True)
    with pytest.raises(ValueError, match="cleanup failed"):
        execute_action(
            _args(),
            credential_factory=lambda _config: third_credential,
            client_factory=lambda _credential, _endpoint: third_client,
        )
    assert third_client.closed is True
    assert third_credential.closed is True


def test_default_factories_select_only_explicit_official_sdk_classes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    azure = ModuleType("azure")
    azure_core = ModuleType("azure.core")
    credentials = ModuleType("azure.core.credentials")
    identity = ModuleType("azure.identity")
    monitor = ModuleType("azure.monitor")
    query_module = ModuleType("azure.monitor.query")

    def constructor(name: str) -> Any:
        def make(**kwargs: object) -> _Credential:
            calls.append((name, kwargs))
            return _Credential()

        return make

    identity.ManagedIdentityCredential = constructor("managed")  # type: ignore[attr-defined]
    identity.WorkloadIdentityCredential = constructor("workload")  # type: ignore[attr-defined]
    identity.EnvironmentCredential = constructor("environment")  # type: ignore[attr-defined]

    def client_constructor(credential: object, **kwargs: object) -> _Client:
        assert isinstance(credential, _Credential)
        calls.append(("client", kwargs))
        return _Client(SimpleNamespace(tables=[]))

    query_module.LogsQueryClient = client_constructor  # type: ignore[attr-defined]
    credentials.TokenCredential = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "azure", azure)
    monkeypatch.setitem(sys.modules, "azure.core", azure_core)
    monkeypatch.setitem(sys.modules, "azure.core.credentials", credentials)
    monkeypatch.setitem(sys.modules, "azure.identity", identity)
    monkeypatch.setitem(sys.modules, "azure.monitor", monitor)
    monkeypatch.setitem(sys.modules, "azure.monitor.query", query_module)

    client_id = "00000000-0000-4000-8000-000000000002"
    tenant_id = "00000000-0000-4000-8000-000000000003"
    managed = admission._default_credential_factory(
        admission.CredentialConfig(mode="managed")
    )
    admission._default_credential_factory(
        admission.CredentialConfig(
            mode="managed", managed_identity_client_id=client_id
        )
    )
    admission._default_credential_factory(
        admission.CredentialConfig(
            mode="workload",
            workload_tenant_id=tenant_id,
            workload_client_id=client_id,
            workload_token_file="/var/run/secrets/token",
        )
    )
    admission._default_credential_factory(
        admission.CredentialConfig(mode="environment")
    )
    client = admission._default_client_factory(
        managed, admission.ENDPOINTS["public"]
    )

    assert isinstance(client, _Client)
    assert calls == [
        ("managed", {}),
        ("managed", {"client_id": client_id}),
        (
            "workload",
            {
                "tenant_id": tenant_id,
                "client_id": client_id,
                "token_file_path": "/var/run/secrets/token",
            },
        ),
        ("environment", {}),
        (
            "client",
            {
                "endpoint": "https://api.loganalytics.io",
                "retry_total": 0,
                "retry_connect": 0,
                "retry_read": 0,
                "retry_status": 0,
            },
        ),
    ]


def test_locked_official_sdk_exposes_the_admitted_query_surface() -> None:
    from azure.monitor.query import LogsQueryClient, LogsQueryResult, LogsTable

    assert version("azure-monitor-query") == "2.0.0"
    parameters = signature(LogsQueryClient.query_workspace).parameters
    assert {
        "workspace_id",
        "query",
        "timespan",
        "server_timeout",
        "include_statistics",
        "include_visualization",
        "additional_workspaces",
    } <= set(parameters)

    sdk_result = LogsQueryResult(
        tables=[
            LogsTable(
                name="PrimaryResult",
                columns=["Count"],
                columns_types=["long"],
                rows=[[1]],
            )
        ]
    )
    admitted = _execute_response(sdk_result)
    assert admitted["tables"][0]["rows"] == [[1]]
    assert admitted["tables"][0]["columns"] == [
        {"name": "Count", "type": "long"}
    ]


def test_action_errors_and_module_bypass_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = object.__new__(ActionModule)
    action._task = SimpleNamespace(
        args=_args(query_sha256="0" * 64),
        check_mode=False,
        no_log=False,
    )
    monkeypatch.setattr(action_plugin._ActionBase, "run", lambda *_args, **_kwargs: {"invocation": {}})
    result = action.run()
    assert result["failed"] is True
    assert result["changed"] is False
    assert "invocation" not in result
    assert action._task.no_log is True

    class Module:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["supports_check_mode"] is True
            assert kwargs["argument_spec"]["query"]["no_log"] is True

        def fail_json(self, **kwargs: Any) -> None:
            raise RuntimeError(kwargs["msg"])

    monkeypatch.setattr(module_stub, "AnsibleModule", Module)
    with pytest.raises(RuntimeError, match="controller-side action plugin"):
        module_stub.main()
    monkeypatch.setattr("ansible.module_utils.basic.AnsibleModule", Module)
    with pytest.raises(RuntimeError, match="controller-side action plugin"):
        runpy.run_path(str(Path(module_stub.__file__)), run_name="__main__")


def test_strict_action_arguments_and_nested_cell_limits() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        execute_action({**_args(), "additional_workspaces": ["other"]}, check_mode=True)
    with pytest.raises(TypeError, match="missing required"):
        execute_action({"workspace_id": WORKSPACE})

    nested: object = "value"
    for _index in range(18):
        nested = [nested]
    with pytest.raises(ValueError, match="nesting limit"):
        _execute_response(SimpleNamespace(tables=[_table(rows=[[nested]])]))


def test_role_molecule_dependencies_and_no_escape_hatches_are_pinned() -> None:
    collection = ROOT / "collections/ansible_collections/general_ludd/azure"
    role = collection / "roles/log_analytics_query"
    defaults = yaml.safe_load((role / "defaults/main.yml").read_text(encoding="utf-8"))
    tasks = (role / "tasks/main.yml").read_text(encoding="utf-8")
    readme = (role / "README.md").read_text(encoding="utf-8")
    scenario = ROOT / "molecule/playbooks/azure_log_analytics_admission"
    molecule = yaml.safe_load((scenario / "molecule.yml").read_text(encoding="utf-8"))
    converge = (scenario / "default/converge.yml").read_text(encoding="utf-8")
    verify = (scenario / "default/verify.yml").read_text(encoding="utf-8")
    source = (
        collection / "plugins/module_utils/log_analytics_admission.py"
    ).read_text(encoding="utf-8")
    requirements = (ROOT / "config/ansible/requirements.txt").read_text(encoding="utf-8")
    controller = (
        ROOT / "requirements/profiles/ansible-controller/pyproject.toml"
    ).read_text(encoding="utf-8")

    assert defaults and all(
        name.startswith("azure_log_analytics_query_") for name in defaults
    )
    assert defaults["azure_log_analytics_query_no_log"] is True
    assert "general_ludd.azure.log_analytics_query" in tasks
    for forbidden in (
        "ansible.builtin.uri",
        "ansible.builtin.copy",
        "ansible.builtin.shell",
        "ansible.builtin.command",
    ):
        assert forbidden not in tasks
    assert "azure-monitor-query==2.0.0" in requirements
    assert "azure-identity==1.26.0" in requirements
    assert "azure-monitor-query==2.0.0" in controller
    assert "DefaultAzureCredential" not in source
    assert "requests" not in source
    assert "urllib" not in source
    assert scan_collections(collection) == []
    assert molecule["provisioner"]["env"]["ANSIBLE_COLLECTIONS_SCAN_SYS_PATH"] == "false"
    assert "print gludd_canary=1" in converge
    assert "check_mode: true" in verify
    for term in ("canary", "drain", "rollback", "#25137", "78567796"):
        assert term in readme.lower()
