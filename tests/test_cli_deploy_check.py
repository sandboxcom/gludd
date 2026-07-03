"""Tests for the ``gludd deploy-check`` CLI subcommand."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import pytest

import general_ludd.cli_deploy_check as mod
from general_ludd.cli import build_parser
from general_ludd.cli_deploy_check import _cmd_run


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
    subactions = [
        a for a in dc._subparsers._group_actions  # type: ignore[union-attr]
        if hasattr(a, "choices")
    ]
    choices: dict[str, object] = {}
    for a in subactions:
        choices.update(a.choices)  # type: ignore[attr-defined]
    assert "run" in choices
