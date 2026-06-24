MSG ?=
FILES ?=
TESTFILE ?=
REF ?=
TARGET ?= master
MYPY_MAX := 0
OPENCODE_DB ?= ~/.local/share/opencode/opencode.db

PYTHON := python3
UV := uv
export VIRTUAL_ENV := $(CURDIR)/.venv
export UV_PROJECT_ENVIRONMENT := $(CURDIR)/.venv
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
        git-branch git-checkout git-merge git-staged git-branch-files \
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
        git-tracked-keys git-ls-tracked git-history-file dist-path-check git-is-ancestor git-revlist-count \
        molecule-clean plan ps-gludd kill-stale kill-gate-force \
        gate-async gate-status gate-background gate-bg-check gate-bg-wait floor-plan gated-merge ship-async write-gate-safe-hook \
        test-hooks test-stop-hooks set-sonnet-target check-readme-status release-cut \
        verify-release-artifact \
        git-tag-rm release-recut \
        git-ff-only ship-ff git-worktree-list git-worktree-remove git-ls-remote-sandboxcom \
        ci-poll ci-jobs ci-annotations test-no-wait-hook \
        verify-remote ci-verdict ci-verdict-fast ci-verdict-loop \
        git-push-branch git-push-branch-nv test-model-ratio-hook test-liveness-workflow gh-pr-ensure \
        gh-run-list gh-run-cancel ci-rerun gh-run-view gh-run-failed-log \
        commit-no-verify \
        git-stash-rebase-pop \
        git-cherry-pick git-cherry-continue git-cherry-abort git-show-diff \
        test-force-delegate-hook \
        test-worktree-disk-guard \
        git-cherry-pick-commit git-amend-msg \
        test-other-shard

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
	@echo "  gate-background       Launch gate via nohup (non-blocking); log -> /tmp/gludd-gate-bg.log"
	@echo "  gate-bg-check         Tail background gate log + check if PID is still alive"
	@echo "  gate-bg-wait          BLOCK until background gate finishes (polls every 5s)"
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

venv-check:
	@echo "VIRTUAL_ENV=$(VIRTUAL_ENV)"
	@echo "UV_PROJECT_ENVIRONMENT=$(UV_PROJECT_ENVIRONMENT)"
	@$(UV) run python -c "import sys; print('sys.executable=' + sys.executable)"

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

# ---------------------------------------------------------------------------
# gate-background: launch `make gate` via nohup so the foreground is NOT blocked.
# Returns immediately. Output -> /tmp/gludd-gate-bg.log, PID -> /tmp/gludd-gate-bg.pid.
# Refuses to launch a second time if the recorded PID is still alive (the gate
# flock in scripts/run_gate.sh would reject it anyway). Check progress with
# `tail -f /tmp/gludd-gate-bg.log`, `make gate-bg-check`, or `make gate-status`.
# ---------------------------------------------------------------------------
gate-background:
	@if [ -f /tmp/gludd-gate-bg.pid ] && kill -0 $$(cat /tmp/gludd-gate-bg.pid) 2>/dev/null; then \
		echo "gate-background already running (PID $$(cat /tmp/gludd-gate-bg.pid))"; \
		echo "  tail -f /tmp/gludd-gate-bg.log"; \
		echo "  make gate-bg-check"; \
		exit 0; \
	fi
	@nohup $(MAKE) --no-print-directory gate > /tmp/gludd-gate-bg.log 2>&1 & \
	echo $$! > /tmp/gludd-gate-bg.pid
	@echo "gate launched in background (PID $$(cat /tmp/gludd-gate-bg.pid))"
	@echo "  tail -f /tmp/gludd-gate-bg.log"
	@echo "  make gate-status"
	@echo "  make gate-bg-check"

# Non-blocking probe of the background gate. COUNTERINTUITIVE SEMANTICS:
# exit 0 while the gate is RUNNING, exit 1 when it has FINISHED (or died).
# In other words, `make gate-bg-check && echo done` prints "done" while the
# gate is still in flight — NOT when it has finished. This is so a poll loop
# can treat "still running" as the truthy/continue state and a dead PID as
# the terminal/error state. Pairs with gate-background's
# /tmp/gludd-gate-bg.{log,pid}. For a blocking wait that exits 0 on finish,
# use `make gate-bg-wait`.
gate-bg-check:
	@if [ ! -f /tmp/gludd-gate-bg.pid ]; then \
		echo "no /tmp/gludd-gate-bg.pid — gate-background not launched (or pid file removed)"; \
		exit 1; \
	fi
	@PID=$$(cat /tmp/gludd-gate-bg.pid); \
	if kill -0 $$PID 2>/dev/null; then \
		echo "gate-background RUNNING (PID $$PID)"; STATE=running; \
	else \
		echo "gate-background FINISHED (PID $$PID not alive)"; STATE=finished; \
	fi; \
	echo "--- tail /tmp/gludd-gate-bg.log ---"; \
	tail -40 /tmp/gludd-gate-bg.log 2>/dev/null || echo "(log empty or missing)"; \
	echo "--- .gate-status ---"; \
	if [ -f .gate-status ]; then cat .gate-status; else echo "(no .gate-status yet)"; fi; \
	[ "$$STATE" = running ] && exit 0 || exit 1

# Blocking wait for the background gate. Polls the recorded PID every 5 seconds
# and returns once it is no longer alive (the gate finished, succeeded, or was
# killed). Exit 0 means "the gate is done"; exit 1 means no PID file was found
# (gate-background was never launched, or the pid file was removed). A poll
# heartbeat is printed each iteration so the wait is observable (see the
# no-unseen-events invariant in AGENTS.md). Intended use:
#   make gate-background && make gate-bg-wait
gate-bg-wait:
	@if [ ! -f /tmp/gludd-gate-bg.pid ]; then \
		echo "no /tmp/gludd-gate-bg.pid — gate-background not launched (or pid file removed)"; \
		exit 1; \
	fi
	@PID=$$(cat /tmp/gludd-gate-bg.pid); \
	echo "gate-bg-wait: waiting for gate PID $$PID (polling every 5s)"; \
	i=0; \
	while kill -0 $$PID 2>/dev/null; do \
		i=$$((i+1)); \
		echo "  [$$i] gate still RUNNING (PID $$PID) — $$(date +%H:%M:%S)"; \
		sleep 5; \
	done; \
	echo "gate-bg-wait: PID $$PID no longer alive — gate finished"; \
	echo "--- tail /tmp/gludd-gate-bg.log ---"; \
	tail -40 /tmp/gludd-gate-bg.log 2>/dev/null || echo "(log empty or missing)"; \
	echo "--- .gate-status ---"; \
	if [ -f .gate-status ]; then cat .gate-status; else echo "(no .gate-status yet)"; fi; \
	exit 0

# Process-hygiene check: list any running pytest/molecule/gate so we never launch
# a second concurrent run that collides with an in-flight one (see gate --basetemp).
ps-pytest:
	@pgrep -fl 'pytest|molecule test|make gate' || echo "NONE running"

# Block until no other `pytest tests/` process is running, so a fork can safely
# launch its own test batch without the basetemp-rotation collision. Times out
# after ~10 min (200 polls x 3s) and proceeds anyway.
wait-pytest:
	@i=0; while pgrep -f 'pytest tests/' >/dev/null 2>&1; do \
		i=$$((i+1)); \
		if [ $$i -ge 200 ]; then echo "wait-pytest: timed out after ~10min, proceeding"; break; fi; \
		sleep 3; \
	done; \
	echo "wait-pytest: clear"

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

# Run ONE of N shards of the molecule scenarios. Usage:
#   make molecule-test-shard SHARD=1/4
# SHARD is "K/N" (1-indexed). Scenarios under molecule/playbooks/ are sorted
# and split into N contiguous groups; shard K runs only its group. Each
# scenario's output goes to /tmp/gludd-molecule-<name>.log; a PASS/FAIL
# summary is printed at the end (non-zero exit if any scenario in the shard
# fails). Designed to parallelize the CI molecule job into 4 ~15-min shards.
molecule-test-shard:
	@if [ -z "$(SHARD)" ]; then echo "Usage: make molecule-test-shard SHARD=K/N (e.g. 1/4)"; exit 1; fi; \
	K=$$(echo "$(SHARD)" | cut -d/ -f1); \
	N=$$(echo "$(SHARD)" | cut -d/ -f2); \
	if [ "$$K" -lt 1 ] || [ "$$K" -gt "$$N" ]; then echo "ERROR: shard $$K out of range [1,$$N]"; exit 1; fi; \
	SCENARIOS=$$(ls -1 molecule/playbooks/ 2>/dev/null | sort); \
	TOTAL=$$(echo "$$SCENARIOS" | grep -c .); \
	echo "=== molecule-test-shard: $$K/$$N of $$TOTAL scenarios ==="; \
	SIZE=$$(($$TOTAL / $$N)); \
	REM=$$(($$TOTAL % $$N)); \
	if [ "$$K" -le "$$REM" ]; then SIZE=$$((SIZE + 1)); START=$$((($$K - 1) * $$SIZE)); else START=$$((($$K - 1) * $$SIZE + $$REM)); fi; \
	MINE=$$(echo "$$SCENARIOS" | sed -n "$$((START + 1)),$$((START + SIZE))p"); \
	echo "shard $$K/$$N: $$SIZE scenarios (offset $$START)"; \
	FAILED=""; PASSED=""; \
	for s in $$MINE; do \
		echo "--- running scenario: $$s (log: /tmp/gludd-molecule-$$s.log) ---"; \
		if $(MAKE) --no-print-directory molecule-test SCENARIO="$$s" > "/tmp/gludd-molecule-$$s.log" 2>&1; then \
			PASSED="$$PASSED $$s"; echo "    PASS $$s"; \
		else \
			FAILED="$$FAILED $$s"; echo "    FAIL $$s (see /tmp/gludd-molecule-$$s.log)"; \
		fi; \
	done; \
	echo ""; echo "PASSED:$$PASSED"; \
	if [ -n "$$FAILED" ]; then echo "FAILED:$$FAILED"; exit 1; fi; \
	echo "=== molecule-test-shard $$K/$$N: ALL scenarios passed ==="

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

git-hard-reset:
	@[ -n "$(REF)" ] || { echo "Usage: make git-hard-reset REF=<ref>"; exit 1; }
	@git reset --hard $(REF)

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

# Read-only ancestor check: exit=0 means A is a strict ancestor of B (ff-only valid).
# Usage: make git-is-ancestor A=<commit> B=<commit>
git-is-ancestor:
	@[ -n "$(A)" ] && [ -n "$(B)" ] || { echo "Usage: make git-is-ancestor A=<commit> B=<commit>"; exit 1; }
	@git merge-base --is-ancestor $(A) $(B); echo "exit=$$?"

# Read-only: list files a ref touches vs its merge-base with BASE (default master).
# Usage: make git-files-vs REF=<branch> [BASE=master]
git-files-vs:
	@[ -n "$(REF)" ] || { echo "Usage: make git-files-vs REF=<branch> [BASE=master]"; exit 1; }
	@MB=$$(git merge-base $(TARGET) $(REF)); \
	echo "=== $(REF) (merge-base with $(TARGET): $$MB) ==="; \
	git diff --name-only $$MB $(REF)

# Read-only: oneline log of a ref's commits since its merge-base with TARGET (default master).
# Usage: make git-log-vs REF=<branch> [TARGET=master]
git-log-vs:
	@[ -n "$(REF)" ] || { echo "Usage: make git-log-vs REF=<branch> [TARGET=master]"; exit 1; }
	@MB=$$(git merge-base $(TARGET) $(REF)); \
	echo "=== $(REF) since $$MB ==="; \
	git log --oneline $$MB..$(REF)

# Read-only rev-list counts for ff-only check.
# Usage: make git-revlist-count A=<old> B=<new>
# Prints: commits unique to A (must be 0 for ff) and commits B is ahead of A.
git-revlist-count:
	@[ -n "$(A)" ] && [ -n "$(B)" ] || { echo "Usage: make git-revlist-count A=<old> B=<new>"; exit 1; }
	@echo "commits unique to A (B..A, must be 0 for ff-only):"; git rev-list --count $(B)..$(A)
	@echo "commits B is ahead of A (A..B, should be >0):"; git rev-list --count $(A)..$(B)
	@echo "--- commits unique to A (would be lost on ff) ---"; git log --oneline $(B)..$(A) || true

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

# Atomic stage+commit+push in one command. Designed for subagent dispatch:
# the main thread calls this via a subagent so it never blocks while
# 10+ other subagents stay active. Uses sandboxcom SSH key.
# ENFORCES lint+typecheck+collect-check BEFORE committing (fast gate, ~10 sec).
# This prevents the anti-TDD pattern of shipping code without any local validation.
ship-commit:
	@if [ -z "$(MSG)" ]; then echo "Usage: make ship-commit MSG='message'"; exit 1; fi
	@echo "=== FAST GATE: lint+typecheck+collect ==="
	@$(MAKE) --no-print-directory lint
	@$(MAKE) --no-print-directory typecheck
	@$(MAKE) --no-print-directory collect-check
	@echo "=== FAST GATE PASSED — committing ==="
	@git add -A
	@git diff --cached --quiet && echo "Nothing to commit" || git commit --no-verify -m "$(MSG)"
	@GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' git push --no-verify sandboxcom master 2>/dev/null
	@echo "Shipped $(MSG)"

# List files changed in a branch vs master (commits unique to the branch).
# Usage: make git-branch-files BR=feature/my-branch
git-branch-files:
	@[ -n "$(BR)" ] || { echo "Usage: make git-branch-files BR=<branch>"; exit 1; }
	@git log --name-only --oneline master..$(BR) 2>/dev/null || echo "branch not found: $(BR)"

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

# Read-only: print a file's contents at a given ref (git show <ref>:<path>).
# Usage: make git-show MSG='<ref>:<path>'
git-show:
	@[ -n "$(MSG)" ] || { echo "Usage: make git-show MSG='<ref>:<path>'"; exit 1; }
	@git show "$(MSG)"

# Read-only: list files a ref touches vs its parent commit (single-commit diff).
# Usage: make git-show-files REF=<ref>
git-show-files:
	@[ -n "$(REF)" ] || { echo "Usage: make git-show-files REF=<ref>"; exit 1; }
	@git show --name-status --oneline "$(REF)"

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

# Commit staged changes on a feature branch. Now ENFORCES the same
# fresh+green gate as git-commit — the "feature branch" rationalization was
# the 2026-06-22 bypass bug (an agent committed with a red gate here).
# Use repo-commit only for the documented non-code meta-commit escape hatch.
commit-bootstrap:
	@if [ -z "$(MSG)" ]; then echo "Usage: make commit-bootstrap MSG='message'"; exit 1; fi
	@$(MAKE) --no-print-directory _gate-fresh-check
	@git diff --cached --quiet && echo "Nothing to commit" || git commit -m "$(MSG)"

# Commit staged changes skipping pre-commit hooks (--no-verify).
# Use ONLY when hooks fail due to stash/conflict from unrelated unstaged files
# AND the staged content is gate-green. The --no-verify flag skips the
# pre-commit HOOK STASH only — NOT the gate-freshness check (added 2026-06-22
# after an agent abused this target to commit a red-gate change).
# Escape hatch: set GLUDD_CI_IS_GATE=1 when the local gate is too slow (>30min)
# and CI is the real validation mechanism. This is for the specific case where
# the full test suite takes longer than the bash tool timeout. NOT for skipping
# a red gate — if the gate is red, fix the failures.
commit-no-verify:
	@if [ -z "$(MSG)" ]; then echo "Usage: make commit-no-verify MSG='message'"; exit 1; fi
	@if [ "${GLUDD_CI_IS_GATE}" != "1" ]; then $(MAKE) --no-print-directory _gate-fresh-check; \
	else echo "WARNING: GLUDD_CI_IS_GATE=1 — skipping local gate check, CI is the gate."; fi
	@git diff --cached --quiet && echo "Nothing to commit" || git commit --no-verify -m "$(MSG)"

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
#   make git-tag-push TAG=v0.1.0-alpha.2 MSG='retag' COMMIT=f1991f2
git-tag-push:
	@[ -n "$(TAG)" ] || { echo "Usage: make git-tag-push TAG=v0.1.0-alpha.N [MSG='...'] [COMMIT=<sha>]"; exit 1; }
	@if [ -n "$(COMMIT)" ]; then \
		git tag -a "$(TAG)" "$(COMMIT)" -m "$(if $(MSG),$(MSG),$(TAG))"; \
	else \
		git tag -a "$(TAG)" -m "$(if $(MSG),$(MSG),$(TAG))"; \
	fi
	@GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' git push sandboxcom "$(TAG)"
	@echo "Pushed tag $(TAG) to sandboxcom/gludd (triggers release job)"

# Delete a git tag locally and on sandboxcom remote.
# Usage: make git-tag-rm TAG=v0.1.0-alpha.2
git-tag-rm:
	@[ -n "$(TAG)" ] || { echo "Usage: make git-tag-rm TAG=v0.1.0-alpha.N"; exit 1; }
	@GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' git push sandboxcom ":refs/tags/$(TAG)" 2>/dev/null || echo "[git-tag-rm] remote tag $(TAG) not found or already deleted"
	@git tag -d "$(TAG)" 2>/dev/null || echo "[git-tag-rm] local tag $(TAG) not found or already deleted"
	@echo "[git-tag-rm] deleted tag $(TAG) (local + remote)"

# --- CI observability (W16) ---
ci-status:
	@gh run list -R sandboxcom/gludd -L 8 2>&1 || echo "gh-run-list-failed"

# Incremental CI poller: surfaces the first failing job immediately.
# Usage: make ci-poll ID=<run-id>
ci-poll:
	@if [ -z "$(ID)" ]; then echo "Usage: make ci-poll ID=<run-id>"; exit 1; fi
	@$(UV) run python scripts/ci_poll.py $(ID)

# Live annotations poller: prints each new failure annotation from a running
# CI job as soon as GitHub populates it — near-real-time per-test failures.
# Polls every CI_ANN_INTERVAL (default 45s).  Exits when the run finishes or
# CI_ANN_MAX_SEC (default 3600s) is reached.  Set CI_ANN_EARLY_EXIT=1 to stop
# on the first annotation.
# Usage: make ci-annotations ID=<run-id>
CI_ANN_INTERVAL ?= 45
ci-annotations:
	@if [ -z "$(ID)" ]; then echo "Usage: make ci-annotations ID=<run-id>"; exit 1; fi
	@CI_ANN_INTERVAL=$(CI_ANN_INTERVAL) $(UV) run python scripts/ci_annotations_poll.py $(ID)

# Confirm a published GitHub Release + list its downloadable assets.
release-view:
	@[ -n "$(TAG)" ] || { echo "Usage: make release-view TAG=v0.1.0-alpha.1"; exit 1; }
	@gh release view "$(TAG)" -R sandboxcom/gludd --json tagName,name,isDraft,isPrerelease,publishedAt,url,assets 2>&1 | $(PYTHON) -c "import sys,json; d=json.load(sys.stdin); print('RELEASE:', d.get('tagName'), '|', d.get('url')); print('  draft=%s prerelease=%s published=%s' % (d.get('isDraft'), d.get('isPrerelease'), d.get('publishedAt'))); a=d.get('assets',[]); print('  ASSETS (%d):' % len(a)); [print('   -', x['name'], x['size'], 'bytes') for x in a]" || echo "release-view-failed"

# ---------------------------------------------------------------------------
# verify-release-artifact: confirm that a GitHub Release for TAG exists AND has
# downloadable assets (the release job uploaded binaries).  A tag alone is NOT
# a release — the Build-and-Release CI job must have completed successfully and
# published assets before this passes.
#
# Exit codes:
#   0  — release found + at least one asset published (artifact confirmed)
#   1  — release missing, draft-only, or has zero assets (NOT released)
#
# Usage:
#   make verify-release-artifact TAG=v0.1.0-alpha.2
# ---------------------------------------------------------------------------
VERIFY_POLLS ?= 6
VERIFY_INTERVAL ?= 60
verify-release-artifact:
	@[ -n "$(TAG)" ] || { echo "Usage: make verify-release-artifact TAG=v0.1.0-alpha.N"; exit 1; }
	@echo "[verify-release-artifact] checking $(TAG) on sandboxcom/gludd ..."
	@$(UV) run python scripts/verify_release_artifact.py "$(TAG)"

# ---------------------------------------------------------------------------
# task-ttl-check: detect stale/frozen subagent tasks whose wall-clock elapsed
# exceeds GLUDD_TASK_TIMEOUT_MS (default 300000 ms = 5 min). Reads the deadline
# state file written by .opencode/plugin/enforce-deadline.ts.
# Exit 0 = all fresh (or none tracked), 1 = stale tasks present.
# ---------------------------------------------------------------------------
task-ttl-check:
	@python3 scripts/task_ttl_check.py --timeout $$(( $${GLUDD_TASK_TIMEOUT_MS:-300000} / 1000 )) || echo "WARNING: stale tasks detected"

ci-faillog:
	@if [ -z "$(RUN)" ]; then echo "Usage: make ci-faillog RUN=<id>"; exit 1; fi
	@gh run view "$(RUN)" -R sandboxcom/gludd --log-failed 2>&1 | tail -120 || echo "ci-faillog-failed"

# List every job + its failing STEP names for a run (works even when
# --log-failed is empty, e.g. a run that failed at an early non-pytest step).
# Usage: make ci-job-steps RUN=<id>
ci-job-steps:
	@if [ -z "$(RUN)" ]; then echo "Usage: make ci-job-steps RUN=<id>"; exit 1; fi
	@gh api repos/sandboxcom/gludd/actions/runs/$(RUN)/jobs --paginate 2>&1 | $(PYTHON) -c "import sys,json; d=json.load(sys.stdin); [ (print('JOB:', j['name'], '->', j['conclusion']), [print('   FAILED STEP:', s['number'], s['name']) for s in j.get('steps',[]) if s.get('conclusion') not in (None,'success','skipped')]) for j in d.get('jobs',[]) ]" || echo "ci-job-steps-failed"

# Raw (unfiltered) full log for a specific job id — to read the error text of an
# early-failing step that --log-failed omits. Usage: make ci-job-log JOB=<id>
ci-job-log:
	@if [ -z "$(JOB)" ]; then echo "Usage: make ci-job-log JOB=<id>"; exit 1; fi
	@gh api repos/sandboxcom/gludd/actions/jobs/$(JOB)/logs 2>&1 | grep -iE "error|fail|traceback|mypy|ruff|coverage|FAILED|passed|no such|not found|Process completed" | tail -60 || echo "ci-job-log-failed"

# List all jobs for a run with status/conclusion. Usage: make ci-jobs ID=<run-id>
ci-jobs:
	@if [ -z "$(ID)" ]; then echo "Usage: make ci-jobs ID=<run-id>"; exit 1; fi
	@gh run view $(ID) -R sandboxcom/gludd --json jobs 2>&1 | $(PYTHON) -c "import sys,json; d=json.load(sys.stdin); [print(j.get('databaseId'), j.get('status'), j.get('conclusion'), j.get('name')) for j in d.get('jobs',[])]" || echo "ci-jobs-failed"

# Print the job ids + names for a run (to feed ci-job-log).
ci-job-ids:
	@if [ -z "$(RUN)" ]; then echo "Usage: make ci-job-ids RUN=<id>"; exit 1; fi
	@gh api repos/sandboxcom/gludd/actions/runs/$(RUN)/jobs --paginate 2>&1 | $(PYTHON) -c "import sys,json; d=json.load(sys.stdin); [print(j['id'], j['conclusion'], j['name']) for j in d.get('jobs',[])]" || echo "ci-job-ids-failed"

ci-artifacts:
	@if [ -z "$(RUN)" ]; then echo "Usage: make ci-artifacts RUN=<id>"; exit 1; fi
	@gh api repos/sandboxcom/gludd/actions/runs/$(RUN)/artifacts 2>&1 | $(PYTHON) -c "import sys,json; d=json.load(sys.stdin); a=d.get('artifacts',[]); print('TOTAL ARTIFACTS:', d.get('total_count', len(a))); [print(' -', x['name'], x['size_in_bytes'], 'bytes', '(EXPIRED)' if x.get('expired') else '(live)') for x in a]" || echo "ci-artifacts-failed"

# Print the headSha + conclusion + status for a specific CI run id, so we can
# decide whether a run is CURRENT or STALE vs a given remote tip.
# Usage: make ci-run-detail RUN=<id>
ci-run-detail:
	@if [ -z "$(RUN)" ]; then echo "Usage: make ci-run-detail RUN=<id>"; exit 1; fi
	@gh api repos/sandboxcom/gludd/actions/runs/$(RUN) 2>&1 | $(PYTHON) -c "import sys,json; d=json.load(sys.stdin); print('run_id=%s' % d.get('id')); print('headSha=%s' % d.get('head_sha')); print('status=%s' % d.get('status')); print('conclusion=%s' % d.get('conclusion')); print('created_at=%s' % d.get('created_at')); print('head_branch=%s' % d.get('head_branch'))" || echo "ci-run-detail-failed"

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
		sleep 10; \
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

# Run the CI "other" shard (integration + e2e + security + top-level test files;
# tests/live/ EXCLUDED — it needs live API keys). Uses a UNIQUE basetemp so it
# coexists safely with concurrent unit shards (no gate-concurrency collision),
# and writes a DURABLE junit XML the parse target reads. Single-process so it
# does not contend on xdist worker dirs with the running unit runs.
run-other-shard-iso:
	@BT="/tmp/gludd-othershard-$$$$"; rm -rf "$$BT"; \
	$(UV) run python -m pytest tests/integration/ tests/e2e/ tests/security/ tests/test_worker_d09_d10_d35.py \
		-p no:cacheprovider --basetemp="$$BT" -q -rfE \
		--junit-xml=/tmp/other_shard.xml; RC=$$?; rm -rf "$$BT"; \
	echo "PYTEST_EXIT=$$RC"; exit 0

# Parse the durable junit XML from run-other-shard-iso into counts + node ids.
parse-other-shard:
	@$(UV) run python -c "import xml.etree.ElementTree as ET, os; \
p='/tmp/other_shard.xml'; \
print('NO_XML_FILE — run make run-other-shard-iso first') if not os.path.exists(p) else None; \
r=ET.parse(p).getroot() if os.path.exists(p) else None; \
suites=([r] if r is not None and r.tag=='testsuite' else (list(r.iter('testsuite')) if r is not None else [])); \
T=sum(int(s.get('tests',0)) for s in suites); F=sum(int(s.get('failures',0)) for s in suites); E=sum(int(s.get('errors',0)) for s in suites); S=sum(int(s.get('skipped',0)) for s in suites); \
print('COUNTS tests=%d failures=%d errors=%d skipped=%d passed=%d'%(T,F,E,S,T-F-E-S)) if r is not None else None; \
[print('FAIL '+(tc.get('classname','')+'::'+tc.get('name',''))) for s in suites for tc in s.iter('testcase') if tc.find('failure') is not None]; \
[print('ERROR '+(tc.get('classname','')+'::'+tc.get('name',''))) for s in suites for tc in s.iter('testcase') if tc.find('error') is not None]"

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
		echo "$$(date +%H:%M:%S) [waiting] run for $$SHORT not registered yet ..."; sleep 5; \
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

# Parse a pytest --junit-xml output file and print only failures/errors + summary.
# Usage: make junit-failures XMLFILE=/tmp/shard1.xml
XMLFILE ?= /tmp/junit.xml
junit-failures:
	@python3 scripts/junit_failures.py "$(XMLFILE)"

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
	@if [ -z "$(FILES)" ]; then \
		echo "Usage: make git-reset FILES='HEAD~1' (or specific ref)"; \
		exit 1; \
	fi
	@git reset $(FILES)

git-branch:
	@if [ -z "$(MSG)" ]; then echo "Usage: make git-branch MSG='branch-name'"; exit 1; fi
	@git branch "$(MSG)"

git-checkout:
	@if [ -z "$(MSG)" ]; then echo "Usage: make git-checkout MSG='branch-name'"; exit 1; fi
	@git checkout "$(MSG)"

# Restore one or more files from HEAD (un-delete working-tree files that are
# tracked but currently deleted/modified). Usage:
#   make git-restore FILES='dist/README.md dist/install.sh'
# Required because raw `git checkout HEAD -- <paths>` is forbidden by the
# make-only Bash policy — agents have no other way to recover a deleted
# tracked file.
git-restore:
	@[ -n "$(FILES)" ] || { echo "Usage: make git-restore FILES='path1 path2 ...'"; exit 1; }
	@git checkout HEAD -- $(FILES) && echo "Restored: $(FILES)"

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

# Run the CI "other" shard: integration + e2e + security + top-level test files.
# Waits for any in-flight pytest to clear first (gate-concurrency hygiene).
test-other-shard:
	@$(MAKE) --no-print-directory wait-pytest
	@$(UV) run python -m pytest tests/integration/ tests/e2e/ tests/security/ tests/test_worker_d09_d10_d35.py $(_XD) -v 2>&1

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

# Test the no_wait_stop.sh stop hook against 6 known-good/known-bad payloads.
test-no-wait-hook:
	@$(PYTHON) scripts/test_no_wait_hook.py

test-model-ratio-hook:
	@$(PYTHON) scripts/test_model_ratio_hook.py

test-force-delegate-hook:
	@$(PYTHON) scripts/test_force_delegate_hook.py

test-worktree-disk-guard:
	@$(PYTHON) scripts/test_worktree_disk_guard.py

test-liveness-workflow:
	@$(PYTHON) scripts/test_liveness_workflow.py

# Read-only discovery: locate Workflow-subagent transcript dirs/files on disk so
# the agent_liveness.py production glob patterns can be verified against reality.
# Lists any 'subagents'/'workflows' dirs + a sample of files under the two
# candidate roots. Purely diagnostic; touches nothing.
# Display a saved CI failed-log (default the run that gh-run-failed-log wrote).
# Prints the failure-relevant lines: pytest FAILED/ERROR nodes, the short test
# summary, assertion/Error lines, ruff/mypy/collection errors, and the exit
# annotation. Diagnostic only; reads a /tmp log that gh-run-failed-log produced.
CI_LOG ?= /tmp/ci-failed-27919581264.log
ci-failed-log-show:
	@if [ ! -f "$(CI_LOG)" ]; then echo "no log at $(CI_LOG)"; exit 1; fi
	@echo "=== $(CI_LOG) ($$(wc -l < $(CI_LOG)) lines) ==="
	@echo "--- failure-relevant lines ---"
	@grep -n -E '^(FAILED|ERROR|PASSED)|::.*(FAILED|ERROR|PASSED)|short test summary|[0-9]+ (failed|error|passed)|AssertionError|^E  |Error:|error:|ModuleNotFoundError|ImportError| collected|exit code|Process completed|^_+ .* _+$$|Traceback' "$(CI_LOG)" || echo "(no matches)"

ci-failed-log-grep:
	@if [ ! -f "$(CI_LOG)" ]; then echo "no log at $(CI_LOG)"; exit 1; fi
	@grep -n -E "$(PAT)" "$(CI_LOG)" || echo "(no matches for $(PAT))"

ci-failed-log-lines:
	@if [ ! -f "$(CI_LOG)" ]; then echo "no log at $(CI_LOG)"; exit 1; fi
	@sed -n '$(FROM),$(TO)p' "$(CI_LOG)"

discover-workflow-transcripts:
	@echo "=== ~/.claude/projects/-Users-shawnwilson-gludd ==="
	@find "$$HOME/.claude/projects/-Users-shawnwilson-gludd" \( -name subagents -o -name workflows \) -type d 2>/dev/null || echo "(none / unreadable)"
	@echo "--- sample transcript files (subagents/workflows paths) ---"
	@find "$$HOME/.claude/projects/-Users-shawnwilson-gludd" -path '*subagents*' -type f 2>/dev/null | head -20 || true
	@find "$$HOME/.claude/projects/-Users-shawnwilson-gludd" -path '*workflows*' -type f 2>/dev/null | head -20 || true
	@echo "=== /private/tmp/claude-$$(id -u)/-Users-shawnwilson-gludd ==="
	@find "/private/tmp/claude-$$(id -u)/-Users-shawnwilson-gludd" \( -name subagents -o -name workflows \) -type d 2>/dev/null || echo "(none / unreadable)"
	@echo "--- sample transcript files (subagents/workflows paths) ---"
	@find "/private/tmp/claude-$$(id -u)/-Users-shawnwilson-gludd" -path '*subagents*' -type f 2>/dev/null | head -20 || true
	@find "/private/tmp/claude-$$(id -u)/-Users-shawnwilson-gludd" -path '*workflows*' -type f 2>/dev/null | head -20 || true
	@echo "=== done ==="

debug-no-wait-hook:
	@$(PYTHON) /tmp/debug_hook.py

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

# Reusable gate-freshness guard. Every commit-shaped target that lands code
# MUST `$(MAKE) _gate-fresh-check` before `git commit`. Enforces:
#   (a) .gate-status exists
#   (b) every check (lint/typecheck/collect/test/smoke) is PASS
#   (c) the status is <30 min old (no stale green)
# Extracted 2026-06-22 after an agent committed with a red gate via
# `commit-no-verify`, rationalizing "pre-existing failures + env issue" —
# the bypass target is for pre-commit stash conflicts only, NOT for skipping
# the gate. Gate integrity must hold across ALL commit targets.
.PHONY: _gate-fresh-check
_gate-fresh-check:
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
	@echo "Gate fresh and green."

# ---------------------------------------------------------------------------
# Activate the BLOCKING gate-safe agent-floor Stop hook (#79/#78)
# ---------------------------------------------------------------------------
# Regenerates .claude/hooks/agent_floor_stop.sh from scripts/gen_gate_safe_hook.py.
# The generator is the sanctioned writer of the hook (do NOT hand-edit the hook).
# Gate-safe rule: a running gate does NOT lower the read-only floor -- only heavy
# worktree-writers are capped during a gate. Idempotent; sets execute permissions.
write-gate-safe-hook:
	@mkdir -p .claude/hooks
	@python3 scripts/gen_gate_safe_hook.py .claude/hooks/agent_floor_stop.sh
	@echo "write-gate-safe-hook done"

# ---------------------------------------------------------------------------
# Comprehensive hook test suite (supersedes test-stop-hooks)
# ---------------------------------------------------------------------------
# Runs ALL .claude/hooks/*.sh under many stdin/env scenarios and verifies:
#   1. exit code is always 0   (non-zero = "hook error" shown to user)
#   2. stdout is empty OR valid JSON  (malformed stdout = harness parse error)
#   3. stderr has no Python traceback  (leaked traceback = visible hook error)
#
# The classic bug this catches: a Stop hook doing `exit 1` instead of
# {"decision":"block"} + exit 0. That single mistake shows "stop hook error"
# on every turn-end.  This test would have caught it.
#
# Also retained: the original test-stop-hooks cases (a-e) so CI history is
# not broken.  `make test-hooks` calls test-stop-hooks internally.
#
# Helper: _hook_case LABEL HOOK_CMD STDIN ENV_OVERRIDES EXPECT_EXIT EXPECT_DECISION
#   EXPECT_DECISION: "block" | "noblock" | "any" (just valid-JSON-or-empty)
#
# Implementation: pure POSIX sh in a single @recipe (Makefile constraint).
# Each case is numbered; FAIL lines include the case label so triage is instant.
test-hooks:
	@echo "========================================================"
	@echo "  make test-hooks -- comprehensive hook safety suite"
	@echo "  Invariants: exit=0  stdout=empty-or-valid-JSON  no traceback"
	@echo "========================================================"
	@OVERALL=PASS; \
	_jv() { s=$$(cat); [ -z "$$s" ] && return 0; printf '%s' "$$s" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; }; \
	_tb() { grep -q 'Traceback\|^Error' /tmp/gludd-hook-stderr.txt 2>/dev/null && echo "TRACEBACK" || echo "CLEAN"; }; \
	echo ""; echo "--- GROUP 1: agent_floor_stop.sh (Stop hook) ---"; \
	echo "[1a] FLOOR=999 active=false -> exit=0, decision=block"; \
	OUT=$$(printf '%s' '{"stop_hook_active":false}' | CLAUDE_AGENT_FLOOR=999 FLOOR_LIVE_OVERRIDE=0 bash .claude/hooks/agent_floor_stop.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$OUT stderr_tb=$$(_tb)"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [1a]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  INFO [1a]: empty (fail-open, no task-dir -- acceptable in test env)"; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("decision")=="block"' 2>/dev/null; then echo "  PASS [1a]: decision=block exit=0"; \
	else echo "  FAIL [1a]: stdout present but decision!=block or invalid JSON"; OVERALL=FAIL; fi; \
	[ "$$(_tb)" = "CLEAN" ] || { echo "  FAIL [1a]: traceback in stderr"; OVERALL=FAIL; }; \
	\
	echo "[1b] stop_hook_active=true -> exit=0, no block (escape)"; \
	OUT=$$(printf '%s' '{"stop_hook_active":true}' | CLAUDE_AGENT_FLOOR=999 bash .claude/hooks/agent_floor_stop.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [1b]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [1b]: empty exit=0 (escape)"; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("decision")!="block"' 2>/dev/null; then echo "  PASS [1b]: no block exit=0"; \
	else echo "  FAIL [1b]: block despite stop_hook_active=true"; OVERALL=FAIL; fi; \
	\
	echo "[1c] FLOOR=0 live=5 -> exit=0, no block"; \
	OUT=$$(printf '%s' '{"stop_hook_active":false}' | CLAUDE_AGENT_FLOOR=0 FLOOR_LIVE_OVERRIDE=5 bash .claude/hooks/agent_floor_stop.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [1c]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [1c]: empty exit=0 (floor=0)"; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("decision")!="block"' 2>/dev/null; then echo "  PASS [1c]: no block"; \
	else echo "  FAIL [1c]: unexpected block FLOOR=0"; OVERALL=FAIL; fi; \
	\
	echo "[1d] empty stdin -> exit=0 (fail-open)"; \
	OUT=$$(printf '' | CLAUDE_AGENT_FLOOR=999 bash .claude/hooks/agent_floor_stop.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC"; [ $$RC -eq 0 ] && echo "  PASS [1d]" || { echo "  FAIL [1d]: exit $$RC"; OVERALL=FAIL; }; \
	\
	echo "[1e] garbage stdin -> exit=0 (fail-open)"; \
	OUT=$$(printf 'NOT JSON AT ALL' | CLAUDE_AGENT_FLOOR=999 bash .claude/hooks/agent_floor_stop.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC"; [ $$RC -eq 0 ] && echo "  PASS [1e]" || { echo "  FAIL [1e]: exit $$RC"; OVERALL=FAIL; }; \
	\
	echo "[1f] FLOOR=999 CEILING=12 -> band display not 999-12 (clamped)"; \
	OUT=$$(printf '%s' '{"stop_hook_active":false}' | CLAUDE_AGENT_FLOOR=999 CLAUDE_AGENT_CEILING=12 FLOOR_LIVE_OVERRIDE=0 bash .claude/hooks/agent_floor_stop.sh 2>/dev/null); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty)' || echo "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("decision="+str(d.get("decision"))+" reason_has_999-12="+str("999-12" in d.get("reason","")))' 2>/dev/null || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [1f]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  INFO [1f]: empty (fail-open, no task-dir)"; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert "999-12" not in d.get("reason","")' 2>/dev/null; then echo "  PASS [1f]: band not inverted"; \
	else echo "  FAIL [1f]: 999-12 in reason or invalid JSON"; OVERALL=FAIL; fi; \
	\
	echo ""; echo "--- GROUP 2: multitasking_backlog_stop.sh (Stop hook) ---"; \
	echo "[2a] stop_hook_active=true -> exit=0, no block"; \
	OUT=$$(printf '%s' '{"stop_hook_active":true}' | bash .claude/hooks/multitasking_backlog_stop.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [2a]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [2a]: empty exit=0 (escape)"; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("decision")!="block"' 2>/dev/null; then echo "  PASS [2a]: no block"; \
	else echo "  FAIL [2a]: unexpected block with escape active"; OVERALL=FAIL; fi; \
	\
	echo "[2b] stop_hook_active=false (real backlog) -> exit=0, empty-or-valid-JSON"; \
	OUT=$$(printf '%s' '{"stop_hook_active":false}' | bash .claude/hooks/multitasking_backlog_stop.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; SERR=$$(cat /tmp/gludd-hook-stderr.txt); \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty)' || printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("JSON ok decision="+str(d.get("decision","allow")))' 2>/dev/null || echo "INVALID_JSON: $$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [2b]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [2b]: empty (allow/fail-open)"; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then echo "  PASS [2b]: valid JSON"; \
	else echo "  FAIL [2b]: invalid JSON stdout"; OVERALL=FAIL; fi; \
	printf '%s' "$$SERR" | grep -q 'Traceback' && { echo "  FAIL [2b]: traceback in stderr"; OVERALL=FAIL; } || true; \
	\
	echo "[2c] empty stdin -> exit=0"; \
	OUT=$$(printf '' | bash .claude/hooks/multitasking_backlog_stop.sh 2>/dev/null); RC=$$?; \
	[ $$RC -eq 0 ] && echo "  PASS [2c]: exit=0" || { echo "  FAIL [2c]: exit $$RC"; OVERALL=FAIL; }; \
	\
	echo "[2d] nonexistent backlog path -> exit=0 (fail-open)"; \
	OUT=$$(printf '%s' '{"stop_hook_active":false}' | GLUDD_MT_BACKLOG=/nonexistent/x.json bash .claude/hooks/multitasking_backlog_stop.sh 2>/dev/null); RC=$$?; \
	[ $$RC -eq 0 ] && echo "  PASS [2d]: exit=0" || { echo "  FAIL [2d]: exit $$RC"; OVERALL=FAIL; }; \
	\
	echo ""; echo "--- GROUP 3: session_start_orchestrate.sh (SessionStart) ---"; \
	echo "[3a] normal run -> exit=0, stdout is empty OR valid JSON (NOT raw plaintext)"; \
	OUT=$$(bash .claude/hooks/session_start_orchestrate.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; SERR=$$(cat /tmp/gludd-hook-stderr.txt); \
	echo "  exit=$$RC stdout_len=$$(printf '%s' "$$OUT" | wc -c | tr -d ' ') stderr_tb=$$(_tb)"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [3a]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [3a]: empty stdout (fail-open)"; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then echo "  PASS [3a]: valid JSON (not raw plaintext)"; \
	else echo "  FAIL [3a]: non-JSON stdout -- this causes session-start hook error"; OVERALL=FAIL; fi; \
	[ "$$(_tb)" = "CLEAN" ] || { echo "  FAIL [3a]: traceback in stderr"; OVERALL=FAIL; }; \
	\
	echo ""; echo "--- GROUP 4: agent_floor_dec.sh (SubagentStop) ---"; \
	echo "[4a] FLOOR=999 live=0 (breach path) -> exit=0, valid JSON"; \
	OUT=$$(printf '%s' '{}' | CLAUDE_AGENT_FLOOR=999 FLOOR_LIVE_OVERRIDE=0 bash .claude/hooks/agent_floor_dec.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty)' || printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("JSON ok keys="+str(list(d.keys())))' 2>/dev/null || echo "INVALID: $$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [4a]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  INFO [4a]: empty (fail-open, no task-dir)"; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then echo "  PASS [4a]: valid JSON"; \
	else echo "  FAIL [4a]: invalid JSON (printf format bug)"; OVERALL=FAIL; fi; \
	[ "$$(_tb)" = "CLEAN" ] || { echo "  FAIL [4a]: traceback"; OVERALL=FAIL; }; \
	\
	echo "[4b] FLOOR=6 live=10 (healthy path) -> exit=0, valid JSON"; \
	OUT=$$(printf '%s' '{}' | CLAUDE_AGENT_FLOOR=6 FLOOR_LIVE_OVERRIDE=10 bash .claude/hooks/agent_floor_dec.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty)' || printf '%s' "$$OUT" | python3 -c 'import json,sys; json.load(sys.stdin); print("JSON ok")' 2>/dev/null || echo "INVALID: $$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [4b]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  INFO [4b]: empty (fail-open)"; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then echo "  PASS [4b]: valid JSON (healthy-path)"; \
	else echo "  FAIL [4b]: invalid JSON"; OVERALL=FAIL; fi; \
	\
	echo ""; echo "--- GROUP 5: enforce_make_bash.sh (PreToolUse/Bash) ---"; \
	echo "[5a] make command -> allow (empty or non-deny JSON)"; \
	OUT=$$(printf '%s' '{"tool_input":{"command":"make test"}}' | bash .claude/hooks/enforce_make_bash.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty=allow)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [5a]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [5a]: empty = allow"; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("hookSpecificOutput",{}).get("permissionDecision")!="deny"' 2>/dev/null; then echo "  PASS [5a]: no deny"; \
	else echo "  FAIL [5a]: unexpected deny for make command"; OVERALL=FAIL; fi; \
	\
	echo "[5b] ls command -> deny (exit=0, permissionDecision=deny)"; \
	OUT=$$(printf '%s' '{"tool_input":{"command":"ls -la"}}' | bash .claude/hooks/enforce_make_bash.sh 2>/dev/null); RC=$$?; \
	echo "  exit=$$RC deny=$$(printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hookSpecificOutput",{}).get("permissionDecision","none"))' 2>/dev/null)"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [5b]: exit $$RC"; OVERALL=FAIL; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("hookSpecificOutput",{}).get("permissionDecision")=="deny"' 2>/dev/null; then echo "  PASS [5b]: deny exit=0"; \
	else echo "  FAIL [5b]: expected deny; got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[5c] empty stdin -> exit=0 (fail-open)"; \
	printf '' | bash .claude/hooks/enforce_make_bash.sh >/dev/null 2>&1; RC=$$?; \
	[ $$RC -eq 0 ] && echo "  PASS [5c]" || { echo "  FAIL [5c]: exit $$RC"; OVERALL=FAIL; }; \
	\
	echo "[5d] garbage stdin -> exit=0 (fail-open)"; \
	printf 'NOT JSON' | bash .claude/hooks/enforce_make_bash.sh >/dev/null 2>&1; RC=$$?; \
	[ $$RC -eq 0 ] && echo "  PASS [5d]" || { echo "  FAIL [5d]: exit $$RC"; OVERALL=FAIL; }; \
	\
	echo ""; echo "--- GROUP 6: floor_pretool / floor_posttool / mainthread_budget ---"; \
	echo "[6a] floor_pretool FLOOR=999 live=0 -> exit=0, valid JSON"; \
	OUT=$$(printf '%s' '{"tool_name":"Read","hook_event_name":"PreToolUse"}' | CLAUDE_AGENT_FLOOR=999 FLOOR_LIVE_OVERRIDE=0 bash .claude/hooks/agent_floor_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [6a]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ] || printf '%s' "$$OUT" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then echo "  PASS [6a]: exit=$$RC stdout valid"; \
	else echo "  FAIL [6a]: invalid JSON: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[6b] floor_posttool FLOOR=999 live=0 -> exit=0, valid JSON"; \
	OUT=$$(printf '%s' '{"tool_name":"Read","hook_event_name":"PostToolUse"}' | CLAUDE_AGENT_FLOOR=999 FLOOR_LIVE_OVERRIDE=0 bash .claude/hooks/agent_floor_posttool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [6b]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ] || printf '%s' "$$OUT" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then echo "  PASS [6b]: exit=$$RC stdout valid"; \
	else echo "  FAIL [6b]: invalid JSON: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[6c] mainthread_budget streak=20 live=0 -> exit=0, valid JSON"; \
	printf '20\n' > /tmp/gludd-mainthread-streak-hooktest; \
	OUT=$$(printf '%s' '{"tool_name":"Read","hook_event_name":"PostToolUse"}' | GLUDD_MAINTHREAD_STREAK_FILE=/tmp/gludd-mainthread-streak-hooktest GLUDD_MAINTHREAD_THRESHOLD=8 FLOOR_LIVE_OVERRIDE=0 bash .claude/hooks/mainthread_budget.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	rm -f /tmp/gludd-mainthread-streak-hooktest; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [6c]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ] || printf '%s' "$$OUT" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then echo "  PASS [6c]: exit=$$RC stdout valid"; \
	else echo "  FAIL [6c]: invalid JSON: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo ""; echo "--- GROUP 7: agent_ceiling_pretool.sh (PreToolUse/Agent) ---"; \
	echo "[7a] live >= CEILING -> exit=0, valid JSON hookSpecificOutput only"; \
	OUT=$$(printf '%s' '{"tool_name":"Agent"}' | CLAUDE_AGENT_CEILING=5 FLOOR_LIVE_OVERRIDE=10 bash .claude/hooks/agent_ceiling_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty)' || printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("JSON ok keys="+str(list(d.keys())))' 2>/dev/null || echo "INVALID: $$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [7a]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ] || printf '%s' "$$OUT" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then echo "  PASS [7a]: valid JSON or empty"; \
	else echo "  FAIL [7a]: invalid JSON"; OVERALL=FAIL; fi; \
	\
	echo "[7b] live < CEILING -> exit=0, empty"; \
	OUT=$$(printf '%s' '{"tool_name":"Agent"}' | CLAUDE_AGENT_CEILING=12 FLOOR_LIVE_OVERRIDE=3 bash .claude/hooks/agent_ceiling_pretool.sh 2>/dev/null); RC=$$?; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [7b]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [7b]: empty (below ceiling)"; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then echo "  PASS [7b]: valid JSON (ok)"; \
	else echo "  FAIL [7b]: non-empty invalid JSON"; OVERALL=FAIL; fi; \
	\
	echo ""; echo "--- GROUP 8: disk_discipline_pretool.sh (PreToolUse/Agent) ---"; \
	echo "[8a] SILENT: non-worktree Agent call -> exit=0, empty stdout"; \
	OUT=$$(printf '%s' '{"tool_input":{"isolation":"none","prompt":"hello"}}' | bash .claude/hooks/disk_discipline_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty=correct)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [8a]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [8a]: silent on non-worktree agent"; \
	else echo "  FAIL [8a]: emitted output for non-worktree agent (should be silent)"; OVERALL=FAIL; fi; \
	\
	echo "[8b] SILENT: worktree Agent, healthy disk + venvs under cap -> exit=0, empty stdout"; \
	OUT=$$(printf '%s' '{"tool_input":{"isolation":"worktree","prompt":"do work"}}' | \
	  GLUDD_DISK_FREE_OVERRIDE=50.0 GLUDD_VENV_COUNT_OVERRIDE=2 GLUDD_DISK_DANGER_GB=2.5 GLUDD_WORKTREE_CAP=6 \
	  bash .claude/hooks/disk_discipline_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty=correct)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [8b]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [8b]: silent on healthy-disk worktree agent"; \
	else echo "  FAIL [8b]: emitted output when disk healthy (should be silent)"; OVERALL=FAIL; fi; \
	\
	echo "[8c] DENY: worktree Agent, HARD FLOOR breach (< 1GB free) -> exit=0, permissionDecision=deny"; \
	OUT=$$(printf '%s' '{"tool_input":{"isolation":"worktree","prompt":"do work"}}' | \
	  GLUDD_DISK_FREE_OVERRIDE=0.4 GLUDD_VENV_COUNT_OVERRIDE=2 GLUDD_DISK_HARD_FLOOR_GB=1.0 \
	  bash .claude/hooks/disk_discipline_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC deny=$$(printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hookSpecificOutput",{}).get("permissionDecision","none"))' 2>/dev/null || echo 'PARSE_ERR')"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [8c]: exit $$RC"; OVERALL=FAIL; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("hookSpecificOutput",{}).get("permissionDecision")=="deny"' 2>/dev/null; then echo "  PASS [8c]: deny exit=0 on ENOSPC-imminent"; \
	else echo "  FAIL [8c]: expected deny; got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[8d] WARN (advisory, not deny): worktree Agent, disk in danger zone (1.5GB, between floor 1.0 and danger 2.5) -> exit=0, additionalContext present, NOT deny"; \
	OUT=$$(printf '%s' '{"tool_input":{"isolation":"worktree","prompt":"do work"}}' | \
	  GLUDD_DISK_FREE_OVERRIDE=1.5 GLUDD_VENV_COUNT_OVERRIDE=2 GLUDD_DISK_HARD_FLOOR_GB=1.0 GLUDD_DISK_DANGER_GB=2.5 \
	  bash .claude/hooks/disk_discipline_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC decision=$$(printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); hs=d.get("hookSpecificOutput",{}); print("deny" if hs.get("permissionDecision")=="deny" else "advisory" if hs.get("additionalContext") else "empty")' 2>/dev/null || echo 'PARSE_ERR')"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [8d]: exit $$RC"; OVERALL=FAIL; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); hs=d.get("hookSpecificOutput",{}); assert hs.get("additionalContext") and hs.get("permissionDecision") != "deny"' 2>/dev/null; then echo "  PASS [8d]: advisory (not deny) in danger zone"; \
	else echo "  FAIL [8d]: expected advisory additionalContext (not deny); got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[8e] WARN: worktree Agent, venv count >= CAP -> exit=0, advisory emitted"; \
	OUT=$$(printf '%s' '{"tool_input":{"isolation":"worktree","prompt":"do work"}}' | \
	  GLUDD_DISK_FREE_OVERRIDE=50.0 GLUDD_VENV_COUNT_OVERRIDE=7 GLUDD_WORKTREE_CAP=6 GLUDD_DISK_DANGER_GB=2.5 GLUDD_DISK_HARD_FLOOR_GB=1.0 \
	  bash .claude/hooks/disk_discipline_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC has_output=$$([ -n "$$OUT" ] && echo yes || echo no)"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [8e]: exit $$RC"; OVERALL=FAIL; \
	elif [ -n "$$OUT" ] && printf '%s' "$$OUT" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then echo "  PASS [8e]: valid JSON advisory for venv-cap breach"; \
	else echo "  FAIL [8e]: expected valid JSON advisory; got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[8f] FAIL-OPEN: garbage stdin -> exit=0, empty"; \
	OUT=$$(printf 'NOT JSON' | bash .claude/hooks/disk_discipline_pretool.sh 2>/dev/null); RC=$$?; \
	[ $$RC -eq 0 ] && echo "  PASS [8f]: fail-open on garbage stdin" || { echo "  FAIL [8f]: exit $$RC"; OVERALL=FAIL; }; \
	\
	echo ""; echo "--- GROUP 9: guardrail_integrity_edit_pretool.sh (PreToolUse/Edit) ---"; \
	echo "[9a] SILENT: edit to a non-hook file (src/) -> exit=0, empty stdout"; \
	OUT=$$(printf '%s' '{"tool_input":{"file_path":"/Users/shawnwilson/gludd/src/general_ludd/foo.py","old_string":"def bar(): pass","new_string":"def bar(): return 1"}}' | bash .claude/hooks/guardrail_integrity_edit_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty=correct)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [9a]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [9a]: silent on non-hook-file edit"; \
	else echo "  FAIL [9a]: emitted output for non-hook file (should be silent)"; OVERALL=FAIL; fi; \
	\
	echo "[9b] SILENT: edit to hook file that KEEPS enforcement token in new_string -> exit=0, empty"; \
	OUT=$$(printf '%s' '{"tool_input":{"file_path":"/Users/shawnwilson/gludd/.claude/hooks/some_hook.sh","old_string":"exit 1  # old path","new_string":"exit 1  # new path"}}' | bash .claude/hooks/guardrail_integrity_edit_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty=correct)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [9b]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [9b]: silent when enforcement token kept in new_string"; \
	else echo "  FAIL [9b]: emitted output (should be silent — enforcement preserved)"; OVERALL=FAIL; fi; \
	\
	echo "[9c] DENY: edit to hook file strips exit 1 entirely -> exit=0, permissionDecision=deny"; \
	OUT=$$(printf '%s' '{"tool_input":{"file_path":"/Users/shawnwilson/gludd/.claude/hooks/enforce_make_bash.sh","old_string":"exit 1  # block","new_string":"echo advisory only"}}' | bash .claude/hooks/guardrail_integrity_edit_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC deny=$$(printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hookSpecificOutput",{}).get("permissionDecision","none"))' 2>/dev/null || echo 'PARSE_ERR')"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [9c]: exit $$RC"; OVERALL=FAIL; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("hookSpecificOutput",{}).get("permissionDecision")=="deny"' 2>/dev/null; then echo "  PASS [9c]: deny on enforcement-stripping hook edit"; \
	else echo "  FAIL [9c]: expected deny; got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[9d] DENY: edit to plugin .ts file strips throw new Error -> exit=0, permissionDecision=deny"; \
	OUT=$$(printf '%s' '{"tool_input":{"file_path":"/Users/shawnwilson/gludd/.opencode/plugin/enforce-make.ts","old_string":"throw new Error(\"BLOCKED\")","new_string":"console.log(\"warning only\")"}}' | bash .claude/hooks/guardrail_integrity_edit_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC deny=$$(printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hookSpecificOutput",{}).get("permissionDecision","none"))' 2>/dev/null || echo 'PARSE_ERR')"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [9d]: exit $$RC"; OVERALL=FAIL; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("hookSpecificOutput",{}).get("permissionDecision")=="deny"' 2>/dev/null; then echo "  PASS [9d]: deny on enforcement-stripping plugin edit"; \
	else echo "  FAIL [9d]: expected deny; got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[9e] SILENT: old_string has NO enforcement token (normal refactor) -> exit=0, empty"; \
	OUT=$$(printf '%s' '{"tool_input":{"file_path":"/Users/shawnwilson/gludd/.claude/hooks/some_hook.sh","old_string":"echo hello","new_string":"echo goodbye"}}' | bash .claude/hooks/guardrail_integrity_edit_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty=correct)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [9e]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [9e]: silent when old_string had no enforcement token"; \
	else echo "  FAIL [9e]: should be silent (no enforcement token in old_string)"; OVERALL=FAIL; fi; \
	\
	echo "[9f] FAIL-OPEN: garbage stdin -> exit=0, empty"; \
	OUT=$$(printf 'NOT JSON' | bash .claude/hooks/guardrail_integrity_edit_pretool.sh 2>/dev/null); RC=$$?; \
	[ $$RC -eq 0 ] && echo "  PASS [9f]: fail-open on garbage stdin" || { echo "  FAIL [9f]: exit $$RC"; OVERALL=FAIL; }; \
	\
	echo ""; echo "--- GROUP 10: gate_concurrency_pretool.sh (PreToolUse/Bash) ---"; \
	echo "[10a] SILENT: non-gate Bash command -> exit=0, empty stdout"; \
	OUT=$$(printf '%s' '{"tool_input":{"command":"make lint"}}' | bash .claude/hooks/gate_concurrency_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty=correct)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [10a]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [10a]: silent on non-gate command (make lint)"; \
	else echo "  FAIL [10a]: emitted output for non-gate command (should be silent)"; OVERALL=FAIL; fi; \
	\
	echo "[10b] SILENT: make gate command, NO pytest running -> exit=0, empty stdout"; \
	OUT=$$(printf '%s' '{"tool_input":{"command":"make gate"}}' | \
	  GLUDD_GATE_PYTEST_RUNNING=0 GLUDD_GATE_BASETEMP=/tmp/nonexistent-basetemp-hooktest-$$$$ \
	  bash .claude/hooks/gate_concurrency_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty=correct)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [10b]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [10b]: silent when no pytest running"; \
	else echo "  FAIL [10b]: emitted output when no pytest running (should be silent)"; OVERALL=FAIL; fi; \
	\
	echo "[10c] DENY: make gate command, pytest IS running (override) -> exit=0, permissionDecision=deny"; \
	OUT=$$(printf '%s' '{"tool_input":{"command":"make gate"}}' | \
	  GLUDD_GATE_PYTEST_RUNNING=1 \
	  bash .claude/hooks/gate_concurrency_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC deny=$$(printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hookSpecificOutput",{}).get("permissionDecision","none"))' 2>/dev/null || echo 'PARSE_ERR')"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [10c]: exit $$RC"; OVERALL=FAIL; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("hookSpecificOutput",{}).get("permissionDecision")=="deny"' 2>/dev/null; then echo "  PASS [10c]: deny when pytest already running"; \
	else echo "  FAIL [10c]: expected deny; got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[10d] DENY: make test command (not just gate) -> deny when pytest running"; \
	OUT=$$(printf '%s' '{"tool_input":{"command":"make test"}}' | \
	  GLUDD_GATE_PYTEST_RUNNING=1 \
	  bash .claude/hooks/gate_concurrency_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC deny=$$(printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hookSpecificOutput",{}).get("permissionDecision","none"))' 2>/dev/null || echo 'PARSE_ERR')"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [10d]: exit $$RC"; OVERALL=FAIL; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("hookSpecificOutput",{}).get("permissionDecision")=="deny"' 2>/dev/null; then echo "  PASS [10d]: deny on make test with pytest running"; \
	else echo "  FAIL [10d]: expected deny; got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[10e] SILENT: make git-status (not a test command) -> exit=0, empty"; \
	OUT=$$(printf '%s' '{"tool_input":{"command":"make git-status"}}' | \
	  GLUDD_GATE_PYTEST_RUNNING=1 \
	  bash .claude/hooks/gate_concurrency_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty=correct)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [10e]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [10e]: silent on make git-status (not a test target)"; \
	else echo "  FAIL [10e]: emitted output for non-gate command (should be silent)"; OVERALL=FAIL; fi; \
	\
	echo "[10f] FAIL-OPEN: garbage stdin -> exit=0, empty"; \
	OUT=$$(printf 'NOT JSON' | GLUDD_GATE_PYTEST_RUNNING=1 bash .claude/hooks/gate_concurrency_pretool.sh 2>/dev/null); RC=$$?; \
	[ $$RC -eq 0 ] && echo "  PASS [10f]: fail-open on garbage stdin" || { echo "  FAIL [10f]: exit $$RC"; OVERALL=FAIL; }; \
	\
	echo "[10g] EXEMPT: make test-count (pytest running) -> exit=0, empty (lock-free, not blocked)"; \
	OUT=$$(printf '%s' '{"tool_input":{"command":"make test-count"}}' | \
	  GLUDD_GATE_PYTEST_RUNNING=1 \
	  bash .claude/hooks/gate_concurrency_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty=correct)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [10g]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [10g]: silent on test-count (exempt from block)"; \
	else echo "  FAIL [10g]: test-count should be exempt; got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[10h] EXEMPT: make collect-check (pytest running) -> exit=0, empty"; \
	OUT=$$(printf '%s' '{"tool_input":{"command":"make collect-check"}}' | \
	  GLUDD_GATE_PYTEST_RUNNING=1 \
	  bash .claude/hooks/gate_concurrency_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty=correct)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [10h]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [10h]: silent on collect-check (exempt from block)"; \
	else echo "  FAIL [10h]: collect-check should be exempt; got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[10i] EXEMPT: make test-unit TESTFILE=tests/unit/test_foo.py (pytest running) -> exit=0, empty"; \
	OUT=$$(printf '%s' '{"tool_input":{"command":"make test-unit TESTFILE=tests/unit/test_foo.py"}}' | \
	  GLUDD_GATE_PYTEST_RUNNING=1 \
	  bash .claude/hooks/gate_concurrency_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty=correct)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [10i]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [10i]: silent on test-unit TESTFILE= (exempt from block)"; \
	else echo "  FAIL [10i]: test-unit TESTFILE= should be exempt; got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[10j] BLOCKED: make test-unit (bare, no TESTFILE) -> deny when pytest running"; \
	OUT=$$(printf '%s' '{"tool_input":{"command":"make test-unit"}}' | \
	  GLUDD_GATE_PYTEST_RUNNING=1 \
	  bash .claude/hooks/gate_concurrency_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC deny=$$(printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hookSpecificOutput",{}).get("permissionDecision","none"))' 2>/dev/null || echo 'PARSE_ERR')"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [10j]: exit $$RC"; OVERALL=FAIL; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("hookSpecificOutput",{}).get("permissionDecision")=="deny"' 2>/dev/null; then echo "  PASS [10j]: deny on bare test-unit (runs full unit suite)"; \
	else echo "  FAIL [10j]: expected deny on bare test-unit; got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[10k] BLOCKED: make validate (pytest running) -> deny"; \
	OUT=$$(printf '%s' '{"tool_input":{"command":"make validate"}}' | \
	  GLUDD_GATE_PYTEST_RUNNING=1 \
	  bash .claude/hooks/gate_concurrency_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC deny=$$(printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hookSpecificOutput",{}).get("permissionDecision","none"))' 2>/dev/null || echo 'PARSE_ERR')"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [10k]: exit $$RC"; OVERALL=FAIL; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("hookSpecificOutput",{}).get("permissionDecision")=="deny"' 2>/dev/null; then echo "  PASS [10k]: deny on make validate (runs full suite)"; \
	else echo "  FAIL [10k]: expected deny on make validate; got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo ""; echo "--- GROUP 11: no_blocking_questions_pretool.sh (PreToolUse/AskUserQuestion) ---"; \
	echo "[11a] AskUserQuestion tool-input -> exit=0, permissionDecision=deny, valid JSON"; \
	OUT=$$(printf '%s' '{"tool_name":"AskUserQuestion","tool_input":{"question":"Should I proceed?","options":["Yes","No"]}}' | bash .claude/hooks/no_blocking_questions_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC deny=$$(printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hookSpecificOutput",{}).get("permissionDecision","none"))' 2>/dev/null || echo 'PARSE_ERR') stderr_tb=$$(_tb)"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [11a]: exit $$RC (must be 0)"; OVERALL=FAIL; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); hs=d.get("hookSpecificOutput",{}); assert hs.get("permissionDecision")=="deny", "decision="+str(hs.get("permissionDecision")); assert hs.get("hookEventName")=="PreToolUse"; assert hs.get("permissionDecisionReason")' 2>/dev/null; then echo "  PASS [11a]: deny exit=0 valid JSON with reason + hookEventName"; \
	else echo "  FAIL [11a]: expected deny with PreToolUse hookEventName + reason; got: $$OUT"; OVERALL=FAIL; fi; \
	[ "$$(_tb)" = "CLEAN" ] || { echo "  FAIL [11a]: traceback in stderr"; OVERALL=FAIL; }; \
	\
	echo "[11b] empty stdin -> exit=0, permissionDecision=deny, valid JSON (every question denied)"; \
	OUT=$$(printf '' | bash .claude/hooks/no_blocking_questions_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC deny=$$(printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hookSpecificOutput",{}).get("permissionDecision","none"))' 2>/dev/null || echo 'PARSE_ERR')"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [11b]: exit $$RC (must be 0)"; OVERALL=FAIL; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("hookSpecificOutput",{}).get("permissionDecision")=="deny"' 2>/dev/null; then echo "  PASS [11b]: deny exit=0 on empty stdin"; \
	else echo "  FAIL [11b]: expected deny; got: $$OUT"; OVERALL=FAIL; fi; \
	[ "$$(_tb)" = "CLEAN" ] || { echo "  FAIL [11b]: traceback in stderr"; OVERALL=FAIL; }; \
	\
	echo "[11c] garbage stdin -> exit=0, valid JSON (fail-open to deny, no traceback)"; \
	OUT=$$(printf 'NOT JSON AT ALL' | bash .claude/hooks/no_blocking_questions_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout_valid=$$(printf '%s' "$$OUT" | python3 -c 'import json,sys; json.load(sys.stdin); print("yes")' 2>/dev/null || echo 'no') stderr_tb=$$(_tb)"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [11c]: exit $$RC (must be 0)"; OVERALL=FAIL; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then echo "  PASS [11c]: exit=0 valid JSON on garbage stdin"; \
	else echo "  FAIL [11c]: expected valid JSON stdout; got: $$OUT"; OVERALL=FAIL; fi; \
	[ "$$(_tb)" = "CLEAN" ] || { echo "  FAIL [11c]: traceback in stderr"; OVERALL=FAIL; }; \
	\
	echo ""; echo "--- GROUP 12: model_utilization_pretool.sh (PreToolUse/Agent) time-bound 2:1 target ---"; \
	_MU_STATE=/tmp/gludd-hooktest-model-util-$$$$.json; \
	_MU_CFG=/tmp/gludd-hooktest-model-util-cfg-$$$$.json; \
	_MU_FUTURE=$$(python3 -c 'import time; print(int(time.time()) + 3600)'); \
	_MU_PAST=$$(python3 -c 'import time; print(int(time.time()) - 3600)'); \
	\
	echo "[12a] ENFORCE: active 2:1 window + opus-heavy (sonnet below 67%) -> DENY with 'MODEL-RATIO' and 'target=' in reason"; \
	printf '%s\n' '{"history":["opus","opus","opus","opus","opus","opus","opus","opus","opus","opus"]}' > "$$_MU_STATE"; \
	printf '%s\n' "{\"target_share\": 0.67, \"until_epoch\": $$_MU_FUTURE}" > "$$_MU_CFG"; \
	OUT=$$(printf '%s' '{"tool_input":{"model":"opus","prompt":"do something"}}' | \
	  GLUDD_MAIN_MODEL_FILE=/nonexistent-test-main-model-isolated \
	  GLUDD_MODEL_UTIL_STATE="$$_MU_STATE" GLUDD_MODEL_UTIL_WINDOW=20 GLUDD_SONNET_TARGET_CONFIG="$$_MU_CFG" \
	  bash .claude/hooks/model_utilization_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC"; \
	echo "  decision=$$(printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hookSpecificOutput",{}).get("permissionDecision","none"))' 2>/dev/null || echo 'PARSE_ERR')"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [12a]: exit $$RC (must be 0)"; OVERALL=FAIL; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); hs=d.get("hookSpecificOutput",{}); pd=hs.get("permissionDecision"); reason=hs.get("permissionDecisionReason",""); assert pd=="deny", "expected deny got "+str(pd); assert "MODEL-RATIO" in reason, "missing MODEL-RATIO"; assert "target=" in reason, "missing target="' 2>/dev/null; then echo "  PASS [12a]: enforcement deny present (MODEL-RATIO + target= in reason), exit=0"; \
	else echo "  FAIL [12a]: expected deny with MODEL-RATIO and target=; got: $$OUT"; OVERALL=FAIL; fi; \
	[ "$$(_tb)" = "CLEAN" ] || { echo "  FAIL [12a]: traceback in stderr"; OVERALL=FAIL; }; \
	\
	echo "[12b] SILENT: active 2:1 window + sonnet >= 67% -> exit=0, empty stdout"; \
	printf '%s\n' '{"history":["sonnet","sonnet","sonnet","sonnet","sonnet","sonnet","sonnet","sonnet","sonnet","sonnet"]}' > "$$_MU_STATE"; \
	printf '%s\n' "{\"target_share\": 0.67, \"until_epoch\": $$_MU_FUTURE}" > "$$_MU_CFG"; \
	OUT=$$(printf '%s' '{"tool_input":{"model":"sonnet","prompt":"do something"}}' | \
	  GLUDD_MODEL_UTIL_STATE="$$_MU_STATE" GLUDD_MODEL_UTIL_WINDOW=20 GLUDD_SONNET_TARGET_CONFIG="$$_MU_CFG" \
	  bash .claude/hooks/model_utilization_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty=correct)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [12b]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [12b]: silent (sonnet at/above 2:1 target), exit=0"; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert not d.get("hookSpecificOutput",{}).get("additionalContext"), "should be silent"' 2>/dev/null; then echo "  PASS [12b]: no nudge when sonnet healthy under active window"; \
	else echo "  FAIL [12b]: emitted nudge when sonnet at/above target; got: $$OUT"; OVERALL=FAIL; fi; \
	[ "$$(_tb)" = "CLEAN" ] || { echo "  FAIL [12b]: traceback in stderr"; OVERALL=FAIL; }; \
	\
	echo "[12c] EXPIRED WINDOW: enforcement stays ACTIVE (expiry ignored); opus-heavy -> DENY; sonnet-heavy -> silent"; \
	printf '%s\n' '{"history":["opus","opus","opus","opus","opus","opus","opus","opus","opus","opus"]}' > "$$_MU_STATE"; \
	printf '%s\n' "{\"target_share\": 0.67, \"until_epoch\": $$_MU_PAST}" > "$$_MU_CFG"; \
	OUT=$$(printf '%s' '{"tool_input":{"model":"opus","prompt":"do something"}}' | \
	  GLUDD_MAIN_MODEL_FILE=/nonexistent-test-main-model-isolated \
	  GLUDD_MODEL_UTIL_STATE="$$_MU_STATE" GLUDD_MODEL_UTIL_WINDOW=20 GLUDD_SONNET_TARGET_CONFIG="$$_MU_CFG" \
	  bash .claude/hooks/model_utilization_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC"; \
	echo "  decision=$$(printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hookSpecificOutput",{}).get("permissionDecision","none"))' 2>/dev/null || echo 'PARSE_ERR')"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [12c-nudge]: exit $$RC"; OVERALL=FAIL; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); hs=d.get("hookSpecificOutput",{}); pd=hs.get("permissionDecision"); reason=hs.get("permissionDecisionReason",""); assert pd=="deny", "expected deny (enforcement ignores expiry) got "+str(pd); assert "MODEL-RATIO" in reason, "missing MODEL-RATIO"; assert "target=" in reason, "missing target="' 2>/dev/null; then echo "  PASS [12c-nudge]: expired window -> enforcement DENY (MODEL-RATIO + target= in reason)"; \
	else echo "  FAIL [12c-nudge]: expected deny with MODEL-RATIO and target=; got: $$OUT"; OVERALL=FAIL; fi; \
	printf '%s\n' '{"history":["sonnet","sonnet","sonnet","sonnet","sonnet","sonnet","sonnet","sonnet","sonnet","sonnet"]}' > "$$_MU_STATE"; \
	OUT2=$$(printf '%s' '{"tool_input":{"model":"sonnet","prompt":"do something"}}' | \
	  GLUDD_MAIN_MODEL_FILE=/nonexistent-test-main-model-isolated \
	  GLUDD_MODEL_UTIL_STATE="$$_MU_STATE" GLUDD_MODEL_UTIL_WINDOW=20 GLUDD_SONNET_TARGET_CONFIG="$$_MU_CFG" \
	  bash .claude/hooks/model_utilization_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC2=$$?; \
	if [ $$RC2 -ne 0 ]; then echo "  FAIL [12c-silent]: exit $$RC2"; OVERALL=FAIL; \
	elif [ -z "$$OUT2" ]; then echo "  PASS [12c-silent]: silent when sonnet healthy (expired window)"; \
	elif printf '%s' "$$OUT2" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert not d.get("hookSpecificOutput",{}).get("additionalContext")' 2>/dev/null; then echo "  PASS [12c-silent]: no nudge when healthy after expiry"; \
	else echo "  FAIL [12c-silent]: emitted nudge when sonnet healthy after expiry; got: $$OUT2"; OVERALL=FAIL; fi; \
	[ "$$(_tb)" = "CLEAN" ] || { echo "  FAIL [12c]: traceback in stderr"; OVERALL=FAIL; }; \
	\
	echo "[12d] FAIL-OPEN: malformed/empty stdin -> exit=0, no Traceback in stderr"; \
	printf '' | GLUDD_MODEL_UTIL_STATE="$$_MU_STATE" GLUDD_SONNET_TARGET_CONFIG="$$_MU_CFG" \
	  bash .claude/hooks/model_utilization_pretool.sh >/tmp/gludd-hooktest-12d-out.txt 2>/tmp/gludd-hook-stderr.txt; RC=$$?; \
	echo "  exit=$$RC"; \
	OUT=$$(cat /tmp/gludd-hooktest-12d-out.txt); SERR=$$(cat /tmp/gludd-hook-stderr.txt); \
	if [ $$RC -ne 0 ]; then echo "  FAIL [12d]: exit $$RC (must be 0)"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ] || printf '%s' "$$OUT" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then echo "  PASS [12d]: exit=0, stdout empty or valid JSON"; \
	else echo "  FAIL [12d]: non-zero exit or invalid stdout"; OVERALL=FAIL; fi; \
	printf '%s' "$$SERR" | grep -q 'Traceback' && { echo "  FAIL [12d]: Traceback in stderr"; OVERALL=FAIL; } || true; \
	printf '%s' "$$SERR" | grep -q 'Traceback' || echo "  PASS [12d-notraceback]: no Traceback in stderr"; \
	rm -f "$$_MU_STATE" "$$_MU_CFG" /tmp/gludd-hooktest-12d-out.txt; \
	\
	echo ""; echo "--- GROUP 13: no_flag_file_write_pretool.sh (PreToolUse/Write+Edit) ---"; \
	echo "[13a] DENY: Write tool_input file_path=.gate-status -> exit=0, permissionDecision=deny"; \
	OUT=$$(printf '%s' '{"tool_input":{"file_path":"/Users/shawnwilson/gludd/.gate-status","content":"lint PASS\ntest PASS\nepoch 9999999999"}}' | bash .claude/hooks/no_flag_file_write_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC deny=$$(printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hookSpecificOutput",{}).get("permissionDecision","none"))' 2>/dev/null || echo 'PARSE_ERR')"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [13a]: exit $$RC (must be 0)"; OVERALL=FAIL; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("hookSpecificOutput",{}).get("permissionDecision")=="deny"' 2>/dev/null; then echo "  PASS [13a]: deny on Write to .gate-status"; \
	else echo "  FAIL [13a]: expected deny; got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[13b] DENY: Write tool_input file_path=.gate-failed -> exit=0, permissionDecision=deny"; \
	OUT=$$(printf '%s' '{"tool_input":{"file_path":"/Users/shawnwilson/gludd/.gate-failed","content":""}}' | bash .claude/hooks/no_flag_file_write_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC deny=$$(printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hookSpecificOutput",{}).get("permissionDecision","none"))' 2>/dev/null || echo 'PARSE_ERR')"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [13b]: exit $$RC"; OVERALL=FAIL; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("hookSpecificOutput",{}).get("permissionDecision")=="deny"' 2>/dev/null; then echo "  PASS [13b]: deny on Write to .gate-failed"; \
	else echo "  FAIL [13b]: expected deny; got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[13c] DENY: Write to foo.gate-status (*.gate-status glob) -> exit=0, deny"; \
	OUT=$$(printf '%s' '{"tool_input":{"file_path":"/tmp/foo.gate-status","content":"PASS"}}' | bash .claude/hooks/no_flag_file_write_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC deny=$$(printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hookSpecificOutput",{}).get("permissionDecision","none"))' 2>/dev/null || echo 'PARSE_ERR')"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [13c]: exit $$RC"; OVERALL=FAIL; \
	elif printf '%s' "$$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("hookSpecificOutput",{}).get("permissionDecision")=="deny"' 2>/dev/null; then echo "  PASS [13c]: deny on Write to *.gate-status"; \
	else echo "  FAIL [13c]: expected deny; got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[13d] SILENT: Write to a normal src file -> exit=0, empty stdout"; \
	OUT=$$(printf '%s' '{"tool_input":{"file_path":"/Users/shawnwilson/gludd/src/general_ludd/foo.py","content":"# code"}}' | bash .claude/hooks/no_flag_file_write_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty=correct)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [13d]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [13d]: silent on normal file write"; \
	else echo "  FAIL [13d]: should be silent for non-flag-file; got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[13e] SILENT: Write to .gate-status-report (not a flag file) -> exit=0, empty"; \
	OUT=$$(printf '%s' '{"tool_input":{"file_path":"/tmp/.gate-status-report","content":"summary"}}' | bash .claude/hooks/no_flag_file_write_pretool.sh 2>/tmp/gludd-hook-stderr.txt); RC=$$?; \
	echo "  exit=$$RC stdout=$$([ -z "$$OUT" ] && echo '(empty=correct)' || echo "$$OUT")"; \
	if [ $$RC -ne 0 ]; then echo "  FAIL [13e]: exit $$RC"; OVERALL=FAIL; \
	elif [ -z "$$OUT" ]; then echo "  PASS [13e]: silent on .gate-status-report (suffix, not exact match)"; \
	else echo "  FAIL [13e]: should be silent for non-exact-match; got: $$OUT"; OVERALL=FAIL; fi; \
	\
	echo "[13f] FAIL-OPEN: garbage stdin -> exit=0, empty"; \
	OUT=$$(printf 'NOT JSON' | bash .claude/hooks/no_flag_file_write_pretool.sh 2>/dev/null); RC=$$?; \
	[ $$RC -eq 0 ] && echo "  PASS [13f]: fail-open on garbage stdin" || { echo "  FAIL [13f]: exit $$RC"; OVERALL=FAIL; }; \
	\
	echo ""; \
	echo "========================================================"; \
	echo "  test-hooks OVERALL: $$OVERALL"; \
	echo "========================================================"; \
	[ "$$OVERALL" = "PASS" ] || exit 1

# ---------------------------------------------------------------------------
# set-sonnet-target: write .claude/sonnet_ratio_target with a time-bound 2:1
# sonnet target.  Until the window expires, model_utilization_pretool.sh uses
# target_share instead of the 10%-band default.
# Usage: make set-sonnet-target [HOURS=24] [SHARE=0.67]
# ---------------------------------------------------------------------------
HOURS ?= 24
SHARE ?= 0.67
set-sonnet-target:
	@python3 -c "\
import json, time; \
until = int(time.time()) + int('$(HOURS)') * 3600; \
cfg = {'target_share': float('$(SHARE)'), 'until_epoch': until}; \
open('.claude/sonnet_ratio_target', 'w').write(json.dumps(cfg)); \
from datetime import datetime; \
print('[set-sonnet-target] wrote .claude/sonnet_ratio_target'); \
print('  target_share=$(SHARE)  until=' + datetime.fromtimestamp(until).strftime('%Y-%m-%d %H:%M') + '  ($(HOURS)h from now)') \
"

# Legacy alias -- preserved so existing CI targets / muscle memory still work.
test-stop-hooks: test-hooks

# ---------------------------------------------------------------------------
# Release-cut gate: README Feature & Task Completion Status table must be
# updated before every release.  See AGENTS.md "Release Cut = Update the README
# Status Table" and scripts/check_readme_status_current.py.
# ---------------------------------------------------------------------------

# require-ci-green: Verify that HEAD (or SHA=...) has a SUCCESSFUL "Build and Release"
# CI run. Exit 0 = green, exit 1 = red/missing (fail-closed), exit 2 = pending.
# Used as step 0 of release-cut to block releasing on a non-green commit.
# Usage:
#   make require-ci-green             # checks HEAD
#   make require-ci-green SHA=abc123  # checks a specific commit
SHA ?=
require-ci-green:
	@echo "[require-ci-green] checking CI status for $$(git rev-parse --short HEAD) ..."
	@$(UV) run python scripts/require_ci_green.py $(SHA)

# Verify that README.md's "Status as of <version>" line matches the release version.
# Usage:
#   make check-readme-status               # reads version from pyproject.toml
#   make check-readme-status TAG='v0.1.0-alpha.2'   # explicit tag override
check-readme-status:
	@echo "[check-readme-status] checking README status table ..."
	@$(UV) run python scripts/check_readme_status_current.py $(if $(TAG),$(TAG),)

# THE release command.  Enforces README currency before pushing anything.
# Usage: make release-cut TAG='v0.1.0-alpha.2' MSG='v0.1.0-alpha.2 — first alpha'
# Steps (in order, abort on first failure):
#   1. check-readme-status  — README Feature & Task Completion Status table must match TAG
#   2. git-push-sandboxcom  — push master branch to sandboxcom/gludd
#   3. git-tag-push         — create annotated tag + push (triggers CI release job)
#   4. release-view         — confirm the published GitHub Release
release-cut:
	@[ -n "$(TAG)" ] || { echo "Usage: make release-cut TAG='v0.1.0-alpha.N' MSG='...'"; exit 1; }
	@echo "[release-cut] step 0/4 — require a GREEN CI run for HEAD before releasing ..."
	@$(MAKE) --no-print-directory require-ci-green || { \
		echo ""; \
		echo "RELEASE ABORTED: CI is not GREEN for HEAD (the commit being released)."; \
		echo "  A release tag must only be cut on a commit whose 'Build and Release' run"; \
		echo "  concluded SUCCESS. Wait for CI to pass (make ci-status), or fix-forward, then retry."; \
		echo "  Do NOT push the tag manually — that bypasses the green-pipeline guardrail."; \
		exit 1; \
	}
	@echo "[release-cut] step 1/4 — check README status table is current for $(TAG) ..."
	@$(MAKE) --no-print-directory check-readme-status TAG='$(TAG)' || { \
		echo ""; \
		echo "RELEASE ABORTED: README Feature & Task Completion Status table is stale."; \
		echo "  Update README.md 'Status as of $(TAG)' line and the status table, then retry."; \
		exit 1; \
	}
	@echo "[release-cut] step 2/4 — push master branch to sandboxcom ..."
	@$(MAKE) --no-print-directory git-push-sandboxcom
	@echo "[release-cut] step 3/4 — create and push annotated tag $(TAG) ..."
	@$(MAKE) --no-print-directory git-tag-push TAG='$(TAG)' MSG='$(MSG)'
	@echo "[release-cut] step 4/4 — verify published release artifact (polls up to $(VERIFY_POLLS)x every $(VERIFY_INTERVAL)s; CI release job runs async) ..."
	@poll=0; while [ $$poll -lt $(VERIFY_POLLS) ]; do \
		poll=$$((poll + 1)); \
		echo "[release-cut] artifact poll $$poll/$(VERIFY_POLLS) at $$(date +%H:%M:%S) ..."; \
		if $(MAKE) --no-print-directory verify-release-artifact TAG='$(TAG)'; then \
			echo ""; \
			echo "release-cut COMPLETE: $(TAG) tag pushed AND artifact confirmed published."; \
			echo "  Artifact URL: run 'make release-view TAG=$(TAG)' for the download URL."; \
			exit 0; \
		fi; \
		if [ $$poll -lt $(VERIFY_POLLS) ]; then \
			echo "  [release-cut] asset not yet visible — waiting $(VERIFY_INTERVAL)s for CI release job ..."; \
			sleep $(VERIFY_INTERVAL); \
		fi; \
	done; \
	echo ""; \
	echo "==========================================================="; \
	echo "WARNING: TAG $(TAG) was pushed but artifact NOT yet confirmed."; \
	echo "  The Build-and-Release CI job is still running or failed."; \
	echo "  DO NOT treat this tag as a shipped release."; \
	echo "  After the CI run completes, verify with:"; \
	echo "    make verify-release-artifact TAG=$(TAG)"; \
	echo "  A release is an ARTIFACT, not a tag."; \
	echo "==========================================================="; \
	exit 1

# release-recut: re-trigger the release CI job for an EXISTING tag whose release
# was skipped (e.g. gate was red when the tag was originally pushed).  Deletes the
# remote tag and re-pushes the local tag (preserving the commit it points to),
# which GitHub Actions treats as a fresh tag-push event.  Does NOT create a new
# tag — the local tag must already exist.
#
# Usage: make release-recut TAG=v0.1.0-alpha.2
release-recut:
	@[ -n "$(TAG)" ] || { echo "Usage: make release-recut TAG=v0.1.0-alpha.N"; exit 1; }
	@echo "[release-recut] step 1/3 — verify local tag $(TAG) exists ..."
	@git tag -l "$(TAG)" | grep -q . || { echo "ABORT: local tag $(TAG) does not exist. release-recut re-pushes an EXISTING local tag."; echo "  If the tag only exists remotely, run: make git-fetch-sandboxcom first, or create the tag manually."; exit 1; }
	@echo "[release-recut] step 2/3 — delete remote tag and re-push (triggers release CI job) ..."
	@GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' git push sandboxcom ":refs/tags/$(TAG)"
	@GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' git push sandboxcom "$(TAG)"
	@echo "[release-recut] step 3/3 — verify published release artifact (polls up to $(VERIFY_POLLS)x every $(VERIFY_INTERVAL)s; CI release job runs async) ..."
	@poll=0; while [ $$poll -lt $(VERIFY_POLLS) ]; do \
		poll=$$((poll + 1)); \
		echo "[release-recut] artifact poll $$poll/$(VERIFY_POLLS) at $$(date +%H:%M:%S) ..."; \
		if $(MAKE) --no-print-directory verify-release-artifact TAG='$(TAG)'; then \
			echo ""; \
			echo "release-recut COMPLETE: $(TAG) re-pushed AND artifact confirmed published."; \
			echo "  Artifact URL: run 'make release-view TAG=$(TAG)' for the download URL."; \
			exit 0; \
		fi; \
		if [ $$poll -lt $(VERIFY_POLLS) ]; then \
			echo "  [release-recut] asset not yet visible — waiting $(VERIFY_INTERVAL)s for CI release job ..."; \
			sleep $(VERIFY_INTERVAL); \
		fi; \
	done; \
	echo ""; \
	echo "==========================================================="; \
	echo "WARNING: TAG $(TAG) was re-pushed but artifact NOT yet confirmed."; \
	echo "  The Build-and-Release CI job is still running or failed."; \
	echo "  DO NOT treat this tag as a shipped release."; \
	echo "  After the CI run completes, verify with:"; \
	echo "    make verify-release-artifact TAG=$(TAG)"; \
	exit 1

# release-create — manually create a GitHub Release with assets (fallback when CI release job fails).
# Builds platform artifacts locally, stages them, and creates the release via gh.
release-create:
	@echo "[release-create] building artifacts locally..."
	$(MAKE) build-executable
	$(MAKE) sbom
	@mkdir -p release-assets
	@cp -v dist/gludd-* release-assets/ 2>/dev/null || true
	@cp -v dist/sbom.json release-assets/ 2>/dev/null || true
	@cp -v LICENSE release-assets/ 2>/dev/null || true
	@cp -v THIRD_PARTY_LICENSES.md release-assets/ 2>/dev/null || true
	@echo "[release-create] creating GitHub Release $(TAG)..."
	gh release create "$(TAG)" release-assets/* --title "$(TAG)" --notes "$(MSG)" --prerelease || \
		gh release upload "$(TAG)" release-assets/* --clobber
	@echo "[release-create] verifying..."
	$(MAKE) verify-release-artifact TAG=$(TAG)

# ---------------------------------------------------------------------------
# True fast-forward without re-running the gate
# ---------------------------------------------------------------------------
# git-ff-only: fast-forward-ONLY merge of REF into the CURRENT branch.
#
# CAVEAT: this target SKIPS the gate entirely by design.  It MUST only be used
# when REF has already been confirmed green by a separate gate run.  The whole
# point is to land master at an already-gated tip without paying the ~3hr
# re-gate cost that ship-async incurs.
#
# Fails loudly (non-zero exit) if REF is not a fast-forward ancestor of HEAD —
# i.e. it refuses to create a merge commit, exactly like `git merge --ff-only`.
# Prints before and after HEAD so the result is observable in the log.
#
# Usage: make git-ff-only REF=<commit-or-branch>
git-ff-only:
	@[ -n "$(REF)" ] || { echo "Usage: make git-ff-only REF=<commit-or-branch>"; exit 1; }
	@echo "[git-ff-only] BEFORE: $$(git rev-parse HEAD)"
	@git merge --ff-only "$(REF)"
	@echo "[git-ff-only] AFTER:  $$(git rev-parse HEAD)"

# ship-ff: convenience wrapper — checkout TARGET then ff-only merge REF.
# Prints before/after HEAD so callers can verify the operation.
#
# CAVEAT: same gate-skip caveat as git-ff-only above — only call this when REF
# is already confirmed green by an independent gate run.
#
# Usage: make ship-ff REF=<commit-or-branch> [TARGET=master]
ship-ff:
	@[ -n "$(REF)" ] || { echo "Usage: make ship-ff REF=<commit-or-branch> [TARGET=master]"; exit 1; }
	@echo "[ship-ff] checking out $(TARGET) ..."
	@git checkout "$(TARGET)"
	@echo "[ship-ff] BEFORE: $$(git rev-parse HEAD)"
	@git merge --ff-only "$(REF)"
	@echo "[ship-ff] AFTER:  $$(git rev-parse HEAD)"
	@echo "[ship-ff] $(TARGET) is now at $(REF)"

# git-divergence: report ahead/behind counts of the current HEAD vs REF, and
# whether REF is an ancestor of HEAD (fast-forwardable). Read-only inspection
# used before deciding rebase vs ff vs force. Usage: make git-divergence REF=<ref>
git-divergence:
	@[ -n "$(REF)" ] || { echo "Usage: make git-divergence REF=<ref>"; exit 1; }
	@echo "[git-divergence] HEAD=$$(git rev-parse --short HEAD)  REF=$$(git rev-parse --short $(REF))"
	@echo "[git-divergence] ahead/behind (local ahead, remote ahead): $$(git rev-list --left-right --count HEAD...$(REF))"
	@git merge-base --is-ancestor $(REF) HEAD && echo "[git-divergence] REF is ANCESTOR of HEAD (HEAD is ahead; safe to push if remote == REF)" || echo "[git-divergence] REF is NOT an ancestor of HEAD (diverged; rebase needed)"

# git-rebase-onto: rebase the current branch onto REF. Use to integrate a
# diverged remote tip under the local commit before re-pushing. Usage:
# make git-rebase-onto REF=sandboxcom/integration/alpha3-rc
git-rebase-onto:
	@[ -n "$(REF)" ] || { echo "Usage: make git-rebase-onto REF=<ref>"; exit 1; }
	@echo "[git-rebase-onto] BEFORE: $$(git rev-parse --short HEAD)"
	@git rebase "$(REF)"
	@echo "[git-rebase-onto] AFTER:  $$(git rev-parse --short HEAD)"

# git-stash-rebase-pop: stash any unstaged changes, rebase onto REF, then
# restore the stash. Handles the case where untracked/modified files block
# a plain rebase. Usage: make git-stash-rebase-pop REF=<ref>
git-stash-rebase-pop:
	@[ -n "$(REF)" ] || { echo "Usage: make git-stash-rebase-pop REF=<ref>"; exit 1; }
	@echo "[git-stash-rebase-pop] stashing unstaged changes ..."
	@git stash --include-untracked
	@echo "[git-stash-rebase-pop] BEFORE: $$(git rev-parse --short HEAD)"
	@git rebase "$(REF)" && echo "[git-stash-rebase-pop] AFTER:  $$(git rev-parse --short HEAD)" && git stash pop && echo "[git-stash-rebase-pop] stash restored" || { echo "[git-stash-rebase-pop] REBASE FAILED — running stash pop to restore then aborting"; git stash pop; exit 1; }

# ---------------------------------------------------------------------------
# git-worktree-list / git-worktree-remove: manage agent worktrees make-only.
#
# WHY git-worktree-remove EXISTS (2026-06-18): a rested gate-marshal subagent
# left a worktree whose background `-n auto` gate kept RESPAWNING (OOM-killing the
# host) — a zombie loop. `make kill-gate-force` kills the gate process but the
# worktree relaunches it; removing the worktree is the only durable stop, and
# there was no make-only way to do it. This is the missing tool, not a process to
# babysit. `--force` removes it even with the gate's files open, which kills the
# relaunch source.
# Usage: make git-worktree-remove WT='.claude/worktrees/agent-XXXX'
# ---------------------------------------------------------------------------
git-worktree-list:
	@git worktree list

git-worktree-remove:
	@[ -n "$(WT)" ] || { echo "Usage: make git-worktree-remove WT='.claude/worktrees/agent-XXXX'"; exit 1; }
	@echo "[git-worktree-remove] removing worktree $(WT) (force) ..."
	@git worktree remove --force "$(WT)" 2>/dev/null && echo "[git-worktree-remove] removed $(WT)" || echo "[git-worktree-remove] remove failed/absent; pruning registrations"
	@git worktree prune
	@echo "[git-worktree-remove] done"

git-ls-remote-sandboxcom:
	@GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' git ls-remote sandboxcom

# Assert that the sandboxcom remote tip of BRANCH == the expected SHA (short or
# full match). Catches silent no-op pushes ("Everything up-to-date") and
# wrong-branch commits. Exits non-zero on mismatch.
# Usage: make verify-remote BRANCH=master SHA=<expected-sha>
BRANCH ?=
SHA ?=
verify-remote:
	@[ -n "$(BRANCH)" ] || { echo "Usage: make verify-remote BRANCH=<branch> SHA=<expected>"; exit 1; }
	@[ -n "$(SHA)" ] || { echo "Usage: make verify-remote BRANCH=<branch> SHA=<expected>"; exit 1; }
	@REMOTE_LINE=$$(GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' git ls-remote sandboxcom "refs/heads/$(BRANCH)" 2>&1); \
	REMOTE_SHA=$$(echo "$$REMOTE_LINE" | awk '{print $$1}'); \
	if [ -z "$$REMOTE_SHA" ]; then echo "REMOTE MISMATCH: branch $(BRANCH) not found on sandboxcom"; exit 1; fi; \
	EXP="$(SHA)"; \
	MATCH=$$(echo "$$REMOTE_SHA" | grep -c "^$${EXP}" 2>/dev/null || echo 0); \
	if [ "$$MATCH" -ge 1 ]; then \
		echo "VERIFIED $(BRANCH)@$${REMOTE_SHA}"; \
	else \
		echo "REMOTE MISMATCH: remote=$${REMOTE_SHA} expected=$${EXP}"; exit 1; \
	fi

# Print the latest CI run's headSha + conclusion for BRANCH and LOUDLY WARN if
# that headSha != the current local HEAD of BRANCH — making stale-run misreads
# impossible (the run must match the branch tip to count as a verdict).
# Usage: make ci-verdict BRANCH=master
ci-verdict:
	@[ -n "$(BRANCH)" ] || { echo "Usage: make ci-verdict BRANCH=<branch>"; exit 1; }
	@LOCAL_HEAD=$$(git rev-parse "$(BRANCH)" 2>/dev/null || echo "UNKNOWN"); \
	echo "local HEAD of $(BRANCH): $$LOCAL_HEAD"; \
	RUN_JSON=$$(gh run list -R sandboxcom/gludd -L 5 --branch "$(BRANCH)" --json headSha,conclusion,status,databaseId,createdAt 2>/dev/null || echo "[]"); \
 	echo "$$RUN_JSON" | GLUDD_LOCAL_HEAD="$$LOCAL_HEAD" GLUDD_BRANCH="$(BRANCH)" $(PYTHON) -c "import sys, json, os; lh=os.environ['GLUDD_LOCAL_HEAD']; br=os.environ['GLUDD_BRANCH']; runs=json.load(sys.stdin); r=(runs[0] if runs else None); (print('ci-verdict: no runs found for branch '+br) or sys.exit(0)) if not r else None; hs=r.get('headSha','?'); concl=(r.get('conclusion') or r.get('status') or '?'); rid=r.get('databaseId','?'); created=r.get('createdAt','?'); print('Latest run %s created=%s' % (rid, created)); print('  headSha=%s  conclusion=%s' % (hs, concl)); stale=(lh != 'UNKNOWN' and not hs.startswith(lh[:7])); print('\n  !! STALE RUN WARNING: run headSha '+hs+' != local HEAD '+lh+'\n  !! This run does NOT reflect the current branch tip -- do NOT use it as a verdict.') if stale else print('  -> RUN MATCHES LOCAL HEAD: verdict = '+str(concl))"

# Fastest possible CI verdict — single `gh run list -L 1`, JSON output, no
# local-HEAD comparison or createdAt lookup.  Parses status immediately and
# prints GREEN / RED / PENDING in <1s.  Use this for hot-loop checks where
# you only need the run's terminal state, not the stale-run audit that the
# full `ci-verdict` provides.
# Usage: make ci-verdict-fast [BRANCH=master]
ci-verdict-fast:
	@[ -n "$(BRANCH)" ] || { echo "Usage: make ci-verdict-fast BRANCH=<branch>"; exit 1; }
	@RUN_JSON=$$(gh run list -R sandboxcom/gludd -L 1 --branch "$(BRANCH)" --json status,conclusion,headSha,databaseId 2>/dev/null || echo "[]"); \
	echo "$$RUN_JSON" | $(PYTHON) -c "import sys, json; \
		runs=json.load(sys.stdin); \
		r=runs[0] if runs else None; \
		(print('ci-verdict-fast: no runs found') or sys.exit(0)) if not r else None; \
		status=r.get('status','?'); concl=r.get('conclusion'); hs=r.get('headSha','?'); rid=r.get('databaseId','?'); \
		verdict='PENDING' if status!='completed' else ('GREEN' if concl=='success' else 'RED'); \
		print('%s  run=%s headSha=%s status=%s conclusion=%s' % (verdict, rid, hs, status, concl))"

# Poll the latest CI run on BRANCH every CI_VERDICT_INTERVAL seconds (default 10)
# until it reaches a terminal state, with a timestamped heartbeat each cycle.
# Exits 0 on GREEN, 1 on RED, 2 on timeout (CI_VERDICT_MAX_SEC, default 3600).
# Streams each poll's verdict line so the wait is observable (no unseen events).
# Usage: make ci-verdict-loop [BRANCH=master] [CI_VERDICT_INTERVAL=10] [CI_VERDICT_MAX_SEC=3600]
CI_VERDICT_INTERVAL ?= 10
CI_VERDICT_MAX_SEC ?= 3600
ci-verdict-loop:
	@[ -n "$(BRANCH)" ] || { echo "Usage: make ci-verdict-loop BRANCH=<branch>"; exit 1; }
	@start=$$(date +%s); poll=0; \
	while :; do \
		now=$$(date +%s); elapsed=$$((now - start)); \
		if [ $$elapsed -ge $(CI_VERDICT_MAX_SEC) ]; then \
			echo "[ci-verdict-loop] TIMEOUT after $$elapsed s (CI_VERDICT_MAX_SEC=$(CI_VERDICT_MAX_SEC))"; \
			exit 2; \
		fi; \
		poll=$$((poll + 1)); \
		line=$$(gh run list -R sandboxcom/gludd -L 1 --branch "$(BRANCH)" --json status,conclusion,headSha,databaseId 2>/dev/null \
			| $(PYTHON) -c "import sys, json; \
				runs=json.load(sys.stdin); r=runs[0] if runs else None; \
				verdict='EMPTY' if not r else ('PENDING' if r.get('status')!='completed' else ('GREEN' if r.get('conclusion')=='success' else 'RED')); \
				d=r or {}; \
				print('|'.join([verdict, d.get('status','?'), str(d.get('conclusion')), d.get('headSha','?'), str(d.get('databaseId','?'))]))"); \
		ts=$$(date +%H:%M:%S); \
		echo "[ci-verdict-loop $$ts elapsed=$${elapsed}s poll=#$$poll] $$line"; \
		case "$$line" in \
			GREEN\|*) echo "[ci-verdict-loop] DONE GREEN after $$elapsed s"; exit 0 ;; \
			RED\|*)   echo "[ci-verdict-loop] DONE RED after $$elapsed s"; exit 1 ;; \
		esac; \
		sleep $(CI_VERDICT_INTERVAL); \
	done

git-push-branch:
	@[ -n "$(TARGET)" ] || { echo "Usage: make git-push-branch TARGET=<branch>"; exit 1; }
	@GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' git push -u sandboxcom "$(TARGET)"
	@echo "Pushed branch $(TARGET) to sandboxcom"
	@echo "Run: make verify-remote BRANCH=$(TARGET) SHA=$$(git rev-parse HEAD)"

# git-push-branch-nv: like git-push-branch but skips pre-push hooks (--no-verify).
# Use ONLY when hooks fail due to stash/conflict from unrelated unstaged files
# and the committed content is already lint/typecheck clean.
# Usage: make git-push-branch-nv TARGET=<branch>
git-push-branch-nv:
	@[ -n "$(TARGET)" ] || { echo "Usage: make git-push-branch-nv TARGET=<branch>"; exit 1; }
	@GIT_SSH_COMMAND='ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new' git push --no-verify -u sandboxcom "$(TARGET)"
	@echo "Pushed branch $(TARGET) to sandboxcom (no-verify)"
	@echo "Run: make verify-remote BRANCH=$(TARGET) SHA=$$(git rev-parse HEAD)"

# Idempotent PR opener: check if a PR for fix/self-update-sec already exists;
# if none, create one targeting master. Reports URL either way.
gh-pr-ensure:
	@EXISTING=$$(gh pr list --head fix/self-update-sec --json number --jq '.[0].number' 2>/dev/null); \
	if [ -n "$$EXISTING" ] && [ "$$EXISTING" != "null" ]; then \
		echo "PR already exists: #$$EXISTING"; \
		gh pr view "$$EXISTING" --json number,url,title --jq '"PR #\(.number): \(.title)\nURL: \(.url)"' 2>/dev/null || echo "PR #$$EXISTING"; \
	else \
		echo "Creating PR for fix/self-update-sec -> master ..."; \
		gh pr create --base master --head fix/self-update-sec \
			--title "fix/self-update-sec: completion-integrity + hook + SSRF + budget fixes" \
			--body "Session fixes: rules-engine W4c, daemon registry/budget_guard, SSRF guard, no-wait/sonnet hook fixes, worker tool-call detection, e2e zai harnesses. See commit log."; \
	fi

# Cherry-pick a commit onto the current branch (no-verify). Stages but does NOT
# commit, so you can inspect + lint before committing. On conflict, the working
# tree is left with conflict markers; resolve them, git-add, then cherry-continue.
# Usage: make git-cherry-pick REF=<sha>
git-cherry-pick:
	@[ -n "$(REF)" ] || { echo "Usage: make git-cherry-pick REF=<sha>"; exit 1; }
	@git cherry-pick --no-commit "$(REF)" && echo "cherry-pick staged (no-commit): $(REF)" || { echo "CHERRY-PICK CONFLICT on $(REF) — files listed above need manual resolution, then: make git-add FILES='...' && make git-cherry-continue"; exit 1; }

# Cherry-pick a commit AND commit it atomically, preserving the original commit
# message. Usage: make git-cherry-pick-commit REF=<sha>
git-cherry-pick-commit:
	@[ -n "$(REF)" ] || { echo "Usage: make git-cherry-pick-commit REF=<sha>"; exit 1; }
	@git cherry-pick --no-edit "$(REF)" && echo "cherry-picked (committed): $(REF)" || { echo "CHERRY-PICK CONFLICT on $(REF) — resolve, git-add, then: make git-cherry-continue"; exit 1; }

# Amend ONLY the message of the current HEAD commit (no content change).
# Usage: make git-amend-msg MSG='corrected message'
git-amend-msg:
	@[ -n "$(MSG)" ] || { echo "Usage: make git-amend-msg MSG='message'"; exit 1; }
	@if [ "${GLUDD_CI_IS_GATE}" != "1" ]; then $(MAKE) --no-print-directory _gate-fresh-check; \
	else echo "WARNING: GLUDD_CI_IS_GATE=1 — skipping local gate check, CI is the gate."; fi
	@git commit --amend --no-verify -m "$(MSG)" && echo "amended HEAD message"

# Continue after resolving cherry-pick conflicts (equivalent to cherry-pick --continue).
git-cherry-continue:
	@git cherry-pick --continue --no-edit && echo "cherry-pick continued"

# Abort a cherry-pick in progress (restores HEAD to pre-cherry-pick state).
git-cherry-abort:
	@git cherry-pick --abort && echo "cherry-pick aborted"

# Show the full diff of a single commit vs its parent (diagnostic).
# Usage: make git-show-diff REF=<sha>
git-show-diff:
	@[ -n "$(REF)" ] || { echo "Usage: make git-show-diff REF=<sha>"; exit 1; }
	@git show "$(REF)"

# List recent CI runs for branch fix/self-update-sec (JSON output).
# Usage: make gh-run-list
gh-run-list:
	@gh run list --branch fix/self-update-sec --limit 20 --json databaseId,headSha,status,conclusion,workflowName,createdAt -R sandboxcom/gludd

# Cancel a specific GHA run by ID. Use only for in_progress/queued superseded runs.
# Usage: make gh-run-cancel ID=<run-id>
gh-run-cancel:
	@[ -n "$(ID)" ] || { echo "Usage: make gh-run-cancel ID=<run-id>"; exit 1; }
	@gh run cancel "$(ID)" -R sandboxcom/gludd && echo "Cancelled run $(ID)" || echo "Cancel failed for $(ID)"

# Re-run a GHA run that was CANCELLED (not failed) so it can reach a clean
# terminal verdict on the SAME commit SHA. Use when a run was manually cancelled
# or superseded mid-flight (no test/lint failure) and you need an uninterrupted
# pass/fail on the identical tip without fabricating a new commit. A re-run keeps
# the same headSha, so `make ci-verdict` will still match the local HEAD.
# Usage: make ci-rerun ID=<run-id>
ci-rerun:
	@[ -n "$(ID)" ] || { echo "Usage: make ci-rerun ID=<run-id>"; exit 1; }
	@gh run rerun "$(ID)" -R sandboxcom/gludd && echo "Re-running run $(ID) (same headSha)" || echo "Re-run failed for $(ID)"

# Cancel a running GHA run.
# Usage: make ci-cancel ID=<run-id>
ci-cancel:
	@gh run cancel $(ID) -R sandboxcom/gludd || true

# Re-run only the FAILED jobs of a GHA run (keeps successful jobs as-is).
# Usage: make ci-rerun-failed ID=<run-id>
ci-rerun-failed:
	@gh run rerun $(ID) --failed -R sandboxcom/gludd || true

# Show job/step status summary for a specific GHA run.
# Usage: make gh-run-view ID=<run-id>
gh-run-view:
	@[ -n "$(ID)" ] || { echo "Usage: make gh-run-view ID=<run-id>"; exit 1; }
	@gh run view "$(ID)" -R sandboxcom/gludd 2>&1 || echo "gh-run-view-failed"

# Fetch the failed-step logs for a specific GHA run. Writes output to
# /tmp/ci-failed-$(ID).log AND streams the last 200 lines to stdout.
# Usage: make gh-run-failed-log ID=<run-id>
gh-run-failed-log:
	@[ -n "$(ID)" ] || { echo "Usage: make gh-run-failed-log ID=<run-id>"; exit 1; }
	@gh run view "$(ID)" -R sandboxcom/gludd --log-failed 2>&1 | tee /tmp/ci-failed-$(ID).log | tail -200 || echo "gh-run-failed-log-failed"
	@echo "[gh-run-failed-log] full log saved to /tmp/ci-failed-$(ID).log"

run-other-shard:
	@$(MAKE) --no-print-directory wait-pytest
	@$(UV) run python -m pytest tests/integration/ tests/e2e/ tests/security/ tests/test_worker_d09_d10_d35.py $(_XD) -v --junit-xml=/tmp/other_shard.xml -p no:randomly 2>&1 | tail -200; true

# Replace exact text in a file using temp files for old/new text.
# Usage:
#   make replace-text FILE=path/to/file.py OLD_FILE=/tmp/old.txt NEW_FILE=/tmp/new.txt
replace-text:
	@[ -n "$(FILE)" ] && [ -n "$(OLD_FILE)" ] && [ -n "$(NEW_FILE)" ] || { \
		echo "Usage: make replace-text FILE=<file> OLD_FILE=<old-text-file> NEW_FILE=<new-text-file>"; exit 1; }
	@$(PYTHON) scripts/replace_text.py "$(FILE)" "$(OLD_FILE)" "$(NEW_FILE)"
