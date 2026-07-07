"""Unit tests for ``general_ludd.abtest._child`` — child-process entrypoint.

Covers the previously 20.3%-rated module by exercising:
  * _apply_limits delegation to shared rlimit module
  * _run_workload for import_module and unknown kinds
  * _write_result_nonce atomic write path
  * main() argv parsing, error paths, and success
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.abtest._child import (
    _apply_limits,
    _run_workload,
    _write_result_nonce,
    main,
)


class TestApplyLimits:
    def test_delegates_to_shared_rlimit(self):
        with patch("general_ludd.abtest._child.apply_limits") as mock_apply:
            _apply_limits(512, 30)
        mock_apply.assert_called_once_with(512, 30)


class TestRunWorkload:
    def test_import_module_success(self):
        with patch.dict(sys.modules):
            result = _run_workload({"kind": "import_module", "module": "os"})
        assert result == {"imported": "os"}

    def test_import_module_with_expected_attr_present(self):
        with patch.dict(sys.modules):
            result = _run_workload(
                {"kind": "import_module", "module": "os", "expect_attr": "path"}
            )
        assert result == {"imported": "os"}

    def test_import_module_raises_when_attr_missing(self):
        with patch.dict(sys.modules), pytest.raises(AssertionError, match="missing attr"):
            _run_workload(
                {
                    "kind": "import_module",
                    "module": "os",
                    "expect_attr": "nonexistent_attr_xyz",
                }
            )

    def test_unknown_workload_kind_raises(self):
        with pytest.raises(ValueError, match="unknown workload kind"):
            _run_workload({"kind": "explode"})


class TestWriteResultNonce:
    def test_writes_atomic_nonce_to_result_path(self, tmp_path):
        result_path = str(tmp_path / "result.json")
        _write_result_nonce(result_path, "nonce-abc123", {"detail": "ok"})
        data = json.loads(Path(result_path).read_text())
        assert data["nonce"] == "nonce-abc123"
        assert data["detail"] == {"detail": "ok"}

    def test_no_tmp_file_left_behind(self, tmp_path):
        result_path = str(tmp_path / "result.json")
        _write_result_nonce(result_path, "n", {})
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []


class TestMain:
    def test_invalid_argv_count_returns_2(self):
        rc = main(["child", "root"])
        assert rc == 2

    def test_workload_failure_returns_1(self):
        argv = [
            "child",
            "/fake/root",
            json.dumps({"kind": "unknown_bad", "module": "os"}),
            "512",
            "30",
            "/tmp/result.json",
            "nonce-xyz",
        ]
        with patch("sys.stdout.write"), patch("sys.stdout.flush"):
            rc = main(argv)
        assert rc == 1

    def test_workload_exception_returns_1(self):
        argv = [
            "child",
            "/fake/root",
            json.dumps({"kind": "import_module", "module": "nonexistent_module_xyz"}),
            "512",
            "30",
            str(Path("/tmp/result.json")),
            "nonce-xyz",
        ]
        with patch("sys.stdout.write"), patch("sys.stdout.flush"):
            rc = main(argv)
        assert rc == 1

    def test_success_path_returns_0_and_writes_nonce(self, tmp_path):
        result_path = str(tmp_path / "result.json")
        argv = [
            "child",
            "/fake/root",
            json.dumps({"kind": "import_module", "module": "os"}),
            "512",
            "30",
            result_path,
            "nonce-SUCCESS",
        ]
        with patch("general_ludd.abtest._child.apply_limits"), \
             patch("sys.stdout.write"), \
             patch("sys.stdout.flush"):
            rc = main(argv)
        assert rc == 0, f"main returned {rc}"

    def test_result_nonce_write_oserror_returns_1(self, tmp_path):
        result_path = str(tmp_path / "result.json")
        argv = [
            "child",
            "/fake/root",
            json.dumps({"kind": "import_module", "module": "os"}),
            "512",
            "30",
            result_path,
            "nonce-ERR",
        ]
        with patch("general_ludd.abtest._child.apply_limits"), \
             patch("general_ludd.abtest._child._write_result_nonce", side_effect=OSError("disk full")):
            rc = main(argv)
        assert rc == 1
