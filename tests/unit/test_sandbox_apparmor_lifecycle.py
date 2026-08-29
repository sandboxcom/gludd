"""Lifecycle and fail-closed contracts for the Linux AppArmor backend."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from general_ludd.security.sandboxes import (
    Capability,
    PermissionSpec,
    SandboxHandle,
    SandboxTarget,
)
from general_ludd.security.sandboxes import linux_apparmor as apparmor


def _spec(*, denied: list[Capability] | None = None) -> PermissionSpec:
    return PermissionSpec(agent_type="worker", capabilities=[], denied=denied or [])


def test_available_requires_both_tools_and_healthy_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0),
    )
    assert apparmor.AppArmorBackend.available() is True

    monkeypatch.setattr(shutil, "which", lambda name: None if name == "aa-status" else "/bin/tool")
    assert apparmor.AppArmorBackend.available() is False


def test_available_handles_status_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

    def unavailable(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise OSError("status unavailable")

    monkeypatch.setattr(subprocess, "run", unavailable)

    assert apparmor.AppArmorBackend.available() is False


def test_apply_writes_and_loads_owned_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=b"")

    monkeypatch.setattr(apparmor, "PROFILE_DIR", tmp_path)
    monkeypatch.setattr(subprocess, "run", run)

    handle = apparmor.AppArmorBackend.apply(_spec(), SandboxTarget())

    assert handle.applied is True
    assert (tmp_path / "gludd-worker").is_file()
    assert calls == [["apparmor_parser", "-r", str(tmp_path / "gludd-worker")]]


def test_apply_failure_returns_advisory_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.CalledProcessError(1, ["apparmor_parser"])

    monkeypatch.setattr(apparmor, "PROFILE_DIR", tmp_path)
    monkeypatch.setattr(subprocess, "run", failed)

    handle = apparmor.AppArmorBackend.apply(_spec(), SandboxTarget())

    assert handle.applied is False
    assert "error" in handle.extra


def test_verify_reports_loaded_deny_rule_and_advisory_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denied = Capability(resource="file:/private/", actions=["read"])
    spec = _spec(denied=[denied])
    (tmp_path / "gludd-worker").write_text(apparmor.render_profile(spec, SandboxTarget()))
    monkeypatch.setattr(apparmor, "PROFILE_DIR", tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [],
            0,
            stdout=b'{"profiles":{"gludd-worker":{}}}',
        ),
    )
    handle = SandboxHandle(backend="apparmor", token="gludd-worker", applied=False)

    findings = apparmor.AppArmorBackend.verify(spec, handle)

    assert [finding.severity for finding in findings] == ["ok", "ok", "warn"]


def test_verify_fails_closed_for_unreadable_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout=b"not-json"),
    )
    handle = SandboxHandle(backend="apparmor", token="gludd-worker", applied=True)

    findings = apparmor.AppArmorBackend.verify(_spec(), handle)

    assert findings[0].severity == "fail"
    assert "aa-status unreadable" in findings[0].message


def test_release_removes_only_applied_profile_and_contains_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        raise OSError("parser unavailable")

    monkeypatch.setattr(apparmor, "PROFILE_DIR", tmp_path)
    monkeypatch.setattr(subprocess, "run", run)
    handle = SandboxHandle(backend="apparmor", token="gludd-worker", applied=True)

    apparmor.AppArmorBackend.release(handle)

    assert calls == [["apparmor_parser", "-R", str(tmp_path / "gludd-worker")]]
