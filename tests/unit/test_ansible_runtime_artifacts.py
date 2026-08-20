"""Unit tests for the beta4 Ansible runtime artifact tooling."""

from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml
from scripts import ansible_runtime_artifacts as artifacts

PINNED_IMAGE = "registry.example/gludd-ee:beta4@sha256:" + "b" * 64


def test_tracked_runtime_artifacts_validate() -> None:
    assert artifacts.validate_files() == []


def test_validate_reports_missing_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing.yml"
    monkeypatch.setattr(artifacts, "INPUTS", {"definition": missing})
    assert artifacts.validate_files() == [f"missing runtime artifact: {missing}"]


def test_validate_reports_dependency_leaks_and_missing_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_project = {
        "project": {
            "dependencies": ["ansible-core==2", "ansible-runner==2", "ansible-builder==3"],
            "optional-dependencies": {"ansible-controller": []},
        }
    }
    monkeypatch.setattr(tomllib, "loads", lambda _text: fake_project)
    errors = artifacts.validate_files()
    assert {error for error in errors if "dependency" in error} == {
        "core dependency leak: ansible-core",
        "core dependency leak: ansible-runner",
        "core dependency leak: ansible-builder",
        "missing optional controller dependency: ansible-core",
        "missing optional controller dependency: ansible-runner",
    }


def test_validate_reports_definition_lock_and_managed_host_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "runtime-lock.json"
    managed = tmp_path / "managed.json"
    lock.write_text(
        json.dumps(
            {
                "schema_version": 9,
                "release": "old",
                "base_image": "different",
                "inputs": {},
            }
        ),
        encoding="utf-8",
    )
    managed.write_text(
        json.dumps(
            {
                "ambient_interpreters_allowed": True,
                "interpreter_variable": "python",
                "requirements": "ambient",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(artifacts, "LOCK", lock)
    monkeypatch.setattr(artifacts, "MANAGED", managed)
    monkeypatch.setattr(
        yaml,
        "safe_load",
        lambda _text: {"version": 2, "images": {"base_image": {"name": "latest"}}, "dependencies": {}},
    )
    errors = artifacts.validate_files()
    assert "execution environment base image is not digest-pinned" in errors
    assert "execution environment definition must use schema version 3" in errors
    assert "execution environment dependencies must name the three locked inputs" in errors
    assert "runtime lock schema/release mismatch" in errors
    assert "runtime lock base image differs from execution environment definition" in errors
    assert "runtime lock input hashes are stale; run make update-ansible-runtime-lock" in errors
    assert "managed-host manifest must reject ambient interpreters" in errors
    assert "managed-host manifest must select ansible_python_interpreter" in errors
    assert "managed-host requirements must be an explicit list" in errors


def test_expected_hashes_are_named_and_content_addressed() -> None:
    hashes = artifacts.expected_input_hashes()
    assert set(hashes) == {"galaxy", "python", "system", "definition"}
    assert all(value.startswith("sha256:") and len(value) == 71 for value in hashes.values())


def test_write_lock_refreshes_only_input_hashes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = tmp_path / "runtime-lock.json"
    lock.write_text('{"schema_version": 1, "release": "0.1.0-beta.4", "inputs": {}}\n', encoding="utf-8")
    monkeypatch.setattr(artifacts, "LOCK", lock)
    monkeypatch.setattr(artifacts, "expected_input_hashes", lambda: {"definition": "sha256:" + "c" * 64})
    artifacts.write_lock()
    data = json.loads(lock.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["inputs"] == {"definition": "sha256:" + "c" * 64}


def test_dependency_name_parser_handles_markers_extras_and_bounds() -> None:
    assert artifacts._dependency_names(
        ["ansible-core>=2.19,<2.20; python_version < '3.12'", "package[extra]==1.0"]
    ) == {"ansible-core", "package"}


def test_build_validate_only_never_invokes_external_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    monkeypatch.setattr(shutil, "which", lambda _name: pytest.fail("which must not run"))
    assert artifacts.build_environment("podman", "gludd-ee:beta4", tmp_path / "context", True) == 0


def test_build_fails_before_mutation_when_artifacts_are_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: ["stale lock"])
    assert artifacts.build_environment("podman", "gludd-ee:beta4", tmp_path / "context", False) == 1
    assert "stale lock" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("available", "expected", "message"),
    [
        ({"podman"}, 2, "ansible-builder is unavailable"),
        ({"ansible-builder"}, 2, "container runtime is unavailable"),
    ],
)
def test_build_reports_missing_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    available: set[str],
    expected: int,
    message: str,
) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}" if name in available else None)
    assert artifacts.build_environment("podman", "gludd-ee:beta4", tmp_path / "context", False) == expected
    assert message in capsys.readouterr().err


def test_build_streams_ansible_builder_with_bounded_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], Path | None]] = []
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")

    def fake_run(command: list[str], *, cwd: Path | None = None, check: bool = False) -> SimpleNamespace:
        assert check is False
        calls.append((command, cwd))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    context = tmp_path / "gludd-ee"
    assert artifacts.build_environment("podman", "gludd-ee:beta4", context, False) == 0
    assert calls[0][0][:2] == ["ansible-builder", "build"]
    assert calls[0][1] == artifacts.ROOT
    assert context.is_dir()


@pytest.mark.parametrize("image", ["latest", "repo@sha256:short", "repo@sha256:" + "G" * 64])
def test_verify_rejects_non_digest_image(image: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert artifacts.verify_environment("podman", image, True) == 2
    assert "digest-pinned" in capsys.readouterr().err


def test_verify_validate_only_accepts_digest_without_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    assert artifacts.verify_environment("podman", PINNED_IMAGE, True) == 0


def test_verify_fails_closed_on_invalid_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: ["bad managed manifest"])
    assert artifacts.verify_environment("podman", PINNED_IMAGE, True) == 1


def test_verify_reports_missing_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert artifacts.verify_environment("podman", PINNED_IMAGE, False) == 2


def test_verify_stops_when_image_inspect_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=7))
    assert artifacts.verify_environment("podman", PINNED_IMAGE, False) == 7


def test_verify_inspects_and_smokes_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")

    def fake_run(command: list[str], *, check: bool = False) -> SimpleNamespace:
        assert check is False
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert artifacts.verify_environment("podman", PINNED_IMAGE, False) == 0
    assert calls[0] == ["podman", "image", "inspect", PINNED_IMAGE]
    assert "--network=none" in calls[1]
    assert "ansible_runner" in calls[1][-1]


def test_cli_validate_and_validate_only_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    assert artifacts.main(["validate"]) == 0
    assert artifacts.main(["build", "--validate-only", "--context", "/tmp/gludd-ee-test"]) == 0
    assert artifacts.main(["verify", "--validate-only", "--image", PINNED_IMAGE]) == 0


def test_cli_write_lock_and_validation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    write = MagicMock()
    monkeypatch.setattr(artifacts, "write_lock", write)
    assert artifacts.main(["write-lock"]) == 0
    write.assert_called_once_with()
    monkeypatch.setattr(artifacts, "validate_files", lambda: ["broken"])
    assert artifacts.main(["validate"]) == 1
