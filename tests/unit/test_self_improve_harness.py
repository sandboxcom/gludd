"""Tests for self-improvement harness and bug fixes discovered by gap analysis."""

from __future__ import annotations

import argparse
import contextlib
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ── Bug: list_active() doesn't exist on ProjectManager ──────────────────────

class TestFixListActiveOnProjectManager:
    def test_project_manager_has_list_active_alias(self):
        from general_ludd.projects.manager import ProjectManager

        mgr = ProjectManager()
        mgr.add_project(name="Test", weight=1.0)
        result = mgr.list_active()
        assert isinstance(result, list)

    def test_list_active_only_returns_active_projects(self):
        from general_ludd.projects.manager import ProjectManager

        mgr = ProjectManager()
        mgr.add_project(name="A", weight=1.0)
        mgr.add_project(name="B", weight=1.0)
        a_id = next(p.project_id for p in mgr.list_projects() if p.name == "A")
        mgr.remove_project(a_id)
        result = mgr.list_active()
        assert all(p.active for p in result)


# ── Bug: TodoRepository.get_by_id not project-scoped ────────────────────────

class TestFixGetByIdProjectScoped:
    @pytest.mark.asyncio
    async def test_get_by_id_accepts_project_id_parameter(self):
        from general_ludd.db.repository import TodoRepository

        session = AsyncMock()
        repo = TodoRepository(session)

        db_result = MagicMock()
        db_result.scalar_one_or_none.return_value = None
        session.execute.return_value = db_result

        result = await repo.get_by_id("TODO-001", project_id="proj-xyz")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_id_scopes_to_project(self):
        from general_ludd.db.repository import TodoRepository

        session = AsyncMock()
        repo = TodoRepository(session)

        db_result = MagicMock()
        db_result.scalars.return_value.first.return_value = None
        session.execute.return_value = db_result

        await repo.get_by_id("TODO-001", project_id="proj-xyz")
        stmt = str(session.execute.call_args[0][0])
        assert "project_id" in stmt


# ── Bug: Bare print in integrity reject/log error handlers ──────────────────

class TestFixIntegrityConnectionErrors:
    def test_integrity_reject_uses_handle_connection_error(self):
        with patch("general_ludd.cli._handle_connection_error") as mock_handler:
            with patch.object(httpx, "post", side_effect=httpx.ConnectError("offline")):
                from general_ludd import cli as cli_mod

                with contextlib.suppress(SystemExit):
                    cli_mod._cmd_integrity_reject(
                        argparse.Namespace(
                            change_id="CHG-001", reason="test",
                            daemon_url="http://localhost:8000",
                            signer="admin",
                        )
                    )
            mock_handler.assert_called_once()

    def test_integrity_log_uses_handle_connection_error(self):
        with patch("general_ludd.cli._handle_connection_error") as mock_handler:
            with patch("httpx.get", side_effect=httpx.ConnectError("offline")):
                from general_ludd import cli as cli_mod

                with contextlib.suppress(SystemExit):
                    cli_mod._cmd_integrity_log(
                        argparse.Namespace(daemon_url="http://localhost:8000")
                    )
            mock_handler.assert_called_once()


# ── Bug: _cmd_status ignores --project ─────────────────────────────────────

class TestFixStatusProjectFlag:
    def test_cmd_status_passes_project_to_api(self):
        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"todo_id": "x", "title": "test"}
            mock_get.return_value = mock_resp

            from general_ludd import cli as cli_mod

            cli_mod._cmd_status(
                argparse.Namespace(
                    todo_id=None, project="proj-filter",
                    daemon_url="http://localhost:8000",
                )
            )
            call_url = mock_get.call_args[0][0]
            assert "project_id=proj-filter" in call_url


# ── SelfImprovementHarness ──────────────────────────────────────────────────

class TestSelfImprovementHarness:
    def test_harness_instantiation(self):
        from general_ludd.self_improve.harness import SelfImprovementHarness

        harness = SelfImprovementHarness(repo_root="/tmp/test")
        assert harness.repo_root == "/tmp/test"

    def test_run_gap_analysis_returns_findings(self):
        from general_ludd.self_improve.harness import SelfImprovementHarness

        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = Path(tmpdir) / "src" / "general_ludd"
            src_dir.mkdir(parents=True)
            (src_dir / "orphan_module.py").write_text("class OrphanClass:\n    pass\n")

            tests_dir = Path(tmpdir) / "tests" / "unit"
            tests_dir.mkdir(parents=True)
            (tests_dir / "test_existing.py").write_text("def test_x(): assert True\n")

            harness = SelfImprovementHarness(repo_root=tmpdir)
            findings = harness.run_gap_analysis()

            assert isinstance(findings, list)
            assert any("orphan_module.py" in str(f) for f in findings)

    def test_generate_fix_todos_from_findings(self):
        from general_ludd.self_improve.harness import SelfImprovementHarness

        harness = SelfImprovementHarness(repo_root="/tmp/test")
        findings = [
            {"type": "missing_tests", "file": "src/mod.py", "severity": "high",
             "message": "src/mod.py has no tests"},
            {"type": "dead_code", "file": "src/orphan.py", "severity": "medium",
             "message": "OrphanClass has no callers"},
        ]
        todos = harness.generate_fix_todos(findings)
        assert len(todos) == 2
        assert todos[0]["work_type"] == "test"
        assert todos[1]["work_type"] == "code"
        assert todos[0]["priority"] == "high"

    def test_enqueue_todos_returns_count(self):
        from general_ludd.self_improve.harness import SelfImprovementHarness

        harness = SelfImprovementHarness(repo_root="/tmp/test")
        todos = [
            {"title": "Fix A", "work_type": "code", "priority": "high"},
            {"title": "Fix B", "work_type": "test", "priority": "medium"},
        ]
        count = harness.enqueue_todos(todos)
        assert count == 2
        assert len(harness._todos) == 2

    def test_run_full_cycle_produces_results(self):
        from general_ludd.self_improve.harness import SelfImprovementHarness

        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = Path(tmpdir) / "src" / "general_ludd"
            src_dir.mkdir(parents=True)
            (src_dir / "__init__.py").write_text("")
            (src_dir / "some_module.py").write_text("class UsedClass:\n    pass\n")

            tests_dir = Path(tmpdir) / "tests" / "unit"
            tests_dir.mkdir(parents=True)
            (tests_dir / "test_some_module.py").write_text(
                "from general_ludd.some_module import UsedClass\n"
                "def test_used(): assert UsedClass is not None\n"
            )

            harness = SelfImprovementHarness(repo_root=tmpdir)
            result = harness.run_full_cycle(daemon_url="http://localhost:8000")

            assert "findings_count" in result
            assert "todos_generated" in result
            assert "todos_enqueued" in result

    def test_harness_integrates_with_gap_analyzer(self):
        from general_ludd.self_improve.harness import SelfImprovementHarness

        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = Path(tmpdir) / "src" / "general_ludd"
            src_dir.mkdir(parents=True)
            (src_dir / "__init__.py").write_text("")
            (src_dir / "no_test.py").write_text("class NoTestClass:\n    pass\n")

            tests_dir = Path(tmpdir) / "tests" / "unit"
            tests_dir.mkdir(parents=True)

            harness = SelfImprovementHarness(repo_root=tmpdir)
            findings = harness.run_gap_analysis()
            assert any("no_test.py" in f.get("file", "") for f in findings)

    def test_empty_repo_produces_no_findings(self):
        from general_ludd.self_improve.harness import SelfImprovementHarness

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "src" / "general_ludd").mkdir(parents=True)
            (Path(tmpdir) / "tests" / "unit").mkdir(parents=True)

            harness = SelfImprovementHarness(repo_root=tmpdir)
            findings = harness.run_gap_analysis()
            assert len(findings) == 0

    def test_harness_generates_descriptive_todo_titles(self):
        from general_ludd.self_improve.harness import SelfImprovementHarness

        harness = SelfImprovementHarness(repo_root="/tmp/test")
        findings = [
            {"type": "missing_tests", "file": "src/general_ludd/foo.py",
             "severity": "high",
             "message": "src/general_ludd/foo.py has no corresponding test file"},
        ]
        todos = harness.generate_fix_todos(findings)
        assert "foo.py" in todos[0]["title"]
        assert "test" in todos[0]["title"].lower()
        assert todos[0]["description"]


# ── Real-task-failure ingestion (feedback loop) ─────────────────────────────
# gludd must turn a shortcoming discovered while doing REAL work (a recurring
# blocker detected by BlockerDetector.chronic_blockers) into a self-improvement
# proposal — not just re-scan its own source for missing test files.

class TestRecurringFailureIngestion:
    def _harness(self):
        from general_ludd.self_improve.harness import SelfImprovementHarness

        # A repo_root with no src/tests → static scan yields nothing, so the
        # only findings come from the injected recurring-failure records.
        return SelfImprovementHarness(repo_root="/tmp/gludd-nonexistent-xyz")

    def test_recurring_failures_become_findings(self):
        records = [
            {"task_type": "code", "blocker_kind": "permission_escalation",
             "incident_count": 7, "recent_todo_ids": ["TODO-1", "TODO-2"]},
        ]
        findings = self._harness().run_gap_analysis(recurring_failures=records)
        rf = [f for f in findings if f.get("type") == "recurring_failure"]
        assert len(rf) == 1
        assert rf[0]["blocker_kind"] == "permission_escalation"
        assert rf[0]["incident_count"] == 7
        assert "code" in rf[0]["message"]

    def test_recurring_failure_produces_actionable_todo(self):
        records = [
            {"task_type": "test", "blocker_kind": "resource_contention",
             "incident_count": 5, "recent_todo_ids": ["TODO-9"]},
        ]
        harness = self._harness()
        findings = harness.run_gap_analysis(recurring_failures=records)
        todos = harness.generate_fix_todos(findings)
        rf_todos = [t for t in todos if t.get("gap_type") == "recurring_failure"]
        assert len(rf_todos) == 1
        assert "recurring" in rf_todos[0]["title"].lower()
        assert rf_todos[0]["description"]
        assert rf_todos[0]["blocker_kind"] == "resource_contention"
        assert rf_todos[0]["recent_todo_ids"] == ["TODO-9"]

    def test_accepts_object_shaped_records(self):
        # BlockerDetector returns ChronicBlocker dataclass instances (attribute
        # access), so the harness must accept objects, not only dicts.
        from types import SimpleNamespace

        rec = SimpleNamespace(
            task_type="analysis", blocker_kind="human_input",
            incident_count=6, recent_todo_ids=["TODO-3"],
        )
        findings = self._harness().run_gap_analysis(recurring_failures=[rec])
        rf = [f for f in findings if f.get("type") == "recurring_failure"]
        assert len(rf) == 1
        assert rf[0]["blocker_kind"] == "human_input"

    def test_no_recurring_failures_preserves_static_behavior(self):
        # No records + empty repo → zero findings (regression guard for the
        # existing static path).
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "src" / "general_ludd").mkdir(parents=True)
            (Path(tmpdir) / "tests" / "unit").mkdir(parents=True)
            from general_ludd.self_improve.harness import SelfImprovementHarness

            harness = SelfImprovementHarness(repo_root=tmpdir)
            assert harness.run_gap_analysis() == []


# ── Model-finding schema normalization ──────────────────────────────────────
# _GAP_PROMPT asks the model for {title, description, priority, tier} but
# generate_fix_todos reads {type, severity, message, file}; unnormalized model
# findings degrade to "Fix: unknown gap" with empty descriptions.

class TestModelFindingNormalization:
    def _gateway(self, findings_json: str):
        gw = MagicMock()
        resp = MagicMock()
        resp.content = findings_json
        gw.call_model.return_value = resp
        return gw

    def test_model_findings_produce_real_todos(self):
        import json

        from general_ludd.self_improve.harness import SelfImprovementHarness

        payload = json.dumps([
            {"title": "Add retry to gateway", "description": "gateway lacks retry",
             "priority": "high", "tier": "code"},
            {"title": "Cover parser", "description": "parser has no tests",
             "priority": "medium", "tier": "test"},
        ])
        harness = SelfImprovementHarness(
            repo_root="/tmp/gludd-nonexistent-xyz",
            model_gateway=self._gateway(payload),
        )
        findings = harness.run_gap_analysis()
        todos = harness.generate_fix_todos(findings)
        assert len(todos) == 2
        assert all("unknown gap" not in t["title"].lower() for t in todos)
        assert all(t["description"] for t in todos)

    def test_model_test_tier_maps_to_test_work_type(self):
        import json

        from general_ludd.self_improve.harness import SelfImprovementHarness

        payload = json.dumps([
            {"title": "Cover parser", "description": "parser has no tests",
             "priority": "medium", "tier": "test"},
        ])
        harness = SelfImprovementHarness(
            repo_root="/tmp/gludd-nonexistent-xyz",
            model_gateway=self._gateway(payload),
        )
        todos = harness.generate_fix_todos(harness.run_gap_analysis())
        assert todos[0]["work_type"] == "test"

    def test_non_dict_model_findings_are_filtered_out(self):
        # A model may emit a JSON array containing non-object elements (str/int/
        # null). Those must NOT crash generate_fix_todos/_normalize_finding; only
        # the valid dict finding survives to become a todo.
        import json

        from general_ludd.self_improve.harness import SelfImprovementHarness

        payload = json.dumps([
            "junk",
            42,
            None,
            ["nested", "list"],
            {"title": "t", "description": "d", "priority": "high", "tier": "code"},
        ])
        harness = SelfImprovementHarness(
            repo_root="/tmp/gludd-nonexistent-xyz",
            model_gateway=self._gateway(payload),
        )
        findings = harness.run_gap_analysis()
        # Only the dict finding survived the filter.
        assert findings == [
            {"title": "t", "description": "d", "priority": "high", "tier": "code"}
        ]
        todos = harness.generate_fix_todos(findings)
        assert len(todos) == 1
        assert todos[0]["work_type"] == "code"
        assert todos[0]["description"] == "d"
        assert "unknown gap" not in todos[0]["title"].lower()
