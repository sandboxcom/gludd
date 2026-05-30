SEARCH ?= hello world
MAX_RESULTS ?= 10
FORMAT ?= text
SEARCH_SCRIPT := scripts/search.py
MSG ?= 

PYTHON := python3
UV := uv
PROJECT_SRC := src/agentic_harness
TESTS_DIR := tests

.PHONY: search-google search-json \
        init sync install-pip lint lint-fix test test-unit test-integration \
        test-guardrails test-scripts test-db \
        typecheck setup-dirs setup-venv clean healthcheck \
        bootstrap skeleton version check-uv check-pytest \
        ansible-syntax ansible-lint-playbooks playbook-list \
        git-status git-init git-commit git-log test-and-commit \
        qa validate

search-google:
	@$(PYTHON) $(SEARCH_SCRIPT) "$(SEARCH)" -n $(MAX_RESULTS) -f $(FORMAT)

search-json:
	@$(PYTHON) $(SEARCH_SCRIPT) "$(SEARCH)" -n $(MAX_RESULTS) -f json

skeleton:
	@$(PYTHON) scripts/skeleton.py

setup-dirs:
	@mkdir -p src/agentic_harness/worker
	@mkdir -p src/agentic_harness/event_loop
	@mkdir -p src/agentic_harness/models
	@mkdir -p src/agentic_harness/db
	@mkdir -p src/agentic_harness/rules
	@mkdir -p src/agentic_harness/schemas
	@mkdir -p src/agentic_harness/secrets
	@mkdir -p src/agentic_harness/git_automation
	@mkdir -p src/agentic_harness/controllers
	@mkdir -p src/agentic_harness/ansible
	@mkdir -p src/agentic_harness/prompts
	@mkdir -p src/agentic_harness/quality
	@mkdir -p src/agentic_harness/runtime
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

sync:
	@$(UV) sync --locked

install-pip:
	@$(PYTHON) -m venv .venv
	@. .venv/bin/activate && pip install --upgrade pip
	@. .venv/bin/activate && pip install -e ".[dev]"

version:
	@$(UV) run python -c "from agentic_harness import __version__; print(f'agentic-harness {__version__}')"

check-uv:
	@command -v $(UV) >/dev/null 2>&1 || (echo "uv not found"; exit 1)
	@$(UV) --version

check-pytest:
	@$(UV) run python -c "import pytest; print(f'pytest {pytest.__version__}')"

lint:
	@$(UV) run ruff check src tests

lint-fix:
	@$(UV) run ruff check --fix src tests

typecheck:
	@$(UV) run mypy src

test:
	@$(UV) run pytest tests/ --cov=agentic_harness --cov-report=term-missing --cov-report=xml -v

test-unit:
	@$(UV) run pytest tests/unit/ -v

test-integration:
	@$(UV) run pytest tests/integration/ -v

test-guardrails:
	@$(UV) run pytest tests/unit/test_guardrails.py -v

test-db:
	@$(UV) run pytest tests/unit/test_db_models.py -v

test-scripts:
	@$(UV) run pytest tests/unit/test_guardrails.py::TestSkeletonScript -v

healthcheck:
	@$(UV) run python -c "from agentic_harness.worker.app import create_app; app = create_app(); print('Worker app factory OK')"
	@$(UV) run python -c "from agentic_harness.event_loop.loop import EventLoop; print('Event loop import OK')"

ansible-syntax:
	@for f in playbooks/*.yml; do echo "Checking $$f..."; ansible-playbook --syntax-check "$$f" || exit 1; done

ansible-lint-playbooks:
	@ansible-lint playbooks/roles || true

playbook-list:
	@ls -1 playbooks/*.yml 2>/dev/null || echo "No playbooks found"

git-status:
	@git status --short || echo "Not a git repo"

git-init:
	@git init
	@git config user.email "agent@harness.local" || true
	@git config user.name "Agentic Harness Agent" || true

git-commit:
	@git add -A
	@git diff --cached --quiet && echo "Nothing to commit" || git commit -m "agent: $(shell date +%Y%m%d%H%M%S) checkpoint"

git-log:
	@git log --oneline -10 || echo "No git history"

clean:
	@rm -rf .venv dist build *.egg-info src/*.egg-info .pytest_cache .mypy_cache .coverage coverage.xml htmlcov .ruff_cache
	@find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "Cleaned."

qa: lint typecheck test healthcheck
	@echo "QA gate passed."

validate: lint typecheck test ansible-syntax healthcheck
	@echo "Full validation passed."

bootstrap: init lint test healthcheck
	@echo "Bootstrap complete."

test-and-commit:
	@echo "Running tests before commit..."
	@$(UV) run pytest tests/ --cov=agentic_harness -q
	@echo "Tests passed. Committing..."
	@git add -A
	@if [ -n "$(MSG)" ]; then \
		git diff --cached --quiet && echo "Nothing to commit" || git commit -m "$(MSG)"; \
	else \
		git diff --cached --quiet && echo "Nothing to commit" || git commit -m "agent: test-green $(shell date +%Y%m%d%H%M%S)"; \
	fi
	@echo "Committed."
