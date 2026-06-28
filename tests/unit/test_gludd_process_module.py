"""Unit test for the gludd_process Ansible module.

Loads the real shipped module via importlib, drives main() with a mocked
AnsibleModule and a mocked GluddClient (NO real HTTP is performed), and
asserts:

  - action=list returns the managed-process registry under
    ansible_facts.gludd_process (changed=False).
  - action=status requires a positive pid and returns the psutil snapshot.
  - action=signal in check mode does NOT POST and reports the would-send note.
  - action=signal with a disallowed signal name fails before any request.
  - action=signal (live) POSTs and reports changed=True.
  - Daemon error responses (404/409/400) map to fail_json with the message.

Every network boundary is mocked.
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
    / "plugins" / "modules" / "gludd_process.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gludd_process", str(MODULE_PATH))
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
    """Stand-in for GluddClient that records calls and replays canned responses."""

    def __init__(self, get_resp: dict[str, Any] | None = None,
                 post_resp: dict[str, Any] | None = None) -> None:
        self._get_resp = get_resp or {}
        self._post_resp = post_resp or {}
        self.get_calls: list[tuple[str, Any]] = []
        self.post_calls: list[tuple[str, Any]] = []

    def get(self, path: str, params: Any = None) -> dict[str, Any]:
        self.get_calls.append((path, params))
        return dict(self._get_resp)

    def post(self, path: str, body: Any = None) -> dict[str, Any]:
        self.post_calls.append((path, body))
        return dict(self._post_resp)


def _default_params(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "action": "list",
        "pid": 0,
        "signal": "SIGTERM",
        "group": False,
        "daemon_url": "http://localhost:8000",
        "psk": "",
        "timeout": 10,
    }
    params.update(overrides)
    return params


@pytest.fixture
def module() -> ModuleType:
    return _load_module()


def _install(module: ModuleType, monkeypatch: pytest.MonkeyPatch,
             fake_mod: _FakeAnsibleModule, fake_client: _FakeClient) -> None:
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "GluddClient", lambda **_: fake_client)


# --------------------------------------------------------------------------
# action=list
# --------------------------------------------------------------------------

def test_list_returns_facts(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params(action="list"))
    fake_client = _FakeClient(
        get_resp={
            "processes": [
                {"pid": 1, "command": "init", "alive": True},
                {"pid": 42, "command": "python3", "alive": True},
            ],
            "count": 2,
            "_status": 200,
        }
    )
    _install(module, monkeypatch, fake_mod, fake_client)

    module.main()

    assert fake_mod.failed is None
    assert fake_mod.exited is not None
    assert fake_client.post_calls == []
    assert fake_client.get_calls == [("/admin/processes", None)]
    facts = fake_mod.exited["ansible_facts"]["gludd_process"]
    assert facts["count"] == 2
    assert facts["processes"][1]["pid"] == 42
    assert fake_mod.exited["changed"] is False


def test_list_daemon_error_fails(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params(action="list"))
    fake_client = _FakeClient(get_resp={"_error": "Connection refused", "_status": 0})
    _install(module, monkeypatch, fake_mod, fake_client)

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert "Connection refused" in fake_mod.failed["msg"]


# --------------------------------------------------------------------------
# action=status
# --------------------------------------------------------------------------

def test_status_requires_pid(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params(action="status", pid=0))
    fake_client = _FakeClient()
    _install(module, monkeypatch, fake_mod, fake_client)

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert "pid" in fake_mod.failed["msg"]
    # No HTTP call should have been made.
    assert fake_client.get_calls == []


def test_status_returns_stats(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params(action="status", pid=4242))
    fake_client = _FakeClient(
        get_resp={"cpu_percent": 1.5, "memory_rss": 1024, "_status": 200}
    )
    _install(module, monkeypatch, fake_mod, fake_client)

    module.main()

    assert fake_mod.failed is None
    assert fake_mod.exited is not None
    assert fake_client.get_calls == [("/admin/processes/4242/stats", None)]
    stats = fake_mod.exited["ansible_facts"]["gludd_process"]["stats"]
    assert stats["cpu_percent"] == 1.5
    # Internal transport keys must be stripped from the facts.
    assert "_status" not in stats
    assert fake_mod.exited["pid"] == 4242
    assert fake_mod.exited["changed"] is False


def test_status_404_maps_to_fail(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params(action="status", pid=9999))
    fake_client = _FakeClient(
        get_resp={"detail": "process 9999 not found", "_status": 404}
    )
    _install(module, monkeypatch, fake_mod, fake_client)

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert "not found" in fake_mod.failed["msg"]
    assert "404" in fake_mod.failed["msg"]


# --------------------------------------------------------------------------
# action=signal — check mode does not POST
# --------------------------------------------------------------------------

def test_signal_check_mode_does_not_post(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(
        _default_params(action="signal", pid=4242, signal="SIGTERM"),
        check_mode=True,
    )
    fake_client = _FakeClient()
    _install(module, monkeypatch, fake_mod, fake_client)

    module.main()

    assert fake_mod.failed is None
    assert fake_mod.exited is not None
    # No POST in check mode.
    assert fake_client.post_calls == []
    assert fake_mod.exited["changed"] is True
    assert "would send SIGTERM" in fake_mod.exited["note"]
    assert "4242" in fake_mod.exited["note"]


def test_signal_check_mode_group_note(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(
        _default_params(action="signal", pid=4242, signal="SIGKILL", group=True),
        check_mode=True,
    )
    fake_client = _FakeClient()
    _install(module, monkeypatch, fake_mod, fake_client)

    module.main()

    assert fake_client.post_calls == []
    assert fake_mod.exited is not None
    assert "process group of 4242" in fake_mod.exited["note"]


# --------------------------------------------------------------------------
# action=signal — disallowed signal name fails before any request
# --------------------------------------------------------------------------

def test_signal_disallowed_name_fails(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(
        _default_params(action="signal", pid=4242, signal="SIGEVIL")
    )
    fake_client = _FakeClient()
    _install(module, monkeypatch, fake_mod, fake_client)

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert "SIGEVIL" in fake_mod.failed["msg"]
    assert "not permitted" in fake_mod.failed["msg"]
    # Defence-in-depth: rejected without contacting the daemon.
    assert fake_client.post_calls == []


def test_signal_requires_pid(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(_default_params(action="signal", pid=0))
    fake_client = _FakeClient()
    _install(module, monkeypatch, fake_mod, fake_client)

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert "pid" in fake_mod.failed["msg"]
    assert fake_client.post_calls == []


# --------------------------------------------------------------------------
# action=signal — live delivery POSTs and reports changed
# --------------------------------------------------------------------------

def test_signal_live_posts_and_changes(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(
        _default_params(action="signal", pid=4242, signal="SIGTERM", group=True)
    )
    fake_client = _FakeClient(
        post_resp={"ok": True, "pid": 4242, "signal": "SIGTERM",
                   "group": True, "_status": 200}
    )
    _install(module, monkeypatch, fake_mod, fake_client)

    module.main()

    assert fake_mod.failed is None
    assert fake_mod.exited is not None
    assert fake_client.post_calls == [
        ("/admin/processes/4242/signal", {"signal": "SIGTERM", "group": True})
    ]
    assert fake_mod.exited["changed"] is True
    assert fake_mod.exited["pid"] == 4242
    assert fake_mod.exited["signal"] == "SIGTERM"


def test_signal_409_maps_to_fail(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(
        _default_params(action="signal", pid=4242, signal="SIGTERM")
    )
    fake_client = _FakeClient(
        post_resp={"detail": "process already exited", "_status": 409}
    )
    _install(module, monkeypatch, fake_mod, fake_client)

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert "already exited" in fake_mod.failed["msg"]
    assert "409" in fake_mod.failed["msg"]


# --------------------------------------------------------------------------
# allow-list constant
# --------------------------------------------------------------------------

def test_allowed_signals_set(module: ModuleType) -> None:
    assert {
        "SIGTERM", "SIGINT", "SIGHUP", "SIGQUIT",
        "SIGUSR1", "SIGUSR2", "SIGKILL", "SIGSTOP", "SIGCONT",
    } <= module.ALLOWED_SIGNALS
    assert "SIGEVIL" not in module.ALLOWED_SIGNALS
