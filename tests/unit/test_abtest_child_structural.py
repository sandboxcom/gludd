"""Structural tests for abtest/_child.py — child interpreter entrypoint."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from general_ludd.abtest._child import (
    _apply_limits,
    _run_workload,
    _write_result_nonce,
    main,
)


class TestApplyLimits:
    def test_callable_no_args_fail_open(self):
        _apply_limits(512, 30)

    def test_large_memory_no_error(self):
        _apply_limits(16384, 300)


class TestRunWorkload:
    def test_import_module_workload(self):
        result = _run_workload({"kind": "import_module", "module": "os"})
        assert result == {"imported": "os"}

    def test_import_module_with_expect_attr_present(self):
        result = _run_workload({"kind": "import_module", "module": "os", "expect_attr": "path"})
        assert result == {"imported": "os"}

    def test_import_module_missing_attr_raises(self):
        with pytest.raises(AssertionError, match="missing attr"):
            _run_workload({"kind": "import_module", "module": "os", "expect_attr": "no_such_attr_xyz"})

    def test_import_module_not_found_raises(self):
        with pytest.raises(ModuleNotFoundError):
            _run_workload({"kind": "import_module", "module": "no_such_module_xyz"})

    def test_unknown_workload_kind_raises(self):
        with pytest.raises(ValueError, match="unknown workload kind"):
            _run_workload({"kind": "nonexistent"})


class TestWriteResultNonce:
    def test_writes_atomic_result_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_path = os.path.join(tmp, "result.json")
            _write_result_nonce(result_path, "abc123", {"key": "value"})
            assert os.path.exists(result_path)
            assert not os.path.exists(result_path + ".tmp")
            with open(result_path) as f:
                payload = json.loads(f.read())
            assert payload["nonce"] == "abc123"
            assert payload["detail"] == {"key": "value"}

    def test_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_path = os.path.join(tmp, "result.json")
            with open(result_path, "w") as f:
                f.write("old content")
            _write_result_nonce(result_path, "xyz", {"a": 1})
            with open(result_path) as f:
                payload = json.loads(f.read())
            assert payload["nonce"] == "xyz"

    def test_fails_on_nonexistent_directory(self):
        with pytest.raises(OSError):
            _write_result_nonce("/no/such/dir/result.json", "nonce", {})


class TestMain:
    def test_usage_error_on_insufficient_args(self):
        rc = main(["prog"])
        assert rc == 2

    def test_usage_error_on_six_args(self):
        rc = main(["prog", "a", "b", "c", "d", "e"])
        assert rc == 2

    def test_successful_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_path = os.path.join(tmp, "result.json")
            candidate_root = os.path.join(tmp, "candidate")
            os.makedirs(os.path.join(candidate_root, "src"))
            workload = json.dumps({"kind": "import_module", "module": "os"})
            rc = main(["prog", candidate_root, workload, "512", "30", result_path, "nonce99"])
            assert rc == 0
            assert os.path.exists(result_path)
            with open(result_path) as f:
                payload = json.loads(f.read())
            assert payload["nonce"] == "nonce99"

    def test_failed_workload_returns_1(self):
        workload = json.dumps({"kind": "import_module", "module": "no_such_module_xyz"})
        rc = main(["prog", "/tmp", workload, "512", "30", "/tmp/out.json", "n"])
        assert rc == 1

    def test_result_write_failure_returns_1(self):
        workload = json.dumps({"kind": "import_module", "module": "os"})
        rc = main(["prog", "/tmp", workload, "512", "30", "/no/such/dir/out.json", "n"])
        assert rc == 1

    def test_candidate_src_path_prepended(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_path = os.path.join(tmp, "result.json")
            candidate_root = os.path.join(tmp, "candidate")
            src_dir = os.path.join(candidate_root, "src")
            os.makedirs(src_dir)
            fake_module = os.path.join(src_dir, "fake_mod.py")
            with open(fake_module, "w") as f:
                f.write("EXPECTED_ATTR = 42\n")
            workload = json.dumps({"kind": "import_module", "module": "fake_mod", "expect_attr": "EXPECTED_ATTR"})
            rc = main(["prog", candidate_root, workload, "512", "30", result_path, "n"])
            assert rc == 0
