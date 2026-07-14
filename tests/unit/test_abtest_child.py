"""Structural tests for abtest/_child.py — the A/B test child interpreter."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from general_ludd.abtest._child import _apply_limits
from general_ludd.abtest._child import _run_workload
from general_ludd.abtest._child import _write_result_nonce
from general_ludd.abtest._child import main


def test_apply_limits_does_not_raise():
    _apply_limits(256, 30)


def test_run_workload_import_module():
    result = _run_workload({"kind": "import_module", "module": "os"})
    assert isinstance(result, dict)
    assert result["imported"] == "os"


def test_run_workload_import_module_with_expect_attr():
    result = _run_workload({
        "kind": "import_module",
        "module": "os",
        "expect_attr": "path",
    })
    assert result["imported"] == "os"


def test_run_workload_import_module_missing_attr_raises():
    with pytest.raises(AssertionError, match="missing attr"):
        _run_workload({
            "kind": "import_module",
            "module": "os",
            "expect_attr": "nonexistent_attr_xyz",
        })


def test_run_workload_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown workload kind"):
        _run_workload({"kind": "nonexistent"})


def test_write_result_nonce(tmp_path: Path):
    result_path = str(tmp_path / "result.json")
    nonce = "test-nonce-123"
    detail = {"status": "ok", "count": 42}
    _write_result_nonce(result_path, nonce, detail)

    assert os.path.exists(result_path)
    with open(result_path) as f:
        data = json.load(f)
    assert data["nonce"] == nonce
    assert data["detail"] == detail


def test_write_result_nonce_overwrites(tmp_path: Path):
    result_path = str(tmp_path / "result.json")
    _write_result_nonce(result_path, "first", {})
    _write_result_nonce(result_path, "second", {"x": 1})

    with open(result_path) as f:
        data = json.load(f)
    assert data["nonce"] == "second"
    assert data["detail"] == {"x": 1}


def test_main_usage_error():
    rc = main([])
    assert rc == 2


def test_main_happy_path():
    rc = main([
        "prog",
        "/nonexistent/root",
        json.dumps({"kind": "import_module", "module": "os"}),
        "256",
        "30",
        "/tmp/test-result.json",
        "test-nonce",
    ])
    assert rc == 0
