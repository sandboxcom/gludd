"""Extended structural tests for abtest/_child.py — A/B test child process."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from general_ludd.abtest._child import _apply_limits, _run_workload, _write_result_nonce, main


@pytest.fixture(autouse=True)
def applied_limits(monkeypatch):
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "general_ludd.abtest._child.apply_limits",
        lambda mem_mb, cpu_s: calls.append((mem_mb, cpu_s)),
    )
    return calls


class TestApplyLimits:
    def test_apply_limits_delegates_standard_values(self, applied_limits):
        _apply_limits(256, 30)

    def test_apply_limits_delegates_zero_memory(self, applied_limits):
        _apply_limits(0, 30)

    def test_apply_limits_delegates_large_values(self, applied_limits):
        _apply_limits(8192, 3600)


class TestRunWorkload:
    def test_import_module(self):
        result = _run_workload({"kind": "import_module", "module": "os"})
        assert result["imported"] == "os"

    def test_import_module_with_expect_attr(self):
        result = _run_workload({"kind": "import_module", "module": "os", "expect_attr": "path"})
        assert result["imported"] == "os"

    def test_import_module_missing_attr_raises(self):
        with pytest.raises(AssertionError, match="missing attr"):
            _run_workload({"kind": "import_module", "module": "os", "expect_attr": "nonexistent_attr_xyz"})

    def test_unknown_workload_kind_raises(self):
        with pytest.raises(ValueError, match="unknown workload kind"):
            _run_workload({"kind": "invalid_kind"})


class TestWriteResultNonce:
    def test_write_result_nonce(self, tmp_path: Path):
        result_path = str(tmp_path / "result.json")
        _write_result_nonce(result_path, "abc123", {"imported": "os"})
        assert Path(result_path).exists()
        content = json.loads(Path(result_path).read_text())
        assert content["nonce"] == "abc123"
        assert content["detail"]["imported"] == "os"

    def test_write_result_nonce_no_tmp_leftover(self, tmp_path: Path):
        result_path = str(tmp_path / "result.json")
        _write_result_nonce(result_path, "xyz", {"ok": True})
        assert not Path(result_path + ".tmp").exists()

    def test_write_result_nonce_overwrites(self, tmp_path: Path):
        result_path = str(tmp_path / "result.json")
        Path(result_path).write_text("old")
        _write_result_nonce(result_path, "new", {"overwritten": True})
        content = json.loads(Path(result_path).read_text())
        assert content["nonce"] == "new"


class TestMainSyntactic:
    def test_main_insufficient_args_returns_2(self):
        exit_code = main(["prog"])
        assert exit_code == 2

    def test_main_too_few_args_usage_message(self):
        exit_code = main(["prog", "root", "{}"])
        assert exit_code == 2

    def test_main_invalid_workload_json(self):
        exit_code = main(["prog", "/tmp", "not-json", "256", "30", "/tmp/result", "nonce"])
        assert exit_code == 1

    def test_main_valid_args_workload_succeeds(self, tmp_path: Path):
        result_path = str(tmp_path / "result.json")
        exit_code = main(
            [
                "prog",
                "/tmp",
                json.dumps({"kind": "import_module", "module": "os"}),
                "256",
                "30",
                result_path,
                "test-nonce",
            ]
        )
        assert exit_code == 0
        assert Path(result_path).exists()
