.PHONY: gen-status-table check-readme-status check-readme-status-current git-status git-log git-add git-commit help lint typecheck collect-check test test-iso smoke gate gate-background gate-status-check gate-tail gate-logs gate-kill qa healthcheck molecule-config-check molecule-help molecule-test-help molecule-test-openbao-break-glass-backup molecule-test-facts molecule-test-root molecule-setup-openbao-break-glass molecule-test-help git-remotes check-mock-log test-ansible-collections deletion-gate-threshold submodule-init submodule-update submodule-status submodule-pin submodule-sync

# Regenerate the STATUS-TABLE in README.md from docs/features.yml
gen-status-table:
	python3 scripts/gen_status_table.py --write --fast

# Check if the STATUS-TABLE in README.md is current (CI gate)
check-readme-status:
	python3 scripts/check_readme_status_current.py

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
gate: lint typecheck collect-check test smoke
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

git-commit:
	git commit -m "$(MSG)"

git-push-sandboxcom:
	git push https://github.com/sandboxcom/gludd.git master

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

# Check molecule test help
molecule-test-help:
	uv run molecule test --help

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

# Submodule management targets

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
