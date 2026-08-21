"""Release-contract tests for the beta4 Python runtime boundary."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

from general_ludd.ansible.isolation import ProcessIsolationConfig

ROOT = Path(__file__).resolve().parents[2]
SHA256_IMAGE = "registry.example/gludd-ee:beta4@sha256:" + "a" * 64
PYTHON_31113_SLIM_INDEX = (
    "docker.io/library/python:3.11.13-slim@sha256:"
    "9bffe4353b925a1656688797ebc68f9c525e79b1d377a764d232182a519eeec4"
)


def _project() -> dict[str, Any]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_core_dependencies_exclude_ansible_controller() -> None:
    project = _project()
    runtime = project["project"]["dependencies"]
    names = {str(item).split("[", 1)[0].split("<", 1)[0].split(">", 1)[0] for item in runtime}
    assert "ansible-core" not in names
    assert "ansible-runner" not in names


def test_ansible_controller_is_optional_and_available_to_tests() -> None:
    project = _project()
    optional = project["project"]["optional-dependencies"]
    controller = "\n".join(optional["ansible-controller"])
    dev_extra = "\n".join(optional["dev"])
    dev_group = "\n".join(project["dependency-groups"]["dev"])
    for dependency in ("ansible-core", "ansible-runner"):
        assert dependency in controller
        assert dependency in dev_extra
        assert dependency in dev_group
    assert "ansible-builder>=3.1.1,<3.2" in dev_extra
    assert "ansible-builder>=3.1.1,<3.2" in dev_group


def test_core_cli_imports_when_ansible_is_unavailable() -> None:
    script = f"""
import importlib.abc
import sys
class BlockAnsible(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'ansible' or fullname.startswith('ansible.') or fullname == 'ansible_runner':
            raise ModuleNotFoundError(fullname)
        return None
sys.meta_path.insert(0, BlockAnsible())
sys.path.insert(0, {str(ROOT / 'src')!r})
import general_ludd.cli
print('CORE_IMPORT_OK')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "CORE_IMPORT_OK"


def test_frozen_core_excludes_ansible_payload() -> None:
    spec = (ROOT / "gludd.spec").read_text(encoding="utf-8")
    assert "collect_data_files('ansible')" not in spec
    assert "collect_submodules(" not in spec
    assert "('collections', 'collections')" not in spec
    assert "('playbooks', 'playbooks')" not in spec
    assert re.search(r"['\"]ansible['\"]", spec)
    assert re.search(r"['\"]ansible_runner['\"]", spec)


def test_execution_environment_definition_uses_locked_inputs() -> None:
    ee = yaml.safe_load((ROOT / "config/ansible/execution-environment.yml").read_text(encoding="utf-8"))
    assert ee["version"] == 3
    assert re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", ee["images"]["base_image"]["name"])
    assert ee["dependencies"] == {
        "galaxy": "requirements.yml",
        "python": "requirements.txt",
        "system": "bindep.txt",
    }


def test_execution_environment_uses_published_beta4_base_index() -> None:
    """The EE must reference Docker Hub's published multi-platform index."""
    ee = yaml.safe_load((ROOT / "config/ansible/execution-environment.yml").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "config/ansible/runtime-lock.json").read_text(encoding="utf-8"))
    assert ee["images"]["base_image"]["name"] == PYTHON_31113_SLIM_INDEX
    assert lock["base_image"] == PYTHON_31113_SLIM_INDEX


def test_runtime_manifests_are_versioned_and_content_addressed() -> None:
    lock = json.loads((ROOT / "config/ansible/runtime-lock.json").read_text(encoding="utf-8"))
    managed = json.loads((ROOT / "config/ansible/managed-host-python.lock.json").read_text(encoding="utf-8"))
    assert lock["schema_version"] == 1
    assert lock["release"] == "0.1.0-beta.4"
    assert re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", lock["base_image"])
    assert set(lock["inputs"]) == {"galaxy", "python", "system", "definition"}
    assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in lock["inputs"].values())
    assert managed["schema_version"] == 1
    assert managed["interpreter_variable"] == "ansible_python_interpreter"
    assert managed["ambient_interpreters_allowed"] is False
    assert managed["requirements"] == []


def test_enabled_container_isolation_rejects_unpinned_images() -> None:
    for value in (None, "gludd-ee:latest", "gludd-ee@sha256:short"):
        with pytest.raises(ValidationError, match="digest-pinned"):
            ProcessIsolationConfig(enabled=True, container_image=value)


def test_enabled_container_isolation_passes_digest_to_runner() -> None:
    config = ProcessIsolationConfig(enabled=True, container_image=SHA256_IMAGE)
    kwargs = config.to_runner_kwargs()
    assert kwargs["container_image"] == SHA256_IMAGE
    assert kwargs["process_isolation"] is True


def test_in_process_controller_requires_explicit_test_mode() -> None:
    config = ProcessIsolationConfig(test_only_in_process=True)
    assert config.enabled is False
    assert config.test_only_in_process is True
    with pytest.raises(ValidationError, match="test-only"):
        ProcessIsolationConfig(enabled=True, container_image=SHA256_IMAGE, test_only_in_process=True)


def test_game_module_reuses_authenticated_stdlib_model_client() -> None:
    from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import GluddClient

    client = GluddClient(base_url="http://127.0.0.1:8765", psk="secret", timeout=11)
    with patch.object(client, "post", return_value={"_status": 200, "text": "game"}) as post:
        first = client.call_model("one", model_profile="local.test", max_tokens=32)
        second = client.call_model("two", max_tokens=16)
        third = client.call_model("three", route_task_type="code_generation")
    assert first["text"] == "game"
    assert second["text"] == "game"
    assert third["text"] == "game"
    assert post.call_count == 3
    assert post.call_args_list[2].args[1]["route_task_type"] == "code_generation"
    assert "task_type" not in post.call_args_list[2].args[1]
    assert client._headers()["Authorization"] == "Bearer secret"


def test_collection_model_transport_has_no_core_gateway_fallback() -> None:
    shim = (
        ROOT
        / "collections/ansible_collections/general_ludd/agent/plugins/module_utils/gludd.py"
    ).read_text(encoding="utf-8")
    module = (
        ROOT
        / "collections/ansible_collections/general_ludd/agent/plugins/modules/game_build.py"
    ).read_text(encoding="utf-8")
    assert "general_ludd.models" not in shim
    assert "local_model_call" not in shim
    assert "local_model_call" not in module
    assert "client.call_model(" in module
    assert 'no_log=True' in module
