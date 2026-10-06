"""Branch contracts for the split CLI facade and extracted platform helpers."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from general_ludd import cli
from general_ludd.cli_commands import platform as platform_commands


@pytest.mark.parametrize(
    ("method", "attribute"),
    [("PUT", "put"), ("PATCH", "patch"), ("OPTIONS", "request")],
)
def test_http_call_supports_remaining_methods(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    attribute: str,
) -> None:
    """The facade keeps every legacy HTTP dispatch path after extraction."""
    response = SimpleNamespace(status_code=200, json=lambda: {"method": method})
    monkeypatch.setattr(cli.httpx, attribute, lambda *_args, **_kwargs: response)

    assert cli._http_call(method, "http://daemon/api") == {"method": method}


_NO_PAYLOAD_HANDLERS = (
    "_cmd_pause_list",
    "_cmd_pause_project",
    "_cmd_pause_model",
    "_cmd_resume_project",
    "_cmd_resume_model",
    "_cmd_add",
    "_cmd_list",
    "_cmd_log_level",
    "_cmd_deployments",
    "_cmd_health",
    "_cmd_models_search",
    "_cmd_models_downloaded",
    "_cmd_models_discovered",
    "_cmd_models_list",
    "_cmd_model_performance",
    "_cmd_model_ranking",
    "_cmd_model_router_status",
    "_cmd_model_router_set",
    "_cmd_local_serve",
    "_cmd_worktree_scan",
    "_cmd_worktree_status",
    "_cmd_mcp_search",
    "_cmd_mcp_list",
    "_cmd_skills_search",
    "_cmd_skills_list",
    "_cmd_skills_install",
    "_cmd_compute_endpoints",
    "_cmd_compute_launch",
    "_cmd_compute_destroy",
    "_cmd_selftest",
    "_cmd_hooks_list",
    "_cmd_hooks_register",
    "_cmd_workers_list",
    "_cmd_workers_ping",
    "_cmd_agents_list",
    "_cmd_metrics_cost",
    "_cmd_metrics_report",
    "_cmd_reload",
    "_cmd_templates_list",
    "_cmd_templates_refresh",
    "_cmd_playbooks_list",
    "_cmd_playbooks_refresh",
    "_cmd_slurm_status",
    "_cmd_slurm_list",
    "_cmd_connectors_list",
    "_cmd_connectors_health",
    "_cmd_connectors_query",
)


def _all_handler_args() -> Namespace:
    return Namespace(
        daemon_url="http://daemon",
        target_id="target",
        reason="test",
        title="title",
        description="description",
        queue="default",
        priority=0,
        work_type="code",
        project=None,
        status=None,
        level="info",
        query="query",
        limit=10,
        service=None,
        task_type="chat",
        strategy="quality",
        model="model",
        engine="vllm",
        host="127.0.0.1",
        port=8000,
        gpu_layers=0,
        context_size=4096,
        path=None,
        name="name",
        id="endpoint",
        url="http://endpoint",
        max_concurrent=1,
        gpu="a100",
        gpu_count=1,
        region=None,
        provider="aws",
        deploy_type="vm",
        max_cost=1.0,
        timeout_minutes=5,
        disk_size_gb=10,
        container_image=None,
        hourly_rate=None,
        no_spot=False,
        allowed_cidr=None,
        ssh_public_key_path=None,
        workload_type="inference",
        instance_id="instance",
        event="todo.created",
        handler="module.handler",
        scope="all",
        source="logs",
        spec=None,
    )


@pytest.mark.parametrize("handler_name", _NO_PAYLOAD_HANDLERS)
def test_http_handlers_return_cleanly_without_payload(
    monkeypatch: pytest.MonkeyPatch,
    handler_name: str,
) -> None:
    """A missing daemon payload remains a quiet, fail-closed early return."""
    monkeypatch.setattr(cli, "_http_call", lambda *_args, **_kwargs: None)

    handler = getattr(cli, handler_name)
    handler(_all_handler_args())


def test_searx_model_search_handles_empty_and_sparse_results(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Extracting parser construction does not change SearX result rendering."""
    searcher = SimpleNamespace(search_models=lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "general_ludd.infra.model_search.SearXModelSearch",
        lambda **_kwargs: searcher,
    )
    args = Namespace(searx_url="http://searx", query="model", source="all")
    cli._cmd_models_searx_search(args)
    assert "No models found" in capsys.readouterr().out

    results = [
        SimpleNamespace(
            name="complete",
            params_count=7,
            license="MIT",
            quantizations_available=["int8"],
            source_url="https://example.test/complete",
        ),
        SimpleNamespace(
            name="sparse",
            params_count=None,
            license=None,
            quantizations_available=[],
            source_url="https://example.test/sparse",
        ),
    ]
    searcher.search_models = lambda *_args, **_kwargs: results
    cli._cmd_models_searx_search(args)
    output = capsys.readouterr().out
    assert "complete" in output
    assert "sparse" in output
    assert "Params: 7B" in output


def test_login_validation_and_failed_token_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Login keeps its validation exits and token-failure exit code."""
    with pytest.raises(SystemExit, match="2"):
        cli._cmd_login(Namespace(list=False, service=None, store="env", timeout=1.0))
    with pytest.raises(SystemExit, match="2"):
        cli._cmd_login(Namespace(list=False, service="not-a-service", store="env", timeout=1.0))

    flow = SimpleNamespace(run=lambda **_kwargs: None)
    monkeypatch.setattr("general_ludd.auth.browser_login.BrowserLoginFlow", lambda *_args, **_kwargs: flow)
    with pytest.raises(SystemExit, match="1"):
        cli._cmd_login(Namespace(list=False, service="github", store="env", timeout=1.0))


def test_login_openbao_connection_failure_exits_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The OpenBao credential path remains fail-closed when unavailable."""

    class BrokenSecretsManager:
        def connect(self) -> None:
            raise RuntimeError("offline")

    monkeypatch.setattr("general_ludd.secrets.manager.SecretsManager", BrokenSecretsManager)
    with pytest.raises(SystemExit, match="1"):
        cli._cmd_login(Namespace(list=False, service="github", store="openbao", timeout=1.0))


def test_filestore_error_and_exception_paths(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Filestore handlers preserve bounded errors without inventing payloads."""
    failed = SimpleNamespace(status_code=500)
    monkeypatch.setattr(cli.httpx, "get", lambda *_args, **_kwargs: failed)
    cli._cmd_filestore_list(Namespace(daemon_url="http://daemon", path="/"))
    cli._cmd_filestore_cat(Namespace(daemon_url="http://daemon", path="file"))
    monkeypatch.setattr(cli.httpx, "post", lambda *_args, **_kwargs: failed)
    cli._cmd_filestore_bootstrap(Namespace(daemon_url="http://daemon", binary="podman"))
    assert capsys.readouterr().err.count("Error: 500") == 3

    seen: list[str] = []
    monkeypatch.setattr(cli.httpx, "get", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(cli, "_handle_connection_error", lambda _exc, url: seen.append(url))
    cli._cmd_filestore_binaries(Namespace(daemon_url="http://daemon"))
    assert seen == ["http://daemon"]


def test_gather_and_format_platform_status_covers_present_and_absent_resources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The extracted platform module handles both populated and empty hosts."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "general-ludd.yml").write_text("version: 1\n")
    filestore = tmp_path / "filestore"
    filestore.mkdir()
    (filestore / "binary").write_bytes(b"data")
    database = tmp_path / "gludd.db"
    database.write_bytes(b"db")

    class Store:
        root_path = filestore

    class Bootstrapper:
        def __init__(self, *, store: object) -> None:
            assert isinstance(store, Store)

        def list_binaries(self) -> list[dict[str, str]]:
            return [{"name": "podman"}]

        def get_known_versions(self) -> dict[str, str]:
            return {"podman": "1", "docker": "2"}

        def list_binaries_with_versions(self) -> list[dict[str, str]]:
            return [{"binary_name": "podman", "version": "1"}]

    class Resolver:
        def is_available(self, name: str) -> bool:
            return name == "podman"

        def resolve(self, name: str) -> str:
            return f"/bin/{name}"

    monkeypatch.setattr(platform_commands, "FileStore", Store)
    monkeypatch.setattr(platform_commands, "BinaryBootstrapper", Bootstrapper)
    monkeypatch.setattr(platform_commands, "BinaryPathResolver", Resolver)
    monkeypatch.setattr(platform_commands, "get_default_db_url", lambda: f"sqlite+aiosqlite:///{database}")
    monkeypatch.setattr(platform_commands, "is_sqlite_url", lambda _url: True)

    info = platform_commands._gather_offline_status(str(config_dir))
    assert info["filestore_file_count"] == 1
    assert info["db_exists"] is True
    assert info["binary_paths"]["podman"] == "/bin/podman"
    assert info["binary_paths"]["docker"] is None
    platform_commands._format_offline_status(info)

    empty: dict[str, Any] = {
        **info,
        "config_files": [],
        "filestore_exists": False,
        "filestore_binaries": [],
        "binary_versions": {},
        "db_exists": False,
        "binary_paths": {},
    }
    platform_commands._format_offline_status(empty)
    output = capsys.readouterr().out
    assert "[stored]" in output
    assert "[not downloaded]" in output
    assert "(not created)" in output
    assert "(not yet created)" in output
