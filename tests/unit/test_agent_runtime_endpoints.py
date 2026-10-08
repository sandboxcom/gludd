"""Typed daemon seams used by the agent collection runtime."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from general_ludd.routers._runtime import IdempotencyStore
from general_ludd.security.permissions import Capability, PermissionSpec


@pytest.mark.asyncio
async def test_idempotency_store_is_bounded_and_rejects_payload_reuse() -> None:
    store = IdempotencyStore(max_entries=1)
    calls: list[str] = []

    async def _produce(value: str) -> dict[str, Any]:
        calls.append(value)
        return {"value": value}

    assert await store.run(
        key=None,
        payload={"value": "uncached"},
        producer=lambda: _produce("uncached"),
    ) == {"value": "uncached"}
    assert await store.run(
        key="first",
        payload={"value": "one"},
        producer=lambda: _produce("one"),
    ) == {"value": "one"}
    replay = await store.run(
        key="first",
        payload={"value": "one"},
        producer=lambda: _produce("unexpected"),
    )
    assert replay == {"value": "one", "idempotent_replay": True}
    with pytest.raises(HTTPException, match="different request"):
        await store.run(
            key="first",
            payload={"value": "conflict"},
            producer=lambda: _produce("conflict"),
        )
    await store.run(
        key="second",
        payload={"value": "two"},
        producer=lambda: _produce("two"),
    )
    await store.run(
        key="first",
        payload={"value": "one"},
        producer=lambda: _produce("one-again"),
    )
    assert calls == ["uncached", "one", "two", "one-again"]


def _authorize(app: FastAPI) -> None:
    spec = PermissionSpec(
        agent_type="agent-runtime-test",
        capabilities=[
            Capability(resource="admin:git", actions=["execute"]),
            Capability(resource="admin:reload", actions=["write"]),
            Capability(resource="admin:skills", actions=["render"]),
            Capability(resource="admin:slurm", actions=["deploy"]),
            Capability(resource="api:observe", actions=["query"]),
            Capability(resource="admin:abtest", actions=["execute"]),
        ],
    )

    @app.middleware("http")
    async def _attach_auth_spec(request: Request, call_next: Any) -> Any:
        request.state.auth_spec = spec
        return await call_next(request)


def _runtime_app(*, authorized: bool = True) -> FastAPI:
    from general_ludd.routers import benchmark, git_history, observe, reload, skills, slurm

    app = FastAPI()
    app.state._config_dir = None
    app.state._project_gludd_dir = None
    app.state._templates_dir = None
    app.state._playbooks_dir = None
    git_history.register(app, {})
    reload.register(app, {})
    skills.register(app, {})
    slurm.register(app, {})
    observe.register(app, {})
    benchmark.register(app, {})
    if authorized:
        _authorize(app)
    return app


def test_git_operation_is_typed_and_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    from general_ludd.routers import git_history

    class FakeGit:
        commits = 0

        def __init__(self, repo_path: str = ".") -> None:
            self.repo_path = repo_path

        def changed_files(self) -> list[str]:
            return ["src/app.py"]

        def commit(self, message: str) -> str:
            type(self).commits += 1
            return "abc1234"

    monkeypatch.setattr(git_history, "GitAutomation", FakeGit, raising=False)
    client = TestClient(_runtime_app())
    body = {
        "op": "commit",
        "path": "/repo",
        "message": "boundary migration",
        "idempotency_key": "commit:boundary-migration",
    }

    first = client.post("/admin/git/operation", json=body)
    second = client.post("/admin/git/operation", json=body)

    assert first.status_code == 200
    assert first.json()["result"]["sha"] == "abc1234"
    assert first.json()["changed"] is True
    assert second.json()["idempotent_replay"] is True
    assert FakeGit.commits == 1
    assert client.post(
        "/admin/git/operation",
        json={"op": "gated_commit", "path": "/repo", "message": "x", "gate_cmd": ["sh", "-c"]},
    ).status_code == 422


def test_reload_code_uses_digest_bound_candidate_and_replays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.routers import reload

    calls: list[dict[str, Any]] = []

    class FakeReloader:
        def __init__(self, **_: Any) -> None:
            pass

        def reload_code_module(self, **kwargs: Any) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(
                success=True,
                scope="code_module",
                details={"module": kwargs["module_name"]},
                error=None,
            )

    monkeypatch.setattr(reload, "HotReloader", FakeReloader)
    client = TestClient(_runtime_app())
    body = {
        "module_name": "general_ludd.example.leaf",
        "candidate_source_path": "/tmp/gludd-candidate/leaf.py",
        "expected_sha256": "a" * 64,
        "health_url": "http://127.0.0.1:8000/readyz",
        "health_timeout": 2.0,
        "idempotency_key": "reload:leaf:" + "a" * 16,
    }

    assert client.post("/admin/reload/code", json=body).json()["success"] is True
    replay = client.post("/admin/reload/code", json=body)
    assert replay.json()["idempotent_replay"] is True
    assert len(calls) == 1
    assert calls[0]["expected_sha256"] == "a" * 64
    assert client.post(
        "/admin/reload/code",
        json={**body, "health_url": "https://attacker.example/readyz"},
    ).status_code == 422


def test_skill_render_is_confined_to_daemon_skill_roots(tmp_path: Path) -> None:
    project_gludd = tmp_path / ".gludd"
    skills_dir = project_gludd / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "review.md").write_text(
        "---\nname: review\ntrigger_patterns: [review this]\n---\nHello {{ project }}!\n",
        encoding="utf-8",
    )
    app = _runtime_app()
    app.state._project_gludd_dir = project_gludd
    client = TestClient(app)

    response = client.post(
        "/admin/skills/render",
        json={"name": "review", "variables": {"project": "Gludd"}},
    )

    assert response.status_code == 200
    assert response.json() == {
        "skill_name": "review",
        "rendered_body": "Hello Gludd!",
        "required_vars": ["project"],
    }
    assert client.post(
        "/admin/skills/render",
        json={"name": "review", "skills_path": "/etc", "variables": {}},
    ).status_code == 422


def test_slurm_deploy_is_bounded_idempotent_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.routers import slurm

    class FakeAdapter:
        cancelled: ClassVar[list[str]] = []

        def cancel(self, job_id: str) -> None:
            self.cancelled.append(job_id)

    adapter = FakeAdapter()

    class FakeDeployment:
        submits = 0

        def __init__(self, adapter: Any = None) -> None:
            assert adapter is not None

        def submit(self, **_: Any) -> str:
            type(self).submits += 1
            return "job-42"

        def poll_until_servable(self, **_: Any) -> str:
            return "http://gpu-1:8000"

    monkeypatch.setattr(slurm, "_make_adapter", lambda _app: adapter)
    monkeypatch.setattr(slurm, "VllmSlurmDeployment", FakeDeployment, raising=False)
    client = TestClient(_runtime_app())
    body = {
        "engine": "vllm",
        "model_id": "org/model",
        "artifact_dir": str(tmp_path),
        "poll_timeout": 30,
        "idempotency_key": "deploy:org-model",
    }

    assert client.post("/admin/slurm/deploy", json=body).json()["job_id"] == "job-42"
    assert client.post("/admin/slurm/deploy", json=body).json()["idempotent_replay"] is True
    assert FakeDeployment.submits == 1
    assert client.post(
        "/admin/slurm/deploy", json={**body, "poll_timeout": 3601}
    ).status_code == 422


def test_observe_facade_is_bounded_and_json_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    from general_ludd.routers import observe

    class Source:
        name = "prod-logs"
        KIND = "logs"

        def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
            return [{"ts": 1.0, "labels": {"service": "api", "host": "web-1"}, **spec}]

    source = Source()

    class Registry:
        def names(self) -> list[str]:
            return [source.name]

        def get(self, name: str) -> Source | None:
            return source if name == source.name else None

    monkeypatch.setattr(observe, "_get_registry", lambda _app: Registry())
    client = TestClient(_runtime_app())
    response = client.post(
        "/api/observe/facade",
        json={"operation": "topology", "kinds": ["logs"], "spec": {}},
    )

    assert response.status_code == 200
    assert response.json()["result"]["topology"] == {
        "services": {"api": ["web-1"]},
        "hosts": {"web-1": ["api"]},
    }
    assert client.post(
        "/api/observe/facade",
        json={"operation": "timeline", "kinds": ["logs"], "unexpected": True},
    ).status_code == 422


def test_abtest_is_allowlisted_and_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from general_ludd.routers import benchmark

    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    verdict = SimpleNamespace(
        promote=True,
        to_dict=lambda: {"a": {"ok": True}, "b": {"ok": True}, "promote": True, "reason": "ok"},
    )
    calls: list[tuple[Any, ...]] = []

    def _run_ab(*args: Any, **kwargs: Any) -> Any:
        calls.append((*args, kwargs))
        return verdict

    monkeypatch.setattr(
        benchmark,
        "run_ab",
        _run_ab,
        raising=False,
    )
    app = _runtime_app()
    app.state._project_root = tmp_path
    client = TestClient(app)
    body = {
        "baseline_root": str(baseline),
        "candidate_root": str(candidate),
        "module": "general_ludd.example.leaf",
        "timeout": 2.0,
        "mem_limit_mb": 128,
    }

    response = client.post("/admin/abtest/run", json=body)

    assert response.status_code == 200
    assert response.json()["promote"] is True
    assert len(calls) == 1
    assert client.post(
        "/admin/abtest/run", json={**body, "module": "os"}
    ).status_code == 422


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/admin/git/operation", {"op": "current_branch", "path": "/repo"}),
        (
            "/admin/reload/code",
            {
                "module_name": "general_ludd.example.leaf",
                "candidate_source_path": "/tmp/gludd-candidate/leaf.py",
                "expected_sha256": "a" * 64,
            },
        ),
        ("/admin/skills/render", {"name": "review"}),
        ("/admin/slurm/deploy", {"engine": "vllm", "model_id": "org/model", "artifact_dir": "/tmp/x"}),
        ("/api/observe/facade", {"operation": "timeline"}),
        (
            "/admin/abtest/run",
            {
                "baseline_root": "/tmp/gludd-worktrees/base",
                "candidate_root": "/tmp/gludd-worktrees/candidate",
                "module": "general_ludd.example.leaf",
            },
        ),
    ],
)
def test_runtime_seams_fail_closed_without_capability(path: str, body: dict[str, Any]) -> None:
    response = TestClient(_runtime_app(authorized=False)).post(path, json=body)
    assert response.status_code == 403
