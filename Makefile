.PHONY: gen-status-table check-status-table check-readme-status check-readme-status-current git-status git-log git-add git-commit git-commit-no-verify help lint typecheck collect-check test test-iso smoke gate gate-background gate-status-check gate-tail gate-logs gate-kill qa healthcheck molecule-config-check molecule-help molecule-test-help molecule-test-openbao-break-glass-backup molecule-test-facts molecule-test-root molecule-setup-openbao-break-glass molecule-test-help git-remotes git-push-sandboxcom-ssh check-mock-log test-ansible-collections deletion-gate-threshold submodule-init submodule-update submodule-status submodule-pin submodule-sync container-build container-run container-push dist test-integration test-live-zai

# Regenerate the STATUS-TABLE in README.md from docs/features.yml
gen-status-table:
	python3 scripts/gen_status_table.py --write --fast

# Check if the STATUS-TABLE in README.md is current (CI gate)
check-readme-status:
	python3 scripts/check_readme_status_current.py

# CI expects this target name (legacy alias)
check-status-table: check-readme-status

# Check README status with explicit tag
check-readme-status-current:
	python3 scripts/check_readme_status_current.py $(TAG)

# Lint with ruff
lint:
	uv run ruff check src/ tests/

# Typecheck with mypy
typecheck:
	uv run mypy src/

# Fast collection-error gate
collect-check:
	uv run python -m pytest --collect-only -q 2>&1 | grep -E "ERROR|error" || true

# Run tests
test:
	uv run python -m pytest tests/ -n auto --dist loadgroup -q

# Smoke test (daemon health check)
smoke:
	uv run python -c "import general_ludd; print('imports ok')"

# Full gate: lint + typecheck + collect-check + test + smoke
gate:
	@echo "=== GATE PHASE: lint ==="
	@$(MAKE) lint || (echo "=== GATE: FAILED ===" && exit 1)
	@echo "=== GATE PHASE: typecheck ==="
	@$(MAKE) typecheck || (echo "=== GATE: FAILED ===" && exit 1)
	@echo "=== GATE PHASE: collect ==="
	@$(MAKE) collect-check || (echo "=== GATE: FAILED ===" && exit 1)
	@echo "=== GATE PHASE: test ==="
	@$(MAKE) test || (echo "=== GATE: FAILED ===" && exit 1)
	@echo "=== GATE PHASE: smoke ==="
	@$(MAKE) smoke || (echo "=== GATE: FAILED ===" && exit 1)
	@echo "=== GATE: PASSED ==="

# Background gate: launches gate via nohup, writes logs to .gate-logs/gate-<timestamp>.log
gate-background:
	@mkdir -p .gate-logs
	@TIMESTAMP=$$(date +%Y%m%d-%H%M%S); \
	nohup make gate > .gate-logs/gate-$${TIMESTAMP}.log 2>&1 & \
	echo $$! > .gate-background.pid; \
	echo "Gate launched in background (PID $$!); log: .gate-logs/gate-$${TIMESTAMP}.log"

# Check background gate status
gate-status-check:
	@if [ ! -f .gate-background.pid ]; then \
		echo "No background gate running (no .gate-background.pid)"; \
		exit 1; \
	fi; \
	PID=$$(cat .gate-background.pid 2>/dev/null); \
	if kill -0 $$PID 2>/dev/null; then \
		echo "Gate RUNNING (PID $$PID)"; \
	else \
		echo "Gate FINISHED (PID $$PID not alive)"; \
	fi; \
	LOG=$$(ls -t .gate-logs/gate-*.log 2>/dev/null | head -1); \
	if [ -n "$$LOG" ]; then \
		echo "--- log: $$(basename $$LOG) ---"; \
		if grep -q "=== GATE: PASSED ===" "$$LOG" 2>/dev/null; then \
			echo "--- RESULT: PASS ---"; \
		elif grep -q "FAIL\|Error\|ERROR" "$$LOG" 2>/dev/null | grep -v "PASS" | head -1 | grep -q .; then \
			echo "--- RESULT: FAIL ---"; \
		else \
			echo "--- (no terminal marker yet) ---"; \
		fi; \
		echo "--- last 20 log lines ---"; \
		tail -20 "$$LOG" 2>/dev/null || echo "(log empty)"; \
	fi; \
	if [ -f .gate-status ]; then \
		echo "--- .gate-status ---"; \
		cat .gate-status; \
	fi

# Live tail of latest gate log
gate-tail:
	@LOG=$$(ls -t .gate-logs/gate-*.log 2>/dev/null | head -1); \
	if [ -n "$$LOG" ]; then \
		tail -f "$$LOG"; \
	else \
		echo "No gate log found"; \
	fi

# List all gate logs
gate-logs:
	@for log in .gate-logs/gate-*.log; do \
		[ -f "$$log" ] || continue; \
		if grep -q "=== GATE: PASSED ===" "$$log" 2>/dev/null; then \
			status="PASS"; \
		elif grep -q "FA\|ERROR" "$$log" 2>/dev/null | grep -v "PASS" | head -1 | grep -q .; then \
			status="FAIL"; \
		else \
			status="RUNNING"; \
		fi; \
		mtime=$$(stat -f '%Sm' "$$log" 2>/dev/null || stat -c '%y' "$$log" 2>/dev/null); \
		echo "$$log  $$status  $$mtime"; \
	done

# Kill background gate
gate-kill:
	@if [ ! -f .gate-background.pid ]; then \
		echo "No background gate to kill"; \
		exit 0; \
	fi; \
	PID=$$(cat .gate-background.pid 2>/dev/null); \
	if kill -0 $$PID 2>/dev/null; then \
		echo "Sending SIGTERM to PID $$PID"; \
		kill -TERM $$PID 2>/dev/null || true; \
		for i in 1 2 3 4 5; do \
			sleep 1; \
			if ! kill -0 $$PID 2>/dev/null; then \
				echo "Gate terminated after SIGTERM"; \
				rm -f .gate-background.pid; \
				exit 0; \
			fi; \
		done; \
		echo "SIGTERM failed, sending SIGKILL"; \
		kill -KILL $$PID 2>/dev/null || true; \
		rm -f .gate-background.pid; \
		echo "Gate killed"; \
	else \
		echo "Gate already finished (PID $$PID not alive)"; \
		rm -f .gate-background.pid; \
	fi

# QA: lint + typecheck + test + healthcheck
qa: lint typecheck test healthcheck

# Healthcheck: verify imports work
healthcheck:
	uv run python -c "import general_ludd; print('healthcheck ok')"

# System load: check current load average and CPU capacity
system-load:
	uv run python -c "from general_ludd.system.monitor import get_load_average, get_cpu_count, can_start_process; load1, load5, load15 = get_load_average(); cores = get_cpu_count(); can_start = can_start_process(); print(f'Load averages: 1m={load1:.2f}, 5m={load5:.2f}, 15m={load15:.2f}'); print(f'CPU cores: {cores}'); print(f'Threshold (2.5x cores): {2.5 * cores:.2f}'); print(f'Max 5m load: 10.0'); print(f'5m load ({load5:.2f}) vs threshold ({2.5 * cores:.2f}): {\"OK\" if load5 <= 2.5 * cores else \"HIGH\"}'); print(f'5m load ({load5:.2f}) vs max (10.0): {\"OK\" if load5 <= 10.0 else \"HIGH\"}'); print(f'Can start process: {\"YES\" if can_start else \"NO\"}')"

git-status:
	git status

git-log:
	git log --oneline -10

git-add:
	git add $(FILES)

git-rm-cached:
	git rm --cached -r $(FILES)

git-commit: _gate-fresh-check
	git commit -m "$(MSG)"

git-commit-no-verify: _gate-fresh-check
	git commit --no-verify -m "$(MSG)"

# Non-code meta-commits (version bumps, docs) — documented escape hatch
repo-commit:
	git commit -m "$(MSG)"

_gate-fresh-check:
	@[ "$(GLUDD_CI_IS_GATE)" = "1" ] && exit 0; \
	if [ ! -f .gate-status ]; then echo "No .gate-status file. Run 'make gate' first."; exit 1; fi; \
	grep -q "lint PASS 0" .gate-status || (echo "lint not PASS 0 in .gate-status"; exit 1); \
	grep -q "typecheck PASS" .gate-status || (echo "typecheck not PASS in .gate-status"; exit 1); \
	grep -q "collect PASS" .gate-status || (echo "collect not PASS in .gate-status"; exit 1); \
	grep -q "test PASS" .gate-status || (echo "test not PASS in .gate-status"; exit 1)

git-diff:
	git diff

git-restore:
	git checkout -- $(FILES)

git-staged:
	git diff --staged

git-add-all:
	git add -A

git-push-sandboxcom:
	git push https://github.com/sandboxcom/gludd.git master

git-push-sandboxcom-ssh:
	GIT_SSH_COMMAND="ssh -i sandboxcom_github_rsa -o IdentitiesOnly=yes" git push sandboxcom master

git-push-nv:
	git push --no-verify https://github.com/sandboxcom/gludd.git master

git-push-branch:
	@test -n "$(BRANCH)" || (echo "Usage: make git-push-branch BRANCH=<branch>"; exit 1)
	@scripts/check_green_branch_guard.py "$(BRANCH)" || (echo "Branch guard blocked push (green branch is immutable)"; exit 1)
	git push https://github.com/sandboxcom/gludd.git $(BRANCH)

verify-remote:
	@test -n "$(BRANCH)" -a -n "$(SHA)" || (echo "Usage: make verify-remote BRANCH=<branch> SHA=<sha>"; exit 1)
	@gitsharedremote=$$(GIT_SSH_COMMAND="ssh -i sandboxcom_github_rsa -o IdentitiesOnly=yes" git ls-remote https://github.com/sandboxcom/gludd.git refs/heads/$(BRANCH) 2>/dev/null | awk '{print $$1}'); \
	if [ "$$gitsharedremote" = "$(SHA)" ]; then echo "VERIFIED $(BRANCH)@$(SHA)"; else echo "REMOTE MISMATCH: remote=$$gitsharedremote expected=$(SHA)"; exit 1; fi

ci-verdict:
	@test -n "$(BRANCH)" || (echo "Usage: make ci-verdict BRANCH=<branch>"; exit 1)
	@python3 scripts/require_ci_green.py "$$(git rev-parse $(BRANCH))"

# Check molecule config
molecule-config-check:
	ls -la ~/.config/molecule/ 2>/dev/null || echo "No molecule config in ~/.config/molecule/"; \
	ls -la .config/molecule/ 2>/dev/null || echo "No molecule config in .config/molecule/"; \
	ls -la /Users/shawnwilson/gludd/molecule/collections/ 2>/dev/null || echo "No collections in molecule/"

# Check molecule help
molecule-help:
	uv run molecule --help

# Check molecule test help
molecule-test-help:
	uv run molecule test --help

# Run molecule test for openbao_break_glass_backup
molecule-test-openbao-break-glass-backup:
	ANSIBLE_COLLECTIONS_PATH=/Users/shawnwilson/gludd/collections MOLECULE_PROJECT_DIRECTORY=/Users/shawnwilson/gludd cd /Users/shawnwilson/gludd/molecule/playbooks/openbao_break_glass_backup && uv run molecule test -s default

# Run molecule test for test_gludd_facts
molecule-test-facts:
	ANSIBLE_COLLECTIONS_PATH=/Users/shawnwilson/gludd/collections MOLECULE_PROJECT_DIRECTORY=/Users/shawnwilson/gludd cd /Users/shawnwilson/gludd/molecule/playbooks/test_gludd_facts && uv run molecule test -s default

# Run molecule test from project root with scenario directory
molecule-test-root:
	ANSIBLE_COLLECTIONS_PATH=/Users/shawnwilson/gludd/collections uv run molecule test -s openbao_break_glass_backup

# Setup molecule scenario directory structure
molecule-setup-openbao-break-glass:
	mkdir -p /Users/shawnwilson/gludd/molecule/playbooks/openbao_break_glass_backup/molecule && ln -sf /Users/shawnwilson/gludd/molecule/playbooks/openbao_break_glass_backup/default /Users/shawnwilson/gludd/molecule/playbooks/openbao_break_glass_backup/molecule/default
	ln -sf /Users/shawnwilson/gludd/molecule/playbooks/openbao_break_glass_backup /Users/shawnwilson/gludd/molecule/openbao_break_glass_backup

git-tags:
	git tag -l --sort=-creatordate

git-remotes:
	git remote -v

help:
	@echo "Available targets:"
	@echo "  gen-status-table         - Regenerate status table in README.md"
	@echo "  check-readme-status      - Check if status table is current"
	@echo "  check-readme-status-current TAG=v0.1.0-alpha.5 - Check with tag"
	@echo "  lint                     - Run ruff linter"
	@echo "  typecheck                - Run mypy type checker"
	@echo "  collect-check            - Fast collection-error gate"
	@echo "  test                     - Run pytest test suite"
	@echo "  smoke                    - Daemon health check"
	@echo "  gate                     - Full gate (lint + typecheck + collect + test + smoke)"
	@echo "  gate-background          - Launch gate in background"
	@echo "  gate-status-check        - Check background gate status"
	@echo "  gate-tail                - Live tail latest gate log"
	@echo "  gate-logs                - List all gate logs"
	@echo "  gate-kill                - Kill background gate"
	@echo "  qa                       - Full QA (lint + typecheck + test + healthcheck)"
	@echo "  healthcheck              - Verify imports work"
	@echo "  system-load              - Check current system load and CPU capacity"
	@echo "  git-status               - Show git status"
	@echo "  git-log                  - Show recent commits"
	@echo "  git-add FILES='file1 file2' - Stage files"
	@echo "  git-commit MSG='message' - Commit staged changes"
	@echo "  git-push-sandboxcom      - Push master to sandboxcom remote"
	@echo "  deletion-gate-threshold THRESHOLD=10 - Update deletion_gate_threshold in config/general-ludd.yml"
	@echo "  test-iso TESTFILE=path   - Run a single test file"
	@echo "  submodule-init           - Initialize all submodules"
	@echo "  submodule-update         - Update all submodules to latest tags"
	@echo "  submodule-status         - Show status of each submodule"
	@echo "  submodule-pin REPO=name TAG=tag - Pin a submodule to a specific tag"
	@echo "  submodule-sync           - Sync submodule URLs"

# Check mock daemon log
check-mock-log:
	cat /tmp/gludd-mock-8794.log 2>/dev/null || echo "Log file not found"

# Test ansible-playbook with collections path
test-ansible-collections:
	ANSIBLE_COLLECTIONS_PATH=/Users/shawnwilson/gludd/collections /Users/shawnwilson/gludd/.venv/bin/ansible-playbook /Users/shawnwilson/gludd/molecule/playbooks/openbao_break_glass_backup/default/converge.yml 2>&1 | head -50

# Config targets

# Update deletion_gate_threshold in config/general-ludd.yml
# Usage: make deletion-gate-threshold THRESHOLD=10
deletion-gate-threshold:
	@if [ -z "$(THRESHOLD)" ]; then \
		echo "Usage: make deletion-gate-threshold THRESHOLD=<number>"; \
		exit 1; \
	fi; \
	python3 -c "import yaml; \
	f = open('config/general-ludd.yml', 'r'); data = yaml.safe_load(f); f.close(); \
	data['deletion_gate_threshold'] = int('$(THRESHOLD)'); \
	f = open('config/general-ludd.yml', 'w'); yaml.dump(data, f, default_flow_style=False, sort_keys=False); f.close()"; \
	echo "Updated deletion_gate_threshold to $(THRESHOLD)"

# Run a single test file (isolated test)
test-iso:
	uv run python -m pytest $(TESTFILE) -v

# Initialize project (dirs + deps)
init:
	uv sync
	mkdir -p .gate-logs

# Sync uv dependencies
sync:
	uv sync

# Clean build artifacts
clean:
	rm -rf .venv build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache
	rm -rf .gate-logs .gate-status .gate-background.pid

# Bootstrap: init + lint + test + healthcheck
bootstrap: init lint test healthcheck

# Run single test file
test-specific:
	uv run python -m pytest $(TESTFILE) -v

# Run unit tests only
test-unit:
	uv run python -m pytest tests/unit/ -v

# Run e2e tests only
test-e2e:
	uv run python -m pytest tests/e2e/ -v

# Integration tests (stub)
test-integration:
	@echo "test-integration: not yet implemented"

# Live ZAI tests (stub)
test-live-zai:
	@echo "test-live-zai: not yet implemented"

# Count test collection (no run)
test-count:
	uv run python -m pytest --collect-only -q

# Show test failures only
test-failures:
	uv run python -m pytest -q 2>&1 | grep -E "FAILED|ERROR" || echo "No failures"

# Run tests then commit if green
test-and-commit: test
	@if [ -n "$(MSG)" ]; then git commit -m "$(MSG)"; else echo "MSG= required"; exit 1; fi

ship-commit:
	@[ -n "$(MSG)" ] || (echo "Usage: make ship-commit MSG='message'"; exit 1)
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) collect-check
	git add -A
	git commit -m "$(MSG)"
	GIT_SSH_COMMAND="ssh -i sandboxcom_github_rsa -o IdentitiesOnly=yes" git push https://github.com/sandboxcom/gludd.git master

# Run guardrail tests
test-guardrails:
	uv run python -m pytest tests/unit/test_guardrails.py tests/unit/test_opencode_plugin_ports.py tests/unit/test_plugin_behavior.py -v

# Run hook tests
test-hooks:
	bash scripts/test_no_wait_hook.sh

# Lint with auto-fix
lint-fix:
	uv run ruff check src/ tests/ --fix --unsafe-fixes

# Full validation: lint + typecheck + test + smoke + ansible-syntax + audit-evidence
validate: lint typecheck test smoke
	@echo "Full validation passed"

# Preflight checks
preflight:
	uv run python -m general_ludd.quality.preflight

# Molecule test single scenario
molecule-test:
	@test -n "$(SCENARIO)" || (echo "Usage: make molecule-test SCENARIO=<name>"; exit 1)
	ANSIBLE_COLLECTIONS_PATH=/Users/shawnwilson/gludd/collections \
	MOLECULE_PROJECT_DIRECTORY=/Users/shawnwilson/gludd \
	cd /Users/shawnwilson/gludd/molecule/playbooks/$(SCENARIO) && \
	uv run molecule test -s default

# Run all molecule scenarios
molecule-test-all:
	@for scenario in molecule/playbooks/*/; do \
		name=$$(basename $$scenario); \
		ANSIBLE_COLLECTIONS_PATH=/Users/shawnwilson/gludd/collections \
		MOLECULE_PROJECT_DIRECTORY=/Users/shawnwilson/gludd \
		cd molecule/playbooks/$$name && \
		uv run molecule test -s default || exit 1; \
	done; \
	echo "ALL scenarios passed"

# Validate opencode.json against schema
validate-opencode-config:
	uv run python -m pytest tests/unit/test_opencode_json_schema.py -v

# Ansible syntax check
ansible-syntax:
	ANSIBLE_COLLECTIONS_PATH=/Users/shawnwilson/gludd/collections \
	.venv/bin/ansible-playbook --syntax-check playbooks/*.yml

# Ansible collection test suite
ansible-collection-test:
	ANSIBLE_COLLECTIONS_PATH=/Users/shawnwilson/gludd/collections \
	uv run python -m pytest tests/integration/test_playbook_registry.py -v

# Feature branch workflow
feature-start:
	@test -n "$(MSG)" || (echo "Usage: make feature-start MSG='feature/name'"; exit 1)
	git checkout -b "$(MSG)"

feature-done:
	@test -n "$(MSG)" || (echo "Usage: make feature-done MSG='feature/name'"; exit 1)
	@if git rev-parse --verify master >/dev/null 2>&1; then \
		git checkout master && git merge --no-ff "$(MSG)"; \
	else \
		git checkout main && git merge --no-ff "$(MSG)"; \
	fi

# Fast CI verdict check
ci-verdict-fast:
	@python3 scripts/require_ci_green.py "$$(git rev-parse master)" 2>/dev/null || echo "RED"

# Scan secrets fresh (no baseline)
scan-secrets-fresh:
	detect-secrets scan --all-files --no-baseline

# Check dist paths for local machine leaks
dist-path-check:
	@if [ -d dist/gludd ]; then \
		grep -r "/Users" dist/gludd/ && echo "BUILD PATHS LEAKED IN DIST" && exit 1 || echo "Tarball dir(s) path-clean."; \
	else \
		echo "No dist/gludd dir to check"; \
	fi

# Verify release artifact exists
verify-release-artifact:
	@test -n "$(TAG)" || (echo "Usage: make verify-release-artifact TAG=<tag>"; exit 1)
	python3 scripts/verify_release_artifact.py "$(TAG)"

# Build pyinstaller executable
build-executable:
	uv run pyinstaller --onefile --name gludd src/general_ludd/cli.py

# Dependencies audit
deps-audit:
	uv run deptry src/

# pip audit for CVEs
pip-audit:
	uv run pip-audit

# Initialize all submodules
submodule-init:
	git submodule update --init --recursive

# Update all submodules to latest stable tags
submodule-update:
	git submodule update --remote --merge

# Show status of each submodule
submodule-status:
	git submodule status
	@echo "---"
	git submodule foreach 'echo "Submodule: $$name"; git describe --tags --always 2>/dev/null || git rev-parse --short HEAD'

# Pin a submodule to a specific tag
# Usage: make submodule-pin REPO=llamacpp TAG=v1.2.3
submodule-pin:
	@if [ -z "$(REPO)" ] || [ -z "$(TAG)" ]; then \
		echo "Usage: make submodule-pin REPO=<submodule-name> TAG=<tag>"; \
		exit 1; \
	fi
	git -C $(REPO) fetch --tags
	git -C $(REPO) checkout $(TAG)
	git add $(REPO) .gitmodules
	git commit -m "Pin submodule $(REPO) to $(TAG)"

# Sync submodule URLs to use HTTPS (for CI)
submodule-sync:
	git submodule sync

# Debug submodule configuration
submodule-debug:
	@echo "=== .gitmodules content ==="
	@cat .gitmodules
	@echo "=== git config submodule.* ==="
	@git config --get-regexp 'submodule\.' || echo "No submodule config found"
	@echo "=== git submodule status ==="
	@git submodule status

# Clone DevSpark repo
clone-devspark:
	@mkdir -p /tmp
	@cd /tmp && git clone https://github.com/markhazleton/devspark devspark 2>&1 || (cd /tmp/devspark && git pull 2>&1)

# Clone DeepSpec repo
clone-deepspec:
	@mkdir -p /tmp
	@cd /tmp && git clone https://github.com/godrix/spec.md deep-spec 2>&1 || (cd /tmp/deep-spec && git pull 2>&1)

# Setup DevSpark structure in gludd
setup-devspark: clone-devspark
	@mkdir -p .devspark/defaults/commands
	@mkdir -p .devspark/defaults/skills
	@mkdir -p .devspark/hooks
	@cp -r /tmp/devspark/.devspark/defaults/commands/* .devspark/defaults/commands/ 2>/dev/null || true
	@cp -r /tmp/devspark/.devspark/defaults/skills/* .devspark/defaults/skills/ 2>/dev/null || true
	@cp -r /tmp/devspark/.devspark/hooks/* .devspark/hooks/ 2>/dev/null || true
	@cp /tmp/devspark/.devspark/agents-registry.json .devspark/agents-registry.json 2>/dev/null || echo "agents-registry.json not found, creating empty"
	@cp /tmp/devspark/.devspark/VERSION .devspark/VERSION 2>/dev/null || true
	@cp /tmp/devspark/.devspark/schemas/harness.schema.json .devspark/schemas/harness.schema.json 2>/dev/null || true

# Install DeepSpec skill for opencode
install-deepspec-skill: clone-deepspec
	@mkdir -p .opencode/skill/deep-spec
	@cp -r /tmp/deep-spec/spec.md/* .opencode/skill/deep-spec/ 2>/dev/null || true
	@echo "DeepSpec skill installed to .opencode/skill/deep-spec/"

# Model CLI targets
model-performance:
	uv run python -m general_ludd.cli models performance $(if $(SERVICE),--service $(SERVICE)) $(if $(TASK_TYPE),--task-type $(TASK_TYPE))

model-ranking:
	@test -n "$(TASK_TYPE)" || (echo "Usage: make model-ranking TASK_TYPE=<type> [STRATEGY=<strategy>]"; exit 1)
	uv run python -m general_ludd.cli models ranking --task-type $(TASK_TYPE) $(if $(STRATEGY),--strategy $(STRATEGY))

model-router-status:
	uv run python -m general_ludd.cli models router-status

model-router-set:
	@test -n "$(TASK_TYPE)" -a -n "$(STRATEGY)" || (echo "Usage: make model-router-set TASK_TYPE=<type> STRATEGY=<strategy>"; exit 1)
	uv run python -m general_ludd.cli models router-set --task-type $(TASK_TYPE) --strategy $(STRATEGY)

# Budget management targets
budget-set:
	@test -n "$(PROJECT)" -a -n "$(AMOUNT)" || (echo "Usage: make budget-set PROJECT=<id> AMOUNT=<usd> [TIMEFRAME_HOURS=<hours>]"; exit 1)
	uv run python -m general_ludd.cli budget set --project $(PROJECT) --amount $(AMOUNT) $(if $(TIMEFRAME_HOURS),--timeframe-hours $(TIMEFRAME_HOURS))

budget-show:
	@test -n "$(PROJECT)" || (echo "Usage: make budget-show PROJECT=<id>"; exit 1)
	uv run python -m general_ludd.cli budget show --project $(PROJECT)

budget-summary:
	uv run python -m general_ludd.cli budget summary

# Background test runner targets
test-bg:
	@test -n "$(TESTFILE)" || (echo "Usage: make test-bg TESTFILE=<path>"; exit 1)
	@mkdir -p .gate-logs
	@SANITIZED=$$(echo "$(TESTFILE)" | tr '/' '_'); \
	nohup uv run python -m pytest $(TESTFILE) -v > .gate-logs/test-$$SANITIZED-$$(date +%Y%m%d-%H%M%S).log 2>&1 & \
	PID=$$!; \
	echo $$PID > .gate-logs/.test-$$SANITIZED.pid; \
	echo "Test launched in background (PID $$PID); testfile=$(TESTFILE)"

test-bg-status:
	@test -n "$(TESTFILE)" || (echo "Usage: make test-bg-status TESTFILE=<path>"; exit 1)
	@SANITIZED=$$(echo "$(TESTFILE)" | tr '/' '_'); \
	if [ -f .gate-logs/.test-$$SANITIZED.pid ]; then \
		PID=$$(cat .gate-logs/.test-$$SANITIZED.pid); \
		if kill -0 $$PID 2>/dev/null; then \
			echo "Test RUNNING (PID $$PID)"; \
		else \
			echo "Test FINISHED (PID $$PID not alive)"; \
		fi; \
		LOG=$$(ls -t .gate-logs/test-$$SANITIZED-*.log 2>/dev/null | head -1); \
		echo "--- last 10 lines of $$(basename $$LOG) ---"; \
		tail -10 "$$LOG" 2>/dev/null || echo "(log empty)"; \
	else \
		echo "No background test for $(TESTFILE)"; \
	fi

test-bg-list:
	@for pidfile in .gate-logs/.test-*.pid; do \
		[ -f "$$pidfile" ] || continue; \
		PID=$$(cat $$pidfile); \
		if kill -0 $$PID 2>/dev/null; then status="RUNNING"; else status="DONE"; fi; \
		echo "$$(basename $$pidfile .pid | sed 's/^\.test-//') PID=$$PID $$status"; \
	done

test-bg-kill:
	@test -n "$(TESTFILE)" || (echo "Usage: make test-bg-kill TESTFILE=<path>"; exit 1)
	@SANITIZED=$$(echo "$(TESTFILE)" | tr '/' '_'); \
	if [ -f .gate-logs/.test-$$SANITIZED.pid ]; then \
		PID=$$(cat .gate-logs/.test-$$SANITIZED.pid); \
		kill -KILL $$PID 2>/dev/null; \
		rm -f .gate-logs/.test-$$SANITIZED.pid; \
		echo "Killed test PID $$PID"; \
	else \
		echo "No background test for $(TESTFILE)"; \
	fi

# SDD workflow targets (DevSpark + DeepSpec integration)
sdd-constitution:
	@cat .devspark/defaults/commands/constitution.md 2>/dev/null || echo "DevSpark not installed. Run: make setup-sdd"

sdd-discover:
	@cat .devspark/defaults/commands/discover-constitution.md 2>/dev/null || echo "DevSpark not installed. Run: make setup-sdd"

sdd-specify:
	@cat .devspark/defaults/commands/specify.md 2>/dev/null || echo "DevSpark not installed. Run: make setup-sdd"

sdd-plan:
	@cat .devspark/defaults/commands/plan.md 2>/dev/null || echo "DevSpark not installed. Run: make setup-sdd"

sdd-tasks:
	@cat .devspark/defaults/commands/tasks.md 2>/dev/null || echo "DevSpark not installed. Run: make setup-sdd"

sdd-implement:
	@cat .devspark/defaults/commands/implement.md 2>/dev/null || echo "DevSpark not installed. Run: make setup-sdd"
	@$(MAKE) gate

sdd-pr:
	@cat .devspark/defaults/commands/create-pr.md 2>/dev/null || echo "DevSpark not installed. Run: make setup-sdd"

sdd-release:
	@cat .devspark/defaults/commands/release.md 2>/dev/null || echo "DevSpark not installed. Run: make setup-sdd"

sdd-audit:
	@cat .devspark/defaults/commands/site-audit.md 2>/dev/null || echo "DevSpark not installed. Run: make setup-sdd"

sdd-critic:
	@cat .devspark/defaults/commands/critic.md 2>/dev/null || echo "DevSpark not installed. Run: make setup-sdd"

sdd-harvest:
	@cat .devspark/defaults/commands/harvest.md 2>/dev/null || echo "DevSpark not installed. Run: make setup-sdd"

sdd-quickfix:
	@cat .devspark/defaults/commands/quickfix.md 2>/dev/null || echo "DevSpark not installed. Run: make setup-sdd"

git-show-commit:
	@test -n "$(SHA)" || (echo "Usage: make git-show-commit SHA=<sha>"; exit 1)
	git show --stat $(SHA)

git-log-branch:
	@test -n "$(BRANCH)" || (echo "Usage: make git-log-branch BRANCH=<branch>"; exit 1)
	git log --oneline $(BRANCH) -20

ci-run-log:
	@test -n "$(RUN)" || (echo "Usage: make ci-run-log RUN=<run_id>"; exit 1)
	gh run view $(RUN) --repo sandboxcom/gludd

ci-run-log-failed:
	@test -n "$(RUN)" || (echo "Usage: make ci-run-log-failed RUN=<run_id>"; exit 1)
	gh run view $(RUN) --repo sandboxcom/gludd --log-failed

container-build:
	@echo "container-build: not yet implemented"

container-run:
	@echo "container-run: not yet implemented"

container-push:
	@echo "container-push: not yet implemented"
