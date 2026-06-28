"""Unit test for the gludd_proc_monitor Ansible module.

Loads the real shipped module via importlib, drives main() with a mocked
AnsibleModule + a mocked GluddClient (NO real network), and asserts:

  - A single pid fetches that process's stats and injects one stats dict under
    ansible_facts.gludd_proc_monitor.
  - pid==0 enumerates /admin/processes and aggregates stats for each alive pid.
  - A per-pid 404 (process exited mid-scan) is skipped, not fatal.
  - A transport error -> fail_json.
  - Check mode makes no daemon call and returns an empty fact set.

Every network boundary is mocked through a fake GluddClient.
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
    / "plugins" / "modules" / "gludd_proc_monitor.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gludd_proc_monitor", str(MODULE_PATH))
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


class _FakeClient:
    """Stand-in for GluddClient — serves canned responses keyed by GET path."""

    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    def __call__(self, *_a: Any, **_k: Any) -> _FakeClient:
        # Allow being used directly as the GluddClient constructor stand-in.
        return self

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(path)
        if path in self._responses:
            return dict(self._responses[path])
        return {"_status": 404, "detail": "not found"}


def _default_params(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "pid": 0,
        "daemon_url": "http://localhost:8000",
        "psk": "",
        "timeout": 10,
    }
    params.update(overrides)
    return params


def _stats(pid: int, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "_status": 200,
        "pid": pid,
        "cpu_percent": 1.5,
        "memory": {"rss": 1024, "vms": 4096},
        "io": {
            "read_bytes": 10,
            "write_bytes": 20,
            "read_count": 1,
            "write_count": 2,
        },
        "num_fds": 12,
        "num_threads": 3,
        "num_ctx_switches": 7,
        "status": "running",
        "open_files": [],
        "locks": [],
    }
    base.update(extra)
    return base


@pytest.fixture
def module() -> ModuleType:
    return _load_module()


# --------------------------------------------------------------------------
# Single pid -> one stats dict in facts
# --------------------------------------------------------------------------

def test_single_pid_returns_one_stats_dict(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params(pid=4242))
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)

    client = _FakeClient({"/admin/processes/4242/stats": _stats(4242)})
    monkeypatch.setattr(module, "GluddClient", client)

    module.main()

    assert fake_mod.failed is None
    assert fake_mod.exited is not None
    facts = fake_mod.exited["ansible_facts"]["gludd_proc_monitor"]
    assert facts["count"] == 1
    assert len(facts["processes"]) == 1
    stats = facts["processes"][0]
    assert stats["pid"] == 4242
    assert stats["memory"]["rss"] == 1024
    # Internal transport keys are stripped from the surfaced stats.
    assert "_status" not in stats
    assert fake_mod.exited["changed"] is False
    # Only the single-pid stats endpoint was called.
    assert client.calls == ["/admin/processes/4242/stats"]


# --------------------------------------------------------------------------
# pid==0 -> aggregate stats for all alive managed processes
# --------------------------------------------------------------------------

def test_all_pids_aggregates_multiple(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params(pid=0))
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)

    client = _FakeClient(
        {
            "/admin/processes": {
                "_status": 200,
                "processes": [
                    {"pid": 1, "command": "init", "alive": True},
                    {"pid": 2, "command": "worker", "alive": True},
                    {"pid": 3, "command": "dead", "alive": False},
                ],
                "count": 3,
            },
            "/admin/processes/1/stats": _stats(1),
            "/admin/processes/2/stats": _stats(2),
            "/admin/processes/3/stats": _stats(3),
        }
    )
    monkeypatch.setattr(module, "GluddClient", client)

    module.main()

    assert fake_mod.failed is None
    assert fake_mod.exited is not None
    facts = fake_mod.exited["ansible_facts"]["gludd_proc_monitor"]
    # Only the two alive processes are collected (pid 3 is not alive -> skipped).
    assert facts["count"] == 2
    pids = sorted(p["pid"] for p in facts["processes"])
    assert pids == [1, 2]
    # The dead process's stats endpoint must never be called.
    assert "/admin/processes/3/stats" not in client.calls
    assert client.calls[0] == "/admin/processes"


# --------------------------------------------------------------------------
# pid==0 -> a per-pid 404 (exited mid-scan) is skipped, not fatal
# --------------------------------------------------------------------------

def test_per_pid_404_is_skipped_not_fatal(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params(pid=0))
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)

    client = _FakeClient(
        {
            "/admin/processes": {
                "_status": 200,
                "processes": [
                    {"pid": 1, "command": "init", "alive": True},
                    {"pid": 2, "command": "gone", "alive": True},
                ],
                "count": 2,
            },
            "/admin/processes/1/stats": _stats(1),
            # pid 2 exited mid-scan -> 404
            "/admin/processes/2/stats": {"_status": 404, "detail": "no such process"},
        }
    )
    monkeypatch.setattr(module, "GluddClient", client)

    module.main()

    assert fake_mod.failed is None
    assert fake_mod.exited is not None
    facts = fake_mod.exited["ansible_facts"]["gludd_proc_monitor"]
    assert facts["count"] == 1
    assert facts["processes"][0]["pid"] == 1


# --------------------------------------------------------------------------
# Transport error -> fail_json
# --------------------------------------------------------------------------

def test_transport_error_fails_json_single_pid(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params(pid=99))
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)

    client = _FakeClient(
        {
            "/admin/processes/99/stats": {
                "_error": "Connection refused",
                "_status": 0,
                "_raw": "",
            }
        }
    )
    monkeypatch.setattr(module, "GluddClient", client)

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert fake_mod.failed["failed"] is True
    assert "Connection refused" in fake_mod.failed["msg"]


def test_transport_error_fails_json_on_listing(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params(pid=0))
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)

    client = _FakeClient(
        {
            "/admin/processes": {
                "_error": "daemon unreachable",
                "_status": 0,
                "_raw": "",
            }
        }
    )
    monkeypatch.setattr(module, "GluddClient", client)

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert "daemon unreachable" in fake_mod.failed["msg"]


# --------------------------------------------------------------------------
# Non-2xx (non-404) on a stats call -> fail_json
# --------------------------------------------------------------------------

def test_non_2xx_status_fails_json(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params(pid=7))
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)

    client = _FakeClient(
        {
            "/admin/processes/7/stats": {
                "_status": 500,
                "detail": "internal error",
            }
        }
    )
    monkeypatch.setattr(module, "GluddClient", client)

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert "500" in fake_mod.failed["msg"]


# --------------------------------------------------------------------------
# Check mode -> no daemon call, empty fact set
# --------------------------------------------------------------------------

def test_check_mode_makes_no_call_returns_empty(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params(pid=0), check_mode=True)
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)

    client = _FakeClient({})

    def _boom(*_a: Any, **_k: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("GluddClient must not be constructed in check mode")

    monkeypatch.setattr(module, "GluddClient", _boom)

    module.main()

    assert fake_mod.failed is None
    assert fake_mod.exited is not None
    facts = fake_mod.exited["ansible_facts"]["gludd_proc_monitor"]
    assert facts["count"] == 0
    assert facts["processes"] == []
    assert fake_mod.exited["changed"] is False
    assert client.calls == []
