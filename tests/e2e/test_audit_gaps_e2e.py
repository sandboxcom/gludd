"""E2E tests covering audit gaps: daemon dispatch, app lifecycle, logging, packaging."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app
from general_ludd.event_loop.loop import PHASE_ORDER, EventLoop

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(autouse=True)
def _reset_daemon_state():
    if daemon_mod._daemon_state is None:
        daemon_mod._daemon_state = {}
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}


class TestDaemonDirectDispatch:
    @pytest.mark.asyncio
    async def test_tick_with_runner_dispatches_via_runner_not_http(self):
        mock_runner = MagicMock()
        mock_runner.prepare_job_dirs.return_value = {
            "root": "/tmp/test",
            "env": "/tmp/test/env",
            "project": "/tmp/test/project",
            "inventory": "/tmp/test/inventory",
            "artifacts": "/tmp/test/artifacts",
        }
        mock_runner.write_vars.return_value = "/tmp/test/env/extravars"
        mock_runner.run_playbook.return_value = {"status": "successful", "rc": 0, "events": []}

        mock_todo = MagicMock()
        mock_todo.todo_id = "TODO-001"
        mock_todo.queue = "core"
        mock_todo.work_type = "code"
        mock_todo.resource_profile = "low_resource"
        mock_todo.plan_artifact = None

        mock_todo_repo = AsyncMock()
        mock_todo_repo.claim_runnable.return_value = [mock_todo]

        mock_project = MagicMock()
        mock_project.project_id = "project-test"
        mock_project_manager = MagicMock()
        mock_project_manager.select_project.return_value = mock_project

        loop = EventLoop(
            runner=mock_runner,
            todo_repo=mock_todo_repo,
            project_manager=mock_project_manager,
        )
        metrics = await loop.tick()

        assert metrics["phases_completed"] == len(PHASE_ORDER)
        assert metrics["todos_dispatched"] == 1
        mock_todo_repo.claim_runnable.assert_awaited_once()
        assert mock_todo_repo.claim_runnable.await_args.kwargs["project_id"] == "project-test"
        mock_runner.run_playbook.assert_called_once()
        call_kwargs = mock_runner.run_playbook.call_args
        assert call_kwargs[1]["playbook_name"] in ("noop.yml", "validate_task.yml")


class TestDaemonAppCreatesEventLoopWithRunner:
    def test_lifespan_creates_event_loop_with_runner_via_testclient(self):
        mock_runner = MagicMock()
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=mock_runner):
            app = create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                assert app.state.event_loop is not None
                assert app.state.event_loop._runner is not None


class TestHTTPDebugLogging:
    def test_debug_log_level_sets_httpx_logger_to_debug(self):
        original_level = logging.getLogger("httpx").level
        try:
            create_daemon_app(log_level="debug")
            assert logging.getLogger("httpx").level == logging.DEBUG
        finally:
            logging.getLogger("httpx").setLevel(original_level)

    def test_info_log_level_does_not_set_httpx_logger_to_debug(self):
        original_level = logging.getLogger("httpx").level
        try:
            create_daemon_app(log_level="info")
            assert logging.getLogger("httpx").level != logging.DEBUG
        finally:
            logging.getLogger("httpx").setLevel(original_level)


class TestLogLevelRuntimeSwitch:
    def test_post_admin_log_level_changes_root_logger(self):
        original_level = logging.getLogger().level
        try:
            app = create_daemon_app()
            client = TestClient(app)
            resp = client.post("/admin/log-level", json={"level": "debug"})
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
            assert logging.getLogger().level == logging.DEBUG
        finally:
            logging.getLogger().setLevel(original_level)


class TestTarballStructure:
    def test_makefile_has_dist_target(self):
        makefile_path = REPO_ROOT / "Makefile"
        content = makefile_path.read_text()
        assert "dist:" in content or "dist " in content.split("\n")[0] or any(
            line.startswith("dist:") for line in content.split("\n")
        )

    def test_systemd_unit_has_security_hardening(self):
        service_path = REPO_ROOT / "dist" / "general-ludd.service"
        if not service_path.exists():
            pytest.skip("dist/general-ludd.service not generated")
        content = service_path.read_text()
        assert "NoNewPrivileges=true" in content
        assert "ProtectSystem=strict" in content
        assert "PrivateTmp=true" in content

    def test_install_sh_references_gludd_binary(self):
        install_path = REPO_ROOT / "dist" / "install.sh"
        if not install_path.exists():
            pytest.skip("dist/install.sh not generated")
        content = install_path.read_text()
        assert "gludd" in content


class TestReadmeUpdated:
    def test_readme_references_gludd_daemon(self):
        readme_path = REPO_ROOT / "README.md"
        content = readme_path.read_text()
        assert "gludd daemon" in content

    def test_readme_does_not_reference_gludd_worker(self):
        readme_path = REPO_ROOT / "README.md"
        content = readme_path.read_text()
        assert "gludd-worker" not in content

    def test_readme_does_not_reference_gludd_loop(self):
        readme_path = REPO_ROOT / "README.md"
        content = readme_path.read_text()
        assert "gludd-loop" not in content


class TestDeprecatedCLIsDeleted:
    def test_worker_cli_does_not_exist(self):
        path = REPO_ROOT / "src" / "general_ludd" / "worker" / "cli.py"
        assert not path.exists(), f"Deprecated file should not exist: {path}"

    def test_event_loop_cli_does_not_exist(self):
        path = REPO_ROOT / "src" / "general_ludd" / "event_loop" / "cli.py"
        assert not path.exists(), f"Deprecated file should not exist: {path}"


class TestContainerEntrypoint:
    def test_containerfile_entrypoint_is_gludd_daemon(self):
        containerfile_path = REPO_ROOT / "Containerfile"
        content = containerfile_path.read_text()
        assert 'ENTRYPOINT ["gludd", "daemon"]' in content

    def test_containerfile_does_not_reference_gludd_worker(self):
        containerfile_path = REPO_ROOT / "Containerfile"
        content = containerfile_path.read_text()
        assert "gludd-worker" not in content


class TestPyprojectDeclaresAnsibleRunner:
    def test_pyproject_depends_on_ansible_runner(self):
        """ansible-runner is a declared dependency.

        It powers the subprocess backend for process_isolation (finding #1
        real fix). The prior guard asserted it was absent because the
        in-process PlaybookExecutor was the only backend; now that we delegate
        to ansible-runner for container confinement, it MUST be declared.
        """
        pyproject_path = REPO_ROOT / "pyproject.toml"
        content = pyproject_path.read_text()
        assert "ansible-runner" in content


class TestPyprojectSingleEntrypoint:
    def test_project_scripts_has_only_gludd(self):
        pyproject_path = REPO_ROOT / "pyproject.toml"
        content = pyproject_path.read_text()
        assert "gludd = " in content
        assert "gludd-worker" not in content
        assert "gludd-loop" not in content

    def test_project_scripts_section_exists(self):
        import tomllib

        pyproject_path = REPO_ROOT / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        scripts = data.get("project", {}).get("scripts", {})
        assert list(scripts.keys()) == ["gludd"]
