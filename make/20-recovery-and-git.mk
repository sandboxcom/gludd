# --- Crash recovery: cleanup after OpenCode/JSC crash ---
# Run after a SIGTRAP/EXC_BREAKPOINT crash leaves stale state files and
# orphaned processes. Resets enforcement state to fresh, kills orphaned
# mock_daemon processes, and removes stale checkpoint state.
# Safe to run at any time — only cleans things from dead processes.
crash-recovery:
	@echo "=== CRASH RECOVERY ==="
	@$(MAKE) --no-print-directory kill-stale
	@echo "  Cleaning stale enforcement state files..."
	@rm -f /tmp/gludd-session-start.json
	@rm -f /tmp/gludd-tool-streak.json
	@rm -f /tmp/gludd-mainthread-streak.json
	@rm -f /tmp/gludd-enhancement-ratio.json
	@rm -f /tmp/gludd-task-deadlines.json /tmp/gludd-task-stale.json
	@rm -f /tmp/gludd-watchdog-disengage.json
	@rm -f /tmp/gludd-plugin-heartbeat-*.json
	@rm -f /tmp/gludd-plugin-alive.json
	@rm -f /tmp/gludd-commit-lock-*.json
	@rm -f /tmp/gludd-session-debug.log
	@rm -f /tmp/gludd-plugin-loaded.log
	@rm -f /tmp/gludd-subagent-*.json
	@echo "  Stale state files cleaned."
	@echo "=== CRASH RECOVERY COMPLETE ==="

clean-tmp:
	@python3 scripts/clean_tmp.py

# Remove leaked API keys and SSH key material from the project root.
# These are gitignored but may accumulate from tool writes or agent errors.
clean-root:
	@bash scripts/clean-root.sh

clean-pycache-test-chat-history:
	@find /Users/shawnwilson/gludd -name "__pycache__" -path "*test_chat_history*" -exec rm -rf {} + 2>/dev/null || true
	@find /Users/shawnwilson/gludd -name "*.pyc" -path "*test_chat_history*" -delete 2>/dev/null || true
	@echo "test_chat_history cache cleared"

# Disk guard — checks disk usage % and cleans caches (pip, uv, pytest, mypy,
# ruff, __pycache__, tmp) when above GLUDD_DISK_THRESHOLD (default 95%).
# Delegate to scripts/disk-guard.sh for the full cleanup logic.
disk-guard:
	@bash scripts/disk-guard.sh guard

disk-check:
	@bash scripts/disk-guard.sh check

uv-cache-prune-status:
	@/bin/ps -ax -o pid=,ppid=,etime=,command= | /usr/bin/awk '/[u]v cache prune/ { found=1; print } END { if (!found) print "UV_CACHE_PRUNE_IDLE" }'

UV_CACHE_PRUNE_TIMEOUT ?= 300
UV_CACHE_PRUNE_FORCE ?= 0

uv-cache-prune:
	@if [ "$(UV_CACHE_PRUNE_FORCE)" = "1" ]; then \
		echo "UV_CACHE_PRUNE_FORCE=1: pruning uv cache with $(UV_CACHE_PRUNE_TIMEOUT)s timeout"; \
	elif /bin/ps -ax -o command= | /usr/bin/awk '/[u]v[[:space:]]+(run|sync|pip|build|add)/ { found=1 } END { exit !found }'; then \
		echo "UV_CACHE_PRUNE_SKIP active uv processes detected; skipping prune (set UV_CACHE_PRUNE_FORCE=1 to override)"; \
		exit 0; \
	else \
		echo "Pruning uv cache (timeout $(UV_CACHE_PRUNE_TIMEOUT)s)..."; \
	fi; \
	$(UV) cache prune --ci & _pid=$$!; _elapsed=0; _hb=5; \
	while kill -0 $$_pid 2>/dev/null; do \
		sleep $$_hb; _elapsed=$$((_elapsed + _hb)); \
		echo "UV_CACHE_PRUNE_HEARTBEAT pid=$$_pid elapsed_s=$$_elapsed max_s=$(UV_CACHE_PRUNE_TIMEOUT)"; \
		if [ $$_elapsed -ge "$(UV_CACHE_PRUNE_TIMEOUT)" ]; then \
			echo "UV_CACHE_PRUNE_TIMEOUT pid=$$_pid elapsed_s=$$_elapsed"; kill $$_pid 2>/dev/null || true; wait $$_pid 2>/dev/null || true; break; \
		fi; \
	done; \
	wait $$_pid 2>/dev/null || true

# Automatic disk preflight: clean only generated caches in completed/inactive
# Gludd worktrees, then fail closed unless both canonical limits are healthy.
DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY ?= 0
DISK_CLEANUP_PREFLIGHT_DRY_RUN ?= 0
DISK_CLEANUP_RECEIPT_GRACE_SECONDS ?= 1800
CHECK_DISK_VALIDATE_ONLY ?= 0

disk-cleanup-preflight:
	@if [ "$(DISK_CLEANUP_PREFLIGHT_DRY_RUN)" != "0" ] && [ "$(DISK_CLEANUP_PREFLIGHT_DRY_RUN)" != "1" ]; then \
		echo "Usage: make disk-cleanup-preflight DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0|1 DISK_CLEANUP_PREFLIGHT_DRY_RUN=0|1 DISK_CLEANUP_RECEIPT_GRACE_SECONDS='>=1800'"; \
		exit 2; \
	elif [ "$(DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY)" = "1" ]; then \
		$(MAKE) --no-print-directory test-files TESTFILES='tests/unit/test_automatic_disk_cleanup.py tests/unit/test_automatic_disk_cleanup_resources.py' PYTEST_ARGS='-q -n 0'; \
	elif [ "$(DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY)" = "0" ] && [ "$(DISK_CLEANUP_PREFLIGHT_DRY_RUN)" = "1" ]; then \
		$(SYSTEM_PYTHON) -m scripts.automatic_disk_cleanup --dry-run --receipt-grace-seconds "$(DISK_CLEANUP_RECEIPT_GRACE_SECONDS)"; \
	elif [ "$(DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY)" = "0" ] && [ "$(DISK_CLEANUP_PREFLIGHT_DRY_RUN)" = "0" ]; then \
		$(SYSTEM_PYTHON) -m scripts.automatic_disk_cleanup --receipt-grace-seconds "$(DISK_CLEANUP_RECEIPT_GRACE_SECONDS)"; \
	else \
		echo "Usage: make disk-cleanup-preflight DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0|1 DISK_CLEANUP_PREFLIGHT_DRY_RUN=0|1 DISK_CLEANUP_RECEIPT_GRACE_SECONDS='>=1800'"; \
		exit 2; \
	fi

# Compatibility entry point used by the pre-commit hook.
check-disk:
	@if [ "$(CHECK_DISK_VALIDATE_ONLY)" = "1" ]; then \
		$(MAKE) --no-print-directory test-files TESTFILES='tests/unit/test_check_disk_usage.py tests/unit/test_automatic_disk_cleanup.py tests/unit/test_automatic_disk_cleanup_resources.py' PYTEST_ARGS='-q -n 0'; \
	elif [ "$(CHECK_DISK_VALIDATE_ONLY)" = "0" ]; then \
		$(MAKE) --no-print-directory disk-cleanup-preflight DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0 DISK_CLEANUP_PREFLIGHT_DRY_RUN=0 DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1800; \
	else \
		echo "Usage: make check-disk CHECK_DISK_VALIDATE_ONLY=0|1"; \
		exit 2; \
	fi

check-disk-classification:
	@$(SYSTEM_PYTHON) scripts/check_disk_usage.py --classify

# Read-only system load diagnostic (AGENTS.md System-Load Gate Before Dispatch Waves).
# Prints 1m load avg, CPU count, and verdict (OK / WARN / CRITICAL). Exit 0 always.
check-system-load:
	@chmod +x scripts/check_system_load.py
	@uv run python scripts/check_system_load.py

# Disk headroom check — run BEFORE any heavy op (gate, agent dispatch) so we
# never silently refill the volume. Prints % used + free on the data volume.
disk:
	@df -h . | awk 'NR==1 || NR==2'
	@echo "--- generated workspace footprint ---"
	@du -sh .gate-logs .venv .cache .pytest_cache .mypy_cache .ruff_cache dist build htmlcov 2>/dev/null | sort -h || true
	@echo "--- largest gate-log entries ---"
	@du -sh .gate-logs/* 2>/dev/null | sort -h | tail -15 || true
	@echo "--- major workspace paths ---"
	@du -sh .git .opencode .claude .agents node_modules collections infra tests src docs 2>/dev/null | sort -h || true
	@echo "--- Terraform footprint ---"
	@du -sh infra/terraform/.plugin-cache infra/terraform/* 2>/dev/null | sort -h | tail -20 || true
	@echo "--- gludd scratch + worktree footprint ---"
	@du -sh /tmp/gludd-* 2>/dev/null | tail -5 || true

CACHE_RESOURCE_ROOT ?= $(HOME)/Library/Caches
CACHE_RESOURCE_LIMIT ?= 20
CACHE_RESOURCE_CANDIDATE ?=
CACHE_RESOURCE_VALIDATE_ONLY ?= 1

cache-disk: ## Show cache directory sizes (uv, huggingface, pip, npm)
	@echo "--- cache directories ---"
	@for d in ~/.cache/uv ~/.cache/huggingface ~/.cache/pip ~/.cache/claude ~/.cache/pre-commit ~/.cache/gh ~/.cache/opencode ~/Library/Caches/pip ~/.npm/_cacache; do \
		if [ -d "$$d" ]; then du -sh "$$d" 2>/dev/null; fi; \
	done
	@echo "--- uv cache detail ---"
	@du -sh ~/.cache/uv/*/ 2>/dev/null | sort -h | tail -10 || true

cache-clean: ## Clean uv, huggingface, and npm caches to free disk space
	@echo "--- cleaning uv cache ---"
	@rm -rf ~/.cache/uv/.gitignore ~/.cache/uv/.lock ~/.cache/uv/CACHEDIR.TAG ~/.cache/uv/archive-v0 ~/.cache/uv/builds-v0 ~/.cache/uv/interpreter-v4 ~/.cache/uv/sdists-v9 ~/.cache/uv/simple-v21 ~/.cache/uv/wheels-v6 2>/dev/null; echo "uv cache directories cleaned"
	@echo "--- cleaning huggingface cache ---"
	@rm -rf ~/.cache/huggingface/hub ~/.cache/huggingface/xet 2>/dev/null; echo "huggingface cache cleaned"
	@echo "--- cleaning npm cache ---"
	@npm cache clean --force 2>/dev/null && echo "npm cache cleaned" || echo "npm cache clean skipped"
	@echo "--- after cleanup ---"
	@$(MAKE) --no-print-directory cache-disk

tmp-gludd-usage:
	@du -sh /tmp/gludd-* 2>/dev/null | sort -h | tail -40 || true
opencode-disk: ## Bounded OpenCode data usage (OPENCODE_DB, OPENCODE_DATA_DIR, OPENCODE_DB_TIMEOUT_SECONDS, OPENCODE_DB_BUSY_TIMEOUT_MS, OPENCODE_MAX_FILE_ENTRIES, OPENCODE_MAINTENANCE_VALIDATE_ONLY)
	@$(SYSTEM_PYTHON) scripts/opencode_db_maintenance.py disk \
		$(if $(filter command line environment,$(origin OPENCODE_DB)),--db "$(OPENCODE_DB)",) \
		$(if $(strip $(OPENCODE_DATA_DIR)),--data-dir "$(OPENCODE_DATA_DIR)",) \
		--timeout-seconds "$(OPENCODE_DB_TIMEOUT_SECONDS)" \
		--busy-timeout-ms "$(OPENCODE_DB_BUSY_TIMEOUT_MS)" \
		--max-file-entries "$(OPENCODE_MAX_FILE_ENTRIES)" \
		$(if $(filter 1,$(OPENCODE_MAINTENANCE_VALIDATE_ONLY)),--validate-only,)

opencode-clean: ## Offline bounded OpenCode DB/cache cleanup (OPENCODE_DB, OPENCODE_DATA_DIR, OPENCODE_DB_TIMEOUT_SECONDS, OPENCODE_DB_BUSY_TIMEOUT_MS, OPENCODE_DB_INCREMENTAL_PAGES, OPENCODE_MAX_FILE_ENTRIES, OPENCODE_MAINTENANCE_VALIDATE_ONLY, OPENCODE_MAINTENANCE_FORCE)
	@$(SYSTEM_PYTHON) scripts/opencode_db_maintenance.py clean \
		$(if $(filter command line environment,$(origin OPENCODE_DB)),--db "$(OPENCODE_DB)",) \
		$(if $(strip $(OPENCODE_DATA_DIR)),--data-dir "$(OPENCODE_DATA_DIR)",) \
		--timeout-seconds "$(OPENCODE_DB_TIMEOUT_SECONDS)" \
		--busy-timeout-ms "$(OPENCODE_DB_BUSY_TIMEOUT_MS)" \
		--incremental-pages "$(OPENCODE_DB_INCREMENTAL_PAGES)" \
		--max-file-entries "$(OPENCODE_MAX_FILE_ENTRIES)" \
		$(if $(filter 1,$(OPENCODE_MAINTENANCE_VALIDATE_ONLY)),--validate-only,) \
		$(if $(filter 1,$(OPENCODE_MAINTENANCE_FORCE)),--force,)

opencode-clean-hard: ## Offline aggressive cache/log cleanup (OPENCODE_DB, OPENCODE_DATA_DIR, OPENCODE_DB_TIMEOUT_SECONDS, OPENCODE_DB_BUSY_TIMEOUT_MS, OPENCODE_DB_INCREMENTAL_PAGES, OPENCODE_MAX_FILE_ENTRIES, OPENCODE_MAINTENANCE_VALIDATE_ONLY, OPENCODE_MAINTENANCE_FORCE)
	@$(SYSTEM_PYTHON) scripts/opencode_db_maintenance.py clean-hard \
		$(if $(filter command line environment,$(origin OPENCODE_DB)),--db "$(OPENCODE_DB)",) \
		$(if $(strip $(OPENCODE_DATA_DIR)),--data-dir "$(OPENCODE_DATA_DIR)",) \
		--timeout-seconds "$(OPENCODE_DB_TIMEOUT_SECONDS)" \
		--busy-timeout-ms "$(OPENCODE_DB_BUSY_TIMEOUT_MS)" \
		--incremental-pages "$(OPENCODE_DB_INCREMENTAL_PAGES)" \
		--max-file-entries "$(OPENCODE_MAX_FILE_ENTRIES)" \
		$(if $(filter 1,$(OPENCODE_MAINTENANCE_VALIDATE_ONLY)),--validate-only,) \
		$(if $(filter 1,$(OPENCODE_MAINTENANCE_FORCE)),--force,)

disk-user-caches: ## Show all user-cache directories accessible to this agent + their sizes
	@echo "=== user cache footprint ==="
	@for d in ~/.cache/huggingface ~/.cache/gh ~/.cache/uv ~/.cache/pip ~/.cache/opencode ~/.cache/claude ~/.cache/pre-commit ~/.cache/gludd ~/.cache/general-ludd; do \
		if [ -d "$$d" ]; then \
			printf "%-50s %s\n" "$$d:" "$$(du -sh "$$d" 2>/dev/null | cut -f1)"; \
		fi; \
	done
	@echo ""
	@echo "=== major user-data roots (read-only) ==="
	@for d in "$$HOME/.local/share/opencode" "$$HOME/.local/share/containers" "$$HOME/.lima" "$$HOME/.npm" "$$HOME/.cargo" "$$HOME/.rustup" "$$HOME/.docker" "$$HOME/Library/Caches" "$$HOME/Library/Application Support" "$$HOME/Library/Developer" "$$HOME/Library/Logs" "$$HOME/tmp" "$$HOME/gludd" "$${TMPDIR:-/tmp}"; do \
		if [ -d "$$d" ]; then \
			echo "  measuring $$d"; \
			printf "%-50s %s\n" "$$d:" "$$(du -sh "$$d" 2>/dev/null | cut -f1)"; \
		fi; \
	done
	@echo ""
	@echo "=== gh run-log zips ==="
	@gh_logs=$$(find ~/.cache/gh -name 'run-log-*.zip' 2>/dev/null | wc -l | tr -d ' '); \
	echo "  count: $$gh_logs files"; \
	echo "  total: $$(du -sh ~/.cache/gh 2>/dev/null | cut -f1)"

cache-resource-inventory: ## List bounded immediate children of an allowlisted cache root
	@case "$(CACHE_RESOURCE_VALIDATE_ONLY)" in 0|1) ;; *) echo "CACHE_RESOURCE_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@[ "$(CACHE_RESOURCE_LIMIT)" -ge 1 ] 2>/dev/null && [ "$(CACHE_RESOURCE_LIMIT)" -le 100 ] 2>/dev/null || { echo "CACHE_RESOURCE_LIMIT must be between 1 and 100"; exit 2; }
	@$(SYSTEM_PYTHON) scripts/cache_resource_manager.py inventory --root "$(CACHE_RESOURCE_ROOT)" --limit "$(CACHE_RESOURCE_LIMIT)"

cache-resource-remove: ## Validate or remove one exact immediate cache child
	@case "$(CACHE_RESOURCE_VALIDATE_ONLY)" in 0|1) ;; *) echo "CACHE_RESOURCE_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@[ -n "$(CACHE_RESOURCE_CANDIDATE)" ] || { echo "CACHE_RESOURCE_CANDIDATE is required"; exit 2; }
	@if [ "$(CACHE_RESOURCE_VALIDATE_ONLY)" = "1" ]; then \
		$(SYSTEM_PYTHON) scripts/cache_resource_manager.py remove --root "$(CACHE_RESOURCE_ROOT)" --candidate "$(CACHE_RESOURCE_CANDIDATE)"; \
	else \
		$(SYSTEM_PYTHON) scripts/cache_resource_manager.py remove --root "$(CACHE_RESOURCE_ROOT)" --candidate "$(CACHE_RESOURCE_CANDIDATE)" --apply; \
	fi

clean-gh-run-logs: ## Delete all cached GitHub Actions run log zip files
	@echo "=== cleaning GH run logs ==="
	@before_files=$$(find ~/.cache/gh -name 'run-log-*.zip' 2>/dev/null | wc -l | tr -d ' '); \
	before_size=$$(du -sh ~/.cache/gh 2>/dev/null | cut -f1); \
	echo "  before: $$before_files files ($$before_size)"
	@find ~/.cache/gh -name 'run-log-*.zip' -delete 2>/dev/null || true
	@after_size=$$(du -sh ~/.cache/gh 2>/dev/null | cut -f1); \
	after_files=$$(find ~/.cache/gh -name 'run-log-*.zip' 2>/dev/null | wc -l | tr -d ' '); \
	echo "  after:  $$after_files files ($$after_size)"
	@echo "=== done ==="

audit-home-tmp: ## Show ~/tmp directory summary (read-only)
	@echo "=== ~/tmp audit ==="
	@echo "total_size=$$(du -sh /Users/shawnwilson/tmp 2>/dev/null | cut -f1)"
	@echo "--- gludd-owned temp patterns (count + size) ---"
	@for prefix in gl-runner gl-runner-iso gludd-tf gludd-llama-stderr gludd-qwen gludd-render gludd-collections gludd-sandbox lsmt_ _MEI; do \
		cnt=$$(find /Users/shawnwilson/tmp -maxdepth 1 -name "$${prefix}*" 2>/dev/null | wc -l | tr -d ' '); \
		if [ "$$cnt" != "0" ]; then \
			sz=$$(du -sch /Users/shawnwilson/tmp/$${prefix}* 2>/dev/null | tail -1 | awk '{print $$1}'); \
			echo "  $${prefix}* count=$$cnt size=$$sz"; \
		fi; \
	done
	@echo "--- other patterns (count) ---"
	@for pat in 'terraform-provider*' 'tmp*.whl' 'Gm*' '__pycache__'; do \
		cnt=$$(find /Users/shawnwilson/tmp -maxdepth 1 -name "$$pat" 2>/dev/null | wc -l | tr -d ' '); \
		[ "$$cnt" != "0" ] && echo "  $$pat count=$$cnt"; \
	done
	@echo "pytest_subdirs=$$(find /Users/shawnwilson/tmp/pytest-of-shawnwilson -maxdepth 1 -name 'pytest-*' 2>/dev/null | wc -l | tr -d ' ')"
	@echo "--- pytest root sizes ---"
	@du -sh /Users/shawnwilson/tmp/pytest-of-shawnwilson/pytest-* 2>/dev/null | sort -h | tail -20 || true
	@echo "--- largest top-level entries ---"
	@du -sh /Users/shawnwilson/tmp/* 2>/dev/null | sort -h | tail -20 || true
	@echo "--- legacy Podman path contents ---"
	@du -ah /Users/shawnwilson/tmp/podman 2>/dev/null | sort -h | tail -20 || true
	@echo "=== done ==="

cleanup-stale-tmp: ## Remove stale gludd-owned temp dirs/files from ~/tmp (APPLY=1 to execute, default dry-run)
	@$(SYSTEM_PYTHON) scripts/cleanup_stale_tmp.py $$([ "$(APPLY)" = "1" ] && echo "--apply") --min-age-seconds 3600

clean-all-caches: clean-tmp clean-hf-cache clean-gh-run-logs clean-worktree-venvs cleanup-stale-tmp ## Clean all caches: tmp, HF, GH run logs, worktree venvs, stale tmp entries
	@echo "=== all caches cleaned ==="
	@$(MAKE) --no-print-directory disk
	@$(MAKE) --no-print-directory disk-user-caches

opencode-db-stats: ## Bounded read-only OpenCode table counts (OPENCODE_DB, OPENCODE_DATA_DIR, OPENCODE_DB_TIMEOUT_SECONDS, OPENCODE_DB_BUSY_TIMEOUT_MS, OPENCODE_MAINTENANCE_VALIDATE_ONLY)
	@$(SYSTEM_PYTHON) scripts/opencode_db_maintenance.py stats \
		$(if $(filter command line environment,$(origin OPENCODE_DB)),--db "$(OPENCODE_DB)",) \
		$(if $(strip $(OPENCODE_DATA_DIR)),--data-dir "$(OPENCODE_DATA_DIR)",) \
		--timeout-seconds "$(OPENCODE_DB_TIMEOUT_SECONDS)" \
		--busy-timeout-ms "$(OPENCODE_DB_BUSY_TIMEOUT_MS)" \
		$(if $(filter 1,$(OPENCODE_MAINTENANCE_VALIDATE_ONLY)),--validate-only,)

opencode-db-schema: ## Bounded read-only OpenCode schema (OPENCODE_DB, OPENCODE_DATA_DIR, OPENCODE_DB_TIMEOUT_SECONDS, OPENCODE_DB_BUSY_TIMEOUT_MS, OPENCODE_MAINTENANCE_VALIDATE_ONLY)
	@$(SYSTEM_PYTHON) scripts/opencode_db_maintenance.py schema \
		$(if $(filter command line environment,$(origin OPENCODE_DB)),--db "$(OPENCODE_DB)",) \
		$(if $(strip $(OPENCODE_DATA_DIR)),--data-dir "$(OPENCODE_DATA_DIR)",) \
		--timeout-seconds "$(OPENCODE_DB_TIMEOUT_SECONDS)" \
		--busy-timeout-ms "$(OPENCODE_DB_BUSY_TIMEOUT_MS)" \
		$(if $(filter 1,$(OPENCODE_MAINTENANCE_VALIDATE_ONLY)),--validate-only,)

opencode-db-sample: ## Bounded read-only OpenCode timestamp sample (OPENCODE_DB, OPENCODE_DATA_DIR, OPENCODE_DB_TIMEOUT_SECONDS, OPENCODE_DB_BUSY_TIMEOUT_MS, OPENCODE_MAINTENANCE_VALIDATE_ONLY)
	@$(SYSTEM_PYTHON) scripts/opencode_db_maintenance.py sample \
		$(if $(filter command line environment,$(origin OPENCODE_DB)),--db "$(OPENCODE_DB)",) \
		$(if $(strip $(OPENCODE_DATA_DIR)),--data-dir "$(OPENCODE_DATA_DIR)",) \
		--timeout-seconds "$(OPENCODE_DB_TIMEOUT_SECONDS)" \
		--busy-timeout-ms "$(OPENCODE_DB_BUSY_TIMEOUT_MS)" \
		$(if $(filter 1,$(OPENCODE_MAINTENANCE_VALIDATE_ONLY)),--validate-only,)

opencode-db-vacuum-incremental: ## Safe PRAGMA incremental_vacuum while OpenCode is running (OPENCODE_DB, OPENCODE_DB_TIMEOUT_SECONDS, OPENCODE_DB_BUSY_TIMEOUT_MS, OPENCODE_DB_INCREMENTAL_PAGES)
	@$(SYSTEM_PYTHON) scripts/opencode_db_maintenance.py incremental-vacuum \
		$(if $(filter command line environment,$(origin OPENCODE_DB)),--db "$(OPENCODE_DB)",) \
		--timeout-seconds "$(OPENCODE_DB_TIMEOUT_SECONDS)" \
		--busy-timeout-ms "$(OPENCODE_DB_BUSY_TIMEOUT_MS)" \
		--incremental-pages "$(OPENCODE_DB_INCREMENTAL_PAGES)"

opencode-db-vacuum-full: ## Full VACUUM to reclaim disk space — refuses while OpenCode runs unless OPENCODE_MAINTENANCE_FORCE=1 (OPENCODE_DB, OPENCODE_DB_TIMEOUT_SECONDS, OPENCODE_DB_BUSY_TIMEOUT_MS, OPENCODE_MAINTENANCE_FORCE)
	@$(SYSTEM_PYTHON) scripts/opencode_db_maintenance.py vacuum-full \
		$(if $(filter command line environment,$(origin OPENCODE_DB)),--db "$(OPENCODE_DB)",) \
		--timeout-seconds "$(OPENCODE_DB_TIMEOUT_SECONDS)" \
		--busy-timeout-ms "$(OPENCODE_DB_BUSY_TIMEOUT_MS)" \
		$(if $(filter 1,$(OPENCODE_MAINTENANCE_FORCE)),--force,)

opencode-db-compact: ## Aggressive prune then compact via sqlite3 backup API — refuses while OpenCode runs unless OPENCODE_MAINTENANCE_FORCE=1 (OPENCODE_DB, OPENCODE_RETENTION_DAYS, OPENCODE_DB_TIMEOUT_SECONDS, OPENCODE_DB_BUSY_TIMEOUT_MS, OPENCODE_MAINTENANCE_FORCE)
	@$(SYSTEM_PYTHON) scripts/opencode_db_maintenance.py compact \
		$(if $(filter command line environment,$(origin OPENCODE_DB)),--db "$(OPENCODE_DB)",) \
		--retention-days "$(OPENCODE_RETENTION_DAYS)" \
		--batch-size "$(OPENCODE_DB_BATCH_SIZE)" \
		--timeout-seconds "$(OPENCODE_DB_TIMEOUT_SECONDS)" \
		--busy-timeout-ms "$(OPENCODE_DB_BUSY_TIMEOUT_MS)" \
		$(if $(filter 1,$(OPENCODE_MAINTENANCE_FORCE)),--force,)

opencode-clean-compact: ## Delete stale .compact backup files from the OpenCode data directory (safe: only removes backups, never the live DB)
	@DATA_DIR="$${OPENCODE_DATA_DIR:-$${HOME}/.local/share/opencode}"; \
	DB="$${OPENCODE_DB:-$${HOME}/.local/share/opencode/opencode.db}"; \
	for suffix in .compact .compact-journal .compact-wal .compact-shm; do \
		f="$${DB}$${suffix}"; \
		if [ -f "$$f" ]; then \
			sz=$$(du -sh "$$f" 2>/dev/null | cut -f1); \
			echo "phase=clean-compact file=$$f size=$$sz status=removing"; \
			rm -f "$$f"; \
		fi; \
	done; \
	echo "phase=clean-compact status=done"

opencode-db-backup: ## Fast sqlite3 backup — copies only used pages (online-safe). Output to OPENCODE_DB_BACKUP_OUTPUT (default: <db>.compact)
	@$(SYSTEM_PYTHON) -c '\
import sqlite3, os, sys, time; \
db = os.environ.get("OPENCODE_DB", os.path.expanduser("~/.local/share/opencode/opencode.db")); \
out = os.environ.get("OPENCODE_DB_BACKUP_OUTPUT", db + ".compact"); \
bt = int(os.environ.get("OPENCODE_DB_BUSY_TIMEOUT_MS", "5000")); \
ts = int(os.environ.get("OPENCODE_DB_TIMEOUT_SECONDS", "600")); \
print(f"phase=backup status=starting db={db} output={out}", flush=True); \
t0 = time.monotonic(); \
src = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=bt/1000); \
src.execute(f"PRAGMA busy_timeout={bt}"); \
dst = sqlite3.connect(out); \
dst.execute(f"PRAGMA busy_timeout={bt}"); \
dst.execute("PRAGMA journal_mode=OFF"); \
src.backup(dst, pages=100, sleep=0.05); \
src.close(); dst.close(); \
sz = os.path.getsize(out); \
elapsed = time.monotonic() - t0; \
print(f"phase=backup status=complete size={sz} elapsed_s={elapsed:.1f}", flush=True); \
'

opencode-db-prune: ## Offline bounded session-tree prune (OPENCODE_DB, OPENCODE_RETENTION_DAYS, OPENCODE_DB_BATCH_SIZE, OPENCODE_DB_MAX_SESSIONS, OPENCODE_DB_TIMEOUT_SECONDS, OPENCODE_DB_BUSY_TIMEOUT_MS, OPENCODE_MAINTENANCE_VALIDATE_ONLY, OPENCODE_MAINTENANCE_FORCE)
	@$(SYSTEM_PYTHON) scripts/opencode_db_maintenance.py prune \
		$(if $(filter command line environment,$(origin OPENCODE_DB)),--db "$(OPENCODE_DB)",) \
		--retention-days "$(OPENCODE_RETENTION_DAYS)" \
		--batch-size "$(OPENCODE_DB_BATCH_SIZE)" \
		--max-sessions "$(OPENCODE_DB_MAX_SESSIONS)" \
		--timeout-seconds "$(OPENCODE_DB_TIMEOUT_SECONDS)" \
		--busy-timeout-ms "$(OPENCODE_DB_BUSY_TIMEOUT_MS)" \
		$(if $(filter 1,$(OPENCODE_MAINTENANCE_VALIDATE_ONLY)),--validate-only,) \
		$(if $(filter 1,$(OPENCODE_MAINTENANCE_FORCE)),--force,)

tmp-gludd-worktree-usage:
	@du -sh /tmp/gludd-worktrees /tmp/gludd-worktrees/* /tmp/gludd-worktrees/*/.venv /tmp/gludd-worktrees/*/.pytest_cache /tmp/gludd-worktrees/*/.mypy_cache /tmp/gludd-worktrees/*/.ruff_cache 2>/dev/null | sort -h | tail -40 || true

tmp-gludd-clean-ci-shards:
	@$(SYSTEM_PYTHON) -m scripts.clean_ci_shard_scratch

tmp-gludd-clean-ci-shards-now:
	@if [ "$(TMP_GLUDD_CLEAN_VALIDATE_ONLY)" = "1" ]; then \
		$(MAKE) --no-print-directory test-files TESTFILES=tests/unit/test_clean_ci_shard_scratch.py PYTEST_ARGS='-q -n 0'; \
	else \
		$(SYSTEM_PYTHON) -m scripts.clean_ci_shard_scratch --min-age-seconds 0; \
	fi

tmp-gludd-clean-orphan-worktrees-now:
	@if [ "$(TMP_GLUDD_ORPHAN_CLEAN_VALIDATE_ONLY)" = "1" ]; then \
		$(MAKE) --no-print-directory test-files TESTFILES=tests/unit/test_clean_ci_shard_scratch.py PYTEST_ARGS='-q -n 0'; \
	elif [ "$(TMP_GLUDD_ORPHAN_CLEAN_VALIDATE_ONLY)" = "0" ]; then \
		$(SYSTEM_PYTHON) -m scripts.clean_ci_shard_scratch --worktree-orphans --delete-worktree-orphans; \
	else \
		echo "Usage: make tmp-gludd-clean-orphan-worktrees-now TMP_GLUDD_ORPHAN_CLEAN_VALIDATE_ONLY=1 (use 0 only after merge)"; \
		exit 2; \
	fi

# Remove only inactive registered peers' regenerable .venv directories. The
# invoking worktree and any worktree with a visible active process are preserved.
clean-worktree-venvs:
	@if [ "$(CLEAN_WORKTREE_VENVS_VALIDATE_ONLY)" = "1" ]; then \
		$(MAKE) --no-print-directory test-files TESTFILES=tests/unit/test_clean_worktree_venvs.py PYTEST_ARGS='-q -n 0'; \
	elif [ "$(CLEAN_WORKTREE_VENVS_VALIDATE_ONLY)" = "0" ]; then \
		$(SYSTEM_PYTHON) -m scripts.clean_worktree_venvs; \
	else \
		echo "Usage: make clean-worktree-venvs CLEAN_WORKTREE_VENVS_VALIDATE_ONLY=0|1"; \
		exit 2; \
	fi

clean-worktree-caches: clean-worktree-venvs
	@/usr/bin/find /Users/shawnwilson/gludd/.claude/worktrees -type d \( -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \) -prune -exec rm -rf {} + 2>/dev/null || true
	@/usr/bin/find /tmp/gludd-worktrees -type d \( -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \) -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "clean-worktree-caches done"
molecule-clean:
	@echo "Removing stray molecule/<scenario> runtime dirs (preserving the canonical default scenario and source directories)..."
	@for d in molecule/*/; do \
		s=$$(basename "$$d"); \
		case "$$s" in \
			playbooks|roles|internal_tools|mock_daemon|library|default) ;; \
			*) if [ -n "$$(git ls-files -- "$$d")" ]; then \
				echo "  Preserving tracked scenario: $$d"; \
			else \
				echo "  Removing stray: $$d"; rm -rf "$$d"; \
			fi ;; \
		esac; \
	done
	@echo "Removing Ansible dependency namespaces accidentally installed into the source collection root..."
	@rm -rf -- collections/ansible_collections/ansible collections/ansible_collections/community
	@find collections/ansible_collections -maxdepth 1 -type d \( -name 'ansible.*.info' -o -name 'community.*.info' \) -exec rm -rf -- {} +
	@echo "molecule-clean done"

molecule-test:
	@if [ -z "$(SCENARIO)" ]; then echo "Usage: make molecule-test SCENARIO=noop|prompt_eval|runtime_validate|binary_smoke_linux"; exit 1; fi
	@echo "Running molecule scenario: $(SCENARIO)"
	@if [ "$(SCENARIO)" = "binary_smoke_linux" ]; then \
		$(MAKE) --no-print-directory build-linux-executable \
			LIMA_INSTANCE="$(LIMA_INSTANCE)" \
			LIMA_DOCKER_CONFIG="$(LIMA_DOCKER_CONFIG)" \
			LINUX_BINARY_IMAGE="$(LINUX_BINARY_IMAGE)" \
			LINUX_BINARY_OUTPUT="$(LINUX_BINARY_OUTPUT)"; \
	fi
	@ANSIBLE_STATE_DIR=$$(mktemp -d "/tmp/gludd-molecule-$(SCENARIO).XXXXXX"); \
	DOCKER_CONFIG_VALUE="$$ANSIBLE_STATE_DIR/docker"; \
	mkdir -p "$$DOCKER_CONFIG_VALUE"; \
	chmod 700 "$$DOCKER_CONFIG_VALUE"; \
	export DOCKER_CONFIG="$$DOCKER_CONFIG_VALUE"; \
	PROJECT_COLLECTIONS="$$(pwd)/collections"; \
	export ANSIBLE_COLLECTIONS_PATH="$$ANSIBLE_STATE_DIR/collections:$$PROJECT_COLLECTIONS:/usr/share/ansible/collections"; \
	echo "Using Ansible collections: $$ANSIBLE_COLLECTIONS_PATH"; \
	DOCKER_HOST_VALUE="$${DOCKER_HOST:-}"; \
	if [ -z "$$DOCKER_HOST_VALUE" ] && command -v limactl >/dev/null 2>&1; then \
		LIMA_SOCKET=$$(limactl list "$(LIMA_INSTANCE)" --format '{{.Dir}}/sock/docker.sock' 2>/dev/null || true); \
		if [ -n "$$LIMA_SOCKET" ] && [ -S "$$LIMA_SOCKET" ]; then \
			DOCKER_HOST_VALUE="unix://$$LIMA_SOCKET"; \
			export DOCKER_HOST="$$DOCKER_HOST_VALUE"; \
			echo "Using Lima Docker API socket: $$DOCKER_HOST_VALUE"; \
		fi; \
	fi; \
	if [ -z "$$DOCKER_HOST_VALUE" ] && command -v podman >/dev/null 2>&1; then \
		PODMAN_SOCKET=$$(podman machine inspect "$(PODMAN_MACHINE)" --format '{{.ConnectionInfo.PodmanSocket.Path}}' 2>/dev/null || true); \
		if [ -n "$$PODMAN_SOCKET" ] && [ -S "$$PODMAN_SOCKET" ]; then \
			DOCKER_HOST_VALUE="unix://$$PODMAN_SOCKET"; \
			export DOCKER_HOST="$$DOCKER_HOST_VALUE"; \
			echo "Using Podman Docker API socket: $$DOCKER_HOST_VALUE"; \
		fi; \
	fi; \
	cleanup() { rm -rf "$$ANSIBLE_STATE_DIR"; }; \
	trap cleanup EXIT INT TERM; \
	MOLECULE_GLOB="molecule/playbooks/*/molecule.yml" ANSIBLE_HOME="$$ANSIBLE_STATE_DIR" $(UV) run molecule test -s "$(SCENARIO)"; \
	EXIT_CODE=$$?; \
	exit $$EXIT_CODE

check-molecule-integrity:
	@python3 /tmp/gludd-molecule-audit.py

molecule-test-model-pipeline:
	@echo "=== model pipeline molecule test (download->quantize->evaluate->register->serve) ==="
	@cd collections/ansible_collections/general_ludd/agent && $(UV) run molecule test -s default

git-status:
	@git status --short || echo "Not a git repo"

git-show:
	@test -n "$(SHA)" || (echo "Usage: make git-show SHA=<sha>"; exit 1)
	git show --stat $(SHA)

git-show-full:
	@test -n "$(SHA)" || (echo "Usage: make git-show-full SHA=<sha>"; exit 1)
	git show --no-ext-diff --no-textconv --no-color --no-color-moved --no-renames --no-indent-heuristic --diff-algorithm=myers --default-prefix --unified=3 "$(SHA)"

git-show-file-to:
	@test -n "$(SHA)" || { echo "Usage: make git-show-file-to SHA=<sha> FILE=path OUT=path"; exit 1; }
	@test -n "$(FILE)" || { echo "Usage: make git-show-file-to SHA=<sha> FILE=path OUT=path"; exit 1; }
	@test -n "$(OUT)" || { echo "Usage: make git-show-file-to SHA=<sha> FILE=path OUT=path"; exit 1; }
	@case "$(FILE)" in /*|*..*) echo "Refusing unsafe FILE: $(FILE)"; exit 1;; esac
	@case "$(OUT)" in /tmp/gludd-*|.opencode/plugin/impl/*) ;; /*|*..*) echo "Refusing unsafe OUT: $(OUT)"; exit 1;; esac
	@mkdir -p "$$(dirname "$(OUT)")"
	@git show "$(SHA):$(FILE)" > "$(OUT)"

git-show-name-only:
	@test -n "$(SHA)" || (echo "Usage: make git-show-name-only SHA=<sha>"; exit 1)
	git show --name-only $(SHA)

# Read-only diagnostic: current branch/HEAD, where master points, and the
# worktree layout — to untangle which tree the shell is actually on.
git-where:
	@echo "--- cwd ---"; pwd
	@echo "--- HEAD ---"; git rev-parse --abbrev-ref HEAD; git rev-parse --short HEAD
	@echo "--- master ---"; git rev-parse --short master 2>/dev/null || echo "no master ref"
	@echo "--- branches ---"; git branch -vv
	@echo "--- worktrees ---"; git worktree list

repo-status:
	@git status --short || echo "Not a git repo"

git-diff:
	@git diff --stat HEAD $(if $(FILES),-- $(FILES),) || echo "No diff"

# Full-patch diff (git-diff is stats-only). Optional FILES scope.
# Usage: make git-diff-full [FILES='path ...']
git-diff-full:
	@git diff HEAD $(if $(FILES),-- $(FILES),) || echo "No diff"

repo-diff:
	@git diff --stat || echo "No diff"

git-staged:
	@git diff --cached --stat || echo "Nothing staged"

git-stash:
	@git stash push -m "gludd-auto-stash-$$(date +%s)" || echo "Nothing to stash"
	@echo "Stashed. Run 'make git-stash-pop' to restore."

git-stash-pop:
	@git stash pop || echo "No stash to pop"

git-stash-clear:
	@COUNT=$$(git stash list 2>/dev/null | wc -l | tr -d ' '); \
	git stash clear && echo "Cleared $$COUNT stash entries." || echo "No stashes to clear."

repo-staged:
	@git diff --cached --stat || echo "Nothing staged"

git-init:
	@git init
	@git config user.email "agent@general-ludd.local" || true
	@git config user.name "General Ludd Agent" || true

STATUS_SNAPSHOT_VALIDATE_ONLY ?= 0
status-snapshot: ## Rewrite SESSION.md gate evidence, or validate without writes.
	@$(UV) run python scripts/status_snapshot.py $(if $(filter 1,$(STATUS_SNAPSHOT_VALIDATE_ONLY)),--validate-only,)

audit-evidence:
	@echo "=== Evidence Audit ==="
	@if [ ! -f TASKS.md ]; then echo "TASKS.md missing"; exit 1; fi
	@# Portable extraction (BSD grep on macOS has no -P): pull every
	@# `tests/...::...` node id out of TASKS.md with stdlib re, de-duped.
	@$(PYTHON) -c "import re; ids=sorted(set(re.findall(r'tests/[^\s:]+(?:::[A-Za-z0-9_]+)+', open('TASKS.md').read()))); open('/tmp/gludd-evidence-tests.txt','w').write('\n'.join(ids))"
	@if [ ! -s /tmp/gludd-evidence-tests.txt ]; then \
		echo "ERROR: no test-evidence node ids found in TASKS.md (extractor empty) — failing closed"; \
		exit 1; \
	fi
	@echo "Running $$(wc -l < /tmp/gludd-evidence-tests.txt | tr -d ' ') evidence tests..."
	@$(UV) run python -m pytest $$(cat /tmp/gludd-evidence-tests.txt) $(_XD) -q > /tmp/gludd-evidence-out.txt 2>&1; \
	EXIT=$$?; \
	if [ $$EXIT -ne 0 ]; then \
		echo "ERROR: evidence tests FAILED (exit $$EXIT) — failing closed"; \
		tail -20 /tmp/gludd-evidence-out.txt; \
		exit 1; \
	fi
	@echo "=== Evidence Audit Complete ==="

untrack:
	@[ -n "$(FILES)" ] || { echo "Usage: make untrack FILES='file1 file2'"; exit 1; }
	@git rm --cached $(FILES)

git-rm:
	@[ -n "$(FILES)" ] || { echo "Usage: make git-rm FILES='path ...'"; exit 1; }
	@git rm -r $(FILES) && echo "git-removed: $(FILES)"

git-rm-cached:
	@[ -n "$(FILES)" ] || { echo "Usage: make git-rm-cached FILES='path ...'"; exit 1; }
	@git rm --cached $(FILES) && echo "untracked: $(FILES)"

git-rm-force:
	@[ -n "$(FILES)" ] || { echo "Usage: make git-rm-force FILES='path ...'"; exit 1; }
	@git rm -rf $(FILES) && echo "git-force-removed: $(FILES)"

rm-files:
	@[ -n "$(FILES)" ] || { echo "Usage: make rm-files FILES='path ...'"; exit 1; }
	@rm -rf $(FILES) && echo "removed: $(FILES)"

git-mv:
	@[ -n "$(FROM)" ] && [ -n "$(TO)" ] || { echo "Usage: make git-mv FROM='old' TO='new'"; exit 1; }
	@mkdir -p "$$(dirname "$(TO)")"
	@rm -f "$(TO)"
	@git mv "$(FROM)" "$(TO)" && echo "git-moved: $(FROM) -> $(TO)"

# Read-only ancestor check: exit=0 means A is a strict ancestor of B (ff-only valid).
# Usage: make git-is-ancestor A=<commit> B=<commit>
git-is-ancestor:
	@[ -n "$(A)" ] && [ -n "$(B)" ] || { echo "Usage: make git-is-ancestor A=<commit> B=<commit>"; exit 1; }
	@git merge-base --is-ancestor $(A) $(B); echo "exit=$$?"

# Read-only rev-list counts for ff-only check.
# Usage: make git-revlist-count A=<old> B=<new>
# Prints: commits unique to A (must be 0 for ff) and commits B is ahead of A.
git-revlist-count:
	@[ -n "$(A)" ] && [ -n "$(B)" ] || { echo "Usage: make git-revlist-count A=<old> B=<new>"; exit 1; }
	@echo "commits unique to A (B..A, must be 0 for ff-only):"; git rev-list --count $(B)..$(A)
	@echo "commits B is ahead of A (A..B, should be >0):"; git rev-list --count $(A)..$(B)
	@echo "--- commits unique to A (would be lost on ff) ---"; git log --oneline $(B)..$(A) || true

# Read-only patch-equivalence inventory using Git's stable patch-id logic.
# '-' means the head-side patch already exists upstream under another commit;
# '+' means it is genuinely absent and needs semantic integration review.
git-patch-equivalence:
	@PATCH_UPSTREAM="$(PATCH_UPSTREAM)"; PATCH_HEAD="$(PATCH_HEAD)"; PATCH_LIMIT="$(PATCH_LIMIT)"; \
	[ -n "$$PATCH_UPSTREAM" ] && [ -n "$$PATCH_HEAD" ] && [ -n "$$PATCH_LIMIT" ] || { echo "Usage: make git-patch-equivalence PATCH_UPSTREAM=development PATCH_HEAD=master PATCH_LIMIT=20"; exit 2; }; \
	case "$$PATCH_LIMIT" in *[!0-9]*|'') echo "PATCH_LIMIT must be a non-negative integer"; exit 2;; esac; \
	PATCH_EQ=$$(git cherry "$$PATCH_UPSTREAM" "$$PATCH_HEAD" | awk '$$1 == "-" { count++ } END { print count + 0 }'); \
	UNIQUE=$$(git cherry "$$PATCH_UPSTREAM" "$$PATCH_HEAD" | awk '$$1 == "+" { count++ } END { print count + 0 }'); \
	echo "patch-equivalent=$$PATCH_EQ unique=$$UNIQUE upstream=$$PATCH_UPSTREAM head=$$PATCH_HEAD"; \
	if [ "$$PATCH_LIMIT" -gt 0 ]; then git cherry -v "$$PATCH_UPSTREAM" "$$PATCH_HEAD" | sed -n "1,$${PATCH_LIMIT}p"; fi

# Read-only: show a commit's hash, parents, committer time, subject, and files.
# Usage: make git-show-commit C=<sha>
git-show-commit:
	@[ -n "$(C)" ] || { echo "Usage: make git-show-commit C=<sha>"; exit 1; }
	@echo "--- $(C) summary ---"; git log -1 --format='%H%nparent: %P%ncommitter_unix: %ct%n%s' $(C)
	@echo "--- files touched ---"; git show --stat --oneline $(C) | tail -n +2

# Recreate a branch at BASE by cherry-picking a commit RANGE onto it. Used to
# re-root mis-branched work onto its intended base WITHOUT touching the source
# branch. NEW (the new branch name) is created at BASE, then every commit in
# RANGE (git rev-list order, oldest-first) is cherry-picked. Aborts + restores
# on any conflict so a half-applied branch is never left behind.
# Usage: make git-rebranch-onto NEW=<branch> BASE=<sha> RANGE='<sha1> <sha2> ...'
git-rebranch-onto:
	@[ -n "$(NEW)" ] && [ -n "$(BASE)" ] && [ -n "$(RANGE)" ] || { echo "Usage: make git-rebranch-onto NEW=<branch> BASE=<sha> RANGE='<sha1> <sha2>'"; exit 1; }
	@git rev-parse --verify "$(BASE)^{commit}" >/dev/null 2>&1 || { echo "ERROR: BASE $(BASE) is not a valid commit"; exit 1; }
	@ORIG=$$(git rev-parse --abbrev-ref HEAD); \
	echo "[rebranch] creating $(NEW) at $(BASE) (from $$ORIG)"; \
	git checkout -b "$(NEW)" "$(BASE)" || { echo "ERROR: could not create $(NEW) at $(BASE)"; exit 1; }; \
	for c in $(RANGE); do \
		echo "[rebranch] cherry-pick $$c"; \
		if ! git cherry-pick "$$c"; then \
			echo "ERROR: cherry-pick $$c CONFLICTED — aborting + cleaning up"; \
			git cherry-pick --abort 2>/dev/null || true; \
			git checkout -f "$$ORIG" 2>/dev/null || true; \
			git branch -D "$(NEW)" 2>/dev/null || true; \
			exit 1; \
		fi; \
	done; \
	echo "[rebranch] done: $(NEW) now at $$(git rev-parse --short HEAD) rooted on $(BASE)"

# Revert working-tree changes for specific files: tracked files -> HEAD version,
# untracked files -> deleted. Used to back a synced-but-broken agent change out
# of the working tree without disturbing other synced work.
git-revert-files:
	@[ -n "$(FILES)" ] || { echo "Usage: make git-revert-files FILES='...'"; exit 1; }
	@for f in $(FILES); do \
		if git ls-files --error-unmatch "$$f" >/dev/null 2>&1; then git checkout HEAD -- "$$f" && echo "  reverted $$f"; \
		else rm -f "$$f" && echo "  removed (untracked) $$f"; fi; \
	done

git-log:
	@git log --oneline -10 || echo "No git history"

git-log-n:
	@git log --oneline -$(if $(N),$(N),10) || echo "No git history"

grep:
	@[ -n "$(Q)" ] || { echo "Usage: make grep Q='pattern' [SEARCH_PATH='dir']"; exit 1; }
	@LEGACY_SEARCH_PATH="$(if $(filter command line,$(origin PATH)),$(PATH),)"; \
	 PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"; export PATH; \
	 grep -rn -- "$(Q)" $(if $(SEARCH_PATH),$(SEARCH_PATH),$(if $(PATH_),$(PATH_),$${LEGACY_SEARCH_PATH:-src tests})) || echo "No matches"

# Scoped grep that writes results to a file (avoids flooding stdout on broad
# audits) and takes a directory scope via DIR (distinct name from PATH, which
# would shadow the shell $PATH and break command resolution if reused here).
# Usage: make grepf Q='pattern' DIR='src/general_ludd/daemon' OUT=/tmp/x.txt
OUT ?= /tmp/gludd-grepf-out.txt
# Directory listing helper for audits: list dirs (default depth 2) under DIR,
# writing to OUT to avoid flooding stdout.
lsd:
	@find $(if $(DIR),$(DIR),src/general_ludd) -maxdepth $(if $(DEPTH),$(DEPTH),2) -type d > "$(OUT)" 2>&1; \
	echo "wrote $$(wc -l < "$(OUT)" | tr -d ' ') lines to $(OUT)"
lsf:
	@find $(if $(DIR),$(DIR),src/general_ludd) -maxdepth $(if $(DEPTH),$(DEPTH),1) -type f -name '*.py' > "$(OUT)" 2>&1; \
	echo "wrote $$(wc -l < "$(OUT)" | tr -d ' ') lines to $(OUT)"
lsa:
	@ls -la $(if $(DIR),$(DIR),src/general_ludd) > "$(OUT)" 2>&1; \
	echo "wrote $$(wc -l < "$(OUT)" | tr -d ' ') lines to $(OUT)"
grepf:
	@[ -n "$(Q)" ] || { echo "Usage: make grepf Q='pattern' [DIR='dir'] [OUT=/tmp/x.txt]"; exit 1; }
	@grep -rn -- "$(Q)" $(if $(DIR),$(DIR),src) > "$(OUT)" 2>&1 || echo "No matches" > "$(OUT)"; \
	echo "wrote $$(wc -l < "$(OUT)" | tr -d ' ') lines to $(OUT)"

# Pure-python recursive grep (system grep is absent in some sandboxes).
# Search Python source and tests for a literal pattern. Used for quick greps
# without raw rg/grep. Usage: make pygrep Q='pattern' [PATH_='src tests']
# Registered in the help target as an audit/discovery utility.

git-tracked-keys:
	@echo "=== Tracked files matching private-key / key patterns ==="
	@git ls-files | grep -E 'id_rsa|id_ed25519|\.pem$$|_rsa$$|_rsa\.pub$$|sandboxcom_github' || echo "NONE TRACKED"

# Check git hygiene: tracked pyc/__pycache__, .gitignore, private key files.
# Exit 0 = clean, exit 1 = issues found.
check-git-hygiene:
	@echo "=== Git Hygiene Check ==="; \
	errors=0; \
	echo "--- Checking for tracked __pycache__/ and .pyc files ---"; \
	tracked_pyc=$$(git ls-files | grep -E '__pycache__/|\.pyc$$' || true); \
	if [ -n "$$tracked_pyc" ]; then \
		echo "FAIL: Tracked __pycache__/ or .pyc files found:"; \
		echo "$$tracked_pyc"; \
		errors=$$((errors+1)); \
	else \
		echo "PASS: No tracked __pycache__/ or .pyc files"; \
	fi; \
	echo "--- Checking .gitignore exists ---"; \
	if [ -f .gitignore ]; then \
		echo "PASS: .gitignore exists"; \
	else \
		echo "FAIL: .gitignore is missing"; \
		errors=$$((errors+1)); \
	fi; \
	echo "--- Checking for tracked private key files ---"; \
	tracked_keys=$$(git ls-files | grep -E 'sandboxcom_github_rsa|\.deepseek\.key|\.zai\.key|\.deepseek\.config' || true); \
	if [ -n "$$tracked_keys" ]; then \
		echo "FAIL: Tracked private key files found:"; \
		echo "$$tracked_keys"; \
		errors=$$((errors+1)); \
	else \
		echo "PASS: No tracked private key files"; \
	fi; \
	if [ $$errors -gt 0 ]; then \
		echo "=== Git Hygiene Check: $$errors issue(s) found ==="; \
		exit 1; \
	else \
		echo "=== Git Hygiene Check: PASSED ==="; \
	fi

git-ls-tracked:
	@git ls-files $(if $(Q),| grep -E "$(Q)",)

git-history-file:
	@[ -n "$(Q)" ] || { echo "Usage: make git-history-file Q='path'"; exit 1; }
	@git log --all --full-history --oneline -- "$(Q)" || echo "No history"

Q ?=
AUTHOR ?=
SINCE ?=
PATH_FILTER ?=
LIMIT ?= 100
OFFSET ?= 0
JSON_OUT ?= 0

git-index:
	@$(PYTHON) scripts/git_history_index.py --repo . --db .gludd/git_history.db index

git-search:
	@if [ -z "$(Q)" ] && [ -z "$(AUTHOR)" ] && [ -z "$(SINCE)" ] && [ -z "$(PATH_FILTER)" ]; then \
		echo "Usage: make git-search Q='...' [AUTHOR='...'] [SINCE='YYYY-MM-DD'] [PATH_FILTER='...'] [LIMIT=100] [JSON_OUT=1]"; exit 1; fi
	@$(PYTHON) scripts/git_history_index.py --repo . --db .gludd/git_history.db search \
		$(if $(Q),--query '$(Q)') \
		$(if $(AUTHOR),--author '$(AUTHOR)') \
		$(if $(SINCE),--since '$(SINCE)') \
		$(if $(PATH_FILTER),--path '$(PATH_FILTER)') \
		--limit $(LIMIT) --offset $(OFFSET) \
		$(if $(filter 1,$(JSON_OUT)),--json,)

git-stats:
	@$(PYTHON) scripts/git_history_index.py --repo . --db .gludd/git_history.db stats \
		$(if $(filter 1,$(JSON_OUT)),--json,)

agent-report:
	@$(PYTHON) scripts/agent_activity_report.py

audit-messages:
	@$(PYTHON) scripts/audit_messages.py 2>&1 || echo "No opencode database found"

audit-schema:
	@$(PYTHON) scripts/db_schema.py

deps-audit:
	@echo "=== Dependency Audit (deptry, fail-closed) ==="
	@REQUIREMENTS_FILE="$$(mktemp /tmp/gludd-deptry-requirements.XXXXXX)"; \
		trap 'rm -f "$$REQUIREMENTS_FILE"' EXIT HUP INT TERM; \
		UV_NO_SYNC=0 $(UV) run --no-project --python 3.11 python scripts/dependency_profiles.py check \
			--root "$(CURDIR)" --manifest config/dependency_profiles.toml \
			--uv "$(UV)" --set audit-runtime; \
		UV_NO_SYNC=0 $(UV) run --no-project --python 3.11 python scripts/dependency_profiles.py export \
			--root "$(CURDIR)" --manifest config/dependency_profiles.toml \
			--set audit-runtime --output "$$REQUIREMENTS_FILE"; \
		$(UV) run --no-sync deptry src --config config/deptry_profiles.toml \
			--requirements-files "$$REQUIREMENTS_FILE"
	@echo "=== Dependency Audit PASS ==="

CORE_DEPENDENCY_OWNERSHIP_REFRESH_VALIDATE_ONLY ?= 1
core-dependency-ownership-refresh:
	@case "$(CORE_DEPENDENCY_OWNERSHIP_REFRESH_VALIDATE_ONLY)" in 0|1) ;; *) echo "CORE_DEPENDENCY_OWNERSHIP_REFRESH_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@$(UV) run --no-sync python scripts/check_core_dependency_ownership.py --root "$(CURDIR)" \
		$(if $(filter 1,$(CORE_DEPENDENCY_OWNERSHIP_REFRESH_VALIDATE_ONLY)),--validate-reconciled,--write-reconciled)

repo-log:
	@git log --oneline -10 || echo "No git history"

git-add:
	@if [ -z "$(FILES)" ]; then echo "Usage: make git-add FILES='file1 file2 ...'"; exit 1; fi
	@for path in $(FILES); do case "$$path" in *sandboxcom_*rsa*|*sandboxcom_*ed25519*|*id_rsa*|*id_ed25519*) echo "REFUSING to stage SSH key path: $$path" >&2; exit 1;; esac; done
	@git add $(FILES)

git-add-all:
	@KEY_PATHS="$$(git ls-files --others --exclude-standard; git diff --name-only)"; if printf '%s\n' "$$KEY_PATHS" | grep -E '(^|/)(sandboxcom_[^/]+|id_(rsa|ed25519))(\.pub)?$$' >/dev/null 2>&1; then echo "REFUSING to stage SSH key path" >&2; exit 1; fi
	@git add -A

git-lock-clean:
	@rm -f $(shell git rev-parse --show-toplevel)/.git/index.lock

# Resolve a conflicted file to HEAD's (--ours) version and stage it — for merges
# where git badly interleaved two independent additions; we re-apply the incoming
# side cleanly by hand afterward.
git-resolve-ours:
	@[ -n "$(FILES)" ] || { echo "Usage: make git-resolve-ours FILES='path'"; exit 1; }
	@git checkout --ours -- $(FILES) && git add $(FILES) && echo "resolved (ours): $(FILES)"

# Reconcile a historical merge into development without an untracked rescue
# Makefile. Conflicting paths preserve development when stage 2 exists; files
# introduced only by the merge source preserve that incoming version. Git's
# clean auto-merges remain untouched. APPLY=0 is the required behavioral smoke.
resolve-development-conflicts:
	@MERGE_SOURCE="$(MERGE_SOURCE)"; APPLY="$(APPLY)"; \
	[ -n "$$MERGE_SOURCE" ] || { echo "Usage: make resolve-development-conflicts MERGE_SOURCE=master APPLY=0|1"; exit 2; }; \
	case "$$APPLY" in 0|1) ;; *) echo "APPLY must be 0 or 1"; exit 2;; esac; \
	BRANCH=$$(git branch --show-current); \
	[ "$$BRANCH" = "development" ] || { echo "Refusing conflict resolution on branch $$BRANCH; expected development"; exit 1; }; \
	COUNT=$$(git diff --name-only --diff-filter=U | wc -l | tr -d ' '); \
	if [ "$$APPLY" = "0" ]; then \
		echo "DRY RUN: $$COUNT unresolved path(s) for $$MERGE_SOURCE -> development"; \
		exit 0; \
	fi; \
	MERGE_HEAD_SHA=$$(git rev-parse -q --verify MERGE_HEAD 2>/dev/null) || { echo "No merge is in progress"; exit 1; }; \
	SOURCE_SHA=$$(git rev-parse "$$MERGE_SOURCE^{commit}") || exit 1; \
	[ "$$MERGE_HEAD_SHA" = "$$SOURCE_SHA" ] || { echo "MERGE_HEAD $$MERGE_HEAD_SHA does not match $$MERGE_SOURCE $$SOURCE_SHA"; exit 1; }; \
	git diff --name-only --diff-filter=U | while IFS= read -r path; do \
		if git ls-files -u -- "$$path" | awk '$$3 == 2 { found=1 } END { exit !found }'; then \
			git checkout --ours -- "$$path"; SIDE=development; \
		else \
			git checkout --theirs -- "$$path"; SIDE="$$MERGE_SOURCE"; \
		fi; \
		git add -- "$$path" || exit 1; \
		echo "resolved $$path ($$SIDE)"; \
	done; \
	REMAINING=$$(git diff --name-only --diff-filter=U | wc -l | tr -d ' '); \
	[ "$$REMAINING" = "0" ] || { echo "$$REMAINING unresolved path(s) remain"; exit 1; }; \
	echo "Resolved $$COUNT conflict(s); non-conflicting $$MERGE_SOURCE changes preserved."

repo-add-all:
	@git add -A

commit-bootstrap: _gate-fresh-check _commit-lock-acquire
	@if [ -z "$(MSG)" ]; then echo "Usage: make commit-bootstrap MSG='message'"; exit 1; fi
	@$(MAKE) --no-print-directory collect-check
	@git diff --cached --quiet && echo "Nothing to commit" || git commit -m "$(MSG)"

# Commit staged changes using a message FILE (avoids shell quoting of multi-line
# messages with angle-bracket emails). Enforces the SAME fresh+green gate guard
# as `git-commit` (a bare `git commit -F` would otherwise bypass it). Usage:
#   make git-commit-file FILE=/tmp/msg.txt
git-commit-file: _gate-fresh-check _commit-lock-acquire
	@[ -n "$(FILE)" ] || { echo "Usage: make git-commit-file FILE=path"; exit 1; }
	@echo "Running pre-commit collection check..."
	@$(MAKE) --no-print-directory collect-check
	@echo "Gate fresh and green. Committing (message file)..."
	@git commit -F "$(FILE)"

provider-smoke:
	@test -n "$(PROVIDER)" || { echo Usage: make provider-smoke PROVIDER=aws SMOKE_TEST=ec2-a100 ARGS=--json; exit 1; }
	@test -n "$(SMOKE_TEST)" || { echo Usage: make provider-smoke PROVIDER=aws SMOKE_TEST=ec2-a100 ARGS=--json; exit 1; }
	@$(UV) run gludd smoke "$(PROVIDER)" "$(SMOKE_TEST)" $(ARGS)

local-accelerator-inventory:
	@$(UV) run python scripts/discover_accelerators.py

# Local hardware smoke targets are dry-run by default; LIVE=1 opts into bounded
# inference on the attached device. BACKEND and ARGS are forwarded verbatim.
mac-unified-memory-smoke:
	@$(UV) run python scripts/mac_unified_memory_smoke.py $(if $(filter 1 true yes,$(LIVE)),--live,) --backend $(or $(BACKEND),auto) $(ARGS)

gpu-hardware-smoke:
	@$(UV) run python scripts/gpu_hardware_smoke.py $(if $(filter 1 true yes,$(LIVE)),--live,) --backend $(or $(BACKEND),auto) $(ARGS)

# Provider deployment harnesses validate credentials and billing scopes without
# creating resources. Add GLUDD_INGEST_URL + GLUDD_INGEST_TOKEN to publish the
# normalized validation event and log record to Gludd's receiver.
provider-harness:
	@test -n "$(PROVIDER)" || { echo "Usage: make provider-harness PROVIDER=azure|runpod [LIVE=1]"; exit 1; }
	@$(UV) run python scripts/provider_smoke_harness.py "$(PROVIDER)" $(if $(filter 1 true yes,$(LIVE)),--live,)

azure-harness:
	@$(MAKE) --no-print-directory provider-harness PROVIDER=azure LIVE=$(LIVE)

runpod-harness:
	@$(MAKE) --no-print-directory provider-harness PROVIDER=runpod LIVE=$(LIVE)

iam-headless-smoke:
	@$(UV) run python scripts/iam_headless_smoke.py

test-opa-policies:
	@if command -v opa >/dev/null 2>&1; then \
		opa test $(OPA_ARGS) config/opa; \
	elif command -v docker >/dev/null 2>&1; then \
		echo "opa MISSING — running policy tests in $(OPA_IMAGE)"; \
		docker run --rm --volume "$(CURDIR)/config/opa:/workspace/config/opa:ro" \
			--workdir /workspace $(OPA_IMAGE) test $(OPA_ARGS) config/opa; \
	else \
		echo "opa MISSING and docker unavailable — run make install-opa"; \
		exit 1; \
	fi

smoke:
	@$(UV) run python scripts/smoke_daemon.py

install-workflow-hook:
	@scripts/hooks/pre-commit-workflow-yaml
	@if [ "$(INSTALL_WORKFLOW_HOOK_VALIDATE_ONLY)" = "1" ]; then \
		echo "install-workflow-hook: validate-only PASS"; \
	else \
		HOOK_DIR="$$(git rev-parse --git-common-dir)/hooks"; \
		mkdir -p "$$HOOK_DIR"; \
		install -m 0755 scripts/hooks/pre-commit-workflow-yaml "$$HOOK_DIR/pre-commit-workflow-yaml"; \
		echo "installed $$HOOK_DIR/pre-commit-workflow-yaml"; \
	fi

install-hooks: install-workflow-hook
	@PIP_INDEX_URL=https://pypi.org/simple $(UV) run pre-commit install --install-hooks
	@PIP_INDEX_URL=https://pypi.org/simple $(UV) run pre-commit install --hook-type pre-push
	@HOOK_PATH=".git/hooks/pre-commit"; \
		if [ ! -d .git ]; then HOOK_PATH="$$(git rev-parse --git-path hooks/pre-commit)"; fi; \
		install -m 0755 "$$HOOK_PATH" "$${HOOK_PATH}.framework"; \
		install -m 0755 scripts/hooks/pre-commit-lint "$$HOOK_PATH"; \
		echo "installed scripts/hooks/pre-commit-lint at $$HOOK_PATH (framework hook: $${HOOK_PATH}.framework)"
	@echo "pre-commit hooks installed: lint wrapper, secrets-scan, ruff, collect-check (pre-commit), gate (pre-push)"

scan-conflicts:
	@$(PYTHON) scripts/scan_conflicts.py

# Observability into the agent-floor guardrail (#79/#78): prints the maintained
# inc/dec counter AND the GROUND-TRUTH count of live subagents. Ground truth is
# now "transcript actively appended during a short probe" (scripts/agent_liveness.py)
# — NOT "mtime within 90s", which counted a just-COMPLETED agent's final
# transcript write as live and so REPORTED 11 WHEN ONLY 3 WERE RUNNING (a counter
# that over-counts is worse than none: it HIDES a floor breach). The probe biases
# toward undercount (over-provision), the safe direction, and is hook-independent.
# If the maintained counter disagrees, trust ground-truth (the probe).
floor-status:
	@printf '[floor-status] maintained counter: '
	@cat "$${TMPDIR:-/tmp}/claude-agent-floor.count" 2>/dev/null || cat /tmp/claude-agent-floor.count 2>/dev/null || echo "(MISSING in both \$$TMPDIR and /tmp)"
	@$(PYTHON) scripts/agent_liveness.py

# Composite orchestration decision: reads a JSON state blob (counts + ages +
# tails) and prints a structured plan (dispatch_n, repoke_ids, kill_ids, reason).
# Composes floor_planner + agent_liveness + agent_watchdog into one command.
# Usage: echo '{"live":4,"inflight":[...],"floor":6,"target":10,"ceiling":12}' | make floor-plan
# Or:    make floor-plan STATE=/tmp/state.json
STATE ?=
floor-plan:
	@if [ -n "$(STATE)" ]; then \
		$(UV) run python scripts/floor_controller.py "$(STATE)"; \
	else \
		$(UV) run python scripts/floor_controller.py; \
	fi

scan-secrets-baseline:
	@echo "[scan-secrets-baseline] scanning tracked files with detect-secrets (no per-file stream; typically 30-90s on this repo)..."
	@$(UV) run detect-secrets scan --exclude-files '$(SECRETS_EXCLUDE_FILES)' > .secrets.baseline.tmp
	@$(PYTHON) -c "import json; d=json.load(open('.secrets.baseline.tmp')); print('[scan-secrets-baseline] OK: valid JSON, %d files carry flagged (baselined) secrets' % len(d.get('results', {})))"
	@mv -f .secrets.baseline.tmp .secrets.baseline
	@echo "[scan-secrets-baseline] wrote .secrets.baseline ($$(wc -c < .secrets.baseline | tr -d ' ') bytes) -- stage it with: make git-add FILES='.secrets.baseline'"

clean-hooks:
	@rm -f .git/hooks/pre-commit.legacy .git/hooks/pre-push.legacy scripts/githooks/pre-commit scripts/githooks/pre-push
	@-rmdir scripts/githooks 2>/dev/null || true
	@echo "Legacy hooks removed"

clean-plugins:
	@echo "No plugin clean operations needed"

clean-untracked:
	@rm -f scripts/scan-secrets.py
	@echo "Cleaned up reinvention-of-wheel files"

git-remote-sandboxcom:
	@chmod 600 sandboxcom_github_rsa
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git remote add sandboxcom git@github.com:sandboxcom/gludd.git 2>/dev/null || true
	@echo "Remote sandboxcom configured"

# -- Push gate: prevent CI thrash (cancelled runs, push storms, excessive pushes) --

# Minimum seconds between pushes (30 minutes). Override with GLUDD_FORCE_PUSH=1.
PUSH_COOLDOWN_SECS ?= 1800
# Max cancelled CI runs in last 2 hours before blocking pushes. Override with GLUDD_FORCE_PUSH=1.
MAX_CANCELLED_RUNS ?= 3

# Pre-push guard: refuse to push if working tree has unstaged changes.
# Prevents pre-commit hook stash conflicts on the remote.
check-clean-tree:
	@$(PYTHON) scripts/check_clean_tree.py
worktree-state:
	@UV=echo $(SYSTEM_PYTHON) scripts/worktree_state_guard.py --json

all-worktree-state:
	@UV=echo $(SYSTEM_PYTHON) scripts/worktree_state_guard.py --all --json

main-worktree-state:
	@UV=echo $(SYSTEM_PYTHON) scripts/worktree_state_guard.py --main --main-path /Users/shawnwilson/gludd --json

worktree-guard:
	@UV=echo $(SYSTEM_PYTHON) scripts/worktree_state_guard.py --assert-clean

main-worktree-guard:
	@UV=echo $(SYSTEM_PYTHON) scripts/worktree_state_guard.py --main-path /Users/shawnwilson/gludd --assert-main-clean --main-claim-token

release-worktree-guard: worktree-guard main-worktree-guard
	@UV=echo $(SYSTEM_PYTHON) scripts/worktree_state_guard.py --assert-clean --claim-token

status-claim-guard: worktree-guard main-worktree-guard
	@UV=echo $(SYSTEM_PYTHON) scripts/worktree_state_guard.py --assert-clean --claim-token
workflow-state:
	@UV=echo $(SYSTEM_PYTHON) scripts/workflow_state_guard.py --json

workflow-gate:
	@UV=echo $(SYSTEM_PYTHON) scripts/workflow_state_guard.py --assert-clean --assert-no-feature-on-master --assert-no-unintegrated-worktrees --assert-no-unintegrated-branches

commit-ready:
	@UV=echo $(SYSTEM_PYTHON) scripts/workflow_state_guard.py --assert-clean --assert-no-feature-on-master

gha-ready: workflow-gate
	@UV=echo GIT_SSH_COMMAND="ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new" $(SYSTEM_PYTHON) scripts/ci_remote_head_guard.py --ref "$(REF)" --remote "$(REMOTE)"

merge-ready:
	@UV=echo $(SYSTEM_PYTHON) scripts/workflow_state_guard.py --assert-clean --assert-merge-ready --assert-no-unintegrated-worktrees --assert-no-unintegrated-branches
# Guard: prevent disabling tests in CI pipeline. Blocks push/release if
# test-shard has continue-on-error or is removed from release.needs.
_test-disabled-guard:
	@if ! grep -A1 '^  release:' .github/workflows/build.yml | grep -q 'test-shard'; then \
		echo "BLOCKED: test-shard missing from release job needs: in build.yml. Tests cannot be removed from release pipeline. Restore it."; exit 1; fi

_push-rate-guard: ci-failure-push-guard
	@# Force-push tracker: prevent GLUDD_FORCE_PUSH abuse (max 5 consecutive bypasses in 12h window)
	@if [ "$$GLUDD_FORCE_PUSH" = "1" ]; then \
		$(PYTHON) scripts/push_rate_guard.py check-bypass || exit 1; \
		$(PYTHON) scripts/push_rate_guard.py record-bypass; \
	else \
		$(PYTHON) scripts/push_rate_guard.py record-normal; \
	fi
	@# Check if CI is currently in-flight on the target branch.
	@# Uses ci_push_guard.py (branch-level active-run check, not commit-specific)
	@# PUSH_BRANCH overrides the branch to check (default: current branch).
	@PUSH_BRANCH=$${PUSH_BRANCH:-$$(git branch --show-current)}; \
		$(PYTHON) scripts/ci_push_guard.py "$$PUSH_BRANCH" || { \
		echo "Push blocked while CI is active on $$PUSH_BRANCH; wait for CI to complete."; \
		echo '{"last_push_blocked":true,"block_reason":"_push-rate-guard:ci-active","epoch":'$$(date +%s)'}' > /tmp/gludd-push-state.json; \
		exit 1; \
	}
	@# Check push cooldown (minimum interval between pushes)
	@LAST_PUSH=$$(python3 -c "import json;from pathlib import Path;p=Path('/tmp/gludd-watchdog-push-timestamps.json');d=json.loads(p.read_text()) if p.exists() else [];print(d[-1] if d else 0)" 2>/dev/null || echo 0); \
	if [ "$$LAST_PUSH" != "0" ]; then \
		NOW=$$(python3 -c "import time;print(time.time())"); \
		ELAPSED=$$(python3 -c "print(int($$NOW - $$LAST_PUSH))"); \
		if [ "$$ELAPSED" -lt "$(PUSH_COOLDOWN_SECS)" ] && [ "$$GLUDD_FORCE_PUSH" != "1" ]; then \
			echo "BLOCKED: last push was $$ELAPSED seconds ago (cooldown: $(PUSH_COOLDOWN_SECS)s)."; \
			echo "Batch commits locally. Use GLUDD_FORCE_PUSH=1 to override."; \
			echo '{"last_push_blocked":true,"block_reason":"_push-rate-guard:cooldown","cooldown_elapsed":'$$ELAPSED',"cooldown_required":$(PUSH_COOLDOWN_SECS),"epoch":'$$(date +%s)'}' > /tmp/gludd-push-state.json; \
			exit 1; \
		fi; \
	fi
	@# Check cancelled-run count in last 2 hours
	@CANCELLED=$$(GLUDD_WORKSPACE_ROOT=$(GLUDD_WORKSPACE_ROOT) python3 scripts/gha_cancelled_count.py 2>/dev/null || echo 0); \
	if [ "$$CANCELLED" -ge "$(MAX_CANCELLED_RUNS)" ] && [ "$$GLUDD_FORCE_PUSH" != "1" ]; then \
		echo "BLOCKED: $$CANCELLED CI runs cancelled in last 2h (max $(MAX_CANCELLED_RUNS))."; \
		echo "Run 'make gate-background' locally instead. Use GLUDD_FORCE_PUSH=1 to override."; \
		echo '{"last_push_blocked":true,"block_reason":"_push-rate-guard:cancelled-runs","cancelled_count":'$$CANCELLED',"max_allowed":$(MAX_CANCELLED_RUNS),"epoch":'$$(date +%s)'}' > /tmp/gludd-push-state.json; \
		exit 1; \
	fi

force-push:
	@GLUDD_FORCE_PUSH=1 $(MAKE) --no-print-directory _push-rate-guard git-push-sandboxcom

master-force-push:
	@GLUDD_FORCE_PUSH=1 $(MAKE) --no-print-directory _push-rate-guard
	@$(MAKE) --no-print-directory require-sandboxcom-ssh-key
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push --force --no-verify -u sandboxcom master
	@$(MAKE) verify-remote BRANCH=master SHA=$$(git rev-parse master)
	@echo "Master branch force-pushed and verified"

git-push-sandboxcom: check-clean-tree _test-disabled-guard _push-rate-guard _stash-before-push-guard _pull-before-push-guard _ci-verdict-history-guard _pre-commit-stash-audit _ci-restart-cap
	@BRANCH=$$(git branch --show-current); \
	GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push -u sandboxcom HEAD:$$BRANCH
	@echo "Pushed $$(git branch --show-current) to sandboxcom/gludd"
	@$(MAKE) --no-print-directory _record-push-verdict

push-dev: check-clean-tree ci-busy-check _push-rate-guard _stash-before-push-guard _ci-restart-cap _pull-before-push-guard
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push sandboxcom development
	@echo "Pushed development to sandboxcom/gludd"
	@$(MAKE) --no-print-directory _record-push-verdict
	@$(PYTHON) scripts/ci_check_cooldown.py deploy
	@python3 -c "import json,time;from pathlib import Path;p=Path('/tmp/gludd-watchdog-push-timestamps.json');d=json.loads(p.read_text()) if p.exists() else [];d.append(time.time());p.write_text(json.dumps(d[-50:]))" 2>/dev/null || true

push-dev-nv: check-clean-tree _push-rate-guard
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push --no-verify sandboxcom development
	@echo "Pushed development to sandboxcom/gludd (--no-verify)"
	@python3 -c "import json,time;from pathlib import Path;p=Path('/tmp/gludd-watchdog-push-timestamps.json');d=json.loads(p.read_text()) if p.exists() else [];d.append(time.time());p.write_text(json.dumps(d[-50:]))" 2>/dev/null || true

# Same as git-push-sandboxcom but skips the pre-push hook (detect-secrets +
# collect-check local gate). Use when the local 21k-test gate is non-viable
# and CI is the gate. The _push-rate-guard (CI-pending / cooldown / thrash)
# is STILL enforced. Mirrors commit-no-verify for the push side.
git-push-sandboxcom-nv: check-clean-tree _push-rate-guard _stash-before-push-guard _pull-before-push-guard _ci-verdict-history-guard _ci-restart-cap
	@BRANCH=$$(git branch --show-current); \
	GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push --no-verify -u sandboxcom HEAD:$$BRANCH
	@echo "Pushed $$(git branch --show-current) to sandboxcom/gludd (--no-verify)"
	@$(MAKE) --no-print-directory _record-push-verdict
	@python3 -c "import json,time;from pathlib import Path;p=Path('/tmp/gludd-watchdog-push-timestamps.json');d=json.loads(p.read_text()) if p.exists() else [];d.append(time.time());p.write_text(json.dumps(d[-50:]))" 2>/dev/null || true

# Push only the committed HEAD for the current branch. This is for CI candidate
# runs from a dirty integration checkout; uncommitted files are not included.
git-push-current-head-nv: check-clean-tree _push-rate-guard
	@BRANCH=$$(git branch --show-current); \
	if [ -z "$$BRANCH" ]; then echo "Cannot push detached HEAD"; exit 1; fi; \
	GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push --no-verify -u sandboxcom HEAD:$$BRANCH
	@echo "Pushed committed HEAD to sandboxcom/gludd"
	@python3 -c "import json,time;from pathlib import Path;p=Path('/tmp/gludd-watchdog-push-timestamps.json');d=json.loads(p.read_text()) if p.exists() else [];d.append(time.time());p.write_text(json.dumps(d[-50:]))" 2>/dev/null || true

git-push-current-head-to-master-nv: check-clean-tree _push-rate-guard
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push --no-verify sandboxcom HEAD:master
	@echo "Pushed committed HEAD to sandboxcom/gludd master"
	@python3 -c "import json,time;from pathlib import Path;p=Path('/tmp/gludd-watchdog-push-timestamps.json');d=json.loads(p.read_text()) if p.exists() else [];d.append(time.time());p.write_text(json.dumps(d[-50:]))" 2>/dev/null || true

# Batch push using the no-verify variant. COMMIT_THRESHOLD=1 is blocked to avoid CI thrash.
batch-push-nv: check-clean-tree _no-bypass-guard _stash-before-push-guard _ci-restart-cap _pull-before-push-guard
	if [ "$$THRESHOLD" = "1" ]; then \
		echo "BLOCKED: COMMIT_THRESHOLD=1 bypass is disabled; commit locally and batch pushes."; \
		exit 1; \
	fi; \
	if [ "$$COUNT" -lt "$$THRESHOLD" ] && [ "$$GLUDD_FORCE_PUSH" != "1" ]; then \
		echo "NOT PUSHING: only $$COUNT unpushed commit(s) (threshold=$$THRESHOLD)."; \
		echo "Batch locally. Use GLUDD_FORCE_PUSH=1 after enough local commits; COMMIT_THRESHOLD=1 is blocked."; \
		exit 0; \
	fi; \
	echo "$$COUNT unpushed commits, threshold met. Pushing (--no-verify)..."; \
	$(MAKE) git-push-sandboxcom-nv

# Batch push: only push after substantial local work (default 5+ unpushed commits).
# Override: GLUDD_FORCE_PUSH=1. COMMIT_THRESHOLD=1 is blocked.
# This is the RECOMMENDED push target. Use instead of git-push-sandboxcom directly.
batch-push: check-clean-tree _no-bypass-guard _stash-before-push-guard _pull-before-push-guard _ci-verdict-history-guard _pre-commit-stash-audit _ci-restart-cap _push-rate-guard
	@COUNT=$$(git log --oneline @{u}..HEAD 2>/dev/null | wc -l | tr -d ' '); \
	THRESHOLD=$${COMMIT_THRESHOLD:-5}; \
	if [ "$$THRESHOLD" = "1" ]; then \
		echo "BLOCKED: COMMIT_THRESHOLD=1 bypass is disabled; commit locally and batch pushes."; \
		exit 1; \
	fi; \
	if [ "$$COUNT" -lt "$$THRESHOLD" ] && [ "$$GLUDD_FORCE_PUSH" != "1" ]; then \
		echo "NOT PUSHING: only $$COUNT unpushed commit(s) (threshold=$$THRESHOLD)."; \
		echo "Batch locally. Use GLUDD_FORCE_PUSH=1 after enough local commits; COMMIT_THRESHOLD=1 is blocked."; \
		exit 0; \
	fi; \
	echo "$$COUNT unpushed commits, threshold met. Pushing..."; \
	BRANCH=$$(git branch --show-current); \
	GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push -u sandboxcom HEAD:$$BRANCH || { PUSH_RC=$$?; echo "Push failed for $$BRANCH (exit $$PUSH_RC); success was not recorded." >&2; exit $$PUSH_RC; }; \
	echo "Pushed $$BRANCH to sandboxcom/gludd ($$COUNT commits)"; \
	$(MAKE) --no-print-directory _record-push-verdict

force-batch-push:
	@GLUDD_FORCE_PUSH=1 $(MAKE) batch-push FORCE=1

# CI-aware push that waits for CI to go green before returning
# Same as git-push-sandboxcom but waits for CI completion after push
ci-push: pre-push-check _push-rate-guard
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push -u sandboxcom master
	@echo "Pushed to sandboxcom/gludd. Waiting for CI..."; \
	$(MAKE) ci-wait

# CI push then poll until green (single script)
ci-push-and-verify: pre-push-check _push-rate-guard _require-gh
	@bash scripts/ci_push_and_verify.sh

# Verify existing CI on HEAD (dry-run, no push)
ci-verify-wait: _require-gh
	@CI_DRY_RUN=1 bash scripts/ci_push_and_verify.sh

# Guard: ensure gh CLI is available
_require-gh:
	@command -v gh >/dev/null 2>&1 || { echo "ERROR: gh CLI not found. Install with: make ci-install-gh"; exit 1; }
