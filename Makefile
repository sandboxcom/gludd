MSG ?=
FILES ?=
TESTFILE ?=
REF ?=
TARGET ?= master
MYPY_MAX := 0
OPENCODE_DB ?= ~/.local/share/opencode/opencode.db

PYTHON := python3
UV := uv
PROJECT_SRC := src/general_ludd
TESTS_DIR := tests
# Worker count: env GLUDD_XDIST overrides (CI sets it so the suite isn't run on a
# single worker — a 4-vCPU runner's cpu//4=1 made the gate sit ~38min near the
# 40min wall). Local default stays cpu//4. Accepts an int or "auto".
_XDIST_WORKERS := $(shell python3 -c "import os; v=os.environ.get('GLUDD_XDIST'); print(v if v else max(1, (os.cpu_count() or 1) // 4))")
_XD = -n $(_XDIST_WORKERS) --dist loadgroup

    .PHONY: \
        init sync install-pip lint lint-fix test test-unit test-specific test-count test-integration test-e2e \
        test-guardrails test-scripts test-db test-live-zai test-tui-daemon \
        typecheck setup-dirs setup-venv clean healthcheck \
        bootstrap skeleton version check-uv check-pytest \
        ansible-syntax ansible-lint-playbooks ansible-collection-test playbook-list \
        git-status git-init git-add git-commit git-log git-diff git-reset \
        git-branch git-checkout git-merge git-staged \
        repo-status repo-diff repo-staged repo-log \
		feature-start feature-done test-and-commit preflight \
		molecule-version molecule-test molecule-test-all \
		collection-roles collection-modules molecule-scenarios \
		container-build container-run container-push \
        build-executable dist dist-clean bundle-binaries bundle-ripgrep \
        sast sbom pip-audit security \
        audit-messages qa validate collect-check gate smoke install-hooks \
        status-snapshot audit-evidence deps-audit dogfood-features \
        skill-install skill-list bootstrap-skills scan-tool-usage \
        scan-secrets scan-secrets-baseline clean-untracked clean-hooks \
        git-remote-sandboxcom git-push-sandboxcom git-pull-sandboxcom git-fetch-sandboxcom \
        git-add-all help grep scan-secrets-fresh untrack \
        git-tracked-keys git-ls-tracked git-history-file dist-path-check \
        molecule-clean plan ps-gludd kill-stale kill-gate-force \
        gate-async gate-status floor-plan gated-merge ship-async \
        git-cherry-pick git-cherry-continue

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "  --- Setup ---"
	@echo "  init                  Set up project (dirs + deps)"
	@echo "  sync                  Sync uv dependencies"
	@echo "  bootstrap             init + lint + test + healthcheck"
	@echo "  install-hooks         Install pre-commit hooks (secrets, lint, collect)"
	@echo ""
	@echo "  --- Quality ---"
	@echo "  lint                  Run ruff linter"
	@echo "  lint-fix              Run ruff with auto-fix"
	@echo "  typecheck             Run mypy"
	@echo "  healthcheck           Verify imports work"
	@echo "  qa                    Run lint + typecheck + test + healthcheck"
	@echo "  validate              Full validation (lint + typecheck + test + ansible + healthcheck)"
	@echo "  gate                  Full gate: lint + typecheck + collect-check + test"
	@echo "  gate-async            Launch gate detached (non-blocking); writes .gate-status"
	@echo "  gate-status           Print current .gate-status (RUNNING/PASS/FAIL)"
	@echo "  collect-check         Fast collection-error gate"
	@echo "  preflight             Preflight quality gate (coverage, lint, mypy, templates, etc.)"
	@echo "  sast                  Run bandit SAST"
	@echo "  sbom                  Generate CycloneDX SBOM"
	@echo "  pip-audit             Audit dependencies for vulnerabilities"
	@echo "  security              Full security: sast + sbom + pip-audit"
	@echo ""
	@echo "  --- Testing ---"
	@echo "  test                  Full test suite with coverage"
	@echo "  test-unit             Unit tests only"
	@echo "  test-integration      Integration tests"
	@echo "  test-e2e              End-to-end tests"
	@echo "  test-specific         Single test (TESTFILE='path::TestClass::test_name')"
	@echo "  test-count            Count collected tests"
	@echo "  test-failures         Show test failures"
	@echo "  test-and-commit       Run tests then commit if green (MSG='msg')"
	@echo "  test-live-zai         Live GLM model test (requires API key)"
	@echo "  test-guardrails       Test guardrail infrastructure"
	@echo ""
	@echo "  --- Git ---"
	@echo "  git-status            Show git status"
	@echo "  git-diff              Show diff stats"
	@echo "  git-staged            Show staged changes"
	@echo "  git-log               Show recent commits"
	@echo "  git-add FILES='...'   Stage specific files"
	@echo "  git-add-all           Stage all changes"
	@echo "  git-commit MSG='...'  Commit staged changes"
	@echo "  git-reset FILES='...' Reset to ref (soft by default)"
	@echo "  git-branch MSG='...'  Create branch"
	@echo "  git-checkout MSG='...' Switch branch"
	@echo "  git-merge MSG='...'   Merge branch with --no-ff"
	@echo "  feature-start MSG='...' Create and switch to feature branch"
	@echo "  feature-done MSG='...' Test, merge to master with --no-ff"
	@echo ""
	@echo "  --- Secrets + Security ---"
	@echo "  scan-secrets          Run detect-secrets scan against baseline"
	@echo "  scan-secrets-baseline Create/update detect-secrets baseline"
	@echo "  clean-untracked       Remove reinvention-of-wheel files"
	@echo "  clean-hooks           Remove legacy hook scripts"
	@echo ""
	@echo "  --- Build + Deploy ---"
	@echo "  dist                  Build distribution tarball"
	@echo "  build-executable      Build standalone executable (pyinstaller)"
	@echo "  container-build       Build container image"
	@echo "  container-run         Run container locally"
	@echo "  container-push        Push container image"
	@echo ""
	@echo "  --- Ansible ---"
	@echo "  ansible-syntax        Validate playbook syntax"
	@echo "  playbook-list         List registered playbooks"
	@echo "  molecule-test         Run molecule tests"
	@echo ""
	@echo "  --- Git Remote ---"
	@echo "  git-remote-sandboxcom Configure sandboxcom GitHub remote with SSH key"
	@echo "  git-push-sandboxcom   Push to sandboxcom/gludd mirror"
	@echo "  git-pull-sandboxcom   Pull and rebase from sandboxcom/gludd"
	@echo "  git-fetch-sandboxcom  Fetch from sandboxcom/gludd"
	@echo "  ship-async REF=<hash> [TARGET=master]  Run gate in background job; ff-only merge on green"
	@echo ""
	@echo "  --- Other ---"
	@echo "  smoke                 Quick daemon boot health check"
	@echo "  clean                 Remove build artifacts"
	@echo "  dist-clean            Remove distribution artifacts"
	@echo "  gated-merge           flock-guarded multi-branch merge with manifest (BASE/BRANCHES/MERGE_STRATEGY/MANIFEST)"

skeleton:
	@$(PYTHON) scripts/skeleton.py

scan-tool-usage:
	@$(PYTHON) scripts/scan_tool_usage.py

setup-dirs:
	@mkdir -p src/general_ludd/worker
	@mkdir -p src/general_ludd/event_loop
	@mkdir -p src/general_ludd/models
	@mkdir -p src/general_ludd/db
	@mkdir -p src/general_ludd/rules
	@mkdir -p src/general_ludd/schemas
	@mkdir -p src/general_ludd/secrets
	@mkdir -p src/general_ludd/git_automation
	@mkdir -p src/general_ludd/controllers
	@mkdir -p src/general_ludd/ansible
	@mkdir -p src/general_ludd/prompts
	@mkdir -p src/general_ludd/quality
	@mkdir -p src/general_ludd/runtime
	@mkdir -p tests/unit
	@mkdir -p tests/integration
	@mkdir -p tests/e2e
	@mkdir -p playbooks
	@mkdir -p roles
	@mkdir -p molecule/playbooks
	@mkdir -p molecule/roles
	@mkdir -p molecule/internal_tools
	@mkdir -p templates/prompts/partials
	@mkdir -p tools/ansible_lint_rules
	@mkdir -p scripts
	@mkdir -p docs
	@mkdir -p config
	@mkdir -p alembic/versions
	@mkdir -p collections
	@echo "Directory structure created."

init: setup-dirs
	@if [ ! -f pyproject.toml ]; then echo "ERROR: pyproject.toml missing"; exit 1; fi
	@if command -v $(UV) >/dev/null 2>&1; then echo "Using uv..."; $(UV) sync; else echo "uv not found, using pip..."; $(PYTHON) -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"; fi
	@$(MAKE) --no-print-directory install-hooks

sync:
	@$(UV) sync --locked

# Regenerate uv.lock from pyproject (after adding/removing a dependency) and
# install it. Use this instead of `sync` when pyproject deps changed.
relock:
	@$(UV) lock
	@$(UV) sync

install-pip:
	@$(PYTHON) -m venv .venv
	@. .venv/bin/activate && pip install --upgrade pip
	@. .venv/bin/activate && pip install -e ".[dev]"

version:
	@$(UV) run python -c "from general_ludd import __version__; print(f'general-ludd-agent {__version__}')"

check-uv:
	@command -v $(UV) >/dev/null 2>&1 || (echo "uv not found"; exit 1)
	@$(UV) --version

check-pytest:
	@$(UV) run python -c "import pytest; print(f'pytest {pytest.__version__}')"

lint:
	@$(UV) run ruff check src tests

lint-fix:
	@$(UV) run ruff check --fix --unsafe-fixes src tests

typecheck:
	@$(UV) run mypy src

test:
	@$(UV) run python -m pytest tests/ --cov=general_ludd --cov-report=term-missing --cov-report=xml $(_XD) -v

test-unit:
	@if [ -n "$(TESTFILE)" ]; then \
		$(UV) run python -m pytest $(TESTFILE) $(_XD) -v; \
	else \
		$(UV) run python -m pytest tests/unit/ $(_XD) -v; \
	fi

test-specific:
	@if [ -z "$(TESTFILE)" ]; then echo "Usage: make test-specific TESTFILE='tests/unit/test_foo.py::TestClass::test_method'"; exit 1; fi
	@$(UV) run python -m pytest $(TESTFILE) $(_XD) -v

test-count:
	@$(UV) run python -m pytest tests/ --co -q 2>&1 | tail -3

test-failures:
	@$(UV) run python -m pytest tests/ $(_XD) -q 2>&1 | tee /tmp/gludd-test-output.txt; EXIT=$$?; \
	grep -E "^(FAILED|ERROR)" /tmp/gludd-test-output.txt; \
	exit $$EXIT

collect-check:
	@$(UV) run python -m pytest tests/ --co -q > /tmp/gludd-collect-output.txt 2>&1; EXIT=$$?; \
	if [ $$EXIT -ne 0 ]; then \
		echo "COLLECTION ERRORS DETECTED"; \
		grep -E "ERROR|error" /tmp/gludd-collect-output.txt | head -5; \
		exit 1; \
	fi; \
	echo "Collection OK"

gate:
	@rm -f .gate-failed
	@echo "=== GATE $(shell date -u +%Y-%m-%dT%H:%M:%SZ) ===" > .gate-status
	@# OBSERVABILITY INVARIANT (see AGENTS.md "No unseen events"): every gate phase
	@# emits a timestamped stdout marker as it STARTS, so a running gate (even
	@# backgrounded) is visibly advancing through phases — never a silent black box.
	@echo "[gate $$(date +%H:%M:%S)] phase 1/5 lint ..."
	@printf "lint " >> .gate-status
	@if $(UV) run ruff check src tests --output-format concise > /dev/null 2>&1; then \
		echo "PASS 0" >> .gate-status; \
	else \
		echo "FAIL $$($(UV) run ruff check src tests --output-format concise 2>&1 | grep -c .)" >> .gate-status && touch .gate-failed; \
	fi
	@echo "[gate $$(date +%H:%M:%S)] phase 2/5 typecheck (mypy, ~30-60s) ..."
	@printf "typecheck " >> .gate-status
	@TC_ERRS=$$($(UV) run mypy src 2>&1 | grep -c 'error:'); \
	TC_ERRS=$${TC_ERRS:-0}; \
	if [ "$$TC_ERRS" -le "$(MYPY_MAX)" ]; then echo "PASS $$TC_ERRS" >> .gate-status; else echo "FAIL $$TC_ERRS" >> .gate-status && touch .gate-failed; fi
	@echo "[gate $$(date +%H:%M:%S)] phase 3/5 collect ..."
	@printf "collect " >> .gate-status
	@$(MAKE) --no-print-directory collect-check > /dev/null 2>&1 && echo "PASS 0" >> .gate-status || (echo "FAIL collection-errors" >> .gate-status && touch .gate-failed)
	@echo "[gate $$(date +%H:%M:%S)] phase 4/5 test (full suite, streams below; ~16 min) ..."
	@printf "test " >> .gate-status
	@# Delegate to scripts/run_gate.sh which provides:
	@#   (1) exclusive non-blocking flock on /tmp/gludd-gate.lock — a concurrent
	@#       gate is REJECTED immediately rather than silently corrupting shared tmp;
	@#   (2) per-run unique basetemp (mktemp -d /tmp/gludd-gate-XXXXXX) so even if
	@#       the lock were bypassed two runs cannot collide on pytest's popen-gwN dirs;
	@#   (3) EXIT/INT/TERM trap that removes the unique basetemp and releases the lock
	@#       on any exit, preventing orphan-holds-lock / tmp-leak after a kill.
	@# run_gate.sh writes "PASS 0" or "FAIL non-zero-exit" to .gate-status itself
	@# and touches .gate-failed on failure, so we only need to propagate its exit.
	@bash scripts/run_gate.sh; EXIT=$$?; \
	if [ "$$EXIT" -ne 0 ] && [ ! -f .gate-failed ]; then touch .gate-failed; fi; \
	exit $$EXIT
	@echo "[gate $$(date +%H:%M:%S)] phase 5/5 smoke (real daemon boot) ..."
	@printf "smoke " >> .gate-status
	@$(MAKE) --no-print-directory smoke > /tmp/gludd-gate-smoke.log 2>&1 && echo "PASS" >> .gate-status || (echo "FAIL" >> .gate-status && touch .gate-failed && echo "[gate] smoke FAILED — tail:" && tail -20 /tmp/gludd-gate-smoke.log)
	@echo "[gate $$(date +%H:%M:%S)] all phases done, finalizing ..."
	@echo "---" >> .gate-status
	@# Stamp the epoch at COMPLETION (executed via $$(...), not parse-time $(shell)).
	@# The git-commit freshness window (30 min) must start when the gate FINISHES,
	@# not when it began — the full gate can run longer than 30 min as the suite grows.
	@echo "epoch $$(date +%s)" >> .gate-status
	@cat .gate-status
	@if [ -f .gate-failed ]; then rm -f .gate-failed; exit 1; fi
	@echo "Gate: ALL PASSED"

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
	grep -E 'pytest|molecule|general_ludd|gludd-gate-basetemp|ansible-playbook' | \
	grep -v -E 'grep |ps-gludd|kill-stale' | \
	while read -r pid ppid etime rest; do \
		[ "$$pid" = "$$SELF" ] && continue; \
		[ "$$pid" = "$$PARENT" ] && continue; \
		if [ "$$ppid" = "1" ]; then state=ORPHAN; else state=active; fi; \
		printf '%-8s %-8s %-10s %-7s %s\n' "$$pid" "$$ppid" "$$etime" "$$state" "$$(echo "$$rest" | cut -c1-86)"; \
	done; \
	echo "--- ORPHAN(ppid=1)=stale, parent died; active=live parent. kill-stale removes ORPHANs only ---"

# Kill stray pytest/gate processes (e.g. xdist workers orphaned by a killed run).
# NOTE: blunt instrument — see kill-stale for self-tree-protecting cleanup.
kill-stray:
	@pkill -9 -f 'gludd-gate-basetemp' 2>/dev/null; pkill -9 -f 'pytest tests/' 2>/dev/null; pkill -9 -f 'make gate' 2>/dev/null; echo "killed stray pytest/gate (if any)"

# Reap ONLY genuinely-stale gludd processes — never the active one. A process is
# killed iff ALL of:
#   (1) it matches a known gludd scratch pattern (molecule mock_daemon, a python
#       running out of a .claude/worktrees/agent-* venv, a stray cli tui, an
#       orphaned pytest/gate-basetemp/ansible run), AND
#   (2) its parent is PID 1 — it was REPARENTED because the make/agent/gate that
#       spawned it died (a live-parented process is still part of an active run), AND
#   (3) it has NO living child processes — so an orphaned-but-alive daemon that is
#       still recycling workers (e.g. the gunicorn daemon) is treated as ACTIVE and
#       KEPT, exactly the "don't kill the active one" guarantee.
# This make invocation's own process + its parent are always excluded, so running
# `make kill-stale` can never kill the shell/agent driving it. See `make ps-gludd`
# for the read-only census this acts on.
kill-stale:
	@SELF=$$$$; PARENT=$$(ps -o ppid= -p $$SELF 2>/dev/null | tr -d ' '); \
	PARENTS=$$(ps -axo ppid= | tr -s ' ' '\n' | grep -E '^[0-9]+$$' | sort -u); \
	echo "[kill-stale] self=$$SELF parent=$$PARENT — reaping orphaned childless gludd scratch only"; \
	ps -axo pid=,ppid=,command= | \
	grep -E 'molecule/mock_daemon|\.claude/worktrees/agent-[^ ]*/\.venv/bin/python|general_ludd\.cli tui|gludd-gate-basetemp|pytest tests/|ansible-playbook' | \
	grep -v -E 'grep |kill-stale|ps-gludd' | \
	while read -r pid ppid rest; do \
		cmd=$$(echo "$$rest" | cut -c1-70); \
		{ [ "$$pid" = "$$SELF" ] || [ "$$pid" = "$$PARENT" ]; } && { echo "  KEEP (self/parent): $$pid"; continue; }; \
		if [ "$$ppid" != "1" ]; then echo "  KEEP (live parent $$ppid = active run): $$pid $$cmd"; continue; fi; \
		if echo "$$PARENTS" | grep -qx "$$pid"; then echo "  KEEP (orphan WITH live children = active daemon): $$pid $$cmd"; continue; fi; \
		kill -TERM "$$pid" 2>/dev/null; sleep 0.2; kill -KILL "$$pid" 2>/dev/null; \
		echo "  KILLED stale orphan: $$pid $$cmd"; \
	done; \
	echo "[kill-stale] done"

# Force-kill any running gate: send SIGTERM to the process that owns the gate
# lock, then remove the lock and any gludd-gate-XXXXXX tmp dirs so the next
# `make gate` can start cleanly. Use when `make kill-stale` is too conservative.
kill-gate-force:
	@echo "[kill-gate-force] reading lock owner from /tmp/gludd-gate.lock ..."
	@HOLDER=$$(cat /tmp/gludd-gate.lock 2>/dev/null || echo ""); \
	if [ -n "$$HOLDER" ] && kill -0 "$$HOLDER" 2>/dev/null; then \
		echo "[kill-gate-force] killing PID $$HOLDER"; \
		kill -TERM "$$HOLDER" 2>/dev/null || true; sleep 1; \
		kill -KILL "$$HOLDER" 2>/dev/null || true; \
	else \
		echo "[kill-gate-force] no live gate process found in lock file"; \
	fi
	@rm -f /tmp/gludd-gate.lock /tmp/gludd-gate.lock.*.tmp
	@rm -rf /tmp/gludd-gate-[A-Za-z0-9]* 2>/dev/null || true
	@echo "[kill-gate-force] lock + tmp dirs removed"

ship-async:
	@bash scripts/ship_async.sh $(REF) $(TARGET)

# STALL WATCHDOG — run a long command under active no-progress + max-runtime
# supervision so a hang can NEVER sit silently forever. Streams the command's
# output to LOG; every 10s it checks (a) how long since LOG last grew (idle) and
# (b) total elapsed. If idle >= STALL_SECS (no progress = stalled) or elapsed >=
# MAX_SECS, it kills the whole process tree and exits non-zero (124) with a clear
# RESULT= line — so the supervising task COMPLETES (and notifies) instead of
# leaving anyone waiting on a dead run. Emits a heartbeat each cycle.
#   Usage: make run-watched CMD='make ci-repro-linux PYV=3.11' STALL_SECS=180 MAX_SECS=3600
BASE ?=
BRANCHES ?=
MERGE_STRATEGY ?= stop-on-conflict
MANIFEST ?= /tmp/gludd-gated-merge-manifest.txt

gated-merge:
	@BASE='$(BASE)' BRANCHES='$(BRANCHES)' MERGE_STRATEGY='$(MERGE_STRATEGY)' MANIFEST='$(MANIFEST)' bash scripts/gated_merge.sh

STALL_SECS ?= 180
MAX_SECS ?= 3600
run-watched:
	@if [ -z "$(CMD)" ]; then echo "Usage: make run-watched CMD='<command>' [STALL_SECS=180] [MAX_SECS=3600] [LOG=/tmp/gludd-watched.log]"; exit 1; fi
	@LOGF="$${LOG:-/tmp/gludd-watched.log}"; : > "$$LOGF"; \
	echo "[watchdog] CMD: $(CMD)"; \
	echo "[watchdog] stall>$(STALL_SECS)s or total>$(MAX_SECS)s -> kill tree + RESULT; log=$$LOGF"; \
	set -m; $(CMD) > "$$LOGF" 2>&1 & CMDPID=$$!; \
	START=$$(date +%s); \
	while kill -0 $$CMDPID 2>/dev/null; do \
		sleep 10; \
		NOW=$$(date +%s); \
		MT=$$(stat -f %m "$$LOGF" 2>/dev/null || stat -c %Y "$$LOGF" 2>/dev/null || echo $$NOW); \
		IDLE=$$((NOW - MT)); ELAPSED=$$((NOW - START)); \
		echo "[watchdog $$(date +%H:%M:%S)] elapsed=$${ELAPSED}s idle=$${IDLE}s (last log line: $$(tail -1 "$$LOGF" 2>/dev/null | cut -c1-70))"; \
		if [ "$$IDLE" -ge "$(STALL_SECS)" ]; then \
			echo "[watchdog] STALL: no output for $${IDLE}s — killing tree"; \
			kill -TERM -$$CMDPID 2>/dev/null || kill -TERM $$CMDPID 2>/dev/null; sleep 2; kill -KILL -$$CMDPID 2>/dev/null || kill -KILL $$CMDPID 2>/dev/null; pkill -9 -f gludd-gate-basetemp 2>/dev/null; \
			echo "[watchdog] RESULT=STALLED idle=$${IDLE}s elapsed=$${ELAPSED}s"; exit 124; \
		fi; \
		if [ "$$ELAPSED" -ge "$(MAX_SECS)" ]; then \
			echo "[watchdog] TIMEOUT: ran $${ELAPSED}s — killing tree"; \
			kill -TERM -$$CMDPID 2>/dev/null || kill -TERM $$CMDPID 2>/dev/null; sleep 2; kill -KILL -$$CMDPID 2>/dev/null || kill -KILL $$CMDPID 2>/dev/null; pkill -9 -f gludd-gate-basetemp 2>/dev/null; \
			echo "[watchdog] RESULT=TIMEOUT elapsed=$${ELAPSED}s"; exit 124; \
		fi; \
	done; \
	wait $$CMDPID; RC=$$?; echo "[watchdog] RESULT=EXIT rc=$$RC elapsed=$$(($$(date +%s)-START))s"; exit $$RC

test-integration:
	@$(UV) run python -m pytest tests/integration/ $(_XD) -v

test-e2e:
	@$(UV) run python -m pytest tests/e2e/ $(_XD) -v

test-tui-daemon:
	@$(UV) run python -m pytest tests/e2e/test_tui_daemon_start.py -v -s

test-guardrails:
	@$(UV) run python -m pytest tests/unit/test_guardrails.py tests/unit/test_user_requested_guardrails.py $(_XD) -v

test-db:
	@$(UV) run python -m pytest tests/unit/test_db_models.py $(_XD) -v

test-scripts:
	@$(UV) run python -m pytest tests/unit/test_guardrails.py::TestSkeletonScript $(_XD) -v

healthcheck:
	@$(UV) run python -c "from general_ludd.worker.app import create_app; app = create_app(); print('Worker app factory OK')"
	@$(UV) run python -c "from general_ludd.event_loop.loop import EventLoop; print('Event loop import OK')"

ansible-syntax:
	@for f in playbooks/*.yml; do echo "Checking $$f..."; $(UV) run ansible-playbook --syntax-check "$$f" || exit 1; done

ansible-lint-playbooks:
	@$(UV) run ansible-lint playbooks/roles || true

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
		fi; \
	done; \
	echo ""; echo "PASSED:$$PASSED"; \
	if [ -n "$$FAILED" ]; then echo "FAILED:$$FAILED"; exit 1; fi; \
	echo "=== molecule-test-all: ALL scenarios passed ==="

clean-tmp:
	@rm -rf /tmp/gludd-iso-* /tmp/gludd-gate-basetemp /tmp/gludd-winfix*-gate.log /tmp/gludd-test-gate.txt /tmp/pytest-of-* 2>/dev/null || true
	@rm -rf /private/tmp/gludd-iso-* /private/tmp/pytest-of-* 2>/dev/null || true
	@echo "clean-tmp done"

# Proactive ENOSPC guard: reclaim scratch + venvs + already-merged worktrees,
# then FAIL FAST if free space is still under FLOOR (default 2048 MiB) so a gate
# or agent batch can never silently drive the volume to the ENOSPC deadlock.
# Run `make disk-guard` before every gate / agent wave.
disk-guard:
	@# SAFE reclaim ONLY — regenerable scratch + venvs. NEVER removes a worktree
	@# (a worktree may hold UNSYNCED agent work; --force here once destroyed 5
	@# fixes). Worktree teardown is a SEPARATE, deliberate step (wt-prune-safe,
	@# no --force) done only AFTER the work is synced. This guard just reclaims
	@# scratch and refuses the heavy op below the floor.
	@# Also reap stale orphaned gludd daemons/test-servers (memory + held ports)
	@# before the heavy op — kill-stale is self-tree- and active-daemon-safe.
	@$(MAKE) --no-print-directory kill-stale || true
	@rm -rf /tmp/gludd-iso-* /tmp/gludd-gate-basetemp /private/tmp/pytest-of-* 2>/dev/null || true
	@rm -rf /Users/shawnwilson/gludd/.claude/worktrees/agent-*/.venv 2>/dev/null || true
	@FREE=$$(df -m / | awk 'NR==2{print $$4}'); FLOOR=$${FLOOR:-2048}; \
	if [ "$$FREE" -lt "$$FLOOR" ]; then \
		echo "DISK-GUARD FAIL: only $${FREE}MiB free (< $${FLOOR}MiB floor) — refusing heavy op."; \
		echo "  Free space first, e.g.: tmutil deletelocalsnapshots / ; rm -rf ~/Library/Caches/*"; \
		exit 1; \
	fi; \
	echo "disk-guard OK: $${FREE}MiB free (floor $${FLOOR})"

# Disk headroom check — run BEFORE any heavy op (gate, agent dispatch) so we
# never silently refill the volume. Prints % used + free on the data volume.
disk:
	@df -h / | awk 'NR==1 || /\/$$/'
	@echo "--- gludd scratch + worktree venv footprint ---"
	@du -sh /tmp/gludd-* 2>/dev/null | tail -5 || true
	@du -sh /Users/shawnwilson/gludd/.claude/worktrees/agent-*/.venv 2>/dev/null | tail -5 || true

# Remove regenerable .venv dirs from agent worktrees (source is preserved;
# `uv sync` recreates on demand). The main disk hog when many worktree agents run.
clean-worktree-venvs:
	@rm -rf /Users/shawnwilson/gludd/.claude/worktrees/agent-*/.venv 2>/dev/null || true
	@echo "clean-worktree-venvs done"

molecule-clean:
	@echo "Removing stray molecule/<scenario> runtime dirs (any dir directly under molecule/ that is NOT playbooks, roles, internal_tools, mock_daemon, library)..."
	@for d in molecule/*/; do \
		s=$$(basename "$$d"); \
		case "$$s" in \
			playbooks|roles|internal_tools|mock_daemon|library) ;; \
			*) echo "  Removing stray: $$d"; rm -rf "$$d" ;; \
		esac; \
	done
	@echo "molecule-clean done"

molecule-test:
	@if [ -z "$(SCENARIO)" ]; then echo "Usage: make molecule-test SCENARIO=noop|prompt_eval|runtime_validate"; exit 1; fi
	@echo "Running molecule scenario: $(SCENARIO)"
	@rm -rf "molecule/$(SCENARIO)"; \
	mkdir -p "molecule/$(SCENARIO)"; \
	cp "molecule/playbooks/$(SCENARIO)/molecule.yml" "molecule/$(SCENARIO)/"; \
	cp -r "molecule/playbooks/$(SCENARIO)/default" "molecule/$(SCENARIO)/default"; \
	$(UV) run molecule test -s "$(SCENARIO)"; \
	EXIT_CODE=$$?; \
	rm -rf "molecule/$(SCENARIO)"; \
	exit $$EXIT_CODE

git-status:
	@git status --short || echo "Not a git repo"

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
	@git diff --stat || echo "No diff"

repo-diff:
	@git diff --stat || echo "No diff"

git-staged:
	@git diff --cached --stat || echo "Nothing staged"

repo-staged:
	@git diff --cached --stat || echo "Nothing staged"

git-init:
	@git init
	@git config user.email "agent@general-ludd.local" || true
	@git config user.name "General Ludd Agent" || true

status-snapshot:
	@python3 scripts/status_snapshot.py

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

grep:
	@[ -n "$(Q)" ] || { echo "Usage: make grep Q='pattern' [PATH='dir']"; exit 1; }
	@grep -rn -- "$(Q)" $(if $(PATH_),$(PATH_),src tests) || echo "No matches"

git-tracked-keys:
	@echo "=== Tracked files matching private-key / key patterns ==="
	@git ls-files | grep -E 'id_rsa|id_ed25519|\.pem$$|_rsa$$|_rsa\.pub$$|sandboxcom_github' || echo "NONE TRACKED"

git-ls-tracked:
	@git ls-files $(if $(Q),| grep -E "$(Q)",)

git-history-file:
	@[ -n "$(Q)" ] || { echo "Usage: make git-history-file Q='path'"; exit 1; }
	@git log --all --full-history --oneline -- "$(Q)" || echo "No history"

audit-messages:
	@$(PYTHON) scripts/audit_messages.py 2>&1 || echo "No opencode database found"

audit-schema:
	@$(PYTHON) scripts/db_schema.py

deps-audit:
	@echo "=== Dependency Audit (deptry) ==="
	@$(UV) run deptry src --ignore DEP004 || true
	@echo "=== Audit Complete ==="

repo-log:
	@git log --oneline -10 || echo "No git history"

git-add:
	@if [ -z "$(FILES)" ]; then echo "Usage: make git-add FILES='file1 file2 ...'"; exit 1; fi
	@git add $(FILES)

git-add-all:
	@git add -A

# Resolve a conflicted file to HEAD's (--ours) version and stage it — for merges
# where git badly interleaved two independent additions; we re-apply the incoming
# side cleanly by hand afterward.
git-resolve-ours:
	@[ -n "$(FILES)" ] || { echo "Usage: make git-resolve-ours FILES='path'"; exit 1; }
	@git checkout --ours -- $(FILES) && git add $(FILES) && echo "resolved (ours): $(FILES)"

repo-add-all:
	@git add -A

commit-bootstrap:
	@if [ -z "$(MSG)" ]; then echo "Usage: make commit-bootstrap MSG='message'"; exit 1; fi
	@git diff --cached --quiet && echo "Nothing to commit" || git commit -m "$(MSG)"

# Commit staged changes using a message FILE (avoids shell quoting of multi-line
# messages with angle-bracket emails). Enforces the SAME fresh+green gate guard
# as `git-commit` (a bare `git commit -F` would otherwise bypass it). Usage:
#   make git-commit-file FILE=/tmp/msg.txt
git-commit-file:
	@[ -n "$(FILE)" ] || { echo "Usage: make git-commit-file FILE=path"; exit 1; }
	@echo "Running pre-commit collection check..."
	@$(MAKE) --no-print-directory collect-check
	@echo "Collection OK. Checking gate status..."
	@if [ ! -f .gate-status ]; then echo "ERROR: .gate-status missing. Run 'make gate' first."; exit 1; fi
	@for check in lint typecheck collect test smoke; do \
		if ! grep -q "^$${check} PASS" .gate-status; then \
			echo "ERROR: Gate $$check not PASS. Run 'make gate'."; exit 1; \
		fi; \
	done
	@EPOCH=$$(grep "^epoch " .gate-status | awk '{print $$2}'); \
	NOW=$$(date +%s); \
	AGE=$$((NOW - EPOCH)); \
	if [ $$AGE -gt 1800 ]; then \
		echo "ERROR: .gate-status is $$AGE seconds old (>30 min). Run 'make gate'."; exit 1; \
	fi
	@echo "Gate fresh and green. Committing (message file)..."
	@git commit -F "$(FILE)"

smoke:
	@echo "=== SMOKE TEST: real daemon boot ==="
	@PORT=$$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()") && \
	echo "Using port $$PORT" && \
	trap 'kill $$PID 2>/dev/null; sleep 0.3; lsof -ti :$$PORT 2>/dev/null | xargs kill 2>/dev/null; echo "Daemon stopped (cleanup)"' EXIT && \
	PID=$$(GLUDD_PORT=$$PORT $(UV) run python -m general_ludd.cli daemon --port $$PORT --log-level info > /tmp/gludd-smoke.log 2>&1 & echo $$!) && \
	echo "Daemon PID: $$PID" && \
	for i in $$(seq 1 30); do \
		sleep 0.5; \
		curl -sf http://localhost:$$PORT/healthz > /dev/null 2>&1 && break; \
	done && \
	echo "Healthz OK" && \
	curl -sf http://localhost:$$PORT/api/status | python3 -m json.tool && \
	curl -sf -X POST http://localhost:$$PORT/api/todos -H "Content-Type: application/json" \
		-d '{"title":"smoke-test-todo","description":"auto-created by make smoke","queue":"intake","work_type":"code"}' \
		| python3 -m json.tool && \
	curl -sf http://localhost:$$PORT/api/todos | python3 -m json.tool > /dev/null && \
	echo "Todo API OK" && \
	! grep -i "typeerror\|traceback\|swallowed" /tmp/gludd-smoke.log > /dev/null 2>&1 && \
	echo "No startup errors in log" && \
	kill $$PID 2>/dev/null; \
	sleep 0.3; \
	lsof -ti :$$PORT 2>/dev/null | xargs kill 2>/dev/null; \
	echo "Daemon stopped" && \
	trap - EXIT && \
	echo "=== SMOKE: PASSED ==="

install-hooks:
	@PIP_INDEX_URL=https://pypi.org/simple $(UV) run pre-commit install --install-hooks
	@PIP_INDEX_URL=https://pypi.org/simple $(UV) run pre-commit install --hook-type pre-push
	@echo "pre-commit hooks installed: secrets-scan, ruff, collect-check (pre-commit), gate (pre-push)"

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
	@$(UV) run detect-secrets scan --exclude-files 'sandboxcom_github_rsa|sandboxcom_github_rsa.pub' > .secrets.baseline.tmp
	@$(PYTHON) -c "import json; d=json.load(open('.secrets.baseline.tmp')); print('[scan-secrets-baseline] OK: valid JSON, %d files carry flagged (baselined) secrets' % len(d.get('results', {})))"
	@mv -f .secrets.baseline.tmp .secrets.baseline
	@echo "[scan-secrets-baseline] wrote .secrets.baseline ($$(wc -c < .secrets.baseline | tr -d ' ') bytes) -- stage it with: make git-add FILES='.secrets.baseline'"

clean-hooks:
	@rm -f .git/hooks/pre-commit.legacy .git/hooks/pre-push.legacy scripts/githooks/pre-commit scripts/githooks/pre-push
	@-rmdir scripts/githooks 2>/dev/null || true
	@echo "Legacy hooks removed"

clean-untracked:
	@rm -f scripts/scan-secrets.py
	@echo "Cleaned up reinvention-of-wheel files"

git-remote-sandboxcom:
	@chmod 600 sandboxcom_github_rsa
	@GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' git remote add sandboxcom git@github.com:sandboxcom/gludd.git 2>/dev/null || true
	@echo "Remote sandboxcom configured"

git-push-sandboxcom:
	@GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' git push -u sandboxcom master
	@echo "Pushed to sandboxcom/gludd"

git-pull-sandboxcom:
	@GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' git pull --rebase sandboxcom master
	@echo "Pulled and rebased from sandboxcom/gludd"

git-fetch-sandboxcom:
	@GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' git fetch sandboxcom
	@echo "Fetched from sandboxcom/gludd"

# Create an annotated tag and push it to sandboxcom to trigger the tag-gated
# release job (version -> gate -> builds -> release). Usage:
#   make git-tag-push TAG=v0.1.0-alpha.1 MSG='alpha release'
git-tag-push:
	@[ -n "$(TAG)" ] || { echo "Usage: make git-tag-push TAG=v0.1.0-alpha.N [MSG='...']"; exit 1; }
	@git tag -a "$(TAG)" -m "$(if $(MSG),$(MSG),$(TAG))"
	@GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' git push sandboxcom "$(TAG)"
	@echo "Pushed tag $(TAG) to sandboxcom/gludd (triggers release job)"

# --- CI observability (W16) ---
ci-status:
	@gh run list -R sandboxcom/gludd -L 8 2>&1 || echo "gh-run-list-failed"

# Confirm a published GitHub Release + list its downloadable assets.
release-view:
	@[ -n "$(TAG)" ] || { echo "Usage: make release-view TAG=v0.1.0-alpha.1"; exit 1; }
	@gh release view "$(TAG)" -R sandboxcom/gludd --json tagName,name,isDraft,isPrerelease,publishedAt,url,assets 2>&1 | $(PYTHON) -c "import sys,json; d=json.load(sys.stdin); print('RELEASE:', d.get('tagName'), '|', d.get('url')); print('  draft=%s prerelease=%s published=%s' % (d.get('isDraft'), d.get('isPrerelease'), d.get('publishedAt'))); a=d.get('assets',[]); print('  ASSETS (%d):' % len(a)); [print('   -', x['name'], x['size'], 'bytes') for x in a]" || echo "release-view-failed"

ci-faillog:
	@if [ -z "$(RUN)" ]; then echo "Usage: make ci-faillog RUN=<id>"; exit 1; fi
	@gh run view "$(RUN)" -R sandboxcom/gludd --log-failed 2>&1 | tail -120 || echo "ci-faillog-failed"

ci-artifacts:
	@if [ -z "$(RUN)" ]; then echo "Usage: make ci-artifacts RUN=<id>"; exit 1; fi
	@gh api repos/sandboxcom/gludd/actions/runs/$(RUN)/artifacts 2>&1 | $(PYTHON) -c "import sys,json; d=json.load(sys.stdin); a=d.get('artifacts',[]); print('TOTAL ARTIFACTS:', d.get('total_count', len(a))); [print(' -', x['name'], x['size_in_bytes'], 'bytes', '(EXPIRED)' if x.get('expired') else '(live)') for x in a]" || echo "ci-artifacts-failed"

# Integration helper: copy a (red-team-fixed/new) file from an agent worktree
# into the main checkout without routing it through the orchestrator's context.
wt-import:
	@if [ -z "$(SRC)" ] || [ -z "$(DST)" ]; then echo "Usage: make wt-import SRC=path DST=path"; exit 1; fi
	@mkdir -p "$$(dirname "$(DST)")"
	@cp "$(SRC)" "$(DST)" && echo "imported -> $(DST)"

# Merge a whole agent worktree's uncommitted changes into the main checkout:
# copy every modified-tracked + new-untracked file (git-ignored paths like .venv
# are auto-excluded by ls-files --exclude-standard). Skips scratch/redteam docs.
# Usage: make wt-sync SRC=/abs/path/to/worktree-root
wt-sync:
	@[ -n "$(SRC)" ] || { echo "Usage: make wt-sync SRC=<worktree-root>"; exit 1; }
	@REFUSED=0; cd "$(SRC)" && { git diff --name-only HEAD; git ls-files --others --exclude-standard; } | sort -u | while read -r f; do \
		case "$$f" in \
			.venv/*|*.pyc|.gate-status|.gate-failed|REDTEAM_*|*.log|*/__init__.py|__init__.py) continue;; \
		esac; \
		dst="/Users/shawnwilson/gludd/$$f"; \
		if [ -f "$$dst" ] && git -C /Users/shawnwilson/gludd ls-files --error-unmatch "$$f" >/dev/null 2>&1 && ! git -C /Users/shawnwilson/gludd diff --quiet HEAD -- "$$f"; then \
			if ! cmp -s "$(SRC)/$$f" "$$dst"; then \
				echo "  ⛔ REFUSED (CLOBBER GUARD): $$f is locally-modified vs HEAD in main; whole-file copy would lose those edits. Use: make wt-apply SRC=$(SRC) FILES=$$f"; \
				continue; \
			fi; \
		fi; \
		mkdir -p "/Users/shawnwilson/gludd/$$(dirname "$$f")"; \
		cp "$(SRC)/$$f" "$$dst" && echo "  synced $$f"; \
	done
	@echo "wt-sync done: $(SRC) (clobber-guard active: locally-modified files are refused, use wt-apply)"

# Bulk wt-sync a LIST of worktrees (each goes through the clobber-guard + __init__ skip).
# Tolerant: a missing/failed worktree is skipped, the rest continue. Usage:
#   make wt-sync-all SRCS='wt1 wt2 ...'
wt-sync-all:
	@[ -n "$(SRCS)" ] || { echo "Usage: make wt-sync-all SRCS='wt1 wt2 ...'"; exit 1; }
	@for wt in $(SRCS); do \
		if [ -d "$$wt" ]; then echo "=== wt-sync $$wt ==="; $(MAKE) --no-print-directory wt-sync SRC="$$wt" || echo "  (wt-sync failed for $$wt, continuing)"; \
		else echo "  skip (missing): $$wt"; fi; \
	done
	@echo "wt-sync-all done"

# 3-WAY apply ONLY specific files' uncommitted diff from a worktree onto main —
# for files that ALSO have local batch edits (whole-file wt-sync would clobber).
# The worktree shares main's object store, so the HEAD base blob is available and
# git 3-way merges the agent's hunks with the batch's, marking only true overlaps.
# Usage: make wt-apply SRC=<worktree-root> FILES='path1 path2'
wt-apply:
	@[ -n "$(SRC)" ] || { echo "Usage: make wt-apply SRC=<worktree-root> FILES='...'"; exit 1; }
	@[ -n "$(FILES)" ] || { echo "Usage: make wt-apply SRC=<worktree-root> FILES='...'"; exit 1; }
	@cd "$(SRC)" && git diff HEAD -- $(FILES) > /tmp/gludd-wt-apply.patch
	@if [ ! -s /tmp/gludd-wt-apply.patch ]; then echo "wt-apply: empty diff for $(FILES) (untracked? use wt-sync/hand-merge)"; exit 1; fi
	@git -C /Users/shawnwilson/gludd apply --3way --verbose /tmp/gludd-wt-apply.patch \
		&& echo "wt-apply OK (3-way): $(FILES)" \
		|| { echo "wt-apply CONFLICT/FAIL — patch at /tmp/gludd-wt-apply.patch; resolve by hand"; exit 1; }

# Bulk force-remove integrated worktrees (reclaims source + ~320MB venv each).
# Only call with worktrees whose work is already synced/applied into main.
# Usage: make wt-remove-many SRCS='wt1 wt2 ...'
wt-remove-many:
	@[ -n "$(SRCS)" ] || { echo "Usage: make wt-remove-many SRCS='wt1 wt2 ...'"; exit 1; }
	@for wt in $(SRCS); do git worktree remove --force "$$wt" 2>/dev/null && echo "  removed: $$wt" || echo "  skip/fail: $$wt"; done
	@echo "wt-remove-many done"

# Drain the WHOLE integrate+reclaim lane (#62) in one command: for every agent
# worktree, wt-sync its uncommitted changes into main (clobber-guarded) then
# reclaim it. Skip any worktree whose id contains a KEEP token (still-running
# agents) so live work is never destroyed. This is the standing loop that keeps
# the orchestrator from falling behind completed subagents.
# Usage: make wt-reap KEEP='a86c88e5 ae182e55'   (KEEP optional)
wt-reap:
	@keep="$(KEEP)"; reaped=0; kept=0; \
	for wt in /Users/shawnwilson/gludd/.claude/worktrees/agent-*; do \
		[ -d "$$wt" ] || continue; \
		id=$$(basename "$$wt"); skip=0; \
		for k in $$keep; do case "$$id" in *$$k*) skip=1;; esac; done; \
		if [ "$$skip" = 1 ]; then echo "  KEEP (running): $$id"; kept=$$((kept+1)); continue; fi; \
		echo "=== reap $$id ==="; \
		$(MAKE) --no-print-directory wt-sync SRC="$$wt"; \
		git worktree remove --force "$$wt" 2>/dev/null && { echo "  reclaimed $$id"; reaped=$$((reaped+1)); } || echo "  (reclaim skip $$id)"; \
	done; \
	echo "wt-reap done: reaped $$reaped, kept $$kept running; run 'make test-count' when the tree is quiet"

# Read-only: list a worktree's uncommitted changed files (for planning a sync).
wt-changed:
	@[ -n "$(SRC)" ] || { echo "Usage: make wt-changed SRC=<worktree-root>"; exit 1; }
	@cd "$(SRC)" && { git diff --name-only HEAD; git ls-files --others --exclude-standard; } | sort -u | grep -vE '^(\.venv/|REDTEAM_|.*\.log$$)' || echo "(no tracked/untracked changes)"

# Tear down an integrated/redundant agent worktree (reclaims its source + frees
# the branch). --force because agent worktrees carry uncommitted (already-synced)
# changes. Usage: make wt-remove SRC=<worktree-root>
wt-remove:
	@[ -n "$(SRC)" ] || { echo "Usage: make wt-remove SRC=<worktree-root>"; exit 1; }
	@git worktree remove --force "$(SRC)" 2>/dev/null && echo "removed: $(SRC)" || echo "remove skipped/failed: $(SRC)"

# Reclaim disk safely: remove every CLEAN worktree (git refuses any with
# uncommitted changes, so dirty/unsynced ones are preserved). Branch refs always
# persist, so committed feature branches survive removal and can be merged by
# name or re-checked-out later. Protects the main checkout + the orchestrator cwd.
wt-prune-safe:
	@git worktree list --porcelain | awk '/^worktree /{print $$2}' | while read -r wt; do \
		case "$$wt" in \
			*/gludd|*a2fb5d73d80b29494) echo "  protected: $$wt"; continue;; \
		esac; \
		git worktree remove "$$wt" 2>/dev/null && echo "  removed (clean): $$wt" || echo "  kept (dirty/unsynced): $$wt"; \
	done
	@echo "wt-prune-safe done"

# Read-only: which feature/* branches still have work NOT yet in master (the real
# integration backlog). A branch absent here is already merged.
# Merge a branch into the working tree WITHOUT committing (so its changes can be
# gated together with other staged work, then committed once). Aborts cleanly on
# conflict. Usage: make git-merge-nc BR=feature/xxx
git-merge-nc:
	@[ -n "$(BR)" ] || { echo "Usage: make git-merge-nc BR=feature/xxx"; exit 1; }
	@git merge --no-ff --no-commit "$(BR)" && echo "merged (uncommitted): $(BR)" || { echo "MERGE CONFLICT — aborting"; git merge --abort; exit 1; }

wt-prune-force-merged:
	@git worktree list --porcelain | awk '/^worktree /{print $$2}' | while read -r wt; do \
		case "$$wt" in \
			*/gludd|*a2fb5d73d80b29494) echo "  protected: $$wt"; continue;; \
		esac; \
		head=$$(git -C "$$wt" rev-parse HEAD 2>/dev/null); \
		if [ -n "$$head" ] && git merge-base --is-ancestor "$$head" master 2>/dev/null; then \
			git worktree remove --force "$$wt" 2>/dev/null && echo "  removed (merged HEAD): $$wt" || echo "  fail: $$wt"; \
		else \
			echo "  KEPT (unmerged HEAD): $$wt"; \
		fi; \
	done
	@echo "wt-prune-force-merged done"

branches-unmerged:
	@git branch --no-merged master | sed 's/^[+* ]*//' | grep -E '^(feature/|worktree-agent-)' | grep -v worktree-agent || echo "(all feature branches merged)"

# Anti-overstatement tool: the MEASURED pass-rate of recent CI runs, so
# "reliable"/"green" must be quoted as this ratio, never asserted as an adjective.
ci-greenness:
	@gh run list -R sandboxcom/gludd -L 20 --json conclusion,status 2>/dev/null | $(PYTHON) -c "import sys,json; r=json.load(sys.stdin); done=[x for x in r if x.get('status')=='completed']; g=[x for x in done if x.get('conclusion')=='success']; total=len(done); print('CI greenness (last %d completed runs): %d GREEN, %d not-green = %d%%.' % (total, len(g), total-len(g), (100*len(g)//total if total else 0))); print('  -> Do NOT call CI \"reliable/green\" without quoting this ratio.')" || echo "ci-greenness-failed"

# --- enabler targets for parallel verification + wider quality gates ---
# Isolated single-file pytest: unique basetemp so concurrent agent runs never
# collide (the #40 fix). Usage: make test-iso TESTFILE=tests/... ID=<uniq>
test-iso:
	@if [ -z "$(TESTFILE)" ]; then echo "Usage: make test-iso TESTFILE=path [ID=x]"; exit 1; fi
	@BT="/tmp/gludd-iso-$${ID:-$$$$}"; rm -rf "$$BT"; $(UV) run python -m pytest $(TESTFILE) -p no:cacheprovider --basetemp="$$BT" -q; RC=$$?; rm -rf "$$BT"; exit $$RC

# Like test-iso but with the GATE's xdist flags (-n 2 --dist loadgroup) to
# reproduce xdist-only hangs/deadlocks that test-iso (single-process) misses.
test-xdist:
	@if [ -z "$(TESTFILE)" ]; then echo "Usage: make test-xdist TESTFILE=path"; exit 1; fi
	@BT="/tmp/gludd-xdist-$${ID:-$$$$}"; rm -rf "$$BT"; $(UV) run python -m pytest $(TESTFILE) -n 2 --dist loadgroup -p no:cacheprovider --basetemp="$$BT" -q; RC=$$?; rm -rf "$$BT"; exit $$RC

# Full-suite xdist run with a THREAD-method per-test timeout so an uninterruptible
# hang (which the gate's signal-method timeout can't catch) is force-failed and
# NAMED, instead of stalling the whole run. Diagnostic only.
test-hang-debug:
	@BT="/tmp/gludd-hangdbg"; rm -rf "$$BT"; $(UV) run python -m pytest tests/ -n 2 --dist loadgroup -p no:cacheprovider --timeout=100 --timeout-method=thread --basetemp="$$BT" -q -rf; RC=$$?; rm -rf "$$BT"; exit $$RC

# Wider lint/type scope (#35) — measures lint across ALL tracked python, not just src/tests.
lint-all:
	@$(UV) run ruff check src tests collections scripts alembic tools molecule
typecheck-all:
	@$(UV) run mypy src scripts tools
# Ansible/YAML lint (#36), fail-on-error (no `|| true`).
yaml-lint:
	@$(UV) run ansible-lint playbooks collections/ansible_collections/general_ludd/agent/roles

ci-log:
	@if [ -n "$(RUN)" ]; then \
		gh run view -R sandboxcom/gludd $(RUN) --log-failed 2>&1 || echo "gh-run-view-failed"; \
	else \
		gh run view -R sandboxcom/gludd --log-failed 2>&1 || echo "gh-run-view-failed"; \
	fi

ci-watch:
	@gh run watch -R sandboxcom/gludd $(RUN) --exit-status 2>&1 || echo "gh-run-watch-failed"

ci-auth:
	@gh auth status 2>&1 || echo "gh-auth-failed"
	@command -v gh >/dev/null 2>&1 && gh --version || echo "gh-not-installed"

# Probe for any tooling that could read the CI run without gh.
ci-install-gh:
	@command -v gh >/dev/null 2>&1 && { echo "gh already installed: $$(gh --version | head -1)"; exit 0; } || true
	@command -v brew >/dev/null 2>&1 || { echo "brew MISSING — cannot install gh"; exit 1; }
	@echo "Installing gh via brew (may take a minute)..."
	@brew install gh 2>&1 | tail -15 || echo "brew-install-gh-failed"
	@command -v gh >/dev/null 2>&1 && gh --version || echo "gh still missing after install"

ci-pyver-list:
	@$(UV) python list 2>&1 | head -40 || echo "uv-python-list-failed"

ci-ssh-test:
	@chmod 600 sandboxcom_github_rsa 2>/dev/null || true
	@GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' ssh -T -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new git@github.com 2>&1 | head -5 || true

ci-remotes:
	@git remote -v 2>&1 || true

# Compare local HEAD to what sandboxcom/master actually has (what CI ran).
ci-diff-since-remote:
	@echo "--- files changed between sandboxcom/master and HEAD ---"
	@git diff --name-only sandboxcom/master..HEAD 2>&1 || echo "(need fetch first)"

ci-head-compare:
	@echo "--- local HEAD ---"; git rev-parse HEAD
	@echo "--- fetching sandboxcom/master ---"
	@GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' git fetch sandboxcom 2>&1 | tail -3
	@echo "--- sandboxcom/master HEAD ---"; git rev-parse sandboxcom/master 2>&1 || echo "no sandboxcom/master ref"
	@echo "--- commits local has that remote does NOT ---"
	@git log --oneline sandboxcom/master..HEAD 2>&1 || echo "(cannot compute)"

# Unauthenticated API attempt (works only if the repo is public).
ci-status-anon:
	@echo "--- unauthenticated GitHub API (works only if repo public) ---"
	@curl -s -H "Accept: application/vnd.github+json" \
		"https://api.github.com/repos/sandboxcom/gludd/actions/runs?per_page=8" 2>&1 | \
		$(PYTHON) -c "import sys,json; d=json.load(sys.stdin); \
		print('MESSAGE:', d.get('message')) if 'workflow_runs' not in d else [print(r.get('id'), r.get('created_at'), r.get('head_branch'), r.get('status'), r.get('conclusion'), r.get('html_url')) for r in d['workflow_runs']]" 2>&1 || echo "ci-status-anon-failed"

# Show jobs (name + conclusion + step that failed) for a run id, unauthenticated.
ci-jobs-anon:
	@if [ -z "$(RUN)" ]; then echo "Usage: make ci-jobs-anon RUN=<run-id>"; exit 1; fi
	@curl -s -H "Accept: application/vnd.github+json" \
		"https://api.github.com/repos/sandboxcom/gludd/actions/runs/$(RUN)/jobs?per_page=50" 2>&1 | \
		$(PYTHON) -c "import sys,json; d=json.load(sys.stdin); \
		[ (print('JOB', j['id'], j['name'], '->', j['conclusion']), [print('   step FAILED:', s['name']) for s in j.get('steps',[]) if s.get('conclusion') not in ('success','skipped',None)]) for j in d.get('jobs',[]) ]" 2>&1 || echo "ci-jobs-anon-failed"

# Try to fetch a job's log (follows redirect to signed URL; public repos sometimes allow).
ci-annotations-anon:
	@if [ -z "$(RUN)" ]; then echo "Usage: make ci-annotations-anon RUN=<run-id>"; exit 1; fi
	@echo "--- check-runs for run $(RUN) (annotations often hold the failure summary) ---"
	@curl -s -H "Accept: application/vnd.github+json" \
		"https://api.github.com/repos/sandboxcom/gludd/actions/runs/$(RUN)/jobs?per_page=50" 2>&1 | \
		$(PYTHON) -c "import sys,json,urllib.request; d=json.load(sys.stdin); \
		[print('JOB', j['id'], j['name'], j['conclusion'], 'check_run:', j.get('check_run_url','')) for j in d.get('jobs',[]) if j['conclusion'] in ('failure','cancelled')]" 2>&1 || echo "failed"

# Poll a run until the RUN-LEVEL conclusion is terminal, then report it.
# CRITICAL: this waits on the run object's own `status`/`conclusion`, NOT on a
# snapshot of currently-visible jobs. The old version declared "RUN GREEN" as
# soon as the visible jobs (version + the two gates) completed — but this
# workflow has DEPENDENT jobs (artifact build) that only appear AFTER the gates,
# so it reported green while the run actually FAILED. GitHub only sets the run's
# status=completed when the WHOLE run is done and conclusion reflects the true
# outcome (failure if any required job failed) — so a false-green is impossible.
# Exits non-zero on a non-success conclusion so the failure is itself observable.
ci-wait-anon:
	@if [ -z "$(RUN)" ]; then echo "Usage: make ci-wait-anon RUN=<run-id>"; exit 1; fi
	@echo "Polling run $(RUN) until the RUN-LEVEL conclusion is terminal..."
	@while true; do \
		RUNJSON=$$(curl -s -H "Accept: application/vnd.github+json" "https://api.github.com/repos/sandboxcom/gludd/actions/runs/$(RUN)"); \
		STATUS=$$(printf '%s' "$$RUNJSON" | $(PYTHON) -c "import sys,json; d=json.load(sys.stdin); print(d.get('status') or '?')"); \
		CONCL=$$(printf '%s' "$$RUNJSON" | $(PYTHON) -c "import sys,json; d=json.load(sys.stdin); print(d.get('conclusion') or '')"); \
		if [ "$$STATUS" = "completed" ]; then \
			JOBS=$$(curl -s -H "Accept: application/vnd.github+json" "https://api.github.com/repos/sandboxcom/gludd/actions/runs/$(RUN)/jobs?per_page=100"); \
			printf '%s' "$$JOBS" | $(PYTHON) -c "import sys,json; d=json.load(sys.stdin); [print('JOB', j['name'], '->', j['conclusion']) for j in d.get('jobs',[])]"; \
			echo "RUN_CONCLUSION=$$CONCL"; \
			if [ "$$CONCL" = "success" ]; then echo "RUN GREEN"; else echo "RUN NOT GREEN ($$CONCL)"; exit 1; fi; \
			break; \
		fi; \
		echo "$$(date +%H:%M:%S) [heartbeat] run status=$$STATUS conclusion=$${CONCL:-pending} (waiting for run-level completion)"; \
		sleep 20; \
	done

# Resolve an action repo's recent tags -> commit SHAs (public API, no auth) so we
# can pin GitHub Actions to a Node-24-compatible release by full SHA.
gh-tags:
	@if [ -z "$(REPO)" ]; then echo "Usage: make gh-tags REPO=owner/name"; exit 1; fi
	@curl -s -H "Accept: application/vnd.github+json" "https://api.github.com/repos/$(REPO)/tags?per_page=20" | \
		$(PYTHON) -c "import sys,json; d=json.load(sys.stdin); print('MSG:', d.get('message')) if isinstance(d,dict) else [print(t['name'], t['commit']['sha']) for t in d]"

# Print the Node runtime an action declares (node20 vs node24) at a given tag,
# so we pin to the MINIMAL node24 release rather than guessing a major bump.
gh-action-node:
	@if [ -z "$(REPO)" ] || [ -z "$(TAG)" ]; then echo "Usage: make gh-action-node REPO=owner/name TAG=vX"; exit 1; fi
	@echo "$(REPO)@$(TAG):"; curl -s "https://raw.githubusercontent.com/$(REPO)/$(TAG)/action.yml" | grep -i 'using:' || echo "  (no using: line / not found)"

# Discover the CI run for the current git HEAD (waiting if it hasn't registered
# yet — the unauthenticated runs list is cached ~60s), then watch it to its
# RUN-LEVEL conclusion. One self-contained "push and watch" command.
ci-watch-head:
	@SHORT=$$(git rev-parse --short=7 HEAD); \
	echo "Watching CI for HEAD $$SHORT ..."; \
	RUNID=""; \
	for i in $$(seq 1 40); do \
		RUNID=$$(curl -s -H "Accept: application/vnd.github+json" "https://api.github.com/repos/sandboxcom/gludd/actions/runs?per_page=10" | $(PYTHON) -c "import sys,json; d=json.load(sys.stdin); runs=[r for r in d.get('workflow_runs',[]) if r['head_sha'].startswith('$$SHORT')]; print(runs[0]['id'] if runs else '')" 2>/dev/null); \
		if [ -n "$$RUNID" ]; then echo "found run $$RUNID for $$SHORT"; break; fi; \
		echo "$$(date +%H:%M:%S) [waiting] run for $$SHORT not registered yet ..."; sleep 15; \
	done; \
	[ -n "$$RUNID" ] || { echo "no run appeared for $$SHORT"; exit 1; }; \
	$(MAKE) --no-print-directory ci-wait-anon RUN=$$RUNID

ci-checkrun-anno:
	@if [ -z "$(CHECK)" ]; then echo "Usage: make ci-checkrun-anno CHECK=<check-run-id>"; exit 1; fi
	@curl -s -H "Accept: application/vnd.github+json" \
		"https://api.github.com/repos/sandboxcom/gludd/check-runs/$(CHECK)/annotations" 2>&1 | \
		$(PYTHON) -c "import sys,json; d=json.load(sys.stdin); print('NO ANNOTATIONS' if not d else ''); [print(a.get('path'),a.get('start_line'),a.get('annotation_level'),'::',a.get('message','')[:500]) for a in (d if isinstance(d,list) else [])]" 2>&1 || echo "failed"

ci-joblog-anon:
	@if [ -z "$(JOB)" ]; then echo "Usage: make ci-joblog-anon JOB=<job-id>"; exit 1; fi
	@curl -sL -H "Accept: application/vnd.github+json" \
		"https://api.github.com/repos/sandboxcom/gludd/actions/jobs/$(JOB)/logs" -o /tmp/gludd-ci-joblog-$(JOB).txt 2>&1 || echo "download-failed"
	@echo "=== last 120 lines of job $(JOB) log ==="
	@tail -120 /tmp/gludd-ci-joblog-$(JOB).txt 2>&1 || echo "no-log"

ci-probe:
	@echo "--- tool availability ---"
	@command -v gh   >/dev/null 2>&1 && echo "gh: $$(command -v gh)"     || echo "gh: MISSING"
	@command -v brew >/dev/null 2>&1 && echo "brew: $$(command -v brew)" || echo "brew: MISSING"
	@command -v curl >/dev/null 2>&1 && echo "curl: $$(command -v curl)" || echo "curl: MISSING"
	@command -v ssh  >/dev/null 2>&1 && echo "ssh: $$(command -v ssh)"   || echo "ssh: MISSING"
	@echo "--- GH_TOKEN / GITHUB_TOKEN env ---"
	@if [ -n "$$GH_TOKEN" ]; then echo "GH_TOKEN set"; elif [ -n "$$GITHUB_TOKEN" ]; then echo "GITHUB_TOKEN set"; else echo "no github token env var"; fi

# Try the GitHub REST API for the latest workflow runs (needs a token with repo read on sandboxcom/gludd).
ci-status-api:
	@TOKEN="$${GH_TOKEN:-$$GITHUB_TOKEN}"; \
	if [ -z "$$TOKEN" ]; then echo "no GH_TOKEN/GITHUB_TOKEN — cannot call API"; exit 0; fi; \
	curl -sf -H "Authorization: Bearer $$TOKEN" -H "Accept: application/vnd.github+json" \
		"https://api.github.com/repos/sandboxcom/gludd/actions/runs?per_page=8" 2>&1 | \
		$(PYTHON) -c "import sys,json; d=json.load(sys.stdin); [print(r['created_at'], r['head_branch'], r['status'], r['conclusion'], r['html_url']) for r in d.get('workflow_runs',[])]" 2>&1 || echo "ci-status-api-failed"

# --- Cross-version CI reproduction (W16) ---
# Reproduce the CI gate under a specific python version (CI runs 3.11 and 3.12).
test-pyver:
	@if [ -z "$(VER)" ]; then echo "Usage: make test-pyver VER=3.11"; exit 1; fi
	@echo "=== test-pyver $(VER): syncing ==="
	@$(UV) sync --python $(VER)
	@echo "=== test-pyver $(VER): ruff ==="
	@$(UV) run --python $(VER) ruff check src tests
	@echo "=== test-pyver $(VER): mypy ==="
	@$(UV) run --python $(VER) mypy src
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
	@$(UV) sync --python $(VER)
	@echo "=== ci-test-1worker $(VER): pytest -n 1 --dist loadgroup (CI ubuntu worker count) ==="
	@$(UV) run --python $(VER) python -m pytest tests/ -n 1 --dist loadgroup -q 2>&1 | tail -50

# Run the EXACT CI gate command sequence under a given python version:
#   uv sync --python VER  &&  make lint typecheck test-count test smoke
# This includes coverage (fail_under=70) which plain test-pyver omits.
ci-gate-exact:
	@if [ -z "$(VER)" ]; then echo "Usage: make ci-gate-exact VER=3.11"; exit 1; fi
	@echo "=== ci-gate-exact $(VER): uv sync ==="
	@$(UV) sync --python $(VER)
	@echo "=== ci-gate-exact $(VER): lint ==="
	@$(UV) run --python $(VER) ruff check src tests
	@echo "=== ci-gate-exact $(VER): typecheck ==="
	@$(UV) run --python $(VER) mypy src
	@echo "=== ci-gate-exact $(VER): test-count ==="
	@$(UV) run --python $(VER) python -m pytest tests/ --co -q 2>&1 | tail -3
	@echo "=== ci-gate-exact $(VER): test (WITH coverage, fail_under=70) ==="
	@$(UV) run --python $(VER) python -m pytest tests/ --cov=general_ludd --cov-report=term-missing --cov-report=xml $(_XD) -q 2>&1 | tail -40
	@echo "=== ci-gate-exact $(VER): DONE (check coverage line above) ==="

# Simulate the CI version-injection + uv sync path to detect lockfile staleness.
ci-version-sim:
	@echo "=== ci-version-sim: injecting PEP440 version then uv sync --locked ==="
	@cp pyproject.toml /tmp/gludd-pyproject.bak
	@cp src/general_ludd/__init__.py /tmp/gludd-init.bak
	@VER="0.1.0a$$(date -u +%Y%m%d%H%M)"; \
		sed -i.tmp "s/__version__ = \".*\"/__version__ = \"$$VER\"/" src/general_ludd/__init__.py; \
		sed -i.tmp "s/^version = \".*\"/version = \"$$VER\"/" pyproject.toml; \
		rm -f pyproject.toml.tmp src/general_ludd/__init__.py.tmp; \
		echo "Injected version $$VER"; \
		echo "--- uv sync --locked (does lockfile go stale?) ---"; \
		$(UV) sync --locked 2>&1 | tail -20; EXIT=$$?; \
		echo "uv sync --locked exit: $$EXIT"; \
		echo "--- uv sync (plain, what CI uses) ---"; \
		$(UV) sync 2>&1 | tail -10; \
		cp /tmp/gludd-pyproject.bak pyproject.toml; \
		cp /tmp/gludd-init.bak src/general_ludd/__init__.py; \
		echo "Restored pyproject.toml + __init__.py"

scan-secrets:
	@$(UV) run detect-secrets scan --baseline .secrets.baseline $(ARGS)

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

git-commit:
	@if [ -z "$(MSG)" ]; then echo "Usage: make git-commit MSG='message'"; exit 1; fi
	@echo "Running pre-commit collection check..."
	@$(MAKE) --no-print-directory collect-check
	@echo "Collection OK. Checking gate status..."
	@if [ ! -f .gate-status ]; then echo "ERROR: .gate-status missing. Run 'make gate' first."; exit 1; fi
	@for check in lint typecheck collect test smoke; do \
		if ! grep -q "^$${check} PASS" .gate-status; then \
			echo "ERROR: Gate $$check not PASS. Run 'make gate'."; exit 1; \
		fi; \
	done
	@EPOCH=$$(grep "^epoch " .gate-status | awk '{print $$2}'); \
	NOW=$$(date +%s); \
	AGE=$$((NOW - EPOCH)); \
	if [ $$AGE -gt 1800 ]; then \
		echo "ERROR: .gate-status is $$AGE seconds old (>30 min). Run 'make gate'."; exit 1; \
	fi
	@echo "Gate fresh and green. Committing..."
	@git diff --cached --quiet && echo "Nothing to commit" || git commit -m "$(MSG)"

repo-commit:
	@if [ -z "$(MSG)" ]; then echo "Usage: make repo-commit MSG='message'"; exit 1; fi
	@git diff --cached --quiet && echo "Nothing to commit" || git commit -m "$(MSG)"

delete-file:
	@[ -n "$(FILES)" ] || { echo "Usage: make delete-file FILES='file1 file2'"; exit 1; }
	@$(RM) $(FILES)

patch-test:
	@[ -n "$(FILE)" ] || { echo "Usage: make patch-test FILE='path' MATCH='old' REPLACE='new'"; exit 1; }
	@python3 -c "import sys; c=open('$(FILE)').read(); c=c.replace('$(MATCH)','$(REPLACE)'); open('$(FILE)','w').write(c)"

fix-benchmark-mock:
	@python3 -c "c=open('tests/unit/test_daemon_coverage_lift.py').read(); c=c.replace('class TestBenchmarkRecordWithSession:\n    @pytest.mark.asyncio\n    async def test_benchmark_record_with_session(self, app, transport):\n        mock_session = MagicMock()\n        mock_sf = MagicMock()','class TestBenchmarkRecordWithSession:\n    @pytest.mark.asyncio\n    async def test_benchmark_record_with_session(self, app, transport):\n        mock_session = MagicMock()\n        mock_session.commit = AsyncMock()\n        mock_sf = MagicMock()'); open('tests/unit/test_daemon_coverage_lift.py','w').write(c)"
	@echo "Fixed benchmark mock"

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
	@if [ -n "$(MSG)" ]; then \
		git reset $(MSG); \
	elif [ -n "$(FILES)" ]; then \
		git reset $(FILES); \
	else \
		echo "Usage: make git-reset MSG='--hard <ref>'  or  make git-reset FILES='HEAD~1'"; \
		exit 1; \
	fi

git-cherry-pick:
	@if [ -z "$(MSG)" ]; then echo "Usage: make git-cherry-pick MSG='<commit>'"; exit 1; fi
	@git cherry-pick $(MSG)

git-cherry-continue:
	@GIT_EDITOR=true git cherry-pick --continue

git-branch:
	@if [ -z "$(MSG)" ]; then echo "Usage: make git-branch MSG='branch-name'"; exit 1; fi
	@git branch "$(MSG)"

git-checkout:
	@if [ -z "$(MSG)" ]; then echo "Usage: make git-checkout MSG='branch-name'"; exit 1; fi
	@git checkout "$(MSG)"

git-merge:
	@if [ -z "$(MSG)" ]; then echo "Usage: make git-merge MSG='branch-name'"; exit 1; fi
	@git merge --no-ff "$(MSG)"

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

preflight:
	@echo "========================================"
	@echo "  PREFLIGHT QUALITY GATE"
	@echo "========================================"
	@$(UV) run python -c "import json, sys; from general_ludd.quality.preflight import run_preflight; r = run_preflight(); json.dump(r, sys.stdout, indent=2); sys.exit(0 if r['overall'] == 'PASS' else 1)"

test-and-commit:
	@echo "Running preflight checks..."
	@$(MAKE) preflight
	@echo "Running tests before commit..."
	@$(UV) run python -m pytest tests/ $(_XD) --cov=general_ludd -q
	@echo "Preflight passed. Tests passed. Committing..."
	@git add -A
	@if [ -n "$(MSG)" ]; then \
		git diff --cached --quiet && echo "Nothing to commit" || git commit -m "$(MSG)"; \
	else \
		git diff --cached --quiet && echo "Nothing to commit" || git commit -m "agent: test-green $(shell date +%Y%m%d%H%M%S)"; \
	fi
	@echo "Committed."
	@$(MAKE) dist

clean:
	@rm -rf .venv dist build *.egg-info src/*.egg-info .pytest_cache .mypy_cache .coverage coverage.xml htmlcov .ruff_cache
	@find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@git rm -r --cached '*__pycache__*' 2>/dev/null || true
	@git rm --cached .coverage coverage.xml 2>/dev/null || true
	@echo "Cleaned."

test-live-zai:
	@echo "Running live Z.AI integration tests..."
	@_zai_key=$$(python3 -c "import json,os; print(json.load(open(os.path.expanduser('~/.local/share/opencode/auth.json'))).get('zai-coding-plan',{}).get('key',''))") && \
	ZAI_API_KEY="$$_zai_key" ZAI_BASE_URL="https://open.bigmodel.cn/api/paas/v4" ZAI_MODEL="glm-5.1" \
	$(UV) run python -m pytest tests/live/test_zai_live.py -v -s

test-zai-identity:
	@echo "Running authenticated Z.AI identity test..."
	@_zai_key=$$(python3 -c "import json,os; print(json.load(open(os.path.expanduser('~/.local/share/opencode/auth.json'))).get('zai-coding-plan',{}).get('key',''))") && \
	ZAI_API_KEY="$$_zai_key" ZAI_BASE_URL="https://open.bigmodel.cn/api/paas/v4" ZAI_MODEL="glm-5.1" \
	$(UV) run python -m pytest tests/live/test_zai_identity.py -v -s

CONTAINER_RUNTIME := $(shell command -v podman 2>/dev/null || command -v docker 2>/dev/null)
CONTAINER_IMAGE := gl-agent:latest

VERSION := $(shell $(UV) run python -c "from general_ludd import __version__; print(__version__)")
PLATFORM := $(shell uname -s)-$(shell uname -m)
TARBALL_NAME := general-ludd-agent-$(VERSION)-$(PLATFORM)
TARBALL_DIR := dist/$(TARBALL_NAME)

build-executable:
	@$(UV) run pyinstaller gludd.spec --clean --noconfirm
	@echo "Built dist/gludd"

verify-status:
	@$(UV) run python scripts/verify_status.py

dist: build-executable bundle-binaries sbom
	@echo "Assembling tarball..."
	@chmod +x dist/install.sh
	@rm -rf $(TARBALL_DIR)
	@mkdir -p $(TARBALL_DIR)
	@cp dist/gludd $(TARBALL_DIR)/gludd
	@cp dist/install.sh $(TARBALL_DIR)/install.sh
	@cp dist/general-ludd.service $(TARBALL_DIR)/general-ludd.service
	@cp dist/README.md $(TARBALL_DIR)/README.md
	@cp -r config $(TARBALL_DIR)/config
	@cp -r templates $(TARBALL_DIR)/templates
	@cp -r dist/binaries $(TARBALL_DIR)/binaries 2>/dev/null || true
	@echo "Packing license + SBOM (W5.2)..."
	@cp LICENSE $(TARBALL_DIR)/LICENSE
	@if [ ! -f THIRD_PARTY_LICENSES.md ]; then echo "ERROR: THIRD_PARTY_LICENSES.md missing"; exit 1; fi
	@cp THIRD_PARTY_LICENSES.md $(TARBALL_DIR)/THIRD_PARTY_LICENSES.md
	@echo "Scrubbing build-machine paths from SBOM (W5.3)..."
	@$(UV) run python -c "import re,pathlib; p=pathlib.Path('dist/sbom.json'); t=p.read_text(); import os; t=t.replace('file://'+os.getcwd(),'file:///opt/general-ludd').replace(os.getcwd(),'/opt/general-ludd'); pathlib.Path('$(TARBALL_DIR)/sbom.json').write_text(t)"
	@mkdir -p $(TARBALL_DIR)/docs
	@if [ -f docs/quickstart.md ]; then cp docs/quickstart.md $(TARBALL_DIR)/docs/; fi
	@if [ -f docs/configuration.md ]; then cp docs/configuration.md $(TARBALL_DIR)/docs/; fi
	@if [ -f docs/architecture.md ]; then cp docs/architecture.md $(TARBALL_DIR)/docs/; fi
	@if [ -f docs/model-setup.md ]; then cp docs/model-setup.md $(TARBALL_DIR)/docs/; fi
	@echo "Verifying no build-machine paths leaked into the tarball dir (W5.3)..."
	@if grep -rIl -e '/Users/' -e 'Mac.localdomain' $(TARBALL_DIR) 2>/dev/null; then \
		echo "ERROR: absolute local paths leaked into $(TARBALL_DIR)"; exit 1; \
	else echo "Tarball dir is path-clean."; fi
	@cd dist && tar czf $(TARBALL_NAME).tar.gz $(TARBALL_NAME)
	@cd dist && shasum -a 256 $(TARBALL_NAME).tar.gz > $(TARBALL_NAME).tar.gz.sha256
	@echo "Created dist/$(TARBALL_NAME).tar.gz"
	@echo "Checksum: dist/$(TARBALL_NAME).tar.gz.sha256"

dist-clean:
	@rm -rf dist/general-ludd-agent-* dist/hottentot-agent-* dist/gludd dist/hottentot build

bundle-binaries: bundle-ripgrep
	@echo "Bundling OpenBao and OpenTofu binaries into dist/binaries..."
	@mkdir -p dist/binaries
	@$(UV) run python scripts/download_bundled_binaries.py || echo "Some binaries could not be downloaded (network unavailable?). The dist will still include what was bundled."

# Bundle a SHA-pinned, musl-static ripgrep (BurntSushi) into dist/binaries/rg.
# The musl-static x86_64-linux build is fully self-contained (no libc dep), which
# is what the dist tarball / container ships. The download is checksum-verified
# fail-closed: if shasum -c does not match RG_SHA256 the staged binary is removed
# and the target exits non-zero, so a corrupted/MITM'd download can never be
# bundled. Locating it at runtime is handled by BinaryBootstrapper.get_bundled_
# binary_path('rg') -> dist/binaries/rg (see code_intelligence/rg_search.py).
RG_VERSION ?= 14.1.1
RG_PLATFORM ?= x86_64-unknown-linux-musl
RG_ARCHIVE := ripgrep-$(RG_VERSION)-$(RG_PLATFORM).tar.gz
RG_URL := https://github.com/BurntSushi/ripgrep/releases/download/$(RG_VERSION)/$(RG_ARCHIVE)
# TODO: fill in the real sha256 of $(RG_ARCHIVE) for $(RG_VERSION)/$(RG_PLATFORM).
# Obtain it from the release page / `shasum -a 256 $(RG_ARCHIVE)`. Until set to a
# real value, bundle-ripgrep fails closed (the placeholder will never verify).
RG_SHA256 ?= 0000000000000000000000000000000000000000000000000000000000000000
bundle-ripgrep:
	@echo "Bundling ripgrep $(RG_VERSION) ($(RG_PLATFORM)) into dist/binaries/rg..."
	@mkdir -p dist/binaries
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT && \
		echo "  downloading $(RG_URL)" && \
		curl -fsSL "$(RG_URL)" -o "$$tmp/$(RG_ARCHIVE)" && \
		echo "$(RG_SHA256)  $$tmp/$(RG_ARCHIVE)" > "$$tmp/rg.sha256" && \
		if ! shasum -a 256 -c "$$tmp/rg.sha256"; then \
			echo "ERROR: ripgrep checksum mismatch (expected $(RG_SHA256)) — refusing to bundle"; \
			exit 1; \
		fi && \
		tar xzf "$$tmp/$(RG_ARCHIVE)" -C "$$tmp" && \
		cp "$$tmp/ripgrep-$(RG_VERSION)-$(RG_PLATFORM)/rg" dist/binaries/rg && \
		chmod +x dist/binaries/rg && \
		echo "  bundled -> dist/binaries/rg" || \
		{ echo "WARNING: ripgrep bundle failed (network unavailable or sha unset?); search degrades to in-process"; rm -f dist/binaries/rg; }

container-build:
	@if [ -z "$(CONTAINER_RUNTIME)" ]; then echo "ERROR: podman or docker not found"; exit 1; fi
	@$(CONTAINER_RUNTIME) build -t $(CONTAINER_IMAGE) .

container-run:
	@if [ -z "$(CONTAINER_RUNTIME)" ]; then echo "ERROR: podman or docker not found"; exit 1; fi
	@$(CONTAINER_RUNTIME) run -p 8000:8000 $(CONTAINER_IMAGE)

container-push:
	@if [ -z "$(CONTAINER_RUNTIME)" ]; then echo "ERROR: podman or docker not found"; exit 1; fi
	@$(CONTAINER_RUNTIME) push $(CONTAINER_IMAGE)

# Reproduce CI's Linux "Gate" step locally — no GitHub login needed. Runs the
# EXACT CI command (make lint typecheck test-count test smoke) inside a Linux
# python container so platform-specific failures (tests skipped on macOS but run
# on Linux, etc.) surface here directly instead of only in CI. PYV=3.11|3.12.
# Uses a container-local venv (UV_PROJECT_ENVIRONMENT) so the host macOS .venv is
# never touched. Streams via tee (observability invariant).
# Ensure the podman Linux VM is initialised and running (macOS needs a VM to run
# Linux containers). Idempotent: init/start fail harmlessly if already done.
podman-up:
	@command -v podman >/dev/null 2>&1 || { echo "podman not installed"; exit 1; }
	@podman machine init 2>/dev/null || true
	@podman machine start 2>/dev/null || true
	@# A stale default connection (e.g. an old Lima VM) hijacks `podman run`;
	@# force the running podman machine to be the default so containers actually run.
	@podman system connection default podman-machine-default 2>/dev/null || true
	@podman machine list

podman-restart:
	@podman machine stop 2>/dev/null || true
	@podman machine start
	@podman system connection default podman-machine-default 2>/dev/null || true
	@podman machine list

VMEM ?= 4096
VCPU ?= 4
# Recreate the podman VM from scratch with the requested resources. The default
# 2GiB VM crashed (and self-destructed) under the full test suite; a fresh VM
# with more memory is more reliable than trying to resize a dead one.
podman-resize:
	@podman machine rm -f podman-machine-default 2>/dev/null || true
	@podman machine init --memory $(VMEM) --cpus $(VCPU) 2>/dev/null || true
	@podman machine start
	@podman system connection default podman-machine-default 2>/dev/null || true
	@podman machine list

podman-diag:
	@echo "--- machine list ---"; podman machine list 2>&1 || true
	@echo "--- connection list ---"; podman system connection list 2>&1 || true
	@echo "--- info (host socket) ---"; podman info --format '{{.Host.RemoteSocket.Path}}' 2>&1 | head -3 || true

PYV ?= 3.11
ci-repro-linux:
	@if [ -z "$(CONTAINER_RUNTIME)" ]; then echo "ERROR: no docker/podman found — cannot reproduce the Linux gate locally"; exit 1; fi
	@echo "=== Reproducing CI Linux gate (python $(PYV)) via $(CONTAINER_RUNTIME) ==="
	@# slim base: tiny pull (avoids the I/O error unpacking the full image's giant
	@# static libs); uv downloads its own python like CI's setup-uv, so the base
	@# python barely matters. rc file preserves the container's real exit through
	@# the tee pipe (observability invariant: the pipe must not swallow failures).
	@rm -f /tmp/gludd-ci-repro-rc; \
	( $(CONTAINER_RUNTIME) run --rm -v "$$(pwd)":/work -w /work \
		-e UV_PROJECT_ENVIRONMENT=/opt/venv-linux -e GLUDD_PSK="" \
		python:$(PYV)-slim-bookworm bash -c "set -e; \
			echo '--- installing make/lsof/curl/git + uv ---'; \
			apt-get update -qq && apt-get install -y -qq make lsof curl procps git >/dev/null; \
			pip install -q uv; \
			echo '--- uv sync (python $(PYV), container-local venv) ---'; \
			uv sync --python $(PYV); \
			echo '--- running CI gate command ---'; \
			make lint typecheck test-count test smoke"; echo $$? > /tmp/gludd-ci-repro-rc ) 2>&1 | tee /tmp/gludd-ci-repro-$(PYV).log; \
	RC=$$(cat /tmp/gludd-ci-repro-rc 2>/dev/null || echo 1); \
	echo "=== ci-repro-linux exit=$$RC ==="; exit $$RC

sast:
	@mkdir -p dist
	@$(UV) run bandit -r src/ -f json -o dist/sast-report.json || true
	@$(UV) run bandit -r src/ -f custom || true

sbom:
	@mkdir -p dist
	@$(UV) run cyclonedx-py environment .venv -o dist/sbom.json --of JSON

# Informational full audit (shows every advisory, never gates).
pip-audit:
	@$(UV) run pip-audit --desc || true

# Gating audit (W5.3): fail-closed on any NEW advisory. The two known,
# adjudicated advisories are ignored with a documented rationale in
# SECURITY.md "Known dependency advisories":
#   - CVE-2025-69872 (diskcache): no upstream fix; mitigated by owner-only
#     (0o700) cache dir in models/response_cache.py — attacker needs cache-dir
#     write access, which the permission removes.
#   - PYSEC-2026-196 (pip): build-time installer only; pip is NOT a runtime
#     dependency (not in pyproject) and is absent from the shipped PyInstaller
#     binary; fixed pip 26.1.2 is used in CI/dev.
pip-audit-gate:
	@echo "=== pip-audit (gating, W5.3) — fails on NEW advisories ==="
	@$(UV) run pip-audit --desc \
		--ignore-vuln CVE-2025-69872 \
		--ignore-vuln PYSEC-2026-196
	@echo "=== pip-audit-gate: no un-adjudicated advisories ==="

pip-upgrade:
	@PIP_INDEX_URL=https://pypi.org/simple $(UV) run python -m pip install --upgrade 'pip>=26.1.2'
	@$(UV) run python -m pip --version

security: sast sbom pip-audit

qa: lint typecheck test healthcheck
	@echo "QA gate passed."

validate: lint ansible-syntax healthcheck
	@ERRS=$$($(UV) run mypy src 2>&1 | grep -c 'error:'); ERRS=$${ERRS:-0}; \
	if [ "$$ERRS" -le "$(MYPY_MAX)" ]; then echo "typecheck: OK ($$ERRS errors, baseline $(MYPY_MAX))"; else echo "typecheck: FAIL ($$ERRS errors > baseline $(MYPY_MAX))"; exit 1; fi
	@$(UV) run python -m pytest tests/ $(_XD) -q > /tmp/gludd-validate.txt 2>&1; EXIT=$$?; \
	if [ $$EXIT -eq 0 ]; then echo "test: PASS"; else echo "test: FAIL (non-zero exit)"; exit 1; fi
	@$(MAKE) --no-print-directory smoke > /dev/null 2>&1 && echo "smoke: PASS" || (echo "smoke: FAIL" && exit 1)
	@$(MAKE) --no-print-directory audit-evidence > /dev/null 2>&1 && echo "audit-evidence: PASS" || (echo "audit-evidence: FAIL" && exit 1)
	@echo "Full validation passed."

bootstrap: init lint test healthcheck
	@echo "Bootstrap complete."

db-sample-message:
	@sqlite3 $(OPENCODE_DB) "SELECT substr(m.data, 1, 500) FROM message m LIMIT 3;" 2>/dev/null

db-sample-part:
	@sqlite3 $(OPENCODE_DB) "SELECT substr(p.data, 1, 500) FROM part p LIMIT 3;" 2>/dev/null
	@sqlite3 $(OPENCODE_DB) ".schema" 2>/dev/null

db-tables:
	@sqlite3 $(OPENCODE_DB) ".tables" 2>/dev/null

db-count:
	@sqlite3 $(OPENCODE_DB) "SELECT COUNT(*) FROM message;" 2>/dev/null

search-opencode:
	@sqlite3 $(OPENCODE_DB) "SELECT json_extract(m.data, '$$.role'), json_extract(p.data, '$$.text') FROM message m JOIN part p ON m.id = p.message_id WHERE json_extract(m.data, '$$.role')='user' AND json_extract(p.data, '$$.text') LIKE '%$(SEARCH)%' LIMIT $(MAX_RESULTS);" 2>/dev/null

collect-prompts:
	@echo "Collecting system prompts from open-source coding agents..."
	@$(UV) run python scripts/collect_prompts.py --output-dir config/prompt_profiles/collected
	@echo "Done. Run 'make collect-prompts SOURCE=aider' for a specific agent."

NAME ?= mp-diagnose

skill-list:
	@$(UV) run $(PYTHON) -c "from general_ludd.skills.catalog import SkillCatalog; cat = SkillCatalog(); [print(f'  {s.name:30s} {s.category:15s} {s.description[:60]}') for s in cat.search(limit=100)]"

skill-install:
	@$(UV) run $(PYTHON) -c "from general_ludd.skills.catalog import SkillCatalog; cat = SkillCatalog(); path = cat.install_skill('$(NAME)', '.opencode/skills'); print(f'Installed: {path}') if path else print(f'Skill not found: $(NAME)')"

bootstrap-skills:
	@echo "Installing default mattpocock skills..."
	@$(UV) run $(PYTHON) scripts/bootstrap_skills.py

analyze-jsonl:
	@python3 /tmp/analyze_tools.py

list-tests:
	@find tests -name 'test_*.py' -type f | sort

dogfood:
	@$(UV) run python scripts/dogfood.py

dogfood-features:
	@$(UV) run python scripts/dogfood_features.py

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
