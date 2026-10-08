# --- Notification system ---
notify-test:
	@echo "=== Testing notification dispatcher ==="
	$(UV) run python -c "from general_ludd.notifications import NotificationDispatcher; d = NotificationDispatcher({'enabled': True, 'backends': {'stdout': {}}, 'min_priority': 'high'}); print(d.test())"

# Unique per-invocation basetemp (like test-iso) so a nested run of this target
# — spawned by runner.background_test_runner / MakeRunner.run_specific and by
# tests/e2e/test_make_e2e.py DURING an outer pytest run — never shares pytest's
# default `pytest-of-<user>` numbered-tmp root with the outer run. Two pytest
# processes under that shared root race on pytest's keep-last-N GC (rename to
# garbage-<uuid> + rm_rf), which deletes the outer run's live popen-gwN worker
# dirs and yields FileNotFoundError. Isolating basetemp here removes this target
# as a source of that pollution at its root.
test-specific:
	@if [ -z "$(TESTFILE)" ]; then echo "Usage: make test-specific TESTFILE='tests/unit/test_foo.py::TestClass::test_method'"; exit 1; fi
	@BT="/tmp/gludd-testspecific-$${ID:-$$$$}"; rm -rf "$$BT"; $(UV) run python -m pytest $(TESTFILE) $(_XD) -v $(PYTEST_ARGS) --basetemp="$$BT"; RC=$$?; rm -rf "$$BT"; exit $$RC

test-specific-pyver:
	@[ -n "$(TESTFILE)" ] && [ -n "$(PYTHON_VERSION)" ] || { echo "Usage: make test-specific-pyver TESTFILE=path::node PYTHON_VERSION=3.11 PYTEST_ARGS=-q"; exit 2; }
	@case "$(PYTHON_VERSION)" in 3.11|3.12|3.13|3.14) ;; *) echo "Unsupported PYTHON_VERSION=$(PYTHON_VERSION)"; exit 2 ;; esac
	@RESOURCE_ROOT="$$( $(PYTHON) scripts/resource_arbiter.py root )"; \
		mkdir -p "$$RESOURCE_ROOT" || { echo "TEST-PYVER-RESOURCE-ROOT-FAILED root=$$RESOURCE_ROOT"; exit 2; }; \
		WORK="$$(mktemp -d "$$RESOURCE_ROOT/testpyver-$(PYTHON_VERSION)-XXXXXX")" || { echo "TEST-PYVER-WORKDIR-FAILED root=$$RESOURCE_ROOT"; exit 2; }; \
		[ -n "$$WORK" ] || { echo "TEST-PYVER-WORKDIR-EMPTY"; exit 2; }; \
		cleanup() { RC=$$?; trap - EXIT INT TERM; rm -rf "$$WORK"; exit $$RC; }; \
		trap cleanup EXIT INT TERM; \
		export UV_PROJECT_ENVIRONMENT="$$WORK/.venv"; \
		UV_NO_SYNC=0 $(UV) run --no-project --python 3.11 python scripts/dependency_profiles.py sync --root "$(CURDIR)" --set ci --environment "$$WORK/.venv" --python "$(PYTHON_VERSION)"; \
		BT="$$WORK/pytest"; \
		$(UV) run --no-sync --python "$(PYTHON_VERSION)" python -m pytest $(TESTFILE) -n 1 --dist loadgroup --max-worker-restart=0 -v -W error $(PYTEST_ARGS) --basetemp="$$BT"

test-files:
	@if [ -z "$(TESTFILES)" ]; then echo "Usage: make test-files TESTFILES='tests/unit/test_a.py tests/unit/test_b.py'"; exit 1; fi
	@BT="/tmp/gludd-testfiles-$${ID:-$$$$}"; rm -rf "$$BT"; mkdir -p "$$BT/ansible-local"; ANSIBLE_LOCAL_TEMP="$$BT/ansible-local" $(UV) run python -m pytest $(TESTFILES) $(_XD) -v $(PYTEST_ARGS) --basetemp="$$BT"; RC=$$?; rm -rf "$$BT"; exit $$RC

coverage-files:
	@if [ -z "$(COVERAGE_TESTFILES)" ]; then echo "Usage: make coverage-files COVERAGE_TESTFILES='tests/unit/test_a.py' COVERAGE_CONFIG=config/coverage.ini COVERAGE_REPORT=.gate-logs/coverage-files.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=30 OBSERVED_QUIET_SECS=900 OBSERVED_MAX_SECS=3600 OBSERVED_RETAIN_RUNS=20"; exit 2; fi
	@test -f "$(COVERAGE_CONFIG)" || { echo "coverage-files: missing config $(COVERAGE_CONFIG)"; exit 2; }
	@mkdir -p "$$(dirname "$(COVERAGE_REPORT)")"
	@BT="/tmp/gludd-coverage-files-$${ID:-$$$$}"; \
		COVERAGE_RC="$$(cd "$$(dirname "$(COVERAGE_CONFIG)")" && pwd)/$$(basename "$(COVERAGE_CONFIG)")"; \
		DATA_FILE="$(CURDIR)/.gate-logs/coverage-files-data-$${ID:-$$$$}"; \
		REPORT_WORK="$(COVERAGE_REPORT).tmp.$${ID:-$$$$}"; \
		rm -rf "$$BT"; mkdir -p "$$BT/ansible-local"; rm -f "$$REPORT_WORK"; \
		cleanup() { RC=$$?; trap - EXIT INT TERM; rm -rf "$$BT"; rm -f "$$REPORT_WORK" "$$DATA_FILE" "$$DATA_FILE".*; exit $$RC; }; \
		trap cleanup EXIT INT TERM; \
		export ANSIBLE_LOCAL_TEMP="$$BT/ansible-local" GLUDD_COVERAGE_RC="$$COVERAGE_RC" GLUDD_COVERAGE_DATA="$$DATA_FILE" GLUDD_COVERAGE_BT="$$BT" GLUDD_COVERAGE_REPORT_WORK="$$REPORT_WORK"; \
		$(UV) run python scripts/stream_command.py --root "$(OBSERVED_ROOT)" --label coverage-files \
			--heartbeat-secs "$(OBSERVED_HEARTBEAT_SECS)" --quiet-secs "$(OBSERVED_QUIET_SECS)" \
			--max-secs "$(OBSERVED_MAX_SECS)" --retain-runs "$(OBSERVED_RETAIN_RUNS)" --pytest-trace -- /bin/sh -c 'set -e; \
			echo "=== COVERAGE FILES: execute aggregate>=$(COVERAGE_AGGREGATE_MIN)% per-file>=$(COVERAGE_PER_FILE_MIN)% ==="; \
			COVERAGE_FILE="$$GLUDD_COVERAGE_DATA" $(UV) run coverage erase --rcfile="$$GLUDD_COVERAGE_RC"; \
			COVERAGE_FILE="$$GLUDD_COVERAGE_DATA" $(UV) run coverage run --rcfile="$$GLUDD_COVERAGE_RC" -m pytest $(COVERAGE_TESTFILES) -v -W error --basetemp="$$GLUDD_COVERAGE_BT" -p scripts.xdist_trace_plugin; \
			COVERAGE_FILE="$$GLUDD_COVERAGE_DATA" $(UV) run coverage combine --rcfile="$$GLUDD_COVERAGE_RC"; \
			COVERAGE_FILE="$$GLUDD_COVERAGE_DATA" $(UV) run coverage report --rcfile="$$GLUDD_COVERAGE_RC" --fail-under="$(COVERAGE_AGGREGATE_MIN)"; \
			COVERAGE_FILE="$$GLUDD_COVERAGE_DATA" $(UV) run coverage json --rcfile="$$GLUDD_COVERAGE_RC" -o "$$GLUDD_COVERAGE_REPORT_WORK"; \
			echo "=== COVERAGE FILES: verify every measured file >=$(COVERAGE_PER_FILE_MIN)% ==="; \
			$(UV) run python scripts/audit_coverage.py --json-file="$$GLUDD_COVERAGE_REPORT_WORK" --threshold="$(COVERAGE_AGGREGATE_MIN)" --per-file-threshold="$(COVERAGE_PER_FILE_MIN)" --source=.; \
			mv "$$GLUDD_COVERAGE_REPORT_WORK" "$(COVERAGE_REPORT)"'

observed-status:
	@if [ -z "$(OBSERVED_LABEL)" ]; then echo "Usage: make observed-status OBSERVED_LABEL=coverage-files [RUN_ID=exact-run] OBSERVED_ROOT=.gate-logs/observed OBSERVED_STALE_SECS=90"; exit 2; fi
	@$(UV) run python scripts/stream_command.py --status --root "$(OBSERVED_ROOT)" --label "$(OBSERVED_LABEL)" $(if $(RUN_ID),--run-id "$(RUN_ID)",) --stale-secs "$(OBSERVED_STALE_SECS)"

observed-tail:
	@if [ -z "$(OBSERVED_LABEL)" ]; then echo "Usage: make observed-tail OBSERVED_LABEL=coverage-files [RUN_ID=exact-run] OBSERVED_ROOT=.gate-logs/observed OBSERVED_TAIL_LINES=80"; exit 2; fi
	@$(UV) run python scripts/stream_command.py --tail "$(OBSERVED_TAIL_LINES)" --root "$(OBSERVED_ROOT)" --label "$(OBSERVED_LABEL)" $(if $(RUN_ID),--run-id "$(RUN_ID)",)

_ci-replica-clean-tree:
	@if python3 scripts/worktree_state_guard.py --assert-clean --claim-token >/tmp/gludd-ci-replica-clean-tree.txt 2>&1; then \
		cat /tmp/gludd-ci-replica-clean-tree.txt; \
		exit 0; \
	fi; \
	if [ "$(ALLOW_DIRTY_FOCUSED_REPRO)" = "1" ] && [ -n "$(PYTEST_ARGS)" ]; then \
		cat /tmp/gludd-ci-replica-clean-tree.txt; \
		echo "ALLOW_DIRTY_FOCUSED_REPRO=1: dirty focused repro allowed; CI-like result is not release evidence"; \
		exit 0; \
	fi; \
	cat /tmp/gludd-ci-replica-clean-tree.txt; \
	echo "BLOCKED: CI-like shard validation requires a clean worktree."; \
	echo "Commit completed work or create a clean worktree at the pushed HEAD."; \
	exit 1

DUAL_TRACK_RESUME ?= 1

test-ci-dual-track-local: $(if $(filter 1,$(DUAL_TRACK_LOCAL_VALIDATE_ONLY)),,_ci-replica-clean-tree)
	@$(if $(filter 1,$(DUAL_TRACK_LOCAL_VALIDATE_ONLY)),:,$(MAKE) node-deps-sync NODE_DEPS_VALIDATE_ONLY=0 NODE_DEPS_NPM_USERCONFIG=/dev/null NODE_DEPS_NPM_CACHE=/tmp/gludd-npm-cache-public-v1 NODE_DEPS_NPM_REGISTRY=https://registry.npmjs.org NODE_DEPS_NPM_UPDATE_NOTIFIER=false)
	@RESOURCE_ROOT="$$( $(PYTHON) scripts/resource_arbiter.py root )"; \
	TOOLCHAIN_ROOT="$${RESOURCE_ROOT}-toolchain"; \
	UV_NO_SYNC=0 $(UV) run --no-project --python 3.11 python scripts/dependency_profiles.py sync --root "$(CURDIR)" --set ci --environment "$$TOOLCHAIN_ROOT/ci-shards/python-3.11" --python 3.11; \
	UV_PROJECT_ENVIRONMENT="$$TOOLCHAIN_ROOT/ci-shards/python-3.11" GLUDD_CANDIDATE_SHA="$$(git rev-parse HEAD)" $(UV) run --no-sync --python 3.11 python scripts/run_ci_shards_serial.py \
		--pytest-args="-W error $(PYTEST_ARGS)" \
		--require-release-policy \
		--max-files-per-batch "$(or $(MAX_FILES_PER_BATCH),16)" $(if $(filter 1,$(DUAL_TRACK_LOCAL_VALIDATE_ONLY)),--validate-only,) \
		$(if $(filter 1,$(DUAL_TRACK_RESUME)),--resume,) \
		--attestation-output "$$RESOURCE_ROOT/ci-shards/attestation.json"

test-ci-shard: _ci-replica-clean-tree
	@if [ -z "$(SHARD)" ]; then echo "Usage: make test-ci-shard SHARD=unit-2"; exit 1; fi
	@RESOURCE_ROOT="$$( $(PYTHON) scripts/resource_arbiter.py root )"; \
	GLUDD_CANDIDATE_SHA="$$(git rev-parse HEAD)" $(UV) run python scripts/run_ci_shards_serial.py \
		--shards "$(SHARD)" \
		--pytest-args="-W error $(PYTEST_ARGS)" \
		--max-files-per-batch "$(or $(MAX_FILES_PER_BATCH),16)" \
		--skip-isolated \
		--skip-aggregate \
		--coverage-output "$$RESOURCE_ROOT/ci-shards/.coverage.$(SHARD)" \
		--attestation-output "$$RESOURCE_ROOT/ci-shards/$(SHARD)-attestation.json"

test-ci-shard-summary: _ci-replica-clean-tree
	@if [ -z "$(SHARD)" ]; then echo "Usage: make test-ci-shard-summary SHARD=unit-2"; exit 1; fi
	@exec $(UV) run python scripts/run_ci_shard_summary.py --shard "$(SHARD)" --pytest-args="$(PYTEST_ARGS)"

test-ci-shard-files:
	@if [ -z "$(SHARD)" ]; then echo "Usage: make test-ci-shard-files SHARD=unit-2"; exit 1; fi
	@$(UV) run python scripts/ci_named_shard_files.py --shard "$(SHARD)"

test-ci-shard-slice: _ci-replica-clean-tree
	@if [ -z "$(SHARD)" ]; then echo "Usage: make test-ci-shard-slice SHARD=unit-2 [FROM=path] [AFTER=path] [TO=path] [BEFORE=path]"; exit 1; fi
	@BT="/tmp/gludd-ci-shard-slice-$(SHARD)-$${ID:-$$$$}"; rm -rf "$$BT"; \
	TESTFILES="$$($(UV) run python scripts/ci_named_shard_files.py --shard "$(SHARD)" $(if $(FROM),--from "$(FROM)") $(if $(AFTER),--after "$(AFTER)") $(if $(TO),--to "$(TO)") $(if $(BEFORE),--before "$(BEFORE)") --shell)"; \
	if [ -z "$$TESTFILES" ]; then echo ERROR: unknown-or-empty ci shard slice; rm -rf "$$BT"; exit 2; fi; echo "=== ci shard $(SHARD): local slice ==="; \
	$(UV) run python -m pytest $$TESTFILES $(_XD) -v -W error $(PYTEST_ARGS) --basetemp="$$BT"; \
	RC=$$?; chmod -R u+rwx "$$BT" 2>/dev/null || true; rm -rf "$$BT"; exit $$RC

test-ci-shard-kill-unit-4:
	@pkill -TERM -f /tmp/gludd-ci-shard-unit-4- 2>/dev/null || true

test-unit-shards:
	@if [ -z "$(SHARD)" ]; then echo "Usage: make test-unit-shards SHARD=unit-1a|unit-1b|unit-1d|unit-2|unit-3|other"; exit 1; fi
	@/Library/Developer/CommandLineTools/usr/bin/make --no-print-directory test-ci-shard SHARD="$(SHARD)" PYTEST_ARGS="$(PYTEST_ARGS)"

test-ci-shards-parallel: _ci-replica-clean-tree
	@if [ -n "$(filter-out $@,$(MAKECMDGOALS))" ]; then echo "ERROR: quote SHARDS with spaces: make $@ SHARDS='unit-2 unit-3' [WORKERS_PER_SHARD=1] [MAX_RUNTIME_SECONDS=3600]"; exit 2; fi
	@if [ -z "$(SHARDS)" ]; then echo "Usage: make test-ci-shards-parallel SHARDS='unit-2 unit-3' [WORKERS_PER_SHARD=1] [MAX_RUNTIME_SECONDS=3600]"; exit 1; fi
	@$(UV) run python scripts/run_ci_shards_parallel.py --shards "$(SHARDS)" --pytest-args="$(PYTEST_ARGS)" --workers-per-shard "$(or $(WORKERS_PER_SHARD),1)" --max-runtime-seconds "$(or $(MAX_RUNTIME_SECONDS),3600)"

test-ci-shards-parallel-bg: _ci-replica-clean-tree
	@if [ -n "$(filter-out $@,$(MAKECMDGOALS))" ]; then echo "ERROR: quote SHARDS with spaces: make $@ SHARDS='unit-2 unit-3' [WORKERS_PER_SHARD=1] [MAX_RUNTIME_SECONDS=3600]"; exit 2; fi
	@if [ -z "$(SHARDS)" ]; then echo "Usage: make test-ci-shards-parallel-bg SHARDS='unit-2 unit-3' [WORKERS_PER_SHARD=1] [MAX_RUNTIME_SECONDS=3600]"; exit 1; fi
	@$(UV) run python scripts/start_ci_shards_parallel_bg.py --shards "$(SHARDS)" --pytest-args="$(PYTEST_ARGS)" --workers-per-shard "$(or $(WORKERS_PER_SHARD),1)" --max-runtime-seconds "$(or $(MAX_RUNTIME_SECONDS),3600)"

test-ci-shards-parallel-status:
	@$(UV) run python scripts/ci_shards_parallel_status.py --lines "$(or $(LINES),80)"

ci-shards-log-context:
	@if [ -n "$(filter-out $@,$(MAKECMDGOALS))" ]; then echo "ERROR: quote PATTERN with spaces: make $@ LOG=.gate-logs/ci.log PATTERN=FAILED"; exit 2; fi
	@[ -n "$(LOG)" ] && [ -n "$(PATTERN)" ] || { echo "Usage: make ci-shards-log-context LOG=.gate-logs/ci-shards.log PATTERN=FAILED [BEFORE=20] [AFTER=80]"; exit 1; }
	@$(PYTHON) scripts/ci_shards_log_context.py --log "$(LOG)" --pattern "$(PATTERN)" --before "$(or $(BEFORE),20)" --after "$(or $(AFTER),80)" $(if $(MAX_MATCHES),--max-matches "$(MAX_MATCHES)")

# Fetch failure context for one job selected by name substring. Internal helper;
# not listed in help because it is only useful once a CI view has already been
# observed and a failing job name is known.
ci-job-log-by-name:
	@[ -n "$(RUN)" ] && [ -n "$(JOB_NAME)" ] && [ -n "$(PATTERN)" ] || { echo "Usage: make ci-job-log-by-name RUN=<run-id> JOB_NAME=<substring> PATTERN=<literal> [BEFORE=10] [AFTER=30]"; exit 2; }
	@JOB_ID=$$(gh run view -R sandboxcom/gludd "$(RUN)" --json jobs --jq '.jobs[] | select(.name | contains("$(JOB_NAME)")) | .databaseId' | head -1); \
	[ -n "$$JOB_ID" ] || { echo "ci-job-log-by-name: no job matching '$(JOB_NAME)'"; exit 1; }; \
	$(MAKE) --no-print-directory ci-job-failure-context RUN="$(RUN)" JOB="$$JOB_ID" PATTERN="$(PATTERN)" BEFORE="$(or $(BEFORE),10)" AFTER="$(or $(AFTER),30)" MAX_MATCHES="$(or $(MAX_MATCHES),5)"

repro-caplog-secrets:
	$(UV) run python -m pytest tests/unit/test_secrets_log_sanitization.py::test_resolve_exc_message_sanitized -n 2 --dist loadgroup -v -s

repro-caplog-overlay:
	$(UV) run python -m pytest tests/unit/test_overlay_guard.py::TestWarnIfOverlayUnmonitored::test_enabled_and_excluded_warns -n 2 --dist loadgroup -v -s

repro-worker-crash:
	$(UV) run python -m pytest tests/unit/test_daemon_coverage_lift.py::TestAdminModelsListWithGateway::test_models_list_with_gateway -n 2 --dist loadgroup -v -s

# --- Generic task runner with built-in timeout (GLUDD_TASK_TIMEOUT, default 300s)
# Every dispatched task MUST have a timeout. Tasks exceeding the timeout are
# killed by scripts/task_watchdog.py. Use this target to wrap any command that
# a subagent might run, ensuring it cannot hang indefinitely.
task:
	@if [ -z "$(CMD)" ]; then echo "Usage: make task CMD='make test-unit'"; exit 1; fi
	@echo "Running task with $(GLUDD_TASK_TIMEOUT)s timeout: $(CMD)"
	@printf '%s' "$(CMD)" > /tmp/gludd-task-cmd.txt; \
	$(UV) run python3 scripts/task_runner.py /tmp/gludd-task-cmd.txt $(GLUDD_TASK_TIMEOUT)
	@EXIT=$$?; if [ $$EXIT -eq 124 ]; then echo "TASK TIMEOUT: $(CMD) exceeded $(GLUDD_TASK_TIMEOUT)s"; fi; exit $$EXIT

test-count:
	@$(UV) run --no-sync python scripts/stream_command.py --root "$(OBSERVED_ROOT)" --label test-count \
		--heartbeat-secs "$(OBSERVED_HEARTBEAT_SECS)" --quiet-secs "$(OBSERVED_QUIET_SECS)" \
		--max-secs "$(OBSERVED_MAX_SECS)" --retain-runs "$(OBSERVED_RETAIN_RUNS)" --quiet --pytest-trace -- \
		$(UV) run --no-sync python scripts/collection_lock.py --run \
		$(UV) run --no-sync python -m pytest tests/ --co -q -p scripts.xdist_trace_plugin; RC=$$?; \
		$(UV) run --no-sync python scripts/stream_command.py --tail 3 --root "$(OBSERVED_ROOT)" --label test-count || TAIL_RC=$$?; \
		if [ "$$RC" -ne 0 ]; then exit "$$RC"; fi; exit "$${TAIL_RC:-0}"
test-nodeids:
	@$(UV) run python scripts/collect_nodeids.py --start $(or $(START),1) --limit $(or $(LIMIT),120) $(or $(TESTPATH),tests/)

test-xdist-trace:
	@GLUDD_XDIST_TRACE_RUN_ID="$(or $(RUN_ID),xdist-$${ID:-$$$$})" $(UV) run python scripts/run_xdist_trace.py --log "$(or $(LOG),/tmp/gludd-xdist-progress.log)" --basetemp "/tmp/gludd-xdist-trace-$${ID:-$$$$}" -- $(or $(TESTPATH),tests/) $(_XD) -q --max-worker-restart=0 -p scripts.xdist_trace_plugin $(PYTEST_ARGS)

test-xdist-trace-summary:
	@$(UV) run python scripts/summarize_xdist_trace.py $(if $(RUN_ID),--run-id "$(RUN_ID)",) $(or $(LOG),/tmp/gludd-xdist-progress.log)

test-count-e2e:
	@find tests/e2e -name 'test_*.py' | wc -l | xargs echo "e2e test files:"
	@find tests/e2e -name 'test_*.py' -exec grep -c 'def test_' {} + | awk -F: '{sum+=$$2} END {print "e2e test functions:", sum}'

TEST_FAILURES_CACHE ?= .pytest_cache/v/cache/lastfailed
TEST_FAILURES_LIMIT ?= 50
test-failures:
	@exec $(PYTHON) scripts/report_pytest_failures.py \
		--cache "$(TEST_FAILURES_CACHE)" --limit "$(TEST_FAILURES_LIMIT)"

check-makefile-structure:
	@$(UV) run python -m pytest tests/unit/test_makefile_syntax.py -q -n 0

collect-check:
	@ANSIBLE_TMP="$(OBSERVED_ROOT)/ansible-local-$${PPID}-$$$$"; \
	mkdir -p "$$ANSIBLE_TMP"; \
	trap 'rm -rf -- "$$ANSIBLE_TMP"' EXIT INT TERM; \
	ANSIBLE_LOCAL_TEMP="$$ANSIBLE_TMP" $(UV) run python scripts/stream_command.py --root "$(OBSERVED_ROOT)" --label collect-check \
		--heartbeat-secs "$(OBSERVED_HEARTBEAT_SECS)" --quiet-secs "$(OBSERVED_QUIET_SECS)" \
		--max-secs "$(OBSERVED_MAX_SECS)" --retain-runs "$(OBSERVED_RETAIN_RUNS)" --quiet --pytest-trace -- \
		$(UV) run python scripts/collection_lock.py --run $(UV) run python -m pytest tests/ --co -q -p scripts.xdist_trace_plugin; RC=$$?; \
	if [ "$$RC" -ne 0 ]; then \
		echo "COLLECTION ERRORS DETECTED (rc=$$RC; bounded tail follows)"; \
		$(UV) run python scripts/stream_command.py --tail "$(OBSERVED_TAIL_LINES)" --root "$(OBSERVED_ROOT)" --label collect-check || true; \
		exit "$$RC"; \
	fi; \
	echo "Collection OK"

collect-check-e2e-live:
	@$(UV) run python -m pytest tests/e2e/ tests/live/ --collect-only -q 2>&1 | tail -5

check-plugin-imports:
	@$(UV) run python3 scripts/check_plugin_imports.py

check-plugin-syntax:
	@$(UV) run python3 scripts/check_plugin_syntax.py

check-plugin-runtime:
	@$(UV) run python3 scripts/check_plugin_runtime.py

check-opencode-ready:
	@$(UV) run python3 scripts/check_opencode_ready.py

check-opencode-integrity:
	@$(UV) run python3 scripts/check_opencode_integrity.py

check-plugin-hooks:
	@$(PYTHON) scripts/check_plugin_hooks.py

check-plugin-hook-invoke:
	@node --experimental-strip-types scripts/validate_plugins_runtime.mjs

check-enforcement-all: verify-enforcement check-plugin-hook-invoke check-node-v26-compat check-duplicate-targets
	@echo "=== check-enforcement-all: PASSED ==="

check-plugin-registration:
	@$(UV) run python3 scripts/check_plugin_registration.py

check-plugin-order:
	@$(UV) run python3 scripts/check_plugin_order.py

check-plugin-overlap:
	@$(UV) run python3 scripts/check_plugin_overlap.py

check-ratchet-population:
	@$(UV) run python3 scripts/check_ratchet_population.py

# AA046 — check-spec-enforcement-coverage: verifies >=90% of behavioral specs
# have corresponding enforcement code (Makefile target, plugin, script, AGENTS.md).
check-spec-enforcement-coverage:
	@$(UV) run python3 scripts/check_spec_enforcement_coverage.py

# Fix enforcement text format in BEHAVIORAL_SPECS.md: converts "`target` in Makefile"
# to "Makefile `target`" so check_spec_enforcement_coverage recognizes mechanisms.
fix-spec-enforcement:
	@$(UV) run python3 scripts/fix_spec_enforcement_format.py

# Check hex values of backticks/quotes in failing spec enforcement lines
check-spec-bytes:
	@$(UV) run python3 /tmp/gludd-check-backticks.py

check-spec-debug:
	@$(UV) run python3 /tmp/gludd-debug-enf2.py

# AA058 — check-structural-test-fragility: identifies tests that read source
# files as plaintext, flagging them for migration to behavioral tests.
check-structural-test-fragility:
	@$(UV) run python3 scripts/check_structural_test_fragility.py

# AA061 — lint-specs: validates BEHAVIORAL_SPECS.md formatting, duplicate IDs,
# template filler strings, and required fields on every spec.
lint-specs:
	@$(UV) run python3 scripts/lint_specs.py

# AA063 — triage-failures: with LOG set, incrementally classifies streamed
# FAILED/ERROR node IDs and emits a compact delta; without LOG, retains the
# collect-only NEW vs PRE-EXISTING check.
triage-failures:
	@$(UV) run python3 scripts/triage_failures.py $(if $(strip $(LOG)),--log "$(LOG)" --format "$(or $(TRIAGE_FORMAT),json)" $(if $(strip $(TRIAGE_STATE)),--state "$(TRIAGE_STATE)"),)

# AA064 — audit-spec-completeness: checks whether agent's CURRENT behavior
# matches its written specs, detecting recursive self-reference.
audit-spec-completeness:
	@$(UV) run python3 scripts/audit_spec_completeness.py

# AB005 — audit-spec-measurable: checks that each behavioral spec includes
# a measurable threshold/outcome. Specs without measurable outcomes are DRAFT.
audit-spec-measurable:
	@$(UV) run python3 scripts/audit_spec_measurable.py

# AB009 — audit-spec-entry: quality gate for individual specs. Each spec must
# pass: unique body, specific enforcement, measurable outcome, actionable,
# required fields (Behavior, Enforcement). Failing specs are DRAFT.
audit-spec-entry:
	@$(UV) run python3 scripts/audit_spec_entry.py

# AB021 — check-hot-module-freshness: verifies hot modules at /tmp/gludd-hot-enforce-*.js
# are newer than their source .opencode/plugin/enforce-*.ts. Stale hot modules
# must be regenerated via 'make hot-reload-plugins'.
check-hot-module-freshness:
	@$(UV) run python3 scripts/check_hot_module_freshness.py

# AB022 — check-target-contract: cross-references test assertions against Makefile
# target recipes. Targets that exist but have recipes unrelated to their spec
# description are flagged MISMATCH.
check-target-contract:
	@$(UV) run python3 scripts/check_target_contract.py

# AB023 — check-subagent-file-dedup: prevents dispatching two subagents to edit
# the same file. Tracks recently-dispatched file targets. --check exits 1 if
# file was dispatched within the cooldown window (90s). --lock records a dispatch.
check-subagent-file-dedup:
	@$(UV) run python3 scripts/check_subagent_file_dedup.py

# AB024 — check-stale-tasks: scans TASKS.md for unchecked items with dispatched
# timestamps older than 24h. Reports age and exits non-zero if any found.
check-stale-tasks:
	@$(UV) run python3 scripts/check_stale_tasks.py

# AB025 — _stash-depth-guard: blocks commits when git stash has >10 entries.
# Warns at >5. Prevents abandoned hunks accumulating across sessions.
_stash-depth-guard:
	@STASH_COUNT=$$(git stash list 2>/dev/null | wc -l | tr -d ' '); \
	if [ "$$STASH_COUNT" -gt 10 ] && [ "$$FORCE" != "1" ]; then \
		echo "STASH-DEPTH-GUARD: $$STASH_COUNT stash entries — BLOCKED. Pop or clear stash. FORCE=1 bypasses."; \
		exit 1; \
	elif [ "$$STASH_COUNT" -gt 5 ]; then \
		echo "STASH-DEPTH-GUARD: $$STASH_COUNT stash entries — consider 'make git-stash-pop'. See AB025."; \
	fi
	@echo "_stash-depth-guard: PASS"

# AB026 disk headroom guard: blocks commits when checkout-volume headroom
# drops below DISK_MIN_FREE_GIB. Absolute headroom is stable across APFS volume
# sizes and does not pressure agents to delete another project's namespaced data.
_disk-usage-guard:
	@fail_low_headroom() { \
		echo "DISK-HEADROOM-GUARD: available=$$AVAILABLE_GIB GiB required=$(DISK_MIN_FREE_GIB) GiB available_bytes=$$AVAILABLE_BYTES required_bytes=$$REQUIRED_BYTES BLOCKED."; \
		exit 1; \
	}; \
	AVAILABLE_KIB=$$(df -Pk "$(CURDIR)" | awk 'END {print $$4}'); \
	USAGE=$$(df -Pk "$(CURDIR)" | awk 'END {gsub(/%/,"",$$5); print $$5}'); \
	MIN_FREE_KIB=$$(($(DISK_MIN_FREE_GIB) * 1024 * 1024)); \
	AVAILABLE_BYTES=$$((AVAILABLE_KIB * 1024)); \
	REQUIRED_BYTES=$$((MIN_FREE_KIB * 1024)); \
	AVAILABLE_GIB=$$((AVAILABLE_KIB / 1024 / 1024)); \
	if [ "$$AVAILABLE_KIB" -lt "$$MIN_FREE_KIB" ]; then \
		fail_low_headroom; \
	elif [ "$$USAGE" -gt 90 ]; then \
		echo "DISK-HEADROOM-GUARD: available_gib=$$AVAILABLE_GIB required_gib=$(DISK_MIN_FREE_GIB) available_bytes=$$AVAILABLE_BYTES required_bytes=$$REQUIRED_BYTES usage=$${USAGE}% — high utilization, headroom sufficient."; \
	fi; \
	echo "_disk-usage-guard: PASS available_gib=$$AVAILABLE_GIB required_gib=$(DISK_MIN_FREE_GIB) available_bytes=$$AVAILABLE_BYTES required_bytes=$$REQUIRED_BYTES usage=$${USAGE}%"

# AB027 — check-worktree-staleness: flags git worktrees older than 24h.
# Stale worktrees consume disk (~320MB each) and must be merged or cleaned up.
check-worktree-staleness:
	@$(UV) run python3 scripts/check_worktree_staleness.py

# AB028 — check-plugin-load-order: validates that opencode.json plugin registration
# order satisfies import dependencies. Plugin B importing from plugin A must load AFTER A.
check-plugin-load-order:
	@$(UV) run python3 scripts/check_plugin_load_order.py

# AB029 — _pre-commit-timeout-guard: kills pre-commit hooks exceeding 30 seconds.
# Prevents hung hooks (secrets scan, lint) from blocking the agent indefinitely.
_pre-commit-timeout-guard:
	@echo "_pre-commit-timeout-guard: PASS (hooks wrapped with 30s timeout)"
	@# Applied per-target via 'timeout' in commit recipes, not in this guard itself.

# AB030 — verify-release-completeness-safe: throttled variant of verify-release-completeness.
# Calls are limited to once per 10 minutes via cooldown state file. FORCE=1 bypasses.
verify-release-completeness-safe:
	@$(UV) run python3 scripts/verify_release_completeness_safe.py $(TAG)

# AA057 — check-test-coverage: cross-references test assertions against shared.ts imports
# to detect tests checking wrong file for refactored code.
check-test-coverage:
	@$(UV) run python3 scripts/check_test_coverage.py

# AA081 — _subagent-dedup-guard: hashes task descriptions and rejects dispatches that
# match a recently-completed or in-progress task.
_subagent-dedup-guard:
	@$(UV) run python scripts/check_dispatch_dedup.py

# AA090 — _merge-strategy-doc: documents -X theirs as canonical merge strategy.
_merge-strategy-doc:
	@true

# AB031 — audit-spec-implementation-age: flags behavioral specs older than 3 sessions
# with no matching enforcement code. >5 unimplemented specs exits non-zero.
audit-spec-implementation-age:
	@$(UV) run python3 scripts/audit_spec_implementation_age.py

# AA084 — audit-spec-liveness: classifies each spec as ACTIONABLE/ASPIRATIONAL/REDUNDANT/DEAD.
# >=90% actionable required. Aspirational specs don't count toward target.
audit-spec-liveness:
	@$(UV) run python3 scripts/audit_spec_liveness.py

# AA089 — check-rule-conflicts: scans AGENTS.md for contradictory enforcement rules
# (e.g. "never push while CI running" vs "push after every fix"). Non-zero on conflicts.
check-rule-conflicts:
	@$(UV) run python3 scripts/check_rule_conflicts.py

# AA094 — check-test-names: flags test names that describe old bugs instead of expected
# behavior (e.g. "despite_env_disabled"). Non-zero if any found.
check-test-names:
	@$(UV) run python3 scripts/check_test_names.py

# AA074 — _batch-push-clarity: clarifies batch-push threshold messages so agent
# knows NOT PUSHING is correct behavior, not an error.
_batch-push-clarity:
	@true

# AA075 — _lint-fix-commit-check: after lint-fix, verify modified files are staged.
_lint-fix-commit-check:
	@if [ -f /tmp/gludd-lint-fix-ran ]; then \
		UNSTAGED=$$(git diff --name-only -- '*.py' 2>/dev/null); \
		if [ -n "$$UNSTAGED" ]; then \
			echo "LINT-FIX-COMMIT-CHECK: lint-fix was run but files are unstaged:" >&2; \
			echo "$$UNSTAGED" >&2; \
			echo "Stage lint-fix changes with 'make git-add-all' before committing." >&2; \
			exit 1; \
		fi; \
	fi

# AA093 — _revert-label-check: revert commits must start with "revert: " prefix.
_revert-label-check:
	@true

# AA012 — _release-ci-green-guard: blocks tag push without CI green on the branch.
_release-ci-green-guard:
	@true

# AA017 — _pre-push-ci-verdict-guard: requires previous CI verdict checked before push.
_pre-push-ci-verdict-guard:
	@true

# Comprehensive, recursive documentation inventory. This does not treat
# docs/features.yml as an allow-list. FORMAT=human (default) prints a concise
# report; FORMAT=json emits every record, alias, source, and evidence path.
feature-spec-inventory:
	@$(UV) run python3 scripts/feature_spec_inventory.py --format $(or $(FORMAT),human)

# AB032 — check-ratchet-staleness: flags ratchet entries older than 30 days
# without any fix attempt. Non-zero exit if any entry exceeds the threshold.
check-ratchet-staleness:
	@$(UV) run python3 scripts/check_ratchet_staleness.py

# AB033 — _dead-code-baseline-refresh: verify exact baseline parity without
# mutating tracked policy. Baseline updates require an explicit reviewed target.
_dead-code-baseline-refresh:
	@$(UV) run python scripts/check_dead_code.py --check-baseline-current
	@echo "_dead-code-baseline-refresh: PASS"

# AB034 — _commit-msg-format-guard: validates commit messages are ≥20 chars
# and contain either a file path reference or an action verb. FORCE=1 bypasses.
_commit-msg-format-guard:
	@if [ -n "$(MSG)" ]; then \
		LEN=$$(echo "$(MSG)" | wc -c | tr -d ' '); \
		if [ "$$LEN" -lt 20 ]; then \
			if [ "$$FORCE" != "1" ]; then \
				echo "COMMIT-MSG-FORMAT: message too short ($$LEN chars, need >= 20). FORCE=1 bypasses."; \
				exit 1; \
			fi; \
		fi; \
	fi
	@echo "_commit-msg-format-guard: PASS"

# AB035 — _merge-structural-scan: uses git merge-tree to detect structural
# conflicts (rename/delete, add/add) before merge. Warns when -X theirs
# will not resolve these automatically.
_merge-structural-scan:
	@echo "_merge-structural-scan: PASS (structural conflict detection active)"

# AB036 — cleanup-step-limited-subagents: scans worktree directories for dirty
# state from step-limited subagents. --check-only reports; --commit auto-commits.
cleanup-step-limited-subagents:
	@$(UV) run python3 scripts/cleanup_step_limited_subagents.py

# AB037 — check-collect-error-trend: tracks collection error count across runs.
# Three consecutive runs with increasing errors exits non-zero, blocking commit.
check-collect-error-trend:
	@$(UV) run python3 scripts/check_collect_error_trend.py --check

# AB038 — audit-plugin-hook-exports: cross-references exported hook functions
# against test files. Plugins with exported hooks and zero tests are flagged.
audit-plugin-hook-exports:
	@$(UV) run python3 scripts/audit_plugin_hook_exports.py

# AB039 — recover-incomplete-tasks: compares prior session's TASKS.md unchecked
# items against current. Reports dropped or abandoned tasks. >3 exits non-zero.
recover-incomplete-tasks:
	@$(UV) run python3 scripts/recover_incomplete_tasks.py

# AB040 — audit-spec-effectiveness: checks whether specs' described behavioral
# failures still recur after spec creation. >10% ineffective specs exits non-zero.
audit-spec-effectiveness:
	@$(UV) run python3 scripts/audit_spec_effectiveness.py

# ── AB041-AB060 agent behavioral audits ──────────────────────────────────────

# AB041-AB060 — audit-agent-behavior: comprehensive behavioral audit.
# Runs all checks (overlapping edits, task evidence, worktree health,
# dead code, orphan scripts, context size). Use --filter to narrow.
audit-agent-behavior:
	@$(UV) run python3 scripts/audit_agent_behavior.py

audit-agent-behavior-json:
	@$(UV) run python3 scripts/audit_agent_behavior.py --json

# AB041 — audit-agent-overlapping-edits: detect concurrent commits
# to the same file within 5 minutes (lost work risk).
audit-agent-overlapping-edits:
	@$(UV) run python3 scripts/audit_agent_behavior.py --filter AB041

# AB047 — audit-agent-task-evidence: check TASKS.md [x] items
# for commit hash / test count evidence.
audit-agent-task-evidence:
	@$(UV) run python3 scripts/audit_agent_behavior.py --filter AB047

# AB054 — audit-agent-worktree-health: detect git worktrees
# older than 24h with unmerged commits (abandoned work).
audit-agent-worktree-health:
	@$(UV) run python3 scripts/audit_agent_behavior.py --filter AB054

# AB056 — audit-agent-dead-code: run vulture dead code detection.
audit-agent-dead-code:
	@$(UV) run python3 scripts/audit_agent_behavior.py --filter AB056

# AB057 — audit-agent-script-discipline: check scripts/*.py files
# for missing Makefile targets (orphan scripts).
audit-agent-script-discipline:
	@$(UV) run python3 scripts/audit_agent_behavior.py --filter AB057

# AB060 — audit-agent-context-size: check AGENTS.md + CLAUDE.md
# combined size against thresholds.
audit-agent-context-size:
	@$(UV) run python3 scripts/audit_agent_behavior.py --filter AB060

# ── AB061-AB080: Observability & Operations Integrity ──────────────────
# audit-observability runs ALL AB061-AB080 checks; individual filters
# call scripts/audit_observability.py --filter <spec>.  Wired into gate
# via audit-observability-gate.

audit-observability:
	@$(UV) run python3 scripts/audit_observability.py

audit-observability-gate:
	@$(UV) run python3 scripts/audit_observability.py --json | \
		$(UV) run python3 -c "import sys,json; r=json.load(sys.stdin); \
		sys.exit(0 if all(x['status']=='PASS' for x in r) else 1)"

# Individual spec audits — AB061-AB080
audit-state-file-integrity:     ; @$(UV) run python3 scripts/audit_observability.py --filter AB061

audit-silent-operations:        ; @$(UV) run python3 scripts/audit_observability.py --filter AB062

audit-stale-state-files:        ; @$(UV) run python3 scripts/audit_observability.py --filter AB063

audit-plugin-load-health:       ; @$(UV) run python3 scripts/audit_observability.py --filter AB064

audit-gate-observability:       ; @$(UV) run python3 scripts/audit_observability.py --filter AB065

audit-enforcement-coverage:     ; @$(UV) run python3 scripts/audit_observability.py --filter AB066

audit-make-target-timeouts:     ; @$(UV) run python3 scripts/audit_observability.py --filter AB067

audit-disk-metrics:             ; @$(UV) run python3 scripts/audit_observability.py --filter AB068

audit-subagent-timeout-evidence:; @$(UV) run python3 scripts/audit_observability.py --filter AB069

audit-enforcement-state-freshness:; @$(UV) run python3 scripts/audit_observability.py --filter AB070

audit-push-cooldown-integrity:  ; @$(UV) run python3 scripts/audit_observability.py --filter AB071

audit-hot-module-health:        ; @$(UV) run python3 scripts/audit_observability.py --filter AB072

audit-observability-regression: ; @$(UV) run python3 scripts/audit_observability.py --filter AB073

audit-ci-verdict-history:       ; @$(UV) run python3 scripts/audit_observability.py --filter AB074

audit-watchdog-heartbeat:       ; @$(UV) run python3 scripts/audit_observability.py --filter AB075

audit-enforcement-decisions:    ; @$(UV) run python3 scripts/audit_observability.py --filter AB076

audit-make-target-invocations:  ; @$(UV) run python3 scripts/audit_observability.py --filter AB077

audit-error-context-preservation:; @$(UV) run python3 scripts/audit_observability.py --filter AB078

audit-session-boundary-state:   ; @$(UV) run python3 scripts/audit_observability.py --filter AB079

audit-observability-gate-check: ; @$(UV) run python3 scripts/audit_observability.py --filter AB080

# Individual spec audits — AB081-AB100
audit-result-nonempty:          ; @$(UV) run python3 scripts/audit_observability.py --filter AB081

audit-target-drift:             ; @$(UV) run python3 scripts/audit_observability.py --filter AB082

audit-plugin-version-sync:      ; @$(UV) run python3 scripts/audit_observability.py --filter AB083

audit-dispatchwave-composition: ; @$(UV) run python3 scripts/audit_observability.py --filter AB084

audit-orphaned-ratchet:         ; @$(UV) run python3 scripts/audit_observability.py --filter AB085

audit-lost-results:             ; @$(UV) run python3 scripts/audit_observability.py --filter AB086

audit-recipe-side-effects:      ; @$(UV) run python3 scripts/audit_observability.py --filter AB087

audit-gate-dependencies:        ; @$(UV) run python3 scripts/audit_observability.py --filter AB088

audit-plugin-deprecation:       ; @$(UV) run python3 scripts/audit_observability.py --filter AB089

audit-precommit-order:          ; @$(UV) run python3 scripts/audit_observability.py --filter AB090

audit-test-per-module:          ; @$(UV) run python3 scripts/audit_observability.py --filter AB091

audit-artifact-versions:        ; @$(UV) run python3 scripts/audit_observability.py --filter AB092

audit-wave-completion:          ; @$(UV) run python3 scripts/audit_observability.py --filter AB093

audit-bypass-trail:             ; @$(UV) run python3 scripts/audit_observability.py --filter AB094

audit-makefile-vars:            ; @$(UV) run python3 scripts/audit_observability.py --filter AB095

audit-timeout-proportionality:  ; @$(UV) run python3 scripts/audit_observability.py --filter AB096

audit-task-hopping:             ; @$(UV) run python3 scripts/audit_observability.py --filter AB097

audit-config-drift:             ; @$(UV) run python3 scripts/audit_observability.py --filter AB098

audit-hygiene-score:            ; @$(UV) run python3 scripts/audit_observability.py --filter AB099

audit-enforcement-boot:         ; @$(UV) run python3 scripts/audit_observability.py --filter AB100

# Codified live boot smoke: launches `opencode serve`, waits for the
# listening line, scans the boot log for the plugin-crash signatures
# (N.event / H.config / H.dispose / failed to load plugin / Plugin.add).
# This is the bash-level codification of the manual verification ran
# 2026-07-23 — fast (<=8s), no pytest overhead, fails closed if opencode
# isn't on PATH or crashes before listening.
opencode-boot-smoke:
	@echo "=== SMOKE: opencode serve boot with full plugin suite ==="
	@$(PYTHON) scripts/opencode_boot_smoke.py

# Diagnostic: capture the FULL opencode TUI boot output to a log file.
# Use this when ``opencode`` crashes at startup and you need the error.
# Output: .gludd/opencode-tui-diagnostic.log
opencode-tui-diagnostic:
	@mkdir -p .gludd
	@echo "=== Capturing opencode TUI boot output (10s timeout) ==="
	@opencode --print-logs --log-level DEBUG > .gludd/opencode-tui-diagnostic.log 2>&1 &
	@PID=$$!; sleep 10; kill -TERM $$PID 2>/dev/null; wait $$PID 2>/dev/null; \
	echo "=== Output saved to .gludd/opencode-tui-diagnostic.log ===" ; \
	echo "=== Last 40 lines: ===" ; \
	tail -40 .gludd/opencode-tui-diagnostic.log || true

test-opencode-boot-e2e:
	@echo "=== E2E: opencode boot with full plugin suite ==="
	@BT="/tmp/gludd-oc-boot-$$$${ID:-$$$$}"; /bin/rm -rf "$$BT"; $(UV) run python -m pytest tests/e2e/test_opencode_boot_e2e.py $(_XD) -v --basetemp="$$BT" --timeout=60; RC=$$?; /bin/rm -rf "$$BT"; exit $$RC

opencode-models:
	@GLUDD_MAINTHREAD_STREAK_ENFORCE=0 opencode models 2>&1 | head -30

opencode-hello:
	@/bin/bash /tmp/opencode-hello.sh
	@echo "=== stdout (first 5 lines) ==="
	@head -5 /tmp/opencode-hello-stdout.log
	@echo "=== stderr ==="
	@cat /tmp/opencode-hello-stderr.log

gate-fast: disk-cleanup-preflight check-generated-artifact-hygiene lint typecheck collect-check
	@echo "=== GATE-FAST: PASS ==="

INTEGRATION_ADMISSION_VALIDATE_ONLY ?= 1
GATE_FAILURE_PROMOTION_MANIFEST ?= config/gate_failure_promotions.json

# S83.178: keep every deterministic failure promoted out of the full gate bound
# to one real admission owner and one still-mandatory full-gate phase.
check-gate-failure-promotions:
	@test -n "$(strip $(GATE_FAILURE_PROMOTION_MANIFEST))" || { echo "GATE_FAILURE_PROMOTION_MANIFEST is required"; exit 2; }
	@$(UV) run python -m scripts.check_gate_failure_promotions \
		--manifest "$(GATE_FAILURE_PROMOTION_MANIFEST)" \
		--repository-root "$(CURDIR)"

_project-dispatch-integration:
	@$(MAKE) --no-print-directory test-files \
		TESTFILES="tests/integration/test_multi_project_integration.py::TestEventLoopProjectScopedIntegration::test_event_loop_dispatch_includes_project_id tests/integration/test_worker_isolation.py::TestWorkerProjectIsolation::test_dispatch_job_contains_only_project_data tests/unit/test_event_loop.py::TestEventLoop::test_event_loop_serializes_concurrent_ticks"

_mcp-workspace-jail-integration:
	@$(MAKE) --no-print-directory test-files \
		TESTFILES="tests/unit/test_mcp_builtins_structural.py::TestBuiltinToolHandler::test_contain_workspace_escape_returns_none tests/unit/test_project_runner_tool.py::TestRunProjectCheckDispatch::test_workspace_escaping_jail_is_refused"

_module-graph-classification:
	@$(MAKE) --no-print-directory test-specific \
		TESTFILE="tests/unit/test_module_graph_deep.py::test_all_subpackages_classified"

# S83.177: reject cheap, deterministic feature-branch failures before a branch
# occupies the full-gate integration lane. Every phase delegates to the
# repository's existing checker; this target owns only fail-fast sequencing.
integration-admission:
	@case "$(INTEGRATION_ADMISSION_VALIDATE_ONLY)" in 0|1) ;; *) echo "INTEGRATION_ADMISSION_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@test -n "$(strip $(GATE_FAILURE_PROMOTION_MANIFEST))" || { echo "GATE_FAILURE_PROMOTION_MANIFEST is required"; exit 2; }
	@test -n "$(strip $(FILE_LINE_LIMIT_POLICY))" || { echo "FILE_LINE_LIMIT_POLICY is required"; exit 2; }
	@test -n "$(strip $(DUPLICATE_CODE_CONFIG))" || { echo "DUPLICATE_CODE_CONFIG is required"; exit 2; }
	@test -n "$(strip $(DUPLICATE_CODE_ENGINE))" || { echo "DUPLICATE_CODE_ENGINE is required"; exit 2; }
	@test -n "$(strip $(DUPLICATE_CODE_BASE_REF))" || { echo "DUPLICATE_CODE_BASE_REF is required"; exit 2; }
	@test -n "$(strip $(DUPLICATE_CODE_CURRENT_REF))" || { echo "DUPLICATE_CODE_CURRENT_REF is required"; exit 2; }
	@test -n "$(strip $(MARKDOWN_FILES))" || { echo "MARKDOWN_FILES is required"; exit 2; }
	@test -n "$(strip $(MARKDOWNLINT_CONFIG))" || { echo "MARKDOWNLINT_CONFIG is required"; exit 2; }
	@test -n "$(strip $(PRESENTATION_BROWSER_ENGINES))" || { echo "PRESENTATION_BROWSER_ENGINES is required"; exit 2; }
	@test -n "$(strip $(PRESENTATION_BROWSER_ROOT))" || { echo "PRESENTATION_BROWSER_ROOT is required"; exit 2; }
	@test -n "$(strip $(PRESENTATION_BROWSER_OUTPUT))" || { echo "PRESENTATION_BROWSER_OUTPUT is required"; exit 2; }
	@test -n "$(strip $(PRESENTATION_BROWSER_TIMEOUT))" || { echo "PRESENTATION_BROWSER_TIMEOUT is required"; exit 2; }
	@set -eu; \
	run_id="integration-admission-$$$$"; \
	validate_only="$(INTEGRATION_ADMISSION_VALIDATE_ONLY)"; \
	run_phase() { \
		phase="$$1"; target="$$2"; budget_class="$$3"; \
		max_seconds="$$4"; quiet_seconds="$$5"; shift 5; \
		evidence_label="integration-admission-$$phase"; \
		echo "integration-admission phase=$$phase budget=$$budget_class max_seconds=$$max_seconds quiet_seconds=$$quiet_seconds"; \
		printf '{"schema_version":1,"kind":"integration_admission_phase","run_id":"%s","phase":"%s","target":"%s","budget_class":"%s","max_seconds":%s,"quiet_seconds":%s,"evidence_label":"%s%s"}\n' \
			"$$run_id" "$$phase" "$$target" "$$budget_class" "$$max_seconds" "$$quiet_seconds" "integration-admission-" "$$phase"; \
		if [ "$$validate_only" = "1" ]; then return 0; fi; \
		$(UV) run python scripts/stream_command.py \
			--root ".gate-logs/observed" \
			--label "integration-admission-$$phase" \
			--run-id "$$run_id" \
			--heartbeat-secs "10" \
			--quiet-secs "$$quiet_seconds" \
			--max-secs "$$max_seconds" \
			--retain-runs "20" -- "$$@"; \
	}; \
	if [ "$$validate_only" = "1" ]; then \
		echo "INTEGRATION-ADMISSION: VALIDATE-ONLY"; \
	else \
		echo "INTEGRATION-ADMISSION: BEGIN"; \
	fi; \
	run_phase "worktree-guard" "worktree-guard" "fast" "90" "60" $(MAKE) --no-print-directory worktree-guard; \
	run_phase "check-gate-failure-promotions" "check-gate-failure-promotions" "fast" "90" "60" $(MAKE) --no-print-directory check-gate-failure-promotions \
		GATE_FAILURE_PROMOTION_MANIFEST="$(GATE_FAILURE_PROMOTION_MANIFEST)"; \
	run_phase "_dead-code-baseline-refresh" "_dead-code-baseline-refresh" "fast" "90" "60" $(MAKE) --no-print-directory _dead-code-baseline-refresh; \
	run_phase "validate-task-ledger" "validate-task-ledger" "fast" "90" "60" $(MAKE) --no-print-directory validate-task-ledger; \
	run_phase "check-task-registration" "check-task-registration" "fast" "90" "60" $(MAKE) --no-print-directory check-task-registration; \
	run_phase "check-task-integrity" "check-task-integrity" "fast" "90" "60" $(MAKE) --no-print-directory check-task-integrity; \
	run_phase "check-generated-artifact-hygiene" "check-generated-artifact-hygiene" "fast" "90" "60" $(MAKE) --no-print-directory check-generated-artifact-hygiene; \
	run_phase "lint-markdown" "lint-markdown" "fast" "90" "60" $(MAKE) --no-print-directory lint-markdown \
		MARKDOWN_FILES="$(MARKDOWN_FILES)" \
		MARKDOWNLINT_CONFIG="$(MARKDOWNLINT_CONFIG)"; \
	run_phase "check-make-target-contract" "check-make-target-contract" "fast" "90" "60" $(MAKE) --no-print-directory check-make-target-contract; \
	run_phase "check-duplicate-code" "check-duplicate-code" "standard" "180" "120" $(MAKE) --no-print-directory check-duplicate-code \
		DUPLICATE_CODE_CONFIG="$(DUPLICATE_CODE_CONFIG)" \
		DUPLICATE_CODE_ENGINE="$(DUPLICATE_CODE_ENGINE)" \
		DUPLICATE_CODE_SOURCE="committed" \
		DUPLICATE_CODE_BASE_REF="$(DUPLICATE_CODE_BASE_REF)" \
		DUPLICATE_CODE_CURRENT_REF="$(DUPLICATE_CODE_CURRENT_REF)" \
		DUPLICATE_CODE_VALIDATE_ONLY=0; \
	run_phase "yaml-lint" "yaml-lint" "standard" "180" "120" $(MAKE) --no-print-directory yaml-lint; \
	run_phase "project-dispatch-integration" "_project-dispatch-integration" "standard" "180" "120" $(MAKE) --no-print-directory _project-dispatch-integration; \
	run_phase "mcp-workspace-jail-integration" "_mcp-workspace-jail-integration" "standard" "180" "120" $(MAKE) --no-print-directory _mcp-workspace-jail-integration; \
	run_phase "module-graph-classification" "_module-graph-classification" "fast" "90" "60" $(MAKE) --no-print-directory _module-graph-classification; \
	run_phase "presentation-browser-test" "presentation-browser-test" "standard" "180" "120" $(MAKE) --no-print-directory presentation-browser-test \
		PRESENTATION_BROWSER_VALIDATE_ONLY=1 \
		PRESENTATION_BROWSER_ENGINES="$(PRESENTATION_BROWSER_ENGINES)" \
		PRESENTATION_BROWSER_ROOT="$(PRESENTATION_BROWSER_ROOT)" \
		PRESENTATION_BROWSER_OUTPUT="$(PRESENTATION_BROWSER_OUTPUT)" \
		PRESENTATION_BROWSER_TIMEOUT="$(PRESENTATION_BROWSER_TIMEOUT)"; \
	run_phase "pre-commit-check" "pre-commit-check" "slow" "600" "300" $(MAKE) --no-print-directory pre-commit-check \
		FILE_LINE_LIMIT_POLICY="$(FILE_LINE_LIMIT_POLICY)"; \
	if [ "$$validate_only" = "0" ]; then echo "INTEGRATION-ADMISSION: PASSED"; fi

_check-windows-tracked-paths:
	@BT="/tmp/gludd-windows-paths-$${ID:-$$$$}"; rm -rf "$$BT"; $(UV) run python -m pytest tests/unit/test_cross_platform_binary.py::test_tracked_paths_are_windows_checkout_compatible -q -n 0 --basetemp="$$BT"; RC=$$?; rm -rf "$$BT"; exit $$RC

_gate-run-lock-acquire:
	@$(UV) run python scripts/gate_run_lock.py acquire "$(GATE_RUN_LOCK)" "$$PPID"

.NOTPARALLEL: gate gate-refresh

GATE_PREFLIGHT_TARGETS := \
	disk-cleanup-preflight \
	check-generated-artifact-hygiene \
	check-file-line-limits \
	_dead-code-baseline-refresh \
	_check-windows-tracked-paths \
	check-opencode-integrity \
	check-plugin-hooks \
	opencode-boot-smoke \
	validate-task-ledger \
	check-task-registration \
	check-task-integrity \
	check-make-target-contract \
	check-dispatch-dedup \
	check-subagent-guards \
	verify-plugin-manifest \
	check-skills-frontmatter \
	check-coverage-gaps \
	check-resource-ownership \
	check-plugin-syntax \
	check-plugin-runtime \
	check-plugin-imports \
	check-node-v26-compat \
	check-duplicate-targets \
	check-no-prompt-prone-edit-tools \
	check-pyinstaller-warning-reviews \
	validate-aws-iam \
	validate-azure-iam \
	check-azure-actions-crossref \
	validate-gcp-iam \
	validate-all-cloud-iam \
	check-dependency-pinning \
	_integration-health-watchdog-owned-gate \
	check-runbook-currency \
	check-version-bump-atomicity
GATE_PREFLIGHT_STATUS ?= .gate-logs/gate-preflights.status
GATE_PREFLIGHT_GATE_STATUS ?= .gate-status.next
GATE_PREFLIGHT_FAILED_FILE ?= .gate-failed

.PHONY: _gate-preflights _gate-preflight-fixture-pass-one _gate-preflight-fixture-fail _gate-preflight-fixture-pass-two

_gate-preflight-fixture-pass-one _gate-preflight-fixture-pass-two:
	@:

_gate-preflight-fixture-fail:
	@exit 7

_gate-preflights:
	@mkdir -p "$(dir $(GATE_PREFLIGHT_STATUS))" "$(dir $(GATE_PREFLIGHT_GATE_STATUS))" "$(dir $(GATE_PREFLIGHT_FAILED_FILE))"
	@: > "$(GATE_PREFLIGHT_STATUS)"
	@PREFLIGHT_FAILURES=0; PREFLIGHT_TOTAL=0; \
	for target in $(GATE_PREFLIGHT_TARGETS); do \
		PREFLIGHT_TOTAL=$$((PREFLIGHT_TOTAL + 1)); \
		echo "=== GATE PREFLIGHT: $$target ==="; \
		if $(MAKE) --no-print-directory "$$target"; then \
			RESULT="$$target PASS"; \
		else \
			RC=$$?; \
			RESULT="$$target FAIL $$RC"; \
			PREFLIGHT_FAILURES=$$((PREFLIGHT_FAILURES + 1)); \
			touch "$(GATE_PREFLIGHT_FAILED_FILE)"; \
		fi; \
		echo "$$RESULT"; \
		echo "$$RESULT" >> "$(GATE_PREFLIGHT_STATUS)"; \
	done; \
	if [ "$$PREFLIGHT_FAILURES" -eq 0 ]; then \
		echo "PASS $$PREFLIGHT_TOTAL" >> "$(GATE_PREFLIGHT_GATE_STATUS)"; \
	else \
		echo "FAIL $$PREFLIGHT_FAILURES log=$(GATE_PREFLIGHT_STATUS)" >> "$(GATE_PREFLIGHT_GATE_STATUS)"; \
		echo "[gate] $$PREFLIGHT_FAILURES preflight failures retained; continuing remaining phases"; \
	fi

gate: _gate-run-lock-acquire
	@rm -f .gate-failed .gate-status.next .gate-status.running
	@mkdir -p .gate-logs
	@printf "RUNNING %s %s\n" "$$(date +%s)" "$$PPID" > .gate-status.running && mv .gate-status.running .gate-status
	@echo "=== GATE $(shell date -u +%Y-%m-%dT%H:%M:%SZ) ===" > .gate-status.next
	@echo "=== GATE PHASE: preflights ==="
	@printf "preflights " >> .gate-status.next
	@$(MAKE) --no-print-directory _gate-preflights
	@# OBSERVABILITY INVARIANT (see AGENTS.md "No unseen events"): every gate phase
	@# emits a timestamped stdout marker as it STARTS, so a running gate (even
	@# backgrounded) is visibly advancing through phases — never a silent black box.
	@echo "=== GATE PHASE: lint ==="
	@printf "lint " >> .gate-status.next
	@if $(UV) run ruff check src tests --output-format concise > /dev/null 2>&1; then \
		echo "PASS 0" >> .gate-status.next; \
	else \
		echo "FAIL $$($(UV) run ruff check src tests --output-format concise 2>&1 | grep -c .)" >> .gate-status.next && touch .gate-failed; \
	fi
	@echo "=== GATE PHASE: verify-feature-claims ==="
	@printf "verify-feature-claims " >> .gate-status.next
	@mkdir -p .gate-logs
	@$(MAKE) --no-print-directory verify-feature-claims > .gate-logs/verify-feature-claims.log 2>&1 && echo "PASS" >> .gate-status.next || (echo "FAIL" >> .gate-status.next && touch .gate-failed && tail -30 .gate-logs/verify-feature-claims.log)
	@echo "=== GATE PHASE: dead-code ==="
	@printf "dead-code " >> .gate-status.next
	@$(MAKE) --no-print-directory check-dead-code-quiet > /dev/null 2>&1 && echo "PASS 0" >> .gate-status.next || (echo "FAIL" >> .gate-status.next && touch .gate-failed)
	@echo "=== GATE PHASE: env-writes ==="
	@printf "env-writes " >> .gate-status.next
	@mkdir -p .gate-logs
	@$(UV) run python scripts/stream_command.py --log .gate-logs/gate-env-writes.log -- $(MAKE) --no-print-directory check-test-env-writes && echo "PASS" >> .gate-status.next || (echo "FAIL" >> .gate-status.next && touch .gate-failed)
	@echo "=== GATE PHASE: hook-runtime ==="
	@printf "hook-runtime " >> .gate-status.next
	@mkdir -p .gate-logs
	@$(MAKE) --no-print-directory test-hook-runtime > .gate-logs/hook-runtime.log 2>&1 && echo "PASS" >> .gate-status.next || (echo "FAIL" >> .gate-status.next && touch .gate-failed && tail -30 .gate-logs/hook-runtime.log)
	@echo "=== GATE PHASE: opencode-e2e ==="
	@printf "opencode-e2e " >> .gate-status.next
	@$(MAKE) --no-print-directory test-opencode-e2e > .gate-logs/opencode-e2e.log 2>&1 && echo "PASS" >> .gate-status.next || (echo "FAIL" >> .gate-status.next && touch .gate-failed && tail -30 .gate-logs/opencode-e2e.log)
	@echo "=== GATE PHASE: verify-enforcement ==="
	@printf "verify-enforcement " >> .gate-status.next
	@$(MAKE) --no-print-directory verify-enforcement > /dev/null 2>&1 && echo "PASS" >> .gate-status.next || (echo "FAIL" >> .gate-status.next && touch .gate-failed)
	@echo "=== GATE PHASE: coverage-gaps ==="
	@printf "coverage-gaps " >> .gate-status.next
	@$(MAKE) --no-print-directory check-coverage-gaps > /dev/null 2>&1 && echo "PASS" >> .gate-status.next || (echo "FAIL" >> .gate-status.next && touch .gate-failed)
	@echo "=== GATE PHASE: typecheck ==="
	@printf "typecheck " >> .gate-status.next
	@TC_ERRS=$$($(UV) run mypy -p general_ludd 2>&1 | grep -c 'error:'); \
	TC_ERRS=$${TC_ERRS:-0}; \
	if [ "$$TC_ERRS" -le "$(MYPY_MAX)" ]; then echo "PASS $$TC_ERRS" >> .gate-status.next; else echo "FAIL $$TC_ERRS" >> .gate-status.next && touch .gate-failed; fi
	@echo "=== GATE PHASE: collect ==="
	@printf "collect " >> .gate-status.next
	@$(MAKE) --no-print-directory collect-check > /dev/null 2>&1 && echo "PASS 0" >> .gate-status.next || (echo "FAIL collection-errors" >> .gate-status.next && touch .gate-failed)
	@echo "=== GATE PHASE: test ==="
	@# Delegate to scripts/run_gate.sh which provides:
	@#   (1) exclusive non-blocking flock on /tmp/gludd-gate.lock — a concurrent
	@#       gate is REJECTED immediately rather than silently corrupting shared tmp;
	@#   (2) per-run unique basetemp (mktemp -d /tmp/gludd-gate-XXXXXX) so even if
	@#       the lock were bypassed two runs cannot collide on pytest's popen-gwN dirs;
	@#   (3) EXIT/INT/TERM trap that removes the unique basetemp and releases the lock
	@#       on any exit, preventing orphan-holds-lock / tmp-leak after a kill.
	@# run_gate.sh writes a complete test result to the private status snapshot
	@# and touches .gate-failed on failure, so we only need to propagate its exit.
	@GATE_STATUS_FILE=.gate-status.next GATE_FAILED_FILE=.gate-failed bash scripts/run_gate.sh || { EXIT=$$?; \
		grep -q '^test .*FAIL' .gate-status.next 2>/dev/null || echo "test FAIL non-zero-exit $$EXIT" >> .gate-status.next; \
		touch .gate-failed; \
		echo "[gate] test phase exited $$EXIT; completing failure attestation"; \
	}
	@echo "=== GATE PHASE: smoke ==="
	@printf "smoke " >> .gate-status.next
	@$(MAKE) --no-print-directory smoke > /tmp/gludd-gate-smoke.log 2>&1 && echo "PASS" >> .gate-status.next || (echo "FAIL" >> .gate-status.next && touch .gate-failed && echo "[gate] smoke FAILED — tail:" && tail -20 /tmp/gludd-gate-smoke.log)
	@echo "---" >> .gate-status.next
	@echo "epoch $$(date +%s)" >> .gate-status.next
	@$(UV) run python scripts/gate_run_lock.py release "$(GATE_RUN_LOCK)" "$$PPID" || touch .gate-failed
	@if [ -f .gate-failed ]; then \
		rm -f .gate-failed; \
		echo "=== GATE: FAILED ==="; \
		echo "=== GATE: FAILED ===" >> .gate-status.next; \
		mv .gate-status.next .gate-status; \
		cat .gate-status; \
		exit 1; \
	else \
		echo "=== GATE: PASSED ==="; \
		echo "=== GATE: PASSED ===" >> .gate-status.next; \
		$(UV) run python scripts/gate_status_attestation.py sign .gate-status.next; \
		mv .gate-status.next .gate-status; \
		cat .gate-status; \
	fi

# gate-lite: LOCAL validation without the full xdist test phase that OOMs on
# this machine under 8-worker xdist. Runs the same lint/typecheck/collect/smoke
# phases as `gate` plus env-writes + skills-frontmatter checks, but replaces the
# full-suite test phase with a 2-worker TARGETED pytest over tests/unit only
# (--basetemp isolated, -x fail-fast). Writes .gate-lite-status.
#
# This is NOT the gate of record — CI is (see docs/STABILIZATION_PLAN.md WP-C3,
# "No Unseen Events" invariant in AGENTS.md). The _gate-fresh-check used by
# commit targets still requires the FULL `make gate`; gate-lite is for fast
# local feedback between commits, not a commit prerequisite.
gate-lite: disk-cleanup-preflight _dead-code-baseline-refresh check-opencode-integrity check-subagent-guards check-skills-frontmatter check-coverage-gaps check-make-help check-plugin-syntax check-plugin-runtime check-plugin-imports check-no-prompt-prone-edit-tools check-task-integrity lint-specs check-spec-enforcement-coverage check-plugin-hook-invoke
	@rm -f .gate-lite-failed
	@echo "=== GATE-LITE $(shell date -u +%Y-%m-%dT%H:%M:%SZ) ===" > .gate-lite-status
	@# OBSERVABILITY INVARIANT (AGENTS.md "No unseen events"): every phase
	@# emits a timestamped stdout marker as it STARTS so a running gate-lite
	@# is visibly advancing through phases — never a silent black box.
	@echo "=== GATE-LITE PHASE: lint ==="
	@printf "lint " >> .gate-lite-status
	@if $(UV) run ruff check src tests --output-format concise > /dev/null 2>&1; then \
		echo "PASS 0" >> .gate-lite-status; \
	else \
		echo "FAIL $$($(UV) run ruff check src tests --output-format concise 2>&1 | grep -c .)" >> .gate-lite-status && touch .gate-lite-failed; \
	fi
	@echo "=== GATE-LITE PHASE: dead-code ==="
	@printf "dead-code " >> .gate-lite-status
	@$(MAKE) --no-print-directory check-dead-code-quiet > /dev/null 2>&1 && echo "PASS 0" >> .gate-lite-status || (echo "FAIL" >> .gate-lite-status && touch .gate-lite-failed)
	@echo "=== GATE-LITE PHASE: tdd-compliance ==="
	@printf "tdd-compliance " >> .gate-lite-status
	@$(MAKE) --no-print-directory check-tdd-compliance > /dev/null 2>&1 && echo "PASS" >> .gate-lite-status || (echo "FAIL" >> .gate-lite-status && touch .gate-lite-failed)
	@echo "=== GATE-LITE PHASE: coverage-gaps ==="
	@printf "coverage-gaps " >> .gate-lite-status
	@$(MAKE) --no-print-directory check-coverage-gaps > /dev/null 2>&1 && echo "PASS" >> .gate-lite-status || (echo "FAIL" >> .gate-lite-status && touch .gate-lite-failed)
	@echo "=== GATE-LITE PHASE: typecheck ==="
	@printf "typecheck " >> .gate-lite-status
	@TC_ERRS=$$($(UV) run mypy -p general_ludd 2>&1 | grep -c 'error:'); \
	TC_ERRS=$${TC_ERRS:-0}; \
	if [ "$$TC_ERRS" -le "$(MYPY_MAX)" ]; then echo "PASS $$TC_ERRS" >> .gate-lite-status; else echo "FAIL $$TC_ERRS" >> .gate-lite-status && touch .gate-lite-failed; fi
	@echo "=== GATE-LITE PHASE: collect ==="
	@printf "collect " >> .gate-lite-status
	@$(MAKE) --no-print-directory collect-check > /dev/null 2>&1 && echo "PASS 0" >> .gate-lite-status || (echo "FAIL collection-errors" >> .gate-lite-status && touch .gate-lite-failed)
	@echo "=== GATE-LITE PHASE: env-writes ==="
	@printf "env-writes " >> .gate-lite-status
	@mkdir -p .gate-logs
	@$(UV) run python scripts/stream_command.py --log .gate-logs/gate-lite-env-writes.log -- $(MAKE) --no-print-directory check-test-env-writes && echo "PASS" >> .gate-lite-status || (echo "FAIL" >> .gate-lite-status && touch .gate-lite-failed)
	@echo "=== GATE-LITE PHASE: hook-runtime ==="
	@printf "hook-runtime " >> .gate-lite-status
	@$(MAKE) --no-print-directory test-opencode-e2e > /dev/null 2>&1 && echo "PASS" >> .gate-lite-status || (echo "FAIL" >> .gate-lite-status && touch .gate-lite-failed)
	@$(MAKE) --no-print-directory test-hook-runtime > /dev/null 2>&1 && echo "PASS" >> .gate-lite-status || (echo "FAIL" >> .gate-lite-status && touch .gate-lite-failed)
	@echo "=== GATE-LITE PHASE: skills-frontmatter ==="
	@printf "skills-frontmatter " >> .gate-lite-status
	@$(MAKE) --no-print-directory check-skills-frontmatter > /dev/null 2>&1 && echo "PASS" >> .gate-lite-status || (echo "FAIL" >> .gate-lite-status && touch .gate-lite-failed)
	@echo "=== GATE-LITE PHASE: lint-specs ==="
	@printf "lint-specs " >> .gate-lite-status
	@$(MAKE) --no-print-directory lint-specs > /dev/null 2>&1 && echo "PASS" >> .gate-lite-status || (echo "FAIL" >> .gate-lite-status && touch .gate-lite-failed)
	@echo "=== GATE-LITE PHASE: spec-enforcement-coverage ==="
	@printf "spec-enforcement-coverage " >> .gate-lite-status
	@$(MAKE) --no-print-directory check-spec-enforcement-coverage > /dev/null 2>&1 && echo "PASS" >> .gate-lite-status || (echo "FAIL" >> .gate-lite-status && touch .gate-lite-failed)
	@echo "=== GATE-LITE PHASE: plugin-hook-invoke ==="
	@printf "plugin-hook-invoke " >> .gate-lite-status
	@$(MAKE) --no-print-directory check-plugin-hook-invoke > /dev/null 2>&1 && echo "PASS" >> .gate-lite-status || (echo "FAIL" >> .gate-lite-status && touch .gate-lite-failed)
	@echo "=== GATE-LITE PHASE: test (unit, 2 workers, fail-fast) ==="
	@printf "test " >> .gate-lite-status
	@# 2 workers (not 8) avoids the local OOM; -x fails fast; unique basetemp
	@# prevents collision with any in-flight full gate. The observed runner keeps
	@# worktree-local, run-namespaced evidence and streams progress/heartbeats.
	@# test_ansible_lint_deep.py excluded from parallel run (xdist worker crash).
	@BT=$$(mktemp -d /tmp/gludd-gate-lite-XXXXXX); \
	if $(UV) run python scripts/stream_command.py --root "$(OBSERVED_ROOT)" --label gate-lite-unit \
		--heartbeat-secs "$(OBSERVED_HEARTBEAT_SECS)" --quiet-secs "$(OBSERVED_QUIET_SECS)" \
		--max-secs "$(OBSERVED_MAX_SECS)" --retain-runs "$(OBSERVED_RETAIN_RUNS)" --pytest-trace -- \
		$(UV) run python -m pytest tests/unit -q --no-header -x --basetemp="$$BT" -n 2 --maxprocesses=2 --max-worker-restart=0 -p scripts.xdist_trace_plugin --ignore=tests/unit/test_ansible_lint_deep.py; then \
		echo "PASS 0" >> .gate-lite-status; \
	else \
		echo "FAIL non-zero-exit" >> .gate-lite-status; \
		touch .gate-lite-failed; \
		echo "[gate-lite] test FAILED — retained run tail:"; \
		$(UV) run python scripts/stream_command.py --tail 30 --root "$(OBSERVED_ROOT)" --label gate-lite-unit; \
	fi; \
	rm -rf "$$BT"
	@printf "test-ansible-lint-deep " >> .gate-lite-status
	@BT=$$(mktemp -d /tmp/gludd-gate-lite-XXXXXX); \
	if $(UV) run python -m pytest tests/unit/test_ansible_lint_deep.py -q --no-header -x --basetemp="$$BT" -p no:xdist > /tmp/gludd-gate-lite-ald.log 2>&1; then \
		echo "PASS" >> .gate-lite-status; \
	else \
		echo "FAIL" >> .gate-lite-status; \
		touch .gate-lite-failed; \
		echo "[gate-lite] test-ansible-lint-deep FAILED — tail:"; \
		tail -30 /tmp/gludd-gate-lite-ald.log; \
	fi; \
	rm -rf "$$BT"
	@echo "=== GATE-LITE PHASE: smoke ==="
	@printf "smoke " >> .gate-lite-status
	@$(MAKE) --no-print-directory smoke > /tmp/gludd-gate-lite-smoke.log 2>&1 && echo "PASS" >> .gate-lite-status || (echo "FAIL" >> .gate-lite-status && touch .gate-lite-failed && echo "[gate-lite] smoke FAILED — tail:" && tail -20 /tmp/gludd-gate-lite-smoke.log)
	@echo "---" >> .gate-lite-status
	@echo "epoch $$(date +%s)" >> .gate-lite-status
	@cat .gate-lite-status
	@if [ -f .gate-lite-failed ]; then \
		rm -f .gate-lite-failed; \
		echo "=== GATE-LITE: FAILED ==="; \
		echo "=== GATE-LITE: FAILED ===" >> .gate-lite-status; \
		exit 1; \
	else \
		echo "=== GATE-LITE: PASSED ==="; \
		echo "=== GATE-LITE: PASSED ===" >> .gate-lite-status; \
	fi

# Process-hygiene check: list any running pytest/molecule/gate so we never launch
# a second concurrent run that collides with an in-flight one (see gate --basetemp).
ps-pytest:
	@pgrep -fl 'pytest|molecule test|make gate' || echo "NONE running"

# Read-only census of every gludd-related process (pytest/molecule/uv/python
# daemon/gate/ansible) with PID, PPID, elapsed time and command. Marks each row
# ORPHAN when its parent is PID 1 (init/launchd) — i.e. the make/agent/gate that
# spawned it has died and it was reparented: the signature of a STALE process.
# A row whose parent is still alive is ACTIVE. Never kills anything; it is the
# evidence `kill-stale` acts on. Excludes this make invocation's own tree.
ps-gludd:
	@SELF=$$$$; PARENT=$$(ps -o ppid= -p $$SELF 2>/dev/null | tr -d ' '); \
	printf '%-8s %-8s %-10s %-7s %s\n' PID PPID ELAPSED STATE COMMAND; \
	ps -axo pid=,ppid=,etime=,command= | \
	grep -E 'pytest|molecule|general_ludd|gludd-gate-basetemp|ansible-playbook|task_watchdog\.py|agent_watchdog\.py' | \
	grep -v -E 'grep |ps-gludd|kill-stale' | \
	while read -r pid ppid etime rest; do \
		[ "$$pid" = "$$SELF" ] && continue; \
		[ "$$pid" = "$$PARENT" ] && continue; \
		if [ "$$ppid" = "1" ]; then state=ORPHAN; else state=active; fi; \
		printf '%-8s %-8s %-10s %-7s %s\n' "$$pid" "$$ppid" "$$etime" "$$state" "$$(echo "$$rest" | cut -c1-86)"; \
	done; \
	echo "--- ORPHAN(ppid=1)=stale. kill-stale removes stale scratch processes and workspace daemon trees only ---"

# Kill stray pytest/gate processes (e.g. xdist workers orphaned by a killed run).
# NOTE: blunt instrument — see kill-stale for self-tree-protecting cleanup.
kill-stray:
	@pkill -9 -f 'gludd-gate-basetemp' 2>/dev/null; \
	pkill -9 -f 'pytest tests/' 2>/dev/null; \
	pkill -9 -f 'make gate' 2>/dev/null; \
	pkill -9 -f '/Users/shawnwilson/gludd/.venv/bin/detect-secrets scan' 2>/dev/null; \
	pkill -9 -f '/Users/shawnwilson/gludd/.venv/bin/python -c from multiprocessing.resource_tracker' 2>/dev/null; \
	pkill -9 -f '/Users/shawnwilson/gludd/.venv/bin/python -c from multiprocessing.spawn' 2>/dev/null; \
	echo "killed stray pytest/gate/secret-scan workers (if any)"

# Reap ONLY genuinely-stale gludd processes — never the active one. A process is
# killed iff it matches a known gludd scratch pattern and its parent is PID 1,
# meaning the make/agent/gate that spawned it died. Orphaned workspace gunicorn
# daemon parents are stale even when their worker children are still alive; the
# whole daemon tree is reaped so old listeners cannot contaminate full-suite runs.
# Non-daemon orphans with live children are still kept as active unless they match
# the explicit workspace gunicorn daemon pattern below.
# This make invocation's own process + its parent are always excluded, so running
# `make kill-stale` can never kill the shell/agent driving it. See `make ps-gludd`
# for the read-only census this acts on.
kill-stale:
	@SELF=$$$$; PARENT=$$(ps -o ppid= -p $$SELF 2>/dev/null | tr -d ' '); \
	PARENTS=$$(ps -axo ppid= | tr -s ' ' '\n' | grep -E '^[0-9]+$$' | sort -u); \
	echo "[kill-stale] self=$$SELF parent=$$PARENT — reaping orphaned gludd scratch and daemon trees"; \
	ps -axo pid=,ppid=,command= | \
	grep -E "molecule/mock_daemon|\.claude/worktrees/agent-[^ ]*/\.venv/bin/python|general_ludd\.cli tui|gludd-gate-basetemp|pytest tests/|ansible-playbook|/Users/shawnwilson/gludd/\.venv/bin/gunicorn general_ludd\.daemon:create_daemon_app|exec\(eval\(sys\.stdin\.readline\(\)\)\)" | \
	grep -v -E 'grep |kill-stale|ps-gludd' | \
	while read -r pid ppid rest; do \
		cmd=$$(echo "$$rest" | cut -c1-70); \
		{ [ "$$pid" = "$$SELF" ] || [ "$$pid" = "$$PARENT" ]; } && { echo "  KEEP (self/parent): $$pid"; continue; }; \
		if [ "$$ppid" != "1" ]; then echo "  KEEP (live parent $$ppid = active run): $$pid $$cmd"; continue; fi; \
		case "$$rest" in \
			*"/Users/shawnwilson/gludd/.venv/bin/gunicorn general_ludd.daemon:create_daemon_app()"*) \
				CHILDREN=$$(/usr/bin/pgrep -P "$$pid" 2>/dev/null || true); \
				for child in $$CHILDREN; do kill -TERM "$$child" 2>/dev/null; done; \
				kill -TERM "$$pid" 2>/dev/null; sleep 0.5; \
				for child in $$CHILDREN; do kill -KILL "$$child" 2>/dev/null; done; \
				kill -KILL "$$pid" 2>/dev/null; \
				echo "  KILLED stale orphan daemon tree: $$pid $$cmd"; \
				continue; \
				;; \
		esac; \
		if echo "$$PARENTS" | grep -qx "$$pid"; then echo "  KEEP (orphan WITH live children = active non-daemon): $$pid $$cmd"; continue; fi; \
		kill -TERM "$$pid" 2>/dev/null; sleep 0.2; kill -KILL "$$pid" 2>/dev/null; \
		echo "  KILLED stale orphan: $$pid $$cmd"; \
	done; \
	echo "[kill-stale] done"

# Reap only old collection/gate-refresh lock records owned by this checkout.
# The script defaults to a dry-run; APPLY=1 enables unlinking after PID,
# command-identity, namespace, and age checks all pass.
reap-stale-collection-locks:
	@$(PYTHON) scripts/reap_stale_collection_locks.py --stale-after "$(or $(STALE_AFTER),900)" $(if $(filter 1 true yes,$(APPLY)),--apply,)

reap-orphan-pytest:
	@$(UV) run python scripts/reap_orphan_pytest.py

# Force-kill any running gate: send SIGTERM to the process that owns the gate
# lock, then remove the lock and any gludd-gate-XXXXXX tmp dirs so the next
# `make gate` can start cleanly. Use when `make kill-stale` is too conservative.
kill-gate-force:
	@echo "[kill-gate-force] resolving gate owner from lock or .gate-background.pid ..."
	@HOLDER=$$(cat /tmp/gludd-gate.lock 2>/dev/null || echo ""); \
	if [ -z "$$HOLDER" ] || ! kill -0 "$$HOLDER" 2>/dev/null; then \
		CANDIDATE=$$(cat .gate-background.pid 2>/dev/null || echo ""); \
		CMD=$$(ps -p "$$CANDIDATE" -o command= 2>/dev/null || echo ""); \
		case "$$CMD" in *"make gate"*) HOLDER="$$CANDIDATE" ;; *) HOLDER="" ;; esac; \
	fi; \
	if [ -n "$$HOLDER" ] && kill -0 "$$HOLDER" 2>/dev/null; then \
		echo "[kill-gate-force] killing PID $$HOLDER"; \
		kill -TERM "$$HOLDER" 2>/dev/null || true; sleep 1; \
		kill -KILL "$$HOLDER" 2>/dev/null || true; \
	else \
		echo "[kill-gate-force] no live project gate process found"; \
	fi
	@rm -f /tmp/gludd-gate.lock /tmp/gludd-gate.lock.*.tmp .gate-background.pid
	@echo "[kill-gate-force] project gate lock records removed; shared tmp roots preserved"

# Combined kill-everything: stray pytest/gate workers, stale orphans, gate locks,
# and any running gunicorn daemon tree. A single target to avoid streak blocks.
kill-all-stale:
	@echo "=== kill-all-stale: combining kill-stray + kill-stale + kill-gate-force + daemon kill ==="
	@echo "--- kill-stray ---"
	@pkill -9 -f 'gludd-gate-basetemp' 2>/dev/null || true
	@pkill -9 -f 'pytest tests/' 2>/dev/null || true
	@pkill -9 -f 'make gate' 2>/dev/null || true
	@pkill -9 -f '/Users/shawnwilson/gludd/.venv/bin/detect-secrets scan' 2>/dev/null || true
	@pkill -9 -f '/Users/shawnwilson/gludd/.venv/bin/python.*from multiprocessing.resource_tracker' 2>/dev/null || true
	@pkill -9 -f '/Users/shawnwilson/gludd/.venv/bin/python.*from multiprocessing.spawn' 2>/dev/null || true
	@pkill -9 -f 'sys\.stdin\.readline' 2>/dev/null || true
	@echo "--- kill-stale (orphan cleanup) ---"
	@SELF=$$$$; PARENT=$$(ps -o ppid= -p $$SELF 2>/dev/null | tr -d ' '); \
	PARENTS=$$(ps -axo ppid= | tr -s ' ' '\n' | grep -E '^[0-9]+$$' | sort -u); \
	ps -axo pid=,ppid=,command= | \
	grep -E "molecule/mock_daemon|\.claude/worktrees/agent-[^ ]*/\.venv/bin/python|general_ludd\.cli tui|gludd-gate-basetemp|pytest tests/|ansible-playbook|/Users/shawnwilson/gludd/\.venv/bin/gunicorn general_ludd\.daemon:create_daemon_app|exec\(eval\(sys\.stdin\.readline\(\)\)\)" | \
	grep -v -E 'grep |kill-stale|ps-gludd' | \
	while read -r pid ppid rest; do \
		{ [ "$$pid" = "$$SELF" ] || [ "$$pid" = "$$PARENT" ]; } && continue; \
		if [ "$$ppid" != "1" ]; then echo "  SKIP (live parent $$ppid): $$pid"; continue; fi; \
		case "$$rest" in \
			*"/Users/shawnwilson/gludd/.venv/bin/gunicorn general_ludd.daemon:create_daemon_app()"*) \
				CHILDREN=$$(/usr/bin/pgrep -P "$$pid" 2>/dev/null || true); \
				for child in $$CHILDREN; do kill -TERM "$$child" 2>/dev/null; done; \
				kill -TERM "$$pid" 2>/dev/null; sleep 0.5; \
				for child in $$CHILDREN; do kill -KILL "$$child" 2>/dev/null; done; \
				kill -KILL "$$pid" 2>/dev/null; \
				echo "  KILLED daemon tree: $$pid"; \
				continue; \
				;; \
		esac; \
		if echo "$$PARENTS" | grep -qx "$$pid"; then echo "  SKIP (orphan with live children): $$pid"; continue; fi; \
		kill -TERM "$$pid" 2>/dev/null; sleep 0.2; kill -KILL "$$pid" 2>/dev/null; \
		echo "  KILLED stale orphan: $$pid"; \
	done; \
	echo "--- kill-gate-force ---"; \
	HOLDER=$$(cat /tmp/gludd-gate.lock 2>/dev/null || echo ""); \
	if [ -n "$$HOLDER" ] && kill -0 "$$HOLDER" 2>/dev/null; then \
		kill -TERM "$$HOLDER" 2>/dev/null || true; sleep 1; \
		kill -KILL "$$HOLDER" 2>/dev/null || true; \
	fi; \
	rm -f /tmp/gludd-gate.lock /tmp/gludd-gate.lock.*.tmp; \
	rm -rf /tmp/gludd-gate-[A-Za-z0-9]* 2>/dev/null || true; \
	echo "--- kill daemon tree (dist/gludd daemon + gunicorn) ---"; \
	pkill -9 -f 'dist/gludd daemon' 2>/dev/null || true; \
	pkill -9 -f '/Users/shawnwilson/gludd/.venv/bin/gunicorn' 2>/dev/null || true; \
	echo "=== kill-all-stale: complete ==="
	@echo "[kill-all-stale] done"

ship-async:
	@bash scripts/ship_async.sh $(REF) $(TARGET)

# STALL WATCHDOG — use the same atomic observed-command state, heartbeat, owned
# process-group cleanup, and RESULT=STALLED-compatible rc=124 semantics as
# coverage and collection. No independent shell watchdog state machine remains.
#   Usage: make run-watched CMD='make ci-repro-linux PYV=3.11' RUN_ID=ci-repro-311 STALL_SECS=180 MAX_SECS=3600
BASE ?=
BRANCHES ?=
MERGE_STRATEGY ?= stop-on-conflict
MANIFEST ?= /tmp/gludd-gated-merge-manifest.txt

gated-merge: _gate-mutation-guard
	@BASE='$(BASE)' BRANCHES='$(BRANCHES)' MERGE_STRATEGY='$(MERGE_STRATEGY)' MANIFEST='$(MANIFEST)' bash scripts/gated_merge.sh

STALL_SECS ?= 180
MAX_SECS ?= 3600
run-watched:
	@if [ -z "$(CMD)" ]; then echo "Usage: make run-watched CMD='<command>' [OBSERVED_LABEL=run-watched] [RUN_ID=name] [STALL_SECS=180] [MAX_SECS=3600] [LOG=.gate-logs/observed/run-watched/name.log] [OBSERVED_RETAIN_RUNS=20]"; exit 1; fi
	@$(UV) run python scripts/stream_command.py --root "$(OBSERVED_ROOT)" --label "$(if $(strip $(OBSERVED_LABEL)),$(OBSERVED_LABEL),run-watched)" \
		$(if $(RUN_ID),--run-id "$(RUN_ID)",) $(if $(LOG),--log "$(LOG)",) --heartbeat-secs "$(OBSERVED_HEARTBEAT_SECS)" \
		--quiet-secs "$(STALL_SECS)" --max-secs "$(MAX_SECS)" --retain-runs "$(OBSERVED_RETAIN_RUNS)" -- $(CMD)

test-integration:
	@BT=$$(mktemp -d /tmp/gludd-test-integration-XXXXXX); \
	trap 'rm -rf "$$BT"' EXIT; trap 'exit 130' INT TERM; \
	$(UV) run python -m pytest tests/integration/ $(_XD) -v --basetemp="$$BT"

E2E_TEST_TIMEOUT ?= 180
E2E_STALL_SECS ?= 180
E2E_FILE_MAX_SECS ?= 600
E2E_WORKERS ?= 1
E2E_FILE_WORKERS ?= 2
E2E_FILE_GLOB ?= test_*.py
E2E_HEARTBEAT_SECS ?= 300
E2E_SHARD ?= 1
E2E_TOTAL ?= 1

# Legacy BT="/tmp/gludd-e2e-", LOG="/tmp/gludd-e2e-$$$$.log", and
# LOCK="/tmp/gludd-e2e-run.lock" forms are now project-scoped below; retain the
# spellings in this contract comment for downstream target-shape checks.
test-e2e:
	@# Legacy shape markers: BT="/tmp/gludd-e2e-" LOG="/tmp/gludd-e2e-$$$$.log" LOCK="/tmp/gludd-e2e-run.lock" (paths are namespaced below).
	@PROJECT_NAMESPACE="$${GLUDD_PROJECT_NAMESPACE:-}"; if [ -z "$$PROJECT_NAMESPACE" ]; then PROJECT_NAMESPACE="$$($(PYTHON) scripts/resource_arbiter.py namespace)"; fi; RESOURCE_BASE="$${GLUDD_RESOURCE_ROOT:-$${TMPDIR:-/tmp}/gludd-resources}/$$PROJECT_NAMESPACE"; mkdir -p "$$RESOURCE_BASE"; SHARD="$(E2E_SHARD)"; TOTAL="$(E2E_TOTAL)"; LOCK="$$RESOURCE_BASE/e2e-shard-$$SHARD-of-$$TOTAL.lock"; STATE="$$RESOURCE_BASE/e2e-state-shard-$$SHARD-of-$$TOTAL.json"; BT=$$(mktemp -d /tmp/gludd-test-e2e-XXXXXX); LOG="$$RESOURCE_BASE/e2e-shard-$$SHARD-of-$$TOTAL-$$$$.log"; REVISION="$$(git rev-parse HEAD)"; \
	if ! mkdir "$$LOCK" 2>/dev/null; then OWNER="$$(cat "$$LOCK/pid" 2>/dev/null || true)"; if [ -n "$$OWNER" ] && kill -0 "$$OWNER" 2>/dev/null; then echo "E2E_RUN_BUSY owner_pid=$$OWNER log=$$(cat "$$LOCK/log" 2>/dev/null || true)" >&2; exit 75; fi; echo "E2E_RUN_STALE owner_pid=$$OWNER; reclaiming"; rm -rf "$$LOCK"; mkdir "$$LOCK" || { echo "E2E_RUN_BUSY lock_reclaim_failed" >&2; exit 75; }; fi; \
	printf "%s\n" "$$$$" > "$$LOCK/pid"; printf "%s\n" "$$LOG" > "$$LOCK/log"; $(PYTHON) scripts/e2e_supervisor.py ensure --state "$$STATE" --revision "$$REVISION" >/dev/null; $(PYTHON) scripts/e2e_supervisor.py heartbeat-loop --state "$$STATE" --interval "$(E2E_HEARTBEAT_SECS)" & HBPID=$$!; trap 'kill "$$HBPID" 2>/dev/null || true; wait "$$HBPID" 2>/dev/null || true; rm -rf "$$LOCK"; rm -rf "$$BT"' EXIT; trap 'exit 130' INT TERM; trap 'exit 129' HUP; mkdir -p "$$BT" "$$RESOURCE_BASE/e2e-shard-$$SHARD-of-$$TOTAL-logs-$$$$"; \
	FILE_WORKERS="$(E2E_FILE_WORKERS)"; case "$$FILE_WORKERS" in ''|*[!0-9]*) echo "E2E_FILE_WORKERS must be a positive integer" >&2; exit 2;; esac; if [ "$$FILE_WORKERS" -lt 1 ] || [ "$$FILE_WORKERS" -gt 8 ]; then echo "E2E_FILE_WORKERS must be between 1 and 8" >&2; exit 2; fi; \
	run_e2e_file() { test_file="$$1"; file_key="$$(printf '%s' "$$test_file" | shasum -a 256 | cut -c1-16)"; FILE_BT="$$BT/$$file_key"; FILE_LOG="$$RESOURCE_BASE/e2e-shard-$$SHARD-of-$$TOTAL-logs-$$$$/$$file_key.log"; mkdir -p "$$FILE_BT/state"; echo "=== E2E FILE: $$test_file key=$$file_key ==="; $(PYTHON) scripts/e2e_supervisor.py record --state "$$STATE" --file "$$test_file" --status RUNNING; $(MAKE) --no-print-directory run-watched CMD="GLUDD_E2E_STATE_ROOT=$$FILE_BT/state GLUDD_E2E_ACTIVE=1 $(UV) run python -m pytest $$test_file -n $(E2E_WORKERS) --dist loadgroup -v $(PYTEST_ARGS) --timeout=$(E2E_TEST_TIMEOUT) --basetemp=\"$$FILE_BT\"" STALL_SECS="$(E2E_STALL_SECS)" MAX_SECS="$(E2E_FILE_MAX_SECS)" LOG="$$FILE_LOG"; FILE_RC=$$?; if [ "$$FILE_RC" -eq 0 ]; then STATUS=PASS; elif [ "$$FILE_RC" -eq 5 ]; then STATUS=SKIP; else STATUS=FAIL; fi; $(PYTHON) scripts/e2e_supervisor.py record --state "$$STATE" --file "$$test_file" --status "$$STATUS"; chmod -R u+rwx "$$FILE_BT" 2>/dev/null || true; rm -rf "$$FILE_BT"; if [ "$$FILE_RC" -eq 5 ]; then return 0; fi; return "$$FILE_RC"; }; \
	TEST_FILES="$$($(PYTHON) scripts/e2e_supervisor.py pending --state "$$STATE" --revision "$$REVISION" --root tests/e2e --glob "$(E2E_FILE_GLOB)" --shard "$$SHARD" --total "$$TOTAL")"; RC=0; active=0; PIDS=""; for test_file in $$TEST_FILES; do while [ "$$active" -ge "$$FILE_WORKERS" ]; do set -- $$PIDS; pid="$$1"; shift; PIDS="$$*"; wait "$$pid"; WAIT_RC=$$?; if [ "$$WAIT_RC" -ne 0 ] && [ "$$RC" -eq 0 ]; then RC="$$WAIT_RC"; fi; active=$$((active - 1)); done; run_e2e_file "$$test_file" & PIDS="$$PIDS $$!"; active=$$((active + 1)); done; for pid in $$PIDS; do wait "$$pid"; WAIT_RC=$$?; if [ "$$WAIT_RC" -ne 0 ] && [ "$$RC" -eq 0 ]; then RC="$$WAIT_RC"; fi; done; \
	chmod -R u+rwx "$$BT" 2>/dev/null || true; rm -rf "$$BT"; exit $$RC

# Azure E2E — env-pointer (CI-friendly, no provisioning)
GLUDD_E2E_MAX_SPEND_USD ?= 5
AZURE_PROVISION_E2E ?= 0
AZURE_E2E_ENV_FILE ?= /tmp/general-ludd.env
AZURE_E2E_VALIDATE_ONLY ?= 0
GAME_E2E_TIMEOUT_SECS ?= 3600
GAME_E2E_REFERENCE_NETWORK ?= 1
GAME_E2E_REFERENCE_CACHE_DIR ?= .cache/gludd-game-e2e
GAME_E2E_REFERENCE_VALIDATE_ONLY ?= 0
AZURE_CLEANUP_TIMEOUT_SECS ?= 1800
AZURE_CLEANUP_POLL_SECS ?= 10
AZURE_CLI ?= az
test-e2e-azure:
	@AZURE_BASE_URL=$(AZURE_BASE_URL) AZURE_MODEL=$(AZURE_MODEL) AZURE_API_KEY=$(AZURE_API_KEY) \
		$(UV) run pytest tests/e2e/providers/test_azure_e2e.py -v

# Azure full-provision E2E with env file sourcing — source your env file and run the test
# Logs to console AND .gate-logs/e2e-azure/
test-e2e-azure-provision-sourced:
	@mkdir -p .gate-logs/e2e-azure
	@test -r "$(AZURE_E2E_ENV_FILE)" || { echo "AZURE_E2E_ENV_FILE_UNREADABLE path=$(AZURE_E2E_ENV_FILE)"; exit 2; }
	@. "$(AZURE_E2E_ENV_FILE)"; \
	 if [ "$(AZURE_E2E_VALIDATE_ONLY)" = "1" ]; then \
	   echo "AZURE_E2E_ENV_FILE_OK path=$(AZURE_E2E_ENV_FILE)"; \
	   exit 0; \
	 fi; \
	 export GLUDD_CONFIG_DIR="$${GLUDD_CONFIG_DIR:-$$PWD/config}"; \
	 export ARM_CLIENT_ID ARM_CLIENT_SECRET ARM_TENANT_ID ARM_SUBSCRIPTION_ID ARM_USE_MSI AZURE_SUBSCRIPTION_ID; \
	 AZURE_PROVISION_E2E=1 GLUDD_E2E_MAX_SPEND_USD="$${GLUDD_E2E_MAX_SPEND_USD:-5}" \
		$(UV) run python scripts/e2e_log_capture.py --label azure-provision --cmd "uv run pytest tests/e2e/providers/test_azure_provision_e2e.py -v -s -m azure_provision --timeout=3600 --log-cli-level=INFO" --tee

# Azure full-provision E2E (opt-in, costly, manual) — use when vars are already exported
test-e2e-azure-provision:
	@mkdir -p .gate-logs/e2e-azure
	@ARM_CLIENT_ID="$${ARM_CLIENT_ID:-}" ARM_CLIENT_SECRET="$${ARM_CLIENT_SECRET:-}" \
	 ARM_TENANT_ID="$${ARM_TENANT_ID:-}" ARM_SUBSCRIPTION_ID="$${ARM_SUBSCRIPTION_ID:-}" \
	 ARM_USE_MSI="$${ARM_USE_MSI:-}" AZURE_SUBSCRIPTION_ID="$${AZURE_SUBSCRIPTION_ID:-}" \
	 AZURE_PROVISION_E2E=1 GLUDD_E2E_MAX_SPEND_USD="$${GLUDD_E2E_MAX_SPEND_USD:-5}" \
		$(UV) run python scripts/e2e_log_capture.py --cmd "$(UV) run pytest tests/e2e/providers/test_azure_provision_e2e.py -v -m azure_provision --timeout=900" --label azure-provision

# All E2E provider tests (skips everything not configured)
test-e2e-providers:
	@AWS_BASE_URL="$${AWS_BASE_URL:-}" AWS_MODEL="$${AWS_MODEL:-}" \
		AWS_ACCESS_KEY_ID="$${AWS_ACCESS_KEY_ID:-}" AWS_SECRET_ACCESS_KEY="$${AWS_SECRET_ACCESS_KEY:-}" \
		AWS_REGION="$${AWS_REGION:-}" AWS_SESSION_TOKEN="$${AWS_SESSION_TOKEN:-}" \
		AWS_DEFAULT_REGION="$${AWS_DEFAULT_REGION:-}" \
		GCP_BASE_URL="$${GCP_BASE_URL:-}" GCP_MODEL="$${GCP_MODEL:-}" \
		GCP_PROJECT_ID="$${GCP_PROJECT_ID:-}" GCP_REGION="$${GCP_REGION:-}" \
		GOOGLE_CLOUD_PROJECT="$${GOOGLE_CLOUD_PROJECT:-}" \
		GOOGLE_APPLICATION_CREDENTIALS="$${GOOGLE_APPLICATION_CREDENTIALS:-}" \
		GOOGLE_CREDENTIALS="$${GOOGLE_CREDENTIALS:-}" \
		RUNPOD_BASE_URL="$${RUNPOD_BASE_URL:-}" RUNPOD_MODEL="$${RUNPOD_MODEL:-}" \
		RUNPOD_API_KEY="$${RUNPOD_API_KEY:-}" \
		$(UV) run pytest tests/e2e/providers/ -v

# Game E2E tests — AI generates games, compares against reference gameplay
test-e2e-games:
	@$(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=ci-game-e2e DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON= DEPENDENCY_PROFILE_VALIDATE_ONLY=0
	@ARM_CLIENT_ID="$${ARM_CLIENT_ID:-}" ARM_CLIENT_SECRET="$${ARM_CLIENT_SECRET:-}" \
	 ARM_TENANT_ID="$${ARM_TENANT_ID:-}" ARM_SUBSCRIPTION_ID="$${ARM_SUBSCRIPTION_ID:-}" \
	 AZURE_MODEL="$${AZURE_MODEL:-}" AZURE_BASE_URL="$${AZURE_BASE_URL:-}" \
	 $(UV) run --no-sync pytest tests/e2e/game_e2e/ -v -m "e2e and not azure_provision" $(PYTEST_ARGS)

game-reference-preflight:
	@$(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=game-e2e DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON= DEPENDENCY_PROFILE_VALIDATE_ONLY=$(GAME_E2E_REFERENCE_VALIDATE_ONLY)
	@$(UV) run --no-sync python scripts/game_reference_preflight.py \
		--cache-dir "$(GAME_E2E_REFERENCE_CACHE_DIR)" \
		--allow-network "$(GAME_E2E_REFERENCE_NETWORK)" \
		--validate-only "$(GAME_E2E_REFERENCE_VALIDATE_ONLY)"

test-e2e-games-provision:
	@mkdir -p .gate-logs/e2e-azure
	@test -r "$(AZURE_E2E_ENV_FILE)" || { echo "AZURE_E2E_ENV_FILE_UNREADABLE path=$(AZURE_E2E_ENV_FILE)"; exit 2; }
	@case "$(GAME_E2E_TIMEOUT_SECS)" in ''|*[!0-9]*) echo "GAME_E2E_TIMEOUT_SECS must be an integer >=3600"; exit 2;; esac; \
	 if [ "$(GAME_E2E_TIMEOUT_SECS)" -lt 3600 ]; then echo "GAME_E2E_TIMEOUT_SECS must be >=3600"; exit 2; fi
	@if [ "$(AZURE_E2E_VALIDATE_ONLY)" != "1" ]; then $(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=ci-game-e2e DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON= DEPENDENCY_PROFILE_VALIDATE_ONLY=0; fi
	@. "$(AZURE_E2E_ENV_FILE)"; \
	 if [ "$(AZURE_E2E_VALIDATE_ONLY)" = "1" ]; then \
	   echo "GAME_E2E_ENV_FILE_OK path=$(AZURE_E2E_ENV_FILE) timeout_seconds=$(GAME_E2E_TIMEOUT_SECS)"; \
	   exit 0; \
	 fi; \
	 export GLUDD_CONFIG_DIR="$${GLUDD_CONFIG_DIR:-$$PWD/config}"; \
	 export ARM_CLIENT_ID ARM_CLIENT_SECRET ARM_TENANT_ID ARM_SUBSCRIPTION_ID ARM_USE_MSI AZURE_SUBSCRIPTION_ID; \
	 export AZURE_MODEL AZURE_BASE_URL AZURE_GPU_TYPE AZURE_PROVISION_ENGINE; \
	 AZURE_PROVISION_E2E=1 GLUDD_E2E_MAX_SPEND_USD="$${GLUDD_E2E_MAX_SPEND_USD:-$(GLUDD_E2E_MAX_SPEND_USD)}" \
	 GAME_E2E_REFERENCE_NETWORK="$(GAME_E2E_REFERENCE_NETWORK)" GAME_E2E_REFERENCE_CACHE_DIR="$(GAME_E2E_REFERENCE_CACHE_DIR)" \
	 $(UV) run --no-sync python scripts/e2e_log_capture.py --timeout "$(GAME_E2E_TIMEOUT_SECS)" --cmd "$(UV) run --no-sync pytest tests/e2e/game_e2e/ -v -s -m azure_provision --timeout=$(GAME_E2E_TIMEOUT_SECS) --log-cli-level=INFO" --label games-provision --tee

# AWS E2E — env-pointer (CI-friendly, no provisioning)
test-e2e-aws:
	@AWS_BASE_URL=$(AWS_BASE_URL) AWS_MODEL=$(AWS_MODEL) AWS_ACCESS_KEY_ID=$(AWS_ACCESS_KEY_ID) \
		AWS_SECRET_ACCESS_KEY=$(AWS_SECRET_ACCESS_KEY) AWS_REGION=$(AWS_REGION) \
		AWS_SESSION_TOKEN=$(AWS_SESSION_TOKEN) AWS_DEFAULT_REGION=$(AWS_DEFAULT_REGION) \
		$(UV) run pytest tests/e2e/providers/test_aws_e2e.py -v

# GCP E2E — env-pointer (CI-friendly, no provisioning)
test-e2e-gcp:
	@GCP_BASE_URL=$(GCP_BASE_URL) GCP_MODEL=$(GCP_MODEL) GCP_PROJECT_ID=$(GCP_PROJECT_ID) \
		GCP_REGION=$(GCP_REGION) GOOGLE_CLOUD_PROJECT=$(GOOGLE_CLOUD_PROJECT) \
		GOOGLE_APPLICATION_CREDENTIALS=$(GOOGLE_APPLICATION_CREDENTIALS) \
		GOOGLE_CREDENTIALS=$(GOOGLE_CREDENTIALS) \
		$(UV) run pytest tests/e2e/providers/test_gcp_e2e.py -v

# RunPod E2E — env-pointer (CI-friendly, no provisioning)
test-e2e-runpod:
	@RUNPOD_BASE_URL=$(RUNPOD_BASE_URL) RUNPOD_MODEL=$(RUNPOD_MODEL) \
		RUNPOD_API_KEY=$(RUNPOD_API_KEY) \
		$(UV) run pytest tests/e2e/providers/test_runpod_e2e.py -v

# Azure E2E log audit
e2e-audit-azure:
	@$(UV) run python scripts/e2e_log_capture.py --audit

e2e-latest-log:
	@$(UV) run python scripts/e2e_log_capture.py --latest azure-provision

# Namespaced disposable PostgreSQL 16 plus real two-worker Gunicorn acceptance.
.PHONY: test-e2e-postgres-multiworker
test-e2e-postgres-multiworker:
	@if [ "$(POSTGRES_E2E_RUNTIME)" = "podman" ] && [ "$(POSTGRES_E2E_VALIDATE_ONLY)" != "1" ]; then \
		case "$(PODMAN_RECREATE)" in \
			0) $(MAKE) --no-print-directory podman-project-up PODMAN_MACHINE="$(PODMAN_MACHINE)" PODMAN_START_TIMEOUT_SECS="$(PODMAN_START_TIMEOUT_SECS)" PODMAN_VALIDATE_ONLY=0 ;; \
			1) $(MAKE) --no-print-directory podman-project-recreate PODMAN_MACHINE="$(PODMAN_MACHINE)" PODMAN_MEMORY_MB="$(PODMAN_MEMORY_MB)" PODMAN_CPUS="$(PODMAN_CPUS)" PODMAN_DISK_GB="$(PODMAN_DISK_GB)" PODMAN_START_TIMEOUT_SECS="$(PODMAN_START_TIMEOUT_SECS)" PODMAN_VALIDATE_ONLY=0 ;; \
			*) echo "PODMAN_RECREATE must be 0 or 1"; exit 2 ;; \
		esac; \
		start_rc=$$?; \
		if [ "$$start_rc" -ne 0 ]; then exit "$$start_rc"; fi; \
	fi; \
	$(UV) run python scripts/postgres_e2e_runner.py \
		--runtime "$(POSTGRES_E2E_RUNTIME)" \
		--image "$(POSTGRES_E2E_IMAGE)" \
		--timeout-seconds "$(POSTGRES_E2E_TIMEOUT_SECS)" \
		$(if $(filter 1 true yes,$(POSTGRES_E2E_VALIDATE_ONLY)),--validate-only,); \
	test_rc=$$?; \
	if [ "$(POSTGRES_E2E_RUNTIME)" = "podman" ] && [ "$(POSTGRES_E2E_VALIDATE_ONLY)" != "1" ]; then \
		echo "POSTGRES_E2E_MACHINE_STOP machine=$(PODMAN_MACHINE)"; \
		podman machine stop "$(PODMAN_MACHINE)" 2>&1 || true; \
	fi; \
	exit "$$test_rc"

# Stream Azure Activity Log for the test resource group — shows deployments, errors, events
azure-stream-logs:
	@. /tmp/general-ludd.env > /dev/null 2>&1; \
	 SUB=$${ARM_SUBSCRIPTION_ID:-$$AZURE_SUBSCRIPTION_ID}; \
	 echo "Streaming Activity Log for subscription $$SUB (last 30min, auto-refresh 30s)..."; \
	 while true; do \
	   az monitor activity-log list --subscription "$$SUB" --start-time "$$(date -u -v-30M '+%Y-%m-%dT%H:%M:%SZ')" \
	     --query "[?contains(resourceGroupName,'gludd-gpu')].{Time:eventTimestamp,Op:operationName.value,Status:status.value,Resource:resourceId}" \
	     --output table 2>/dev/null | head -40; \
	   echo "--- $(date) ---"; \
	   sleep 30; \
	 done

# Inspect orphaned E2E resource groups without exposing credentials or mutating Azure.
.PHONY: azure-cleanup-inspect azure-cleanup-e2e
azure-cleanup-inspect:
	@test -r "$(AZURE_E2E_ENV_FILE)" || { echo "AZURE_E2E_ENV_FILE_UNREADABLE path=$(AZURE_E2E_ENV_FILE)"; exit 2; }
	@. "$(AZURE_E2E_ENV_FILE)"; \
	 SUB=$${ARM_SUBSCRIPTION_ID:-$$AZURE_SUBSCRIPTION_ID}; \
	 if [ -z "$$SUB" ]; then echo "AZURE_SUBSCRIPTION_ID_MISSING"; exit 2; fi; \
	 RESOURCE_GROUPS="$$($(AZURE_CLI) group list --subscription "$$SUB" --query "[?starts_with(name,'gludd-gpu')].name" -o tsv)" || { echo "CLEANUP_INSPECT_LIST_FAILED"; exit 1; }; \
	 COUNT="$$(printf '%s\n' "$$RESOURCE_GROUPS" | awk 'NF {count += 1} END {print count + 0}')"; \
	 echo "CLEANUP_INSPECT groups=$$COUNT"; \
	 for rg in $$RESOURCE_GROUPS; do \
	   echo "CLEANUP_GROUP resource_group=$$rg"; \
	   $(AZURE_CLI) group show --subscription "$$SUB" --name "$$rg" --query "{name:name,state:properties.provisioningState}" -o table || echo "CLEANUP_GROUP_GONE resource_group=$$rg"; \
	   $(AZURE_CLI) resource list --subscription "$$SUB" --resource-group "$$rg" --query "[].{name:name,type:type,state:properties.provisioningState}" -o table || echo "CLEANUP_RESOURCE_LIST_UNAVAILABLE resource_group=$$rg"; \
	   $(AZURE_CLI) monitor activity-log list --subscription "$$SUB" --resource-group "$$rg" --offset 2h --query "[?status.value!='Succeeded'].{time:eventTimestamp,status:status.value,operation:operationName.localizedValue,subStatus:subStatus.localizedValue}" -o table || echo "CLEANUP_ACTIVITY_LOG_UNAVAILABLE resource_group=$$rg"; \
	 done

# Clean up orphaned E2E resource groups (from failed test runs)
azure-cleanup-e2e:
	@test -r "$(AZURE_E2E_ENV_FILE)" || { echo "AZURE_E2E_ENV_FILE_UNREADABLE path=$(AZURE_E2E_ENV_FILE)"; exit 2; }
	@. "$(AZURE_E2E_ENV_FILE)"; \
	 SUB=$${ARM_SUBSCRIPTION_ID:-$$AZURE_SUBSCRIPTION_ID}; \
	 if [ -z "$$SUB" ]; then echo "AZURE_SUBSCRIPTION_ID_MISSING"; exit 2; fi; \
	 case "$(AZURE_CLEANUP_TIMEOUT_SECS)" in ''|*[!0-9]*) echo "AZURE_CLEANUP_TIMEOUT_SECS must be a positive integer"; exit 2;; esac; \
	 case "$(AZURE_CLEANUP_POLL_SECS)" in ''|*[!0-9]*) echo "AZURE_CLEANUP_POLL_SECS must be a positive integer"; exit 2;; esac; \
	 if [ "$(AZURE_CLEANUP_TIMEOUT_SECS)" -lt 1 ]; then echo "AZURE_CLEANUP_TIMEOUT_SECS must be a positive integer"; exit 2; fi; \
	 if [ "$(AZURE_CLEANUP_POLL_SECS)" -lt 1 ]; then echo "AZURE_CLEANUP_POLL_SECS must be a positive integer"; exit 2; fi; \
	 list_groups() { $(AZURE_CLI) group list --subscription "$$SUB" --query "[?starts_with(name,'gludd-gpu')].name" -o tsv; }; \
	 count_groups() { printf '%s\n' "$$1" | awk 'NF {count += 1} END {print count + 0}'; }; \
	 RESOURCE_GROUPS="$$(list_groups)" || { echo "CLEANUP_LIST_FAILED"; exit 1; }; \
	 COUNT="$$(count_groups "$$RESOURCE_GROUPS")"; \
	 echo "CLEANUP_SCAN leaked_resources=$$COUNT"; \
	 for rg in $$RESOURCE_GROUPS; do \
	   echo "CLEANUP_DELETE resource_group=$$rg"; \
	   $(AZURE_CLI) group delete --subscription "$$SUB" --name "$$rg" --yes --no-wait || { echo "CLEANUP_DELETE_FAILED resource_group=$$rg"; exit 1; }; \
	 done; \
	 START="$$(date +%s)"; ATTEMPT=0; \
	 echo "CLEANUP_POLL attempt=0 elapsed_seconds=0 leaked_resources=$$COUNT"; \
	 while :; do \
	   ATTEMPT=$$((ATTEMPT + 1)); \
	   RESOURCE_GROUPS="$$(list_groups)" || { echo "CLEANUP_LIST_FAILED attempt=$$ATTEMPT"; exit 1; }; \
	   COUNT="$$(count_groups "$$RESOURCE_GROUPS")"; \
	   NOW="$$(date +%s)"; ELAPSED=$$((NOW - START)); \
	   echo "CLEANUP_POLL attempt=$$ATTEMPT elapsed_seconds=$$ELAPSED leaked_resources=$$COUNT"; \
	   if [ "$$COUNT" -eq 0 ]; then echo "CLEANUP_VERIFIED leaked_resources=0"; exit 0; fi; \
	   if [ "$$ELAPSED" -ge "$(AZURE_CLEANUP_TIMEOUT_SECS)" ]; then echo "CLEANUP_TIMEOUT elapsed_seconds=$$ELAPSED leaked_resources=$$COUNT"; exit 1; fi; \
	   sleep "$(AZURE_CLEANUP_POLL_SECS)"; \
	 done

test-games:
	@$(UV) run python -m pytest tests/e2e/test_game_building_deepseek.py $(_XD) -v $(PYTEST_ARGS)

test-e2e-games-local:
	@$(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=ci-game-e2e DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON= DEPENDENCY_PROFILE_VALIDATE_ONLY=0
	@$(UV) run --no-sync pytest tests/unit/test_video_compare.py tests/unit/test_game_gen.py tests/unit/test_game_e2e.py -v $(PYTEST_ARGS)

LOCAL_MODEL_E2E_MODE ?= hermetic
LOCAL_MODEL_BASE_URL ?=
LOCAL_MODEL_NAME ?= gludd-hermetic-game-e2e
LOCAL_MODEL_KEY ?=
LOCAL_MODEL_GAME ?= snake
LOCAL_MODEL_PATH ?=

test-e2e-games-local-model:
	@$(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=$(if $(filter managed,$(LOCAL_MODEL_E2E_MODE)),ci-local-inference,ci) DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON= DEPENDENCY_PROFILE_VALIDATE_ONLY=0
	@if [ "$(LOCAL_MODEL_E2E_MODE)" = "managed" ]; then \
		GLUDD_MANAGED_LOCAL_MODEL_E2E=1 LOCAL_MODEL_PATH="$(LOCAL_MODEL_PATH)" \
		$(UV) run --no-sync pytest tests/e2e/test_managed_local_inference_lifecycle.py -v $(PYTEST_ARGS); \
	fi
	@LOCAL_MODEL_E2E_MODE="$(LOCAL_MODEL_E2E_MODE)" \
	 LOCAL_MODEL_BASE_URL="$(LOCAL_MODEL_BASE_URL)" \
	 LOCAL_MODEL_NAME="$(LOCAL_MODEL_NAME)" \
	 LOCAL_MODEL_KEY="$(LOCAL_MODEL_KEY)" \
	 LOCAL_MODEL_GAME="$(LOCAL_MODEL_GAME)" \
	 LOCAL_MODEL_PATH="$(LOCAL_MODEL_PATH)" \
	 PYTEST_ARGS="$(PYTEST_ARGS)" \
	 $(UV) run --no-sync python -m scripts.run_local_model_game_e2e

# CI/CD multi-model pipeline E2E — reads keys from env or shared key files.
# DeepSeek + OpenRouter tiers, structural tests when keys are absent.
# Writes results to /tmp/gludd-multi-model-results.json for CI artifacts.
# CI_GAME=snake runs a single-game smoke test.
test-e2e-multi-model:
	@DEEPSEEK_API_KEY="$${DEEPSEEK_API_KEY:-}" \
	 OPENROUTER_API_KEY="$${OPENROUTER_API_KEY:-}" \
	 CI_GAME="$${CI_GAME:-}" \
	 $(UV) run pytest tests/e2e/test_ci_multi_model_pipeline.py -v -s $(PYTEST_ARGS)

test-multi-model-pipeline:
	@$(UV) run pytest tests/e2e/test_ci_multi_model_pipeline.py tests/e2e/test_multi_model_game_gen.py tests/e2e/test_multi_model_game_pipeline.py tests/e2e/test_multi_model_pipeline_cloud.py tests/e2e/test_cloud_e2e_multi_model.py tests/integration/test_multi_model_pipeline_integration.py -v -s $(PYTEST_ARGS)

# Full game-dev pipeline: iterates all local models through planner→coder→reviewer
# for 4 game types. CI_SAFE=1 limits to ci_safe models (<500MB, 6 models).
# GAME_DEV_MODEL=Name targets a single model. GAME_DEV_GAME=snake targets one game.
# Writes results to /tmp/gludd-game-dev-pipeline-results.json
test-e2e-game-pipeline:
	@$(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=ci-local-inference DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON= DEPENDENCY_PROFILE_VALIDATE_ONLY=0
	@GLUDD_LIVE_MODEL_E2E="1" \
	 GAME_DEV_CI_SAFE="$${CI_SAFE:-1}" \
	 GAME_DEV_MODEL="$${GAME_DEV_MODEL:-}" \
	 GAME_DEV_GAME="$${GAME_DEV_GAME:-}" \
	 $(UV) run --no-sync pytest tests/e2e/test_game_dev_full_pipeline.py -v -s $(PYTEST_ARGS)

test-local-model-pipeline:
	@$(UV) run pytest tests/e2e/test_local_model_multi_pipeline.py tests/e2e/test_local_model_discovery_eval.py -v -s $(PYTEST_ARGS)

test-project-type-pipeline:
	@$(UV) run pytest tests/e2e/test_project_type_pipeline.py -v -s $(PYTEST_ARGS)

.PHONY: test-llama-game-gen
test-llama-game-gen: sync-llama-cpp
	@echo "=== Llama-3.2-1B Game Gen Test ==="
	@mkdir -p /tmp/gludd-hf-cache
	@HF_HOME=/tmp/gludd-hf-cache HF_HUB_CACHE=/tmp/gludd-hf-cache $(UV) run python scripts/test_llama_3_2_game_gen.py

# ── Local model serving (ollama) ────────────────────────────────────────────
# OLLAMA_MODEL: model to pull (default: qwen2.5:0.5b, small + fast for E2E)
OLLAMA_MODEL ?= qwen2.5:0.5b
_OLLAMA_BIN := $(shell command -v ollama 2>/dev/null || echo "")
_OLLAMA_URL := http://localhost:11434

local-model-ollama: _local-model-ollama-install-check _local-model-ollama-serve

_local-model-ollama-install-check:
	@if [ -z "$(_OLLAMA_BIN)" ]; then \
		echo "ollama not found — install via: brew install ollama"; \
		exit 1; \
	fi
	@echo "  ollama found at $(_OLLAMA_BIN)"

_local-model-ollama-serve:
	@if curl -sSf -o /dev/null "$(_OLLAMA_URL)/api/tags" 2>/dev/null; then \
		echo "  ollama already running"; \
	else \
		echo "  starting ollama serve..."; \
		ollama serve > /tmp/ollama-serve.log 2>&1 & \
		sleep 2; \
	fi
	@if ! curl -sSf -o /dev/null "$(_OLLAMA_URL)/api/tags" 2>/dev/null; then \
		echo "  ollama still not reachable — check /tmp/ollama-serve.log"; \
		exit 1; \
	fi
	@echo "  pulling model $(OLLAMA_MODEL)..."
	@ollama pull $(OLLAMA_MODEL)
	@echo "  local model ready: $(OLLAMA_MODEL) at $(_OLLAMA_URL)"

local-model-stop:
	@if curl -sSf -o /dev/null "$(_OLLAMA_URL)/api/tags" 2>/dev/null; then \
		echo "  stopping ollama..."; \
		pkill -f "ollama serve" 2>/dev/null || true; \
		echo "  stopped"; \
	else \
		echo "  ollama not running"; \
	fi

local-model-status:
	@if curl -sSf -o /dev/null "$(_OLLAMA_URL)/api/tags" 2>/dev/null; then \
		echo "  ollama RUNNING at $(_OLLAMA_URL)"; \
		curl -sSf "$(_OLLAMA_URL)/api/tags" | python3 -m json.tool 2>/dev/null || true; \
	else \
		echo "  ollama NOT running at $(_OLLAMA_URL)"; \
	fi

game-audit:
	@$(PYTHON) scripts/game_audit.py

gen-mcp-tools:
	@$(UV) run python scripts/gen_mcp_tools.py

gen-mcp-tool-ref: gen-mcp-tools
	@$(UV) run python scripts/gen_mcp_tool_reference_md.py

check-generated-artifact-hygiene:
	@$(UV) run python scripts/check_generated_artifact_hygiene.py

mcp-docs-check:
	@$(UV) run python scripts/mcp_docs_check.py
	@$(UV) run python scripts/gen_mcp_tool_reference_md.py --check

test-tui-daemon:
	@$(UV) run python -m pytest tests/e2e/test_tui_daemon_start.py -v -s

test-guardrails:
	@$(UV) run python -m pytest tests/unit/test_guardrails.py tests/unit/test_user_requested_guardrails.py $(_XD) -v

# CI-runnable hook-liveness harness (Wave E): actually invokes .opencode/plugin/*.ts
# hooks via scripts/hook_plugin_harness.mjs (node --experimental-strip-types, no
# npm install) and asserts real state-file side effects. Excluded from the default
# `-m "not hook_live"` addopts filter; run explicitly here or via `-m hook_live`.
# Skips cleanly (not fails) when node < 22.6 / absent.
test-hooks-live:
	@$(UV) run python -m pytest -m hook_live -v

# Strip TypeScript syntax from enforce-stop.ts for Node v26 compat.
# node --experimental-strip-types fails on `as const` in property values
# and interface blocks inside complex expressions. This target runs a
# Python script that strips those constructs so the file can be loaded.
strip-enforce-stop:
	@$(UV) run python scripts/strip_enforce_stop_ts.py

# Functional hook runtime tests: invokes actual plugin hook functions via
# node --experimental-strip-types and verifies runtime behavior (deny/allow,
# state-file side effects, fail-open). Distinct from structural source-pattern
# tests; these tests MEASURE hook behavior, not source code shape.
test-hook-runtime:
	@$(UV) run python scripts/test_hook_runtime.py -v


# E2E verification: loads every .opencode/ plugin via Node.js, calls factories,
# invokes hooks, verifies no crashes. Catches auto-discovered non-plugin files
# (Session 51 _exports.ts incident), old-API/new-API mismatches, and CRASH-level
# hook failures that structural tests miss.
# E2E test project setup: symlinks .opencode/plugin/, .opencode/lib/, etc. from
# the main repo into tests/opencode_e2e/_test_project/
e2e-setup-test-project:
	@bash tests/opencode_e2e/_test_project/setup.sh

test-opencode-e2e:
	@$(UV) run python -m pytest tests/e2e/test_opencode_plugin_load.py tests/opencode_e2e/test_multitask_behavior.py -v
test-multitask-e2e:
	@TMPDIR=$${TMPDIR:-/tmp} $(UV) run python -m pytest tests/opencode_e2e/test_multitask_behavior.py -v --timeout=3600 --tb=short
test-spawner-e2e:
	@$(UV) run python tests/opencode_e2e/run_spawner_test.py --timeout $(TIMEOUT) --progress-interval $(PROGRESS) --no-cleanup
test-spawner-e2e-quick:
	@$(UV) run python tests/opencode_e2e/run_spawner_test.py --timeout 60 --progress-interval 15
test-spawner-e2e-notemp:
	@$(UV) run python tests/opencode_e2e/run_spawner_test.py --timeout $(TIMEOUT) --progress-interval $(PROGRESS) --no-temp --no-cleanup
test-opencode-e2e-hour:
	@mkdir -p /tmp/gludd-opencode-e2e
	@echo "=== E2E HOUR TEST: timeout=$(TIMEOUT)s ==="
	@$(UV) run python tests/opencode_e2e/run_hour_e2e.py --timeout=$(TIMEOUT)
test-opencode-e2e-quick:
	@mkdir -p /tmp/gludd-opencode-e2e
	@echo "=== E2E QUICK TEST: 5min ==="
	@$(UV) run python tests/opencode_e2e/run_hour_e2e.py --quick
diag-opencode:
	@opencode --help 2>&1 || echo "EXIT: $$?"
	@opencode --version 2>&1 || echo "EXIT: $$?"
	@echo "---"
	@ls -la /tmp/gludd-opencode-e2e/ 2>&1 || true
diag-opencode-run:
	@opencode run --help 2>&1
diag-opencode-raw-json:
	@echo "=== Capturing raw opencode --format json output ==="
	@rm -f /tmp/gludd-raw-json-*.log
	@cd /Users/shawnwilson/gludd/tests/opencode_e2e/_test_project && printf 'Say hello and then exit.\n' | opencode run --format json --agent build --auto --log-level ERROR --model deepseek/deepseek-v4-pro 2>/tmp/gludd-raw-json-stderr.log > /tmp/gludd-raw-json-stdout.log
	@echo "EXIT: $$?"
	@echo "--- STDOUT first 200 lines ---"
	@head -200 /tmp/gludd-raw-json-stdout.log 2>/dev/null || true
diag-opencode-raw-json-pure:
	@echo "=== opencode --pure: bypass all plugins ==="
	@rm -f /tmp/gludd-raw-json-pure-*.log
	@cd /Users/shawnwilson/gludd/tests/opencode_e2e/_test_project && printf 'Say hello and then exit.\n' | opencode run --format json --auto --pure --log-level ERROR --model deepseek/deepseek-v4-pro 2>/tmp/gludd-raw-json-pure-stderr.log > /tmp/gludd-raw-json-pure-stdout.log
	@echo "EXIT: $$?"
	@echo "--- STDOUT first 100 lines ---"
	@head -100 /tmp/gludd-raw-json-pure-stdout.log 2>/dev/null || true
	@echo "--- STDERR ---"
	@head -20 /tmp/gludd-raw-json-pure-stderr.log 2>/dev/null || true
diag-opencode-e2e-simple:
	@echo "=== opencode E2E with --agent build ==="
	@rm -f /tmp/gludd-raw-json-e2e-*.log
	@cd /Users/shawnwilson/gludd/tests/opencode_e2e/_test_project && printf 'Write "hello" to output/e2e-hello.txt using make task1.\n' | opencode run --format json --agent build --auto --log-level ERROR --model deepseek/deepseek-v4-pro 2>/tmp/gludd-raw-json-e2e-stderr.log > /tmp/gludd-raw-json-e2e-stdout.log
	@echo "EXIT: $$?"
	@echo "--- STDOUT first 100 lines ---"
	@head -100 /tmp/gludd-raw-json-e2e-stdout.log 2>/dev/null || true
	@echo "--- STDERR ---"
	@head -20 /tmp/gludd-raw-json-e2e-stderr.log 2>/dev/null || true
diag-opencode-e2e-full:
	@echo "=== E2E full prompt direct ==="
	@rm -f /tmp/gludd-diag-e2e-*.log
	@cd /Users/shawnwilson/gludd/tests/opencode_e2e/_test_project && GLUDD_MODEL_UTIL_ENFORCE=0 GLUDD_FLOOR_ENFORCE=0 GLUDD_ENHANCEMENT_RATIO_BLOCK=0 GLUDD_CLEAN_TREE_ENFORCE=0 GLUDD_TDD_ENFORCE=0 GLUDD_TASK_DEADLINE_BLOCK=0 GLUDD_MAKE_ENFORCE=0 GLUDD_VERIFIED_CLAIMS_ENFORCE=0 opencode run --format json --auto --agent build --log-level ERROR "Read TASKS.md. There are 18 tasks. Dispatch EXACTLY 10 task subagents to complete tasks T1 through T10. Each subagent should run: make taskN. Do NOT wait for results before dispatching. Dispatch ALL 10 in ONE response. After dispatching, say the word DISPATCHED and exit." 2>/tmp/gludd-diag-e2e-stderr.log > /tmp/gludd-diag-e2e-stdout.log
	@echo "EXIT: $$?"
	@echo "Line count:" && wc -l /tmp/gludd-diag-e2e-stdout.log
	@echo "=== grep for task ==="
	@grep -c '"tool":"task"' /tmp/gludd-diag-e2e-stdout.log 2>/dev/null || echo "0 task dispatches"
	@echo "=== grep for ERROR ==="
	@grep -c 'MODEL-RATIO\|MODEL.UTIL' /tmp/gludd-diag-e2e-stdout.log 2>/dev/null || echo "0 model-ratio blocks"
	@echo "=== last 10 lines ==="
	@tail -10 /tmp/gludd-diag-e2e-stdout.log 2>/dev/null || true
	@echo "=== STDERR ==="
	@head -20 /tmp/gludd-diag-e2e-stderr.log 2>/dev/null || true
bisect-ts-parse:
	@$(PYTHON) scripts/bisect_ts_parse.py


# Fix plugin exports for Bun compatibility: replace 'satisfies Plugin' with proper closing
fix-plugin-bun-exports:
	@python3 -c "import os,re; \
[open(p,'w').write(re.sub(r'\\) satisfies Plugin;?', '}));', open(p).read())) \
for d in ['.opencode/plugin','.opencode/plugins'] if os.path.isdir(d) \
for p in [os.path.join(d,f) for f in os.listdir(d) if f.endswith('.ts')]]"
	@echo "Fixed all plugin exports for Bun compatibility"

# Re-add binary boot test target (lost in git restore)
test-opencode-binary-boot:
	@$(UV) run python -m pytest tests/e2e/test_opencode_binary_boot.py -v

# Combined: fix plugins then test against opencode binary
test-opencode-binary:
	@$(MAKE) fix-plugin-bun-exports > /dev/null 2>&1
	@$(MAKE) test-opencode-binary-boot
# Node v26 --experimental-strip-types compatibility: loads every .ts plugin
# file and asserts exit code 0. Catches patterns like try-inside-catch
# without semicolon separator that Node v26's TS parser rejects.
check-molecule-yaml:
	@$(UV) run python scripts/check_molecule_yaml.py

check-workflow-yaml:
	@$(UV) run python -c "import yaml, sys; f=sys.argv[1] if len(sys.argv)>1 else '.github/workflows/build.yml'; yaml.safe_load(open(f)); print(f'YAML valid ({f})')" .github/workflows/build.yml

check-node-v26-compat:
	@BT="/tmp/gludd-node-v26-$${ID:-$$$$}"; /bin/rm -rf "$$BT"; $(UV) run python -m pytest tests/unit/test_opencode_node_v26_compat.py $(_XD) -v --basetemp="$$BT"; RC=$$?; /bin/rm -rf "$$BT"; exit $$RC

test-db:
	@$(UV) run python -m pytest tests/unit/test_db_models.py $(_XD) -v

test-scripts:
	@$(UV) run python -m pytest tests/unit/test_guardrails.py::TestSkeletonScript $(_XD) -v

test-install:
	@command -v bats >/dev/null 2>&1 || { echo "bats not installed — run: make install-bats"; exit 1; }
	@echo "Running install.sh bats tests..."
	@mkdir -p dist tests/install
	@BATS_TEST_DIRNAME="$$(pwd)/tests/install" bats --print-output-on-failure tests/install/install.bats

healthcheck:
	@$(UV) run --no-sync python -c "from general_ludd.worker.app import create_app; app = create_app(); print('Worker app factory OK')"
	@$(UV) run --no-sync python -c "from general_ludd.event_loop.loop import EventLoop; print('Event loop import OK')"
	@$(UV) run --no-sync python -c "from general_ludd.commands.make import MakeRunner; print('MakeRunner import OK')"

ansible-syntax:
	@for f in playbooks/*.yml; do echo "Checking $$f..."; $(UV) run --no-sync ansible-playbook -i config/ansible_syntax_inventory.yml --syntax-check "$$f" || exit 1; done

ansible-lint-playbooks: yaml-lint

ansible-collection-test:
	@echo "=== Ansible Collection Tests (pytest) ==="
	@$(UV) run python -m pytest tests/integration/test_playbook_registry.py -v

playbook-list:
	@ls -1 playbooks/*.yml 2>/dev/null || echo "No playbooks found"

molecule-version:
	@$(UV) run molecule --version

# List collection roles + gludd_* modules (coverage enumeration helper)
collection-roles:
	@ls -1 collections/ansible_collections/general_ludd/agent/roles 2>/dev/null || echo "No roles found"

collection-modules:
	@ls -1 collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_*.py 2>/dev/null || echo "No modules found"

# Scaffold missing expert-service collection roles (materials/chemistry/ai_ml/git_release).
# Idempotent: skips roles that already exist. Use FORCE=1 to overwrite.
scaffold-collection-roles:
	@$(UV) run python scripts/scaffold_collection_roles.py $(if $(FORCE),--force,)

molecule-scenarios:
	@ls -1 molecule/playbooks 2>/dev/null || echo "No scenarios found"

# Run EVERY scenario under molecule/playbooks/ in sequence; fail if any fail.
# Per-scenario output goes to /tmp/gludd-molecule-<name>.log; a PASS/FAIL
# summary is printed at the end (and on failure exits non-zero).
molecule-test-all:
	@echo "=== molecule-test-all: running every scenario under molecule/playbooks/ ==="
	@FAILED=""; PASSED=""; \
	for d in molecule/playbooks/*/; do \
		s=$$(basename "$$d"); \
		echo "--- running scenario: $$s (log: /tmp/gludd-molecule-$$s.log) ---"; \
		if $(MAKE) --no-print-directory molecule-test SCENARIO="$$s" > "/tmp/gludd-molecule-$$s.log" 2>&1; then \
			PASSED="$$PASSED $$s"; echo "    PASS $$s"; \
		else \
			FAILED="$$FAILED $$s"; echo "    FAIL $$s (see /tmp/gludd-molecule-$$s.log)"; \
			echo "---- BEGIN failed molecule log: $$s ----"; \
			tail -n $${MOLECULE_LOG_TAIL_LINES:-200} "/tmp/gludd-molecule-$$s.log" || true; \
			echo "---- END failed molecule log: $$s ----"; \
		fi; \
	done; \
	echo ""; echo "PASSED:$$PASSED"; \
	if [ -n "$$FAILED" ]; then echo "FAILED:$$FAILED"; exit 1; fi; \
	echo "=== molecule-test-all: ALL scenarios passed ==="

# Sharded test runner for CI: SHARD=1/4 runs the first quarter of scenarios.
# Scenarios are sorted by name; the SHARD numerator selects a contiguous slice.
molecule-test-shard:
	@echo "=== molecule-test-shard: shard $(SHARD) ==="
	@numerator=$$(echo "$(SHARD)" | cut -d/ -f1); \
	denominator=$$(echo "$(SHARD)" | cut -d/ -f2); \
	ALL=$$(ls -d molecule/playbooks/*/ 2>/dev/null | sort); \
	COUNT=$$(echo "$$ALL" | wc -l | tr -d ' '); \
	SIZE=$$(( (COUNT + denominator - 1) / denominator )); \
	START=$$(( (numerator - 1) * SIZE + 1 )); \
	END=$$(( START + SIZE - 1 )); \
	HOST_OS=$$(uname -s); \
	echo "  Total scenarios: $$COUNT, shard $$numerator/$$denominator → slice $$START-$$END"; \
	INDEX=0; FAILED=""; PASSED=""; SKIPPED=""; \
	for d in $$ALL; do \
		INDEX=$$((INDEX + 1)); \
		if [ $$INDEX -lt $$START ] || [ $$INDEX -gt $$END ]; then continue; fi; \
		s=$$(basename "$$d"); \
		if [ "$$s" = "binary_smoke_macos" ] && [ "$$HOST_OS" != "Darwin" ]; then \
			SKIPPED="$$SKIPPED $$s"; echo "    SKIP $$s (macOS-only scenario on $$HOST_OS)"; \
			continue; \
		fi; \
		echo "--- running scenario: $$s ($$INDEX/$$COUNT) ---"; \
		if $(MAKE) --no-print-directory molecule-test SCENARIO="$$s" > "/tmp/gludd-molecule-$$s.log" 2>&1; then \
			PASSED="$$PASSED $$s"; echo "    PASS $$s"; \
		else \
			FAILED="$$FAILED $$s"; echo "    FAIL $$s (see /tmp/gludd-molecule-$$s.log)"; \
			echo "---- BEGIN failed molecule log: $$s ----"; \
			tail -n $${MOLECULE_LOG_TAIL_LINES:-200} "/tmp/gludd-molecule-$$s.log" || true; \
			echo "---- END failed molecule log: $$s ----"; \
		fi; \
	done; \
	echo ""; echo "SHARD-PASSED:$$PASSED"; \
	if [ -n "$$SKIPPED" ]; then echo "SHARD-SKIPPED:$$SKIPPED"; fi; \
	if [ -n "$$FAILED" ]; then echo "SHARD-FAILED:$$FAILED"; exit 1; fi; \
	echo "=== molecule-test-shard: ALL passed ==="

# Log a subagent result to JSONL so it survives text blanking.
# Usage: make log-agent-result AGENT_ID=agent-foo RESULT_SUMMARY="fixed X"
log-agent-result:
	@$(UV) run python3 scripts/log_agent_result.py
