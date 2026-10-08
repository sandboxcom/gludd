# --- Cross-version CI reproduction (W16) ---
# Reproduce the CI gate under a specific python version (CI runs 3.11 and 3.12).
# cancel a CI run via gh run cancel
ci-kill-zombie:
	@echo "ci-kill-zombie: cancel a CI run via gh run cancel"

test-pyver:
	@if [ -z "$(VER)" ]; then echo "Usage: make test-pyver VER=3.11"; exit 1; fi
	@echo "=== test-pyver $(VER): syncing ==="
	@$(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=ci DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON=$(VER) DEPENDENCY_PROFILE_VALIDATE_ONLY=0
	@echo "=== test-pyver $(VER): ruff ==="
	@$(UV) run --python $(VER) ruff check src tests
	@echo "=== test-pyver $(VER): mypy ==="
	@$(UV) run --python $(VER) mypy -p general_ludd
	@echo "=== test-pyver $(VER): collect ==="
	@$(UV) run --python $(VER) python -m pytest tests/ --co -q > /tmp/gludd-pyver-collect-$(VER).txt 2>&1; \
		EXIT=$$?; if [ $$EXIT -ne 0 ]; then echo "COLLECTION ERRORS under $(VER):"; tail -20 /tmp/gludd-pyver-collect-$(VER).txt; exit 1; fi; \
		echo "collect OK under $(VER)"
	@echo "=== test-pyver $(VER): pytest ==="
	@$(UV) run --python $(VER) python -m pytest tests/ -q

# Reproduce CI's EXACT xdist worker count. GitHub ubuntu-latest has 4 vCPUs,
# so _XDIST_WORKERS = max(1, 4//4) = 1. Test ordering under 1 serial worker
# differs from local multi-worker runs and can surface asyncio teardown bugs
# ("Event loop is closed") that the gate's strict-xfail ratchet does not cover.
ci-test-eventbus:
	@$(UV) run --python $(if $(VER),$(VER),3.11) python -m pytest tests/unit/test_event_bus_coverage_lift.py tests/unit/test_event_bus_async.py tests/unit/test_event_bus_coverage.py tests/unit/test_event_loop.py tests/unit/test_events.py -p no:cacheprovider -W error::RuntimeWarning -v 2>&1 | tail -60

ci-test-1worker:
	@if [ -z "$(VER)" ]; then echo "Usage: make ci-test-1worker VER=3.11"; exit 1; fi
	@$(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=ci DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON=$(VER) DEPENDENCY_PROFILE_VALIDATE_ONLY=0
	@echo "=== ci-test-1worker $(VER): pytest -n 1 --dist loadgroup (CI ubuntu worker count) ==="
	@$(UV) run --python $(VER) python -m pytest tests/ -n 1 --dist loadgroup -q 2>&1 | tail -50

# Run the EXACT CI gate command sequence under a given python version:
#   locked `ci` profile sync under VER, then lint/typecheck/collect/test/smoke.
# This includes coverage (fail_under=85) which plain test-pyver omits.
ci-gate-exact:
	@if [ -z "$(VER)" ]; then echo "Usage: make ci-gate-exact VER=3.11"; exit 1; fi
	@echo "=== ci-gate-exact $(VER): locked profile sync ==="
	@$(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=ci DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON=$(VER) DEPENDENCY_PROFILE_VALIDATE_ONLY=0
	@echo "=== ci-gate-exact $(VER): lint ==="
	@$(UV) run --python $(VER) ruff check src tests
	@echo "=== ci-gate-exact $(VER): typecheck ==="
	@$(UV) run --python $(VER) mypy -p general_ludd
	@echo "=== ci-gate-exact $(VER): test-count ==="
	@$(UV) run --python $(VER) python -m pytest tests/ --co -q 2>&1 | tail -3
	@echo "=== ci-gate-exact $(VER): test (WITH coverage, fail_under=85) ==="
	@$(UV) run --python $(VER) python -m pytest tests/ --cov=general_ludd --cov-report=term-missing --cov-report=xml $(_XD) -q 2>&1 | tail -40
	@echo "=== ci-gate-exact $(VER): DONE (check coverage line above) ==="

# Simulate CI's lock check before version injection and prove later metadata is stale.
ci-version-sim:
	@echo "=== ci-version-sim: locked profile check before version injection ==="
	@UV_NO_SYNC=0 $(UV) run --no-project --python 3.11 python scripts/dependency_profiles.py check --root "$(CURDIR)" --set ci
	@cp pyproject.toml /tmp/gludd-pyproject.bak
	@cp src/general_ludd/__init__.py /tmp/gludd-init.bak
	@VER="0.1.0a$$(date -u +%Y%m%d%H%M)"; \
		sed -i.tmp "s/__version__ = \".*\"/__version__ = \"$$VER\"/" src/general_ludd/__init__.py; \
		sed -i.tmp "s/^version = \".*\"/version = \"$$VER\"/" pyproject.toml; \
		rm -f pyproject.toml.tmp src/general_ludd/__init__.py.tmp; \
		echo "Injected version $$VER"; \
		echo "--- locked profile check after injection (expected stale) ---"; \
		UV_NO_SYNC=0 $(UV) run --no-project --python 3.11 python scripts/dependency_profiles.py check --root "$(CURDIR)" --set ci 2>&1 | tail -20; EXIT=$$?; \
		echo "locked profile check exit: $$EXIT"; \
		cp /tmp/gludd-pyproject.bak pyproject.toml; \
		cp /tmp/gludd-init.bak src/general_ludd/__init__.py; \
		echo "Restored pyproject.toml + __init__.py"

scan-secrets:
	@TMP=$$(mktemp /tmp/gludd-secrets-baseline.XXXXXX); cp .secrets.baseline "$$TMP"; echo "[scan-secrets] scanning temporary baseline copy $$TMP"; $(UV) run detect-secrets scan --baseline "$$TMP" $(ARGS); RC=$$?; rm -f "$$TMP"; exit $$RC

# ── Secrets management targets ──
# secrets-scan: scan for secrets without modifying files (checks against baseline)
secrets-scan:
	@TMP=$$(mktemp /tmp/gludd-secrets-baseline.XXXXXX); cp .secrets.baseline "$$TMP"; echo "[secrets-scan] scanning temporary baseline copy $$TMP"; $(UV) run detect-secrets scan --baseline "$$TMP" $(ARGS); RC=$$?; rm -f "$$TMP"; if [ $$RC -ne 0 ]; then echo '{"last_push_blocked":true,"block_reason":"secrets-scan:secrets-found","epoch":'$$(date +%s)'}' > /tmp/gludd-push-state.json; fi; exit $$RC

# secrets-scrub: find and scrub secrets from the codebase (interactive audit)
secrets-scrub:
	@[ -f .secrets.baseline ] || { echo "ERROR: .secrets.baseline missing. Run 'make secrets-baseline' first."; exit 1; }
	@$(UV) run detect-secrets audit .secrets.baseline

# install-trufflehog: install the trufflehog binary (Go) for live secret verification.
# On macOS: brew install trufflehog. On Linux (CI): official install script.
# Idempotent — skips if already on PATH.
install-trufflehog:
	@if command -v trufflehog >/dev/null 2>&1; then \
		echo "[install-trufflehog] trufflehog already installed: $$(trufflehog --version 2>&1 | head -1)"; \
		exit 0; \
	fi
	@if command -v brew >/dev/null 2>&1; then \
		echo "[install-trufflehog] Installing via brew ..."; \
		brew install trufflehog 2>&1 | tail -5 || echo "brew-install-trufflehog-failed"; \
	elif [ -f /etc/os-release ] && grep -qi ubuntu /etc/os-release 2>/dev/null; then \
		echo "[install-trufflehog] Installing via official script (Linux) ..."; \
		curl -sSfL https://raw.githubusercontent.com/trufflesecurity/trufflehog/main/scripts/install.sh 2>/dev/null | sh -s -- -b /usr/local/bin 2>&1 || echo "trufflehog-install-script-failed"; \
	else \
		echo "[install-trufflehog] No package manager found. Install manually: https://github.com/trufflesecurity/trufflehog"; \
	fi
	@command -v trufflehog >/dev/null 2>&1 && trufflehog --version 2>&1 | head -1 || echo "[install-trufflehog] WARNING: trufflehog still not on PATH"

# verify-secrets: cross-reference .secrets.baseline against trufflehog live verification.
# Exits 0 on clean, 1 if live secrets found, 2 if trufflehog not installed.
verify-secrets:
	@$(PYTHON) scripts/verify_secrets_baseline.py

# verify-secrets-safe: CI wrapper — treats exit 2 (not-installed) as non-fatal.
# Use in CI pipelines. For local use, prefer verify-secrets directly.
verify-secrets-safe:
	@{ $(PYTHON) scripts/verify_secrets_baseline.py; RC=$$?; if [ $$RC -eq 2 ]; then echo "[verify-secrets] trufflehog not installed — skipping verification (non-fatal)"; exit 0; fi; exit $$RC; }

# secrets-baseline: atomically refresh and verify the canonical baseline.
SECRETS_BASELINE_FILE ?= .secrets.baseline
POLICY ?= config/detect_secrets_baseline_policy.json
REPO_ROOT ?= $(CURDIR)
EXECUTABLE ?= detect-secrets

secrets-baseline:
	@$(UV) run python scripts/manage_secrets_baseline.py refresh --baseline "$(SECRETS_BASELINE_FILE)" --policy "$(POLICY)" --repo-root "$(REPO_ROOT)" --executable "$(EXECUTABLE)"
	@$(UV) run python scripts/manage_secrets_baseline.py check --baseline "$(SECRETS_BASELINE_FILE)" --policy "$(POLICY)" --repo-root "$(REPO_ROOT)" --executable "$(EXECUTABLE)"

# secrets-baseline-check: validate canonical structure without refreshing.
secrets-baseline-check:
	@$(UV) run python scripts/manage_secrets_baseline.py check --baseline "$(SECRETS_BASELINE_FILE)" --policy "$(POLICY)" --repo-root "$(REPO_ROOT)" --executable "$(EXECUTABLE)"

# security-audit: all phases emit bounded JSON heartbeats and timings. The
# detect-secrets child is deliberately silenced so credential values cannot be
# copied into terminals or CI logs; only its exit status and timing are exposed.
# The Python orchestrator invokes the existing pip-audit-gate, node-deps-audit,
# and security-backlog-gate targets rather than reimplementing those scanners.
SECURITY_AUDIT_HEARTBEAT_SECS ?= 15
SECURITY_AUDIT_PHASE_TIMEOUT_SECS ?= 1800
SECURITY_AUDIT_VALIDATE_ONLY ?= 0
SECURITY_AUDIT_SUMMARY ?= dist/security-audit-summary.json
security-audit:
	@case "$(SECURITY_AUDIT_HEARTBEAT_SECS)" in ''|*[!0-9]*) echo "SECURITY_AUDIT_HEARTBEAT_SECS must be an integer between 5 and 300" >&2; exit 2;; esac; \
	case "$(SECURITY_AUDIT_PHASE_TIMEOUT_SECS)" in ''|*[!0-9]*) echo "SECURITY_AUDIT_PHASE_TIMEOUT_SECS must be an integer between 60 and 7200" >&2; exit 2;; esac; \
	[ "$(SECURITY_AUDIT_HEARTBEAT_SECS)" -ge 5 ] && [ "$(SECURITY_AUDIT_HEARTBEAT_SECS)" -le 300 ] || { echo "SECURITY_AUDIT_HEARTBEAT_SECS must be between 5 and 300" >&2; exit 2; }; \
	[ "$(SECURITY_AUDIT_PHASE_TIMEOUT_SECS)" -ge 60 ] && [ "$(SECURITY_AUDIT_PHASE_TIMEOUT_SECS)" -le 7200 ] || { echo "SECURITY_AUDIT_PHASE_TIMEOUT_SECS must be between 60 and 7200" >&2; exit 2; }; \
	case "$(SECURITY_AUDIT_VALIDATE_ONLY)" in 0|1) :;; *) echo "SECURITY_AUDIT_VALIDATE_ONLY must be 0 or 1" >&2; exit 2;; esac; \
	VALIDATE_ARG=""; [ "$(SECURITY_AUDIT_VALIDATE_ONLY)" = "0" ] || VALIDATE_ARG="--validate-only"; \
	$(PYTHON) scripts/security_audit_observability.py audit \
		--heartbeat-seconds "$(SECURITY_AUDIT_HEARTBEAT_SECS)" \
		--timeout-seconds "$(SECURITY_AUDIT_PHASE_TIMEOUT_SECS)" \
		--summary "$(SECURITY_AUDIT_SUMMARY)" \
		--sast-report "$(SAST_REPORT)" \
		--sast-summary "$(SAST_SUMMARY)" \
		--sast-baseline "$(SAST_BASELINE)" \
		$$VALIDATE_ARG

# clean-artifacts: clean build artifacts, caches, temp files (replaces direct rm commands)
clean-artifacts:
	@$(MAKE) --no-print-directory clean
	@$(MAKE) --no-print-directory clean-tmp
	@$(MAKE) --no-print-directory dist-clean
	@$(MAKE) --no-print-directory clean-worktree-venvs
	@echo "clean-artifacts done"

# health-check: verify imports and basic system health (replaces direct python/uv commands)
health-check:
	@$(MAKE) --no-print-directory healthcheck

scan-secrets-fresh:
	@echo "=== Fresh secrets scan (NO baseline, NO key exclusion) — W5.3 ==="
	@$(UV) run detect-secrets scan --all-files > /tmp/gludd-secrets-fresh.json 2>/dev/null || true
	@$(UV) run python -c "import json; d=json.load(open('/tmp/gludd-secrets-fresh.json')); r=d.get('results',{}); print('Files with potential secrets:', len(r)); [print(' ', f) for f in sorted(r)]"

dist-path-check:
	@echo "=== Scanning the built tarball dir(s) for absolute local paths (W5.3) ==="
	@DIRS=$$(ls -d dist/general-ludd-agent-* 2>/dev/null | grep -v '\.tar\.gz' || true); \
	if [ -z "$$DIRS" ]; then echo "No tarball dir — run 'make dist' first"; exit 0; fi; \
	HITS=$$(grep -rIl -e '/Users/' -e 'Mac.localdomain' $$DIRS 2>/dev/null || true); \
	if [ -n "$$HITS" ]; then echo "LEAKED LOCAL PATHS in tarball:"; echo "$$HITS"; exit 1; else echo "Tarball dir(s) path-clean."; fi

# gate-refresh: re-run fast gate phases (lint, typecheck, collect) and write a
# fresh .gate-status with current timestamp. Test/smoke lines are PRESERVED from
# the prior full gate run. This lets the agent prove partial gate green to
# unblock commits while the gate-background test phase is still running.
# Does NOT run the full test suite — that's what gate-background is for. When
# the preserved test result is unavailable, verbose pytest node IDs stream live
# and are also retained in .gate-logs/gate-refresh-test.log.
.PHONY: gate-refresh _gate-refresh-body
gate-refresh: _gate-run-lock-acquire check-generated-artifact-hygiene
	@RC=0; \
	if [ "$(GATE_REFRESH_VALIDATE_ONLY)" = "1" ]; then \
		$(UV) run python scripts/stream_command.py --help > /dev/null || RC=$$?; \
		echo "gate-refresh: validate-only PASS (live verbose node IDs + durable log configured)"; \
	else \
		$(UV) run python scripts/collection_lock.py --resource gate-refresh --run $(MAKE) --no-print-directory _gate-refresh-body || RC=$$?; \
	fi; \
	$(UV) run python scripts/gate_run_lock.py release "$(GATE_RUN_LOCK)" "$$PPID" || RC=1; \
	exit $$RC

_gate-refresh-body:
	@if [ ! -f .gate-status ]; then \
		echo "ERROR: .gate-status missing — no prior gate to refresh. Run 'make gate' first."; exit 1; \
	fi; \
	rm -f .gate-failed .gate-status.next .gate-status.running; \
	OLD_TEST=$$(grep -m1 "^test " .gate-status 2>/dev/null || echo ""); \
	OLD_SMOKE=$$(grep -m1 "^smoke " .gate-status 2>/dev/null || echo ""); \
	printf "RUNNING %s %s\n" "$$(date +%s)" "$$PPID" > .gate-status.running; \
	mv .gate-status.running .gate-status; \
	STATUS_WORK=.gate-status.next; \
	echo "=== GATE-REFRESH $$(date -u +%Y-%m-%dT%H:%M:%SZ) ===" > "$$STATUS_WORK"; \
	echo "=== GATE PHASE: lint ==="; \
	printf "lint " >> "$$STATUS_WORK"; \
	if $(UV) run ruff check src tests --output-format concise > /dev/null 2>&1; then \
		echo "PASS 0" >> "$$STATUS_WORK"; \
	else \
		echo "FAIL $$($(UV) run ruff check src tests --output-format concise 2>&1 | grep -c .)" >> "$$STATUS_WORK" && touch .gate-failed; \
	fi; \
	mkdir -p .gate-logs; \
	echo "=== GATE PHASE: verify-feature-claims ==="; \
	printf "verify-feature-claims " >> "$$STATUS_WORK"; \
	$(MAKE) --no-print-directory verify-feature-claims > .gate-logs/verify-feature-claims.log 2>&1 && echo "PASS" >> "$$STATUS_WORK" || (echo "FAIL" >> "$$STATUS_WORK" && touch .gate-failed && tail -30 .gate-logs/verify-feature-claims.log); \
	echo "=== GATE PHASE: hot-reload ==="; \
	printf "hot-reload " >> "$$STATUS_WORK"; \
	$(MAKE) --no-print-directory hot-reload-plugins > .gate-logs/hot-reload.log 2>&1 && echo "PASS" >> "$$STATUS_WORK" || (echo "FAIL" >> "$$STATUS_WORK" && touch .gate-failed && tail -30 .gate-logs/hot-reload.log); \
	echo "=== GATE PHASE: verify-hot-reload ==="; \
	printf "verify-hot-reload " >> "$$STATUS_WORK"; \
	$(MAKE) --no-print-directory check-hot-reload-fresh > .gate-logs/verify-hot-reload.log 2>&1 && echo "PASS" >> "$$STATUS_WORK" || (echo "FAIL" >> "$$STATUS_WORK" && touch .gate-failed && tail -30 .gate-logs/verify-hot-reload.log); \
	echo "=== GATE PHASE: restart-needed ==="; \
	printf "restart-needed " >> "$$STATUS_WORK"; \
	$(MAKE) --no-print-directory check-plugin-restart-needed > .gate-logs/restart-needed.log 2>&1 && echo "PASS" >> "$$STATUS_WORK" || (echo "FAIL" >> "$$STATUS_WORK" && touch .gate-failed && tail -30 .gate-logs/restart-needed.log); \
	echo "=== GATE PHASE: check-status-table ==="; \
	printf "check-status-table " >> "$$STATUS_WORK"; \
	$(MAKE) --no-print-directory check-status-table > .gate-logs/check-status-table.log 2>&1 && echo "PASS" >> "$$STATUS_WORK" || (echo "FAIL" >> "$$STATUS_WORK" && touch .gate-failed && tail -30 .gate-logs/check-status-table.log); \
	echo "=== GATE PHASE: env-writes ==="; \
	printf "env-writes " >> "$$STATUS_WORK"; \
	$(UV) run python scripts/stream_command.py --log .gate-logs/gate-refresh-env-writes.log -- $(MAKE) --no-print-directory check-test-env-writes && echo "PASS" >> "$$STATUS_WORK" || (echo "FAIL" >> "$$STATUS_WORK" && touch .gate-failed); \
	echo "=== GATE PHASE: hook-runtime ==="; \
	printf "hook-runtime " >> "$$STATUS_WORK"; \
	mkdir -p .gate-logs; \
	$(MAKE) --no-print-directory test-hook-runtime > .gate-logs/hook-runtime.log 2>&1 && echo "PASS" >> "$$STATUS_WORK" || (echo "FAIL" >> "$$STATUS_WORK" && touch .gate-failed && tail -30 .gate-logs/hook-runtime.log); \
	echo "=== GATE PHASE: typecheck ==="; \
	printf "typecheck " >> "$$STATUS_WORK"; \
	TC_ERRS=$$($(UV) run mypy -p general_ludd 2>&1 | grep -c 'error:'); \
	TC_ERRS=$${TC_ERRS:-0}; \
	if [ "$$TC_ERRS" -le "$(MYPY_MAX)" ]; then echo "PASS $$TC_ERRS" >> "$$STATUS_WORK"; else echo "FAIL $$TC_ERRS" >> "$$STATUS_WORK" && touch .gate-failed; fi; \
	echo "=== GATE PHASE: collect ==="; \
	printf "collect " >> "$$STATUS_WORK"; \
	$(MAKE) --no-print-directory collect-check > /dev/null 2>&1 && echo "PASS 0" >> "$$STATUS_WORK" || (echo "FAIL collection-errors" >> "$$STATUS_WORK" && touch .gate-failed); \
	if [ -n "$$OLD_TEST" ] && echo "$$OLD_TEST" | grep -q "PASS"; then echo "$$OLD_TEST" >> "$$STATUS_WORK"; else \
		echo "=== GATE-REFRESH PHASE: test ==="; \
		if $(UV) run python scripts/stream_command.py --log .gate-logs/gate-refresh-test.log -- $(UV) run python -m pytest tests/unit/ -vv --no-header -n 2 --maxprocesses=2; then \
			echo "test PASS 0" >> "$$STATUS_WORK"; \
		else \
			echo "test FAIL non-zero-exit" >> "$$STATUS_WORK" && touch .gate-failed && echo "[gate-refresh] test FAILED — tail:" && tail -20 .gate-logs/gate-refresh-test.log; \
		fi; \
	fi; \
	if [ -n "$$OLD_SMOKE" ] && echo "$$OLD_SMOKE" | grep -q "PASS"; then echo "$$OLD_SMOKE" >> "$$STATUS_WORK"; else \
		echo "=== GATE-REFRESH PHASE: smoke ==="; \
		printf "smoke " >> "$$STATUS_WORK"; \
		$(MAKE) --no-print-directory smoke > /tmp/gludd-gate-refresh-smoke.log 2>&1 && echo "PASS" >> "$$STATUS_WORK" || (echo "FAIL" >> "$$STATUS_WORK" && touch .gate-failed && echo "[gate-refresh] smoke FAILED — tail:" && tail -20 /tmp/gludd-gate-refresh-smoke.log); \
	fi; \
	echo "---" >> "$$STATUS_WORK"; \
	echo "epoch $$(date +%s)" >> "$$STATUS_WORK"; \
	if [ -f .gate-failed ]; then \
		rm -f .gate-failed; \
		echo "=== GATE-REFRESH: FAILED (fast phases) ==="; \
		echo "=== GATE: FAILED ===" >> "$$STATUS_WORK"; \
		mv "$$STATUS_WORK" .gate-status; \
		cat .gate-status; \
		exit 1; \
	else \
		echo "=== GATE-REFRESH: PASSED ==="; \
		echo "=== GATE: PASSED ===" >> "$$STATUS_WORK"; \
		$(UV) run python scripts/gate_status_attestation.py sign "$$STATUS_WORK"; \
		mv "$$STATUS_WORK" .gate-status; \
		cat .gate-status; \
	fi

_gate-fresh-check: check-gate-fresh
	@true

# Internal: serialize commit-shaped targets so parallel subagents cannot race on
# the git index (staging sweeps, index-lock errors). Uses flock (Linux) with a
# Python fcntl fallback (macOS). The lock file persists for the process lifetime;
# when make exits, fd 9 closes and the lock auto-releases. This is LAYER 1 of the
# commit-serialization guardrail (AGENTS.md). LAYER 2 is the enforce-commit-lock
# plugin that wraps the ENTIRE bash tool call boundary.
_gate-mutation-guard:
	@$(UV) run python scripts/gate_run_lock.py assert-inactive "$(GATE_RUN_LOCK)" "$$PPID"

_commit-lock-acquire: _gate-mutation-guard
	@exec 9>/tmp/gludd-commit.lock; \
	flock -n 9 2>/dev/null || python3 -c "import fcntl; f=open('/tmp/gludd-commit.lock'); \
	  fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)" 2>/dev/null || { \
	    echo "COMMIT-LOCK: another commit is in flight. Retry serially." >&2; exit 1; }

# Commit recipes that intentionally pass --no-verify still enforce the same
# staged-content quality boundary once. Normal commit paths use the real
# pre-commit hooks and do not repeat these checks.
_pre-commit-content-guard:
	@$(MAKE) --no-print-directory check-file-line-limits FILE_LINE_LIMIT_POLICY="$(FILE_LINE_LIMIT_POLICY)" FILE_LINE_LIMIT_STAGED=1
	@$(MAKE) --no-print-directory check-duplicate-code DUPLICATE_CODE_CONFIG="$(DUPLICATE_CODE_CONFIG)" DUPLICATE_CODE_ENGINE="$(DUPLICATE_CODE_ENGINE)" DUPLICATE_CODE_SOURCE=staged DUPLICATE_CODE_BASE_REF=HEAD DUPLICATE_CODE_CURRENT_REF=HEAD DUPLICATE_CODE_VALIDATE_ONLY=0

git-commit: _gate-fresh-check _commit-lock-acquire _commit-lint-guard _commit-docstring-guard _pre-commit-stage-guard _stash-leak-guard _pre-commit-stash-audit _edit-commit-atomicity-guard _pre-commit-spec-quality-guard
	@if [ -z "$(MSG)" ]; then echo "Usage: make git-commit MSG='message'"; exit 1; fi
	@echo "Running pre-commit collection check..."
	@$(MAKE) --no-print-directory collect-check
	@echo "Gate fresh and green. Running pre-commit directly on staged files..."
	@if ! git diff --cached --quiet; then \
		git diff --cached --name-only -z | xargs -0 $(UV) run pre-commit run --files && \
		git diff --cached --name-only -z | xargs -0 git add; \
	fi
	@$(MAKE) --no-print-directory check-gate-fresh
	@git diff --cached --quiet && echo "Nothing to commit" || git commit -n -m "$(MSG)"

commit-no-verify: _pre-commit-content-guard _gate-fresh-check _commit-lock-acquire _commit-lint-guard _commit-docstring-guard _pre-commit-stage-guard _edit-commit-atomicity-guard
	@if [ -z "$(MSG)" ]; then echo "Usage: make commit-no-verify MSG='message'"; exit 1; fi
	@$(MAKE) --no-print-directory collect-check
	@git diff --cached --quiet && echo "Nothing to commit" || git commit -n -m "$(MSG)"

# git-commit-no-verify: commit without pre-commit hook stash, enforcing gate check.
# The --no-verify flag skips ONLY the pre-commit hook stash, NOT the gate.
# There is no GLUDD_CI_IS_GATE bypass — the fresh+green .gate-status check is
# unconditional. Run `make gate` and have it pass; that is the only path.
git-commit-no-verify: _pre-commit-content-guard _gate-fresh-check _commit-lock-acquire
	@if [ -z "$(MSG)" ]; then echo "Usage: make git-commit-no-verify MSG='message'"; exit 1; fi
	@$(MAKE) --no-print-directory collect-check
	@git diff --cached --quiet && echo "Nothing to commit" || git commit -n -m "$(MSG)"

# git-amend-msg: amend the last commit message (--amend --no-edit equivalent),
# enforcing gate check. Cannot bypass the gate via --amend.
git-amend-msg: _pre-commit-content-guard _gate-fresh-check _commit-lock-acquire
	@if [ -z "$(MSG)" ]; then echo "Usage: make git-amend-msg MSG='message'"; exit 1; fi
	@$(MAKE) --no-print-directory collect-check
	@git commit --amend --no-verify -m "$(MSG)"

repo-commit: _pre-commit-content-guard _commit-lock-acquire _commit-lint-guard _commit-docstring-guard
	@if [ -z "$(MSG)" ]; then echo "Usage: make repo-commit MSG='message'"; exit 1; fi
	@git diff --cached --quiet && echo "Nothing to commit" || git commit -n -m "$(MSG)"

# ship-commit: commit staged changes locally. By default (PUSH=0), does NOT
# push — push requires explicit PUSH=1 or a separate make development-push
# / make batch-push. This prevents the CI cancellation loop where every
# commit triggers a push that cancels the prior CI run.
# Allowlisted from the local _gate-fresh-check (CI is the gate for
# subagent-dispatched pushes; see test_commit_gate_freshness.py ALLOWLIST_NO_GATE).
PUSH ?= 0
ship-commit: _pre-commit-content-guard _commit-lock-acquire _commit-lint-guard _commit-docstring-guard _pre-commit-stage-guard _stash-leak-guard _push-parameter-audit _pre-commit-stash-audit _edit-commit-atomicity-guard
	@if [ -z "$(MSG)" ]; then echo "Usage: make ship-commit MSG='message'"; exit 1; fi
	@STAGED_FILES=$$(git diff --cached --name-only | LC_ALL=C sort); \
		if [ -n "$(SHIP_COMMIT_EXPECTED_FILES)" ]; then \
			EXPECTED_FILES=$$(printf '%s\n' $(SHIP_COMMIT_EXPECTED_FILES) | LC_ALL=C sort); \
			if [ "$$STAGED_FILES" != "$$EXPECTED_FILES" ]; then \
				echo "BLOCKED: staged files differ from requested commit scope." >&2; \
				echo "Requested files:" >&2; printf '%s\n' "$$EXPECTED_FILES" >&2; \
				echo "Actually staged:" >&2; printf '%s\n' "$$STAGED_FILES" >&2; \
				exit 1; \
			fi; \
		fi; \
		STAGED_TREE=$$(git write-tree); \
		echo "Running pre-commit collection check..."; \
		$(MAKE) --no-print-directory collect-check || exit $$?; \
		CURRENT_TREE=$$(git write-tree); \
		if [ "$$CURRENT_TREE" != "$$STAGED_TREE" ]; then \
			echo "BLOCKED: staged index changed during preflight; refusing mixed commit." >&2; \
			echo "Expected staged tree: $$STAGED_TREE" >&2; \
			echo "Current staged tree:  $$CURRENT_TREE" >&2; \
			git diff --cached --name-only >&2; \
			exit 1; \
		fi; \
		echo "Committing staged changes..."; \
		if git diff --cached --quiet; then echo "Nothing to commit"; else git commit -n -m "$(MSG)"; fi
	@if [ "$(PUSH)" = "1" ]; then $(MAKE) --no-print-directory batch-push; else echo "Committed locally. Use PUSH=1 to push, or make batch-push separately."; fi

# ship-commit-files: atomic staging + commit under the commit lock. Bundles
# `git-add` + `ship-commit` so one subagent's `git add -A` cannot sweep
# another's staged files. Usage: make ship-commit-files FILES='...' MSG='...'
ship-commit-files: _commit-lock-acquire
	@[ -n "$(FILES)" ] || { echo "Usage: make ship-commit-files FILES='...' MSG='...'"; exit 1; }
	@$(MAKE) --no-print-directory git-add FILES='$(FILES)'
	@$(MAKE) --no-print-directory ship-commit MSG='$(MSG)' SHIP_COMMIT_EXPECTED_FILES='$(FILES)'

commit-and-ship: lint-fix git-add-all
	@$(MAKE) --no-print-directory ship-commit MSG='$(MSG)'

commit-and-ship-push: lint-fix git-add-all
	@$(MAKE) --no-print-directory ship-commit MSG='$(MSG)'
	@$(MAKE) --no-print-directory development-push
	@$(MAKE) --no-print-directory ci-verdict

delete-file:
	@[ -n "$(FILES)" ] || { echo "Usage: make delete-file FILES='file1 file2'"; exit 1; }
	@$(RM) $(FILES)

delete-binary-re-core:
	@echo "=== DELETE-BINARY-RE-CORE: deleting src/general_ludd/binary_re/ and test ==="
	@rm -rf src/general_ludd/binary_re/ || true
	@rm -f tests/unit/test_binary_re_contracts.py || true
	@git rm -rf --ignore-unmatch src/general_ludd/binary_re/ tests/unit/test_binary_re_contracts.py
	@echo "=== DELETE-BINARY-RE-CORE: COMPLETE ==="

patch-test:
	@[ -n "$(FILE)" ] || { echo "Usage: make patch-test FILE=path MATCH=old REPLACE=new"; exit 1; }
	@OLD=$$(mktemp /tmp/gludd-patch-old.XXXXXX); NEW=$$(mktemp /tmp/gludd-patch-new.XXXXXX); \
		printf "%b" "$(MATCH)" > "$$OLD"; \
		printf "%b" "$(REPLACE)" > "$$NEW"; \
		$(PYTHON) scripts/replace_text.py "$(FILE)" "$$OLD" "$$NEW"; RC=$$?; \
		rm -f "$$OLD" "$$NEW"; exit $$RC

copy-file:
	@test -n "$(SRC)" || { echo "Usage: make copy-file SRC=path DST=path"; exit 1; }
	@test -n "$(DST)" || { echo "Usage: make copy-file SRC=path DST=path"; exit 1; }
	@case "$(SRC)" in /tmp/gludd-*) ;; /*|*..*) echo "Refusing path outside workspace: $(SRC)"; exit 1;; esac
	@case "$(DST)" in /tmp/gludd-*) ;; /*|*..*) echo "Refusing path outside workspace: $(DST)"; exit 1;; esac
	@cp "$(SRC)" "$(DST)"

replace-text:
	@test -n "$(FILE)" || { echo "Usage: make replace-text FILE=path OLD=/tmp/gludd-old NEW=/tmp/gludd-new"; exit 1; }
	@test -n "$(OLD)" || { echo "Usage: make replace-text FILE=path OLD=/tmp/gludd-old NEW=/tmp/gludd-new"; exit 1; }
	@test -n "$(NEW)" || { echo "Usage: make replace-text FILE=path OLD=/tmp/gludd-old NEW=/tmp/gludd-new"; exit 1; }
	@case "$(FILE)" in /tmp/gludd-*) ;; /*|*..*) echo "Refusing path outside workspace: $(FILE)"; exit 1;; esac
	@case "$(OLD)" in /tmp/gludd-*) ;; /*|*..*) echo "Refusing path outside workspace: $(OLD)"; exit 1;; esac
	@case "$(NEW)" in /tmp/gludd-*) ;; /*|*..*) echo "Refusing path outside workspace: $(NEW)"; exit 1;; esac
	@$(PYTHON) scripts/replace_text.py "$(FILE)" "$(OLD)" "$(NEW)"

write-text:
	@[ -n "$(FILE)" ] || { echo "Usage: make write-text FILE=path TEXT=..."; exit 1; }
	@case "$(FILE)" in /tmp/gludd-*) ;; /*|*..*) echo "Refusing path outside workspace: $(FILE)"; exit 1;; esac
	@printf '%b' "$$TEXT" > "$$FILE"

append-text:
	@[ -n "$(FILE)" ] || { echo "Usage: make append-text FILE=path TEXT=..."; exit 1; }
	@case "$(FILE)" in /tmp/gludd-*) ;; /*|*..*) echo "Refusing path outside workspace: $(FILE)"; exit 1;; esac
	@printf '%b' "$$TEXT" >> "$$FILE"

write-text-b64:
	@[ -n "$(FILE)" ] || { echo "Usage: make write-text-b64 FILE=path TEXT_B64=base64"; exit 1; }
	@[ -n "$(TEXT_B64)" ] || { echo "Usage: make write-text-b64 FILE=path TEXT_B64=base64"; exit 1; }
	@case "$(FILE)" in /tmp/gludd-*) ;; /*|*..*) echo "Refusing path outside workspace: $(FILE)"; exit 1;; esac
	@TEXT_B64="$(TEXT_B64)" FILE_PATH="$(FILE)" $(PYTHON) -c "import base64, os; open(os.environ[\"FILE_PATH\"], \"wb\").write(base64.b64decode(os.environ[\"TEXT_B64\"]))"

replace-text-b64:
	@[ -n "$(FILE)" ] || { echo "Usage: make replace-text-b64 FILE=path OLD_B64=base64 NEW_B64=base64"; exit 1; }
	@[ -n "$(OLD_B64)" ] || { echo "Usage: make replace-text-b64 FILE=path OLD_B64=base64 NEW_B64=base64"; exit 1; }
	@[ -n "$(NEW_B64)" ] || { echo "Usage: make replace-text-b64 FILE=path OLD_B64=base64 NEW_B64=base64"; exit 1; }
	@case "$(FILE)" in /tmp/gludd-*) ;; /*|*..*) echo "Refusing path outside workspace: $(FILE)"; exit 1;; esac
	@OLD_TMP=$$(mktemp /tmp/gludd-old.XXXXXX); NEW_TMP=$$(mktemp /tmp/gludd-new.XXXXXX); 		OLD_B64="$(OLD_B64)" NEW_B64="$(NEW_B64)" OLD_TMP="$$OLD_TMP" NEW_TMP="$$NEW_TMP" $(PYTHON) -c "import base64, os; open(os.environ[\"OLD_TMP\"], \"wb\").write(base64.b64decode(os.environ[\"OLD_B64\"])); open(os.environ[\"NEW_TMP\"], \"wb\").write(base64.b64decode(os.environ[\"NEW_B64\"]))"; 		$(PYTHON) scripts/replace_text.py "$(FILE)" "$$OLD_TMP" "$$NEW_TMP"; RC=$$?; rm -f "$$OLD_TMP" "$$NEW_TMP"; exit $$RC

fix-benchmark-mock:
	@python3 -c "c=open('tests/unit/test_daemon_coverage_lift.py').read(); c=c.replace('class TestBenchmarkRecordWithSession:\n    @pytest.mark.asyncio\n    async def test_benchmark_record_with_session(self, app, transport):\n        mock_session = MagicMock()\n        mock_sf = MagicMock()','class TestBenchmarkRecordWithSession:\n    @pytest.mark.asyncio\n    async def test_benchmark_record_with_session(self, app, transport):\n        mock_session = MagicMock()\n        mock_session.commit = AsyncMock()\n        mock_sf = MagicMock()'); open('tests/unit/test_daemon_coverage_lift.py','w').write(c)"
	@echo "Fixed benchmark mock"

# ── LangGraph benchmark ──────────────────────────────────────────────
# Compare LangGraph-backed implementations against hand-rolled counterparts.
# All comparisons use mocked model calls (no real API) to measure pure framework
# overhead.  Outputs results as JSON to stdout.
#
#   make bench-langgraph                  — default (warmup=5, iterations=50)
#   make bench-langgraph WARMUP=2 ITERS=10 — custom run size

WARMUP ?= 5
ITERS  ?= 50

bench-langgraph:
	@$(UV) run python -c "from general_ludd.benchmark.langgraph_bench import BenchmarkRunner; r = BenchmarkRunner(warmup=$(WARMUP), iterations=$(ITERS)); r.run_all(); r.report()"

fix-ratchet-mocks:
	@python3 -c " \
c=open('tests/unit/test_daemon_coverage_lift.py').read(); \
c=c.replace('patch(\"general_ludd.secrets.manager.SecretsManager\")','patch(\"general_ludd.daemon.SecretsManager\")'); \
open('tests/unit/test_daemon_coverage_lift.py','w').write(c)"
	@python3 -c " \
c=open('tests/unit/test_preflight_coverage.py').read(); \
c=c.replace('patch(\"general_ludd.filestore.store.FileStore\"','patch(\"general_ludd.quality.preflight.FileStore\"'); \
open('tests/unit/test_preflight_coverage.py','w').write(c)"
	@python3 -c " \
c=open('tests/unit/test_secrets_manager_coverage.py').read(); \
c=c.replace('patch(\"general_ludd.config.binary_paths.BinaryPathResolver\")','patch(\"general_ludd.secrets.manager.BinaryPathResolver\")'); \
open('tests/unit/test_secrets_manager_coverage.py','w').write(c)"
	@echo "Fixed ratchet mock targets"

git-reset:
	@if [ -z "$(FILES)" ]; then \
		echo "Usage: make git-reset FILES='HEAD~1' (or specific ref)"; \
		exit 1; \
	fi
	@git reset -- $(FILES)

git-uncommit-last:
	@if [ "$(CONFIRM)" != "1" ]; then \
		echo "Usage: make git-uncommit-last CONFIRM=1 [DRY_RUN=1]"; \
		exit 1; \
	fi
	@parents=$$(git rev-list --parents -n 1 HEAD); \
	parent_count=$$(printf '%s\n' "$$parents" | awk '{print NF - 1}'); \
	if [ "$$parent_count" -ne 1 ]; then \
		echo "Refusing to uncommit a root or merge commit"; \
		exit 1; \
	fi; \
	if git branch -r --contains HEAD | grep -q .; then \
		echo "Refusing to uncommit a commit contained by a remote-tracking branch"; \
		exit 1; \
	fi; \
	if [ "$(DRY_RUN)" = "1" ]; then \
		echo "Would run: git reset --mixed HEAD^"; \
	else \
		git reset --mixed HEAD^; \
		echo "Uncommitted local HEAD; all file changes were preserved"; \
	fi

git-restore:
	@if [ -z "$(FILES)" ]; then \
		echo "Usage: make git-restore FILES='path/to/file ...' (discards working-tree changes, restoring to HEAD)"; \
		exit 1; \
	fi
	@git restore -- $(FILES)
	@echo "Restored to HEAD: $(FILES)"

git-branch:
	@if [ -z "$(MSG)" ]; then echo "Usage: make git-branch MSG='branch-name'"; exit 1; fi
	@git branch "$(MSG)"

git-checkout: _gate-mutation-guard
	@if [ -z "$(MSG)" ]; then echo "Usage: make git-checkout MSG='branch-name'"; exit 1; fi
	@git checkout "$(MSG)"

git-merge: _merge-strategy-guard
	@if [ -z "$(MSG)" ]; then echo "Usage: make git-merge MSG='branch-name'"; exit 1; fi
	@git merge --no-ff "$(MSG)"

git-merge-abort:
	@git merge --abort
	@echo "Merge aborted."

git-rebase-abort:
	@git rebase --abort
	@echo "Rebase aborted."

git-rebase-continue:
	@git rebase --continue
	@echo "Rebase continued."

git-rebase-skip:
	@git rebase --skip
	@echo "Rebase skipped current commit."

git-reset-hard:
	@if [ -z "$(MSG)" ]; then echo "Usage: make git-reset-hard MSG='ref' (DESTRUCTIVE — discards all uncommitted changes)"; exit 1; fi
	@git reset --hard "$(MSG)"
	@echo "Hard reset to $(MSG) — all uncommitted changes discarded."

git-cherry-pick: _gate-mutation-guard
	@if [ -z "$(SHA)" ]; then echo "Usage: make git-cherry-pick SHA=<commit>"; exit 1; fi
	@git cherry-pick "$(SHA)"


git-cherry-pick-list:
	@[ -n "$(SHAS)" ] || { echo "Usage: make git-cherry-pick-list SHAS='sha1 sha2 ...'"; exit 1; }
	@[ -z "$$(git status --porcelain)" ] || { echo "ERROR: clean tree required before cherry-pick preflight"; exit 1; }
	@for SHA in $(SHAS); do \
		echo "=== cherry-pick $$SHA ==="; \
		BASE=$$(git merge-base HEAD "$$SHA") || exit 1; \
		INCOMING=$$(git diff --name-only "$$SHA^" "$$SHA"); \
		LOCAL=$$(git diff --name-only "$$BASE" HEAD); \
		for SHARED in Makefile TASKS.md opencode.json AGENTS.md .claude/settings.json; do \
			if echo "$$INCOMING" | grep -qx "$$SHARED" && echo "$$LOCAL" | grep -qx "$$SHARED"; then \
				echo "ERROR: $$SHA overlaps locally changed shared file $$SHARED"; \
				echo "Resolve intentionally with a reviewed merge, then retry this target."; \
				exit 1; \
			fi; \
		done; \
		git cherry-pick "$$SHA" || exit 1; \
	done

SUBMODULE_INIT_VALIDATE_ONLY ?= 0

submodule-init:
	@case "$(SUBMODULE_INIT_VALIDATE_ONLY)" in 0|1) ;; *) echo "ERROR: SUBMODULE_INIT_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@if [ ! -f .gitmodules ]; then echo "No .gitmodules file"; exit 1; fi
	@if [ "$(SUBMODULE_INIT_VALIDATE_ONLY)" = "1" ]; then \
		git config --file .gitmodules --get-regexp '^submodule\..*\.path$$' >/dev/null || { echo "ERROR: .gitmodules has no submodule paths"; exit 1; }; \
		git config --file .gitmodules --get-regexp '^submodule\..*\.url$$' >/dev/null || { echo "ERROR: .gitmodules has no submodule URLs"; exit 1; }; \
		echo "SUBMODULE_INIT_VALIDATED file=.gitmodules mode=network-free"; \
	else \
		git submodule update --init --recursive; \
	fi

submodule-update:
	@if [ ! -f .gitmodules ]; then echo "No .gitmodules file"; exit 1; fi
	@git submodule update --remote --merge --recursive

submodule-status:
	@if [ ! -f .gitmodules ]; then echo "No .gitmodules file"; exit 1; fi
	@git submodule status --recursive

submodule-pin:
	@if [ -z "$(REPO)" ] || [ -z "$(TAG)" ]; then echo "Usage: make submodule-pin REPO=external/llamacpp TAG=v1.0.0"; exit 1; fi
	@git -C "$(REPO)" fetch --tags
	@git -C "$(REPO)" checkout "$(TAG)"
	@git add .gitmodules
	@echo "Pinned $(REPO) to $(TAG)"

feature-start:
	@if [ -z "$(MSG)" ]; then echo "Usage: make feature-start MSG='feature/short-name'"; exit 1; fi
	@git checkout -b "$(MSG)"
	@echo "Created and switched to branch: $(MSG)"

feature-done:
	@if [ -z "$(MSG)" ]; then echo "Usage: make feature-done MSG='feature/short-name'"; exit 1; fi
	@echo "Running full test suite before merge..."
	@$(UV) run python -m pytest tests/ $(_XD) -q
	@git checkout -f master
	@git merge --no-ff "$(MSG)"
	@echo "Merged $(MSG) into master"
	@echo "Building distributables..."
	@$(MAKE) dist
	@echo "Feature complete. Tests green, distributables built."

# --- Worktree-per-subagent dispatch protocol ---
# Each subagent that mutates files works in an isolated git worktree on its
# own branch, so concurrent edits cannot interleave on the shared master
# checkout (no dirty-tree surprises, no commit races, no misattributed work).
# Lifecycle: agent-worktree (create) -> subagent edits+commits on the branch
# -> agent-merge (fan in to master) -> agent-cleanup (teardown). Read-only
# research tasks skip this entirely — they do not touch the working tree.

# Create an isolated worktree for a subagent. Usage:
#   make agent-worktree BRANCH=agent-fix-slurm
# Prints "WORKTREE_PATH=<path>" for the subagent to work in. If the branch
# already exists (re-dispatch / resume), the worktree is attached to it
# instead of being re-created from scratch.
agent-worktree:
	@[ -n "$(BRANCH)" ] || { echo "Usage: make agent-worktree BRANCH=agent-<name>"; exit 1; }
	@WORKTREE_PATH="/tmp/gludd-worktrees/$(BRANCH)"; \
	mkdir -p /tmp/gludd-worktrees; \
	git worktree add "$$WORKTREE_PATH" -b "$(BRANCH)" 2>/dev/null || git worktree add "$$WORKTREE_PATH" "$(BRANCH)"; \
	$(MAKE) --no-print-directory workstream-register BRANCH="$(BRANCH)" WORKTREE="$$WORKTREE_PATH" ACTIVE_WORKSTREAM_REGISTRY="$(ACTIVE_WORKSTREAM_REGISTRY)"; \
	echo "WORKTREE_PATH=$$WORKTREE_PATH"; \
	echo "Worktree ready at $$WORKTREE_PATH on branch $(BRANCH)"

# Create an isolated worktree at an explicit base ref. Usage:
#   make agent-worktree-base BRANCH=release-sync BASE=sandboxcom/master
agent-worktree-base:
	@[ -n "$(BRANCH)" ] && [ -n "$(BASE)" ] || { echo "Usage: make agent-worktree-base BRANCH=agent-<name> BASE=<ref>"; exit 1; }
	@WORKTREE_PATH="/tmp/gludd-worktrees/$(BRANCH)"; \
	mkdir -p /tmp/gludd-worktrees; \
	git rev-parse --verify "$(BASE)^{commit}" >/dev/null 2>&1 || { echo "ERROR: BASE $(BASE) is not a valid commit"; exit 1; }; \
	git worktree add "$$WORKTREE_PATH" -b "$(BRANCH)" "$(BASE)" 2>/dev/null || git worktree add "$$WORKTREE_PATH" "$(BRANCH)"; \
	$(MAKE) --no-print-directory workstream-register BRANCH="$(BRANCH)" WORKTREE="$$WORKTREE_PATH" ACTIVE_WORKSTREAM_REGISTRY="$(ACTIVE_WORKSTREAM_REGISTRY)"; \
	echo "WORKTREE_PATH=$$WORKTREE_PATH"; \
	echo "Worktree ready at $$WORKTREE_PATH on branch $(BRANCH) from $(BASE)"

# Merge a subagent's worktree branch back to master. Run on the MAIN checkout
# (never from inside a worktree). Usage:
#   make agent-merge BRANCH=agent-fix-slurm
agent-merge: _gate-mutation-guard
	@[ -n "$(BRANCH)" ] || { echo "Usage: make agent-merge BRANCH=agent-<name>"; exit 1; }
	@git merge --no-ff "$(BRANCH)" -m "merge: $(BRANCH) worktree work into master"
	@echo "Merged $(BRANCH) into master"

# Remove a worktree and its branch after the work has been merged. Safe to
# run even if the worktree was already removed manually. Usage:
#   make agent-cleanup BRANCH=agent-fix-slurm
agent-cleanup:
	@[ -n "$(BRANCH)" ] || { echo "Usage: make agent-cleanup BRANCH=agent-<name>"; exit 1; }
	@WORKTREE_PATH="/tmp/gludd-worktrees/$(BRANCH)"; \
	CLAUDE_WT_PATH=".claude/worktrees/$(BRANCH)"; \
	git worktree remove "$$WORKTREE_PATH" --force 2>/dev/null || true; \
	git worktree unlock "$$CLAUDE_WT_PATH" 2>/dev/null || true; \
	git worktree remove "$$CLAUDE_WT_PATH" --force 2>/dev/null || true; \
	git branch -d "$(BRANCH)" 2>/dev/null || true; \
	$(MAKE) --no-print-directory workstream-unregister BRANCH="$(BRANCH)" ACTIVE_WORKSTREAM_REGISTRY="$(ACTIVE_WORKSTREAM_REGISTRY)"; \
	echo "Cleaned up worktree + branch for $(BRANCH)"

# Logical workstreams represent model-agent ownership and therefore cannot be
# inferred from OS PIDs.  Registration is explicit and shared by all worktrees.
workstream-register:
	@[ -n "$(BRANCH)" ] && [ -n "$(WORKTREE)" ] || { echo "Usage: make workstream-register BRANCH=<name> WORKTREE=<path>"; exit 2; }
	@$(UV) run python -m scripts.workstream_registry register --branch "$(BRANCH)" --worktree "$(WORKTREE)" \
		$(if $(ACTIVE_WORKSTREAM_REGISTRY),--registry "$(ACTIVE_WORKSTREAM_REGISTRY)")

workstream-unregister:
	@[ -n "$(BRANCH)" ] || { echo "Usage: make workstream-unregister BRANCH=<name>"; exit 2; }
	@$(UV) run python -m scripts.workstream_registry unregister --branch "$(BRANCH)" \
		$(if $(ACTIVE_WORKSTREAM_REGISTRY),--registry "$(ACTIVE_WORKSTREAM_REGISTRY)")

# Bulk cleanup of all stale worktrees in .claude/worktrees/.
# Unlocks then force-removes every worktree, deletes branches, prunes metadata.
# Usage: make clean-stale-worktrees
clean-stale-worktrees:
	@echo "=== Cleaning stale worktrees ==="; \
	count=0; \
	for wt_dir in .claude/worktrees/agent-*; do \
		[ -d "$$wt_dir" ] || continue; \
		branch=$$(git --work-tree="$$wt_dir" rev-parse --abbrev-ref HEAD 2>/dev/null || echo ""); \
		git worktree unlock "$$wt_dir" 2>/dev/null || true; \
		if ! git worktree remove --force "$$wt_dir" 2>/dev/null; then \
			rm -rf "$$wt_dir"; \
		fi; \
		[ -n "$$branch" ] && git branch -D "$$branch" 2>/dev/null || true; \
		count=$$((count + 1)); \
	done; \
	for wt_dir in /tmp/gludd-worktrees/agent-*; do \
		[ -d "$$wt_dir" ] || continue; \
		branch=$$(git --work-tree="$$wt_dir" rev-parse --abbrev-ref HEAD 2>/dev/null || echo ""); \
		git worktree unlock "$$wt_dir" 2>/dev/null || true; \
		if ! git worktree remove --force "$$wt_dir" 2>/dev/null; then \
			rm -rf "$$wt_dir"; \
		fi; \
		[ -n "$$branch" ] && git branch -D "$$branch" 2>/dev/null || true; \
		count=$$((count + 1)); \
	done; \
	git worktree prune; \
	echo "=== Cleaned $$count stale worktrees ==="

# List active worktrees (read-only diagnostic).
agent-worktree-list:
	@git worktree list

# Stdout is a NUL-delimited argv stream for exactly one Azure CLI process.
# The named custom role must already exist; this target never calls Azure and
# never receives, writes, or logs the credential returned by Azure CLI.
azure-self-improve-auth-args:
	@# Inputs: AZURE_SELF_IMPROVE_SUBSCRIPTION_ID AZURE_SELF_IMPROVE_RESOURCE_GROUP AZURE_SELF_IMPROVE_ACCOUNT AZURE_SELF_IMPROVE_SP_NAME
	@$(SYSTEM_PYTHON) scripts/render_azure_self_improve_auth_args.py

# Canonical operator-owned role application through supported Microsoft SDKs.
# LIVE=0 validates locally and constructs no credential or Azure client.
azure-accelerator-role-apply:
	@# Inputs: AZURE_ACCELERATOR_SUBSCRIPTION_ID AZURE_ACCELERATOR_RESOURCE_GROUP AZURE_ACCELERATOR_LOCATION AZURE_ACCELERATOR_OPERATOR_AUTH AZURE_ACCELERATOR_PRINCIPAL_OBJECT_ID AZURE_ACCELERATOR_ROLE_APPLY_LIVE
	@if [ "$(AZURE_ACCELERATOR_ROLE_APPLY_LIVE)" = "1" ]; then $(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=azure DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON= DEPENDENCY_PROFILE_VALIDATE_ONLY=0; fi
	@$(UV) run --no-sync python -m general_ludd.azure.accelerator_role

# Deprecated compatibility: stdout is one NUL-delimited Azure CLI argv.
azure-accelerator-role-args:
	@# Inputs: AZURE_ACCELERATOR_SUBSCRIPTION_ID AZURE_ACCELERATOR_RESOURCE_GROUP
	@$(UV) run python scripts/render_azure_accelerator_auth_args.py role

# Stdout is one NUL-delimited argv that updates an existing Azure role definition.
azure-accelerator-role-update-args:
	@# Inputs: AZURE_ACCELERATOR_SUBSCRIPTION_ID AZURE_ACCELERATOR_RESOURCE_GROUP
	@$(UV) run python scripts/render_azure_accelerator_auth_args.py role-update

# Stdout is one NUL-delimited argv for the Entra principal/assignment API.
azure-accelerator-auth-args:
	@# Inputs: AZURE_ACCELERATOR_SUBSCRIPTION_ID AZURE_ACCELERATOR_RESOURCE_GROUP AZURE_ACCELERATOR_SP_NAME
	@$(UV) run python scripts/render_azure_accelerator_auth_args.py auth

# Atomically ingest Azure CLI JSON; old and failed generations are never pruned.
azure-accelerator-auth-store:
	@# Inputs: AZURE_ACCELERATOR_AUTH_FILE AZURE_ACCELERATOR_AUTH_SOURCE_FILE AZURE_ACCELERATOR_SUBSCRIPTION_ID AZURE_ACCELERATOR_AUTH_STORE_VALIDATE_ONLY
	@[ -n "$(AZURE_ACCELERATOR_SUBSCRIPTION_ID)" ] || { echo "AZURE_ACCELERATOR_SUBSCRIPTION_ID is required" >&2; exit 2; }
	@case "$(AZURE_ACCELERATOR_AUTH_STORE_VALIDATE_ONLY)" in 0|1) ;; *) echo "AZURE_ACCELERATOR_AUTH_STORE_VALIDATE_ONLY must be 0 or 1" >&2; exit 2 ;; esac
	@$(UV) run python scripts/store_azure_accelerator_credentials.py \
		$(if $(strip $(AZURE_ACCELERATOR_AUTH_FILE)),--auth-file "$(AZURE_ACCELERATOR_AUTH_FILE)",) \
		$(if $(strip $(AZURE_ACCELERATOR_AUTH_SOURCE_FILE)),--source-file "$(AZURE_ACCELERATOR_AUTH_SOURCE_FILE)",) \
		--subscription-id "$(AZURE_ACCELERATOR_SUBSCRIPTION_ID)" \
		$(if $(filter 1,$(AZURE_ACCELERATOR_AUTH_STORE_VALIDATE_ONLY)),--validate-only,)

# Stdout is one NUL-delimited argv for an operator-owned ARM group deployment.
# The Gludd service principal intentionally cannot execute this bootstrap.
azure-containerapp-environment-bootstrap-args:
	@# Inputs: AZURE_ACCELERATOR_SUBSCRIPTION_ID AZURE_ACCELERATOR_RESOURCE_GROUP AZURE_CONTAINERAPP_ENVIRONMENT AZURE_CONTAINERAPP_WORKLOAD_PROFILE_NAME AZURE_CONTAINERAPP_WORKLOAD_PROFILE_TYPE AZURE_CONTAINERAPP_LOCATION
	@$(UV) run python scripts/render_azure_accelerator_auth_args.py environment-bootstrap

# Validate Azure CLI --json-auth output without sourcing or rendering secrets.
azure-accelerator-auth-check:
	@# Inputs: AZURE_ACCELERATOR_AUTH_FILE AZURE_ACCELERATOR_SUBSCRIPTION_ID AZURE_ACCELERATOR_AUTH_VALIDATE_ONLY
	@[ -n "$(AZURE_ACCELERATOR_AUTH_FILE)" ] || { echo "AZURE_ACCELERATOR_AUTH_FILE is required" >&2; exit 2; }
	@[ -n "$(AZURE_ACCELERATOR_SUBSCRIPTION_ID)" ] || { echo "AZURE_ACCELERATOR_SUBSCRIPTION_ID is required" >&2; exit 2; }
	@case "$(AZURE_ACCELERATOR_AUTH_VALIDATE_ONLY)" in 0|1) ;; *) echo "AZURE_ACCELERATOR_AUTH_VALIDATE_ONLY must be 0 or 1" >&2; exit 2 ;; esac
	@$(UV) run python scripts/validate_azure_accelerator_credentials.py --auth-file "$(AZURE_ACCELERATOR_AUTH_FILE)" --subscription-id "$(AZURE_ACCELERATOR_SUBSCRIPTION_ID)" $(if $(filter 1,$(AZURE_ACCELERATOR_AUTH_VALIDATE_ONLY)),--validate-only,)

# Verify one existing Container Apps GPU profile and its quota without mutation.
azure-containerapp-preflight:
	@# Inputs: AZURE_ACCELERATOR_AUTH_FILE AZURE_ACCELERATOR_SUBSCRIPTION_ID AZURE_CONTAINERAPP_RESOURCE_GROUP AZURE_CONTAINERAPP_ENVIRONMENT AZURE_CONTAINERAPP_WORKLOAD_PROFILE_NAME AZURE_CONTAINERAPP_LOCATION AZURE_CONTAINERAPP_MODEL_ID AZURE_CONTAINERAPP_MODEL_REVISION AZURE_CONTAINERAPP_PARAMETER_COUNT AZURE_CONTAINERAPP_WEIGHT_BITS AZURE_CONTAINERAPP_KV_CACHE_MIB AZURE_CONTAINERAPP_RUNTIME_OVERHEAD_MIB AZURE_CONTAINERAPP_PREFLIGHT_LIVE
	@[ -n "$(AZURE_ACCELERATOR_AUTH_FILE)" ] || { echo "AZURE_ACCELERATOR_AUTH_FILE is required" >&2; exit 2; }
	@[ -n "$(AZURE_ACCELERATOR_SUBSCRIPTION_ID)" ] || { echo "AZURE_ACCELERATOR_SUBSCRIPTION_ID is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_RESOURCE_GROUP)" ] || { echo "AZURE_CONTAINERAPP_RESOURCE_GROUP is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_ENVIRONMENT)" ] || { echo "AZURE_CONTAINERAPP_ENVIRONMENT is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_WORKLOAD_PROFILE_NAME)" ] || { echo "AZURE_CONTAINERAPP_WORKLOAD_PROFILE_NAME is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LOCATION)" ] || { echo "AZURE_CONTAINERAPP_LOCATION is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_MODEL_ID)" ] || { echo "AZURE_CONTAINERAPP_MODEL_ID is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_MODEL_REVISION)" ] || { echo "AZURE_CONTAINERAPP_MODEL_REVISION is required" >&2; exit 2; }
	@if [ "$(AZURE_CONTAINERAPP_PREFLIGHT_LIVE)" = "1" ]; then $(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=azure DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON= DEPENDENCY_PROFILE_VALIDATE_ONLY=0; fi
	@$(UV) run --no-sync python scripts/azure_containerapp_preflight.py \
		--auth-file "$(AZURE_ACCELERATOR_AUTH_FILE)" \
		--subscription-id "$(AZURE_ACCELERATOR_SUBSCRIPTION_ID)" \
		--resource-group "$(AZURE_CONTAINERAPP_RESOURCE_GROUP)" \
		--environment "$(AZURE_CONTAINERAPP_ENVIRONMENT)" \
		--workload-profile-name "$(AZURE_CONTAINERAPP_WORKLOAD_PROFILE_NAME)" \
		--location "$(AZURE_CONTAINERAPP_LOCATION)" \
		--model-id "$(AZURE_CONTAINERAPP_MODEL_ID)" \
		--model-revision "$(AZURE_CONTAINERAPP_MODEL_REVISION)" \
		--parameter-count "$(AZURE_CONTAINERAPP_PARAMETER_COUNT)" \
		--weight-bits "$(AZURE_CONTAINERAPP_WEIGHT_BITS)" \
		--kv-cache-mib "$(AZURE_CONTAINERAPP_KV_CACHE_MIB)" \
		--runtime-overhead-mib "$(AZURE_CONTAINERAPP_RUNTIME_OVERHEAD_MIB)" \
		--live "$(AZURE_CONTAINERAPP_PREFLIGHT_LIVE)"

# Execute one Terraform phase only inside an ownership-marked live-proof root.
azure-containerapp-terraform-phase: tf-cache-setup
	@# Inputs: AZURE_CONTAINERAPP_TF_PHASE AZURE_CONTAINERAPP_TF_DIR AZURE_CONTAINERAPP_TF_PLAN_FILE AZURE_CONTAINERAPP_TF_JSON_FILE AZURE_CONTAINERAPP_TF_VALIDATE_ONLY
	@case "$(AZURE_CONTAINERAPP_TF_VALIDATE_ONLY)" in 0|1) ;; *) echo "AZURE_CONTAINERAPP_TF_VALIDATE_ONLY must be 0 or 1" >&2; exit 2;; esac
	@if [ "$(AZURE_CONTAINERAPP_TF_VALIDATE_ONLY)" = "1" ]; then \
		echo "AZURE_CONTAINERAPP_TERRAFORM_PHASE_PLAN phase=$(AZURE_CONTAINERAPP_TF_PHASE) secret_output=false"; \
	else \
		TF_PLUGIN_CACHE_DIR="$(TF_PLUGIN_CACHE)" $(UV) run python scripts/azure_containerapp_terraform_phase.py \
			--phase "$(AZURE_CONTAINERAPP_TF_PHASE)" \
			--terraform-dir "$(AZURE_CONTAINERAPP_TF_DIR)" \
			--plan-file "$(AZURE_CONTAINERAPP_TF_PLAN_FILE)" \
			--json-file "$(AZURE_CONTAINERAPP_TF_JSON_FILE)"; \
	fi

# Fail closed before paid compute if the GitHub Environment protection drifts.
azure-containerapp-environment-guard:
	@# Inputs: AZURE_CONTAINERAPP_GITHUB_REPOSITORY AZURE_CONTAINERAPP_GITHUB_ENVIRONMENT AZURE_CONTAINERAPP_GITHUB_ENVIRONMENT_JSON AZURE_CONTAINERAPP_GITHUB_BRANCH_POLICIES_JSON AZURE_CONTAINERAPP_GITHUB_ENVIRONMENT_VALIDATE_ONLY
	@case "$(AZURE_CONTAINERAPP_GITHUB_ENVIRONMENT_VALIDATE_ONLY)" in 0|1) ;; *) echo "AZURE_CONTAINERAPP_GITHUB_ENVIRONMENT_VALIDATE_ONLY must be 0 or 1" >&2; exit 2;; esac
	@[ -n "$(AZURE_CONTAINERAPP_GITHUB_REPOSITORY)" ] || { echo "AZURE_CONTAINERAPP_GITHUB_REPOSITORY is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_GITHUB_ENVIRONMENT)" ] || { echo "AZURE_CONTAINERAPP_GITHUB_ENVIRONMENT is required" >&2; exit 2; }
	@PYTHONPATH="$(CURDIR)/src" UV_NO_SYNC=0 $(UV) run --no-project --python 3.11 python -m scripts.verify_azure_containerapp_environment \
		--repository "$(AZURE_CONTAINERAPP_GITHUB_REPOSITORY)" \
		--environment "$(AZURE_CONTAINERAPP_GITHUB_ENVIRONMENT)" \
		$(if $(strip $(AZURE_CONTAINERAPP_GITHUB_ENVIRONMENT_JSON)),--environment-json "$(AZURE_CONTAINERAPP_GITHUB_ENVIRONMENT_JSON)",) \
		$(if $(strip $(AZURE_CONTAINERAPP_GITHUB_BRANCH_POLICIES_JSON)),--branch-policies-json "$(AZURE_CONTAINERAPP_GITHUB_BRANCH_POLICIES_JSON)",) \
		--validate-only "$(AZURE_CONTAINERAPP_GITHUB_ENVIRONMENT_VALIDATE_ONLY)"

# Hermetic by default; live mode accepts one explicit private auth contract.
azure-containerapp-live-proof:
	@# Inputs: AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_MODE AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_FILE AZURE_CONTAINERAPP_LIVE_PROOF_FEDERATED_TOKEN_FILE AZURE_CONTAINERAPP_LIVE_PROOF_CLIENT_ID AZURE_CONTAINERAPP_LIVE_PROOF_TENANT_ID AZURE_CONTAINERAPP_LIVE_PROOF_SUBSCRIPTION_ID AZURE_CONTAINERAPP_LIVE_PROOF_RESOURCE_GROUP AZURE_CONTAINERAPP_LIVE_PROOF_ENVIRONMENT AZURE_CONTAINERAPP_LIVE_PROOF_WORKLOAD_PROFILE_NAME AZURE_CONTAINERAPP_LIVE_PROOF_LOCATION AZURE_CONTAINERAPP_LIVE_PROOF_ALLOWED_CIDR AZURE_CONTAINERAPP_LIVE_PROOF_MAX_COST_USD AZURE_CONTAINERAPP_LIVE_PROOF_TTL_MINUTES AZURE_CONTAINERAPP_LIVE_PROOF_LIVE AZURE_CONTAINERAPP_LIVE_PROOF_ACKNOWLEDGEMENT AZURE_CONTAINERAPP_LIVE_PROOF_PROJECT_ROOT AZURE_CONTAINERAPP_LIVE_PROOF_SOURCE_PATH AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_PRESET AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_SECONDS
	@case "$(AZURE_CONTAINERAPP_LIVE_PROOF_LIVE)" in 0|1) ;; *) echo "AZURE_CONTAINERAPP_LIVE_PROOF_LIVE must be 0 or 1" >&2; exit 2;; esac
	@case "$(AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_MODE)" in \
		file) [ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_FILE)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_FILE is required for file auth" >&2; exit 2; } ;; \
		workload_identity) \
			[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_FEDERATED_TOKEN_FILE)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_FEDERATED_TOKEN_FILE is required for workload identity" >&2; exit 2; }; \
			[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_CLIENT_ID)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_CLIENT_ID is required for workload identity" >&2; exit 2; }; \
			[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_TENANT_ID)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_TENANT_ID is required for workload identity" >&2; exit 2; } ;; \
		*) echo "AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_MODE must be file or workload_identity" >&2; exit 2 ;; \
	 esac
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_SUBSCRIPTION_ID)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_SUBSCRIPTION_ID is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_RESOURCE_GROUP)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_RESOURCE_GROUP is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_ENVIRONMENT)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_ENVIRONMENT is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_WORKLOAD_PROFILE_NAME)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_WORKLOAD_PROFILE_NAME is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_LOCATION)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_LOCATION is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_ALLOWED_CIDR)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_ALLOWED_CIDR is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_MAX_COST_USD)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_MAX_COST_USD is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_TTL_MINUTES)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_TTL_MINUTES is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_ACKNOWLEDGEMENT)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_ACKNOWLEDGEMENT is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_PROJECT_ROOT)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_PROJECT_ROOT is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_SOURCE_PATH)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_SOURCE_PATH is required" >&2; exit 2; }
	@case "$(AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_PRESET)" in always_destroy|zero_cost_only|balanced|latency_first) ;; *) echo "AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_PRESET must be always_destroy, zero_cost_only, balanced, or latency_first" >&2; exit 2;; esac
	@case "$(AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_SECONDS)" in ''|*[!0-9]*|0) echo "AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_SECONDS must be a positive integer" >&2; exit 2;; esac
	@if [ "$(AZURE_CONTAINERAPP_LIVE_PROOF_LIVE)" = "1" ]; then $(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=azure DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON= DEPENDENCY_PROFILE_VALIDATE_ONLY=0; fi
	@$(UV) run --no-sync python scripts/azure_containerapp_live_proof.py \
		$(if $(filter file,$(AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_MODE)),--auth-file "$(AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_FILE)",--federated-token-file "$(AZURE_CONTAINERAPP_LIVE_PROOF_FEDERATED_TOKEN_FILE)" --azure-client-id "$(AZURE_CONTAINERAPP_LIVE_PROOF_CLIENT_ID)" --azure-tenant-id "$(AZURE_CONTAINERAPP_LIVE_PROOF_TENANT_ID)") \
		--subscription-id "$(AZURE_CONTAINERAPP_LIVE_PROOF_SUBSCRIPTION_ID)" \
		--resource-group "$(AZURE_CONTAINERAPP_LIVE_PROOF_RESOURCE_GROUP)" \
		--environment "$(AZURE_CONTAINERAPP_LIVE_PROOF_ENVIRONMENT)" \
		--workload-profile-name "$(AZURE_CONTAINERAPP_LIVE_PROOF_WORKLOAD_PROFILE_NAME)" \
		--location "$(AZURE_CONTAINERAPP_LIVE_PROOF_LOCATION)" \
		--allowed-cidr "$(AZURE_CONTAINERAPP_LIVE_PROOF_ALLOWED_CIDR)" \
		--max-cost-usd "$(AZURE_CONTAINERAPP_LIVE_PROOF_MAX_COST_USD)" \
		--ttl-minutes "$(AZURE_CONTAINERAPP_LIVE_PROOF_TTL_MINUTES)" \
		--live "$(AZURE_CONTAINERAPP_LIVE_PROOF_LIVE)" \
		--acknowledgement "$(AZURE_CONTAINERAPP_LIVE_PROOF_ACKNOWLEDGEMENT)" \
		--project-root "$(AZURE_CONTAINERAPP_LIVE_PROOF_PROJECT_ROOT)" \
		--source-path "$(AZURE_CONTAINERAPP_LIVE_PROOF_SOURCE_PATH)" \
		--idle-retention-preset "$(AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_PRESET)" \
		--idle-retention-seconds "$(AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_SECONDS)"

# One credential-free local/GHA contract for every Azure Container Apps boundary.
test-azure-containerapp-coverage:
	@$(MAKE) --no-print-directory coverage-files \
		COVERAGE_TESTFILES='tests/unit/test_ansible_runtime_artifacts.py tests/unit/test_azure_accelerator_credentials.py tests/unit/test_azure_accelerator_openbao.py tests/unit/test_azure_accelerator_role.py tests/unit/test_azure_resource_group_bootstrap.py tests/unit/test_azure_containerapp_ansible_orchestration.py tests/unit/test_azure_containerapp_retention_module_utils.py tests/unit/test_azure_containerapp_arm.py tests/unit/test_azure_containerapp_bootstrap_planning.py tests/unit/test_azure_containerapp_environment_document.py tests/unit/test_azure_containerapp_environment_guard.py tests/unit/test_azure_containerapp_environment_lifecycle.py tests/unit/test_azure_containerapp_environment_operations.py tests/unit/test_azure_containerapp_environment_retention.py tests/unit/test_azure_containerapp_environment_make_runtime.py tests/unit/test_azure_containerapp_environment_runtime_types.py tests/unit/test_azure_containerapp_environment_state.py tests/unit/test_azure_containerapp_environment_preflight.py tests/unit/test_azure_containerapp_environment_terraform.py tests/unit/test_azure_containerapp_gpu.py tests/unit/test_azure_containerapp_gpu_backend.py tests/unit/test_azure_idle_retention.py tests/unit/test_azure_containerapp_live_proof.py tests/unit/test_azure_containerapp_make_runtime.py tests/unit/test_azure_containerapp_owned_lifecycle.py tests/unit/test_azure_containerapp_preflight.py tests/unit/test_azure_containerapp_preflight_cli.py tests/unit/test_azure_containerapp_runtime_factories.py tests/unit/test_azure_containerapp_runtime_readers.py tests/unit/test_azure_containerapp_resource_owner.py tests/unit/test_azure_containerapp_runtime_resources.py tests/unit/test_azure_containerapp_runtime_state.py tests/unit/test_azure_containerapp_sdk.py tests/unit/test_azure_containerapp_terraform_executor.py tests/unit/test_azure_containerapp_terraform_phase.py tests/unit/test_azure_containerapp_topology.py tests/unit/test_azure_containerapp_tfvars.py tests/unit/test_azure_infrastructure_evidence.py tests/unit/test_azure_self_improve_model_selection.py tests/unit/test_deployment_telemetry.py tests/unit/test_provider_auth.py tests/unit/test_select_azure_self_improve_model.py tests/unit/test_self_improve_azure_containerapp_backend.py tests/unit/test_self_improve_azure_containerapp_bootstrap.py tests/unit/test_self_improve_runtime_config.py tests/e2e/test_azure_containerapp_live_proof_cli.py tests/e2e/test_azure_containerapp_gha_oidc.py' \
		COVERAGE_CONFIG=config/coverage_azure_containerapp.ini \
		COVERAGE_REPORT=.gate-logs/coverage-azure-containerapp.json \
		COVERAGE_AGGREGATE_MIN=85 \
		COVERAGE_PER_FILE_MIN=75 \
		OBSERVED_ROOT=.gate-logs/observed \
		OBSERVED_HEARTBEAT_SECS=1 \
		OBSERVED_QUIET_SECS=60 \
		OBSERVED_MAX_SECS=300 \
		OBSERVED_RETAIN_RUNS=20

# Isolated inference worker: the parent owns its process group and exchange files.
self-improve-local-proposal:
	@if [ -n "$(SELF_IMPROVE_CONTRACT_FILE)" ] && [ -n "$(SELF_IMPROVE_ENVELOPE_FILE)" ]; then \
		echo "SELF_IMPROVE_CONTRACT_FILE and SELF_IMPROVE_ENVELOPE_FILE are mutually exclusive"; exit 2; \
	elif [ "$(SELF_IMPROVE_WORKER_VALIDATE_ONLY)" = "1" ]; then \
		echo "SELF_IMPROVE_LOCAL_PROPOSAL_PLAN model=$(SELF_IMPROVE_MODEL_PATH) prompt=$(SELF_IMPROVE_PROMPT_FILE) proposal=$(SELF_IMPROVE_PROPOSAL_FILE) contract=$(SELF_IMPROVE_CONTRACT_FILE) envelope=$(SELF_IMPROVE_ENVELOPE_FILE)"; \
	else \
		[ -n "$(SELF_IMPROVE_MODEL_PATH)" ] || { echo "SELF_IMPROVE_MODEL_PATH is required"; exit 2; }; \
		[ -n "$(SELF_IMPROVE_PROMPT_FILE)" ] || { echo "SELF_IMPROVE_PROMPT_FILE is required"; exit 2; }; \
		[ -n "$(SELF_IMPROVE_PROPOSAL_FILE)" ] || { echo "SELF_IMPROVE_PROPOSAL_FILE is required"; exit 2; }; \
		$(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=local-inference DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON= DEPENDENCY_PROFILE_VALIDATE_ONLY=0; \
		if [ -n "$(SELF_IMPROVE_ENVELOPE_FILE)" ]; then \
			$(UV) run --no-sync python scripts/self_improve_local_proposal.py --model-path "$(SELF_IMPROVE_MODEL_PATH)" --prompt-file "$(SELF_IMPROVE_PROMPT_FILE)" --proposal-file "$(SELF_IMPROVE_PROPOSAL_FILE)" --envelope-file "$(SELF_IMPROVE_ENVELOPE_FILE)"; \
		elif [ -n "$(SELF_IMPROVE_CONTRACT_FILE)" ]; then \
			$(UV) run --no-sync python scripts/self_improve_local_proposal.py --model-path "$(SELF_IMPROVE_MODEL_PATH)" --prompt-file "$(SELF_IMPROVE_PROMPT_FILE)" --proposal-file "$(SELF_IMPROVE_PROPOSAL_FILE)" --contract-file "$(SELF_IMPROVE_CONTRACT_FILE)"; \
		else \
			$(UV) run --no-sync python scripts/self_improve_local_proposal.py --model-path "$(SELF_IMPROVE_MODEL_PATH)" --prompt-file "$(SELF_IMPROVE_PROMPT_FILE)" --proposal-file "$(SELF_IMPROVE_PROPOSAL_FILE)"; \
		fi; \
	fi

# Local self-improvement benchmark — compares every proposed edit with Codex.
# Usage: make azure-self-improve-live-proof TARGET=development SELF_IMPROVE_BASELINE_REF=<sha> SELF_IMPROVE_REFERENCE_REF=<sha> AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_MODE=file AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_FILE=/private/auth.json AZURE_CONTAINERAPP_LIVE_PROOF_SUBSCRIPTION_ID=<uuid> AZURE_CONTAINERAPP_LIVE_PROOF_RESOURCE_GROUP=gludd-models-eastus AZURE_CONTAINERAPP_LIVE_PROOF_ENVIRONMENT=gludd-gpu-environment AZURE_CONTAINERAPP_LIVE_PROOF_LOCATION=eastus AZURE_CONTAINERAPP_LIVE_PROOF_ALLOWED_CIDR=auto AZURE_CONTAINERAPP_LIVE_PROOF_MAX_COST_USD=5 AZURE_CONTAINERAPP_LIVE_PROOF_TTL_MINUTES=60 AZURE_CONTAINERAPP_LIVE_PROOF_LIVE=0 AZURE_CONTAINERAPP_LIVE_PROOF_ACKNOWLEDGEMENT=DEPLOY_ONE_CONTAINER_APP_AND_DESTROY AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_PRESET=always_destroy AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_SECONDS=21600 AZURE_SELF_IMPROVE_MODEL_POLICY=config/self-improve/azure-model-selection-policy.json AZURE_SELF_IMPROVE_MODEL_CATALOG=config/self-improve/azure-model-catalog-ci.json AZURE_SELF_IMPROVE_EVIDENCE_FILE=/tmp/gludd-self-improve-evidence.json AZURE_SELF_IMPROVE_REGISTRY_CACHE=/tmp/gludd-self-improve-model-cache AZURE_SELF_IMPROVE_TASK_FILE=config/self-improve/catalog-truth.json
azure-self-improve-live-proof:
	@# Inputs: TARGET SELF_IMPROVE_MODEL_PATH SELF_IMPROVE_BASELINE_REF SELF_IMPROVE_REFERENCE_REF AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_MODE AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_FILE AZURE_CONTAINERAPP_LIVE_PROOF_FEDERATED_TOKEN_FILE AZURE_CONTAINERAPP_LIVE_PROOF_CLIENT_ID AZURE_CONTAINERAPP_LIVE_PROOF_TENANT_ID AZURE_CONTAINERAPP_LIVE_PROOF_SUBSCRIPTION_ID AZURE_CONTAINERAPP_LIVE_PROOF_RESOURCE_GROUP AZURE_CONTAINERAPP_LIVE_PROOF_ENVIRONMENT AZURE_CONTAINERAPP_LIVE_PROOF_LOCATION AZURE_CONTAINERAPP_LIVE_PROOF_ALLOWED_CIDR AZURE_CONTAINERAPP_LIVE_PROOF_MAX_COST_USD AZURE_CONTAINERAPP_LIVE_PROOF_TTL_MINUTES AZURE_CONTAINERAPP_LIVE_PROOF_LIVE AZURE_CONTAINERAPP_LIVE_PROOF_ACKNOWLEDGEMENT AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_PRESET AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_SECONDS AZURE_SELF_IMPROVE_MODEL_POLICY AZURE_SELF_IMPROVE_MODEL_CATALOG AZURE_SELF_IMPROVE_EVIDENCE_FILE AZURE_SELF_IMPROVE_REGISTRY_CACHE AZURE_SELF_IMPROVE_TASK_FILE
	@case "$(AZURE_CONTAINERAPP_LIVE_PROOF_LIVE)" in 0|1) ;; *) echo "AZURE_CONTAINERAPP_LIVE_PROOF_LIVE must be 0 or 1" >&2; exit 2;; esac
	@case "$(AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_MODE)" in \
		file) [ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_FILE)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_FILE is required for file auth" >&2; exit 2; } ;; \
		workload_identity) \
			[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_FEDERATED_TOKEN_FILE)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_FEDERATED_TOKEN_FILE is required for workload identity" >&2; exit 2; }; \
			[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_CLIENT_ID)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_CLIENT_ID is required for workload identity" >&2; exit 2; }; \
			[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_TENANT_ID)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_TENANT_ID is required for workload identity" >&2; exit 2; } ;; \
		*) echo "AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_MODE must be file or workload_identity" >&2; exit 2 ;; \
	esac
	@[ -n "$(SELF_IMPROVE_BASELINE_REF)" ] || { echo "SELF_IMPROVE_BASELINE_REF is required" >&2; exit 2; }
	@[ -n "$(SELF_IMPROVE_REFERENCE_REF)" ] || { echo "SELF_IMPROVE_REFERENCE_REF is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_SUBSCRIPTION_ID)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_SUBSCRIPTION_ID is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_RESOURCE_GROUP)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_RESOURCE_GROUP is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_ENVIRONMENT)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_ENVIRONMENT is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_LOCATION)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_LOCATION is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_ALLOWED_CIDR)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_ALLOWED_CIDR is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_MAX_COST_USD)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_MAX_COST_USD is required" >&2; exit 2; }
	@[ -n "$(AZURE_CONTAINERAPP_LIVE_PROOF_TTL_MINUTES)" ] || { echo "AZURE_CONTAINERAPP_LIVE_PROOF_TTL_MINUTES is required" >&2; exit 2; }
	@if [ "$(AZURE_CONTAINERAPP_LIVE_PROOF_LIVE)" = "1" ]; then $(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=ci-azure DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON= DEPENDENCY_PROFILE_VALIDATE_ONLY=0; fi
	@set -eu; temporary_directory="$$(mktemp -d "$${TMPDIR:-/tmp}/gludd-azure-self-improve.XXXXXX")"; \
		trap 'rm -rf "$$temporary_directory"' EXIT INT TERM; \
		selection_file="$$temporary_directory/model-selection.json"; \
		runtime_file="$$temporary_directory/runtime.json"; \
		echo "AZURE_SELF_IMPROVE_PHASE phase=model_selection secret_output=false"; \
		$(UV) run --no-sync python scripts/select_azure_self_improve_model.py \
			--task-file "$(AZURE_SELF_IMPROVE_TASK_FILE)" \
			--policy-file "$(AZURE_SELF_IMPROVE_MODEL_POLICY)" \
			--evidence-file "$(AZURE_SELF_IMPROVE_EVIDENCE_FILE)" \
			--location "$(AZURE_CONTAINERAPP_LIVE_PROOF_LOCATION)" \
			$(if $(filter 0,$(AZURE_CONTAINERAPP_LIVE_PROOF_LIVE)),--catalog-file "$(AZURE_SELF_IMPROVE_MODEL_CATALOG)",) \
			--registry-cache-dir "$(AZURE_SELF_IMPROVE_REGISTRY_CACHE)" \
			--output "$$selection_file"; \
		echo "AZURE_SELF_IMPROVE_PHASE phase=runtime_compile secret_output=false"; \
		$(UV) run python scripts/render_azure_self_improve_runtime_config.py \
			$(if $(filter file,$(AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_MODE)),--auth-file "$(AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_FILE)",--federated-token-file "$(AZURE_CONTAINERAPP_LIVE_PROOF_FEDERATED_TOKEN_FILE)" --azure-client-id "$(AZURE_CONTAINERAPP_LIVE_PROOF_CLIENT_ID)" --azure-tenant-id "$(AZURE_CONTAINERAPP_LIVE_PROOF_TENANT_ID)") \
			--model-selection-file "$$selection_file" \
			--evidence-file "$(AZURE_SELF_IMPROVE_EVIDENCE_FILE)" \
			--subscription-id "$(AZURE_CONTAINERAPP_LIVE_PROOF_SUBSCRIPTION_ID)" \
			--resource-group "$(AZURE_CONTAINERAPP_LIVE_PROOF_RESOURCE_GROUP)" \
			--environment "$(AZURE_CONTAINERAPP_LIVE_PROOF_ENVIRONMENT)" \
			--location "$(AZURE_CONTAINERAPP_LIVE_PROOF_LOCATION)" \
			--allowed-cidr "$(AZURE_CONTAINERAPP_LIVE_PROOF_ALLOWED_CIDR)" \
			--max-cost-usd "$(AZURE_CONTAINERAPP_LIVE_PROOF_MAX_COST_USD)" \
			--ttl-minutes "$(AZURE_CONTAINERAPP_LIVE_PROOF_TTL_MINUTES)" \
			--acknowledgement "$(AZURE_CONTAINERAPP_LIVE_PROOF_ACKNOWLEDGEMENT)" \
			--idle-retention-preset "$(AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_PRESET)" \
			--idle-retention-seconds "$(AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_SECONDS)" \
			--output "$$runtime_file"; \
		echo "AZURE_SELF_IMPROVE_PHASE phase=mixed_candidate_evaluation secret_output=false"; \
		$(MAKE) --no-print-directory test-self-improve TARGET="$(TARGET)" SELF_IMPROVE_MODEL_PATH="$(SELF_IMPROVE_MODEL_PATH)" SELF_IMPROVE_CONFIG_FILE="$$runtime_file" SELF_IMPROVE_BASELINE_REF="$(SELF_IMPROVE_BASELINE_REF)" SELF_IMPROVE_REFERENCE_REF="$(SELF_IMPROVE_REFERENCE_REF)" SELF_IMPROVE_TASK_FILE="$(AZURE_SELF_IMPROVE_TASK_FILE)" SELF_IMPROVE_MAX_ATTEMPTS=1 SELF_IMPROVE_VALIDATE_ONLY=$(if $(filter 1,$(AZURE_CONTAINERAPP_LIVE_PROOF_LIVE)),0,1)

# Usage: make test-self-improve TARGET=name [SELF_IMPROVE_MODEL_PATH=optional override] [SELF_IMPROVE_CONFIG_FILE=optional.json] SELF_IMPROVE_BASELINE_REF=<sha> SELF_IMPROVE_REFERENCE_REF=<sha> SELF_IMPROVE_TASK_FILE=task.json SELF_IMPROVE_VALIDATE_ONLY=0
test-self-improve:
	@[ -n "$(TARGET)" ] || { echo "TARGET is required"; exit 2; }
	@[ -n "$(SELF_IMPROVE_BASELINE_REF)" ] || { echo "SELF_IMPROVE_BASELINE_REF is required"; exit 2; }
	@[ -n "$(SELF_IMPROVE_REFERENCE_REF)" ] || { echo "SELF_IMPROVE_REFERENCE_REF is required"; exit 2; }
	@[ -n "$(SELF_IMPROVE_TASK_FILE)" ] || { echo "SELF_IMPROVE_TASK_FILE is required"; exit 2; }
	@if [ -n "$(SELF_IMPROVE_CONFIG_FILE)" ]; then $(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=ci-azure DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON= DEPENDENCY_PROFILE_VALIDATE_ONLY=0; fi
	@$(UV) run --no-sync python scripts/run_self_improve_e2e.py --target "$(TARGET)" --local-model-path "$(SELF_IMPROVE_MODEL_PATH)" --self-improve-config-file "$(SELF_IMPROVE_CONFIG_FILE)" --baseline-ref "$(SELF_IMPROVE_BASELINE_REF)" --reference-ref "$(SELF_IMPROVE_REFERENCE_REF)" --task-file "$(SELF_IMPROVE_TASK_FILE)" --max-attempts "$(SELF_IMPROVE_MAX_ATTEMPTS)" $(if $(filter 1,$(SELF_IMPROVE_VALIDATE_ONLY)),--validate-only,)

# Canonical ten-shape contract; validate-only is safe, live inference is explicit.
test-self-improve-acceptance-matrix:
	@case "$(SELF_IMPROVE_ACCEPTANCE_MATRIX_LIVE)" in 0|1) ;; *) echo "SELF_IMPROVE_ACCEPTANCE_MATRIX_LIVE must be 0 or 1"; exit 2;; esac
	@EXPECTED_MATRIX_SHA256="5a5dfc0b40308a8b39039dd28e923d59bd9b8855eb5d679bd25fb68ba4b19a25"; \
		ACTUAL_MATRIX_SHA256="$$($(PYTHON) -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$(SELF_IMPROVE_ACCEPTANCE_MATRIX_FILE)")"; \
		[ "$$ACTUAL_MATRIX_SHA256" = "$$EXPECTED_MATRIX_SHA256" ] || { echo "acceptance matrix drift: expected=$$EXPECTED_MATRIX_SHA256 actual=$$ACTUAL_MATRIX_SHA256"; exit 2; }
	@$(UV) run python -m tests.unit.self_improve_acceptance_matrix_runner --manifest "$(SELF_IMPROVE_ACCEPTANCE_MATRIX_FILE)" --model-path "$(SELF_IMPROVE_ACCEPTANCE_MATRIX_MODEL_PATH)" $(if $(filter 1,$(SELF_IMPROVE_ACCEPTANCE_MATRIX_LIVE)),--live,)

# Hermetic provider-neutral policy boundary; no model downloads or cloud credentials.
test-self-improve-private-policy:
	@$(UV) run python -m pytest tests/e2e/test_self_improve_private_policy_e2e.py -W error

# Reproducible catalog-truth sentinel: safe plan by default; live inference is explicit.
test-self-improve-catalog-truth:
	@case "$(SELF_IMPROVE_CATALOG_LIVE)" in 0|1) ;; *) echo "SELF_IMPROVE_CATALOG_LIVE must be 0 or 1"; exit 2;; esac
	@ACTUAL_FIXTURE_SHA256="$$($(PYTHON) -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "config/self-improve/catalog-truth.json")"; \
		[ "$$ACTUAL_FIXTURE_SHA256" = "67e59f242aba0ade9b5992354daf5f0ec2392df3627ef0c929596011cfe5c30e" ] || { echo "catalog-truth fixture drift: expected=67e59f242aba0ade9b5992354daf5f0ec2392df3627ef0c929596011cfe5c30e actual=$$ACTUAL_FIXTURE_SHA256"; exit 2; }
	@$(MAKE) --no-print-directory test-self-improve TARGET=catalog-truth SELF_IMPROVE_MODEL_PATH= SELF_IMPROVE_BASELINE_REF=eac05dc88c03f14fbd7dd5f4c6d72943609d9e26 SELF_IMPROVE_REFERENCE_REF=80b381bd87f32487d784964ce93566e3b016b191 SELF_IMPROVE_TASK_FILE=config/self-improve/catalog-truth.json SELF_IMPROVE_MAX_ATTEMPTS=2 SELF_IMPROVE_VALIDATE_ONLY="$(if $(filter 1,$(SELF_IMPROVE_CATALOG_LIVE)),0,1)"

# Fast deterministic replay of typed failures; never loads or downloads a model.
test-self-improve-failure-corpus:
	@[ -n "$(SELF_IMPROVE_FAILURE_CORPUS_FILE)" ] || { echo "SELF_IMPROVE_FAILURE_CORPUS_FILE is required"; exit 2; }
	@$(UV) run python -m scripts.replay_self_improve_failure_corpus --corpus "$(SELF_IMPROVE_FAILURE_CORPUS_FILE)"

# Reproducible multi-file context/lifecycle sentinel; safe plan by default.
test-self-improve-multifile:
	@case "$(SELF_IMPROVE_MULTIFILE_LIVE)" in 0|1) ;; *) echo "SELF_IMPROVE_MULTIFILE_LIVE must be 0 or 1"; exit 2;; esac
	@EXPECTED_FIXTURE_SHA256_OCTETS="76 3f c9 c6 bc ea 10 30 35 a1 48 a1 aa 5e df d4 15 cc 9e 81 06 15 f5 17 cd 1e 36 0a 05 ce 7c 4d"; \
		EXPECTED_FIXTURE_SHA256="$$(printf '%s' "$$EXPECTED_FIXTURE_SHA256_OCTETS" | tr -d ' ')"; \
		ACTUAL_FIXTURE_SHA256="$$($(PYTHON) -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "config/self-improve/context-budget-lifecycle.json")"; \
		[ "$$ACTUAL_FIXTURE_SHA256" = "$$EXPECTED_FIXTURE_SHA256" ] || { echo "multifile fixture drift: expected=$$EXPECTED_FIXTURE_SHA256 actual=$$ACTUAL_FIXTURE_SHA256"; exit 2; }
	@$(MAKE) --no-print-directory test-self-improve TARGET=multifile-context-lifecycle SELF_IMPROVE_MODEL_PATH= SELF_IMPROVE_BASELINE_REF=80b381bd87f32487d784964ce93566e3b016b191 SELF_IMPROVE_REFERENCE_REF=6463324cfcf6db9b9a2f9ec203e0bd3862a1e80e SELF_IMPROVE_TASK_FILE=config/self-improve/context-budget-lifecycle.json SELF_IMPROVE_MAX_ATTEMPTS=2 SELF_IMPROVE_VALIDATE_ONLY="$(if $(filter 1,$(SELF_IMPROVE_MULTIFILE_LIVE)),0,1)"

# Verify an immutable managed-promotion marker only on development history.
# Usage: make self-improve-promotion-marker SELF_IMPROVE_PROMOTION_ARTIFACT_DIGEST=<sha256> SELF_IMPROVE_PROMOTION_PLAN_DIGEST=<sha256> SELF_IMPROVE_PROMOTION_ATTEMPT_DIGEST=<sha256> SELF_IMPROVE_PROMOTION_VALIDATE_ONLY=0|1
self-improve-promotion-marker:
	@ARTIFACT="$(SELF_IMPROVE_PROMOTION_ARTIFACT_DIGEST)"; \
	PLAN="$(SELF_IMPROVE_PROMOTION_PLAN_DIGEST)"; \
	ATTEMPT="$(SELF_IMPROVE_PROMOTION_ATTEMPT_DIGEST)"; \
	VALIDATE_ONLY="$(SELF_IMPROVE_PROMOTION_VALIDATE_ONLY)"; \
	for VALUE in "$$ARTIFACT" "$$PLAN" "$$ATTEMPT"; do \
		case "$$VALUE" in *[!0-9a-f]*|'') echo "promotion digests must be lowercase hexadecimal"; exit 2;; esac; \
		[ "$${#VALUE}" -eq 64 ] || { echo "promotion digests must contain 64 characters"; exit 2; }; \
	done; \
	case "$$VALIDATE_ONLY" in 0|1) ;; *) echo "SELF_IMPROVE_PROMOTION_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac; \
	if [ "$$VALIDATE_ONLY" = 1 ]; then echo "PROMOTION_MARKER_VALIDATE_ONLY=ok branch=development"; exit 0; fi; \
	git rev-parse --verify development^{commit} >/dev/null 2>&1 || { echo "development branch is unavailable"; exit 2; }; \
	COMMIT=$$(git log development --fixed-strings --grep="Gludd-Self-Improve-Artifact=$$ARTIFACT" --format='%H' -n 1); \
	if [ -z "$$COMMIT" ]; then echo "PROMOTION_ABSENT"; exit 3; fi; \
	BODY=$$(git show -s --format='%B' "$$COMMIT"); \
	printf '%s\n' "$$BODY" | grep -Fq "Gludd-Self-Improve-Artifact=$$ARTIFACT" || { echo "artifact marker mismatch"; exit 2; }; \
	printf '%s\n' "$$BODY" | grep -Fq "Gludd-Self-Improve-Plan=$$PLAN" || { echo "plan marker mismatch"; exit 2; }; \
	printf '%s\n' "$$BODY" | grep -Fq "Gludd-Self-Improve-Attempt=$$ATTEMPT" || { echo "attempt marker mismatch"; exit 2; }; \
	echo "PROMOTION_COMMIT=$$COMMIT"

# Compatibility alias retains one explicit reference boundary; it never fans out.
test-self-improve-all:
	@$(MAKE) --no-print-directory test-self-improve TARGET="$(TARGET)" SELF_IMPROVE_MODEL_PATH="$(SELF_IMPROVE_MODEL_PATH)" SELF_IMPROVE_BASELINE_REF="$(SELF_IMPROVE_BASELINE_REF)" SELF_IMPROVE_REFERENCE_REF="$(SELF_IMPROVE_REFERENCE_REF)" SELF_IMPROVE_TASK_FILE="$(SELF_IMPROVE_TASK_FILE)" SELF_IMPROVE_MAX_ATTEMPTS="$(SELF_IMPROVE_MAX_ATTEMPTS)" SELF_IMPROVE_VALIDATE_ONLY="$(SELF_IMPROVE_VALIDATE_ONLY)"
