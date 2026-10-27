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

# Legacy names share the canonical fixed limits and ownership-checked cleanup.
disk-guard:
	@SYSTEM_PYTHON="$(SYSTEM_PYTHON)" \
		DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY="$(DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY)" \
		DISK_CLEANUP_PREFLIGHT_DRY_RUN="$(DISK_CLEANUP_PREFLIGHT_DRY_RUN)" \
		DISK_CLEANUP_RECEIPT_GRACE_SECONDS="$(DISK_CLEANUP_RECEIPT_GRACE_SECONDS)" \
		bash scripts/disk-guard.sh guard

disk-check:
	@SYSTEM_PYTHON="$(SYSTEM_PYTHON)" bash scripts/disk-guard.sh check

uv-cache-prune-status:
	@/bin/ps -ax -o pid=,ppid=,etime=,command= | /usr/bin/awk '/[u]v cache prune/ { found=1; print } END { if (!found) print "UV_CACHE_PRUNE_IDLE" }'

# Resolve raw public values again in the inherited native target context.
uv-cache-prune: override GLUDD_UV_CACHE_PRUNE_TIMEOUT = $(value UV_CACHE_PRUNE_TIMEOUT)

uv-cache-prune: override GLUDD_UV_CACHE_PRUNE_FORCE = $(value UV_CACHE_PRUNE_FORCE)

uv-cache-prune: override GLUDD_UV_CACHE_PRUNE_VALIDATE_ONLY = $(value UV_CACHE_PRUNE_VALIDATE_ONLY)

uv-cache-prune: override GLUDD_UV_CACHE_PRUNE_ROOT = $(value UV_CACHE_PRUNE_OBSERVED_ROOT)

uv-cache-prune:
	@case "$$GLUDD_UV_CACHE_PRUNE_VALIDATE_ONLY:$$GLUDD_UV_CACHE_PRUNE_FORCE" in 0:0|0:1|1:0|1:1) ;; *) echo "UV_CACHE_PRUNE_VALIDATE_ONLY and UV_CACHE_PRUNE_FORCE must be 0 or 1" >&2; exit 2;; esac; \
	case "$$GLUDD_UV_CACHE_PRUNE_TIMEOUT" in ''|*[!0-9]*) echo "UV_CACHE_PRUNE_TIMEOUT must be a positive integer" >&2; exit 2;; esac; \
	[ "$$GLUDD_UV_CACHE_PRUNE_TIMEOUT" -gt 0 ] 2>/dev/null || { echo "UV_CACHE_PRUNE_TIMEOUT must be a positive integer" >&2; exit 2; }; \
	[ -n "$$GLUDD_UV_CACHE_PRUNE_ROOT" ] || { echo "UV_CACHE_PRUNE_OBSERVED_ROOT must not be empty" >&2; exit 2; }; \
	if [ "$$GLUDD_UV_CACHE_PRUNE_VALIDATE_ONLY" = "1" ]; then \
		printf 'UV_CACHE_PRUNE_PLAN max_seconds=%s force=%s observed_root=%s heartbeat=5 quiet=0 retain=20\n' "$$GLUDD_UV_CACHE_PRUNE_TIMEOUT" "$$GLUDD_UV_CACHE_PRUNE_FORCE" "$$GLUDD_UV_CACHE_PRUNE_ROOT"; \
		exit 0; \
	fi; \
	if [ "$$GLUDD_UV_CACHE_PRUNE_FORCE" = "1" ]; then \
		echo "UV_CACHE_PRUNE_FORCE=1: pruning uv cache with $$GLUDD_UV_CACHE_PRUNE_TIMEOUT s timeout"; \
	else \
		PRUNE_PROCESSES=$$(/bin/ps -ax -o command=) || { echo "UV_CACHE_PRUNE_REFUSED process inspection failed" >&2; exit 2; }; \
		if printf '%s\n' "$$PRUNE_PROCESSES" | /usr/bin/awk '/[u]v[[:space:]]+(run|sync|pip|build|add)/ { found=1 } END { exit !found }'; then \
			echo "UV_CACHE_PRUNE_SKIP active uv processes detected; skipping prune (set UV_CACHE_PRUNE_FORCE=1 to override)"; \
			exit 0; \
		else \
			PRUNE_SCAN_STATUS=$$?; \
			[ "$$PRUNE_SCAN_STATUS" -eq 1 ] || { echo "UV_CACHE_PRUNE_REFUSED process classification failed" >&2; exit 2; }; \
		fi; \
	fi; \
	$(PYTHON) scripts/stream_command.py --root "$$GLUDD_UV_CACHE_PRUNE_ROOT" --label uv-cache-prune \
		--heartbeat-secs 5 --quiet-secs 0 --max-secs "$$GLUDD_UV_CACHE_PRUNE_TIMEOUT" --retain-runs 20 \
		-- $(UV) cache prune --ci

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

# Reclaim only age-qualified, provably inactive direct children of one exact
# Gludd resource root. Inventory is the default; mutation requires an explicit
# 0 value and writes an append-only receipt outside the cleanup root.
STALE_RESOURCE_NAMESPACE_ROOT ?= $(HOME)/tmp/gludd-resources
STALE_RESOURCE_NAMESPACE_PROJECT_ROOT ?= $(CURDIR)
STALE_RESOURCE_NAMESPACE_REGISTRY ?=
STALE_RESOURCE_NAMESPACE_RECEIPT ?= $(STALE_RESOURCE_NAMESPACE_ROOT).cleanup-receipts.jsonl
STALE_RESOURCE_NAMESPACE_GRACE_SECONDS ?= 86400
STALE_RESOURCE_NAMESPACE_MAX_CANDIDATES ?= 1000
STALE_RESOURCE_NAMESPACE_MAX_ENTRIES ?= 100000
STALE_RESOURCE_NAMESPACE_HEARTBEAT_SECONDS ?= 5
STALE_RESOURCE_NAMESPACE_VALIDATE_ONLY ?= 1
STALE_RESOURCE_NAMESPACE_PYTHON ?=
export GLUDD_STALE_RESOURCE_NAMESPACE_PYTHON := $(value STALE_RESOURCE_NAMESPACE_PYTHON)
unexport STALE_RESOURCE_NAMESPACE_PYTHON

clean-stale-resource-namespaces:
	@case "$(STALE_RESOURCE_NAMESPACE_VALIDATE_ONLY)" in 0|1) ;; *) echo "STALE_RESOURCE_NAMESPACE_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@CLEANUP_PYTHON="$${GLUDD_STALE_RESOURCE_NAMESPACE_PYTHON:-$(SYSTEM_PYTHON)}"; \
		"$$CLEANUP_PYTHON" -m scripts.clean_stale_resource_namespaces \
		--root "$(STALE_RESOURCE_NAMESPACE_ROOT)" \
		--project-root "$(STALE_RESOURCE_NAMESPACE_PROJECT_ROOT)" \
		$(if $(strip $(STALE_RESOURCE_NAMESPACE_REGISTRY)),--registry "$(STALE_RESOURCE_NAMESPACE_REGISTRY)",) \
		--receipt "$(STALE_RESOURCE_NAMESPACE_RECEIPT)" \
		--grace-seconds "$(STALE_RESOURCE_NAMESPACE_GRACE_SECONDS)" \
		--max-candidates "$(STALE_RESOURCE_NAMESPACE_MAX_CANDIDATES)" \
		--max-entries "$(STALE_RESOURCE_NAMESPACE_MAX_ENTRIES)" \
		--heartbeat-seconds "$(STALE_RESOURCE_NAMESPACE_HEARTBEAT_SECONDS)" \
		$(if $(filter 1,$(STALE_RESOURCE_NAMESPACE_VALIDATE_ONLY)),--validate-only,--apply)

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
	@$(MAKE) --no-print-directory tmp-gludd-usage

# Read-only inventory is the default. Apply mode delegates all object parsing
# and rewriting to Git, retains reflogs/worktree metadata, and requires an
# explicit confirmation token plus a conservative unreachable-object grace.
GIT_OBJECT_STORE_REPOSITORY ?= $(CURDIR)
GIT_OBJECT_STORE_CONFIRM ?=
GIT_OBJECT_STORE_PRUNE_DAYS ?= 30
GIT_OBJECT_STORE_TIMEOUT_SECS ?= 900
GIT_OBJECT_STORE_KILL_AFTER_SECS ?= 10
GIT_OBJECT_STORE_HEARTBEAT_SECS ?= 5
GIT_OBJECT_STORE_VALIDATE_ONLY ?= 1

.PHONY: git-object-store-reclaim
git-object-store-reclaim: ## Inventory or conservatively reclaim one Git object store
	@case "$(GIT_OBJECT_STORE_VALIDATE_ONLY)" in 0|1) ;; *) echo "GIT_OBJECT_STORE_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@case "$(GIT_OBJECT_STORE_PRUNE_DAYS)" in ""|*[!0-9]*) echo "GIT_OBJECT_STORE_PRUNE_DAYS must be an integer of at least 30"; exit 2;; esac; \
	[ "$(GIT_OBJECT_STORE_PRUNE_DAYS)" -ge 30 ] || { echo "GIT_OBJECT_STORE_PRUNE_DAYS must be an integer of at least 30"; exit 2; }
	@case "$(GIT_OBJECT_STORE_TIMEOUT_SECS)" in ""|*[!0-9]*|0) echo "GIT_OBJECT_STORE_TIMEOUT_SECS must be a positive integer"; exit 2;; esac
	@case "$(GIT_OBJECT_STORE_KILL_AFTER_SECS)" in ""|*[!0-9]*|0) echo "GIT_OBJECT_STORE_KILL_AFTER_SECS must be a positive integer"; exit 2;; esac
	@case "$(GIT_OBJECT_STORE_HEARTBEAT_SECS)" in ""|*[!0-9]*|0) echo "GIT_OBJECT_STORE_HEARTBEAT_SECS must be a positive integer"; exit 2;; esac
	@[ "$(GIT_OBJECT_STORE_HEARTBEAT_SECS)" -le "$(GIT_OBJECT_STORE_TIMEOUT_SECS)" ] || { echo "GIT_OBJECT_STORE_HEARTBEAT_SECS must not exceed GIT_OBJECT_STORE_TIMEOUT_SECS"; exit 2; }
	@set -eu; \
	repository="$(GIT_OBJECT_STORE_REPOSITORY)"; \
	[ -n "$$repository" ] || { echo "GIT_OBJECT_STORE_REPOSITORY is required"; exit 2; }; \
	inside=$$(git -C "$$repository" rev-parse --is-inside-work-tree 2>/dev/null || true); \
	[ "$$inside" = "true" ] || { echo "Refusing non-worktree Git repository: $$repository"; exit 2; }; \
	repository=$$(git -C "$$repository" rev-parse --show-toplevel); \
	git -C "$$repository" rev-parse --verify HEAD >/dev/null 2>&1 || { echo "Refusing repository without a committed HEAD: $$repository"; exit 2; }; \
	common_dir=$$(git -C "$$repository" rev-parse --path-format=absolute --git-common-dir); \
	objects_dir=$$(git -C "$$repository" rev-parse --path-format=absolute --git-path objects); \
	[ -d "$$objects_dir" ] || { echo "Refusing missing Git object store: $$objects_dir"; exit 2; }; \
	before_kib=$$(du -sk "$$objects_dir" 2>/dev/null | awk '{print $$1}'); \
	before_kib=$${before_kib:-0}; \
	echo "GIT_OBJECT_STORE_INVENTORY repository=$$repository common_dir=$$common_dir objects_dir=$$objects_dir size_kib=$$before_kib"; \
	(cd "$$repository" && git count-objects -vH); \
	if [ "$(GIT_OBJECT_STORE_VALIDATE_ONLY)" = "1" ]; then \
		echo "GIT_OBJECT_STORE_VALID repository=$$repository prune_days=$(GIT_OBJECT_STORE_PRUNE_DAYS) mutation=false"; \
		exit 0; \
	fi; \
	[ "$(GIT_OBJECT_STORE_CONFIRM)" = "RECLAIM-GIT-OBJECTS" ] || { echo "GIT_OBJECT_STORE_CONFIRM must equal RECLAIM-GIT-OBJECTS"; exit 2; }; \
	head_before=$$(git -C "$$repository" rev-parse HEAD); \
	head_ref_before=$$(git -C "$$repository" symbolic-ref -q HEAD || printf '%s' DETACHED); \
	refs_before=$$(git -C "$$repository" show-ref --head | git -C "$$repository" hash-object --stdin); \
	status_before=$$(git -C "$$repository" status --porcelain=v2 --branch --untracked-files=all | git -C "$$repository" hash-object --stdin); \
	index_before=$$(git -C "$$repository" diff --cached --binary HEAD | git -C "$$repository" hash-object --stdin); \
	worktree_diff_before=$$(git -C "$$repository" diff --binary | git -C "$$repository" hash-object --stdin); \
	worktrees_before=$$(git -C "$$repository" worktree list --porcelain | git -C "$$repository" hash-object --stdin); \
	echo "GIT_OBJECT_STORE_BEGIN repository=$$repository prune_days=$(GIT_OBJECT_STORE_PRUNE_DAYS) timeout_secs=$(GIT_OBJECT_STORE_TIMEOUT_SECS)"; \
	git -C "$$repository" \
		-c gc.packRefs=false \
		-c gc.reflogExpire=never \
		-c gc.reflogExpireUnreachable=never \
		-c gc.worktreePruneExpire=never \
		-c gc.rerereResolved=never \
		-c gc.rerereUnresolved=never \
		gc --no-detach --prune="$(GIT_OBJECT_STORE_PRUNE_DAYS).days.ago" & \
	gc_pid=$$!; \
	terminate_owned_gc() { \
		if kill -0 "$$gc_pid" 2>/dev/null; then \
			kill -TERM "$$gc_pid" 2>/dev/null || true; \
			grace_elapsed=0; \
			while kill -0 "$$gc_pid" 2>/dev/null && [ "$$grace_elapsed" -lt "$(GIT_OBJECT_STORE_KILL_AFTER_SECS)" ]; do \
				sleep 1; grace_elapsed=$$((grace_elapsed + 1)); \
			done; \
			if kill -0 "$$gc_pid" 2>/dev/null; then \
				echo "GIT_OBJECT_STORE_KILL pid=$$gc_pid kill_after_secs=$(GIT_OBJECT_STORE_KILL_AFTER_SECS) signal=KILL"; \
				kill -KILL "$$gc_pid" 2>/dev/null || true; \
			fi; \
		fi; \
		wait "$$gc_pid" 2>/dev/null || true; \
	}; \
	trap 'terminate_owned_gc; exit 130' HUP INT TERM; \
	gc_rc=0; elapsed=0; timed_out=0; \
	while kill -0 "$$gc_pid" 2>/dev/null; do \
		sleep "$(GIT_OBJECT_STORE_HEARTBEAT_SECS)"; \
		elapsed=$$((elapsed + $(GIT_OBJECT_STORE_HEARTBEAT_SECS))); \
		echo "GIT_OBJECT_STORE_HEARTBEAT pid=$$gc_pid elapsed_secs=$$elapsed size_kib=$$(du -sk "$$objects_dir" 2>/dev/null | awk '{print $$1}')"; \
		if [ "$$elapsed" -ge "$(GIT_OBJECT_STORE_TIMEOUT_SECS)" ] && kill -0 "$$gc_pid" 2>/dev/null; then \
			echo "GIT_OBJECT_STORE_TIMEOUT pid=$$gc_pid timeout_secs=$(GIT_OBJECT_STORE_TIMEOUT_SECS) signal=TERM"; \
			terminate_owned_gc; gc_rc=124; timed_out=1; break; \
		fi; \
	done; \
	if [ "$$timed_out" -eq 0 ]; then wait "$$gc_pid" || gc_rc=$$?; fi; \
	trap - HUP INT TERM; \
	echo "GIT_OBJECT_STORE_POSTCHECK phase=invariants"; \
	head_after=$$(git -C "$$repository" rev-parse HEAD); \
	head_ref_after=$$(git -C "$$repository" symbolic-ref -q HEAD || printf '%s' DETACHED); \
	refs_after=$$(git -C "$$repository" show-ref --head | git -C "$$repository" hash-object --stdin); \
	status_after=$$(git -C "$$repository" status --porcelain=v2 --branch --untracked-files=all | git -C "$$repository" hash-object --stdin); \
	index_after=$$(git -C "$$repository" diff --cached --binary HEAD | git -C "$$repository" hash-object --stdin); \
	worktree_diff_after=$$(git -C "$$repository" diff --binary | git -C "$$repository" hash-object --stdin); \
	worktrees_after=$$(git -C "$$repository" worktree list --porcelain | git -C "$$repository" hash-object --stdin); \
	[ "$$head_before" = "$$head_after" ] && [ "$$head_ref_before" = "$$head_ref_after" ] && \
	[ "$$refs_before" = "$$refs_after" ] && [ "$$status_before" = "$$status_after" ] && \
	[ "$$index_before" = "$$index_after" ] && [ "$$worktree_diff_before" = "$$worktree_diff_after" ] && \
	[ "$$worktrees_before" = "$$worktrees_after" ] || { echo "Git reclamation changed refs, HEAD, index, worktree, or worktree registration"; exit 1; }; \
	echo "GIT_OBJECT_STORE_POSTCHECK phase=connectivity"; \
	(cd "$$repository" && git fsck --connectivity-only --no-dangling); \
	[ "$$gc_rc" -eq 0 ] || { echo "Git object-store reclamation failed or exceeded its bound: rc=$$gc_rc"; exit "$$gc_rc"; }; \
	after_kib=$$(du -sk "$$objects_dir" 2>/dev/null | awk '{print $$1}'); \
	after_kib=$${after_kib:-0}; \
	reclaimed_kib=$$((before_kib - after_kib)); \
	echo "GIT_OBJECT_STORE_INVENTORY_AFTER repository=$$repository size_kib=$$after_kib"; \
	(cd "$$repository" && git count-objects -vH); \
	echo "GIT_OBJECT_STORE_READY repository=$$repository before_kib=$$before_kib after_kib=$$after_kib reclaimed_kib=$$reclaimed_kib refs_unchanged=true worktree_unchanged=true"

# Cache diagnostics share one bounded owner; bulk deletion entrypoints are retired.
cache-disk: cache-resource-inventory ## Show bounded allowlisted cache children

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

disk-user-caches: cache-resource-inventory ## Show bounded allowlisted cache children

# Bind raw inputs in the actual target context, including diagnostic aliases.
export GLUDD_CACHE_RESOURCE_ROOT
export GLUDD_CACHE_RESOURCE_LIMIT
export GLUDD_CACHE_RESOURCE_CANDIDATE
export GLUDD_CACHE_RESOURCE_VALIDATE_ONLY
cache-resource-inventory cache-resource-remove: override GLUDD_CACHE_RESOURCE_ROOT = $(value CACHE_RESOURCE_ROOT)
cache-resource-inventory cache-resource-remove: override GLUDD_CACHE_RESOURCE_LIMIT = $(value CACHE_RESOURCE_LIMIT)
cache-resource-inventory cache-resource-remove: override GLUDD_CACHE_RESOURCE_CANDIDATE = $(value CACHE_RESOURCE_CANDIDATE)
cache-resource-inventory cache-resource-remove: override GLUDD_CACHE_RESOURCE_VALIDATE_ONLY = $(value CACHE_RESOURCE_VALIDATE_ONLY)

cache-resource-inventory: ## List bounded immediate children of an allowlisted cache root
	@case "$$GLUDD_CACHE_RESOURCE_VALIDATE_ONLY" in 0|1) ;; *) echo "CACHE_RESOURCE_VALIDATE_ONLY must be 0 or 1" >&2; exit 2;; esac; \
	[ "$$GLUDD_CACHE_RESOURCE_LIMIT" -ge 1 ] 2>/dev/null && [ "$$GLUDD_CACHE_RESOURCE_LIMIT" -le 100 ] 2>/dev/null || { echo "CACHE_RESOURCE_LIMIT must be between 1 and 100" >&2; exit 2; }; \
	$(SYSTEM_PYTHON) scripts/cache_resource_manager.py inventory --root "$${GLUDD_CACHE_RESOURCE_ROOT:-$$HOME/.cache}" --limit "$$GLUDD_CACHE_RESOURCE_LIMIT"

cache-resource-remove: ## Validate or remove one exact immediate cache child
	@case "$$GLUDD_CACHE_RESOURCE_VALIDATE_ONLY" in 0|1) ;; *) echo "CACHE_RESOURCE_VALIDATE_ONLY must be 0 or 1" >&2; exit 2;; esac; \
	[ -n "$$GLUDD_CACHE_RESOURCE_CANDIDATE" ] || { echo "CACHE_RESOURCE_CANDIDATE is required" >&2; exit 2; }; \
	set -- remove --root "$${GLUDD_CACHE_RESOURCE_ROOT:-$$HOME/.cache}" --candidate "$$GLUDD_CACHE_RESOURCE_CANDIDATE"; \
	if [ "$$GLUDD_CACHE_RESOURCE_VALIDATE_ONLY" = "0" ]; then set -- "$$@" --apply; fi; \
	$(SYSTEM_PYTHON) scripts/cache_resource_manager.py "$$@"



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



opencode-db-stats: ## Bounded SQL-readonly OpenCode table counts and page metadata (OPENCODE_DB, OPENCODE_DATA_DIR, OPENCODE_DB_TIMEOUT_SECONDS, OPENCODE_DB_BUSY_TIMEOUT_MS, OPENCODE_MAINTENANCE_VALIDATE_ONLY)
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
	@git --no-optional-locks status --short; native_rc=$$?; \
	if [ "$$native_rc" -eq 0 ]; then \
		printf '%s\n' 'AGENT_NEXT target=git-status outcome=observed native_status=0 gate_evidence=false release_ready=false next_owner=worktree-state; status is an observation; preserve owned changes and obtain the required source, gate and exact-SHA proofs' >&2; \
	else \
		printf '%s\n' "AGENT_NEXT target=git-status outcome=failed native_status=$$native_rc gate_evidence=false release_ready=false next_owner=worktree-state; retain the native Git reason; reconcile repository and owned index/lock state before retry; failure grants no commit or release authority" >&2; \
	fi; \
	exit "$$native_rc"

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

# Read-only tree comparison of two explicit, verified commit objects.
.PHONY: git-compare-commits
git-compare-commits:
	@printf '%s\n' \
		'import os, re, subprocess, sys' \
		'base = os.environ.get("GLUDD_GIT_COMPARE_BASE", "")' \
		'head = os.environ.get("GLUDD_GIT_COMPARE_HEAD", "")' \
		'if not all(re.fullmatch(r"[0-9a-fA-F]{40}", value) for value in (base, head)):' \
		'    print("AGENT_NEXT target=git-compare-commits outcome=refused native_status=2 phase=input next_action=pass-two-literal-full40hex-SHAs gate_evidence=false release_ready=false", file=sys.stderr)' \
		'    raise SystemExit(2)' \
		'def finish(outcome, status, phase, field, next_action):' \
		'    print(f"AGENT_NEXT target=git-compare-commits outcome={outcome} native_status={status} base={base} head={head} scope=commit-trees-only phase={phase} input={field} next_action={next_action} gate_evidence=false release_ready=false", file=sys.stderr)' \
		'    raise SystemExit(status)' \
		'git = ["git", "--no-pager", "--no-optional-locks"]' \
		'for field, sha in (("GIT_COMPARE_BASE", base), ("GIT_COMPARE_HEAD", head)):' \
		'    try:' \
		'        result = subprocess.run([*git, "cat-file", "-t", sha], stdout=subprocess.PIPE, check=False)' \
		'    except OSError as error:' \
		'        print(error, file=sys.stderr)' \
		'        finish("failed", 127, "git-exec", field, "repair-native-git-execution")' \
		'    status = result.returncode if result.returncode >= 0 else 128 - result.returncode' \
		'    if status:' \
		'        finish("failed", status, "verify-commit", field, "repair-missing-or-unreadable-pinned-commit")' \
		'    if result.stdout != b"commit\n":' \
		'        finish("refused", 2, "object-type", field, "select-a-direct-commit-object")' \
		'try:' \
		'    result = subprocess.run([*git, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--no-color", "--exit-code", base, head, "--"], check=False)' \
		'except OSError as error:' \
		'    print(error, file=sys.stderr)' \
		'    finish("failed", 127, "git-exec", "pinned-pair", "repair-native-git-execution")' \
		'status = result.returncode if result.returncode >= 0 else 128 - result.returncode' \
		'outcome = "identical" if status == 0 else ("different" if status == 1 else "failed")' \
		'next_action = "continue-independent-source-review" if status == 0 else ("review-the-pinned-patch" if status == 1 else "repair-the-native-diff-input")' \
		'finish(outcome, status, "diff", "pinned-pair", next_action)' | "$(PYTHON)" -P -

repo-diff:
	@git diff --stat || echo "No diff"

git-staged:
	@git diff --cached --stat || echo "Nothing staged"

git-stash:
	@git stash push -m "gludd-auto-stash-$$(date +%s)" || echo "Nothing to stash"
	@echo "Stashed. Run 'make git-stash-pop' to restore."

git-stash-list:
	@git stash list || echo "No stashes"

git-stash-show:
	@git stash show -p $(STASH) || echo "No stash $(STASH)"

git-stash-drop:
	@git stash drop $(STASH) || echo "No stash $(STASH)"

git-stash-pop:
	@git stash pop || echo "No stash to pop"

GIT_CHECKPOINT_LABEL ?=
GIT_CHECKPOINT_SHA ?=
GIT_CHECKPOINT_VALIDATE_ONLY ?= 1
ifneq (,$(filter git-owned-checkpoint-push git-owned-checkpoint-restore,$(MAKECMDGOALS)))
ifneq (,$(findstring $$,$(value FILES)$(value GIT_CHECKPOINT_LABEL)$(value GIT_CHECKPOINT_SHA)$(value GIT_CHECKPOINT_VALIDATE_ONLY)))
$(error OWNED-CHECKPOINT-REFUSED inputs must be literal file and identity data)
endif
endif
GIT_CHECKPOINT_OWNER := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))../scripts/owned_git_checkpoint.py)
export GIT_CHECKPOINT_FILES := $(value FILES)
export GIT_CHECKPOINT_LABEL GIT_CHECKPOINT_SHA GIT_CHECKPOINT_VALIDATE_ONLY

.PHONY: git-owned-checkpoint-push git-owned-checkpoint-restore
git-owned-checkpoint-push:
	@# FILES, GIT_CHECKPOINT_LABEL, GIT_CHECKPOINT_VALIDATE_ONLY are exported without shell interpolation.
	@$(PYTHON) "$(GIT_CHECKPOINT_OWNER)" push

git-owned-checkpoint-restore:
	@# FILES, GIT_CHECKPOINT_LABEL, GIT_CHECKPOINT_SHA, GIT_CHECKPOINT_VALIDATE_ONLY are environment-exported.
	@$(PYTHON) "$(GIT_CHECKPOINT_OWNER)" restore

# Selected checkpoint-owner link repair; capture public values as literal data.
GIT_CHECKPOINT_WORKTREE ?=
GIT_CHECKPOINT_ADMIN_DIR ?=
GIT_CHECKPOINT_EXPECTED_HEAD ?=
GIT_CHECKPOINT_EXPECTED_BRANCH ?=
ifneq (,$(filter git-owned-checkpoint-repair-link,$(MAKECMDGOALS)))
ifneq ($(words $(MAKECMDGOALS)),1)
$(error OWNED-CHECKPOINT-REFUSED use one repair target per invocation)
endif
ifneq (,$(findstring $$,$(value GIT_CHECKPOINT_WORKTREE)$(value GIT_CHECKPOINT_ADMIN_DIR)$(value GIT_CHECKPOINT_EXPECTED_HEAD)$(value GIT_CHECKPOINT_EXPECTED_BRANCH)$(value GIT_CHECKPOINT_SHA)$(value GIT_CHECKPOINT_VALIDATE_ONLY)))
$(error OWNED-CHECKPOINT-REFUSED repair inputs must be literal identity data)
endif
endif
override GLUDD_CHECKPOINT_REPAIR_WORKTREE := $(value GIT_CHECKPOINT_WORKTREE)
override GLUDD_CHECKPOINT_REPAIR_ADMIN_DIR := $(value GIT_CHECKPOINT_ADMIN_DIR)
override GLUDD_CHECKPOINT_REPAIR_EXPECTED_HEAD := $(value GIT_CHECKPOINT_EXPECTED_HEAD)
override GLUDD_CHECKPOINT_REPAIR_EXPECTED_BRANCH := $(value GIT_CHECKPOINT_EXPECTED_BRANCH)
override GLUDD_CHECKPOINT_REPAIR_SHA := $(value GIT_CHECKPOINT_SHA)
override GLUDD_CHECKPOINT_REPAIR_VALIDATE_ONLY := $(value GIT_CHECKPOINT_VALIDATE_ONLY)
export GLUDD_CHECKPOINT_REPAIR_WORKTREE GLUDD_CHECKPOINT_REPAIR_ADMIN_DIR
export GLUDD_CHECKPOINT_REPAIR_EXPECTED_HEAD GLUDD_CHECKPOINT_REPAIR_EXPECTED_BRANCH
export GLUDD_CHECKPOINT_REPAIR_SHA GLUDD_CHECKPOINT_REPAIR_VALIDATE_ONLY
unexport GIT_CHECKPOINT_WORKTREE GIT_CHECKPOINT_ADMIN_DIR GIT_CHECKPOINT_EXPECTED_HEAD GIT_CHECKPOINT_EXPECTED_BRANCH

.PHONY: git-owned-checkpoint-repair-link
git-owned-checkpoint-repair-link:
	@# GIT_CHECKPOINT_WORKTREE, GIT_CHECKPOINT_ADMIN_DIR, GIT_CHECKPOINT_SHA are literal environment inputs.
	@# GIT_CHECKPOINT_EXPECTED_HEAD, GIT_CHECKPOINT_EXPECTED_BRANCH, GIT_CHECKPOINT_VALIDATE_ONLY bind the plan/apply scope.
	@$(PYTHON) -P "$(GIT_CHECKPOINT_OWNER)" repair-link

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

# Literal commit arguments must not expand while Make builds the child environment.
ifneq ($(filter git-is-ancestor,$(MAKECMDGOALS)),)
unexport A B
endif

# Read-only ancestry check, including identical commits. Print and retain native status.
# Usage: make git-is-ancestor A=<commit> B=<commit>
git-is-ancestor:
	@[ -n '$(subst ','"'"',$(value A))' ] && [ -n '$(subst ','"'"',$(value B))' ] || { echo "Usage: make git-is-ancestor A=<commit> B=<commit>"; exit 1; }
	@ANCESTRY_STATUS=0; git merge-base --is-ancestor -- '$(subst ','"'"',$(value A))' '$(subst ','"'"',$(value B))' || ANCESTRY_STATUS=$$?; \
		printf 'exit=%s\n' "$$ANCESTRY_STATUS"; exit "$$ANCESTRY_STATUS"

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
	@echo "--- files touched ---"; \
	GIT_SHOW_FILES=$$(git show --stat --oneline $(C)) || { GIT_SHOW_STATUS=$$?; \
		printf '%s\n' "AGENT_NEXT target=git-show-commit outcome=failed native_status=2 child_status=$$GIT_SHOW_STATUS phase=git-show gate_evidence=false release_ready=false; retain the native failure and repair requested C before claiming revision proof" >&2; exit "$$GIT_SHOW_STATUS"; }; \
	printf '%s\n' "$$GIT_SHOW_FILES" | tail -n +2 || { GIT_SHOW_STATUS=$$?; \
		printf '%s\n' "AGENT_NEXT target=git-show-commit outcome=failed native_status=2 child_status=$$GIT_SHOW_STATUS phase=display gate_evidence=false release_ready=false; retain the native failure and repair the display child before claiming revision proof" >&2; exit "$$GIT_SHOW_STATUS"; }; \
	printf '%s\n' 'AGENT_NEXT target=git-show-commit outcome=passed native_status=0 gate_evidence=false release_ready=false next_owner=worktree-state; requested revision metadata and every display child finished; verify branch/clean ownership and required native acceptance before integration or release' >&2

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

git-executable:
	@if [ -z "$(FILES)" ]; then echo "Usage: make git-executable FILES='file1 file2 ...'"; exit 1; fi
	@git update-index --chmod=+x $(FILES)

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

_commit-locked-commit-bootstrap: _commit-lock-acquire _gate-fresh-check
	@if [ -z "$(MSG)" ]; then echo "Usage: make commit-bootstrap MSG='message'"; exit 1; fi
	@$(MAKE) --no-print-directory collect-check
	@git diff --cached --quiet && echo "Nothing to commit" || git commit -m "$(MSG)"

# Commit staged changes using a message FILE (avoids shell quoting of multi-line
# messages with angle-bracket emails). Enforces the SAME fresh+green gate guard
# as `git-commit` (a bare `git commit -F` would otherwise bypass it). Usage:
#   make git-commit-file FILE=/tmp/msg.txt
_commit-locked-git-commit-file: _commit-lock-acquire _gate-fresh-check
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
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git remote add sandboxcom git@github.com:sandboxcom/gludd.git 2>/dev/null || true
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git remote set-url sandboxcom git@github.com:sandboxcom/gludd.git || true
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git remote set-url --push sandboxcom git@github.com:sandboxcom/gludd.git || true
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
	@$(MAKE) --no-print-directory _runtime-publish-admission RUNTIME_PUBLISH_ADMISSION_VALIDATE_ONLY=0
	@$(MAKE) --no-print-directory require-sandboxcom-ssh-key
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push --force --no-verify -u sandboxcom master
	@$(MAKE) verify-remote BRANCH=master SHA=$$(git rev-parse master)
	@echo "Master branch force-pushed and verified"

.PHONY: batch-push git-push-sandboxcom
# Native GNU Make inspection must not execute a recursive publication recipe.
override _publication-native-flags := $(strip $(call _native-make-option-prefix,$(MAKEFLAGS)))
override _publication-native-short := $(call _native-make-boolean-cluster,$(filter-out -%,$(firstword $(_publication-native-flags)))) $(foreach option,$(filter-out --%,$(filter -%,$(_publication-native-flags))),$(call _native-make-boolean-cluster,$(patsubst -%,%,$(option))))
override _publication-native-mode := $(if $(findstring q,$(_publication-native-short))$(filter --question,$(_publication-native-flags)),query,$(if $(findstring n,$(_publication-native-short))$(filter --just-print --dry-run --recon,$(_publication-native-flags)),dry-run,$(if $(findstring t,$(_publication-native-short))$(filter --touch,$(_publication-native-flags)),touch,execute)))

git-push-sandboxcom: check-clean-tree _test-disabled-guard _push-rate-guard _stash-before-push-guard _pull-before-push-guard _ci-verdict-history-guard _pre-commit-stash-audit _ci-restart-cap
	@if [ "$(_publication-native-mode)" != execute ]; then echo "AGENT_NEXT target=git-push-sandboxcom outcome=planned mode=$(_publication-native-mode) gate_evidence=false release_ready=false: Native inspection starts no publication, runtime admission or hosted observation." >&2; exit 0; fi; \
	gludd_publish_branch=; gludd_publish_head=; gludd_publish_writes=false; \
	gludd_publish_fail() { gludd_publish_rc=$$1; gludd_publish_outcome=$$2; gludd_publish_phase=$$3; \
		case "$$gludd_publish_phase" in \
		expected-input) gludd_publish_next='Supply both optional PUBLISH_EXPECTED_BRANCH and full40hex PUBLISH_EXPECTED_SHA, or leave both empty; keep inputs literal.' ;; \
		expected-identity|identity|candidate) gludd_publish_next='Reconcile the saved branch/fullSHA with actual native HEAD before normal guarded publication; do not publish a different candidate.' ;; \
		authentication) gludd_publish_next='Repair the actual remote/helper/key/account prerequisite through its existing owner; consent remains operator-owned.' ;; \
		runtime) gludd_publish_next='Retain the actual admission receipt and repair its failed native input before another normal publication.' ;; \
		diagnostic-input|diagnostic-preflight) gludd_publish_next='Repair the explicit feature-ci purpose, saved expected identity or independently published workflow baseline; preserve native failures. No release admission is substituted and no transport has started in this result branch.' ;; \
		hosted-signal|diagnostic-signal) gludd_publish_next='Transport and history may already have applied; reconcile the saved branch/fullSHA and the actual returned run or native refusal. Use the existing ci-trigger-committed-head with that exact identity to request or reuse its Build run; do not repush unchanged source merely to request CI. Terminal CI and promotion proof remain required.' ;; \
		*) gludd_publish_next='Transport or history may already have applied; preserve the native reason and reconcile the actual remote/history before retry. No older remote or unchanged repush supplies missing proof.' ;; \
		esac; \
		printf 'AGENT_NEXT target=git-push-sandboxcom outcome=%s phase=%s native_status=%s branch=%s sha=%s transport_may_have_applied=%s gate_evidence=false release_ready=false: %s\n' "$$gludd_publish_outcome" "$$gludd_publish_phase" "$$gludd_publish_rc" "$$gludd_publish_branch" "$$gludd_publish_head" "$$gludd_publish_writes" "$$gludd_publish_next" >&2; \
		exit "$$gludd_publish_rc"; }; \
	gludd_publish_purpose="$$GLUDD_PUBLISH_PURPOSE"; \
	case "$$gludd_publish_purpose" in canonical|feature-ci) ;; *) gludd_publish_fail 2 refused diagnostic-input ;; esac; \
	gludd_publish_branch=$$(git branch --show-current) || gludd_publish_fail "$$?" failed candidate; \
	gludd_publish_head=$$(git rev-parse --verify HEAD^{commit}) || gludd_publish_fail "$$?" failed candidate; \
	[ -n "$$gludd_publish_branch" ] || gludd_publish_fail 2 refused candidate; \
	gludd_expected_branch="$$GLUDD_PUBLISH_EXPECTED_BRANCH"; gludd_expected_sha="$$GLUDD_PUBLISH_EXPECTED_SHA"; \
	if [ -n "$$gludd_expected_branch" ] || [ -n "$$gludd_expected_sha" ]; then \
		[ -n "$$gludd_expected_branch" ] && [ "$${#gludd_expected_sha}" -eq 40 ] || gludd_publish_fail 2 refused expected-input; \
		case "$$gludd_expected_sha" in *[!0123456789abcdefABCDEF]*) gludd_publish_fail 2 refused expected-input ;; esac; \
		git check-ref-format "refs/heads/$$gludd_expected_branch" || gludd_publish_fail "$$?" refused expected-input; \
		gludd_expected_sha=$$(printf '%s' "$$gludd_expected_sha" | tr ABCDEF abcdef) || gludd_publish_fail "$$?" failed expected-input; \
		if [ "$$gludd_expected_branch" != "$$gludd_publish_branch" ] || [ "$$gludd_expected_sha" != "$$gludd_publish_head" ]; then gludd_publish_fail 2 refused expected-identity; fi; \
	fi; \
	$(MAKE) --no-print-directory _require-sandboxcom-git-auth || gludd_publish_fail "$$?" failed authentication; \
	if [ "$$gludd_publish_purpose" = feature-ci ]; then \
		[ -n "$$gludd_expected_branch" ] && [ -n "$$gludd_expected_sha" ] || gludd_publish_fail 2 refused diagnostic-input; \
		case "$$gludd_publish_branch" in feature/*|diagnostic/*) ;; *) gludd_publish_fail 2 refused diagnostic-input ;; esac; \
		[ "$$FORCE" != 1 ] && [ "$$GLUDD_FORCE_PUSH" != 1 ] || gludd_publish_fail 2 refused diagnostic-input; \
		GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' $(SYSTEM_PYTHON) scripts/ci_remote_head_guard.py \
			--purpose feature-ci --publication --ref "$$gludd_publish_branch" --remote sandboxcom \
			--expected-branch "$$gludd_expected_branch" --expected-sha "$$gludd_expected_sha" \
			--baseline-ref "$$GLUDD_PUBLISH_BASELINE_REF" --baseline-sha "$$GLUDD_PUBLISH_BASELINE_SHA" \
			--repo sandboxcom/gludd --workflow 'Build and Release' || gludd_publish_fail "$$?" refused diagnostic-preflight; \
	else $(MAKE) --no-print-directory _runtime-publish-admission RUNTIME_PUBLISH_ADMISSION_VALIDATE_ONLY=0 || gludd_publish_fail "$$?" failed runtime; fi; \
	gludd_publish_current_branch=$$(git branch --show-current) || gludd_publish_fail "$$?" failed identity; \
	gludd_publish_current_head=$$(git rev-parse --verify HEAD^{commit}) || gludd_publish_fail "$$?" failed identity; \
	if [ "$$gludd_publish_current_branch" != "$$gludd_publish_branch" ] || [ "$$gludd_publish_current_head" != "$$gludd_publish_head" ]; then gludd_publish_fail 2 refused identity; fi; \
	gludd_publish_writes=true; \
	GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push -u sandboxcom "$$gludd_publish_head:refs/heads/$$gludd_publish_branch" || gludd_publish_fail "$$?" failed transport; \
	git branch --set-upstream-to="sandboxcom/$$gludd_publish_branch" "$$gludd_publish_branch" || gludd_publish_fail "$$?" failed tracking; \
	$(MAKE) --no-print-directory _record-push-verdict PUSH_VERDICT_SHA="$$gludd_publish_head" PUBLISH_EXPECTED_BRANCH="$$gludd_publish_branch" PUBLISH_EXPECTED_SHA="$$gludd_publish_head" PUBLISH_PURPOSE="$$gludd_publish_purpose" || gludd_publish_fail "$$?" failed verdict; \
	$(MAKE) --no-print-directory verify-remote BRANCH="$$gludd_publish_branch" SHA="$$gludd_publish_head" || gludd_publish_fail "$$?" failed remote-verification; \
	gludd_publish_signal_phase=hosted-signal; \
	if [ "$$gludd_publish_purpose" = feature-ci ]; then gludd_publish_signal_phase=diagnostic-signal; fi; \
	$(MAKE) --no-print-directory ci-trigger-committed-head PUBLISH_PURPOSE="$$gludd_publish_purpose" \
		PUBLISH_EXPECTED_BRANCH="$$gludd_publish_branch" PUBLISH_EXPECTED_SHA="$$gludd_publish_head" \
		PUBLISH_BASELINE_REF="$$GLUDD_PUBLISH_BASELINE_REF" PUBLISH_BASELINE_SHA="$$GLUDD_PUBLISH_BASELINE_SHA" \
		REF="$$gludd_publish_branch" REMOTE=sandboxcom REPO=sandboxcom/gludd WORKFLOW='Build and Release' EXAMPLE=0 \
		WORK_ITEM_ID='$(subst ','"'"',$(value WORK_ITEM_ID))' || gludd_publish_fail "$$?" failed "$$gludd_publish_signal_phase"; \
	if [ "$$gludd_publish_purpose" = feature-ci ]; then \
		printf 'AGENT_NEXT target=git-push-sandboxcom outcome=diagnostic-signaled purpose=feature-ci native_status=0 branch=%s sha=%s gate_evidence=false release_ready=false: The saved source SHA is verified remotely and the existing exact-SHA signal returned a CI run. Inspect that run with the native CI failure/status owners; no terminal CI, canonical push, deployment or release proof is granted. Continue feature to development to master through unchanged promotion gates.\n' "$$gludd_publish_branch" "$$gludd_publish_head" >&2; exit 0; \
	fi; \
	printf 'AGENT_NEXT target=git-push-sandboxcom outcome=published native_status=0 branch=%s sha=%s gate_evidence=false release_ready=false: The saved fullSHA is verified remotely and the existing exact-SHA signal returned an accepted or reused Build run. Observe that actual run with pipeline-status and the native CI failure/status owners; no terminal CI, canonical push, deployment or release proof is granted. Canonical full gate, deployment acceptance and release promotion remain required.\n' "$$gludd_publish_branch" "$$gludd_publish_head" >&2

push-dev: check-clean-tree ci-busy-check _push-rate-guard _stash-before-push-guard _ci-restart-cap _pull-before-push-guard
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push sandboxcom development
	@echo "Pushed development to sandboxcom/gludd"
	@$(MAKE) --no-print-directory _record-push-verdict
	@$(PYTHON) scripts/ci_check_cooldown.py deploy
	@python3 -c "import json,time;from pathlib import Path;p=Path('/tmp/gludd-watchdog-push-timestamps.json');d=json.loads(p.read_text()) if p.exists() else [];d.append(time.time());p.write_text(json.dumps(d[-50:]))" 2>/dev/null || true

push-dev-nv: check-clean-tree _push-rate-guard
	@$(MAKE) --no-print-directory _runtime-publish-admission RUNTIME_PUBLISH_ADMISSION_VALIDATE_ONLY=0
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push --no-verify sandboxcom development
	@echo "Pushed development to sandboxcom/gludd (--no-verify)"
	@python3 -c "import json,time;from pathlib import Path;p=Path('/tmp/gludd-watchdog-push-timestamps.json');d=json.loads(p.read_text()) if p.exists() else [];d.append(time.time());p.write_text(json.dumps(d[-50:]))" 2>/dev/null || true

# Same as git-push-sandboxcom but skips the pre-push hook (detect-secrets +
# collect-check local gate). Use when the local 21k-test gate is non-viable
# and CI is the gate. The _push-rate-guard (CI-pending / cooldown / thrash)
# is STILL enforced. Mirrors commit-no-verify for the push side.
git-push-sandboxcom-nv: check-clean-tree _push-rate-guard _stash-before-push-guard _pull-before-push-guard _ci-verdict-history-guard _ci-restart-cap
	@$(MAKE) --no-print-directory _runtime-publish-admission RUNTIME_PUBLISH_ADMISSION_VALIDATE_ONLY=0
	@BRANCH=$$(git branch --show-current); \
	GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push --no-verify -u sandboxcom HEAD:$$BRANCH
	@echo "Pushed $$(git branch --show-current) to sandboxcom/gludd (--no-verify)"
	@$(MAKE) --no-print-directory _record-push-verdict
	@python3 -c "import json,time;from pathlib import Path;p=Path('/tmp/gludd-watchdog-push-timestamps.json');d=json.loads(p.read_text()) if p.exists() else [];d.append(time.time());p.write_text(json.dumps(d[-50:]))" 2>/dev/null || true

# Push only the committed HEAD for the current branch. This is for CI candidate
# runs from a dirty integration checkout; uncommitted files are not included.
git-push-current-head-nv: check-clean-tree _push-rate-guard
	@$(MAKE) --no-print-directory _runtime-publish-admission RUNTIME_PUBLISH_ADMISSION_VALIDATE_ONLY=0
	@BRANCH=$$(git branch --show-current); \
	if [ -z "$$BRANCH" ]; then echo "Cannot push detached HEAD"; exit 1; fi; \
	GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push --no-verify -u sandboxcom HEAD:$$BRANCH
	@echo "Pushed committed HEAD to sandboxcom/gludd"
	@python3 -c "import json,time;from pathlib import Path;p=Path('/tmp/gludd-watchdog-push-timestamps.json');d=json.loads(p.read_text()) if p.exists() else [];d.append(time.time());p.write_text(json.dumps(d[-50:]))" 2>/dev/null || true

git-push-current-head-to-master-nv: check-clean-tree _push-rate-guard
	@$(MAKE) --no-print-directory _runtime-publish-admission RUNTIME_PUBLISH_ADMISSION_VALIDATE_ONLY=0
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

# Publication owns the canonical runtime/release policy and existing bounded observer.
RUNTIME_PUBLISH_ENVIRONMENT ?=
RUNTIME_PUBLISH_ADMISSION_VALIDATE_ONLY ?= 0
.PHONY: _runtime-publish-admission
_runtime-publish-admission:
	@case "$(RUNTIME_PUBLISH_ADMISSION_VALIDATE_ONLY)" in 0|1) ;; *) echo 'RUNTIME_PUBLISH_ADMISSION_VALIDATE_ONLY must be 0 or 1'; exit 2;; esac
	@if [ "$(RUNTIME_PUBLISH_ADMISSION_VALIDATE_ONLY)" = 1 ]; then \
		echo "RUNTIME-PUBLISH-ADMISSION-PLAN environment=$(RUNTIME_PUBLISH_ENVIRONMENT) manifest_check=exact-pinned prerequisite_check=check-gate-failure-promotions prerequisite_max_seconds=$(or $(RUNTIME_PUBLISH_MAX_SECS),90) prerequisite_quiet_seconds=$(or $(RUNTIME_PUBLISH_QUIET_SECS),60) max_seconds=$(or $(RUNTIME_PUBLISH_MAX_SECS),90) quiet_seconds=$(or $(RUNTIME_PUBLISH_QUIET_SECS),60) profile=release exact_sha_resume=0 max_files_per_batch=16 gate_workers=1 pytest_trace=1 observed_root=$(OBSERVED_ROOT) release_ready=false gate_evidence=false"; exit 0; \
	fi; \
	runtime_environment="$(RUNTIME_PUBLISH_ENVIRONMENT)"; \
	if [ -z "$$runtime_environment" ]; then \
		resource_root="$$($(PYTHON) scripts/resource_arbiter.py root)" || exit $$?; \
		runtime_environment="$${resource_root}-toolchain/ci-shards/python-3.11"; \
	fi; \
	[ -x "$$runtime_environment/bin/python" ] || { echo "Runtime publication admission requires the existing canonical Python 3.11 environment: $$runtime_environment" >&2; exit 2; }; \
	"$$runtime_environment/bin/python" -c 'import importlib.util, sys; sys.version_info[:2] == (3, 11) or sys.exit("runtime publication requires Python 3.11"); importlib.util.find_spec("pytest_playwright") is not None or sys.exit("runtime publication requires the installed Playwright pytest fixture plugin")' || exit $$?; \
	$(MAKE) --no-print-directory check-ansible-base-image ANSIBLE_EE_BASE_IMAGE_CHECK_VALIDATE_ONLY=0 \
		UV_PROJECT_ENVIRONMENT="$$runtime_environment" VIRTUAL_ENV="$$runtime_environment" \
		UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never \
		|| { manifest_rc=$$?; echo "Pinned Ansible base manifest admission failed (exit $$manifest_rc); runtime replay was not started; Git transport was not attempted." >&2; exit $$manifest_rc; }; \
	prerequisite_run_id="batch-push-prerequisite-admission-$$$$"; \
	UV_PROJECT_ENVIRONMENT="$$runtime_environment" VIRTUAL_ENV="$$runtime_environment" UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never \
	$(MAKE) --no-print-directory run-watched WORK_ITEM_ID="$(WORK_ITEM_ID)" \
		UV_PROJECT_ENVIRONMENT="$$runtime_environment" VIRTUAL_ENV="$$runtime_environment" \
		UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never \
		GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1 RUN_WATCHED_PYTEST_TRACE=1 \
		CMD='$(MAKE) --no-print-directory check-gate-failure-promotions GATE_FAILURE_PROMOTION_MANIFEST=config/gate_failure_promotions.json' \
		OBSERVED_LABEL=batch-push-prerequisite-admission RUN_ID="$$prerequisite_run_id" STALL_SECS=$(or $(RUNTIME_PUBLISH_QUIET_SECS),60) MAX_SECS=$(or $(RUNTIME_PUBLISH_MAX_SECS),90) \
		LOG="$(OBSERVED_ROOT)/batch-push-prerequisite-admission/$$prerequisite_run_id.log" \
		OBSERVED_ROOT="$(OBSERVED_ROOT)" OBSERVED_HEARTBEAT_SECS=10 OBSERVED_RETAIN_RUNS="$(or $(OBSERVED_RETAIN_RUNS),20)" \
		|| { prerequisite_rc=$$?; echo "Canonical promotion prerequisite failed (exit $$prerequisite_rc); runtime replay was not started; Git transport was not attempted." >&2; exit $$prerequisite_rc; }; \
	runtime_run_id="batch-push-runtime-admission-$$$$"; \
	UV_PROJECT_ENVIRONMENT="$$runtime_environment" VIRTUAL_ENV="$$runtime_environment" UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never \
	$(MAKE) --no-print-directory run-watched WORK_ITEM_ID="$(WORK_ITEM_ID)" \
		UV_PROJECT_ENVIRONMENT="$$runtime_environment" VIRTUAL_ENV="$$runtime_environment" \
		UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never \
		GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1 RUN_WATCHED_PYTEST_TRACE=1 \
		CMD='$(MAKE) --no-print-directory _pytest-runtime-compatibility-admission' \
		OBSERVED_LABEL=batch-push-runtime-admission RUN_ID="$$runtime_run_id" STALL_SECS=$(or $(RUNTIME_PUBLISH_QUIET_SECS),60) MAX_SECS=$(or $(RUNTIME_PUBLISH_MAX_SECS),90) \
		LOG="$(OBSERVED_ROOT)/batch-push-runtime-admission/$$runtime_run_id.log" \
		OBSERVED_ROOT="$(OBSERVED_ROOT)" OBSERVED_HEARTBEAT_SECS=10 OBSERVED_RETAIN_RUNS="$(or $(OBSERVED_RETAIN_RUNS),20)" \
		|| { runtime_rc=$$?; echo "Runtime publication admission failed (exit $$runtime_rc); Git transport was not attempted." >&2; exit $$runtime_rc; }

# Batch push: only push after substantial local work (default 5+ unpushed commits).
# Override: GLUDD_FORCE_PUSH=1. COMMIT_THRESHOLD=1 is blocked.
# This is the RECOMMENDED push target. Use instead of git-push-sandboxcom directly.
batch-push: check-clean-tree _no-bypass-guard _stash-before-push-guard _pull-before-push-guard _ci-verdict-history-guard _pre-commit-stash-audit _ci-restart-cap _push-rate-guard
	@if [ "$(_publication-native-mode)" != execute ]; then echo "AGENT_NEXT target=batch-push outcome=planned mode=$(_publication-native-mode) gate_evidence=false release_ready=false: Native inspection starts no publication, runtime admission or hosted observation." >&2; exit 0; fi; \
	COUNT=$$(git log --oneline @{u}..HEAD 2>/dev/null | wc -l | tr -d ' '); \
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
	gludd_batch_head=; BRANCH=; gludd_batch_writes=false; \
	gludd_batch_fail() { gludd_batch_rc=$$1; gludd_batch_phase=$$2; \
		if [ "$$gludd_batch_phase" = hosted-signal ]; then gludd_batch_next='Transport and history may already have applied; reconcile the saved branch/fullSHA and actual run or refusal through ci-trigger-committed-head; do not repush unchanged source merely to request CI.'; \
		else gludd_batch_next='Preserve the actual failed owner and native reason; reconcile the saved branch/fullSHA and potentially applied remote/history before retry. No unchanged repush supplies missing proof.'; fi; \
		printf 'AGENT_NEXT target=batch-push outcome=failed phase=%s native_status=%s branch=%s sha=%s transport_may_have_applied=%s gate_evidence=false release_ready=false: %s\n' "$$gludd_batch_phase" "$$gludd_batch_rc" "$$BRANCH" "$$gludd_batch_head" "$$gludd_batch_writes" "$$gludd_batch_next" >&2; exit "$$gludd_batch_rc"; }; \
	BRANCH=$$(git branch --show-current) || gludd_batch_fail "$$?" candidate; \
	gludd_batch_head=$$(git rev-parse --verify HEAD^{commit}) || gludd_batch_fail "$$?" candidate; \
	[ -n "$$BRANCH" ] || gludd_batch_fail 2 candidate; \
	gludd_batch_expected_branch="$$GLUDD_PUBLISH_EXPECTED_BRANCH"; gludd_batch_expected_sha="$$GLUDD_PUBLISH_EXPECTED_SHA"; \
	if [ -n "$$gludd_batch_expected_branch" ] || [ -n "$$gludd_batch_expected_sha" ]; then \
		[ -n "$$gludd_batch_expected_branch" ] && [ "$${#gludd_batch_expected_sha}" -eq 40 ] || gludd_batch_fail 2 expected-input; \
		case "$$gludd_batch_expected_sha" in *[!0123456789abcdefABCDEF]*) gludd_batch_fail 2 expected-input ;; esac; \
		git check-ref-format "refs/heads/$$gludd_batch_expected_branch" || gludd_batch_fail "$$?" expected-input; \
		gludd_batch_expected_sha=$$(printf '%s' "$$gludd_batch_expected_sha" | tr ABCDEF abcdef) || gludd_batch_fail "$$?" expected-input; \
		if [ "$$gludd_batch_expected_branch" != "$$BRANCH" ] || [ "$$gludd_batch_expected_sha" != "$$gludd_batch_head" ]; then gludd_batch_fail 2 expected-identity; fi; \
	fi; \
	$(MAKE) --no-print-directory _runtime-publish-admission RUNTIME_PUBLISH_ADMISSION_VALIDATE_ONLY=0 || gludd_batch_fail "$$?" runtime; \
	gludd_batch_current_branch=$$(git branch --show-current) || gludd_batch_fail "$$?" identity; \
	gludd_batch_current_head=$$(git rev-parse --verify HEAD^{commit}) || gludd_batch_fail "$$?" identity; \
	if [ "$$gludd_batch_current_branch" != "$$BRANCH" ] || [ "$$gludd_batch_current_head" != "$$gludd_batch_head" ]; then gludd_batch_fail 2 identity; fi; \
	gludd_batch_writes=true; \
	GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push -u sandboxcom "$$gludd_batch_head:refs/heads/$$BRANCH" || { \
		PUSH_RC=$$?; echo "Push failed for $$BRANCH (exit $$PUSH_RC); success was not recorded." >&2; \
		echo "AGENT_NEXT target=batch-push outcome=failed phase=transport native_status=$$PUSH_RC gate_evidence=false release_ready=false: Preserve the native Git reason and intended candidate SHA. Missing workflow scope requires actual credential authorization; do not retry unchanged publication or adopt an older remote as candidate proof. Use make github-auth-status to inspect actual scopes. For missing workflow scope, preview make github-workflow-auth GITHUB_WORKFLOW_AUTH_VALIDATE_ONLY=1 GITHUB_WORKFLOW_AUTH_ACCOUNT='' before requesting operator consent for mode 0. After the prerequisite changes, run make verify-state then normal make batch-push and exact-SHA remote/CI verification." >&2; \
		exit "$$PUSH_RC"; \
	}; \
	git branch --set-upstream-to="sandboxcom/$$BRANCH" "$$BRANCH" || gludd_batch_fail "$$?" tracking; \
	echo "Pushed $$BRANCH to sandboxcom/gludd ($$COUNT commits)"; \
	$(MAKE) --no-print-directory _record-push-verdict PUSH_VERDICT_SHA="$$gludd_batch_head" PUBLISH_EXPECTED_BRANCH="$$BRANCH" PUBLISH_EXPECTED_SHA="$$gludd_batch_head" PUBLISH_PURPOSE=canonical || gludd_batch_fail "$$?" verdict; \
	$(MAKE) --no-print-directory verify-remote BRANCH="$$BRANCH" SHA="$$gludd_batch_head" || gludd_batch_fail "$$?" remote-verification; \
	$(MAKE) --no-print-directory ci-trigger-committed-head PUBLISH_PURPOSE=canonical \
		PUBLISH_EXPECTED_BRANCH="$$BRANCH" PUBLISH_EXPECTED_SHA="$$gludd_batch_head" PUBLISH_BASELINE_REF= PUBLISH_BASELINE_SHA= \
		REF="$$BRANCH" REMOTE=sandboxcom REPO=sandboxcom/gludd WORKFLOW='Build and Release' EXAMPLE=0 \
		WORK_ITEM_ID='$(subst ','"'"',$(value WORK_ITEM_ID))' || gludd_batch_fail "$$?" hosted-signal; \
	printf 'AGENT_NEXT target=batch-push outcome=published native_status=0 branch=%s sha=%s gate_evidence=false release_ready=false: The saved source is verified remotely and the existing exact-SHA dispatcher returned an accepted or reused Build run. Observe that actual run; terminal CI, canonical push, deployment and release proof remain required.\n' "$$BRANCH" "$$gludd_batch_head" >&2

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
