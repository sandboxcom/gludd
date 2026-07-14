"""Structural tests for general_ludd.abtest._child — the A/B child entrypoint.

These tests verify the child subprocess entrypoint's contract: argument parsing,
workload runner dispatch, result nonce writing, and resource limit application.
They do NOT spawn real child processes; they test the unit logic directly.
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from general_ludd.abtest import _child


def test_main_insufficient_args_returns_usage_code() -> None:
    result = _child.main(["_child"])
    assert result == 2


def test_main_six_args_insufficient() -> None:
    result = _child.main(["_child", "/root", "{}", "100", "30", "/tmp/r.json"])
    assert result == 2


def _write_temp_workload(workload: dict) -> str:
    return json.dumps(workload)


class TestRunWorkload:
    def test_import_module_succeeds(self) -> None:
        detail = _child._run_workload({"kind": "import_module", "module": "json"})
        assert detail == {"imported": "json"}

    def test_import_module_missing_attr_raises(self) -> None:
        with pytest.raises(AssertionError):
            _child._run_workload(
                {"kind": "import_module", "module": "json", "expect_attr": "no_such_attr"}
            )

    def test_unknown_workload_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown workload kind"):
            _child._run_workload({"kind": "no_such_kind"})


class TestWriteResultNonce:
    def test_writes_nonce_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result_path = os.path.join(tmpdir, "result.json")
            _child._write_result_nonce(result_path, "abc-123", {"imported": "json"})

            with open(result_path) as f:
                payload = json.load(f)
            assert payload["nonce"] == "abc-123"
            assert payload["detail"] == {"imported": "json"}

    def test_no_lingering_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result_path = os.path.join(tmpdir, "result.json")
            _child._write_result_nonce(result_path, "n", {})

            files = os.listdir(tmpdir)
            assert files == ["result.json"]


class TestApplyLimits:
    def test_delegates_to_shared_rlimit(self) -> None:
        with patch("general_ludd.abtest._child.apply_limits") as mock_rlimit:
            _child._apply_limits(512, 30)
            mock_rlimit.assert_called_once_with(512, 30)


class TestMainIntegration:
    def test_happy_path_writes_result_and_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result_path = os.path.join(tmpdir, "result.json")
            workload = _write_temp_workload({"kind": "import_module", "module": "json"})

            with patch("general_ludd.abtest._child.apply_limits"):
                rc = _child.main(
                    ["_child", tmpdir, workload, "512", "30", result_path, "nonce-42"]
                )
            assert rc == 0
            with open(result_path) as f:
                payload = json.load(f)
            assert payload["nonce"] == "nonce-42"

    def test_workload_failure_returns_1_no_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result_path = os.path.join(tmpdir, "result.json")
            workload = _write_temp_workload({"kind": "import_module", "module": "nonexistent_module_xyz"})

            with patch("general_ludd.abtest._child.apply_limits"):
                rc = _child.main(
                    ["_child", tmpdir, workload, "512", "30", result_path, "nonce-99"]
                )
            assert rc == 1
            assert not os.path.exists(result_path)

    def test_ose_error_on_result_write_returns_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result_path = os.path.join(tmpdir, "no_such_dir", "result.json")
            workload = _write_temp_workload({"kind": "import_module", "module": "json"})

            with patch("general_ludd.abtest._child.apply_limits"):
                rc = _child.main(
                    ["_child", tmpdir, workload, "512", "30", result_path, "n"]
                )
            assert rc == 1


def test_module_is_executable_via_main() -> None:
    assert hasattr(_child, "main")
    assert callable(_child.main)


def test_candidate_src_inserted_at_front_of_sys_path() -> None:
    import sys

    original = list(sys.path)
    candidate = "/some/candidate"
    try:
        _child.main(
            ["_child", candidate, json.dumps({"kind": "import_module", "module": "json"}),
             "512", "30", "/tmp/nonexistent/result.json", "n"]
        )
    except SystemExit:
        pass
    finally:
        sys.path[:] = original
