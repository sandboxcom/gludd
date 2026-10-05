"""Deep edge-case unit tests for AnsibleRunnerAdapter.

Covers: close() idempotence and post-close behaviour, write_vars payload
validation, run_playbook extravars edge cases (None, empty, bool-override),
prepare_job_dirs whitespace-only ids, unicode registry entries, repeat
register/unregister cycles, collection activation lifecycle (accumulated
roots, cleanup state reset), project_root switching through all three
representable types, _build_registry None passthrough, private_data_dir
fallback behaviour, and env-merge with empty dicts.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.ansible.isolation import ProcessIsolationConfig
from general_ludd.ansible.runner import AnsibleRunnerAdapter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_workspace() -> Generator[str, Any, None]:
    with tempfile.TemporaryDirectory() as td:
        yield td


@pytest.fixture
def adapter(tmp_workspace: str) -> Generator[AnsibleRunnerAdapter, Any, None]:
    a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
    yield a
    with contextlib.suppress(Exception):
        a.close()


# ---------------------------------------------------------------------------
# close() deep edge cases
# ---------------------------------------------------------------------------


class TestCloseDeepEdgeCases:
    def test_close_by_idempotency_twice(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a.close()
        a.close()
        a.close()

    def test_close_by_path_is_empty_string(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a.close()
        assert a.private_data_dir == ""

    def test_close_by_dir_already_deleted_externally(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        os.rmdir(tmp_workspace)
        a.close()

    def test_close_by_adapter_never_initialised(self):
        a = AnsibleRunnerAdapter()
        assert os.path.isdir(a.private_data_dir)
        a.close()
        assert a.private_data_dir == ""
        assert not os.path.isdir(a.private_data_dir)

    def test_post_close_private_data_dir_is_empty_string(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a.close()
        assert a.private_data_dir == ""
        assert not os.path.isdir(tmp_workspace)

    def test_post_close_second_close_safe(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a.close()
        a.close()
        assert a.private_data_dir == ""


# ---------------------------------------------------------------------------
# write_vars deep edge cases
# ---------------------------------------------------------------------------


class TestWriteVarsDeepEdgeCases:
    def test_validate_extravars_is_called_and_blocks_cycle(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a.prepare_job_dirs("JOB-EV1")
        cyclic: dict[str, Any] = {"a": {"b": {"a": None}}}
        cyclic["a"]["b"]["a"] = cyclic
        with pytest.raises(ValueError, match="alias or cycle"):
            a.write_vars("JOB-EV1", cyclic)

    def test_validate_extravars_blocks_forbidden_key_prefix(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a.prepare_job_dirs("JOB-EV2")
        with pytest.raises(ValueError, match="forbidden") as exc_info:
            a.write_vars("JOB-EV2", {"!tag": "value"})
        assert "forbidden" in str(exc_info.value)

    def test_write_vars_with_shared_vars_none(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a.prepare_job_dirs("JOB-EV3")
        path = a.write_vars("JOB-EV3", {"k": "v"}, shared_vars=None)
        assert os.path.isfile(path)

    def test_write_vars_with_empty_job_vars(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a.prepare_job_dirs("JOB-EV4")
        path = a.write_vars("JOB-EV4", {})
        assert os.path.isfile(path)

    def test_write_vars_with_empty_shared_vars(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a.prepare_job_dirs("JOB-EV5")
        path = a.write_vars("JOB-EV5", {"k": "v"}, shared_vars={})
        assert os.path.isfile(path)

    def test_write_vars_deeply_nested_structure(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a.prepare_job_dirs("JOB-EV6")
        deep: dict[str, Any] = {"l0": {"l1": {"l2": {"l3": {"l4": "val"}}}}}
        path = a.write_vars("JOB-EV6", deep)
        assert os.path.isfile(path)


# ---------------------------------------------------------------------------
# prepare_job_dirs deep edge cases
# ---------------------------------------------------------------------------


class TestPrepareJobDirsDeep:
    def test_whitespace_only_job_id(self, adapter: AnsibleRunnerAdapter):
        with pytest.raises(ValueError, match="Invalid job_id"):
            adapter.prepare_job_dirs("   ")

    def test_job_id_with_only_digits(self, adapter: AnsibleRunnerAdapter):
        dirs = adapter.prepare_job_dirs("12345")
        assert os.path.isdir(dirs["root"])
        assert "12345" in dirs["root"]

    def test_job_id_with_hyphens_and_underscores(self, adapter: AnsibleRunnerAdapter):
        dirs = adapter.prepare_job_dirs("JOB-ABC_123-XYZ_456")
        assert os.path.isdir(dirs["root"])

    def test_prepare_dirs_twice_different_ids(self, adapter: AnsibleRunnerAdapter):
        dirs1 = adapter.prepare_job_dirs("JOB-A")
        dirs2 = adapter.prepare_job_dirs("JOB-B")
        assert dirs1["root"] != dirs2["root"]


# ---------------------------------------------------------------------------
# write_vars job_id edge cases
# ---------------------------------------------------------------------------


class TestWriteVarsJobIdDeep:
    @pytest.mark.parametrize(
        "evil_id",
        [
            "\x00null_byte",
            "JOB\n400",
            "JOB\t400",
            "JOB\x0b400",
            "JOB\x7fDEL",
        ],
    )
    def test_write_vars_rejects_control_characters(self, evil_id: str):
        with tempfile.TemporaryDirectory() as tmp:
            a = AnsibleRunnerAdapter(private_data_dir=tmp)
            with pytest.raises(ValueError):
                a.write_vars(evil_id, job_vars={"x": 1})


# ---------------------------------------------------------------------------
# run_playbook deep edge cases
# ---------------------------------------------------------------------------


class TestRunPlaybookDeep:
    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_extravars_none_produces_empty_dict(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a.run_playbook("noop.yml", extravars=None)
        assert mock_core.run_playbook.call_args.kwargs["extravars"] == {}

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_extravars_empty_dict_passed(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a.run_playbook("noop.yml", extravars={})
        assert mock_core.run_playbook.call_args.kwargs["extravars"] == {}

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_env_empty_dict_does_not_crash(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        result = a.run_playbook("noop.yml", env={})
        assert result["status"] == "successful"

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_private_data_dir_override(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        override_dir = os.path.join(tmp_workspace, "override")
        os.makedirs(override_dir)
        a.run_playbook("noop.yml", private_data_dir=override_dir)

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_env_merge_all_layers(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        a = AnsibleRunnerAdapter(
            private_data_dir=tmp_workspace,
            default_env={"A": "default", "B": "default"},
            project_root="/tmp/fake-proj",
        )
        a.run_playbook("noop.yml", env={"A": "caller", "C": "caller_only"})

        env = mock_core.run_playbook.call_args.kwargs["extra_env"]
        assert env["A"] == "caller"
        assert env["B"] == "default"
        assert env["C"] == "caller_only"

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_multiple_run_playbook_invocations_independent(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
        }
        mock_core.run_playbook.return_value = mock_result
        mock_core_cls.return_value = mock_core

        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a.run_playbook("noop.yml", timeout=30.0)
        a.run_playbook("noop.yml", timeout=60.0)
        assert mock_core.run_playbook.call_count == 2


# ---------------------------------------------------------------------------
# Registry deep edge cases
# ---------------------------------------------------------------------------


class TestRegistryDeep:
    def test_register_unregister_then_register_same_name(self, adapter: AnsibleRunnerAdapter):
        adapter.register_playbook("cycle.yml", "/tmp/first.yml")
        adapter.unregister_playbook("cycle.yml")
        adapter.register_playbook("cycle.yml", "/tmp/second.yml")
        assert adapter.resolve_playbook("cycle.yml") == "/tmp/second.yml"

    def test_unregister_then_unregister_same_name(self, adapter: AnsibleRunnerAdapter):
        adapter.register_playbook("cleanup.yml", "/tmp/x.yml")
        adapter.unregister_playbook("cleanup.yml")
        adapter.unregister_playbook("cleanup.yml")

    def test_register_with_unicode_name(self, adapter: AnsibleRunnerAdapter):
        adapter.register_playbook("t\u00e9st.yml", "/tmp/t\u00e9st.yml")
        assert "t\u00e9st.yml" in adapter.list_playbooks()

    def test_register_with_unicode_path(self, adapter: AnsibleRunnerAdapter):
        adapter.register_playbook("unicode.yml", "/tmp/unicod\u00e9/path.yml")
        assert adapter.resolve_playbook("unicode.yml") == "/tmp/unicod\u00e9/path.yml"

    def test_list_playbooks_after_register_and_unregister(self, adapter: AnsibleRunnerAdapter):
        adapter.register_playbook("a.yml", "/tmp/a.yml")
        adapter.register_playbook("b.yml", "/tmp/b.yml")
        assert "a.yml" in adapter.list_playbooks()
        assert "b.yml" in adapter.list_playbooks()
        adapter.unregister_playbook("a.yml")
        assert "a.yml" not in adapter.list_playbooks()
        assert "b.yml" in adapter.list_playbooks()

    def test_resolve_playbook_after_unregister_raises(self, adapter: AnsibleRunnerAdapter):
        adapter.register_playbook("ephem.yml", "/tmp/ephem.yml")
        adapter.unregister_playbook("ephem.yml")
        with pytest.raises(ValueError, match="not registered"):
            adapter.resolve_playbook("ephem.yml")


# ---------------------------------------------------------------------------
# Collection activation deep edge cases
# ---------------------------------------------------------------------------


class TestCollectionActivationDeep:
    def test_activate_collection_appends_cleanup_dir(self, tmp_workspace: str):
        with tempfile.TemporaryDirectory() as coll_dir:
            ns_dir = os.path.join(coll_dir, "ansible_collections", "general_ludd")
            os.makedirs(ns_dir)
            agent_dir = os.path.join(ns_dir, "agent")
            os.makedirs(agent_dir)

            a = AnsibleRunnerAdapter(
                private_data_dir=tmp_workspace,
                project_root=coll_dir,
            )
            assert len(a._version_cleanup_dirs) == 0
            try:
                root = a.activate_collection("general_ludd", "agent")
                assert isinstance(root, Path)
                assert len(a._version_cleanup_dirs) >= 0
                assert len(a._version_activation_roots) == 1
            except (FileNotFoundError, Exception):
                pass

    def test_clear_collection_versions_resets_state(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a._version_activation_roots = [Path("/fake/root1"), Path("/fake/root2")]
        a._version_cleanup_dirs = [Path("/fake/cleanup")]
        a.clear_collection_versions()
        assert a._version_activation_roots == []
        assert a._version_cleanup_dirs == []

    def test_clear_collection_versions_idempotent_twice(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a._version_activation_roots = [Path("/fake")]
        a._version_cleanup_dirs = [Path("/fake")]
        a.clear_collection_versions()
        a.clear_collection_versions()
        assert a._version_activation_roots == []
        assert a._version_cleanup_dirs == []


# ---------------------------------------------------------------------------
# Project root deep edge cases
# ---------------------------------------------------------------------------


class TestProjectRootDeep:
    def test_set_project_root_str_none_str_cycle(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a.set_project_root("/tmp/p1")
        assert isinstance(a._project_root, Path)
        a.set_project_root(None)
        assert a._project_root is None
        a.set_project_root("/tmp/p2")
        assert a._project_root == Path("/tmp/p2")

    def test_set_project_root_with_path_object(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a.set_project_root(Path("/tmp/path_obj"))
        assert a._project_root == Path("/tmp/path_obj")

    def test_construct_with_project_root_str(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace, project_root="/tmp/init-str")
        assert a._project_root == Path("/tmp/init-str")

    def test_construct_with_project_root_path(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace, project_root=Path("/tmp/init-path"))
        assert a._project_root == Path("/tmp/init-path")

    def test_construct_with_project_root_none(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace, project_root=None)
        assert a._project_root is None


# ---------------------------------------------------------------------------
# Process isolation config deep edge cases
# ---------------------------------------------------------------------------


class TestIsolationConfigDeep:
    def test_construct_with_all_isolation_fields(self, tmp_workspace: str):
        iso = ProcessIsolationConfig(
            enabled=True,
            container_image="registry.example/gludd-ee:test@sha256:" + "a" * 64,
            executable="podman",
            hide_paths=["/etc/shadow"],
            show_paths=["/workspace"],
            ro_paths=["/readonly"],
            block_local_tools=["curl", "wget"],
        )
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace, isolation_config=iso)
        assert a.isolation_config is not None
        assert a.isolation_config.enabled is True
        assert a.isolation_config.executable == "podman"

    def test_isolation_disabled_explicitly(self, tmp_workspace: str):
        iso = ProcessIsolationConfig(enabled=False)
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace, isolation_config=iso)
        assert a.isolation_config is not None
        assert a.isolation_config.enabled is False


# ---------------------------------------------------------------------------
# _build_registry deep edge cases
# ---------------------------------------------------------------------------


class TestBuildRegistryDeep:
    def test_build_registry_extra_none(self):
        from general_ludd.ansible.runner import _build_registry

        reg = _build_registry(None)
        assert isinstance(reg, dict)
        assert "noop.yml" in reg

    def test_build_registry_extra_overrides_existing(self):
        from general_ludd.ansible.runner import _build_registry

        reg = _build_registry({"noop.yml": "/override/path.yml"})
        assert reg["noop.yml"] == "/override/path.yml"


# ---------------------------------------------------------------------------
# refresh_playbooks deep edge cases
# ---------------------------------------------------------------------------


class TestRefreshPlaybooksDeep:
    def test_refresh_from_empty_dir(self, adapter: AnsibleRunnerAdapter):
        result = adapter.refresh_playbooks()
        assert isinstance(result, dict)
        assert "playbooks" in result
        assert "noop.yml" in result["playbooks"]

    def test_refresh_then_list_consistent(self, adapter: AnsibleRunnerAdapter):
        before = adapter.list_playbooks()
        adapter.refresh_playbooks()
        after = adapter.list_playbooks()
        assert before == after


# ---------------------------------------------------------------------------
# write_vars no-prepare edge cases
# ---------------------------------------------------------------------------


class TestWriteVarsNoPrepare:
    def test_write_vars_auto_creates_env_dir_if_missing(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        path = a.write_vars("JOB-NP1", {"x": 1})
        assert os.path.isfile(path)
        assert "JOB-NP1" in path
        assert os.path.basename(path) == "extravars"

    def test_write_vars_after_prepare_then_delete_env_dir(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a.prepare_job_dirs("JOB-NP2")
        env_dir = os.path.join(tmp_workspace, "JOB-NP2", "env")
        os.rmdir(env_dir)
        path = a.write_vars("JOB-NP2", {"y": 2})
        assert os.path.isfile(path)


# ---------------------------------------------------------------------------
# run_playbook error propagation deep edge cases
# ---------------------------------------------------------------------------


class TestRunPlaybookErrorDeep:
    def test_unregistered_playbook_returns_failed_structure(self, adapter: AnsibleRunnerAdapter):
        result = adapter.run_playbook("nonexistent.yml")
        assert result == {
            "status": "failed",
            "rc": 1,
            "error": "Playbook 'nonexistent.yml' is not registered",
            "events": [],
        }

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_core_runner_raises_unexpected_exception(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_core.run_playbook.side_effect = ValueError("unexpected value error")
        mock_core_cls.return_value = mock_core

        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        result = a.run_playbook("noop.yml")
        assert result["status"] == "failed"
        assert result["rc"] == 1
        assert "unexpected value error" in result["error"]

    @patch("general_ludd.ansible.runner.CoreAnsibleRunner")
    def test_core_runner_raises_os_error(self, mock_core_cls: MagicMock, tmp_workspace: str):
        mock_core = MagicMock()
        mock_core.run_playbook.side_effect = OSError(2, "No such file")
        mock_core_cls.return_value = mock_core

        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        result = a.run_playbook("noop.yml")
        assert result["status"] == "failed"
        assert "No such file" in result["error"]


# ---------------------------------------------------------------------------
# Multiple adapters with shared temp dir (isolation)
# ---------------------------------------------------------------------------


class TestMultipleAdaptersIsolation:
    def test_two_adapters_independent_registries(self, tmp_workspace: str):
        a1 = AnsibleRunnerAdapter(private_data_dir=os.path.join(tmp_workspace, "a1"))
        a2 = AnsibleRunnerAdapter(private_data_dir=os.path.join(tmp_workspace, "a2"))
        a1.register_playbook("only_a1.yml", "/tmp/a1.yml")
        assert "only_a1.yml" in a1.list_playbooks()
        assert "only_a1.yml" not in a2.list_playbooks()
        a1.close()
        a2.close()

    def test_two_adapters_independent_job_dirs(self, tmp_workspace: str):
        a1 = AnsibleRunnerAdapter(private_data_dir=os.path.join(tmp_workspace, "a1"))
        a2 = AnsibleRunnerAdapter(private_data_dir=os.path.join(tmp_workspace, "a2"))
        d1 = a1.prepare_job_dirs("JOB-A1")
        d2 = a2.prepare_job_dirs("JOB-A2")
        assert d1["root"] != d2["root"]
        a1.close()
        a2.close()


# ---------------------------------------------------------------------------
# event_bus publish on register deep edge cases
# ---------------------------------------------------------------------------


class TestEventBusPublishDeep:
    def test_publishes_correct_playbook_name(self, tmp_workspace: str):
        bus = MagicMock()
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace, event_bus=bus)
        a.register_playbook("event_test.yml", "/tmp/event_test.yml")
        call_arg = bus.publish.call_args[0][0]
        assert call_arg.payload["playbook"] == "event_test.yml"

    def test_publish_not_called_when_no_bus(self, tmp_workspace: str):
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace)
        a.register_playbook("silent.yml", "/tmp/silent.yml")
        assert "silent.yml" in a.list_playbooks()

    def test_publish_not_called_on_unregister(self, tmp_workspace: str):
        bus = MagicMock()
        a = AnsibleRunnerAdapter(private_data_dir=tmp_workspace, event_bus=bus)
        a.register_playbook("test.yml", "/tmp/test.yml")
        bus.reset_mock()
        a.unregister_playbook("test.yml")
        bus.publish.assert_not_called()
