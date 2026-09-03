"""Observability guardrails: "an unseen event is not an event."

User-mandated invariant (2026-06-15, codified in AGENTS.md "No unseen events"):
no long-running operation in this repo's tooling may run silently. Every long
phase must STREAM its output (``tee``) or emit a periodic HEARTBEAT / progress
marker, so a human (or agent) watching always sees forward motion. A process
whose progress cannot be observed is treated as broken.

These tests parse the Makefile and FAIL if a future change reintroduces a
silent long-running operation (the exact class of defect that produced a
16-minute black-box gate and a heartbeat-less CI poller).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
RUN_GATE_SH = ROOT / "scripts" / "run_gate.sh"
SERIAL_SHARD_RUNNER = ROOT / "scripts" / "run_ci_shards_serial.py"


def _recipe(target: str) -> str:
    """Return the indented recipe body for a single Makefile target."""
    lines = MAKEFILE.read_text().splitlines()
    body: list[str] = []
    in_target = False
    for line in lines:
        if re.match(rf"^{re.escape(target)}:", line):
            in_target = True
            continue
        if in_target:
            # A new non-indented, non-blank line ends the recipe.
            if line and not line[0].isspace():
                break
            body.append(line)
    return "\n".join(body)


class TestNoUnseenEvents:
    def test_gate_test_phase_streams_via_tee(self) -> None:
        """The full-suite phase must tee so a running gate is observable.

        The gate target now delegates its test phase to scripts/run_gate.sh
        (collision-proof: flock + unique basetemp + EXIT trap). The invariant is
        preserved: the full suite runs through the exact named CI shards and its
        output is still streamed through tee.
        We verify:
          1. The Makefile gate recipe calls run_gate.sh (delegation wired).
          2. run_gate.sh delegates to the serial named-shard runner.
          3. Every shard streams owned-process heartbeats and worker-death state.
          4. run_gate.sh pipes through tee (output is never a silent black box).
        """
        gate_body = _recipe("gate")
        assert "run_gate.sh" in gate_body, (
            "gate target must delegate its test phase to scripts/run_gate.sh"
        )
        run_gate_text = RUN_GATE_SH.read_text()
        assert "run_ci_shards_serial.py" in run_gate_text, (
            "scripts/run_gate.sh must delegate to the complete named-shard runner"
        )
        serial_runner = SERIAL_SHARD_RUNNER.read_text()
        for marker in (
            "SHARD-HEARTBEAT",
            "WORKER-DEATH",
            "start_new_session=True",
            "OWNED-PYTEST-RESULT",
        ):
            assert marker in serial_runner, (
                f"serial named shards must retain observable owned cleanup: {marker}"
            )
        assert "tee" in run_gate_text, (
            "scripts/run_gate.sh MUST pipe its output through tee so a "
            "backgrounded gate is never a silent black box "
            "(regression of the 16-min-silence defect)"
        )

    def test_gate_full_suite_is_never_silenced(self) -> None:
        body = _recipe("gate")
        for line in body.splitlines():
            if "pytest tests/" in line:
                assert "/dev/null" not in line, (
                    f"gate must not pipe the full suite to /dev/null: {line.strip()!r}"
                )

    def test_env_write_gate_phases_stream_bounded_checker_output(self) -> None:
        invocations = [
            line
            for line in MAKEFILE.read_text().splitlines()
            if "$(MAKE) --no-print-directory check-test-env-writes" in line
        ]

        assert len(invocations) == 3
        for line in invocations:
            assert "/dev/null" not in line
            assert "scripts/stream_command.py --log .gate-logs/" in line
            assert "&& echo \"PASS\"" in line
            assert "touch .gate-" in line

    def test_gate_emits_a_progress_marker_per_phase(self) -> None:
        """Each gate phase must print a stdout marker as it starts (heartbeat)."""
        body = _recipe("gate")
        markers = re.findall(r"=== GATE PHASE:", body)
        assert len(markers) >= 5, (
            f"gate must emit a per-phase stdout progress marker (lint/typecheck/"
            f"collect/test/smoke); found {len(markers)}"
        )

    def test_ci_poll_loop_has_a_heartbeat(self) -> None:
        """The CI wait poller must print a heartbeat every cycle, not sleep silently."""
        body = _recipe("ci-wait-anon")
        assert "sleep" in body, "ci-wait-anon is expected to be a poll loop"
        assert "heartbeat" in body.lower(), (
            "ci-wait-anon MUST print a heartbeat each poll cycle — a silent "
            "sleep loop is an unseen event (regression of the silent-poller defect)"
        )

    def test_disk_reclaim_delegates_to_bounded_heartbeat_guard(self) -> None:
        body = _recipe("disk-reclaim")

        assert "$(MAKE) --no-print-directory disk-guard" in body, (
            "disk-reclaim must use the bounded disk-guard implementation so cache "
            "pruning emits heartbeats and cannot stall indefinitely"
        )
        assert "uv cache prune" not in body

    def test_disk_status_uses_workspace_volume_and_lists_generated_footprints(
        self,
    ) -> None:
        body = _recipe("disk")

        assert "df -h ." in body
        assert "df -h /" not in body
        for generated_path in (".gate-logs", ".venv", ".cache"):
            assert generated_path in body
        assert ".gate-logs/*" in body
        assert ".opencode" in body
        assert ".git" in body
        assert "infra/terraform/*" in body

    def test_uv_cache_prune_status_exposes_pid_parent_and_elapsed_time(self) -> None:
        body = _recipe("uv-cache-prune-status")

        for field in ("pid=", "ppid=", "etime=", "command="):
            assert field in body
        assert "[u]v cache prune" in body

    def test_pid_reaper_only_accepts_orphaned_uv_cache_prune(self) -> None:
        body = _recipe("kill-project-pid")

        assert "uv cache prune" in body
        assert '"$$ppid" = "1"' in body
        assert "Refusing to kill non-orphan uv cache prune" in body
        assert "make gate" in body
        assert "_gate-refresh-body" in body
        assert "Refusing to kill non-orphan gate" in body
        assert "uv run python -m pytest tests/unit/" in body
        assert "Refusing to kill non-orphan pytest" in body
        assert '! /bin/kill -0 "$$ppid"' in body
        assert "python -m general_ludd.cli daemon" in body
        assert "/Users/shawnwilson/tmp/pytest-of-shawnwilson/" in body
        assert "Refusing to kill non-orphan test daemon" in body

    def test_force_gate_kill_uses_background_pid_without_global_tmp_deletion(self) -> None:
        body = _recipe("kill-gate-force")

        assert ".gate-background.pid" in body
        assert "rm -rf /tmp/gludd-gate-" not in body

    def test_ci_poll_waits_on_run_level_conclusion(self) -> None:
        """The poller must trust the RUN-level conclusion, not a job snapshot.

        Regression guard: the old poller declared "RUN GREEN" as soon as the
        currently-visible jobs completed, but dependent jobs (artifact build)
        appear later — so it false-greened a run that actually FAILED. The run
        object's own `conclusion` is only finalized when the whole run is done.
        """
        body = _recipe("ci-wait-anon")
        assert "d.get('conclusion')" in body, (
            "ci-wait-anon MUST read the run-level conclusion, not infer green "
            "from a snapshot of visible jobs (that false-greened a failing run)"
        )
        assert "RUN_CONCLUSION" in body
        assert "exit 1" in body, (
            "a non-success run conclusion must surface as a non-zero exit"
        )

    def test_no_full_suite_pytest_to_devnull_anywhere(self) -> None:
        """No recipe may RUN the full suite while discarding output to /dev/null.

        Only actual pytest invocations count — `pkill`/`pgrep` lines reference
        'pytest tests/' as a process *pattern*, not a run, and their `2>/dev/null`
        silences the kill, not test output.
        """
        offenders = []
        for line in MAKEFILE.read_text().splitlines():
            if "pkill" in line or "pgrep" in line:
                continue
            if re.search(r"-m pytest tests/|run pytest tests/", line) and "/dev/null" in line:
                offenders.append(line.strip())
        assert not offenders, f"full-suite pytest run silenced to /dev/null: {offenders}"

    def test_smoke_in_gate_surfaces_failure_log(self) -> None:
        """A failed smoke phase must tail its log, not swallow it to /dev/null."""
        body = _recipe("gate")
        # The smoke invocation captures to a log; on failure it must surface a tail.
        assert "gludd-gate-smoke.log" in body, (
            "gate smoke output must be captured to a log file (not /dev/null) so "
            "failures are inspectable"
        )
        assert "tail -20 /tmp/gludd-gate-smoke.log" in body, (
            "gate must tail the smoke log on failure so the cause is visible"
        )


class TestNoSilentStalls:
    """No operation may hang silently — enforced at EVERY layer (universal stall
    guard, not a one-off): per-test timeout, a command watchdog, and CI job caps.
    """

    def test_pytest_has_global_per_test_timeout(self) -> None:
        cfg = (ROOT / "pyproject.toml").read_text()
        assert "[tool.pytest.ini_options]" in cfg
        m = re.search(r"^timeout\s*=\s*(\d+)", cfg, re.MULTILINE)
        assert m and int(m.group(1)) > 0, (
            "pyproject [tool.pytest.ini_options] MUST set a global per-test "
            "`timeout` so one hanging test can never freeze the whole suite"
        )

    def test_pytest_timeout_dependency_declared(self) -> None:
        cfg = (ROOT / "pyproject.toml").read_text()
        assert "pytest-timeout" in cfg, "pytest-timeout must be a declared dev dependency"

    def test_run_watched_watchdog_exists(self) -> None:
        mk = MAKEFILE.read_text()
        assert "run-watched:" in mk, "Makefile must provide the run-watched stall watchdog"
        for token in ("STALL_SECS", "MAX_SECS", "RESULT=STALLED", "kill"):
            assert token in mk, f"run-watched watchdog is missing {token!r}"

    def test_observed_commands_share_one_status_and_tail_mechanism(self) -> None:
        coverage = _recipe("coverage-files")
        test_count = _recipe("test-count")
        collect = _recipe("collect-check")
        watched = _recipe("run-watched")

        for label, body in (
            ("coverage-files", coverage),
            ("test-count", test_count),
            ("collect-check", collect),
            ("run-watched", watched),
        ):
            assert "scripts/stream_command.py" in body
            assert f"--label {label}" in body
            assert "--root \"$(OBSERVED_ROOT)\"" in body
            assert "--retain-runs \"$(OBSERVED_RETAIN_RUNS)\"" in body

        assert "--pytest-trace" in coverage
        assert "-p scripts.xdist_trace_plugin" in coverage
        assert "-W error" in coverage
        assert "$(COVERAGE_REPORT).tmp." in coverage
        assert 'mv "$$GLUDD_COVERAGE_REPORT_WORK" "$(COVERAGE_REPORT)"' in coverage
        assert "--quiet" in test_count
        assert "--quiet" in collect
        assert "scripts/collection_lock.py --run" in collect
        assert "/tmp/gludd-collect-output.txt" not in collect
        assert 'exit "$$RC"' in collect
        assert "--quiet-secs \"$(STALL_SECS)\"" in watched
        assert "--max-secs \"$(MAX_SECS)\"" in watched
        assert '$(if $(RUN_ID),--run-id "$(RUN_ID)",)' in watched
        assert "while kill -0" not in watched

        status = _recipe("observed-status")
        tail = _recipe("observed-tail")
        assert "--status" in status
        assert "--stale-secs \"$(OBSERVED_STALE_SECS)\"" in status
        assert "--tail \"$(OBSERVED_TAIL_LINES)\"" in tail

    def test_every_ci_job_has_timeout_minutes(self) -> None:
        import yaml

        wf = yaml.safe_load((ROOT / ".github" / "workflows" / "build.yml").read_text())
        jobs = wf.get("jobs", {})
        assert jobs, "build.yml has no jobs"
        missing = [name for name, j in jobs.items() if "timeout-minutes" not in j]
        assert not missing, (
            f"CI jobs with no timeout-minutes (could hang for hours): {missing}"
        )
