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
        test-guardrails test-scripts test-db test-live-zai \
        typecheck setup-dirs setup-venv clean healthcheck \
        bootstrap skeleton version check-uv check-pytest \
        ansible-syntax ansible-lint-playbooks playbook-list \
        git-status git-init git-add git-commit git-log git-diff git-reset \
        git-branch git-checkout git-merge git-staged \
        feature-start feature-done test-and-commit \
        container-build container-run container-push \
        build-executable dist dist-clean \
        sast sbom pip-audit security \
        qa validate

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
	@$(UV) run ruff check --fix src tests

typecheck:
	@$(UV) run mypy src

test:
	@$(UV) run pytest tests/ --cov=general_ludd --cov-report=term-missing --cov-report=xml -v

test-unit:
	@if [ -n "$(TESTFILE)" ]; then \
		$(UV) run pytest $(TESTFILE) -v; \
	else \
		$(UV) run pytest tests/unit/ -v; \
	fi

test-specific:
	@if [ -z "$(TESTFILE)" ]; then echo "Usage: make test-specific TESTFILE='tests/unit/test_foo.py::TestClass::test_method'"; exit 1; fi
	@$(UV) run pytest $(TESTFILE) -v

test-count:
	@$(UV) run pytest tests/ --co -q 2>&1 | tail -3

test-integration:
	@$(UV) run pytest tests/integration/ -v

test-e2e:
	@$(UV) run pytest tests/e2e/ -v

test-guardrails:
	@$(UV) run pytest tests/unit/test_guardrails.py -v

test-db:
	@$(UV) run pytest tests/unit/test_db_models.py -v

test-scripts:
	@$(UV) run pytest tests/unit/test_guardrails.py::TestSkeletonScript -v

healthcheck:
	@$(UV) run python -c "from general_ludd.worker.app import create_app; app = create_app(); print('Worker app factory OK')"
	@$(UV) run python -c "from general_ludd.event_loop.loop import EventLoop; print('Event loop import OK')"

ansible-syntax:
	@for f in playbooks/*.yml; do echo "Checking $$f..."; ansible-playbook --syntax-check "$$f" || exit 1; done

ansible-lint-playbooks:
	@ansible-lint playbooks/roles || true

playbook-list:
	@ls -1 playbooks/*.yml 2>/dev/null || echo "No playbooks found"

git-status:
	@git status --short || echo "Not a git repo"

git-diff:
	@git diff --stat || echo "No diff"

git-staged:
	@git diff --cached --stat || echo "Nothing staged"

git-init:
	@git init
	@git config user.email "agent@general-ludd.local" || true
	@git config user.name "General Ludd Agent" || true

git-log:
	@git log --oneline -10 || echo "No git history"

git-add:
	@if [ -z "$(FILES)" ]; then echo "Usage: make git-add FILES='file1 file2 ...'"; exit 1; fi
	@git add $(FILES)

git-add-all:
	@git add -A

git-commit:
	@if [ -z "$(MSG)" ]; then echo "Usage: make git-commit MSG='message'"; exit 1; fi
	@git diff --cached --quiet && echo "Nothing to commit" || git commit -m "$(MSG)"

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
	@$(UV) run pytest tests/ -q
	@git checkout -f master
	@git merge --no-ff "$(MSG)"
	@echo "Merged $(MSG) into master"
	@echo "Building distributables..."
	@$(MAKE) dist
	@echo "Feature complete. Tests green, distributables built."

test-and-commit:
	@echo "Running tests before commit..."
	@$(UV) run pytest tests/ --cov=general_ludd -q
	@echo "Tests passed. Committing..."
	@git add -A
	@if [ -n "$(MSG)" ]; then \
		git diff --cached --quiet && echo "Nothing to commit" || git commit -m "$(MSG)"; \
	else \
		git diff --cached --quiet && echo "Nothing to commit" || git commit -m "agent: test-green $(shell date +%Y%m%d%H%M%S)"; \
	fi
	@echo "Committed."

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
	$(UV) run pytest tests/live/test_zai_live.py -v -s

CONTAINER_RUNTIME := $(shell command -v podman 2>/dev/null || command -v docker 2>/dev/null)
CONTAINER_IMAGE := gl-agent:latest

VERSION := $(shell $(UV) run python -c "from general_ludd import __version__; print(__version__)")
PLATFORM := $(shell uname -s)-$(shell uname -m)
TARBALL_NAME := general-ludd-agent-$(VERSION)-$(PLATFORM)
TARBALL_DIR := dist/$(TARBALL_NAME)

build-executable:
	@$(UV) run pyinstaller gludd.spec --clean --noconfirm
	@echo "Built dist/gludd"

dist: build-executable
	@echo "Assembling tarball..."
	@rm -rf $(TARBALL_DIR)
	@mkdir -p $(TARBALL_DIR)
	@cp dist/gludd $(TARBALL_DIR)/gludd
	@cp dist/install.sh $(TARBALL_DIR)/install.sh
	@cp dist/general-ludd.service $(TARBALL_DIR)/general-ludd.service
	@cp -r config $(TARBALL_DIR)/config
	@cp -r templates $(TARBALL_DIR)/templates
	@if [ -d docs ]; then cp -r docs $(TARBALL_DIR)/docs; fi
	@cd dist && tar czf $(TARBALL_NAME).tar.gz $(TARBALL_NAME)
	@echo "Created dist/$(TARBALL_NAME).tar.gz"

dist-clean:
	@rm -rf dist/general-ludd-agent-* dist/gludd build
	@echo "Dist artifacts cleaned."

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

validate: lint typecheck test ansible-syntax healthcheck
	@echo "Full validation passed."

bootstrap: init lint test healthcheck
	@echo "Bootstrap complete."
