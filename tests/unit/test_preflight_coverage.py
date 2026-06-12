from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestCheckCoverage:
    def test_coverage_file_missing(self, tmp_path):
        from general_ludd.quality.preflight import check_coverage

        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_coverage(threshold=85.0)
            assert result["passed"] is False
            assert result["coverage_pct"] == 0.0

    def test_coverage_file_present_above_threshold(self, tmp_path):
        from general_ludd.quality.preflight import check_coverage

        xml_content = '<coverage line-rate="0.90" branch-rate="0" lines-valid="100"></coverage>'
        (tmp_path / "coverage.xml").write_text(xml_content)
        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_coverage(threshold=85.0)
            assert result["passed"] is True
            assert result["coverage_pct"] == 90.0

    def test_coverage_file_present_below_threshold(self, tmp_path):
        from general_ludd.quality.preflight import check_coverage

        xml_content = '<coverage line-rate="0.50" branch-rate="0" lines-valid="100"></coverage>'
        (tmp_path / "coverage.xml").write_text(xml_content)
        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_coverage(threshold=85.0)
            assert result["passed"] is False
            assert result["coverage_pct"] == 50.0

    def test_coverage_parse_error(self, tmp_path):
        from general_ludd.quality.preflight import check_coverage

        (tmp_path / "coverage.xml").write_text("not valid xml <<<")
        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_coverage(threshold=85.0)
            assert result["passed"] is False
            assert "error" in result

    def test_default_threshold(self, tmp_path):
        from general_ludd.quality.preflight import check_coverage

        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_coverage()
            assert result["threshold"] == 85.0


class TestCheckLint:
    @patch("general_ludd.quality.preflight.subprocess.run")
    def test_lint_success(self, mock_run):
        from general_ludd.quality.preflight import check_lint

        mock_run.return_value = MagicMock(returncode=0, stdout="All checks passed!\n")
        result = check_lint()
        assert result["passed"] is True
        assert result["error_count"] == 0

    @patch("general_ludd.quality.preflight.subprocess.run")
    def test_lint_failure(self, mock_run):
        from general_ludd.quality.preflight import check_lint

        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="src/foo.py:1:1 E501 line too long\nsrc/bar.py:2:1 F401 unused import\n",
        )
        result = check_lint()
        assert result["passed"] is False
        assert result["error_count"] > 0

    @patch("general_ludd.quality.preflight.subprocess.run", side_effect=Exception("timeout"))
    def test_lint_exception(self, mock_run):
        from general_ludd.quality.preflight import check_lint

        result = check_lint()
        assert result["passed"] is False
        assert "output" in result


class TestCheckMypy:
    @patch("general_ludd.quality.preflight.subprocess.run")
    def test_mypy_success(self, mock_run):
        from general_ludd.quality.preflight import check_mypy

        mock_run.return_value = MagicMock(returncode=0, stdout="Success: no issues found in 10 source files\n")
        result = check_mypy()
        assert result["passed"] is True
        assert result["error_count"] == 0

    @patch("general_ludd.quality.preflight.subprocess.run")
    def test_mypy_failure(self, mock_run):
        from general_ludd.quality.preflight import check_mypy

        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="src/foo.py:10: error: Incompatible types\nsrc/bar.py:20: error: Missing return\n",
        )
        result = check_mypy()
        assert result["passed"] is False
        assert result["error_count"] == 2

    @patch("general_ludd.quality.preflight.subprocess.run", side_effect=Exception("boom"))
    def test_mypy_exception(self, mock_run):
        from general_ludd.quality.preflight import check_mypy

        result = check_mypy()
        assert result["passed"] is False


class TestCheckTemplates:
    def test_all_templates_present(self, tmp_path):
        from general_ludd.quality.preflight import check_templates

        tpl_dir = tmp_path / "templates" / "prompts"
        tpl_dir.mkdir(parents=True)
        for fname in [
            "implementation.md.j2",
            "test_creation.md.j2",
            "code_review.md.j2",
            "documentation.md.j2",
            "gap_analysis.md.j2",
            "log_audit.md.j2",
            "prompt_eval.md.j2",
            "dependency_update.md.j2",
        ]:
            (tpl_dir / fname).write_text("template")
        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_templates()
            assert result["passed"] is True
            assert len(result["found"]) == 9
            assert len(result["missing"]) == 0

    def test_templates_missing(self, tmp_path):
        from general_ludd.quality.preflight import check_templates

        tpl_dir = tmp_path / "templates" / "prompts"
        tpl_dir.mkdir(parents=True)
        (tpl_dir / "implementation.md.j2").write_text("template")
        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_templates()
            assert result["passed"] is False
            assert len(result["missing"]) > 0

    def test_templates_dir_missing(self, tmp_path):
        from general_ludd.quality.preflight import check_templates

        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_templates()
            assert result["passed"] is False


class TestCheckPlaybooks:
    def test_playbooks_present(self, tmp_path):
        from general_ludd.quality.preflight import check_playbooks

        pb_dir = tmp_path / "playbooks"
        pb_dir.mkdir()
        (pb_dir / "deploy.yml").write_text("---")
        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_playbooks()
            assert result["passed"] is True
            assert "deploy.yml" in result["found"]

    def test_playbooks_dir_missing(self, tmp_path):
        from general_ludd.quality.preflight import check_playbooks

        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_playbooks()
            assert result["passed"] is False
            assert "error" in result

    def test_playbooks_dir_empty(self, tmp_path):
        from general_ludd.quality.preflight import check_playbooks

        pb_dir = tmp_path / "playbooks"
        pb_dir.mkdir()
        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_playbooks()
            assert result["passed"] is False
            assert result["count"] == 0


class TestCheckMoleculeScenarios:
    def test_scenarios_present(self, tmp_path):
        from general_ludd.quality.preflight import check_molecule_scenarios

        mol_dir = tmp_path / "molecule" / "playbooks" / "default"
        mol_dir.mkdir(parents=True)
        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_molecule_scenarios()
            assert result["passed"] is True
            assert result["scenario_count"] >= 1

    def test_molecule_dir_missing(self, tmp_path):
        from general_ludd.quality.preflight import check_molecule_scenarios

        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_molecule_scenarios()
            assert result["passed"] is False
            assert result["scenario_count"] == 0


class TestCheckFilestore:
    @patch("general_ludd.quality.preflight.FileStore")
    def test_filestore_creation(self, MockFS):
        from general_ludd.quality.preflight import check_filestore

        mock = MagicMock()
        mock.root_path = "/fake/path"
        MockFS.return_value = mock
        result = check_filestore()
        assert result["passed"] is True
        assert result["root_path"] == "/fake/path"

    @patch("general_ludd.quality.preflight.FileStore", side_effect=Exception("no fs"))
    def test_filestore_exception(self, MockFS):
        from general_ludd.quality.preflight import check_filestore

        result = check_filestore()
        assert result["passed"] is False
        assert "error" in result


class TestCheckSprintBoxes:
    def test_no_unchecked_boxes(self, tmp_path):
        from general_ludd.quality.preflight import check_sprint_boxes

        sprint_dir = tmp_path / "docs" / "internal"
        sprint_dir.mkdir(parents=True)
        (sprint_dir / "sprint01.md").write_text("- [x] done task\n- [x] another done\n")
        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_sprint_boxes()
            assert result["passed"] is True
            assert result["unchecked_count"] == 0

    def test_unchecked_boxes(self, tmp_path):
        from general_ludd.quality.preflight import check_sprint_boxes

        sprint_dir = tmp_path / "docs" / "internal"
        sprint_dir.mkdir(parents=True)
        (sprint_dir / "sprint01.md").write_text("- [ ] todo\n- [x] done\n* [ ] another todo\n")
        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_sprint_boxes()
            assert result["passed"] is False
            assert result["unchecked_count"] == 2

    def test_no_sprint_dir(self, tmp_path):
        from general_ludd.quality.preflight import check_sprint_boxes

        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_sprint_boxes()
            assert result["passed"] is True
            assert result["unchecked_count"] == 0


class TestRunPreflight:
    @patch("general_ludd.quality.preflight.run_completion_audit", return_value={"passed": True, "overall": "PASS"})
    @patch("general_ludd.quality.preflight.check_sprint_boxes", return_value={"passed": True, "unchecked_count": 0})
    @patch("general_ludd.quality.preflight.check_filestore", return_value={"passed": True, "root_path": "/tmp"})
    @patch(
        "general_ludd.quality.preflight.check_molecule_scenarios",
        return_value={"passed": True, "scenario_count": 1},
    )
    @patch(
        "general_ludd.quality.preflight.check_playbooks",
        return_value={"passed": True, "found": ["a.yml"], "count": 1},
    )
    @patch(
        "general_ludd.quality.preflight.check_templates",
        return_value={"passed": True, "found": [], "missing": [], "total": 0},
    )
    @patch("general_ludd.quality.preflight.check_mypy", return_value={"passed": True, "error_count": 0})
    @patch("general_ludd.quality.preflight.check_lint", return_value={"passed": True, "error_count": 0})
    @patch(
        "general_ludd.quality.preflight.check_coverage",
        return_value={"passed": True, "coverage_pct": 90.0, "threshold": 85.0},
    )
    def test_all_pass(
        self, mock_cov, mock_lint, mock_mypy, mock_tpl,
        mock_pb, mock_mol, mock_fs, mock_sprint, mock_audit,
    ):
        from general_ludd.quality.preflight import run_preflight

        report = run_preflight()
        assert report["overall"] == "PASS"
        assert report["passed_count"] == report["total_count"]

    @patch("general_ludd.quality.preflight.run_completion_audit", return_value={"passed": True, "overall": "PASS"})
    @patch("general_ludd.quality.preflight.check_sprint_boxes", return_value={"passed": True, "unchecked_count": 0})
    @patch("general_ludd.quality.preflight.check_filestore", return_value={"passed": True, "root_path": "/tmp"})
    @patch(
        "general_ludd.quality.preflight.check_molecule_scenarios",
        return_value={"passed": True, "scenario_count": 1},
    )
    @patch(
        "general_ludd.quality.preflight.check_playbooks",
        return_value={"passed": True, "found": ["a.yml"], "count": 1},
    )
    @patch(
        "general_ludd.quality.preflight.check_templates",
        return_value={"passed": True, "found": [], "missing": [], "total": 0},
    )
    @patch("general_ludd.quality.preflight.check_mypy", return_value={"passed": False, "error_count": 5})
    @patch("general_ludd.quality.preflight.check_lint", return_value={"passed": True, "error_count": 0})
    @patch(
        "general_ludd.quality.preflight.check_coverage",
        return_value={"passed": True, "coverage_pct": 90.0, "threshold": 85.0},
    )
    def test_some_fail(
        self, mock_cov, mock_lint, mock_mypy, mock_tpl,
        mock_pb, mock_mol, mock_fs, mock_sprint, mock_audit,
    ):
        from general_ludd.quality.preflight import run_preflight

        report = run_preflight()
        assert report["overall"] == "FAIL"
        assert report["passed_count"] < report["total_count"]


class TestVerifyTaskCompletion:
    def test_coverage_85_pattern(self):
        from general_ludd.quality.preflight import verify_task_completion

        result = verify_task_completion(["coverage must be 85%"], {"coverage_pct": 90.0})
        assert result["complete"] is True

    def test_coverage_85_pattern_fail(self):
        from general_ludd.quality.preflight import verify_task_completion

        result = verify_task_completion(["coverage must be 85%"], {"coverage_pct": 70.0})
        assert result["complete"] is False

    def test_coverage_generic_pattern(self):
        from general_ludd.quality.preflight import verify_task_completion

        result = verify_task_completion(["code coverage"], {"coverage_pct": 85.0})
        assert result["complete"] is True

    def test_lint_clean_pattern(self):
        from general_ludd.quality.preflight import verify_task_completion

        result = verify_task_completion(["no lint errors"], {"lint_errors": 0})
        assert result["complete"] is True

    def test_lint_clean_fail(self):
        from general_ludd.quality.preflight import verify_task_completion

        result = verify_task_completion(["lint clean pass"], {"lint_errors": 3})
        assert result["complete"] is False

    def test_mypy_pattern(self):
        from general_ludd.quality.preflight import verify_task_completion

        result = verify_task_completion(["no type errors"], {"mypy_errors": 0})
        assert result["complete"] is True

    def test_test_pass_pattern(self):
        from general_ludd.quality.preflight import verify_task_completion

        result = verify_task_completion(["all tests pass"], {"test_fail_count": 0})
        assert result["complete"] is True

    def test_test_count_pattern(self):
        from general_ludd.quality.preflight import verify_task_completion

        result = verify_task_completion(["test count is positive"], {"test_pass_count": 42})
        assert result["complete"] is True

    def test_test_count_pattern_zero(self):
        from general_ludd.quality.preflight import verify_task_completion

        result = verify_task_completion(["test count is positive"], {"test_pass_count": 0})
        assert result["complete"] is False

    def test_unknown_criterion_fails_closed(self):
        from general_ludd.quality.preflight import verify_task_completion

        result = verify_task_completion(["something custom"], {})
        assert result["complete"] is False
        assert result["criteria_results"][0]["reason"] == "unknown_criterion"

    def test_empty_criteria(self):
        from general_ludd.quality.preflight import verify_task_completion

        result = verify_task_completion([], {})
        assert result["complete"] is False
        assert result["confidence"] == 0.0

    def test_confidence_calculation(self):
        from general_ludd.quality.preflight import verify_task_completion

        result = verify_task_completion(
            ["coverage 85", "no lint"],
            {"coverage_pct": 90.0, "lint_errors": 3},
        )
        assert result["complete"] is False
        assert result["confidence"] == 0.5
        assert result["passed"] == 1
        assert result["total"] == 2


class TestRunCompletionAudit:
    def test_finds_unused_class(self, tmp_path):
        from general_ludd.quality.preflight import run_completion_audit

        src_dir = tmp_path / "src" / "general_ludd"
        src_dir.mkdir(parents=True)
        (src_dir / "alone.py").write_text("class OrphanClass:\n    pass\n")

        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = run_completion_audit()
            assert result["passed"] is False
            assert any(f["class_name"] == "OrphanClass" for f in result["findings"])

    def test_no_unused_classes(self, tmp_path):
        from general_ludd.quality.preflight import run_completion_audit

        src_dir = tmp_path / "src" / "general_ludd"
        src_dir.mkdir(parents=True)
        (src_dir / "used.py").write_text("class UsedClass:\n    pass\n")
        (src_dir / "consumer.py").write_text("from used import UsedClass\nUsedClass()\n")

        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = run_completion_audit()
            assert result["passed"] is True
            assert result["findings"] == []

    def test_skips_private_classes(self, tmp_path):
        from general_ludd.quality.preflight import run_completion_audit

        src_dir = tmp_path / "src" / "general_ludd"
        src_dir.mkdir(parents=True)
        (src_dir / "internal.py").write_text("class _PrivateHelper:\n    pass\n")

        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = run_completion_audit()
            assert result["passed"] is True

    def test_empty_src_dir(self, tmp_path):
        from general_ludd.quality.preflight import run_completion_audit

        src_dir = tmp_path / "src" / "general_ludd"
        src_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("")

        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = run_completion_audit()
            assert "completion_pct" in result


class TestGenerateBacklogFromAudit:
    def test_generates_todos(self):
        from general_ludd.quality.preflight import generate_backlog_from_audit

        audit = {
            "findings": [
                {
                    "class_name": "DeadClass",
                    "file": "src/foo.py",
                    "line": 10,
                    "reason": "class defined but never instantiated",
                    "severity": "warn",
                }
            ]
        }
        todos = generate_backlog_from_audit(audit)
        assert len(todos) == 1
        assert "DeadClass" in todos[0]["title"]
        assert todos[0]["priority"] == "medium"
        assert todos[0]["source_file"] == "src/foo.py"

    def test_fail_severity_high_priority(self):
        from general_ludd.quality.preflight import generate_backlog_from_audit

        audit = {
            "findings": [
                {
                    "class_name": "Broken",
                    "file": "src/bar.py",
                    "line": 5,
                    "reason": "critical",
                    "severity": "fail",
                }
            ]
        }
        todos = generate_backlog_from_audit(audit)
        assert todos[0]["priority"] == "high"

    def test_empty_findings(self):
        from general_ludd.quality.preflight import generate_backlog_from_audit

        todos = generate_backlog_from_audit({"findings": []})
        assert todos == []

    def test_no_findings_key(self):
        from general_ludd.quality.preflight import generate_backlog_from_audit

        todos = generate_backlog_from_audit({})
        assert todos == []

    def test_function_name_fallback(self):
        from general_ludd.quality.preflight import generate_backlog_from_audit

        audit = {
            "findings": [
                {
                    "function_name": "dead_func",
                    "file": "src/baz.py",
                    "line": 1,
                    "reason": "unused",
                    "severity": "warn",
                }
            ]
        }
        todos = generate_backlog_from_audit(audit)
        assert "dead_func" in todos[0]["title"]
