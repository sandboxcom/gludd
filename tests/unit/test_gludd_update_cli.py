"""Tests for scripts/gludd_update.py — the operator-facing self-update CLI.

Covers issue #81 part 3 (the operator surface):

* a *config* update request ("update gludd: ...") whose plan targets config/yaml
  emits a **high**-priority todo spec with ``needs_review=False`` (fast-track),
* a *code* update request emits a **low**-priority spec carrying the
  ``needs_review=True`` flag,
* a *role* update request emits a **medium**-priority spec, and
* standalone-WITHOUT-router prints the 'router unavailable' notice and exits
  non-zero **without raising**.

The (separately-owned) ``UpdateRequestRouter`` is replaced with a lightweight
stub via monkeypatching ``load_router`` — the CLI must never import the real
router at module load, so importing this test module must succeed even though
``general_ludd.self_update.router`` does not exist.
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
SCRIPT_PATH = ROOT / "scripts" / "gludd_update.py"


def _load_module():
    """Import gludd_update.py by path (it has no top-level router dependency)."""
    spec = importlib.util.spec_from_file_location("gludd_update", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("gludd_update", module)
    spec.loader.exec_module(module)
    return module


gludd_update = _load_module()


# ---------------------------------------------------------------------------
# Stub router + plans
# ---------------------------------------------------------------------------


class _StubPlan:
    """A stand-in for the real (separately-owned) UpdatePlan dataclass."""

    def __init__(self, *, subsystem, target_kind, target_paths, capability_required, risk):
        self.subsystem = subsystem
        self.target_kind = target_kind
        self.target_paths = target_paths
        self.capability_required = capability_required
        self.risk = risk


def _make_stub_router(plan):
    """Return a stub UpdateRequestRouter *class* whose route() yields ``plan``."""

    class _StubRouter:
        def __init__(self):
            self.calls = []

        def route(self, text):
            self.calls.append(text)
            return plan

    return _StubRouter


def _install_router(monkeypatch, plan):
    """Make the CLI's load_router() return a stub router for ``plan``."""
    monkeypatch.setattr(gludd_update, "load_router", lambda: _make_stub_router(plan))


# ---------------------------------------------------------------------------
# parse_request
# ---------------------------------------------------------------------------


class TestParseRequest:
    def test_strips_prefix_case_insensitively(self):
        assert gludd_update.parse_request("  Update Gludd:  add a knob ") == "add a knob"

    def test_missing_prefix_raises(self):
        with pytest.raises(gludd_update.RequestParseError):
            gludd_update.parse_request("please add a knob")

    def test_empty_body_raises(self):
        with pytest.raises(gludd_update.RequestParseError):
            gludd_update.parse_request("update gludd:   ")


# ---------------------------------------------------------------------------
# priority derivation
# ---------------------------------------------------------------------------


class TestDerivePriority:
    def test_config_is_high_no_review(self):
        plan = _StubPlan(
            subsystem="model_router",
            target_kind="config",
            target_paths=["config/ratchet.yml"],
            capability_required="config_write",
            risk="low",
        )
        priority, needs_review = gludd_update.derive_priority(plan)
        assert priority == gludd_update.PRIORITY_HIGH
        assert needs_review is False

    def test_yaml_is_high(self):
        plan = _StubPlan(
            subsystem="x", target_kind="yaml", target_paths=["a.yml"],
            capability_required=None, risk="low",
        )
        assert gludd_update.derive_priority(plan)[0] == gludd_update.PRIORITY_HIGH

    def test_role_is_medium_no_review(self):
        plan = _StubPlan(
            subsystem="agent_roles", target_kind="role",
            target_paths=["roles/foo"], capability_required="role_edit", risk="medium",
        )
        priority, needs_review = gludd_update.derive_priority(plan)
        assert priority == gludd_update.PRIORITY_MEDIUM
        assert needs_review is False

    def test_code_is_low_with_review(self):
        plan = _StubPlan(
            subsystem="execution", target_kind="code",
            target_paths=["src/general_ludd/x.py"], capability_required="code_write",
            risk="high",
        )
        priority, needs_review = gludd_update.derive_priority(plan)
        assert priority == gludd_update.PRIORITY_LOW
        assert needs_review is True

    def test_unknown_kind_is_treated_as_reviewable_code(self):
        plan = _StubPlan(
            subsystem="?", target_kind="something_new", target_paths=[],
            capability_required=None, risk=None,
        )
        priority, needs_review = gludd_update.derive_priority(plan)
        assert priority == gludd_update.PRIORITY_LOW
        assert needs_review is True


# ---------------------------------------------------------------------------
# end-to-end via run() with an injected todo_creator
# ---------------------------------------------------------------------------


class TestRunEmitsSpec:
    def test_config_request_emits_high_priority_spec(self, monkeypatch):
        plan = _StubPlan(
            subsystem="model_router",
            target_kind="config",
            target_paths=["config/ratchet.yml"],
            capability_required="config_write",
            risk="low",
        )
        _install_router(monkeypatch, plan)
        captured = {}
        rc = gludd_update.run(
            "update gludd: bump the ratchet max",
            todo_creator=lambda spec: captured.update(spec),
        )
        assert rc == 0
        assert captured["priority"] == "high"
        assert captured["needs_review"] is False
        assert captured["plan"]["target_kind"] == "config"
        assert captured["plan"]["target_paths"] == ["config/ratchet.yml"]
        assert captured["plan"]["subsystem"] == "model_router"
        assert captured["request_text"] == "bump the ratchet max"
        assert captured["title"].startswith("update gludd:")

    def test_code_request_emits_low_priority_with_review_flag(self, monkeypatch):
        plan = _StubPlan(
            subsystem="execution",
            target_kind="code",
            target_paths=["src/general_ludd/execution/engine.py"],
            capability_required="code_write",
            risk="high",
        )
        _install_router(monkeypatch, plan)
        captured = {}
        rc = gludd_update.run(
            "update gludd: add a retry to the executor",
            todo_creator=lambda spec: captured.update(spec),
        )
        assert rc == 0
        assert captured["priority"] == "low"
        assert captured["needs_review"] is True
        assert captured["plan"]["target_kind"] == "code"

    def test_role_request_emits_medium_priority(self, monkeypatch):
        plan = _StubPlan(
            subsystem="agent_roles",
            target_kind="role",
            target_paths=["collections/.../roles/foo"],
            capability_required="role_edit",
            risk="medium",
        )
        _install_router(monkeypatch, plan)
        captured = {}
        rc = gludd_update.run(
            "update gludd: tweak the standup role",
            todo_creator=lambda spec: captured.update(spec),
        )
        assert rc == 0
        assert captured["priority"] == "medium"
        assert captured["needs_review"] is False

    def test_spec_is_emitted_as_json_when_no_creator(self, monkeypatch):
        plan = _StubPlan(
            subsystem="x", target_kind="config", target_paths=["a.yml"],
            capability_required=None, risk="low",
        )
        _install_router(monkeypatch, plan)
        out = io.StringIO()
        rc = gludd_update.run("update gludd: do a thing", out=out)
        assert rc == 0
        import json

        payload = json.loads(out.getvalue())
        assert payload["priority"] == "high"
        assert payload["work_type"] == "self_update"


# ---------------------------------------------------------------------------
# standalone WITHOUT router — the critical fail-closed-without-raising path
# ---------------------------------------------------------------------------


class TestRouterUnavailable:
    def test_missing_router_prints_notice_and_nonzero_without_raising(self, monkeypatch):
        # Force load_router() to behave as if the router module is absent.
        monkeypatch.setattr(gludd_update, "load_router", lambda: None)
        err = io.StringIO()
        out = io.StringIO()
        rc = gludd_update.run("update gludd: anything", out=out, err=err)
        assert rc != 0
        assert "router unavailable" in err.getvalue().lower()
        # No spec emitted on stdout.
        assert out.getvalue() == ""

    def test_load_router_swallows_import_errors(self, monkeypatch):
        # Even if importlib.import_module raises, load_router must return None,
        # never propagate.
        import importlib as _importlib

        def _boom(name):
            raise ModuleNotFoundError(name)

        monkeypatch.setattr(_importlib, "import_module", _boom)
        assert gludd_update.load_router() is None

    def test_main_does_not_raise_on_missing_router(self, monkeypatch, capsys):
        monkeypatch.setattr(gludd_update, "load_router", lambda: None)
        rc = gludd_update.main(["update gludd: anything"])
        assert rc == 3
        captured = capsys.readouterr()
        assert "router unavailable" in captured.err.lower()

    def test_malformed_request_exits_two(self):
        rc = gludd_update.run("this is not a request", out=io.StringIO(), err=io.StringIO())
        assert rc == 2
