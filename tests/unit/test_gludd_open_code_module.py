"""Unit test for the gludd_open_code Ansible module.

Verifies the make-command construction — specifically that it uses
make -C to set the working directory rather than relying on
run_command's cwd parameter.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).parent.parent.parent
MODULE_PATH = (
    ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
    / "plugins" / "modules" / "gludd_open_code.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gludd_open_code", str(MODULE_PATH))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeAnsibleModule:
    def __init__(self, params: dict[str, Any], check_mode: bool = False) -> None:
        self.params = params
        self.check_mode = check_mode
        self.exited: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None
        self._run_command_calls: list[tuple] = []
        self._next_run_command_result: tuple[int, str, str] = (0, "ok", "")

    def exit_json(self, **kwargs: Any) -> None:
        self.exited = kwargs

    def fail_json(self, **kwargs: Any) -> None:
        self.failed = kwargs

    def run_command(
        self,
        args: str | list[str],
        cwd: str | None = None,
        environ_update: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        self._run_command_calls.append(
            (args, cwd, environ_update)
        )
        return self._next_run_command_result


class TestBuildCmd:
    """Unit tests for the _build_cmd helper that constructs make commands."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.mod = _load_module()

    def test_gate_check_default_path(self) -> None:
        cmd = self.mod._build_cmd("gate_check", {"repo_path": "."})
        assert "make" in cmd
        assert "-C" in cmd
        assert "gate-fast" in cmd

    def test_gate_check_explicit_path(self) -> None:
        cmd = self.mod._build_cmd("gate_check", {"repo_path": "/workspace/gludd"})
        assert cmd == "make -C /workspace/gludd gate-fast"

    def test_push_and_verify(self) -> None:
        cmd = self.mod._build_cmd("push_and_verify", {"repo_path": "."})
        assert "make" in cmd
        assert "-C" in cmd
        assert "ci-push-and-verify" in cmd

    def test_commit_ship_with_msg(self) -> None:
        cmd = self.mod._build_cmd(
            "commit_ship",
            {"repo_path": "/ws", "commit_msg": "feat: add x"},
        )
        assert cmd == 'make -C /ws ship MSG="feat: add x"'

    def test_commit_ship_without_msg(self) -> None:
        cmd = self.mod._build_cmd(
            "commit_ship",
            {"repo_path": "/ws", "commit_msg": ""},
        )
        assert cmd == "make -C /ws ship"

    def test_test_batch_with_files(self) -> None:
        cmd = self.mod._build_cmd(
            "test_batch",
            {
                "repo_path": ".",
                "test_files": ["tests/a.py", "tests/b.py"],
            },
        )
        assert cmd == 'make -C . test-batch TESTFILES="tests/a.py tests/b.py"'

    def test_test_batch_no_files(self) -> None:
        cmd = self.mod._build_cmd(
            "test_batch",
            {"repo_path": ".", "test_files": []},
        )
        assert cmd == "make -C . test-batch"

    def test_status_check(self) -> None:
        cmd = self.mod._build_cmd("status_check", {"repo_path": "."})
        assert cmd == "make -C . status-update"


class TestRunModule:
    """Integration-level tests that exercise run_module via injection."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.mod = _load_module()
        self._original_ansible_module = getattr(self.mod, "AnsibleModule", None)

    def teardown_method(self) -> None:
        if self._original_ansible_module is not None:
            self.mod.AnsibleModule = self._original_ansible_module

    def test_gate_check_success(self) -> None:
        fake = _FakeAnsibleModule(
            {"action": "gate_check", "repo_path": "/tmp/repo"},
        )
        fake._next_run_command_result = (0, "gate ok\n", "")
        self.mod.AnsibleModule = lambda *args, **kw: fake

        self.mod.run_module()

        assert fake.exited is not None
        assert fake.failed is None
        assert fake.exited["rc"] == 0
        assert fake.exited["action"] == "gate_check"
        assert fake.exited["cmd"] == "make -C /tmp/repo gate-fast"

    def test_gate_check_failure(self) -> None:
        fake = _FakeAnsibleModule(
            {"action": "gate_check", "repo_path": "/tmp/repo"},
        )
        fake._next_run_command_result = (2, "", "make: *** No rule...\n")
        self.mod.AnsibleModule = lambda *args, **kw: fake

        self.mod.run_module()

        assert fake.failed is not None
        assert fake.failed["rc"] == 2
        assert "failed (rc=2)" in fake.failed["msg"]

    def test_commit_ship_requires_msg(self) -> None:
        fake = _FakeAnsibleModule(
            {"action": "commit_ship", "repo_path": "."},
        )
        self.mod.AnsibleModule = lambda *args, **kw: fake

        self.mod.run_module()

        assert fake.failed is not None
        assert "commit_msg is required" in fake.failed["msg"]

    def test_test_batch_requires_files(self) -> None:
        fake = _FakeAnsibleModule(
            {"action": "test_batch", "repo_path": "."},
        )
        self.mod.AnsibleModule = lambda *args, **kw: fake

        self.mod.run_module()

        assert fake.failed is not None
        assert "test_files is required" in fake.failed["msg"]

    def test_env_passed_to_run_command(self) -> None:
        fake = _FakeAnsibleModule(
            {
                "action": "gate_check",
                "repo_path": "/repo",
                "branch": "feature/x",
                "ci_timeout": 300,
            },
        )
        fake._next_run_command_result = (0, "", "")
        self.mod.AnsibleModule = lambda *args, **kw: fake

        self.mod.run_module()

        assert len(fake._run_command_calls) == 1
        _cmd, cwd, env = fake._run_command_calls[0]
        assert cwd is None
        assert env is not None
        assert env["BRANCH"] == "feature/x"
