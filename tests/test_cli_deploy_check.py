"""Tests for the ``gludd deploy-check`` CLI subcommand."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from typing import cast

import pytest

import general_ludd.cli_deploy_check as mod
from general_ludd.cli import build_parser
from general_ludd.cli_deploy_check import (
    _cmd_approve,
    _cmd_reject,
    _cmd_run,
    _cmd_suggest_fix,
)


class _Args:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


_CANNED = {
    "findings": [
        {
            "rule_id": "a",
            "severity": "warn",
            "engine": "vllm",
            "message": "gpu_memory_utilization under-uses the GPU",
            "remediation": "raise gpu_memory_utilization toward 0.90",
            "evidence": {},
        },
        {
            "rule_id": "k",
            "severity": "warn",
            "engine": "vllm",
            "message": "extra GPUs are idle",
            "remediation": "set tensor_parallel_size",
            "evidence": {},
        },
    ],
    "remediations": [
        {
            "rule_id": "a",
            "format": "yaml",
            "config_patch": {"gpu_memory_utilization": 0.90},
            "requires_restart": True,
            "notes": "n",
        },
        {
            "rule_id": "k",
            "format": "yaml",
            "config_patch": {"tensor_parallel_size": 2},
            "requires_restart": True,
            "notes": "n",
        },
    ],
    "has_critical": False,
}


def test_run_lists_rule_ids_and_patches(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "deployment.json"
    cfg.write_text(json.dumps({"engine": "vllm", "gpu_memory_utilization": 0.3}))

    monkeypatch.setattr(mod, "_http", lambda *a, **k: _CANNED)
    args = _Args(
        config=str(cfg),
        gpu_type="h100",
        gpu_count=8,
        daemon_url="http://localhost:8000",
        json=False,
    )
    captured = io.StringIO()
    with redirect_stdout(captured):
        _cmd_run(args)
    out = captured.getvalue()
    # Rule ids listed.
    assert "rule a" in out
    assert "rule k" in out
    # config_patch surfaced.
    assert "gpu_memory_utilization" in out
    assert "tensor_parallel_size" in out


def test_run_json_output(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "d.json"
    cfg.write_text(json.dumps({"engine": "vllm"}))
    monkeypatch.setattr(mod, "_http", lambda *a, **k: _CANNED)
    args = _Args(
        config=str(cfg),
        gpu_type=None,
        gpu_count=1,
        daemon_url="http://localhost:8000",
        json=True,
    )
    captured = io.StringIO()
    with redirect_stdout(captured):
        _cmd_run(args)
    data = json.loads(captured.getvalue())
    assert {f["rule_id"] for f in data["findings"]} == {"a", "k"}


def test_has_critical_exits_nonzero(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "d.json"
    cfg.write_text(json.dumps({"engine": "bogus"}))
    canned = dict(_CANNED)
    canned["has_critical"] = True
    monkeypatch.setattr(mod, "_http", lambda *a, **k: canned)
    args = _Args(
        config=str(cfg),
        gpu_type=None,
        gpu_count=1,
        daemon_url="http://localhost:8000",
        json=False,
    )
    with pytest.raises(SystemExit) as exc, redirect_stdout(io.StringIO()):
        _cmd_run(args)
    assert exc.value.code == 2


def test_run_subparser_is_registered() -> None:
    _parser, subcommand_map = build_parser()
    assert "deploy-check" in subcommand_map
    dc = subcommand_map["deploy-check"]
    # The 'run' subcommand exists under deploy-check.
    assert dc._subparsers is not None
    subactions = [
        a for a in dc._subparsers._group_actions
        if hasattr(a, "choices")
    ]
    choices: dict[str, object] = {}
    for a in subactions:
        ch = getattr(a, "choices", None)
        if ch is not None:
            choices.update(ch)
    assert "run" in choices
    # The SLM fix-loop subcommands are also registered.
    assert "suggest-fix" in choices
    assert "approve" in choices
    assert "reject" in choices


def test_suggest_fix_prints_patch_and_source(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "d.json"
    cfg.write_text(json.dumps({"engine": "vllm", "gpu_memory_utilization": 0.99}))
    canned = {
        "fix_id": "abc123",
        "patch": {"gpu_memory_utilization": 0.90},
        "source": "deterministic",
    }
    monkeypatch.setattr(mod, "_http", lambda *a, **k: canned)
    args = _Args(
        config=str(cfg),
        gpu_type="a100_80",
        gpu_count=1,
        daemon_url="http://localhost:8000",
        json=False,
    )
    captured = io.StringIO()
    with redirect_stdout(captured):
        _cmd_suggest_fix(args)
    out = captured.getvalue()
    assert "abc123" in out
    assert "deterministic" in out
    assert "gpu_memory_utilization" in out


def test_approve_prints_status_and_merged_config(monkeypatch) -> None:
    canned = {
        "fix_id": "abc123",
        "status": "approved",
        "merged_config": {"engine": "vllm", "gpu_memory_utilization": 0.90},
        "retried": True,
    }
    seen: dict[str, object] = {}

    def _fake_http(method: str, url: str, *, json_body: object = None, **k: object) -> dict[str, object]:
        seen["url"] = url
        seen["json_body"] = json_body
        return canned

    monkeypatch.setattr(mod, "_http", _fake_http)
    args = _Args(
        fix_id="abc123",
        retry=True,
        daemon_url="http://localhost:8000",
        json=False,
    )
    captured = io.StringIO()
    with redirect_stdout(captured):
        _cmd_approve(args)
    out = captured.getvalue()
    assert "approved" in out
    assert "gpu_memory_utilization" in out
    assert cast(str, seen["url"]).endswith("/admin/deployments/fixes/abc123/approve")
    assert seen["json_body"] == {"retry": True}


def test_reject_prints_status_and_reason(monkeypatch) -> None:
    canned = {"fix_id": "abc123", "status": "rejected", "reason": "nope"}
    seen: dict[str, object] = {}

    def _fake_http(method: str, url: str, *, json_body: object = None, **k: object) -> dict[str, object]:
        seen["json_body"] = json_body
        return canned

    monkeypatch.setattr(mod, "_http", _fake_http)
    args = _Args(
        fix_id="abc123",
        reason="nope",
        daemon_url="http://localhost:8000",
        json=False,
    )
    captured = io.StringIO()
    with redirect_stdout(captured):
        _cmd_reject(args)
    out = captured.getvalue()
    assert "rejected" in out
    assert "nope" in out
    assert seen["json_body"] == {"reason": "nope"}
