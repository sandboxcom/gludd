"""Behavioral unit tests for gludd_langgraph_decision's validate-and-fallback logic.

The decision module's core behavior -- parse the model's JSON reply, validate the
chosen token against ``options``, and fall back to ``options[0]`` (valid=False +
warning) on any failure -- lives entirely inside ``main()``, behind the
``AnsibleModule`` harness. There is no separately-importable pure helper to test
(see the NOTE at the bottom of this file).

To exercise that logic as a unit without a real Ansible run, we:

  1. Load the module file directly off its filesystem path (it lives in the
     collection tree, not on sys.path) with importlib.
  2. Replace its ``AnsibleModule`` with a fake that records params, captures
     ``exit_json`` / ``fail_json`` (raising to unwind main like the real one
     does via SystemExit), and reports check_mode.
  3. Replace its ``GluddClient`` with a fake whose ``.post`` returns a canned
     daemon response, so NO HTTP/model call is made.

This drives main() through the real parse_structured + fallback code path.
"""

from __future__ import annotations

import contextlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar

import pytest

# tests/unit/<file> -> parents[2] == repo root
ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "plugins"
    / "modules"
    / "gludd_langgraph_decision.py"
)


# --------------------------------------------------------------------------- #
# Fakes that stand in for AnsibleModule / GluddClient.
# --------------------------------------------------------------------------- #


class _Unwind(Exception):
    """Raised by fake exit_json/fail_json to unwind main(), like SystemExit."""


class FakeAnsibleModule:
    """Minimal stand-in for ansible.module_utils.basic.AnsibleModule.

    Applies argument-spec defaults over the supplied params and records the
    terminal exit_json / fail_json call.
    """

    def __init__(self, *, argument_spec: dict, **_: Any) -> None:
        self._spec = argument_spec
        self.params: dict[str, Any] = {}
        for name, opt in argument_spec.items():
            self.params[name] = opt.get("default")
        # Overlay caller-provided params (set by the test before main() runs).
        self.params.update(self._provided)
        self.check_mode: bool = self._check_mode
        self.exit_result: dict[str, Any] | None = None
        self.fail_result: dict[str, Any] | None = None

    # The test injects these two via class attributes before main() runs.
    _provided: ClassVar[dict[str, Any]] = {}
    _check_mode: bool = False

    def exit_json(self, **kwargs: Any) -> None:
        self.exit_result = kwargs
        raise _Unwind()

    def fail_json(self, **kwargs: Any) -> None:
        self.fail_result = kwargs
        raise _Unwind()


class FakeClient:
    """Stand-in for GluddClient: .post returns a pre-set canned response."""

    canned: ClassVar[dict[str, Any]] = {}
    last_path: str | None = None
    last_body: dict[str, Any] | None = None

    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        type(self).last_path = path
        type(self).last_body = body
        return dict(type(self).canned)


# --------------------------------------------------------------------------- #
# Loader + driver
# --------------------------------------------------------------------------- #


def _load_decision_module() -> ModuleType:
    if not MODULE_PATH.is_file():
        pytest.skip(f"decision module not present yet: {MODULE_PATH}")
    spec = importlib.util.spec_from_file_location(
        "_gludd_langgraph_decision_under_test", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_main(
    params: dict[str, Any],
    canned_resp: dict[str, Any],
    *,
    check_mode: bool = False,
) -> dict[str, Any]:
    """Drive the decision module's main() with fakes and return its result.

    The returned dict is the kwargs passed to exit_json (success) or fail_json
    (failure); inspect ``["failed"]`` to tell them apart.
    """
    mod = _load_decision_module()

    FakeAnsibleModule._provided = params
    FakeAnsibleModule._check_mode = check_mode
    FakeClient.canned = canned_resp
    # Reset cross-test class state so each run observes only its own .post call.
    FakeClient.last_path = None
    FakeClient.last_body = None

    # Capture the module instance the real main() constructs.
    captured: dict[str, Any] = {}

    def _factory(**kwargs: Any) -> FakeAnsibleModule:
        inst = FakeAnsibleModule(**kwargs)
        captured["module"] = inst
        return inst

    mod.AnsibleModule = _factory
    mod.GluddClient = FakeClient

    with contextlib.suppress(_Unwind):
        mod.main()

    inst: FakeAnsibleModule = captured["module"]
    if inst.exit_result is not None:
        return inst.exit_result
    assert inst.fail_result is not None, "main() neither exited nor failed"
    return inst.fail_result


OPTIONS = ["canary", "blue_green", "all_at_once"]
BASE_PARAMS = {
    "prompt": "pick a rollout",
    "options": OPTIONS,
    "model_profile": "default",
    "daemon_url": "http://localhost:8000",
    "psk": "",
    "timeout": 120,
}


def _resp(text: str, *, status: int = 200, profile: str = "default") -> dict[str, Any]:
    return {"text": text, "_status": status, "model_profile_id": profile}


# --------------------------------------------------------------------------- #
# Tests: the validate-and-fallback decision logic.
# --------------------------------------------------------------------------- #


class TestDecisionValid:
    def test_in_options_decision_is_valid(self):
        resp = _resp('{"decision": "blue_green", "rationale": "safer"}')
        result = _run_main(dict(BASE_PARAMS), resp)
        assert result["failed"] is False
        assert result["valid"] is True
        assert result["decision"] == "blue_green"
        assert result["rationale"] == "safer"
        assert "warnings" not in result or not result["warnings"]
        # The chosen token is one of the allowed options.
        assert result["decision"] in OPTIONS

    def test_fenced_json_reply_is_parsed(self):
        resp = _resp('```json\n{"decision": "canary", "rationale": "small diff"}\n```')
        result = _run_main(dict(BASE_PARAMS), resp)
        assert result["failed"] is False
        assert result["valid"] is True
        assert result["decision"] == "canary"
        assert result["rationale"] == "small diff"

    def test_missing_rationale_defaults_to_empty(self):
        resp = _resp('{"decision": "all_at_once"}')
        result = _run_main(dict(BASE_PARAMS), resp)
        assert result["valid"] is True
        assert result["decision"] == "all_at_once"
        assert result["rationale"] == ""


class TestDecisionFallback:
    def test_out_of_options_decision_falls_back(self):
        resp = _resp('{"decision": "rolling", "rationale": "n/a"}')
        result = _run_main(dict(BASE_PARAMS), resp)
        assert result["failed"] is False
        assert result["valid"] is False
        assert result["decision"] == OPTIONS[0]  # fell back to options[0]
        assert result.get("warnings"), "expected a fallback warning"
        assert any("not in allowed options" in w for w in result["warnings"])

    def test_unparseable_reply_falls_back(self):
        resp = _resp("this is not json at all")
        result = _run_main(dict(BASE_PARAMS), resp)
        assert result["failed"] is False
        assert result["valid"] is False
        assert result["decision"] == OPTIONS[0]
        assert result.get("warnings")
        assert any("could not parse decision JSON" in w for w in result["warnings"])

    def test_json_array_not_object_falls_back(self):
        # Valid JSON but not a dict -> not isinstance(obj, dict) -> fallback.
        resp = _resp('["canary"]')
        result = _run_main(dict(BASE_PARAMS), resp)
        assert result["valid"] is False
        assert result["decision"] == OPTIONS[0]
        assert result.get("warnings")

    def test_empty_text_falls_back(self):
        resp = _resp("")
        result = _run_main(dict(BASE_PARAMS), resp)
        assert result["valid"] is False
        assert result["decision"] == OPTIONS[0]
        assert result.get("warnings")

    def test_raw_text_preserved_on_fallback(self):
        raw = "garbage reply ```not json```"
        resp = _resp(raw)
        result = _run_main(dict(BASE_PARAMS), resp)
        assert result["raw"] == raw  # raw model text surfaced for debugging


class TestDecisionErrorPaths:
    def test_daemon_unreachable_fails(self):
        resp = {"_error": "connection refused", "_status": 0}
        result = _run_main(dict(BASE_PARAMS), resp)
        assert result["failed"] is True
        assert "daemon unreachable" in result["msg"]

    def test_non_200_status_fails(self):
        resp = {"text": "", "_status": 500, "detail": "boom"}
        result = _run_main(dict(BASE_PARAMS), resp)
        assert result["failed"] is True
        assert "decision call failed" in result["msg"]
        assert result.get("status") == 500

    def test_empty_options_fails(self):
        params = dict(BASE_PARAMS)
        params["options"] = []
        result = _run_main(params, _resp('{"decision": "x"}'))
        assert result["failed"] is True
        assert "non-empty list" in result["msg"]


class TestDecisionCheckMode:
    def test_check_mode_skips_call_and_returns_placeholder(self):
        result = _run_main(dict(BASE_PARAMS), _resp("ignored"), check_mode=True)
        assert result["failed"] is False
        assert result["decision"] == OPTIONS[0]
        assert result["valid"] is False
        assert result["raw"] == "[check-mode]"
        # In check mode no daemon call is attempted; FakeClient.post not invoked
        # (last_path is reset to None at the start of each _run_main).
        assert FakeClient.last_path is None
        assert FakeClient.last_body is None


class TestDecisionRequestShape:
    def test_post_targets_models_call_with_options(self):
        _run_main(dict(BASE_PARAMS), _resp('{"decision": "canary"}'))
        assert FakeClient.last_path == "/admin/models/call"
        assert FakeClient.last_body is not None
        assert FakeClient.last_body["options"] == OPTIONS
        assert FakeClient.last_body["response_format"] == "json"


# --------------------------------------------------------------------------- #
# NOTE on what is NOT unit-testable without a refactor.
# --------------------------------------------------------------------------- #
# The validate-and-fallback logic is embedded in main() rather than a pure,
# importable helper (e.g. a hypothetical `decide(raw, options) -> (decision,
# valid, warnings)`). We reach it here ONLY by monkeypatching AnsibleModule and
# GluddClient and driving main() end-to-end. If that logic were extracted into a
# stand-alone function it could be tested directly without any harness. Likewise,
# the AnsibleModule argument validation (required/mutually_exclusive) is enforced
# by the real AnsibleModule and is NOT exercised by FakeAnsibleModule -- covering
# it would need the real ansible runtime or a refactor of the spec into a
# separately-callable validator.
