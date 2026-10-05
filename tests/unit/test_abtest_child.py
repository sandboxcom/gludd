"""Structural tests for abtest/_child.py — the A/B test child interpreter."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from general_ludd.abtest._child import _apply_limits, _run_workload, _write_result_nonce, main


def test_apply_limits_delegates_without_poisoning_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "general_ludd.abtest._child.apply_limits",
        lambda mem_mb, cpu_s: calls.append((mem_mb, cpu_s)),
    )

    _apply_limits(256, 30)
    assert calls == [(256, 30)]


def test_run_workload_import_module() -> None:
    result = _run_workload({"kind": "import_module", "module": "os"})
    assert isinstance(result, dict)
    assert result["imported"] == "os"


def test_run_workload_import_module_with_expect_attr() -> None:
    result = _run_workload(
        {
            "kind": "import_module",
            "module": "os",
            "expect_attr": "path",
        }
    )
    assert result["imported"] == "os"


def test_run_workload_import_module_missing_attr_raises() -> None:
    with pytest.raises(AssertionError, match="missing attr"):
        _run_workload(
            {
                "kind": "import_module",
                "module": "os",
                "expect_attr": "nonexistent_attr_xyz",
            }
        )


def test_run_workload_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown workload kind"):
        _run_workload({"kind": "nonexistent"})


def test_write_result_nonce(tmp_path: Path) -> None:
    result_path = str(tmp_path / "result.json")
    nonce = "test-nonce-123"
    detail = {"status": "ok", "count": 42}
    _write_result_nonce(result_path, nonce, detail)

    assert os.path.exists(result_path)
    with open(result_path) as f:
        data = json.load(f)
    assert data["nonce"] == nonce
    assert data["detail"] == detail


def test_write_result_nonce_overwrites(tmp_path: Path) -> None:
    result_path = str(tmp_path / "result.json")
    _write_result_nonce(result_path, "first", {})
    _write_result_nonce(result_path, "second", {"x": 1})

    with open(result_path) as f:
        data = json.load(f)
    assert data["nonce"] == "second"
    assert data["detail"] == {"x": 1}


def test_main_usage_error() -> None:
    rc = main([])
    assert rc == 2


def test_main_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "general_ludd.abtest._child.apply_limits",
        lambda mem_mb, cpu_s: calls.append((mem_mb, cpu_s)),
    )
    result_path = tmp_path / "test-result.json"

    rc = main(
        [
            "prog",
            "/nonexistent/root",
            json.dumps({"kind": "import_module", "module": "os"}),
            "256",
            "30",
            str(result_path),
            "test-nonce",
        ],
        apply_resource_limits=True,
    )
    assert rc == 0
    assert calls == [(256, 30)]


def test_main_invalid_json_returns_1() -> None:
    rc = main(
        [
            "prog",
            "/nonexistent/root",
            "not-valid-json",
            "256",
            "30",
            "/tmp/result.json",
            "nonce",
        ]
    )
    assert rc == 1


def test_main_workload_exception_returns_1(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "general_ludd.abtest._child.apply_limits",
        lambda mem_mb, cpu_s: None,
    )
    monkeypatch.setattr(
        "general_ludd.abtest._child._run_workload",
        lambda wl: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    result_path = tmp_path / "test-result.json"
    rc = main(
        [
            "prog",
            "/nonexistent/root",
            json.dumps({"kind": "import_module", "module": "os"}),
            "256",
            "30",
            str(result_path),
            "test-nonce",
        ]
    )
    assert rc == 1


def test_main_write_result_oserror_returns_1(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "general_ludd.abtest._child.apply_limits",
        lambda mem_mb, cpu_s: None,
    )
    monkeypatch.setattr(
        "general_ludd.abtest._child._run_workload",
        lambda wl: {"imported": "os"},
    )
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    result_path = str(readonly_dir / "subdir" / "result.json")
    rc = main(
        [
            "prog",
            "/nonexistent/root",
            json.dumps({"kind": "import_module", "module": "os"}),
            "256",
            "30",
            result_path,
            "test-nonce",
        ]
    )
    assert rc == 1
