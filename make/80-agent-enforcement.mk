# --- Agent watchdog daemon (10s poll, resets streak counter) ---
watchdog-start:
	@echo "Starting agent watchdog (10s poll)..."
	@nohup $(UV) run python3 scripts/agent_watchdog.py > .gate-logs/watchdog.log 2>&1 & echo $$! > .gate-logs/watchdog.pid; echo "watchdog PID=$$(cat .gate-logs/watchdog.pid)"

watchdog-status:
	@echo "=== Watchdog status ==="
	@if [ -f .gate-logs/watchdog.pid ]; then \
		echo "PID: $$(cat .gate-logs/watchdog.pid)"; \
		ps -p $$(cat .gate-logs/watchdog.pid) > /dev/null 2>&1 && echo "Status: running" || echo "Status: stopped"; \
	else \
		echo "No PID file — watchdog not started"; \
	fi
	@echo "--- Last 15 log lines ---"
	@tail -15 .gate-logs/watchdog.log 2>/dev/null || echo "No log yet"

watchdog-stop:
	@$(UV) run python3 scripts/agent_watchdog.py --stop
	@rm -f .gate-logs/watchdog.pid

agent-watchdog-stop: watchdog-stop

watchdog-read:
	@if [ -f /tmp/gludd-continue.txt ]; then \
		echo "=== WATCHDOG CONTINUE DIRECTIVES ==="; \
		cat /tmp/gludd-continue.txt; \
		echo "=== END WATCHDOG DIRECTIVES ==="; \
	else \
		echo "No watchdog directives (file absent)"; \
	fi

watchdog-auto:
	@echo "Starting auto-watchdog (persists across sessions)..."
	@if [ -f .gate-logs/watchdog.pid ] && kill -0 $$(cat .gate-logs/watchdog.pid) 2>/dev/null; then \
		echo "Agent watchdog already running PID=$$(cat .gate-logs/watchdog.pid)"; \
	else \
		nohup $(UV) run python3 scripts/agent_watchdog.py > .gate-logs/watchdog.log 2>&1 & \
		echo $$! > .gate-logs/watchdog.pid; \
		echo "Agent watchdog started PID=$$!"; \
	fi
	@if [ -f .gate-logs/task-watchdog.pid ] && kill -0 $$(cat .gate-logs/task-watchdog.pid) 2>/dev/null; then \
		echo "Task watchdog already running PID=$$(cat .gate-logs/task-watchdog.pid)"; \
	else \
		nohup $(UV) run python3 scripts/task_watchdog.py > .gate-logs/task-watchdog.log 2>&1 & \
		echo $$! > .gate-logs/task-watchdog.pid; \
		echo "Task watchdog started PID=$$!"; \
	fi

watchdog-log:
	@tail -50 .gate-logs/watchdog.log 2>/dev/null || echo "No log yet"

# --- Task watchdog daemon (5s poll, kills hung tasks > GLUDD_TASK_TIMEOUT_MS) ---
# Reads /tmp/gludd-task-deadlines.json (written by enforce-deadline.ts plugin).
# Finds tasks whose elapsed > timeout, kills their child processes (SIGTERM→SIGKILL),
# records kills in /tmp/gludd-task-killed.json. Prevents indefinite task blocking.
task-watchdog-start:
	@echo "Starting task watchdog (5s poll, kills tasks > $$(( ${GLUDD_TASK_TIMEOUT} * 1000 ))ms)..."
	@nohup $(UV) run python3 scripts/task_watchdog.py > .gate-logs/task-watchdog.log 2>&1 & echo $$! > .gate-logs/task-watchdog.pid; echo "task watchdog PID=$$(cat .gate-logs/task-watchdog.pid)"

task-watchdog-status:
	@echo "=== Task watchdog status ==="
	@if [ -f .gate-logs/task-watchdog.pid ]; then \
		echo "PID: $$(cat .gate-logs/task-watchdog.pid)"; \
		ps -p $$(cat .gate-logs/task-watchdog.pid) > /dev/null 2>&1 && echo "Status: running" || echo "Status: stopped"; \
	else \
		echo "No PID file — task watchdog not started"; \
	fi
	@echo "--- Kill log (last 10) ---"
	@if [ -f /tmp/gludd-task-killed.json ]; then \
		$(UV) run python3 -c "import json; [print(f'  {e[\"task_id\"]} pid={e[\"pid\"]} elapsed={e[\"elapsed_ms\"]/1000:.0f}s') for e in json.load(open('/tmp/gludd-task-killed.json'))[-10:]]" 2>/dev/null || echo "  (no kills recorded)"; \
	else \
		echo "  (no kills recorded)"; \
	fi

task-watchdog-stop:
	@if [ -f .gate-logs/task-watchdog.pid ]; then \
		kill $$(cat .gate-logs/task-watchdog.pid) 2>/dev/null || true; \
		rm -f .gate-logs/task-watchdog.pid; \
		echo "Task watchdog stopped"; \
	else \
		echo "No task watchdog running"; \
	fi

task-watchdog-log:
	@tail -50 .gate-logs/task-watchdog.log 2>/dev/null || echo "No log yet"

# --- Plugin version check — detects stale plugin code after .ts file edits ---
check-plugin-versions:
	@$(UV) run python3 scripts/check_plugin_hashes.py

check-plugin-versions-quiet:
	@$(UV) run python3 scripts/check_plugin_hashes.py --quiet

write-plugin-manifest:
	@$(UV) run python3 scripts/check_plugin_hashes.py --write-manifest

# Stable, read-only roster for operators and monitoring probes.
list-plugins:
	@$(UV) run python3 scripts/list_plugins.py --markdown

codemod-lean-enforcement-plugins:
	@$(UV) run python3 scripts/lean_enforcement_plugins.py

# --- Plugin liveness check — verifies plugin hooks are structurally intact
# and actually firing. Three layers: structural (source code), passive (counter
# files from running plugin), active (runtime verification).
# Used by agent_watchdog.py, validate, and preflight.
check-plugin-liveness:
	@$(UV) run python3 scripts/check_plugin_liveness.py

# --- Plugin health dashboard — one-stop liveness + state + hook-fire observability.
# Reads /tmp/gludd-plugin-alive.json (reportAlive heartbeats), /tmp/gludd-hook-fires.jsonl
# (if any plugin logs per-invocation data), and enforcement state files. Exits 0 on
# success, exits 1 if all heartbeats are stale or alive.json is missing entirely.
check-plugin-health:
	@$(UV) run python3 scripts/check_plugin_health.py

# --- Plugin heartbeat check — runtime evidence that the core enforcement
# plugins (enforce-floor, enforce-delegate, enforce-stop) are ACTUALLY
# executing their tool.execute.before hook, not merely registered. Reads
# /tmp/gludd-plugin-heartbeat-<name>.json (freshness) + the LOADED log.
# Exits 0 if all plugins fired within GLUDD_HEARTBEAT_STALE_SECS (default 60s),
# 1 otherwise. Use after editing .ts files to confirm a restart is needed.
check-plugin-heartbeats:
	@$(UV) run python3 scripts/verify_plugin_liveness.py

# --- Hot-reload plugin modules (standalone JS for loadHotModule) ---
# Compiles enforcement plugin .ts source to standalone JS modules in /tmp/
# that loadHotModule() can load without an opencode restart.
hot-reload-plugins:
	@node scripts/build_hot_modules.js
	@echo ""
	@ls -la /tmp/gludd-hot-*.js 2>/dev/null || echo "  (none built)"

# Show hot-reload module status: which exist, ages, sizes
hot-reload-status:
	@node scripts/build_hot_modules.js --status

hot-reload-clean:
	@rm -f /tmp/gludd-hot-*.js
	@echo "Hot-reload modules removed"

fix-subagent-detection:
	@$(UV) run python3 scripts/fix_subagent_detection.py

subagent-init:
	@printf '{"subagent": true, "pid": %s, "ts": %s}\n' "$$$$" "$(shell date +%s)" > /tmp/gludd-subagent-$$$$.json
	@echo "subagent-init: created /tmp/gludd-subagent-$$$$.json"

subagent-cleanup:
	@rm -f /tmp/gludd-subagent-$$$$.json 2>/dev/null || true
	@echo "subagent-cleanup: removed /tmp/gludd-subagent-$$$$.json"

check-hot-reload-fresh:
	@if [ "$${CI:-}" = "true" ]; then \
		echo "CI environment — skipping hot-reload freshness check (modules in /tmp/ don't persist across steps)"; \
	elif $(UV) run python3 scripts/check_hot_reload_fresh.py; then \
		echo "PASS: all expected hot modules are fresh and valid"; \
	else \
		echo "FAIL: hot-module freshness or validity check failed"; \
		exit 1; \
	fi

# Mechanical restart-needed check: compares session-start timestamp against
# every .ts source file under .opencode/plugin/ (including impl/ sub-files).
# Exit 1 = restart needed; exit 0 = current.  CI-safe (skipped when GLUDD_CI=1).
check-plugin-restart-needed:
	@if [ "$${CI:-}" = "true" ] || [ "$${GLUDD_CI:-}" = "1" ]; then \
		echo "CI environment — skipping plugin restart-needed check (no running session)"; \
	else \
		$(UV) run python3 scripts/check_plugin_restart_needed.py; \
	fi

# --- Restart opencode for plugin changes to take effect ---
# TypeScript plugin changes are compiled once at opencode startup — edits to
# .opencode/plugin/*.ts do NOT take effect until opencode is restarted.
# Run this target to see the restart procedure and a process census.
restart-opencode:
	@echo "=== OpenCode Restart Procedure ==="
	@echo ""
	@echo "Plugin .ts edits do NOT hot-reload. OpenCode compiles plugins once at startup."
	@echo "To activate plugin changes:"
	@echo ""
	@echo "  1. Save all work and commit (make test-and-commit MSG='...')"
	@echo "  2. Quit opencode (Cmd+Q / Ctrl+C depending on interface)"
	@echo "  3. Re-launch opencode"
	@echo ""
	@echo "Before restart, verify plugin health:"
	@echo "  make check-plugin-liveness        — structural integrity check"
	@echo "  make check-plugin-versions        — hash freshness check"
	@echo ""
	@echo "If enforcement plugins are blocking you, disengage first:"
	@echo "  make disengage-enforcement        — suspends all plugin blocking for 1 hour"
	@echo ""

# --- Emergency enforcement disengage — stops all enforcement blocking immediately ---
disengage-enforcement:
	@echo "DISENGAGING enforcement — all plugin blocking suspended for 1 hour"
	@$(UV) run python3 -c "import json,time; ts=int(time.time()*1000); json.dump({'disengage_until':ts+3600000,'disengage_until_epoch_ms':ts+3600000,'reason':'manual_disengage','ts':time.time()},open('/tmp/gludd-watchdog-disengage.json','w'))"
	@$(UV) run python3 -c "import json,time; ts=int(time.time()*1000); json.dump({'consecutiveBlocks':0,'totalBlocks':0,'lastBlockTs':0,'disengageUntil':ts+3600000},open('/tmp/gludd-block-counter.json','w'))"
	@$(UV) run python3 -c "import json,time; json.dump({'last_ci_check':int(time.time()*1000),'last_ci_status':'SUCCESS','run_id':'disengaged','head_sha':'$(shell git rev-parse HEAD)'},open('/tmp/gludd-watchdog-ci.json','w'))"
	@AUDIT="$${GLUDD_DISENGAGE_AUDIT_PATH:-/tmp/gludd-disengage-audit.jsonl}"; \
	$(UV) run python3 -c "import json,os,time; print(json.dumps({'ts':time.time(),'pid':os.getpid(),'reason':'manual_disengage','duration_seconds':3600,'source':'make'}))" >> "$$AUDIT"; \
	COUNT="$$(wc -l < "$$AUDIT" | tr -d ' ')"; \
	echo "Disengage count: $$COUNT (recommended max 3/session)"
	@echo "Disengage files written — enforcement hooks will pass through for 1 hour"

# Disengage only the next enforcement hook operation. Writes the dedicated
# single-use marker that isDisengaged() consumes (delete + return true), so
# enforcement automatically re-arms after the next hook.
disengage-next:
	@$(UV) run python3 -c "import json,time; json.dump({'expires': 1, 'created_at': time.time(), 'reason': 'manual_single_use'},open('/tmp/gludd-disengage-next','w'))"
	@echo "DISENGAGED: single-operation disengage armed — enforcement re-arms after the next hook"

# --- Reload enforcement state mid-session ---
# Refresh state files that plugins re-read on every hook invocation so
# enforcement changes take effect without an opencode restart.
reload-enforcement:
	@echo "=== RELOAD ENFORCEMENT STATE ==="
	@$(MAKE) --no-print-directory clean-tmp
	@FLOOR="$${CLAUDE_AGENT_FLOOR:-0}"; \
	CEILING="$${CLAUDE_AGENT_CEILING:-3}"; \
	case "$$FLOOR" in ''|*[!0-9]*) FLOOR=0 ;; esac; \
	case "$$CEILING" in ''|*[!0-9]*) CEILING=3 ;; esac; \
	if [ "$$CEILING" -gt 3 ]; then CEILING=3; fi; \
	if [ "$$CEILING" -lt 1 ]; then CEILING=1; fi; \
	if [ "$$FLOOR" -gt "$$CEILING" ]; then FLOOR="$$CEILING"; fi; \
	echo "$${FLOOR}" > /tmp/gludd-floor-override; \
	echo "  /tmp/gludd-floor-override          → $${FLOOR}"
	@$(UV) run python3 -c 'import json,os,time; path=os.environ.get("GLUDD_STREAK_FILE","/tmp/gludd-tool-streak.json"); json.dump({"count":0,"ts":int(time.time()*1000)},open(path,"w"))'
	@echo "  /tmp/gludd-tool-streak.json        → count=0"
	@$(UV) run python3 -c 'import json,os,time; path=os.environ.get("GLUDD_MAINTHREAD_STREAK_FILE","/tmp/gludd-mainthread-streak.json"); json.dump({"streak":0,"last_dispatch_ts":int(time.time()*1000),"ts":int(time.time()*1000)},open(path,"w"))'
	@echo "  /tmp/gludd-mainthread-streak.json  → strength=0"
	@rm -f /tmp/gludd-watchdog-disengage.json
	@echo "  /tmp/gludd-watchdog-disengage.json → removed"
	@rm -f /tmp/gludd-disengage-next
	@echo "  /tmp/gludd-disengage-next         → removed"
	@rm -f /tmp/gludd-false-done-blocks.json
	@echo "  /tmp/gludd-false-done-blocks.json  → removed"
	@rm -f /tmp/gludd-enhancement-ratio.json
	@echo "  /tmp/gludd-enhancement-ratio.json  → removed (wave cleared)"
	@rm -f /tmp/gludd-session-start.json
	@echo "  /tmp/gludd-session-start.json      → removed (window reset)"
	@rm -f /tmp/gludd-task-deadlines.json /tmp/gludd-task-stale.json
	@echo "  /tmp/gludd-task-deadlines.json     → removed"
	@$(UV) run python3 -c 'import os,pathlib; pathlib.Path(os.environ.get("GLUDD_MULTITASK_STATE_FILE","/tmp/gludd-multitask-state.json")).unlink(missing_ok=True)'
	@echo "  /tmp/gludd-multitask-state.json    → removed (PID staleness guard)"
	@echo "=== RELOAD COMPLETE — plugins will re-read state on next hook call ==="

# --- Re-arm enforcement — remove disengage signals so plugins resume blocking ---
rearm-enforcement:
	@REMOVED=0; \
	if [ -f /tmp/gludd-watchdog-disengage.json ]; then \
		rm -f /tmp/gludd-watchdog-disengage.json; REMOVED=1; \
	fi; \
	if [ -f /tmp/gludd-disengage-next ]; then \
		rm -f /tmp/gludd-disengage-next; REMOVED=1; \
	fi; \
	if [ "$$REMOVED" = "1" ]; then \
		echo "REARMED: disengage signals removed — enforcement plugins will resume blocking."; \
	else \
		echo "REARMED (no-op): no disengage signal found — enforcement already active."; \
	fi

# --- Enforcement status — print current enforcement state ---
enforcement-status:
	@echo "=== ENFORCEMENT STATUS ==="
	@printf "  floor-override:          "; [ -f /tmp/gludd-floor-override ] && cat /tmp/gludd-floor-override || echo "(none — using default)"
	@printf "  tool-streak:             "; [ -f /tmp/gludd-tool-streak.json ] && $(UV) run python3 -c 'import json; d=json.load(open("/tmp/gludd-tool-streak.json")); print("count=%s" % d.get("count", 0))' || echo "(none)"
	@printf "  mainthread-streak:       "; [ -f /tmp/gludd-mainthread-streak.json ] && $(UV) run python3 -c 'import json; d=json.load(open("/tmp/gludd-mainthread-streak.json")); print("streak=%s" % d.get("streak", 0))' || echo "(none)"
	@printf "  disengaged:              "; [ -f /tmp/gludd-watchdog-disengage.json ] && echo "YES" || echo "NO"
	@printf "  enhancement-ratio:       "; [ -f /tmp/gludd-enhancement-ratio.json ] && echo "active (wave tracked)" || echo "(none — wave cleared)"
	@printf "  session-start:           "; [ -f /tmp/gludd-session-start.json ] && echo "active" || echo "(none — window reset)"
	@printf "  task-deadlines:          "; [ -f /tmp/gludd-task-deadlines.json ] && echo "active" || echo "(none)"
	@printf "  multitask-state:         "; [ -f /tmp/gludd-multitask-state.json ] && $(UV) run python3 -c 'import json; d=json.load(open("/tmp/gludd-multitask-state.json")); print("pid=%s zeroStreak=%s" % (d.get("pid"), d.get("zeroStreak", 0)))' || echo "(none)"
	@echo "=== ENFORCEMENT STATUS COMPLETE ==="

# Static coverage audit: match source → test imports (no pytest run).
#   make static-coverage [THRESHOLD=85]
static-coverage:
	@THRESHOLD=$(or $(THRESHOLD),85) $(PYTHON) scripts/static_coverage_audit.py
