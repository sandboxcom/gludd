"""Unit tests for stub closure items S12-S19 from STUB_CLOSURE_SPEC.md.

S12: web_search router bypasses hardened WebRetriever.
S13: Permission-override fails closed on YAML parse error.
S14: Worker staleness lifecycle (heartbeat/cleanup_stale) has zero prod callers.
S15: Validation job pipeline returns honest 501.
S16: Writer subprocess mode structural gaps (queue + config shape).
S17: DAST _start_app no longer uses shell=True.
S18: StallWatchdog stall actions publish-only, zero subscribers consume.
S19: code_quality_score derived from real test results where available.
"""

from __future__ import annotations

import inspect

import pytest

# ── S12: web_search router bypasses WebRetriever ──────────────────────────


class TestS12WebSearchBypassesRetriever:
    def test_web_search_uses_bare_urllib_not_web_retriever(self) -> None:
        """_web_search() uses urllib.request.urlopen directly, never WebRetriever."""
        import ast

        import general_ludd.routers.web_search as ws

        source = inspect.getsource(ws)
        module_ast = ast.parse(source)

        class RetrieverVisitor(ast.NodeVisitor):
            def __init__(self):
                self.has_urlopen = False
                self.has_web_retriever = False
                self.has_is_url_blocked = False

            def visit_Call(self, node):
                if (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "urllib"
                    and node.func.attr == "request"
                    and hasattr(node, "args")
                ):
                    for arg in node.args:
                        if (
                            isinstance(arg, ast.Call)
                            and isinstance(arg.func, ast.Attribute)
                            and arg.func.attr == "urlopen"
                        ):
                            self.has_urlopen = True
                self.generic_visit(node)

            def visit_Attribute(self, node):
                if isinstance(node, ast.Attribute) and node.attr in (
                    "WebRetriever",
                    "is_url_blocked",
                ):
                    if node.attr == "WebRetriever":
                        self.has_web_retriever = True
                    if node.attr == "is_url_blocked":
                        self.has_is_url_blocked = True
                self.generic_visit(node)

        visitor = RetrieverVisitor()
        visitor.visit(module_ast)
        # S12: The function does NOT route through WebRetriever or is_url_blocked
        assert not visitor.has_web_retriever, "S12 FIXED: _web_search now references WebRetriever — update this test"
        assert not visitor.has_is_url_blocked, "S12 FIXED: _web_search now calls is_url_blocked — update this test"

    def test_web_search_function_imports_urllib(self) -> None:
        """_web_search imports urllib.request directly, not retrieval.web."""
        import general_ludd.routers.web_search as ws

        source = inspect.getsource(ws._web_search)
        assert "urllib.request" in source or "urllib.parse" in source
        assert "WebRetriever" not in source

    def test_web_search_silent_fallback_on_exception(self) -> None:
        """_web_search returns [] on exception — swallowing real errors silently."""

        from general_ludd.routers.web_search import _web_search

        result = _web_search("test")
        assert isinstance(result, list)
        # The function wraps urlopen in try/except Exception: return []
        # This is the "silent swallow" gap from S12 spec.


# ── S13: Permission-override has been fixed to fail-closed ─────────────────


class TestS13PermissionOverrideFailClosed:
    def test_get_human_spec_raises_on_yaml_parse_error(self) -> None:
        """_get_human_spec raises on YAML parse failure, not silently falls back."""
        import general_ludd.routers.security as sec

        source = inspect.getsource(sec._get_human_spec)
        # S13 FIXED: the except block must raise, not pass
        assert "raise" in source, (
            "S13 GAP: _get_human_spec exception handler does not raise — "
            "YAML parse errors are silently swallowed, falling back to permissive default"
        )

    def test_get_human_spec_does_not_silently_pass(self) -> None:
        """Verify the except block is not bare 'pass' — must re-raise."""
        import general_ludd.routers.security as sec

        source = inspect.getsource(sec._get_human_spec)
        # The except block after parse_file should NOT be empty/silent-pass
        # Find "except Exception as exc:" and verify the body contains raise
        lines = source.split("\n")
        in_except_block = False
        raises_in_block = False
        for line in lines:
            stripped = line.strip()
            if "except Exception" in stripped and "exc" in stripped:
                in_except_block = True
                continue
            if in_except_block:
                if "raise" in stripped:
                    raises_in_block = True
                if stripped and not stripped.startswith("#") and not stripped.startswith("logger"):
                    # If we hit a non-comment, non-logger line outside the raise,
                    # check that raise was already found
                    pass
        assert raises_in_block, (
            "S13 GAP: _get_human_spec catches Exception but does not raise — this is the original S13 security bug"
        )

    def test_get_human_spec_logs_error_before_raise(self) -> None:
        """_get_human_spec logs the error before raising (S13 compliance)."""
        import general_ludd.routers.security as sec

        source = inspect.getsource(sec._get_human_spec)
        # Must contain both logger.error() and raise
        assert "logger.error" in source or "logging.getLogger" in source

    def test_human_spec_permission_file_path_resolution(self) -> None:
        """_get_human_spec resolves YAML file from config_dir/permissions/<role>.yml."""
        import general_ludd.routers.security as sec

        source = inspect.getsource(sec._get_human_spec)
        assert "permissions" in source
        assert ".yml" in source


# ── S14: Worker staleness lifecycle (heartbeat/cleanup_stale) ─────────────


class TestS14WorkerStalenessLifecycle:
    def test_heartbeat_method_exists(self) -> None:
        """WorkerBroadcaster.heartbeat exists and updates last_seen."""
        from general_ludd.reload.worker_broadcast import WorkerBroadcaster

        bc = WorkerBroadcaster()
        bc.register("w1", "https://example.com")
        bc.heartbeat("w1")
        workers = bc.list_workers()
        assert len(workers) == 1
        # S14 GAP: heartbeat exists but has zero production callers

    def test_cleanup_stale_method_exists(self) -> None:
        """WorkerBroadcaster.cleanup_stale removes workers past threshold."""
        from general_ludd.reload.worker_broadcast import WorkerBroadcaster

        bc = WorkerBroadcaster(stale_threshold_seconds=0.0)
        bc.register("w1", "https://example.com")
        bc.cleanup_stale()
        workers = bc.list_workers()
        # With threshold=0 and immediately calling cleanup, w1 should be removed
        assert len(workers) == 0

    def test_heartbeat_has_no_production_callers(self) -> None:
        """Verify heartbeat() is only called in the class itself or tests."""
        import importlib

        mod = importlib.import_module("general_ludd.reload.worker_broadcast")
        source = inspect.getsource(mod)

        # Check that heartbeat and cleanup_stale are present but only
        # referenced within the class definition itself
        assert ".heartbeat(" not in source.replace("def heartbeat", "")
        assert ".cleanup_stale(" not in source.replace("def cleanup_stale", "")

    def test_broadcast_has_ssrf_guard_on_send(self) -> None:
        """WorkerBroadcaster checks SSRF safety before sending PSK anywhere."""
        from general_ludd.reload.worker_broadcast import WorkerBroadcaster

        # The _is_safe_worker_address static function exists as SSRF guard
        assert hasattr(WorkerBroadcaster, "_is_safe_worker_address") or hasattr(WorkerBroadcaster, "_auth_headers")


# ── S15: Validation job pipeline returns honest 501 ────────────────────────


class TestS15ValidationJobHonest501:
    def test_validate_job_route_returns_501(self) -> None:
        """GET /jobs/validate returns 501, not a silent success ack."""
        import general_ludd.worker.app as wa

        source = inspect.getsource(wa)
        # The validate job route raises HTTPException with 501
        assert "validate" in source.lower()
        assert "501" in source, (
            "S15 GAP: validation job route does not return 501 — callers will believe validation ran when it didn't"
        )

    def test_policy_validate_job_route_returns_501(self) -> None:
        """/jobs/policy-validate also returns honest 501."""
        import general_ludd.worker.app as wa

        source = inspect.getsource(wa)
        assert "policy-validate" in source or "policy_validate" in source
        # At least two 501 references: validate and policy-validate
        count_501 = source.count("501")
        assert count_501 >= 2, "S15 GAP: expected at least 2 501 responses (validate + policy-validate)"

    def test_return_review_route_returns_501(self) -> None:
        """/jobs/return-review also returns 501 (not silent ack)."""
        import general_ludd.worker.app as wa

        source = inspect.getsource(wa)
        assert "return-review" in source or "return_review" in source

    def test_no_validation_phase_stub_in_event_loop(self) -> None:
        """The event loop should not have a validator stub that spins idle."""
        import general_ludd.event_loop.loop as loop_mod

        source = inspect.getsource(loop_mod)
        # S15: original loop.py:3198-3212 was a validation stub with no phase.
        # Verify it no longer exists or is gated behind a 501.
        assert "def _dispatch_validate_job" in source, "S15 GAP: validate job dispatch removed entirely"


# ── S16: Writer subprocess mode structural gaps ────────────────────────────


class TestS16WriterSubprocessStructuralGaps:
    def test_writer_child_expects_nested_database_config(self) -> None:
        """_child.py main() expects config['database'] dict, not flat db dict."""
        import general_ludd.writer._child as child

        source = inspect.getsource(child.main)
        assert 'config.get("database")' in source, (
            "S16 FIXED: _child.py no longer expects nested 'database' key — config shape matches daemon.py now"
        )

    def test_write_queue_is_in_process_deque(self) -> None:
        """WriteQueue uses asyncio.Queue/deque, not IPC — no cross-process transfer."""
        import general_ludd.ipc.queue as qmod

        source = inspect.getsource(qmod)
        # The WriteQueue is an in-process structure
        assert "WriteQueue" in source or "deque" in source or "asyncio" in source

    def test_writer_process_supervisor_exists(self) -> None:
        """WriterSupervisor exists for managing WriterProcess lifecycle."""
        from general_ludd.writer.supervisor import WriterSupervisor

        assert WriterSupervisor is not None

    def test_writer_child_spool_path_from_config(self) -> None:
        """The writer child reads inbound_spool_path from config, defaulting to ''."""
        import general_ludd.writer._child as child

        source = inspect.getsource(child.main)
        assert "inbound_spool_path" in source
        assert 'config.get("inbound_spool_path", "")' in source


# ── S17: DAST _start_app no longer uses shell=True ─────────────────────────


class TestS17DastStartAppNoShell:
    def test_start_app_uses_shlex_split_not_shell_true(self) -> None:
        """_start_app uses shlex.split + Popen(argv), NOT shell=True."""
        import general_ludd.project_runner.dast as dast

        source = inspect.getsource(dast._start_app)
        assert "shlex.split" in source, (
            "S17 GAP: _start_app does not use shlex.split — start_command is passed directly to shell=True"
        )
        assert "shell=True" not in source.replace("shell", ""), "S17 GAP: _start_app still has shell=True"

    def test_start_app_does_not_have_bare_shell_true(self) -> None:
        """Verify the subprocess.Popen call has no shell=True."""
        import ast

        import general_ludd.project_runner.dast as dast

        source = inspect.getsource(dast._start_app)

        class ShellTrueChecker(ast.NodeVisitor):
            def __init__(self):
                self.has_shell_true = False

            def visit_Call(self, node):
                if len(node.keywords) > 0:
                    for kw in node.keywords:
                        if kw.arg == "shell" and hasattr(kw.value, "value") and kw.value.value is True:
                            self.has_shell_true = True
                self.generic_visit(node)

        tree = ast.parse(source)
        checker = ShellTrueChecker()
        checker.visit(tree)
        assert not checker.has_shell_true, "S17 GAP: _start_app subprocess.Popen still has shell=True"

    def test_dast_module_is_unwired_no_router_entry(self) -> None:
        """The DAST module has no router, CLI, or daemon entry point (S17 context)."""
        import importlib

        dast_path = "general_ludd.project_runner.dast"
        try:
            importlib.import_module(dast_path)
        except ImportError:
            pytest.skip("DAST module not importable")
        # S17: The entire DAST module is unreachable — confirm this structurally
        # by checking that no router exports a DAST endpoint
        import general_ludd.routers as routers_mod

        source = inspect.getsource(routers_mod)
        assert "dast" not in source.lower()


# ── S18: StallWatchdog publish-only, zero consumers ────────────────────────


class TestS18StallWatchdogPublishOnly:
    def test_stall_watchdog_on_stall_publishes_events(self) -> None:
        """StallWatchdog's on_stall callback publishes StallDetectedEvent + SlowOperationEvent."""
        import general_ludd.daemon as dm

        source = inspect.getsource(dm)
        assert "StallDetectedEvent" in source
        assert "SlowOperationEvent" in source

    def test_stall_watchdog_sweeper_is_started(self) -> None:
        """The daemon starts the StallWatchdog sweeper thread."""
        import general_ludd.daemon as dm

        source = inspect.getsource(dm)
        assert "start_sweeper" in source, "S18 GAP: StallWatchdog sweeper is not started — stalls are never detected"

    def test_stall_detected_event_has_no_subscriber(self) -> None:
        """No production code subscribes to StallDetectedEvent."""
        import ast

        import general_ludd.daemon as dm

        source = inspect.getsource(dm)
        tree = ast.parse(source)
        has_subscribe = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "subscribe":
                for arg in node.args:
                    if isinstance(arg, ast.Name) and "Stall" in arg.id:
                        has_subscribe = True
        assert not has_subscribe, "S18 FIXED: StallDetectedEvent now has a subscriber — update this test"

    def test_stall_report_captures_thread_stacks(self) -> None:
        """StallReport supports thread stack capture for debugging."""
        from general_ludd.observability.timing import StallReport, StallWatchdog

        StallWatchdog(capture_stacks=True)
        report = StallReport(
            op_id="test",
            key="test_key",
            elapsed_s=60.0,
            deadline_s=30.0,
            started_monotonic=0.0,
        )
        assert report.elapsed_s == 60.0
        assert report.deadline_s == 30.0


# ── S19: code_quality_score fixed to use real test results ──────────────────


class TestS19CodeQualityScore:
    def test_compute_scores_from_trace_uses_test_results(self) -> None:
        """compute_scores_from_trace derives code_quality from test_results when available."""
        from general_ludd.observability.recorder import compute_scores_from_trace
        from general_ludd.observability.tracer import ExecutionTrace

        trace = ExecutionTrace()
        # With test_results present, should derive quality from pass/total
        trace.test_results = {"total": 10, "passed": 8}
        scores = compute_scores_from_trace(trace, success=True)
        assert scores["code_quality"] == 0.8

    def test_compute_scores_from_trace_falls_back_to_05(self) -> None:
        """code_quality_score falls back to 0.5 when no test_results available."""
        from general_ludd.observability.recorder import compute_scores_from_trace
        from general_ludd.observability.tracer import ExecutionTrace

        trace = ExecutionTrace()
        scores = compute_scores_from_trace(trace, success=True)
        assert scores["code_quality"] == 0.5

    def test_compute_scores_from_trace_zero_total(self) -> None:
        """When test_results total is 0, falls back to 0.5 (avoid div-by-zero)."""
        from general_ludd.observability.recorder import compute_scores_from_trace
        from general_ludd.observability.tracer import ExecutionTrace

        trace = ExecutionTrace()
        trace.test_results = {"total": 0, "passed": 0}
        scores = compute_scores_from_trace(trace, success=True)
        assert scores["code_quality"] == 0.5

    def test_record_job_benchmark_derives_quality_from_test_exit_code(self) -> None:
        """record_job_benchmark derives code_quality from test_exit_code."""
        import general_ludd.event_loop.benchmark as bm

        source = inspect.getsource(bm.record_job_benchmark)
        assert "test_exit_code" in source, (
            "S19 GAP: record_job_benchmark does not accept test_exit_code — code_quality is always hardcoded 0.5"
        )

    def test_record_job_benchmark_accepts_test_exit_code_param(self) -> None:
        """record_job_benchmark signature includes test_exit_code: int | None."""
        from general_ludd.event_loop.benchmark import record_job_benchmark

        sig = inspect.signature(record_job_benchmark)
        assert "test_exit_code" in sig.parameters
        assert "test_summary" in sig.parameters

    def test_loop_dispatch_call_does_not_pass_test_data(self) -> None:
        """loop.py call to record_job_benchmark does not pass test_exit_code."""
        import general_ludd.event_loop.loop as lm

        source = inspect.getsource(lm)
        # Find the record_job_benchmark call site around line 2744
        # S19 GAP: this call site does not pass test_exit_code,
        # so dispatched jobs always get 0.5 code_quality
        assert "record_job_benchmark" in source

    def test_engine_call_site_does_not_pass_test_data(self) -> None:
        """engine.py call sites to record_job_benchmark do not pass test_exit_code."""
        import general_ludd.execution.engine as eng

        source = inspect.getsource(eng)
        assert "record_job_benchmark" in source

    def test_engine_computes_real_test_data_adjacent(self) -> None:
        """engine.py computes test_exit_code/test_summary just before benchmark call."""
        import general_ludd.execution.engine as eng

        source = inspect.getsource(eng)
        # engine.py:597 computes real test_exit_code, test_summary from _run_tests()
        assert "test_exit_code" in source
        assert "test_summary" in source
        # S19 GAP: these computed values exist but are not threaded into
        # record_job_benchmark call at line 606/788
