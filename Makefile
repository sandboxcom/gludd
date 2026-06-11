SEARCH ?= hello world
MAX_RESULTS ?= 10
FORMAT ?= text
SEARCH_SCRIPT := scripts/search.py
MSG ?=
FILES ?=
TESTFILE ?=

PYTHON := python3
UV := uv
PROJECT_SRC := src/general_ludd
TESTS_DIR := tests

    .PHONY: search-google search-json \
        init sync install-pip lint lint-fix test test-unit test-specific test-count test-integration test-e2e \
        test-guardrails test-scripts test-db test-live-zai test-tui-daemon diag-gunicorn \
        typecheck setup-dirs setup-venv clean healthcheck \
        bootstrap skeleton version check-uv check-pytest \
        ansible-syntax ansible-lint-playbooks playbook-list \
        git-status git-init git-add git-commit git-log git-diff git-reset \
        git-branch git-checkout git-merge git-staged \
        repo-status repo-diff repo-staged repo-log \
		feature-start feature-done test-and-commit preflight \
		molecule-version molecule-test \
		container-build container-run container-push \
        build-executable dist dist-clean bundle-binaries \
        sast sbom pip-audit security \
        audit-messages qa validate collect-check gate smoke install-hooks \
        status-snapshot audit-evidence \
        skill-install skill-list bootstrap-skills \
        scan-secrets scan-secrets-baseline clean-untracked clean-hooks \
        git-remote-sandboxcom git-push-sandboxcom git-add-all help

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
	@echo ""
	@echo "  --- Other ---"
	@echo "  smoke                 Quick daemon boot health check"
	@echo "  clean                 Remove build artifacts"
	@echo "  dist-clean            Remove distribution artifacts"

search-google:
	@$(PYTHON) $(SEARCH_SCRIPT) "$(SEARCH)" -n $(MAX_RESULTS) -f $(FORMAT)

search-json:
	@$(PYTHON) $(SEARCH_SCRIPT) "$(SEARCH)" -n $(MAX_RESULTS) -f json

skeleton:
	@$(PYTHON) scripts/skeleton.py

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
	@$(UV) run python -m pytest tests/ --cov=general_ludd --cov-report=term-missing --cov-report=xml -v

test-unit:
	@if [ -n "$(TESTFILE)" ]; then \
		$(UV) run python -m pytest $(TESTFILE) -v; \
	else \
		$(UV) run python -m pytest tests/unit/ -v; \
	fi

test-specific:
	@if [ -z "$(TESTFILE)" ]; then echo "Usage: make test-specific TESTFILE='tests/unit/test_foo.py::TestClass::test_method'"; exit 1; fi
	@$(UV) run python -m pytest $(TESTFILE) -v

test-count:
	@$(UV) run python -m pytest tests/ --co -q 2>&1 | tail -3

test-failures:
	@$(UV) run python -m pytest tests/ -q 2>&1 | tee /tmp/gludd-test-output.txt; EXIT=$$?; \
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
	@echo "=== GATE $(shell date -u +%Y-%m-%dT%H:%M:%SZ) ===" > .gate-status
	@echo "epoch $(shell date +%s)" >> .gate-status
	@printf "lint " >> .gate-status
	@if $(UV) run ruff check src tests --output-format concise > /dev/null 2>&1; then \
		echo "PASS 0" >> .gate-status; \
	else \
		echo "FAIL $$($(UV) run ruff check src tests --output-format concise 2>&1 | grep -c .)" >> .gate-status && touch .gate-failed; \
	fi
	@printf "typecheck " >> .gate-status
	@TC_ERRS=$$($(UV) run mypy src 2>&1 | grep -c 'error:' || echo 0); \
	if [ $$TC_ERRS -le 18 ]; then echo "PASS $$TC_ERRS" >> .gate-status; else echo "FAIL $$TC_ERRS" >> .gate-status && touch .gate-failed; fi
	@printf "collect " >> .gate-status
	@$(MAKE) --no-print-directory collect-check > /dev/null 2>&1 && echo "PASS 0" >> .gate-status || (echo "FAIL collection-errors" >> .gate-status && touch .gate-failed)
	@printf "test " >> .gate-status
	@$(UV) run python -m pytest tests/ -q 2>&1 > /tmp/gludd-test-gate.txt; EXIT=$$?; \
	if [ $$EXIT -eq 0 ]; then echo "PASS 0" >> .gate-status; else \
		echo "FAIL non-zero-exit" >> .gate-status && touch .gate-failed; \
	fi
	@printf "smoke " >> .gate-status
	@$(MAKE) --no-print-directory smoke > /dev/null 2>&1 && echo "PASS" >> .gate-status || (echo "FAIL" >> .gate-status && touch .gate-failed)
	@echo "---" >> .gate-status
	@cat .gate-status
	@if [ -f .gate-failed ]; then rm -f .gate-failed; exit 1; fi
	@echo "Gate: ALL PASSED"

test-integration:
	@$(UV) run python -m pytest tests/integration/ -v

test-e2e:
	@$(UV) run python -m pytest tests/e2e/ -v

test-tui-daemon:
	@$(UV) run python -m pytest tests/e2e/test_tui_daemon_start.py -v -s

test-guardrails:
	@$(UV) run python -m pytest tests/unit/test_guardrails.py tests/unit/test_user_requested_guardrails.py -v

test-db:
	@$(UV) run python -m pytest tests/unit/test_db_models.py -v

test-scripts:
	@$(UV) run python -m pytest tests/unit/test_guardrails.py::TestSkeletonScript -v

healthcheck:
	@$(UV) run python -c "from general_ludd.worker.app import create_app; app = create_app(); print('Worker app factory OK')"
	@$(UV) run python -c "from general_ludd.event_loop.loop import EventLoop; print('Event loop import OK')"

ansible-syntax:
	@for f in playbooks/*.yml; do echo "Checking $$f..."; $(UV) run ansible-playbook --syntax-check "$$f" || exit 1; done

ansible-lint-playbooks:
	@$(UV) run ansible-lint playbooks/roles || true

playbook-list:
	@ls -1 playbooks/*.yml 2>/dev/null || echo "No playbooks found"

molecule-version:
	@$(UV) run molecule --version

molecule-test:
	@if [ -z "$(SCENARIO)" ]; then echo "Usage: make molecule-test SCENARIO=noop|prompt_eval|runtime_validate"; exit 1; fi
	@echo "Running molecule scenario: $(SCENARIO)"
	@cp -r "molecule/playbooks/$(SCENARIO)/default" "molecule/$(SCENARIO)/default" 2>/dev/null; \
	mkdir -p "molecule/$(SCENARIO)/default"; \
	cp "molecule/playbooks/$(SCENARIO)/molecule.yml" "molecule/$(SCENARIO)/"; \
	cp "molecule/playbooks/$(SCENARIO)/default"/*.yml "molecule/$(SCENARIO)/default/" 2>/dev/null; \
	$(UV) run molecule test -s "$(SCENARIO)"; \
	EXIT_CODE=$$?; \
	rm -rf "molecule/$(SCENARIO)"; \
	exit $$EXIT_CODE

git-status:
	@git status --short || echo "Not a git repo"

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
	@echo "<!-- gate:begin -->" > /tmp/gludd-status-snippet.md
	@echo "## Current Gate Status ($(shell date -u +%Y-%m-%d))" >> /tmp/gludd-status-snippet.md
	@echo "" >> /tmp/gludd-status-snippet.md
	@if [ -f .gate-status ]; then \
		sed 's/^/- /' .gate-status >> /tmp/gludd-status-snippet.md; \
	else \
		echo "- No .gate-status file. Run 'make gate' first." >> /tmp/gludd-status-snippet.md; \
	fi
	@echo "" >> /tmp/gludd-status-snippet.md
	@echo "<!-- gate:end -->" >> /tmp/gludd-status-snippet.md
	@echo "Status snapshot written to /tmp/gludd-status-snippet.md"
	@echo "Replace the gate block in SESSION.md with this content."

audit-evidence:
	@echo "=== Evidence Audit ==="
	@if [ ! -f TASKS.md ]; then echo "TASKS.md missing"; exit 1; fi
	@grep -oP 'tests/[^ :]+::\S+' TASKS.md | sort -u > /tmp/gludd-evidence-tests.txt
	@if [ ! -s /tmp/gludd-evidence-tests.txt ]; then \
		echo "No test evidence references found in TASKS.md"; \
		exit 0; \
	fi
	@echo "Running evidence tests..."
	@$(UV) run python -m pytest $$(cat /tmp/gludd-evidence-tests.txt) -q 2>&1 || echo "Some evidence tests failed — see above."
	@echo "=== Evidence Audit Complete ==="

git-log:
	@git log --oneline -10 || echo "No git history"

audit-messages:
	@$(PYTHON) scripts/audit_messages.py 2>&1 || echo "No opencode database found"

audit-schema:
	@$(PYTHON) scripts/db_schema.py

repo-log:
	@git log --oneline -10 || echo "No git history"

git-add:
	@if [ -z "$(FILES)" ]; then echo "Usage: make git-add FILES='file1 file2 ...'"; exit 1; fi
	@git add $(FILES)

git-add-all:
	@git add -A

repo-add-all:
	@git add -A

commit-bootstrap:
	@if [ -z "$(MSG)" ]; then echo "Usage: make commit-bootstrap MSG='message'"; exit 1; fi
	@git diff --cached --quiet && echo "Nothing to commit" || git commit -m "$(MSG)"

smoke:
	@echo "=== SMOKE TEST: real daemon boot ==="
	@PORT=$$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()") && \
	echo "Using port $$PORT" && \
	trap 'kill $$PID 2>/dev/null; echo "Daemon stopped (cleanup)"' EXIT && \
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
	kill $$PID 2>/dev/null && \
	echo "Daemon stopped" && \
	trap - EXIT && \
	echo "=== SMOKE: PASSED ==="

install-hooks:
	@PIP_INDEX_URL=https://pypi.org/simple $(UV) run pre-commit install --install-hooks
	@PIP_INDEX_URL=https://pypi.org/simple $(UV) run pre-commit install --hook-type pre-push
	@echo "pre-commit hooks installed: secrets-scan, ruff, collect-check (pre-commit), gate (pre-push)"

scan-secrets-baseline:
	@$(UV) run detect-secrets scan --all-files --exclude-files 'sandboxcom_github_rsa|sandboxcom_github_rsa.pub' 2>/dev/null > .secrets.baseline || true
	@echo "Secrets baseline created in .secrets.baseline"

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

scan-secrets:
	@$(UV) run detect-secrets scan --baseline .secrets.baseline $(ARGS)

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
	@$(UV) run python -m pytest tests/ -q
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
	@$(UV) run python -m pytest tests/ --cov=general_ludd -q
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

dist: build-executable bundle-binaries
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
	@mkdir -p $(TARBALL_DIR)/docs
	@if [ -f docs/quickstart.md ]; then cp docs/quickstart.md $(TARBALL_DIR)/docs/; fi
	@if [ -f docs/configuration.md ]; then cp docs/configuration.md $(TARBALL_DIR)/docs/; fi
	@if [ -f docs/architecture.md ]; then cp docs/architecture.md $(TARBALL_DIR)/docs/; fi
	@if [ -f docs/model-setup.md ]; then cp docs/model-setup.md $(TARBALL_DIR)/docs/; fi
	@cd dist && tar czf $(TARBALL_NAME).tar.gz $(TARBALL_NAME)
	@cd dist && shasum -a 256 $(TARBALL_NAME).tar.gz > $(TARBALL_NAME).tar.gz.sha256
	@echo "Created dist/$(TARBALL_NAME).tar.gz"
	@echo "Checksum: dist/$(TARBALL_NAME).tar.gz.sha256"

dist-clean:
	@rm -rf dist/general-ludd-agent-* dist/hottentot-agent-* dist/gludd dist/hottentot build

bundle-binaries:
	@echo "Bundling OpenBao and OpenTofu binaries into dist/binaries..."
	@mkdir -p dist/binaries
	@$(UV) run python scripts/download_bundled_binaries.py || echo "Some binaries could not be downloaded (network unavailable?). The dist will still include what was bundled."

container-build:
	@if [ -z "$(CONTAINER_RUNTIME)" ]; then echo "ERROR: podman or docker not found"; exit 1; fi
	@$(CONTAINER_RUNTIME) build -t $(CONTAINER_IMAGE) .

container-run:
	@if [ -z "$(CONTAINER_RUNTIME)" ]; then echo "ERROR: podman or docker not found"; exit 1; fi
	@$(CONTAINER_RUNTIME) run -p 8000:8000 $(CONTAINER_IMAGE)

container-push:
	@if [ -z "$(CONTAINER_RUNTIME)" ]; then echo "ERROR: podman or docker not found"; exit 1; fi
	@$(CONTAINER_RUNTIME) push $(CONTAINER_IMAGE)

sast:
	@mkdir -p dist
	@$(UV) run bandit -r src/ -f json -o dist/sast-report.json || true
	@$(UV) run bandit -r src/ -f custom || true

sbom:
	@mkdir -p dist
	@$(UV) run cyclonedx-py environment .venv -o dist/sbom.json --of JSON

pip-audit:
	@$(UV) run pip-audit --desc || true

security: sast sbom pip-audit

qa: lint typecheck test healthcheck
	@echo "QA gate passed."

validate: lint ansible-syntax healthcheck
	@$(UV) run mypy src > /dev/null 2>&1; E=$$?; \
	ERRS=$$($(UV) run mypy src 2>&1 | grep -c 'error:' || echo 0); \
	if [ $$ERRS -le 18 ]; then echo "typecheck: OK ($$ERRS errors, baseline 18)"; else echo "typecheck: FAIL ($$ERRS errors > baseline 18)"; exit 1; fi
	@$(UV) run python -m pytest tests/ -q 2>&1 > /tmp/gludd-validate.txt; EXIT=$$?; \
	if [ $$EXIT -eq 0 ]; then echo "test: PASS"; else echo "test: FAIL (non-zero exit)"; exit 1; fi
	@$(MAKE) --no-print-directory smoke > /dev/null 2>&1 && echo "smoke: PASS" || (echo "smoke: FAIL" && exit 1)
	@echo "Full validation passed."

bootstrap: init lint test healthcheck
	@echo "Bootstrap complete."

db-sample-message:
	@sqlite3 ~/.local/share/opencode/opencode.db "SELECT substr(m.data, 1, 500) FROM message m LIMIT 3;" 2>/dev/null

db-sample-part:
	@sqlite3 ~/.local/share/opencode/opencode.db "SELECT substr(p.data, 1, 500) FROM part p LIMIT 3;" 2>/dev/null
	@sqlite3 ~/.local/share/opencode/opencode.db ".schema" 2>/dev/null

db-tables:
	@sqlite3 ~/.local/share/opencode/opencode.db ".tables" 2>/dev/null

db-count:
	@sqlite3 ~/.local/share/opencode/opencode.db "SELECT COUNT(*) FROM message;" 2>/dev/null

search-opencode:
	@sqlite3 ~/.local/share/opencode/opencode.db "SELECT json_extract(m.data, '$$.role'), json_extract(p.data, '$$.text') FROM message m JOIN part p ON m.id = p.message_id WHERE json_extract(m.data, '$$.role')='user' AND json_extract(p.data, '$$.text') LIKE '%$(SEARCH)%' LIMIT $(MAX_RESULTS);" 2>/dev/null

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
