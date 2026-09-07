"""Unit tests for Ansible runner adapter."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import yaml

from general_ludd.ansible.isolation import ProcessIsolationConfig
from general_ludd.ansible.runner import AnsibleRunnerAdapter


class TestRunnerAdapterRegistry:
    def test_default_registry_contains_noop(self) -> None:
        adapter = AnsibleRunnerAdapter()
        assert "noop.yml" in adapter.registry
        assert "bootstrap_execution_environment.yml" in adapter.registry

    def test_registry_maps_to_filesystem_paths(self) -> None:
        adapter = AnsibleRunnerAdapter()
        path = adapter.registry["noop.yml"]
        assert path.endswith("playbooks/noop.yml")

    def test_registry_rejects_unregistered_playbook(self) -> None:
        adapter = AnsibleRunnerAdapter()
        with pytest.raises(ValueError, match="not registered"):
            adapter.resolve_playbook("evil.yml")

    def test_resolve_playbook_returns_path(self) -> None:
        adapter = AnsibleRunnerAdapter()
        path = adapter.resolve_playbook("noop.yml")
        assert "noop.yml" in path


class TestRunnerPrepareDirs:
    def test_prepare_dirs_creates_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            dirs = adapter.prepare_job_dirs("JOB-100")
            for key in ("root", "env", "project", "inventory", "artifacts"):
                assert key in dirs
                assert os.path.isdir(dirs[key])

    def test_prepare_dirs_root_contains_job_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            dirs = adapter.prepare_job_dirs("JOB-200")
            assert "JOB-200" in dirs["root"]


class TestRunnerWriteVars:
    def test_write_vars_creates_yaml_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            adapter.prepare_job_dirs("JOB-300")
            path = adapter.write_vars(
                "JOB-300",
                job_vars={"job_id": "JOB-300", "target": "db"},
                shared_vars={"env": "staging"},
            )
            assert os.path.isfile(path)
            with open(path) as f:
                content = yaml.safe_load(f)
            assert content["job_vars"]["job_id"] == "JOB-300"
            assert content["shared_vars"]["env"] == "staging"

    def test_write_vars_extravars_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            adapter.prepare_job_dirs("JOB-301")
            path = adapter.write_vars(
                "JOB-301",
                job_vars={"x": 1},
                shared_vars=None,
                filename="extravars",
            )
            assert os.path.basename(path) == "extravars"

    @pytest.mark.parametrize(
        "evil_id",
        ["../../etc", "..", "a/b", "a\\b", "JOB 1", "lower", "with.dot", ""],
    )
    def test_write_vars_rejects_unsafe_job_id(self, evil_id: str) -> None:
        # write_vars is reachable independently of prepare_job_dirs, and job_id
        # is attacker-controllable (JobSpec.job_id). A non-[A-Z0-9_-] id must be
        # refused before any path is built — no traversal out of the workspace.
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            with pytest.raises(ValueError):
                adapter.write_vars(evil_id, job_vars={"x": 1})
            # Nothing was written outside the (empty) workspace.
            assert os.listdir(tmp) == []


class TestRunnerRunPlaybook:
    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_run_playbook_calls_core_runner(self, mock_core_cls: MagicMock) -> None:
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [{}],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            result = adapter.run_playbook(
                playbook_name="noop.yml",
                private_data_dir=tmp,
            )
        mock_core.run_playbook.assert_called_once()
        assert result["status"] == "successful"
        assert result["rc"] == 0

    def test_run_playbook_rejects_unregistered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            result = adapter.run_playbook(playbook_name="evil.yml")
        assert result["status"] == "failed"
        assert "not registered" in result["error"]

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_run_playbook_captures_events(self, mock_core_cls: MagicMock) -> None:
        events = [
            {"event": "playbook_on_start"},
            {"event": "runner_on_ok", "event_data": {"task": "debug"}},
        ]
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": events,
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            result = adapter.run_playbook(
                playbook_name="noop.yml",
                private_data_dir=tmp,
            )
        assert len(result["events"]) == 2


class TestExecutionEnvironmentBootstrap:
    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_reconcile_bootstraps_outside_then_activates_verified_image(
        self,
        core_runner_cls: MagicMock,
    ) -> None:
        configured_runner = MagicMock()
        bootstrap_runner = MagicMock()
        core_runner_cls.side_effect = [configured_runner, bootstrap_runner]
        bootstrap_result = MagicMock()
        bootstrap_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [
                {
                    "event": "runner_on_ok",
                    "result": {
                        "ansible_facts": {
                            "gludd_execution_environment": {
                                "state": "present",
                                "owner": "gludd",
                                "image": "localhost/gludd-ee-test:locked",
                                "image_id": "a" * 64,
                                "verified": True,
                            }
                        }
                    },
                }
            ],
        }
        bootstrap_runner.run_playbook.return_value = bootstrap_result
        isolation = ProcessIsolationConfig(
            enabled=True,
            executable="podman",
            container_image=f"registry.example/gludd@sha256:{'b' * 64}",
        )

        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(
                private_data_dir=tmp,
                project_root=tmp,
                isolation_config=isolation,
            )
            result = adapter.reconcile_execution_environment()

        configured_runner.run_playbook.assert_not_called()
        bootstrap_runner.run_playbook.assert_called_once()
        configured_runner.set_process_isolation.assert_called_once()
        activated = configured_runner.set_process_isolation.call_args.args[0]
        assert activated.enabled is True
        assert activated.container_image == f"sha256:{'a' * 64}"
        assert result["execution_environment"]["verified"] is True

    def test_reconcile_uses_bounded_playbook_contract(self) -> None:
        event_bus = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(
                private_data_dir=tmp,
                project_root=tmp,
                event_bus=event_bus,
            )
            with patch.object(
                adapter,
                "_run_resolved_playbook",
                return_value={
                    "status": "successful",
                    "rc": 0,
                    "events": [
                        {
                            "result": {
                                "ansible_facts": {
                                    "gludd_execution_environment": {
                                        "state": "present",
                                        "owner": "gludd",
                                        "image_id": "c" * 64,
                                        "verified": True,
                                    }
                                }
                            }
                        }
                    ],
                },
            ) as run_bootstrap:
                result = adapter.reconcile_execution_environment(
                    constraints={
                        "machine_cpus": 4,
                        "machine_memory_mb": 8192,
                        "machine_disk_gb": 24,
                        "ephemeral": True,
                    }
                )

        assert result["status"] == "successful"
        run_bootstrap.assert_called_once()
        invocation = run_bootstrap.call_args.kwargs
        assert invocation["playbook_path"].endswith(
            "playbooks/bootstrap_execution_environment.yml"
        )
        assert invocation["extravars"] == {
                "execution_environment_bootstrap_state": "present",
                "execution_environment_bootstrap_project_root": tmp,
                "execution_environment_bootstrap_machine_cpus": 4,
                "execution_environment_bootstrap_machine_memory_mb": 8192,
                "execution_environment_bootstrap_machine_disk_gb": 24,
                "execution_environment_bootstrap_ephemeral": True,
            }
        assert invocation["timeout"] == 7800.0
        assert invocation["env"] is None
        published_names = [call.args[0].name for call in event_bus.publish.call_args_list]
        assert published_names == [
            "execution_environment_reconcile_started",
            "execution_environment_reconcile_completed",
        ]

    def test_reconcile_absent_uses_shorter_timeout_and_explicit_root(self) -> None:
        with (
            tempfile.TemporaryDirectory() as private_data_dir,
            tempfile.TemporaryDirectory() as project_root,
        ):
            adapter = AnsibleRunnerAdapter(private_data_dir=private_data_dir)
            with patch.object(
                adapter,
                "_run_resolved_playbook",
                return_value={
                    "status": "successful",
                    "rc": 0,
                    "events": [
                        {
                            "result": {
                                "ansible_facts": {
                                    "gludd_execution_environment": {
                                        "state": "absent",
                                        "owner": "gludd",
                                        "verified": False,
                                    }
                                }
                            }
                        }
                    ],
                },
            ) as run_bootstrap:
                adapter.reconcile_execution_environment(
                    state="absent",
                    project_root=project_root,
                )

        run_bootstrap.assert_called_once()
        invocation = run_bootstrap.call_args.kwargs
        assert invocation["playbook_path"].endswith(
            "playbooks/bootstrap_execution_environment.yml"
        )
        assert invocation["extravars"] == {
                "execution_environment_bootstrap_state": "absent",
                "execution_environment_bootstrap_project_root": project_root,
            }
        assert invocation["timeout"] == 1200.0
        assert invocation["env"] is None

    @pytest.mark.parametrize("state", ["", "installed", "destroy", "PRESENT"])
    def test_reconcile_rejects_unknown_state(self, state: str) -> None:
        adapter = AnsibleRunnerAdapter()
        with pytest.raises(ValueError, match="state must be one of"):
            adapter.reconcile_execution_environment(state=state)

    def test_reconcile_rejects_unknown_or_prefixed_constraint(self) -> None:
        adapter = AnsibleRunnerAdapter()
        with pytest.raises(ValueError, match="Unsupported execution-environment constraint"):
            adapter.reconcile_execution_environment(constraints={"arbitrary_var": True})
        with pytest.raises(ValueError, match="Unsupported execution-environment constraint"):
            adapter.reconcile_execution_environment(
                constraints={"execution_environment_bootstrap_state": "absent"}
            )

    def test_reconcile_emits_failure_without_secret_values(self) -> None:
        event_bus = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp, event_bus=event_bus)
            with patch.object(
                adapter,
                "_run_resolved_playbook",
                return_value={
                    "status": "failed",
                    "rc": 1,
                    "error": "credential=do-not-publish",
                    "events": [],
                },
            ):
                result = adapter.reconcile_execution_environment(
                    project_root=tmp,
                    constraints={"validate_only": True},
                    timeout=60,
                )

        assert result["status"] == "failed"
        terminal_event = event_bus.publish.call_args_list[-1].args[0]
        assert terminal_event.name == "execution_environment_reconcile_failed"
        assert terminal_event.payload == {
            "name": "execution_environment_reconcile_failed",
            "state": "present",
            "rc": 1,
        }
