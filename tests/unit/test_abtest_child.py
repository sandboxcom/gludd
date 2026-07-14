"""Structural tests for abtest/_child.py — child entrypoint functions."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from general_ludd.abtest._child import _run_workload, main


class TestRunWorkload:
    def test_import_module_success(self):
        result = _run_workload({"kind": "import_module", "module": "json"})
        assert result == {"imported": "json"}

    def test_import_module_unknown_raises(self):
        with pytest.raises(ModuleNotFoundError):
            _run_workload({"kind": "import_module", "module": "nonexistent_mod_xyz123"})

    def test_import_with_missing_expected_attr_raises(self):
        raised = False
        try:
            _run_workload({
                "kind": "import_module",
                "module": "json",
                "expect_attr": "nonexistent_attr_xyz",
            })
        except AssertionError as e:
            assert "missing attr" in str(e)
            raised = True
        assert raised

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError, match="unknown workload kind"):
            _run_workload({"kind": "invalid_kind"})


class TestMain:
    def test_insufficient_args(self):
        result = main(["prog"])
        assert result == 2

    def test_insufficient_args_partial(self):
        result = main(["prog", "root", '{}', "100", "10"])
        assert result == 2

    def test_bad_workload_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result_path = os.path.join(tmpdir, "result")
            with pytest.raises(json.JSONDecodeError):
                main([
                    "prog",
                    tmpdir,
                    "not-json",
                    "100", "10",
                    result_path,
                    "nonce123",
                ])

    def test_successful_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result_path = os.path.join(tmpdir, "result")
            workload = json.dumps({"kind": "import_module", "module": "json"})
            result = main([
                "prog",
                tmpdir,
                workload,
                "100", "10",
                result_path,
                "nonce-abc-123",
            ])
            assert result == 0
            assert os.path.exists(result_path)
            with open(result_path) as f:
                data = json.loads(f.read())
            assert data["nonce"] == "nonce-abc-123"
            assert "detail" in data
            assert data["detail"]["imported"] == "json"

    def test_workload_failure_returns_one(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result_path = os.path.join(tmpdir, "result")
            workload = json.dumps({"kind": "import_module", "module": "nonexistent_xyz"})
            result = main([
                "prog",
                tmpdir,
                workload,
                "100", "10",
                result_path,
                "nonce",
            ])
            assert result == 1
            assert not os.path.exists(result_path)

    def test_non_existent_result_dir_oserror(self):
        workload = json.dumps({"kind": "import_module", "module": "json"})
        result = main([
            "prog",
            "/tmp/nonexistent_dir_xyz_123",
            workload,
            "100", "10",
            "/tmp/nonexistent_dir_xyz_123/out",
            "nonce",
        ])
        assert result == 1
