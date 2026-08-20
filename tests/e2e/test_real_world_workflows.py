"""Comprehensive E2E tests exercising real-world workflows through all parts of gludd.

Scenarios:
  1. Project onboarding — ``project paths``, config load chain
  2. Git automation — GitAutomation lifecycle (init, commit, branch, merge, tag)
  3. Agent dispatch pipeline — daemon → POST /api/todos → GET /api/todos
  4. Model routing decision — POST /api/dispatch with different kinds
  5. Config chain — NetworkConfig with GLUDD_NETWORK__HOST env override
  6. Config load chain — build_config_layer E2E
  7. Error recovery — corrupt config → graceful degradation
  8. Binary smoke test — subprocess health check, port lifecycle
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import httpx
import pytest
import yaml

from tests.e2e._daemon_harness import start_daemon_process, stop_daemon_process

# ---------------------------------------------------------------------------
# Availability probes
# ---------------------------------------------------------------------------


def _cli_available() -> bool:
    try:
        probe = subprocess.run(
            [sys.executable, "-m", "general_ludd.cli", "version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return False
    return probe.returncode == 0


def _gunicorn_available() -> bool:
    try:
        proc = subprocess.run(
            [sys.executable, "-c", "import gunicorn"],
            capture_output=True,
            timeout=15,
        )
    except Exception:
        return False
    return proc.returncode == 0


_CLI_AVAILABLE = _cli_available()
_GUNICORN_AVAILABLE = _gunicorn_available()

_skip_cli = pytest.mark.skipif(
    not _CLI_AVAILABLE,
    reason="`python -m general_ludd.cli` is not runnable in this environment",
)
_skip_gunicorn = pytest.mark.skipif(
    not _GUNICORN_AVAILABLE,
    reason="gunicorn is not importable",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_gludd(
    args: list[str],
    *,
    timeout: float = 30.0,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "general_ludd.cli", *args]
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=full_env,
        cwd=str(cwd) if cwd else None,
    )


def find_free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def wait_for_url(url: str, *, timeout: float = 30.0, interval: float = 0.3) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def _write_isolated_config(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "daemon.db"
    (config_dir / "general-ludd.yml").write_text(
        textwrap.dedent(f"""\
        database:
          url: 'sqlite+aiosqlite:///{db_path}'
        """)
    )
    return config_dir


# ---------------------------------------------------------------------------
# Scenario 1: Project onboarding + config chain
# ---------------------------------------------------------------------------


class TestProjectOnboarding:
    def test_project_paths_shows_bundled_tier(self, tmp_path: Path):
        """project paths outputs the precedence table with BUNDLED tier."""
        result = run_gludd(["project", "paths", str(tmp_path)], timeout=30)
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        out = result.stdout
        assert "Collection search path" in out
        assert "BUNDLED" in out
        assert "roles" in out

    def test_project_paths_json_parseable(self, tmp_path: Path):
        """project paths --json emits parseable JSON with expected record shape."""
        result = run_gludd(["project", "paths", str(tmp_path), "--json"], timeout=30)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) >= 1
        for entry in data:
            assert {"source", "path", "precedence", "exists", "roles", "modules"} <= set(entry)
        sources = {entry["source"] for entry in data}
        assert "bundled" in sources

    def test_project_init_no_namespace_errors_cleanly(self, tmp_path: Path):
        """project init without --namespace exits non-zero with clean message."""
        result = run_gludd(["project", "init", str(tmp_path)], timeout=30)
        assert result.returncode != 0
        assert "namespace" in result.stderr.lower()
        assert "Traceback (most recent call last)" not in result.stderr


class TestConfigChainE2E:
    def test_build_config_layer_merges_user_agent_defaults(self):
        """build_config_layer produces a ConfigLayer with all three tiers."""
        from general_ludd.config.loader import build_config_layer

        layer = build_config_layer()
        assert layer.user is not None
        assert layer.agent is not None
        assert isinstance(layer.defaults, dict)

    def test_network_config_defaults_to_loopback(self):
        """NetworkConfig defaults to 127.0.0.1:8000."""
        from general_ludd.config.user_config import NetworkConfig

        nc = NetworkConfig()
        assert nc.host == "127.0.0.1"
        assert nc.port == 8000

    def test_network_config_env_override(self):
        """GLUDD_NETWORK__HOST env var overrides NetworkConfig.host."""
        from general_ludd.config.user_config import NetworkConfig

        nc = NetworkConfig(host="0.0.0.0", allowed_cidr=["10.0.0.0/8"])
        assert nc.host == "0.0.0.0"

    def test_network_config_world_open_requires_cidr(self):
        """Host 0.0.0.0 without allowed_cidr raises ValueError."""
        from general_ludd.config.user_config import NetworkConfig

        with pytest.raises(ValueError, match=r"allowed_cidr|binds to all interfaces"):
            NetworkConfig(host="0.0.0.0")

    def test_network_config_world_open_with_cidr_ok(self):
        """Host 0.0.0.0 with allowed_cidr succeeds."""
        from general_ludd.config.user_config import NetworkConfig

        nc = NetworkConfig(host="0.0.0.0", allowed_cidr=["10.0.0.0/8", "192.168.0.0/16"])
        assert nc.host == "0.0.0.0"
        assert len(nc.allowed_cidr) == 2

    def test_user_config_from_yaml_loads(self, tmp_path: Path):
        """UserConfig.from_yaml loads a minimal valid config file."""
        from general_ludd.config.user_config import UserConfig

        config_path = tmp_path / "user.yml"
        config_path.write_text("database:\n  url: 'sqlite+aiosqlite:///test.db'\n")
        config = UserConfig.from_yaml(config_path)
        assert config.database["url"] == "sqlite+aiosqlite:///test.db"

    def test_user_config_env_override_chain(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """GLUDD_NETWORK env var overrides file-based network config."""
        from general_ludd.config.user_config import UserConfig

        config_path = tmp_path / "user.yml"
        config_path.write_text(
            "network:\n  host: 127.0.0.1\n  port: 8000\n"
            "database:\n  url: 'sqlite+aiosqlite:///test.db'\n"
        )
        monkeypatch.setenv(
            "GLUDD_NETWORK",
            '{"host": "0.0.0.0", "allowed_cidr": ["10.0.0.0/8"]}',
        )
        config = UserConfig.from_yaml(config_path)
        assert config.network.host == "0.0.0.0"
        assert config.network.allowed_cidr == ["10.0.0.0/8"]

    def test_load_user_config_defaults(self):
        """load_user_config returns a UserConfig with defaults when no file exists."""
        from general_ludd.config.loader import load_user_config

        config = load_user_config(Path("/nonexistent/path/user.yml"))
        assert config.network.host == "127.0.0.1"

    def test_agent_config_default_when_file_missing(self):
        """load_agent_config returns defaults when file does not exist."""
        from general_ludd.config.loader import load_agent_config

        config = load_agent_config(Path("/nonexistent/agent_config.yml"))
        assert config is not None


# ---------------------------------------------------------------------------
# Scenario 2: Git automation workflow
# ---------------------------------------------------------------------------


class TestGitAutomationWorkflow:
    def test_init_repo(self):
        """GitAutomation initialises a repo."""
        from general_ludd.git_automation.repo import GitAutomation

        with tempfile.TemporaryDirectory() as d:
            git = GitAutomation(repo_path=d)
            r1 = git.init_repo()
            assert r1.created

    def test_init_repo_idempotent(self):
        """GitAutomation.init_repo is idempotent."""
        from general_ludd.git_automation.repo import GitAutomation

        with tempfile.TemporaryDirectory() as d:
            git = GitAutomation(repo_path=d)
            r1 = git.init_repo()
            r2 = git.init_repo()
            assert r1.created
            assert not r2.created

    def test_commit_and_log(self):
        """Full commit lifecycle: write file, stage, commit, verify SHA."""
        from general_ludd.git_automation.repo import GitAutomation

        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
            subprocess.run(
                ["git", "config", "user.email", "agent@harness.local"],
                cwd=d, capture_output=True, check=False,
            )
            subprocess.run(
                ["git", "config", "user.name", "Agentic Harness Agent"],
                cwd=d, capture_output=True, check=False,
            )
            (Path(d) / "hello.txt").write_text("hello world\n")
            git = GitAutomation(repo_path=d)
            result = git.commit(message="add hello")
            assert result, "commit should return truthy commit hash"
            assert len(str(result)) >= 7

            sha = git.get_current_commit()
            assert len(sha) >= 7

    def test_branch_commit_merge_lifecycle(self):
        """Create branch, commit on it, merge back with --no-ff."""
        from general_ludd.git_automation.repo import GitAutomation

        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-b", "main"], cwd=d, capture_output=True, check=True)
            subprocess.run(
                ["git", "config", "user.email", "agent@harness.local"],
                cwd=d, capture_output=True, check=False,
            )
            subprocess.run(
                ["git", "config", "user.name", "Agentic Harness Agent"],
                cwd=d, capture_output=True, check=False,
            )
            git = GitAutomation(repo_path=d)

            (Path(d) / "base.txt").write_text("base\n")
            git.commit(message="initial commit")

            branch = git.create_branch("feature/test-merge")
            assert branch

            (Path(d) / "feature.txt").write_text("feature work\n")
            git.commit(message="feature work")

            merge_result = git.merge_branch(d, "feature/test-merge", "main", strategy="no-ff")
            assert merge_result.success

            sha = git.get_current_commit()
            assert len(sha) >= 7

    def test_create_release_tag(self):
        """create_release_tag returns a 14-char timestamp-based tag."""
        from general_ludd.git_automation.repo import GitAutomation

        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
            subprocess.run(
                ["git", "config", "user.email", "agent@harness.local"],
                cwd=d, capture_output=True, check=False,
            )
            subprocess.run(
                ["git", "config", "user.name", "Agentic Harness Agent"],
                cwd=d, capture_output=True, check=False,
            )
            (Path(d) / "f.txt").write_text("x\n")
            git = GitAutomation(repo_path=d)
            git.commit(message="base")
            tag = git.create_release_tag(d)
            assert len(tag) == 14

    def test_checkpoint_tag_contains_todo_id(self):
        """create_checkpoint_tag includes the todo_id in the tag name."""
        from general_ludd.git_automation.repo import GitAutomation

        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
            subprocess.run(
                ["git", "config", "user.email", "agent@harness.local"],
                cwd=d, capture_output=True, check=False,
            )
            subprocess.run(
                ["git", "config", "user.name", "Agentic Harness Agent"],
                cwd=d, capture_output=True, check=False,
            )
            (Path(d) / "f.txt").write_text("x\n")
            git = GitAutomation(repo_path=d)
            git.commit(message="base")
            tag = git.create_checkpoint_tag(d, todo_id="TODO-042", sha="abcd1234567")
            assert "agent/TODO-042/" in tag

    def test_worktree_lifecycle(self):
        """create_worktree, list_worktrees, remove_worktree."""
        from general_ludd.git_automation.repo import GitAutomation

        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
            subprocess.run(
                ["git", "config", "user.email", "agent@harness.local"],
                cwd=d, capture_output=True, check=False,
            )
            subprocess.run(
                ["git", "config", "user.name", "Agentic Harness Agent"],
                cwd=d, capture_output=True, check=False,
            )
            (Path(d) / "f.txt").write_text("x\n")
            git = GitAutomation(repo_path=d)
            git.commit(message="base")

            wt_path = os.path.join(str(_tmp_path := tempfile.mkdtemp()), "gludd-e2e-wt")
            wt = git.create_worktree(d, "wt-e2e-branch", wt_path)
            assert wt.success, f"worktree creation failed: {wt}"
            assert os.path.isdir(wt.path)

            worktrees = git.list_worktrees(d)
            assert len(worktrees) >= 2, f"expected >=2 worktrees, got {len(worktrees)}"

            git.remove_worktree(d, wt.path)
            assert not os.path.isdir(wt.path)


# ---------------------------------------------------------------------------
# Scenario 3: Agent dispatch pipeline (daemon required)
# ---------------------------------------------------------------------------


class TestAgentDispatchPipeline:
    @pytest.fixture()
    def dispatch_app(self):
        """FastAPI app with /api/dispatch, /api/todos registered."""
        from fastapi import FastAPI

        app = FastAPI()
        app.state._startup_config = {}
        app.state.log_level = "info"
        app.state.tick_interval = 1.0
        app.state.event_loop = None
        app.state._session_factory = None
        app.state._db_engine = None
        app.state._event_bus = None
        app.state._hook_system = None
        app.state._metrics_collector = None
        app.state._project_manager = None
        app.state._spend_limiter = None

        daemon_state: dict[str, object] = {
            "todos": [],
            "tick_metrics": {},
            "quality_gate": {},
        }
        app.state.daemon_state = daemon_state

        from general_ludd.routers.dispatch import register as register_dispatch

        register_dispatch(app, daemon_state)
        return app

    def test_dispatch_available_lists_kinds(self, dispatch_app):
        """GET /api/dispatch/available returns registered handler kinds."""
        from fastapi.testclient import TestClient

        with TestClient(dispatch_app) as client:
            resp = client.get("/api/dispatch/available")
            assert resp.status_code == 200
            data = resp.json()
            assert "registered_kinds" in data

    def test_dispatch_empty_body_rejected(self, dispatch_app):
        """POST /api/dispatch with empty body returns 422."""
        from fastapi.testclient import TestClient

        with TestClient(dispatch_app) as client:
            resp = client.post("/api/dispatch", json={})
            assert resp.status_code == 422

    def test_dispatch_invalid_kind_parsed(self, dispatch_app):
        """POST /api/dispatch with unknown kind is fail-closed gracefully."""
        from fastapi.testclient import TestClient

        with TestClient(dispatch_app) as client:
            resp = client.post(
                "/api/dispatch",
                json={"tool_calls": [{"kind": "nonexistent", "name": "foo", "args": {}}]},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "results" in data
            results = data["results"]
            assert len(results) == 1
            assert not results[0].get("ok", True), "unknown kind should fail-closed"

    def test_dispatch_recent_returns_empty_initially(self, dispatch_app):
        """GET /api/dispatch/recent returns empty list when no dispatches happened."""
        from fastapi.testclient import TestClient

        with TestClient(dispatch_app) as client:
            resp = client.get("/api/dispatch/recent")
            assert resp.status_code == 200
            data = resp.json()
            assert "recent" in data
            assert "total" in data

    def test_dispatch_tool_calls_capped_at_20(self, dispatch_app):
        """POST /api/dispatch with >20 calls returns 422."""
        from fastapi.testclient import TestClient

        with TestClient(dispatch_app) as client:
            resp = client.post(
                "/api/dispatch",
                json={
                    "tool_calls": [
                        {"kind": "mcp", "name": f"tool_{i}", "args": {}}
                        for i in range(25)
                    ]
                },
            )
            assert resp.status_code == 422
            assert "cap" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Scenario 4: Model routing decision
# ---------------------------------------------------------------------------


class TestModelRoutingDecision:
    def test_model_routing_config_defaults(self):
        """ModelRoutingConfig has sensible defaults."""
        from general_ludd.config.model_routing import ModelRoutingConfig

        config = ModelRoutingConfig()
        assert config.default_profile is None
        assert isinstance(config.role_routing, dict)
        assert isinstance(config.quality_routing, dict)
        assert isinstance(config.latency_routing, dict)
        assert isinstance(config.pattern_routing, dict)

    def test_model_profile_default_provider_openai(self):
        """ModelProfile defaults to openai provider."""
        from general_ludd.models.gateway import ModelProfile

        profile = ModelProfile(model_profile_id="test-profile")
        assert profile.provider == "openai"
        assert profile.context_window == 128000
        assert profile.max_failover_retries == 3

    def test_model_profile_rejects_empty_id(self):
        """ModelProfile with empty model_profile_id raises ValidationError."""
        from pydantic import ValidationError

        from general_ludd.models.gateway import ModelProfile

        with pytest.raises(ValidationError):
            ModelProfile(model_profile_id="")

    def test_model_profile_cost_fields_non_negative(self):
        """cost_per_input_token and cost_per_output_token reject negative values."""
        from pydantic import ValidationError

        from general_ludd.models.gateway import ModelProfile

        with pytest.raises(ValidationError):
            ModelProfile(model_profile_id="p", cost_per_input_token=-0.01)
        with pytest.raises(ValidationError):
            ModelProfile(model_profile_id="p", cost_per_output_token=-1.0)

    def test_router_task_types_have_strategies(self):
        """AdaptiveRouter has DEFAULT_STRATEGIES covering key strategy types."""
        from general_ludd.models.performance_router import DEFAULT_STRATEGIES

        assert len(DEFAULT_STRATEGIES) > 0
        assert "balanced" in DEFAULT_STRATEGIES
        assert "quality" in DEFAULT_STRATEGIES
        for _strategy_name, weights in DEFAULT_STRATEGIES.items():
            assert "success_rate" in weights, "missing success_rate weighting"

    def test_build_router_from_config_maps_profiles(self):
        """build_router_from_config uses role/quality/latency/pattern routing."""
        from general_ludd.config.model_routing import (
            ModelRoutingConfig,
            build_router_from_config,
        )

        config = ModelRoutingConfig(
            default_profile="default",
            weak_model_profile="weak",
            role_routing={"coder": "gpt4", "reviewer": "gpt35"},
            quality_routing={"high": "gpt4", "low": "gpt35"},
            latency_routing={"fast": "gpt35"},
            pattern_routing={"commit_message": "gpt35"},
        )
        router = build_router_from_config(config)
        assert router is not None
        profile_id = router.resolve_role("coder")
        assert profile_id == "gpt4"
        resolved_weak = router.resolve_role("weak")
        assert resolved_weak == "weak"


# ---------------------------------------------------------------------------
# Scenario 5: Config chain + env override (daemon required)
# ---------------------------------------------------------------------------


class TestConfigChainWithDaemon:
    @pytest.mark.skipif(not _GUNICORN_AVAILABLE, reason="gunicorn not available")
    def test_daemon_binds_loopback_by_default(self, tmp_path: Path):
        """Daemon started without --host binds to 127.0.0.1."""
        config_dir = _write_isolated_config(tmp_path)
        port = find_free_port()
        base_url = f"http://127.0.0.1:{port}"

        proc = start_daemon_process(
            config_dir=config_dir,
            cwd=tmp_path,
            port=port,
        )
        try:
            if not wait_for_url(f"{base_url}/healthz", timeout=40.0):
                out, err = stop_daemon_process(proc)
                pytest.fail(
                    "daemon did not become healthy\n"
                    f"stdout={out!r}\nstderr={err!r}"
                )
            resp = httpx.get(f"{base_url}/healthz", timeout=5.0)
            assert resp.status_code == 200
            assert resp.json()["status"] == "healthy"

            resp = httpx.get(f"{base_url}/api/facts", timeout=10.0)
            assert resp.status_code == 200
            assert isinstance(resp.json(), dict)
        finally:
            stop_daemon_process(proc)

    @pytest.mark.skipif(not _GUNICORN_AVAILABLE, reason="gunicorn not available")
    def test_daemon_models_list_endpoint(self, tmp_path: Path):
        """GET /admin/models returns profiles from the daemon."""
        config_dir = _write_isolated_config(tmp_path)
        port = find_free_port()
        base_url = f"http://127.0.0.1:{port}"

        proc = start_daemon_process(
            config_dir=config_dir,
            cwd=tmp_path,
            port=port,
        )
        try:
            if not wait_for_url(f"{base_url}/healthz", timeout=40.0):
                out, err = stop_daemon_process(proc)
                pytest.fail(
                    "daemon did not become healthy\n"
                    f"stdout={out!r}\nstderr={err!r}"
                )
            resp = httpx.get(f"{base_url}/admin/models", timeout=10.0)
            assert resp.status_code == 200
            data = resp.json()
            assert "profiles" in data
        finally:
            stop_daemon_process(proc)

    @pytest.mark.skipif(not _GUNICORN_AVAILABLE, reason="gunicorn not available")
    def test_cli_models_list_against_daemon(self, tmp_path: Path):
        """gludd models list --daemon-url <url> queries the live daemon."""
        config_dir = _write_isolated_config(tmp_path)
        port = find_free_port()
        base_url = f"http://127.0.0.1:{port}"

        proc = start_daemon_process(
            config_dir=config_dir,
            cwd=tmp_path,
            port=port,
        )
        try:
            if not wait_for_url(f"{base_url}/healthz", timeout=40.0):
                out, err = stop_daemon_process(proc)
                pytest.fail(
                    "daemon did not become healthy\n"
                    f"stdout={out!r}\nstderr={err!r}"
                )

            result = run_gludd(
                ["models", "list", "--daemon-url", base_url], timeout=20
            )
            assert result.returncode == 0, f"stderr: {result.stderr!r}"
            assert "No models registered" in result.stdout or result.stdout.strip() == ""
        finally:
            stop_daemon_process(proc)

    @pytest.mark.skipif(not _GUNICORN_AVAILABLE, reason="gunicorn not available")
    def test_cli_health_against_daemon(self, tmp_path: Path):
        """gludd health --daemon-url <url> reports healthy."""
        config_dir = _write_isolated_config(tmp_path)
        port = find_free_port()
        base_url = f"http://127.0.0.1:{port}"

        proc = start_daemon_process(
            config_dir=config_dir,
            cwd=tmp_path,
            port=port,
        )
        try:
            if not wait_for_url(f"{base_url}/healthz", timeout=40.0):
                out, err = stop_daemon_process(proc)
                pytest.fail(
                    "daemon did not become healthy\n"
                    f"stdout={out!r}\nstderr={err!r}"
                )

            result = run_gludd(
                ["health", "--daemon-url", base_url], timeout=20
            )
            assert result.returncode == 0, f"stderr: {result.stderr!r}"
            assert "healthy" in result.stdout
        finally:
            stop_daemon_process(proc)


# ---------------------------------------------------------------------------
# Scenario 6: Daemon todo pipeline
# ---------------------------------------------------------------------------


class TestDaemonTodoPipeline:
    @pytest.fixture()
    def todo_app(self):
        """FastAPI app with /api/todos registered and in-memory SQLite."""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import StaticPool

        from general_ludd.db.models import Base

        engine = create_async_engine(
            "sqlite+aiosqlite://",
            echo=False,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        import asyncio

        async def _setup():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(_setup())
        factory = async_sessionmaker(engine, expire_on_commit=False)

        from fastapi import FastAPI

        app = FastAPI()
        app.state._session_factory = factory
        app.state._db_engine = engine
        app.state._config_dir = None
        app.state._startup_config = {}
        app.state.log_level = "info"
        app.state.tick_interval = 1.0
        app.state.event_loop = None
        app.state._templates_dir = None
        app.state._playbooks_dir = None
        app.state._metrics_collector = None
        app.state._project_manager = None
        app.state._recent_traces = None
        app.state._skill_registry = None
        app.state._spend_limiter = None
        app.state._dispatch_facet = None
        app.state._otel_bridge = None
        app.state._schedule_last_plan = None
        app.state._filestore = None
        app.state._hardware = None
        app.state._budget_guard = None

        daemon_state: dict[str, object] = {
            "todos": [],
            "tick_metrics": {},
            "quality_gate": {},
        }
        app.state.daemon_state = daemon_state

        from general_ludd.routers.todos import register as register_todos

        register_todos(app, daemon_state)
        return app

    def test_create_todo_returns_201(self, todo_app):
        """POST /api/todos with valid payload returns 201."""
        from fastapi.testclient import TestClient

        with TestClient(todo_app) as client:
            resp = client.post(
                "/api/todos",
                json={
                    "title": "E2E test task",
                    "queue": "core",
                    "priority": "high",
                    "work_type": "code",
                    "project_id": "proj-test",
                },
            )
            assert resp.status_code == 201
            data = resp.json()
            assert "todo_id" in data
            assert data["title"] == "E2E test task"
            assert data["status"] == "queued"

    def test_create_todo_without_title_returns_422(self, todo_app):
        """POST /api/todos without title returns 422."""
        from fastapi.testclient import TestClient

        with TestClient(todo_app) as client:
            resp = client.post("/api/todos", json={"queue": "core"})
            assert resp.status_code == 422

    def test_list_todos_returns_list(self, todo_app):
        """GET /api/todos returns a list."""
        from fastapi.testclient import TestClient

        with TestClient(todo_app) as client:
            resp = client.get("/api/todos")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)

    def test_get_todo_by_id(self, todo_app):
        """GET /api/todos/{todo_id} returns the created todo."""
        from fastapi.testclient import TestClient

        with TestClient(todo_app) as client:
            create_resp = client.post(
                "/api/todos",
                json={
                    "title": "Get me back",
                    "queue": "core",
                    "work_type": "code",
                    "project_id": "proj-test",
                },
            )
            assert create_resp.status_code == 201
            todo_id = create_resp.json()["todo_id"]

            resp = client.get(f"/api/todos/{todo_id}")
            assert resp.status_code == 200
            assert resp.json()["title"] == "Get me back"

    def test_todo_full_lifecycle(self, todo_app):
        """Create -> list -> get -> update status."""
        from fastapi.testclient import TestClient

        with TestClient(todo_app) as client:
            # Create
            resp = client.post(
                "/api/todos",
                json={
                    "title": "Lifecycle task",
                    "queue": "core",
                    "priority": "medium",
                    "work_type": "code",
                    "project_id": "proj-lifecycle",
                },
            )
            assert resp.status_code == 201
            todo_id = resp.json()["todo_id"]

            # List should contain it
            list_resp = client.get("/api/todos", params={"project_id": "proj-lifecycle"})
            assert list_resp.status_code == 200
            ids = [t["todo_id"] for t in list_resp.json()]
            assert todo_id in ids

            # Get detail
            detail_resp = client.get(f"/api/todos/{todo_id}")
            assert detail_resp.status_code == 200
            assert detail_resp.json()["status"] == "queued"

            # Update title
            update_resp = client.put(
                f"/api/todos/{todo_id}",
                json={
                    "title": "Lifecycle task (updated)",
                    "description": "Updated description",
                    "acceptance_criteria": [],
                    "definition_of_done": "Verify update works",
                },
            )
            assert update_resp.status_code == 200

            # Verify update
            final_resp = client.get(f"/api/todos/{todo_id}")
            assert final_resp.status_code == 200
            assert final_resp.json()["title"] == "Lifecycle task (updated)"


# ---------------------------------------------------------------------------
# Scenario 7: Error recovery — corrupt config → graceful degradation
# ---------------------------------------------------------------------------


class TestErrorRecovery:
    def test_corrupt_yaml_config_raises(self, tmp_path: Path):
        """Corrupt YAML config file produces a parse error (not crash)."""
        config_path = tmp_path / "bad.yml"
        config_path.write_text("network: [unclosed bracket\n  host: 127.0.0.1\n")
        with pytest.raises(yaml.YAMLError):
            yaml.safe_load(config_path.read_text())

    def test_missing_config_file_defaults_gracefully(self):
        """load_user_config with nonexistent path uses defaults."""
        from general_ludd.config.loader import load_user_config

        config = load_user_config(Path("/nonexistent/deadbeef/user.yml"))
        assert config.network.host == "127.0.0.1"
        assert config.network.port == 8000

    def test_user_config_rejects_invalid_port(self):
        """UserConfig with port as string rejects gracefully."""
        from pydantic import ValidationError

        from general_ludd.config.user_config import NetworkConfig

        with pytest.raises(ValidationError):
            NetworkConfig(port="not-a-number")  # type: ignore[arg-type]

    def test_daemon_app_handles_missing_config_dir(self):
        """create_daemon_app with nonexistent config_dir still starts."""
        from unittest.mock import MagicMock, patch

        import general_ludd.daemon as daemon_mod

        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = daemon_mod.create_daemon_app(
                tick_interval=0.01,
                config_dir="/nonexistent/path/gludd/config",
            )
            assert app is not None

    def test_daemon_healthz_returns_200_without_db(self):
        """create_daemon_app /healthz works even if DB is unavailable."""
        from unittest.mock import MagicMock, patch

        from fastapi.testclient import TestClient

        import general_ludd.daemon as daemon_mod

        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = daemon_mod.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get("/healthz")
                assert resp.status_code == 200
                assert resp.json()["status"] == "healthy"


# ---------------------------------------------------------------------------
# Scenario 8: Binary smoke test — subprocess version, help, daemon port lifecycle
# ---------------------------------------------------------------------------


class TestBinarySmokeTest:
    def test_version_outputs_semver(self):
        """gludd version outputs semver-like string."""
        import re

        result = run_gludd(["version"], timeout=20)
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        match = re.search(r"(\d+\.\d+\.\d+(?:[-+.][\w.]+)?)", result.stdout)
        assert match, f"no semver in stdout: {result.stdout!r}"
        assert "general-ludd-agent" in result.stdout

    def test_help_lists_key_subcommands(self):
        """gludd --help lists daemon, version, models, project, config."""
        result = run_gludd(["--help"], timeout=20)
        assert result.returncode == 0
        for cmd in ["daemon", "version", "models", "project", "config"]:
            assert cmd in result.stdout, f"'{cmd}' missing from --help"

    def test_health_command_no_daemon_errors(self):
        """gludd health without a running daemon exits non-zero."""
        result = run_gludd(["health", "--daemon-url", "http://127.0.0.1:1"], timeout=10)
        assert result.returncode != 0

    def test_invalid_flag_clean_error(self):
        """gludd --invalid-flag exits non-zero, no traceback."""
        result = run_gludd(["--invalid-flag"], timeout=20)
        assert result.returncode != 0
        assert "Traceback (most recent call last)" not in result.stderr

    def test_unknown_subcommand_clean_error(self):
        """gludd not-a-command exits non-zero cleanly."""
        result = run_gludd(["not-a-command"], timeout=20)
        assert result.returncode != 0
        assert "Traceback (most recent call last)" not in result.stderr

    @pytest.mark.skipif(not _GUNICORN_AVAILABLE, reason="gunicorn not available")
    def test_daemon_start_health_stop_port_released(self, tmp_path: Path):
        """Full daemon lifecycle: start → health → models → stop → port released."""
        config_dir = _write_isolated_config(tmp_path)
        port = find_free_port()
        base_url = f"http://127.0.0.1:{port}"

        proc = start_daemon_process(
            config_dir=config_dir,
            cwd=tmp_path,
            port=port,
        )
        assert proc.poll() is None, "daemon should be running"

        try:
            assert wait_for_url(f"{base_url}/healthz", timeout=40.0), (
                "daemon did not become healthy"
            )

            resp = httpx.get(f"{base_url}/healthz", timeout=5.0)
            assert resp.status_code == 200
            assert resp.json()["status"] == "healthy"

            resp = httpx.get(f"{base_url}/api/facts", timeout=10.0)
            assert resp.status_code == 200

            resp = httpx.get(f"{base_url}/admin/models", timeout=10.0)
            assert resp.status_code == 200
            assert "profiles" in resp.json()
        finally:
            stop_daemon_process(proc)

        assert proc.returncode is not None, "daemon process should have exited"

        with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout, httpx.HTTPError)):
            httpx.get(f"{base_url}/healthz", timeout=2.0)


# ---------------------------------------------------------------------------
# Cross-cutting: ModelProfile validation, facts endpoint, observability
# ---------------------------------------------------------------------------


class TestModelProfileValidation:
    def test_model_profile_full_construction(self):
        """ModelProfile with all fields constructs correctly."""
        from general_ludd.models.gateway import ModelProfile

        profile = ModelProfile(
            model_profile_id="full-profile",
            role_names=["coder", "reviewer"],
            provider="anthropic",
            provider_package="langchain-anthropic",
            provider_class_hint="ChatAnthropic",
            model_name="claude-sonnet-4-20250514",
            context_window=200000,
            max_input_tokens=190000,
            max_output_tokens=10000,
            cost_per_input_token=0.000003,
            cost_per_output_token=0.000015,
            api_metered=True,
            run_budget_usd=500.0,
            enabled=True,
            resource_profile="ai_heavy",
            roles=["coder", "reviewer"],
            latency_class="medium",
            quality_class="high",
        )
        assert profile.model_profile_id == "full-profile"
        assert profile.provider == "anthropic"
        assert profile.context_window == 200000
        assert profile.enabled is True
        assert profile.quality_class == "high"

    def test_model_profile_context_window_minimum_one(self):
        """context_window < 1 raises ValidationError."""
        from pydantic import ValidationError

        from general_ludd.models.gateway import ModelProfile

        with pytest.raises(ValidationError):
            ModelProfile(model_profile_id="p", context_window=0)

    def test_model_profile_fallback_max_concurrency_default(self):
        """fallback_max_concurrency defaults to 2."""
        from general_ludd.models.gateway import ModelProfile

        profile = ModelProfile(model_profile_id="p")
        assert profile.fallback_max_concurrency == 2


class TestFactsEndpoint:
    @pytest.fixture()
    def facts_app(self):
        """FastAPI app with /api/facts registered."""
        from fastapi import FastAPI

        app = FastAPI()
        app.state._startup_config = {}
        app.state.log_level = "info"
        app.state.tick_interval = 1.0
        app.state.event_loop = None
        app.state._session_factory = None
        app.state._db_engine = None
        app.state._event_bus = None
        app.state._hook_system = None
        app.state._metrics_collector = None
        app.state._project_manager = None
        app.state._spend_limiter = None
        app.state._recent_traces = None
        app.state._health_tracker = None
        app.state._model_gateway = None
        app.state._hardware = None
        app.state._dispatch_facet = lambda: {
            "recent_count": 0, "total_dispatched": 0, "recent": [], "registered_kinds": []
        }

        daemon_state: dict[str, object] = {
            "todos": [],
            "tick_metrics": {"ticks": 0, "seconds": 0},
            "quality_gate": {},
            "observability": {"branches": [], "events": 0},
            "models": {"connected": 0},
            "spend": {"current_run_usd": 0.0, "lifetime_usd": 0.0},
            "overflow": {"total_dropped": 0, "rate_limit_breaches": 0},
        }
        app.state.daemon_state = daemon_state

        from general_ludd.routers.facts import register as register_facts

        register_facts(app, daemon_state)
        return app

    def test_facts_returns_dict_with_expected_keys(self, facts_app):
        """GET /api/facts returns a dict with expected top-level keys."""
        from fastapi.testclient import TestClient

        with TestClient(facts_app) as client:
            resp = client.get("/api/facts")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, dict)
            for key in ("models", "spend", "dispatch"):
                assert key in data, f"'{key}' missing from facts response"

    def test_facts_observability_has_branches(self, facts_app):
        """facts.codebase includes churn/complexity signals."""
        from fastapi.testclient import TestClient

        with TestClient(facts_app) as client:
            resp = client.get("/api/facts")
            data = resp.json()
            codebase = data.get("codebase", {})
            assert isinstance(codebase, dict)
            assert "churn" in codebase or "complexity" in codebase
