"""Unit test for the gludd_osquery Ansible module.

Loads the real shipped module via importlib, drives main() with a mocked
AnsibleModule + mocked subprocess (NO real osquery is downloaded or run), and
asserts:

  - SELECT-only validation rejects a DROP query (fail_json, binary never run).
  - A valid SELECT runs osqueryi via an arg-list subprocess (no shell=True),
    parses the JSON rows, and injects them under ansible_facts.gludd_osquery.
  - A missing binary fails clearly.
  - Timeout / parse errors fail clearly.

Every subprocess/network boundary is mocked.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).parent.parent.parent
MODULE_PATH = (
    ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
    / "plugins" / "modules" / "gludd_osquery.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gludd_osquery", str(MODULE_PATH))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeAnsibleModule:
    """Stand-in for ansible.module_utils.basic.AnsibleModule."""

    def __init__(self, params: dict[str, Any], check_mode: bool = False) -> None:
        self.params = params
        self.check_mode = check_mode
        self.exited: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None
        self.warnings: list[str] = []

    def exit_json(self, **kwargs: Any) -> None:
        self.exited = kwargs

    def fail_json(self, **kwargs: Any) -> None:
        self.failed = kwargs

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def _default_params(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "query": "SELECT pid, name FROM processes LIMIT 5",
        "timeout": 10,
        "osquery_path": "/fake/osqueryi",
        "daemon_url": "http://localhost:8000",
        "psk": "",
    }
    params.update(overrides)
    return params


@pytest.fixture
def module() -> ModuleType:
    return _load_module()


# --------------------------------------------------------------------------
# SELECT-only validation (pure function — no subprocess)
# --------------------------------------------------------------------------

class TestValidateSelectOnly:
    def test_accepts_plain_select(self, module: ModuleType) -> None:
        assert module.validate_select_only("SELECT * FROM processes") is None

    def test_accepts_lowercase_and_whitespace(self, module: ModuleType) -> None:
        assert module.validate_select_only("  select pid from users  ") is None

    def test_accepts_with_cte(self, module: ModuleType) -> None:
        q = "WITH p AS (SELECT pid FROM processes) SELECT * FROM p"
        assert module.validate_select_only(q) is None

    def test_accepts_trailing_semicolon(self, module: ModuleType) -> None:
        assert module.validate_select_only("SELECT 1;") is None

    @pytest.mark.parametrize(
        "bad",
        [
            "DROP TABLE processes",
            "DELETE FROM users",
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET x=1",
            "CREATE TABLE t (x int)",
            "ALTER TABLE t ADD c int",
            "ATTACH DATABASE 'x' AS y",
            "PRAGMA table_info(processes)",
        ],
    )
    def test_rejects_mutating(self, module: ModuleType, bad: str) -> None:
        assert module.validate_select_only(bad) is not None

    def test_rejects_drop_hidden_after_select(self, module: ModuleType) -> None:
        # Stacked statements must be refused even if the first is a SELECT.
        assert module.validate_select_only("SELECT 1; DROP TABLE t") is not None

    def test_rejects_keyword_in_comment(self, module: ModuleType) -> None:
        # A comment is stripped, so a SELECT with a benign DROP-mentioning
        # comment is still allowed (the keyword is gone after stripping).
        assert module.validate_select_only("SELECT 1 -- not a DROP here") is None

    def test_rejects_empty(self, module: ModuleType) -> None:
        assert module.validate_select_only("   ") is not None

    def test_rejects_non_select_statement(self, module: ModuleType) -> None:
        assert module.validate_select_only("EXPLAIN SELECT 1") is not None


# --------------------------------------------------------------------------
# main() — drop query is rejected before the binary runs
# --------------------------------------------------------------------------

def test_drop_query_rejected_and_binary_never_run(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params(query="DROP TABLE processes"))
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)

    ran = {"called": False}

    def _no_run(*_a: Any, **_k: Any) -> Any:  # pragma: no cover - must not run
        ran["called"] = True
        raise AssertionError("subprocess.run must NOT be called for a rejected query")

    monkeypatch.setattr(module.subprocess, "run", _no_run)

    module.main()

    assert ran["called"] is False
    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert "query rejected" in fake_mod.failed["msg"]


# --------------------------------------------------------------------------
# main() — valid SELECT runs osqueryi (mocked) and injects rows
# --------------------------------------------------------------------------

def test_valid_select_runs_and_injects_rows(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params())
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "resolve_osquery_binary", lambda *_a, **_k: "/fake/osqueryi")

    captured: dict[str, Any] = {}

    class _Completed:
        returncode = 0
        stdout = '[{"pid":"1","name":"init"},{"pid":"42","name":"python3"}]'
        stderr = ""

    def _fake_run(cmd: list[str], **kwargs: Any) -> _Completed:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _Completed()

    monkeypatch.setattr(module.subprocess, "run", _fake_run)

    module.main()

    assert fake_mod.failed is None
    assert fake_mod.exited is not None
    # arg-list subprocess, NO shell=True.
    assert captured["cmd"][0] == "/fake/osqueryi"
    assert captured["cmd"][1] == "--json"
    assert captured["cmd"][2] == "SELECT pid, name FROM processes LIMIT 5"
    assert captured["kwargs"].get("shell") in (None, False)
    assert "shell" not in captured["kwargs"] or captured["kwargs"]["shell"] is False

    facts = fake_mod.exited["ansible_facts"]["gludd_osquery"]
    assert facts["count"] == 2
    assert facts["rows"][0]["name"] == "init"
    assert facts["rows"][1]["pid"] == "42"
    assert "SELECT" in facts["query"]
    assert fake_mod.exited["changed"] is False


# --------------------------------------------------------------------------
# main() — missing binary fails clearly
# --------------------------------------------------------------------------

def test_missing_binary_fails_clearly(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No explicit path, filestore unavailable, nothing on PATH.
    fake_mod = _FakeAnsibleModule(_default_params(osquery_path=""))
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "resolve_osquery_binary", lambda *_a, **_k: None)

    def _no_run(*_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("subprocess.run must not run when binary is missing")

    monkeypatch.setattr(module.subprocess, "run", _no_run)

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert "osquery binary not found" in fake_mod.failed["msg"]


# --------------------------------------------------------------------------
# main() — timeout + bad JSON fail clearly
# --------------------------------------------------------------------------

def test_timeout_fails_clearly(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params())
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "resolve_osquery_binary", lambda *_a, **_k: "/fake/osqueryi")

    def _timeout(*_a: Any, **_k: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="osqueryi", timeout=10)

    monkeypatch.setattr(module.subprocess, "run", _timeout)

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert "timed out" in fake_mod.failed["msg"]


def test_bad_json_fails_clearly(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params())
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "resolve_osquery_binary", lambda *_a, **_k: "/fake/osqueryi")

    class _Completed:
        returncode = 0
        stdout = "not json at all"
        stderr = ""

    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_k: _Completed())

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert "parse" in fake_mod.failed["msg"].lower()


def test_nonzero_exit_fails_clearly(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params())
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "resolve_osquery_binary", lambda *_a, **_k: "/fake/osqueryi")

    class _Completed:
        returncode = 1
        stdout = ""
        stderr = "Error: no such table: bogus"

    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_k: _Completed())

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert "exited 1" in fake_mod.failed["msg"]


# --------------------------------------------------------------------------
# main() — check mode validates + locates but does not run
# --------------------------------------------------------------------------

def test_check_mode_does_not_run_binary(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params(), check_mode=True)
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "resolve_osquery_binary", lambda *_a, **_k: "/fake/osqueryi")

    def _no_run(*_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("subprocess.run must not run in check mode")

    monkeypatch.setattr(module.subprocess, "run", _no_run)

    module.main()

    assert fake_mod.failed is None
    assert fake_mod.exited is not None
    assert fake_mod.exited["changed"] is False
    assert fake_mod.exited["ansible_facts"]["gludd_osquery"]["count"] == 0


# --------------------------------------------------------------------------
# resolve_osquery_binary — explicit path + PATH fallback (mocked)
# --------------------------------------------------------------------------

class TestResolveBinary:
    def test_explicit_path_used_when_executable(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(module.os.path, "isfile", lambda p: True)
        monkeypatch.setattr(module.os, "access", lambda p, m: True)
        assert module.resolve_osquery_binary("/usr/bin/osqueryi") == "/usr/bin/osqueryi"

    def test_explicit_path_rejected_when_missing(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(module.os.path, "isfile", lambda p: False)
        assert module.resolve_osquery_binary("/nope/osqueryi") is None

    def test_path_fallback(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No explicit path; filestore import path raises; shutil.which finds it.
        monkeypatch.setattr(
            module.shutil, "which", lambda name: "/usr/local/bin/osqueryi"
        )
        # Force the filestore branch to miss (simulate get_binary_path -> None).
        import sys
        import types

        fake_pkg = types.ModuleType("general_ludd.filestore.bootstrap")

        class _Boot:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

            def get_binary_path(self, name: str) -> None:
                return None

        fake_pkg.__dict__["BinaryBootstrapper"] = _Boot
        monkeypatch.setitem(
            sys.modules, "general_ludd.filestore.bootstrap", fake_pkg
        )
        assert module.resolve_osquery_binary("") == "/usr/local/bin/osqueryi"

    def test_explicit_path_nonexecutable_returns_none(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # resolve_osquery_binary returns None for exists-but-not-executable;
        # main() is responsible for the precise error (FINDING 8).
        monkeypatch.setattr(module.os.path, "isfile", lambda p: True)
        monkeypatch.setattr(module.os, "access", lambda p, m: False)
        assert module.resolve_osquery_binary("/opt/osqueryi") is None


# --------------------------------------------------------------------------
# Managed-host boundary — controller filestore must never be imported
# --------------------------------------------------------------------------

def test_controller_filestore_is_not_probed_with_module(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Managed modules resolve target-host binaries without controller imports."""
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/osqueryi")

    class _MockModule:
        def __init__(self) -> None:
            self.warnings: list[str] = []

        def warn(self, msg: str) -> None:
            self.warnings.append(msg)

    mock_module = _MockModule()
    result = module.resolve_osquery_binary("", module=mock_module)

    assert result == "/usr/bin/osqueryi"
    assert mock_module.warnings == []
    assert "general_ludd.filestore" not in MODULE_PATH.read_text(encoding="utf-8")


def test_managed_path_fallback_is_silent_without_module(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The optional module argument does not alter managed-host PATH lookup."""
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/local/bin/osqueryi")

    result = module.resolve_osquery_binary("")
    assert result == "/usr/local/bin/osqueryi"


# --------------------------------------------------------------------------
# FINDING 8 — explicit path that exists but is non-executable: precise error
# --------------------------------------------------------------------------

def test_nonexecutable_explicit_path_fails_with_precise_message(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When osquery_path exists but is not executable, main() must fail_json
    with 'exists but not executable' and 'chmod +x <path>' — not the generic
    'binary not found' message."""
    fake_mod = _FakeAnsibleModule(_default_params(osquery_path="/opt/osqueryi"))
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)

    # File exists but is NOT executable.
    monkeypatch.setattr(module.os.path, "isfile", lambda p: True)
    monkeypatch.setattr(module.os, "access", lambda p, m: False)

    def _no_run(*_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("subprocess.run must not be called for a non-executable path")

    monkeypatch.setattr(module.subprocess, "run", _no_run)

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert "exists but is not executable" in fake_mod.failed["msg"]
    assert "chmod +x" in fake_mod.failed["msg"]
    assert "/opt/osqueryi" in fake_mod.failed["msg"]


# --------------------------------------------------------------------------
# FINDING 4 — daemon_url and psk non-default must warn (reserved params)
# --------------------------------------------------------------------------

def test_daemon_url_nondefault_warns(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-default daemon_url must emit a module.warn stating it is reserved
    and has no effect in this version."""
    fake_mod = _FakeAnsibleModule(
        _default_params(daemon_url="http://myhost:9000")
    )
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "resolve_osquery_binary", lambda *_a, **_k: "/fake/osqueryi")

    class _Completed:
        returncode = 0
        stdout = "[]"
        stderr = ""

    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_k: _Completed())

    module.main()

    assert fake_mod.failed is None
    assert any("daemon_url" in w for w in fake_mod.warnings), (
        f"Expected daemon_url warning; got: {fake_mod.warnings}"
    )
    assert any("reserved" in w for w in fake_mod.warnings)


def test_psk_nondefault_warns(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-empty psk must emit a module.warn stating it is reserved and has
    no effect in this version."""
    fake_mod = _FakeAnsibleModule(
        _default_params(psk="secret-token")
    )
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "resolve_osquery_binary", lambda *_a, **_k: "/fake/osqueryi")

    class _Completed:
        returncode = 0
        stdout = "[]"
        stderr = ""

    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_k: _Completed())

    module.main()

    assert fake_mod.failed is None
    assert any("psk" in w for w in fake_mod.warnings), (
        f"Expected psk warning; got: {fake_mod.warnings}"
    )
    assert any("reserved" in w for w in fake_mod.warnings)
