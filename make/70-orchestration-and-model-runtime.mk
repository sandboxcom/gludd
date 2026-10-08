# --- Orchestration planner (#32) ---
# Reads a JSON work-list (file path via WORK= or stdin) and prints which items
# can run in parallel NOW (batch 0) plus the full ordered batch plan.
# Usage: make plan WORK=/tmp/example.json
WORK ?=
plan:
	@if [ -n "$(WORK)" ]; then \
		$(UV) run python scripts/plan_work.py "$(WORK)"; \
	else \
		$(UV) run python scripts/plan_work.py; \
	fi

audit-findings:
	@$(UV) run python -c "from general_ludd.quality.preflight import run_completion_audit as a; r=a(); print('pct', r['completion_pct'], 'failed', r['failed_count']); [print(f['class_name'], f['file']) for f in r['findings']]"

release-validate:
	@$(UV) run python -c "import json; from general_ludd.runtime.release_orchestrator import build_and_validate_release as b; from general_ludd import __version__ as v; print(json.dumps(b(version=v, output_dir='dist', build_container=False), indent=2))"

# ---------------------------------------------------------------------------
# Non-blocking async gate (.gate-status: RUNNING/PASS/FAIL, flock-guarded)
# ---------------------------------------------------------------------------
# Launch the gate fully detached — main thread returns immediately.
# A second call is refused (flock-exclusive) if one is already running.
# Override GATE_CMD to inject a fake gate in tests (default: scripts/run_gate.sh).
# STATUS_FILE / LOCK_FILE can also be overridden for test isolation.
gate-async:
	@bash scripts/gate_async.sh "$(REF)"

# Print the current .gate-status file (RUNNING/PASS/FAIL).
gate-status:
	@if [ -f .gate-status ]; then cat .gate-status; else echo "(no .gate-status found)"; fi

# Launch the gate through the repository-owned Python session launcher. It
# atomically publishes the exact PID/start-token/session/log identity, starts a
# bounded watcher, and returns immediately. The watcher exits with the gate and
# removes .gate-background.pid only when the receipt still names its exact PID.
_GATE_ENTRY_MAKEFILE := $(abspath $(firstword $(MAKEFILE_LIST)))
_GATE_REPOSITORY_ROOT := $(abspath $(dir $(_GATE_ENTRY_MAKEFILE)))
gate-background:
	@$(UV) run --project "$(_GATE_REPOSITORY_ROOT)" python "$(_GATE_REPOSITORY_ROOT)/scripts/start_gate_background.py" \
		--project-root "$(CURDIR)" \
		--make-command "$(_GATE_MAKE)" \
		--makefile "$(_GATE_ENTRY_MAKEFILE)" \
		--timeout-seconds "$(GATE_TIMEOUT)" \
		--validate-only "$(GATE_BACKGROUND_VALIDATE_ONLY)"

# Managed command runners may reap detached descendants as soon as their root
# command exits. Keep that root Make invocation alive while the ordinary
# non-blocking launcher owns the gate; gate-wait streams bounded phase
# heartbeats and preserves the terminal result.
.PHONY: gate-background-observed
gate-background-observed:
	@case "$(GATE_BACKGROUND_OBSERVED_VALIDATE_ONLY)" in 0|1) ;; *) echo "GATE_BACKGROUND_OBSERVED_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac; \
	if [ "$(GATE_BACKGROUND_OBSERVED_VALIDATE_ONLY)" = "1" ]; then \
		echo "gate-background-observed: VALIDATE launch timeout=$${GATE_TIMEOUT:-21600}s poll=$(GATE_POLL_INTERVAL)s"; \
		exit 0; \
	fi; \
	$(_GATE_MAKE) --no-print-directory gate-background GATE_TIMEOUT="$(GATE_TIMEOUT)" || exit $$?; \
	EXPECTED_PID=$$(cat .gate-background.pid 2>/dev/null || echo ""); \
	if [ -z "$$EXPECTED_PID" ]; then echo "gate-background-observed: launch published no PID"; exit 1; fi; \
	$(_GATE_MAKE) --no-print-directory gate-wait GATE_EXPECTED_PID="$$EXPECTED_PID" GATE_POLL_INTERVAL="$(GATE_POLL_INTERVAL)"

# Launch gate-lite detached via nohup; returns PID immediately (<1s).
# Writes output to .gate-logs/gate-lite-<ts>.log, PID to .gate-lite-background.pid.
gate-lite-background:
	@mkdir -p .gate-logs
	@GATE_TIMEOUT_OVERRIDE=$${GATE_LITE_TIMEOUT:-1800}; \
	STALE_PID=$$(cat .gate-lite-background.pid 2>/dev/null || echo ""); \
	GATE_PID_NOW=$$(date +%s); \
	if [ -n "$$STALE_PID" ]; then \
		if kill -0 "$$STALE_PID" 2>/dev/null; then \
			GATE_MTIME=$$(stat -f %m .gate-lite-background.pid 2>/dev/null || stat -c %Y .gate-lite-background.pid 2>/dev/null || echo 0); \
			ELAPSED=$$(( GATE_PID_NOW - GATE_MTIME )); \
			if [ "$$ELAPSED" -gt "$$GATE_TIMEOUT_OVERRIDE" ]; then \
				echo "[gate-lite-background] WARNING: existing gate-lite running for $$ELAPSED s (>$$GATE_TIMEOUT_OVERRIDE s) - auto-killing staled process"; \
				$(MAKE) gate-lite-kill; \
			else \
				echo "[gate-lite-background] gate-lite already running (pid=$$STALE_PID elapsed=$$ELAPSED s) - refusing to launch duplicate"; \
				exit 0; \
			fi; \
		else \
			echo "[gate-lite-background] removing stale PID file (pid=$$STALE_PID not alive)"; \
			rm -f .gate-lite-background.pid; \
		fi; \
	fi
	@nohup $(MAKE) gate-lite > .gate-logs/gate-lite-$$(date +%Y%m%d%H%M%S).log 2>&1 & echo $$! | tee .gate-lite-background.pid; \
	GATE_TIMEOUT_VAL=$${GATE_LITE_TIMEOUT:-1800}; \
	EXPECTED_PID=$$(cat .gate-lite-background.pid 2>/dev/null); \
	( sleep $$GATE_TIMEOUT_VAL; \
	  if [ -f .gate-lite-background.pid ]; then \
	    PID_TO_KILL=$$(cat .gate-lite-background.pid 2>/dev/null); \
	    if [ -n "$$PID_TO_KILL" ] && [ "$$PID_TO_KILL" = "$$EXPECTED_PID" ] && kill -0 "$$PID_TO_KILL" 2>/dev/null; then \
	      echo "GATE_TIMEOUT" > .gate-lite-status; \
	      echo "=== GATE-LITE: ABORTED (timeout $$GATE_TIMEOUT_VAL s) ===" >> .gate-logs/gate-lite-$$(ls -t .gate-logs/gate-lite-*.log 2>/dev/null | head -1); \
	      kill -TERM "$$PID_TO_KILL" 2>/dev/null; \
	      sleep 10; \
	      kill -KILL "$$PID_TO_KILL" 2>/dev/null; \
	      rm -f .gate-lite-background.pid; \
	      echo "[gate-lite-background-timeout] killed PID $$PID_TO_KILL after $$GATE_TIMEOUT_VAL s timeout"; \
	    fi; \
	  fi ) > /dev/null 2>&1 &

# Probe background gate: running/pass/fail + current phase + last 20 log lines + .gate-status.
gate-status-check:
	@PID="$(GATE_EXPECTED_PID)"; \
	if [ -z "$$PID" ]; then PID=$$(cat .gate-background.pid 2>/dev/null || echo ""); fi; \
	if [ -n "$$PID" ] && kill -0 "$$PID" 2>/dev/null; then \
		echo "RUNNING (pid=$$PID)"; \
		LOGF=$$(ls -t .gate-logs/gate-*.log 2>/dev/null | head -1); \
		if [ -n "$$LOGF" ]; then \
			PHASE=$$(grep '\[gate .*\] phase ' "$$LOGF" 2>/dev/null | tail -1 || echo "(no phase marker yet)"); \
			echo "Phase: $$PHASE"; \
			echo "--- last 20 lines ---"; \
			tail -20 "$$LOGF"; \
		fi; \
	elif [ -f .gate-status ]; then \
		STATUS=$$(cat .gate-status); \
		if printf '%s\n' "$$STATUS" | grep -q '^RUNNING'; then \
			echo "FINISHED: ORPHANED"; \
			echo "=== GATE: ABORTED ==="; \
			echo "reason=orphaned-running-status pid=$$PID"; \
		else \
			echo "FINISHED:"; printf '%s\n' "$$STATUS"; \
		fi; \
	else \
		echo "(no background gate found)"; \
	fi

# Poll the background gate every GATE_POLL_INTERVAL seconds until it terminates.
# Emits a timestamped heartbeat each cycle. Exits 0 on PASSED, 1 on FAILED/aborted.
gate-wait:
	@while true; do \
		OUT=$$( $(MAKE) --no-print-directory gate-status-check GATE_EXPECTED_PID="$(GATE_EXPECTED_PID)" 2>&1 ); \
		TS=$$(date +%H:%M:%S); \
		if echo "$$OUT" | grep -q '=== GATE: PASSED ==='; then \
			echo "[$$TS] $$OUT" | tail -30; exit 0; \
		elif echo "$$OUT" | grep -qE '=== GATE: (FAILED|ABORTED) ==='; then \
			echo "[$$TS] $$OUT" | tail -30; exit 1; \
		elif echo "$$OUT" | grep -q '^FINISHED'; then \
			echo "[$$TS] $$OUT" | tail -30; \
			if echo "$$OUT" | grep -qi PASS; then exit 0; else exit 1; fi; \
		else \
			PHASE=$$(echo "$$OUT" | grep -oE 'Phase: .*' | head -1); \
			echo "[$$TS] still running... $$PHASE"; \
			sleep $(GATE_POLL_INTERVAL); \
		fi; \
	done

# Return immediately with gate status (no polling).
gate-wait-report:
	@PID=$$(cat .gate-background.pid 2>/dev/null || echo ""); \
	if [ -z "$$PID" ]; then \
		echo "no gate running"; \
	elif kill -0 "$$PID" 2>/dev/null; then \
		$(MAKE) --no-print-directory gate-status-check; \
	else \
		echo "no gate running"; \
	fi

# Probe background gate-lite: running/pass/fail + current phase + last 20 log lines + .gate-lite-status.
gate-lite-status-check:
	@PID=$$(cat .gate-lite-background.pid 2>/dev/null || echo ""); \
	if [ -n "$$PID" ] && kill -0 "$$PID" 2>/dev/null; then \
		echo "RUNNING (pid=$$PID)"; \
		LOGF=$$(ls -t .gate-logs/gate-lite-*.log 2>/dev/null | head -1); \
		if [ -n "$$LOGF" ]; then \
			PHASE=$$(grep 'GATE-LITE PHASE' "$$LOGF" 2>/dev/null | tail -1 || echo "(no phase marker yet)"); \
			echo "Phase: $$PHASE"; \
			echo "--- last 20 lines ---"; \
			tail -20 "$$LOGF"; \
		fi; \
	elif [ -f .gate-lite-status ]; then \
		echo "FINISHED:"; cat .gate-lite-status; \
	else \
		echo "(no background gate-lite found)"; \
	fi

# Bounded snapshot of the latest gate logs. Never leaves a tail watcher behind.
GATE_TAIL_LINES ?= 80
gate-tail:
	@case "$(GATE_TAIL_LINES)" in ''|*[!0-9]*) echo "GATE_TAIL_LINES must be a positive integer"; exit 2;; esac; \
	if [ "$(GATE_TAIL_LINES)" -lt 1 ]; then echo "GATE_TAIL_LINES must be a positive integer"; exit 2; fi; \
	LOGF=$$(ls -t .gate-logs/gate-*.log 2>/dev/null | head -1); \
	if [ -n "$$LOGF" ]; then tail -n "$(GATE_TAIL_LINES)" "$$LOGF"; else echo "(no gate log found)"; fi

gate-lite-tail:
	@case "$(GATE_TAIL_LINES)" in ''|*[!0-9]*) echo "GATE_TAIL_LINES must be a positive integer"; exit 2;; esac; \
	if [ "$(GATE_TAIL_LINES)" -lt 1 ]; then echo "GATE_TAIL_LINES must be a positive integer"; exit 2; fi; \
	LOGF=$$(ls -t .gate-logs/gate-lite-*.log 2>/dev/null | head -1); \
	if [ -n "$$LOGF" ]; then tail -n "$(GATE_TAIL_LINES)" "$$LOGF"; else echo "(no gate-lite log found)"; fi

# List .gate-logs/*.log with mtime + PASS/FAIL/incomplete.
gate-logs:
	@mkdir -p .gate-logs
	@for f in .gate-logs/gate-*.log; do \
		if [ -f "$$f" ]; then \
			MTIME=$$(stat -f '%Sm' -t '%Y-%m-%d %H:%M:%S' "$$f" 2>/dev/null || stat -c '%y' "$$f" 2>/dev/null | cut -d. -f1); \
			if grep -q 'FAIL' "$$f" 2>/dev/null; then STATUS="FAIL"; \
			elif grep -q '=== GATE: PASSED ===' "$$f" 2>/dev/null; then STATUS="PASS"; \
			elif grep -q 'GATE:' "$$f" 2>/dev/null; then STATUS="FAIL"; \
			else STATUS="incomplete"; fi; \
			echo "$$MTIME  $$STATUS  $$f"; \
		fi; \
	done
	@for f in .gate-logs/gate-lite-*.log; do \
		if [ -f "$$f" ]; then \
			MTIME=$$(stat -f '%Sm' -t '%Y-%m-%d %H:%M:%S' "$$f" 2>/dev/null || stat -c '%y' "$$f" 2>/dev/null | cut -d. -f1); \
			if grep -q 'FAIL' "$$f" 2>/dev/null; then STATUS="FAIL"; \
			elif grep -q '=== GATE-LITE: PASSED ===' "$$f" 2>/dev/null; then STATUS="PASS"; \
			elif grep -q 'GATE-LITE:' "$$f" 2>/dev/null; then STATUS="FAIL"; \
			else STATUS="incomplete"; fi; \
			echo "$$MTIME  $$STATUS  $$f"; \
		fi; \
	done

# Force-kill one identity-verified gate tree: descendants first, bounded TERM,
# then KILL. The Python owner writes terminal status/evidence and releases only
# lock records whose PID identities belong to the verified tree.
gate-kill:
	@APPLY=1 /usr/bin/python3 scripts/kill_owned_gate.py

# Force-kill a running background gate-lite: SIGTERM then SIGKILL after 10s.
gate-lite-kill:
	@PID=$$(cat .gate-lite-background.pid 2>/dev/null || echo ""); \
	if [ -n "$$PID" ] && kill -0 "$$PID" 2>/dev/null; then \
		echo "[gate-lite-kill] sending SIGTERM to pid=$$PID"; \
		kill -TERM "$$PID" 2>/dev/null || true; \
		ELAPSED=0; \
		while [ $$ELAPSED -lt 10 ] && kill -0 "$$PID" 2>/dev/null; do sleep 1; ELAPSED=$$((ELAPSED+1)); done; \
		if kill -0 "$$PID" 2>/dev/null; then \
			echo "[gate-lite-kill] sending SIGKILL to pid=$$PID"; \
			kill -KILL "$$PID" 2>/dev/null || true; \
		fi; \
		rm -f .gate-lite-background.pid; \
		echo "[gate-lite-kill] done"; \
	else \
		echo "(no running background gate-lite found)"; \
	fi

# Kill any running background gate, remove stale PID, clean old gate logs (>24h).
gate-cleanup:
	@$(MAKE) gate-kill
	@$(MAKE) gate-lite-kill
	@rm -f .gate-background.pid .gate-lite-background.pid .gate-status.next .gate-status.running
	@echo "[gate-cleanup] removing gate and gate-lite logs older than 24h..."
	@find .gate-logs -name "gate-*.log" -mtime +0 2>/dev/null -delete
	@find .gate-logs -name "gate-lite-*.log" -mtime +0 2>/dev/null -delete
	@echo "[gate-cleanup] also removing coverage data..."
	@rm -f .gate-logs/coverage-branch.json .gate-logs/coverage-data-*.json .gate-logs/coverage-data-*.json.progress.json
	@echo "[gate-cleanup] done"

CLEAN_HF_CACHE_ROOT ?= $(GLUDD_SELF_IMPROVE_MODEL_CACHE)
CLEAN_HF_CACHE_REQUIRED_BYTES ?= 0
CLEAN_HF_CACHE_VALIDATE_ONLY ?= 1

.PHONY: clean-hf-cache
clean-hf-cache: ## Diagnose or reclaim only Gludd-owned unleased model artifacts
	@$(UV) run python scripts/clean_hf_cache.py \
		--cache-root "$(CLEAN_HF_CACHE_ROOT)" \
		--required-bytes "$(CLEAN_HF_CACHE_REQUIRED_BYTES)" \
		--validate-only "$(CLEAN_HF_CACHE_VALIDATE_ONLY)"

# ---------------------------------------------------------------------------
# Coverage audit: per-file coverage check with configurable threshold.
#   make audit-coverage [THRESHOLD=85] [SOURCE=src/general_ludd]
#
# Steps:
#   1. Run pytest with --cov=src/general_ludd --cov-report=json --cov-report=term-missing
#   2. Parse coverage.json, extract per-file percentages
#   3. Flag any file below THRESHOLD (default 85)
#   4. Write structured report to .gate-logs/coverage-<ts>.json
#   5. Exit 0 if all files > threshold, exit 1 if any below
#
#   make coverage-json          Parse existing coverage.json (skip pytest run)
# ---------------------------------------------------------------------------
THRESHOLD ?= 85
SOURCE ?= src/general_ludd

audit-coverage:
	@mkdir -p .gate-logs
	@$(UV) run python scripts/audit_coverage.py --threshold=$(THRESHOLD) --source=$(SOURCE)

coverage-json:
	@mkdir -p .gate-logs
	@$(UV) run python scripts/audit_coverage.py --json-file=coverage.json --threshold=$(THRESHOLD) --source=$(SOURCE)

coverage-report-from-data:
	@mkdir -p .gate-logs
	@$(UV) run python scripts/generate_coverage_report.py

coverage-branch-json:
	@mkdir -p .gate-logs
	@$(UV) run python scripts/gen_branch_coverage_json.py

coverage-branch-stats:
	@$(UV) run python scripts/parse_branch_coverage.py

# Targeted coverage check on key files (user-requested coverage report).
coverage-key-files:
	@PYYAML_FORCE_LIBYAML=0 $(UV) run python -m pytest \
		tests/unit/test_abtest_child.py \
		tests/unit/test_routers_web_search.py \
		tests/unit/test_renderers_runner.py \
		tests/unit/test_routers_features_endpoints.py \
		tests/unit/test_routers_quantization_endpoints.py \
		tests/unit/test_routers_integrity_endpoints.py \
		tests/unit/test_routers_processes_coverage.py \
		tests/unit/test_linux_landlock.py \
		tests/unit/test_connector_sentry.py \
		tests/unit/test_routers_registration.py \
		--cov=general_ludd.abtest._child \
		--cov=general_ludd.routers.web_search \
		--cov=general_ludd.renderers.runner \
		--cov=general_ludd.routers.features \
		--cov=general_ludd.routers.quantization \
		--cov=general_ludd.routers.integrity \
		--cov=general_ludd.routers.processes \
		--cov=general_ludd.security.sandboxes.linux_landlock \
		--cov=general_ludd.connectors.sentry \
		--cov=general_ludd.routers \
		--cov-report=term-missing

# Non-ansible coverage check (skips routers that import ansible).
coverage-key-files-noansible:
	@$(UV) run python -m pytest \
		tests/unit/test_abtest_child.py \
		tests/unit/test_renderers_runner.py \
		tests/unit/test_linux_landlock.py \
		--cov=general_ludd.abtest._child \
		--cov=general_ludd.renderers.runner \
		--cov=general_ludd.security.sandboxes.linux_landlock \
		--cov-report=term-missing

# Gate + coverage audit: runs the full gate then checks per-file coverage >=85%.
# Exits non-zero if gate fails OR any source file is below threshold.
gate-audit:
	@echo "=== GATE-AUDIT $(shell date -u +%Y-%m-%dT%H:%M:%SZ) ==="
	@$(MAKE) --no-print-directory gate
	@echo ""
	@echo "--- coverage check ---"
	@$(MAKE) --no-print-directory audit-coverage
# Regenerates .claude/hooks/agent_floor_stop.sh from scripts/gen_gate_safe_hook.py.
# The generator is the sanctioned writer of the hook (do NOT hand-edit the hook).
# Gate-safe rule: a running gate does NOT lower the read-only floor -- only heavy
# worktree-writers are capped during a gate. Idempotent; sets execute permissions.
write-gate-safe-hook:
	@mkdir -p .claude/hooks
	@python3 scripts/gen_gate_safe_hook.py .claude/hooks/agent_floor_stop.sh
	@echo "write-gate-safe-hook done"
