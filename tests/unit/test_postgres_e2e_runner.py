"""Unit tests for the namespaced live PostgreSQL E2E runner."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts import postgres_e2e_runner as runner


def test_namespace_is_stable_and_project_scoped(tmp_path: Path) -> None:
    assert runner.project_namespace(tmp_path) == runner.project_namespace(tmp_path)
    assert runner.project_namespace(tmp_path) != runner.project_namespace(tmp_path / "other")


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("127.0.0.1:55432\n", 55432),
        ("0.0.0.0:49152\n", 49152),
        ("[::1]:54321\n", 54321),
    ],
)
def test_parse_mapped_port(output: str, expected: int) -> None:
    assert runner.parse_mapped_port(output) == expected


def test_parse_mapped_port_rejects_invalid_output() -> None:
    with pytest.raises(ValueError, match="mapped PostgreSQL port"):
        runner.parse_mapped_port("not-a-port")


def test_validate_only_does_not_call_container_runtime(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    (tmp_path / "tests" / "e2e").mkdir(parents=True)
    (tmp_path / "tests" / "e2e" / "test_postgres_multiworker_live.py").write_text("")
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("container runtime must not run"),
    )

    assert runner.main(
        [
            "--runtime",
            "missing-runtime",
            "--image",
            "postgres:16-alpine",
            "--timeout-seconds",
            "1",
            "--validate-only",
        ]
    ) == 0


def test_live_runner_starts_waits_tests_and_cleans_up(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    (tmp_path / "tests" / "e2e").mkdir(parents=True)
    (tmp_path / "tests" / "e2e" / "test_postgres_multiworker_live.py").write_text("")
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "/usr/bin/podman")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        del kwargs
        calls.append(list(command))
        if "port" in command:
            return subprocess.CompletedProcess(command, 0, "127.0.0.1:49152\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    assert runner.main(
        [
            "--runtime",
            "podman",
            "--image",
            "postgres:16-alpine",
            "--timeout-seconds",
            "5",
        ]
    ) == 0
    assert calls[0][1] == "run"
    assert any(command[1] == "exec" for command in calls)
    assert any("pytest" in command for command in calls)
    assert calls[-1][1:3] == ["rm", "-f"]
