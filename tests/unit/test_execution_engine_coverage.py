"""Coverage tests for general_ludd.execution.engine.

Append-only sibling of test_execution_engine.py. Targets the low-coverage
surface of engine.py: module-level helpers, budget pre-check branches, the
workspace path jail, unified-diff parsing, metrics recording, the empty/no-
change/model-error branches, and the async execute path.

NOTE on the requested tool_loop.py bug: there is no execution/tool_loop.py in
this codebase (execution/ holds only __init__.py + engine.py), so the
max_iterations=0 UnboundLocalError scenario does not exist here. These tests
cover engine.py instead. See the agent report for details.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.execution import engine as engine_mod
from general_ludd.execution.engine import (
    ExecutionEngine,
    _build_system_prompt,
    _build_user_prompt,
    _extract_file_paths,
    _git_create_branch,
    _parse_fenced_blocks,
    _run_tests,
    _slugify,
)
from general_ludd.schemas.job import JobSpec


def _job(**overrides) -> JobSpec:
    base = dict(
        job_id="JOB-X",
        todo_id="TODO-X",
        playbook="code",
        queue="core",
        work_type="code",
        prompt_text="Do the thing",
    )
    base.update(overrides)
    return JobSpec(**base)


def _engine(gateway=None, workspace=None, **kwargs) -> ExecutionEngine:
    return ExecutionEngine(
        model_gateway=gateway if gateway is not None else MagicMock(),
        workspace_path=workspace or tempfile.mkdtemp(),
        **kwargs,
    )


# --------------------------------------------------------------------------
# Module-level helpers
# --------------------------------------------------------------------------
class TestParseFencedBlocks:
    def test_extracts_language_and_content(self):
        text = "```python\nprint('hi')\n```"
        blocks = _parse_fenced_blocks(text)
        assert blocks == [{"language": "python", "content": "print('hi')"}]

    def test_blank_language_defaults_to_text(self):
        blocks = _parse_fenced_blocks("```\nbody\n```")
        assert blocks[0]["language"] == "text"

    def test_multiple_blocks(self):
        text = "```diff\nd\n```\nmid\n```py\np\n```"
        blocks = _parse_fenced_blocks(text)
        assert [b["language"] for b in blocks] == ["diff", "py"]

    def test_no_blocks_returns_empty(self):
        assert _parse_fenced_blocks("no fences here") == []


class TestExtractFilePaths:
    def test_single_file_section(self):
        text = "FILE: src/a.py\nprint('a')\n"
        assert _extract_file_paths(text) == [("src/a.py", "print('a')")]

    def test_multiple_file_sections(self):
        text = "FILE: a.py\naaa\nFILE: b.py\nbbb\n"
        result = _extract_file_paths(text)
        assert ("a.py", "aaa") in result
        assert ("b.py", "bbb") in result

    def test_case_insensitive_keyword(self):
        assert _extract_file_paths("file: x.py\nc\n") == [("x.py", "c")]

    def test_no_file_marker_returns_empty(self):
        assert _extract_file_paths("plain text") == []


class TestSlugify:
    def test_basic_kebab_case(self):
        assert _slugify("Fix The Bug!") == "fix-the-bug"

    def test_truncates_to_max_len(self):
        assert len(_slugify("a" * 100, max_len=10)) <= 10

    def test_strips_leading_trailing_dashes(self):
        result = _slugify("  !!hello!!  ")
        assert not result.startswith("-")
        assert not result.endswith("-")


class TestBuildPrompts:
    def test_system_prompt_contains_output_format(self):
        prompt = _build_system_prompt(_job())
        assert "FILE:" in prompt
        assert "coding agent" in prompt

    def test_system_prompt_includes_skill_body(self):
        prompt = _build_system_prompt(_job(skill_body="use TDD"))
        assert "use TDD" in prompt

    def test_system_prompt_with_behavior_prepends_block(self):
        behavior = MagicMock()
        with patch.object(engine_mod, "BehaviorRenderer") as renderer_cls:
            renderer_cls.return_value.render.return_value = "BEHAVIOR_BLOCK"
            prompt = _build_system_prompt(_job(), behavior=behavior)
        assert prompt.startswith("BEHAVIOR_BLOCK")

    def test_user_prompt_uses_prompt_text(self):
        assert _build_user_prompt(_job(prompt_text="hello")) == "hello"

    def test_user_prompt_falls_back_when_no_prompt_text(self):
        out = _build_user_prompt(_job(prompt_text=None, todo_id="TODO-9"))
        assert "TODO-9" in out


class TestRunTests:
    def test_make_not_found_returns_zero(self):
        with patch.object(engine_mod.subprocess, "Popen", side_effect=FileNotFoundError):
            code, msg = _run_tests("/tmp")
        assert code == 0
        assert "make not found" in msg

    def test_generic_exception_returns_one(self):
        with patch.object(engine_mod.subprocess, "Popen", side_effect=RuntimeError("boom")):
            code, msg = _run_tests("/tmp")
        assert code == 1
        assert "boom" in msg

    def test_success_returns_returncode_and_truncated_output(self):
        proc = MagicMock()
        proc.communicate.return_value = ("x" * 5000, "")
        proc.returncode = 0
        with patch.object(engine_mod.subprocess, "Popen", return_value=proc):
            code, msg = _run_tests("/tmp")
        assert code == 0
        assert len(msg) == 2000

    def test_empty_stdout_falls_back_to_stderr(self):
        proc = MagicMock()
        proc.communicate.return_value = ("", "stderr detail")
        proc.returncode = 2
        with patch.object(engine_mod.subprocess, "Popen", return_value=proc):
            code, msg = _run_tests("/tmp")
        assert code == 2
        assert "stderr detail" in msg

    def test_timeout_kills_group_and_reports(self):
        proc = MagicMock()
        proc.pid = 4321
        proc.communicate.side_effect = [
            engine_mod.subprocess.TimeoutExpired(cmd="make", timeout=120),
            ("", ""),
        ]
        with patch.object(engine_mod.subprocess, "Popen", return_value=proc), \
             patch.object(engine_mod.os, "getpgid", return_value=4321), \
             patch.object(engine_mod.os, "killpg") as killpg:
            code, msg = _run_tests("/tmp")
        assert code == 1
        assert "timed out" in msg
        killpg.assert_called_once()


class TestGitHelpers:
    def test_create_branch_returns_false_on_exception(self):
        with patch.object(engine_mod, "GitAutomation", side_effect=RuntimeError("x")):
            assert _git_create_branch("/tmp", "br") is False

    def test_create_branch_returns_true_on_success(self):
        with patch.object(engine_mod, "GitAutomation") as ga:
            ga.return_value.create_branch.return_value = None
            assert _git_create_branch("/tmp", "br") is True

    def test_git_commit_truncates_to_8_chars(self):
        with patch.object(engine_mod, "GitAutomation") as ga:
            ga.return_value.commit.return_value = "abcdef1234567890"  # pragma: allowlist secret
            assert engine_mod._git_commit("/tmp", "msg") == "abcdef12"

    def test_git_commit_returns_none_on_exception(self):
        with patch.object(engine_mod, "GitAutomation", side_effect=RuntimeError):
            assert engine_mod._git_commit("/tmp", "msg") is None

    async def test_git_commit_async_runs_in_executor(self):
        with patch.object(engine_mod, "GitAutomation") as ga:
            ga.return_value.commit.return_value = "deadbeefcafe"  # pragma: allowlist secret
            sha = await engine_mod._git_commit_async("/tmp", "msg")
        assert sha == "deadbeef"


# --------------------------------------------------------------------------
# Budget pre-check branches
# --------------------------------------------------------------------------
class TestBudgetPreCheck:
    def test_none_guard_is_allowed(self):
        assert _engine()._budget_pre_check(None) is None

    def test_check_all_limits_allowed(self):
        guard = MagicMock()
        guard.check_all_limits.return_value = {"allowed": True}
        # Avoid try_charge fallback shadowing the branch under test.
        del guard.try_charge
        assert _engine()._budget_pre_check(guard) is None

    def test_check_all_limits_denied_with_reason(self):
        guard = MagicMock()
        guard.check_all_limits.return_value = {"allowed": False, "reason": "broke"}
        del guard.try_charge
        assert _engine()._budget_pre_check(guard) == "broke"

    def test_check_all_limits_raises_is_fail_closed(self):
        guard = MagicMock()
        guard.check_all_limits.side_effect = RuntimeError("kaboom")
        del guard.try_charge
        out = _engine()._budget_pre_check(guard)
        assert "kaboom" in out

    def test_non_dict_verdict_is_denied(self):
        guard = MagicMock()
        guard.check_all_limits.return_value = "nope"
        del guard.try_charge
        assert _engine()._budget_pre_check(guard) == "budget check returned non-dict"

    def test_try_charge_interface_denied(self):
        guard = MagicMock(spec=["try_charge"])
        guard.try_charge.return_value = {"allowed": False, "reason": "drained"}
        assert _engine()._budget_pre_check(guard) == "drained"

    def test_try_charge_interface_allowed(self):
        guard = MagicMock(spec=["try_charge"])
        guard.try_charge.return_value = {"allowed": True}
        assert _engine()._budget_pre_check(guard) is None

    def test_unknown_interface_is_denied(self):
        guard = MagicMock(spec=[])
        assert _engine()._budget_pre_check(guard) == "budget guard has unknown interface"


# --------------------------------------------------------------------------
# Workspace path jail
# --------------------------------------------------------------------------
class TestResolveInWorkspace:
    def test_valid_relative_path_resolves(self):
        ws = tempfile.mkdtemp()
        eng = _engine(workspace=ws)
        resolved = eng._resolve_in_workspace("sub/file.py")
        assert resolved.startswith(os.path.realpath(ws))

    def test_absolute_path_escape_rejected(self):
        eng = _engine()
        with pytest.raises(ValueError, match="escapes the workspace"):
            eng._resolve_in_workspace("/etc/passwd")

    def test_dotdot_traversal_rejected(self):
        eng = _engine()
        with pytest.raises(ValueError, match="escapes the workspace"):
            eng._resolve_in_workspace("../../etc/shadow")

    def test_write_file_creates_nested_dirs(self):
        ws = tempfile.mkdtemp()
        eng = _engine(workspace=ws)
        eng._write_file("a/b/c.txt", "hello")
        with open(os.path.join(ws, "a", "b", "c.txt")) as f:
            assert f.read() == "hello"


# --------------------------------------------------------------------------
# Diff parsing helpers
# --------------------------------------------------------------------------
class TestDiffParsing:
    def test_target_paths_strip_p1_prefix(self):
        eng = _engine()
        diff = "--- a/src/x.py\n+++ b/src/x.py\n"
        assert eng._diff_target_paths(diff) == ["src/x.py", "src/x.py"]

    def test_target_paths_skip_dev_null(self):
        eng = _engine()
        diff = "--- /dev/null\n+++ b/new.py\n"
        assert eng._diff_target_paths(diff) == ["new.py"]

    def test_changed_files_dedup_plus_lines_only(self):
        eng = _engine()
        diff = "--- a/x.py\n+++ b/x.py\n+++ b/x.py\n"
        assert eng._diff_changed_files(diff) == ["x.py"]

    def test_apply_diff_empty_targets_returns_empty(self):
        eng = _engine()
        assert eng._apply_unified_diff("not a diff at all") == []

    def test_apply_diff_escaping_target_refused(self):
        eng = _engine()
        diff = "--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n-x\n+y\n"
        assert eng._apply_unified_diff(diff) == []

    def test_apply_diff_patch_failure_returns_empty(self):
        ws = tempfile.mkdtemp()
        eng = _engine(workspace=ws)
        diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-x\n+y\n"
        result = MagicMock()
        result.returncode = 1
        result.stderr = "patch failed"
        result.stdout = ""
        with patch.object(engine_mod.subprocess, "run", return_value=result):
            assert eng._apply_unified_diff(diff) == []

    def test_apply_diff_success_returns_changed_files(self):
        ws = tempfile.mkdtemp()
        eng = _engine(workspace=ws)
        diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-x\n+y\n"
        result = MagicMock()
        result.returncode = 0
        with patch.object(engine_mod.subprocess, "run", return_value=result):
            assert eng._apply_unified_diff(diff) == ["x.py"]

    def test_apply_diff_subprocess_exception_returns_empty(self):
        ws = tempfile.mkdtemp()
        eng = _engine(workspace=ws)
        diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-x\n+y\n"
        with patch.object(engine_mod.subprocess, "run", side_effect=RuntimeError("oops")):
            assert eng._apply_unified_diff(diff) == []


# --------------------------------------------------------------------------
# Metrics recording
# --------------------------------------------------------------------------
class TestRecordMetrics:
    def test_none_collector_is_noop(self):
        eng = _engine(metrics_collector=None)
        eng._record_metrics(_job(), success=True)  # must not raise

    def test_collector_called_with_fields(self):
        collector = MagicMock()
        eng = _engine(metrics_collector=collector)
        eng._record_metrics(_job(work_type="docs"), success=True, tokens=10)
        collector.record_model_call.assert_called_once()
        kwargs = collector.record_model_call.call_args.kwargs
        assert kwargs["work_type"] == "docs"
        assert kwargs["success"] is True

    def test_collector_exception_is_swallowed(self):
        collector = MagicMock()
        collector.record_model_call.side_effect = RuntimeError("x")
        eng = _engine(metrics_collector=collector)
        eng._record_metrics(_job(), success=False)  # must not raise


# --------------------------------------------------------------------------
# execute() branches
# --------------------------------------------------------------------------
class TestExecuteBranches:
    def test_no_gateway_returns_failure(self):
        eng = ExecutionEngine(model_gateway=None, workspace_path=tempfile.mkdtemp())
        result = eng.execute(_job())
        assert result.exit_code == 1
        assert "No model gateway" in result.result_summary

    def test_budget_denial_short_circuits(self):
        guard = MagicMock()
        guard.check_all_limits.return_value = {"allowed": False, "reason": "no funds"}
        del guard.try_charge
        gw = MagicMock()
        eng = _engine(gateway=gw, budget_guard=guard)
        with patch.object(engine_mod, "_is_git_repo", return_value=False):
            result = eng.execute(_job())
        assert result.exit_code == 1
        assert "no funds" in result.result_summary
        gw.call_model.assert_not_called()

    def test_model_call_exception_returns_failure(self):
        gw = MagicMock()
        gw.call_model.side_effect = RuntimeError("network down")
        collector = MagicMock()
        eng = _engine(gateway=gw, metrics_collector=collector)
        with patch.object(engine_mod, "_is_git_repo", return_value=False):
            result = eng.execute(_job())
        assert result.exit_code == 1
        assert "network down" in result.result_summary
        # failure metric recorded
        assert collector.record_model_call.call_args.kwargs["success"] is False

    def test_empty_output_returns_failure(self):
        gw = MagicMock()
        gw.call_model.return_value = MagicMock(content="   ")
        eng = _engine(gateway=gw)
        with patch.object(engine_mod, "_is_git_repo", return_value=False):
            result = eng.execute(_job())
        assert result.exit_code == 1
        assert "empty output" in result.result_summary

    def test_no_changes_parsed_returns_failure(self):
        gw = MagicMock()
        gw.call_model.return_value = MagicMock(content="prose with no file markers")
        eng = _engine(gateway=gw)
        with patch.object(engine_mod, "_is_git_repo", return_value=False):
            result = eng.execute(_job())
        assert result.exit_code == 1
        assert "No changes parsed" in result.result_summary

    def test_file_write_happy_path_non_git(self):
        gw = MagicMock()
        gw.call_model.return_value = MagicMock(
            content="```\nFILE: out.py\nprint('ok')\n```"
        )
        ws = tempfile.mkdtemp()
        eng = _engine(gateway=gw, workspace=ws)
        with patch.object(engine_mod, "_is_git_repo", return_value=False), \
             patch.object(engine_mod, "_run_tests", return_value=(0, "passed")):
            result = eng.execute(_job())
        assert result.exit_code == 0
        assert "out.py" in result.result_summary
        assert "not a git repository" in result.result_summary
        with open(os.path.join(ws, "out.py")) as f:
            assert "print('ok')" in f.read()

    def test_git_path_commits_and_creates_branch(self):
        gw = MagicMock()
        gw.call_model.return_value = MagicMock(
            content="```\nFILE: out.py\nbody\n```"
        )
        eng = _engine(gateway=gw)
        with patch.object(engine_mod, "_is_git_repo", return_value=True), \
             patch.object(engine_mod, "_git_create_branch", return_value=True) as mk, \
             patch.object(engine_mod, "_git_commit", return_value="abc12345"), \
             patch.object(engine_mod, "_git_current_branch", return_value="gludd/x"), \
             patch.object(engine_mod, "_run_tests", return_value=(0, "ok")):
            result = eng.execute(_job())
        assert result.exit_code == 0
        assert "abc12345" in result.result_summary
        assert "commit:abc12345" in result.artifacts
        mk.assert_called_once()

    def test_diff_block_dispatches_to_apply_unified_diff(self):
        gw = MagicMock()
        gw.call_model.return_value = MagicMock(
            content="```diff\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n```"
        )
        eng = _engine(gateway=gw)
        with patch.object(engine_mod, "_is_git_repo", return_value=False), \
             patch.object(ExecutionEngine, "_apply_unified_diff", return_value=["x.py"]) as apply, \
             patch.object(engine_mod, "_run_tests", return_value=(0, "ok")):
            result = eng.execute(_job())
        apply.assert_called_once()
        assert result.exit_code == 0
        assert "x.py" in result.result_summary


# --------------------------------------------------------------------------
# execute_async() branches + defer_commit
# --------------------------------------------------------------------------
class TestExecuteAsync:
    async def test_no_gateway_returns_failure(self):
        eng = ExecutionEngine(model_gateway=None, workspace_path=tempfile.mkdtemp())
        result = await eng.execute_async(_job())
        assert result.exit_code == 1
        assert "No model gateway" in result.result_summary

    async def test_budget_denial_short_circuits(self):
        guard = MagicMock()
        guard.check_all_limits.return_value = {"allowed": False, "reason": "nope"}
        del guard.try_charge
        gw = MagicMock()
        eng = _engine(gateway=gw, budget_guard=guard)
        with patch.object(engine_mod, "_is_git_repo", return_value=False):
            result = await eng.execute_async(_job())
        assert result.exit_code == 1
        assert "nope" in result.result_summary

    async def test_empty_output_returns_failure(self):
        gw = MagicMock()
        gw.call_model.return_value = MagicMock(content="")
        eng = _engine(gateway=gw)
        with patch.object(engine_mod, "_is_git_repo", return_value=False):
            result = await eng.execute_async(_job())
        assert result.exit_code == 1
        assert "empty output" in result.result_summary

    async def test_no_changes_returns_failure(self):
        gw = MagicMock()
        gw.call_model.return_value = MagicMock(content="no markers here")
        eng = _engine(gateway=gw)
        with patch.object(engine_mod, "_is_git_repo", return_value=False):
            result = await eng.execute_async(_job())
        assert result.exit_code == 1
        assert "No changes parsed" in result.result_summary

    async def test_happy_path_defers_commit_when_git(self):
        gw = MagicMock()
        gw.call_model.return_value = MagicMock(content="```\nFILE: out.py\nb\n```")
        eng = _engine(gateway=gw)
        with patch.object(engine_mod, "_is_git_repo", return_value=True), \
             patch.object(engine_mod, "_git_create_branch", return_value=True), \
             patch.object(ExecutionEngine, "defer_commit") as defer, \
             patch.object(engine_mod, "_run_tests", return_value=(0, "ok")):
            result = await eng.execute_async(_job())
        assert result.exit_code == 0
        assert "deferred to background" in result.result_summary
        defer.assert_called_once()

    async def test_defer_commit_schedules_background_task(self):
        eng = _engine()
        with patch.object(engine_mod, "_git_commit_async") as commit_async:
            async def _fake(path, msg):
                return "sha12345"
            commit_async.side_effect = _fake
            eng.defer_commit("/tmp", "msg")
            assert len(eng._background_tasks) == 1
            # let the scheduled task run to completion
            await asyncio.gather(*list(eng._background_tasks))
        assert len(eng._background_tasks) == 0
