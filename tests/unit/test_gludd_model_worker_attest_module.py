"""Contracts for the content-free model-worker attestation Ansible module."""

from __future__ import annotations

import runpy
from pathlib import Path
from unittest.mock import patch

import pytest
from ansible_collections.general_ludd.agent.plugins.module_utils.model_worker_attestation import (
    ModelWorkerAttestor as CollectionModelWorkerAttestor,
)

from general_ludd.hardware.model_worker_attestation import (
    ModelWorkerAttestationResult,
)

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "plugins"
    / "modules"
    / "gludd_model_worker_attest.py"
)
MODULE_UTILS_PATH = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "plugins"
    / "module_utils"
    / "model_worker_attestation.py"
)
CORE_RUNTIME_PATH = ROOT / "src" / "general_ludd" / "hardware" / "model_worker_attestation.py"


class _Exit(Exception):
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload


class _Fail(Exception):
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload


def _params() -> dict[str, object]:
    return {
        "runner_id": "vllm-observed",
        "source_revision": "vllm-observed-v1",
        "backend": "nvidia",
        "minimum_device_count": 2,
        "minimum_memory_mib": 80_000,
        "required_interconnect": "nvlink",
        "expected_topology_digest": "a" * 64,
        "runtime_probe": ["vllm", "--version"],
        "expected_runtime_version_digest": "sha256:" + "b" * 64,
        "timeout_seconds": 15,
    }


def _result() -> ModelWorkerAttestationResult:
    return ModelWorkerAttestationResult(
        facts_attested=True,
        driver_ready=True,
        runtime_ready=True,
        topology_ready=True,
        device_count=2,
        source_revision="vllm-observed-v1",
        topology_digest="a" * 64,
        observed_inventory_digest="c" * 64,
        backend="nvidia",
        interconnect="nvlink",
        driver_version_digest="sha256:" + "d" * 64,
        runtime_version_digest="sha256:" + "b" * 64,
        reason_codes=(),
    )


def test_module_returns_only_the_core_attestation_fact() -> None:
    from ansible_collections.general_ludd.agent.plugins.modules import (
        gludd_model_worker_attest as subject,
    )

    captured: list[object] = []

    class FakeAnsibleModule:
        def __init__(self, **_kwargs: object) -> None:
            self.params = _params()
            self.check_mode = True

        def exit_json(self, **payload: object) -> None:
            raise _Exit(payload)

        def fail_json(self, **payload: object) -> None:
            raise _Fail(payload)

    class FakeAttestor:
        def attest(self, request: object) -> ModelWorkerAttestationResult:
            captured.append(request)
            return _result()

    with (
        patch.object(subject, "AnsibleModule", FakeAnsibleModule),
        patch.object(subject, "ModelWorkerAttestor", return_value=FakeAttestor()),
        pytest.raises(_Exit) as exited,
    ):
        subject.main()

    fact = exited.value.payload["ansible_facts"]["gludd_model_worker_attestation"]  # type: ignore[index]
    assert exited.value.payload["changed"] is False
    assert fact == _result().to_dict()
    request = captured[0]
    assert request.runner_id == "vllm-observed"  # type: ignore[attr-defined]
    assert request.runtime_probe == ("vllm", "--version")  # type: ignore[attr-defined]
    assert request.minimum_device_count == 2  # type: ignore[attr-defined]


def test_invalid_input_fails_without_echoing_probe_content() -> None:
    from ansible_collections.general_ludd.agent.plugins.modules import (
        gludd_model_worker_attest as subject,
    )

    class FakeAnsibleModule:
        def __init__(self, **_kwargs: object) -> None:
            self.params = _params() | {
                "backend": "BAD backend",
                "runtime_probe": ["private-runtime-value"],
            }
            self.check_mode = False

        def exit_json(self, **payload: object) -> None:
            raise _Exit(payload)

        def fail_json(self, **payload: object) -> None:
            raise _Fail(payload)

    with (
        patch.object(subject, "AnsibleModule", FakeAnsibleModule),
        pytest.raises(_Fail) as failed,
    ):
        subject.main()

    assert "model worker attestation failed" in str(failed.value.payload)
    assert "private-runtime-value" not in str(failed.value.payload)


def test_module_uses_no_shell_and_marks_runtime_probe_no_log() -> None:
    text = MODULE_PATH.read_text(encoding="utf-8")

    assert "ModelWorkerAttestationRequest" in text
    assert "ModelWorkerAttestor" in text
    assert "subprocess" not in text
    assert "shell" not in text.casefold()
    assert 'runtime_probe=dict(type="list", elements="str", required=True, no_log=True)' in text


def test_module_uses_collection_owned_managed_host_runtime() -> None:
    """Managed hosts must not depend on the controller's Gludd installation."""
    text = MODULE_PATH.read_text(encoding="utf-8")

    assert (
        "from ansible_collections.general_ludd.agent.plugins.module_utils."
        "model_worker_attestation import ("
    ) in text
    assert "from general_ludd.hardware.model_worker_attestation" not in text


def test_collection_runtime_is_an_exact_vendor_of_the_canonical_engine() -> None:
    """The transferred Ansible runtime must not drift from controller semantics."""
    assert MODULE_UTILS_PATH.read_bytes() == CORE_RUNTIME_PATH.read_bytes()


def test_real_attestor_remains_the_module_default() -> None:
    from ansible_collections.general_ludd.agent.plugins.modules import (
        gludd_model_worker_attest as subject,
    )

    assert subject.ModelWorkerAttestor is CollectionModelWorkerAttestor


def test_module_has_a_strict_focused_coverage_profile() -> None:
    profile = ROOT / "config" / "coverage_model_worker_attest.ini"

    text = profile.read_text(encoding="utf-8")
    assert "branch = True" in text
    assert "gludd_model_worker_attest.py" in text


def test_executable_entrypoint_runs_the_same_read_only_attestation() -> None:
    class FakeAnsibleModule:
        def __init__(self, **_kwargs: object) -> None:
            self.params = _params()

        def exit_json(self, **payload: object) -> None:
            raise _Exit(payload)

        def fail_json(self, **payload: object) -> None:
            raise _Fail(payload)

    class FakeAttestor:
        def attest(self, _request: object) -> ModelWorkerAttestationResult:
            return _result()

    with (
        patch("ansible.module_utils.basic.AnsibleModule", FakeAnsibleModule),
        patch(
            "ansible_collections.general_ludd.agent.plugins.module_utils."
            "model_worker_attestation.ModelWorkerAttestor",
            return_value=FakeAttestor(),
        ),
        pytest.raises(_Exit) as exited,
    ):
        runpy.run_path(str(MODULE_PATH), run_name="__main__")

    assert exited.value.payload["changed"] is False
