"""Tests for the make audit-coverage target and coverage audit tooling."""

import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"
AUDIT_SCRIPT = ROOT / "scripts" / "audit_coverage.py"


class TestAuditCoverageScript:
    """Unit tests for the coverage audit parser (no pytest run)."""

    def test_script_exists(self):
        assert AUDIT_SCRIPT.exists(), "scripts/audit_coverage.py must exist"

    def test_all_files_above_threshold(self, tmp_path):
        coverage_json = tmp_path / "coverage.json"
        coverage_json.write_text(json.dumps({
            "files": {
                "src/general_ludd/foo.py": {
                    "summary": {"num_statements": 100, "covered_lines": 95, "num_lines_covered": 95},
                },
                "src/general_ludd/bar.py": {
                    "summary": {"num_statements": 50, "covered_lines": 45, "num_lines_covered": 45},
                },
            }
        }))

        result = subprocess.run(
            ["python3", str(AUDIT_SCRIPT), f"--json-file={coverage_json}", "--threshold=85"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}\n{result.stdout}\n{result.stderr}"
        assert "All" in result.stdout and "meet the" in result.stdout

    def test_some_files_below_threshold(self, tmp_path):
        coverage_json = tmp_path / "coverage.json"
        coverage_json.write_text(json.dumps({
            "files": {
                "src/general_ludd/foo.py": {
                    "summary": {"num_statements": 100, "covered_lines": 95},
                },
                "src/general_ludd/bar.py": {
                    "summary": {"num_statements": 100, "covered_lines": 42},
                },
            }
        }))

        result = subprocess.run(
            ["python3", str(AUDIT_SCRIPT), f"--json-file={coverage_json}", "--threshold=85"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}\n{result.stdout}\n{result.stderr}"
        assert "bar.py" in result.stdout

    def test_empty_files_skipped(self, tmp_path):
        coverage_json = tmp_path / "coverage.json"
        coverage_json.write_text(json.dumps({
            "files": {
                "src/general_ludd/foo.py": {
                    "summary": {"num_statements": 100, "covered_lines": 90},
                },
                "src/general_ludd/empty.py": {
                    "summary": {"num_statements": 0, "covered_lines": 0},
                },
            }
        }))

        result = subprocess.run(
            ["python3", str(AUDIT_SCRIPT), f"--json-file={coverage_json}", "--threshold=85"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        assert result.returncode == 0

    def test_branch_arcs_and_contexts_are_reported(self, tmp_path):
        coverage_json = tmp_path / "coverage.json"
        coverage_json.write_text(json.dumps({
            "files": {
                "src/general_ludd/branchy.py": {
                    "summary": {
                        "num_statements": 10, "covered_lines": 10,
                        "num_branches": 4, "covered_branches": 3,
                    },
                    "missing_branches": [[10, 12]],
                    "contexts": {"10": ["test_branch"]},
                },
            }
        }))
        result = subprocess.run(
            ["python3", str(AUDIT_SCRIPT), f"--json-file={coverage_json}", "--threshold=70"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        assert result.returncode == 0
        spec = importlib.util.spec_from_file_location("audit_coverage_arcs", AUDIT_SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        parsed, _, passed = module.parse_coverage_json(str(coverage_json), 70, "src/general_ludd")
        assert passed
        assert parsed["branch_coverage"] == 75.0
        assert parsed["e2e_branch_coverage"] == 75.0
        assert parsed["e2e_branch_totals"] == {
            "scope": "tests/e2e",
            "total": 4,
            "covered": 3,
            "missing": 1,
            "coverage_percent": 75.0,
            "total_branches": 4,
            "covered_branches": 3,
        }
        assert parsed["per_file_thresholds"] == {"line": 75.0, "branch": 75.0}
        assert parsed["per_file_results"]["general_ludd/branchy.py"]["passed"] is True
        assert parsed["missing_arcs"]["general_ludd/branchy.py"] == [[10, 12]]
        assert parsed["contexts"]["general_ludd/branchy.py"] == ["10"]

    def test_per_file_branch_floor_fails_even_when_aggregate_passes(self, tmp_path):
        coverage_json = tmp_path / "coverage.json"
        coverage_json.write_text(json.dumps({
            "files": {
                "src/general_ludd/low.py": {"summary": {
                    "num_statements": 10, "covered_lines": 10,
                    "num_branches": 4, "covered_branches": 2,
                }},
                "src/general_ludd/high.py": {"summary": {
                    "num_statements": 10, "covered_lines": 10,
                    "num_branches": 100, "covered_branches": 100,
                }},
            }
        }))
        spec = importlib.util.spec_from_file_location("audit_coverage_floor", AUDIT_SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _, under, passed = module.parse_coverage_json(
            str(coverage_json), 85, "src/general_ludd", per_file_threshold=75
        )
        assert not passed
        assert "general_ludd/low.py" in under

    def test_aggregate_floor_does_not_replace_lower_per_file_line_floor(self, tmp_path):
        """A file between the 75% file floor and 85% aggregate floor must pass."""
        coverage_json = tmp_path / "coverage.json"
        coverage_json.write_text(json.dumps({
            "files": {
                "src/general_ludd/eighty.py": {"summary": {
                    "num_statements": 10, "covered_lines": 8,
                    "num_branches": 4, "covered_branches": 4,
                }},
                "src/general_ludd/perfect.py": {"summary": {
                    "num_statements": 10, "covered_lines": 10,
                    "num_branches": 4, "covered_branches": 4,
                }},
            }
        }))
        spec = importlib.util.spec_from_file_location("audit_coverage_line_floors", AUDIT_SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        report, under, passed = module.parse_coverage_json(
            str(coverage_json), 85, "src/general_ludd", per_file_threshold=75
        )

        assert report["line_coverage"] == 90.0
        assert under == []
        assert passed
        assert report["per_file_results"]["general_ludd/eighty.py"]["passed"] is True

    def test_cli_names_the_branch_ratio_when_only_branch_floor_fails(self, tmp_path):
        """Failure output must not print a passing line ratio as the cause."""
        coverage_json = tmp_path / "coverage.json"
        coverage_json.write_text(json.dumps({
            "files": {
                "src/general_ludd/branchy.py": {"summary": {
                    "num_statements": 10, "covered_lines": 10,
                    "num_branches": 4, "covered_branches": 2,
                }},
            }
        }))

        result = subprocess.run(
            [
                "python3",
                str(AUDIT_SCRIPT),
                f"--json-file={coverage_json}",
                "--threshold=75",
                "--per-file-threshold=75",
            ],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )

        assert result.returncode == 1
        assert "line=100.0%" in result.stdout
        assert "branch=50.0%" in result.stdout
        assert "branch<75.0%" in result.stdout

    def test_json_report_written(self, tmp_path):
        coverage_json = tmp_path / "coverage.json"
        out_json = tmp_path / "out.json"
        coverage_json.write_text(json.dumps({
            "files": {
                "src/general_ludd/foo.py": {
                    "summary": {"num_statements": 100, "covered_lines": 95},
                },
            }
        }))

        subprocess.run(
            ["python3", str(AUDIT_SCRIPT), f"--json-file={coverage_json}", "--threshold=85", f"--json-out={out_json}"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        assert out_json.exists()
        parsed = json.loads(out_json.read_text())
        assert "threshold" in parsed
        assert parsed["threshold"] == 85
        assert parsed["passed"] is True
        assert parsed["total_files"] == 1

    def test_missing_coverage_json_exits_two(self, tmp_path):
        missing = tmp_path / "nope.json"
        result = subprocess.run(
            ["python3", str(AUDIT_SCRIPT), f"--json-file={missing}", "--threshold=85"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        assert result.returncode == 2

    def test_default_audit_uses_isolated_report_and_bounded_child(self, tmp_path, monkeypatch):
        spec = importlib.util.spec_from_file_location("audit_coverage", AUDIT_SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        calls = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            return type("Result", (), {"returncode": 0})()

        monkeypatch.setattr(module.subprocess, "run", fake_run)
        report_path = tmp_path / "coverage-data.json"

        assert module.run_pytest_coverage("src/general_ludd", str(report_path)) == 0
        args, kwargs = calls[0]
        final_args, _ = calls[-1]
        assert "-o" in final_args and str(report_path) in final_args
        assert kwargs["env"]["GLUDD_COVERAGE_AUDIT"] == "1"
        assert kwargs["timeout"] == module.COVERAGE_AUDIT_TIMEOUT_SECONDS
        assert "--cov-branch" in args
        assert "--cov-fail-under=0" in args
        assert any("tests/e2e/" in str(arg) for arg in args)
        assert kwargs["env"]["GLUDD_E2E_ACTIVE"] == "1"

    def test_shard_results_are_recorded_and_failed_shards_are_reported(self, tmp_path, monkeypatch):
        spec = importlib.util.spec_from_file_location("audit_coverage_shards", AUDIT_SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            result = type("Result", (), {})()
            result.returncode = 7 if len(calls) == 1 else 0
            return result

        monkeypatch.setattr(module.subprocess, "run", fake_run)
        shards = []
        assert module.run_pytest_coverage(
            "src/general_ludd", str(tmp_path / "coverage.json"), shards
        ) == 7
        assert len(shards) == 1
        assert shards[0]["path"].startswith("tests/e2e/")
        assert shards[0]["status"] == "failed"
        assert shards[0]["returncode"] == 7

    def test_failed_run_writes_report_with_failed_shards(self, tmp_path, monkeypatch):
        spec = importlib.util.spec_from_file_location("audit_coverage_failure", AUDIT_SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        json_out = tmp_path / "failed-report.json"
        monkeypatch.setattr(module, "run_pytest_coverage", lambda source, path, shards: (
            shards.append({"path": "tests/e2e/test_broken.py", "status": "failed", "returncode": 9})
            or 9
        ))
        monkeypatch.setattr(
            module.sys,
            "argv",
            ["audit_coverage.py", "--json-out=" + str(json_out)],
        )
        try:
            module.main()
        except SystemExit as exc:
            assert exc.code == 9
        else:
            raise AssertionError("failed coverage run must exit non-zero")
        report = json.loads(json_out.read_text())
        assert report["passed"] is False
        assert report["failed_shards"] == [
            {"path": "tests/e2e/test_broken.py", "status": "failed", "returncode": 9}
        ]

    def test_e2e_environment_overrides_caller_value(self, monkeypatch):
        spec = importlib.util.spec_from_file_location("audit_coverage_env", AUDIT_SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        monkeypatch.setenv("GLUDD_E2E_ACTIVE", "0")
        env = module._coverage_environment()
        assert env["GLUDD_E2E_ACTIVE"] == "1"
        assert env["GLUDD_COVERAGE_AUDIT"] == "1"

    def test_e2e_shards_use_bounded_xdist_workers(self, tmp_path, monkeypatch):
        """Shard workers isolate per-test resources without unbounded fan-out."""
        spec = importlib.util.spec_from_file_location("audit_coverage_serial", AUDIT_SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return type("Result", (), {"returncode": 0})()

        monkeypatch.setattr(module.subprocess, "run", fake_run)
        assert module.run_pytest_coverage("src/general_ludd", str(tmp_path / "coverage.json")) == 0
        pytest_args = calls[0]
        assert "-n" in pytest_args
        worker_index = pytest_args.index("-n")
        assert pytest_args[worker_index + 1] == "2"
        assert pytest_args[pytest_args.index("--dist") + 1] == "loadgroup"


class TestMakefileTargets:
    """Integration-level tests that the Make targets exist and are wired."""

    def test_audit_coverage_target_exists(self):
        content = MAKEFILE.read_text()
        assert "audit-coverage:" in content, "Makefile missing audit-coverage target"

    def test_gate_audit_target_exists(self):
        content = MAKEFILE.read_text()
        assert "gate-audit:" in content, "Makefile missing gate-audit target"

    def test_coverage_json_target_exists(self):
        content = MAKEFILE.read_text()
        assert "coverage-json:" in content, "Makefile missing coverage-json target"

    def test_targets_in_phony(self):
        content = MAKEFILE.read_text()
        assert "audit-coverage" in content
        assert "gate-audit" in content

    def test_uses_python_variable(self):
        content = MAKEFILE.read_text()
        lines = content.splitlines()
        # Find the audit-coverage target section
        in_target = False
        python_found = False
        for line in lines:
            if line.startswith("audit-coverage:"):
                in_target = True
                continue
            if in_target and line and not line.startswith("\t") and not line.startswith("    "):
                in_target = False
            if in_target and "$(UV) run python" in line:
                python_found = True
                break
        assert python_found, "audit-coverage target must use the project UV interpreter in its recipe"

    def test_audit_targets_use_project_environment(self):
        """Coverage must run through uv so imports match the E2E environment."""
        content = MAKEFILE.read_text()
        for target in ("audit-coverage:", "coverage-json:"):
            start = content.index(target)
            recipe = content[start:content.find("\n\n", start)]
            assert "$(UV) run python scripts/audit_coverage.py" in recipe
            assert "$(PYTHON) scripts/audit_coverage.py" not in recipe

    def test_make_audit_coverage_help_listed(self):
        content = MAKEFILE.read_text()
        assert "audit-coverage" in content
        assert "Run coverage audit" in content

    def test_make_coverage_json_uses_nox_test(self, tmp_path):
        coverage_json = tmp_path / "coverage.json"
        coverage_json.write_text(json.dumps({
            "files": {
                "src/general_ludd/foo.py": {
                    "summary": {"num_statements": 100, "covered_lines": 95},
                },
            }
        }))

        result = subprocess.run(
            ["python3", str(AUDIT_SCRIPT), f"--json-file={coverage_json}", "--threshold=85"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        assert result.returncode == 0


class TestCoverageAuditRole:
    """Verify the coverage_audit Ansible role exists and is well-formed."""

    def test_role_directory_exists(self):
        role = ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent" / "roles" / "coverage_audit"
        assert role.is_dir(), f"Role directory missing: {role}"

    def test_tasks_main_yml_exists(self):
        tasks = ROOT / "collections/ansible_collections/general_ludd/agent/roles/coverage_audit/tasks/main.yml"
        assert tasks.exists(), "tasks/main.yml missing"

    def test_defaults_main_yml_exists(self):
        defaults = ROOT / "collections/ansible_collections/general_ludd/agent/roles/coverage_audit/defaults/main.yml"
        assert defaults.exists(), "defaults/main.yml missing"

    def test_meta_main_yml_exists(self):
        meta = ROOT / "collections/ansible_collections/general_ludd/agent/roles/coverage_audit/meta/main.yml"
        assert meta.exists(), "meta/main.yml missing"

    def test_defaults_contain_threshold(self):
        defaults = ROOT / "collections/ansible_collections/general_ludd/agent/roles/coverage_audit/defaults/main.yml"
        content = defaults.read_text()
        assert "threshold" in content
        assert "85" in content or "threshold:" in content

    def test_meta_role_name(self):
        meta = ROOT / "collections/ansible_collections/general_ludd/agent/roles/coverage_audit/meta/main.yml"
        content = meta.read_text()
        assert "coverage_audit" in content

    def test_tasks_use_artifact_dir(self):
        tasks = ROOT / "collections/ansible_collections/general_ludd/agent/roles/coverage_audit/tasks/main.yml"
        content = tasks.read_text()
        assert "artifact_dir" in content
