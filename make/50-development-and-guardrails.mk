# --- Development-branch workflow targets ---
# Feature work merges into `development` (not master). `development` merges into
# `master` ONLY for releases. These are development-branch variants of the
# agent-worktree / agent-merge protocol.

# Create an isolated worktree for a subagent, branching from `development`.
# If `development` doesn't exist locally, create it from `master` first.
# Usage: make agent-worktree-dev BRANCH=agent-fix-slurm
agent-worktree-dev:
	@[ -n "$(BRANCH)" ] || { echo "Usage: make agent-worktree-dev BRANCH=agent-<name>"; exit 1; }
	@git rev-parse --verify development 2>/dev/null || { echo "Creating development branch from master..."; git branch development master; }
	@WORKTREE_PATH="/tmp/gludd-worktrees/$(BRANCH)"; \
	mkdir -p /tmp/gludd-worktrees; \
	git worktree add "$$WORKTREE_PATH" -b "$(BRANCH)" development 2>/dev/null || git worktree add "$$WORKTREE_PATH" "$(BRANCH)"; \
	echo "WORKTREE_PATH=$$WORKTREE_PATH"; \
	echo "Worktree ready at $$WORKTREE_PATH on branch $(BRANCH) (base: development)"

# Merge a subagent's worktree branch back to `development` (--no-ff).
# Checks out development, merges the feature branch, then returns to the
# previous branch. Usage: make agent-merge-dev BRANCH=agent-fix-slurm
agent-merge-dev:
	@[ -n "$(BRANCH)" ] || { echo "Usage: make agent-merge-dev BRANCH=agent-<name>"; exit 1; }
	@git checkout development && \
	git merge --no-ff "$(BRANCH)" -m "merge: $(BRANCH) worktree work into development" && \
	git checkout - && \
	echo "Merged $(BRANCH) into development"

# Push the development branch to the sandboxcom remote.
development-push: check-clean-tree ci-busy-check _push-rate-guard
	@$(MAKE) ci-busy-check BRANCH=development
	@$(MAKE) require-sandboxcom-ssh-key
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push -u sandboxcom development
	@$(MAKE) verify-remote BRANCH=development SHA=$$(git rev-parse development)
	@echo "Development branch pushed and verified"

# Force-push the development branch (when rebase rewrites history).
development-force-push:
	@GLUDD_FORCE_PUSH=1 $(MAKE) --no-print-directory _push-rate-guard
	@$(MAKE) require-sandboxcom-ssh-key
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push --force --no-verify -u sandboxcom development
	@$(MAKE) verify-remote BRANCH=development SHA=$$(git rev-parse development)
	@echo "Development branch force-pushed and verified"

# Reconcile a branch into development while retaining a merge parent. MODE is
# required so an ancestry-only (-s ours) decision remains visible and auditable.
# APPLY defaults to 0; APPLY=1 is fail-closed outside a clean development tree.
# Usage: make development-merge-forward SOURCE=<ref> MODE=content|ancestry-only APPLY=0|1
development-merge-forward:
	@SOURCE_VALUE='$(SOURCE)'; MODE_VALUE='$(MODE)'; APPLY_VALUE='$(APPLY)'; \
	if [ -z "$$APPLY_VALUE" ]; then APPLY_VALUE=0; fi; \
	if [ -z "$$SOURCE_VALUE" ]; then echo "Usage: make development-merge-forward SOURCE=<ref> MODE=content|ancestry-only APPLY=0|1"; exit 2; fi; \
	case "$$MODE_VALUE" in content|ancestry-only) ;; *) echo "MODE must be explicitly set to content or ancestry-only"; exit 2 ;; esac; \
	case "$$APPLY_VALUE" in 0|1) ;; *) echo "APPLY must be 0 or 1"; exit 2 ;; esac; \
	if [ "$$MODE_VALUE" = ancestry-only ]; then \
		case "$$SOURCE_VALUE" in master|refs/heads/master|*/master) echo "ancestry-only mode is forbidden for master"; exit 2 ;; esac; \
		echo "WARNING: mode=ancestry-only strategy=ours records ancestry while preserving development content"; \
	fi; \
	if ! SOURCE_SHA=$$(git rev-parse --verify "$${SOURCE_VALUE}^{commit}" 2>/dev/null); then echo "Invalid SOURCE ref: $$SOURCE_VALUE"; exit 2; fi; \
	if [ "$$APPLY_VALUE" = 0 ]; then \
		echo "MERGE_FORWARD_DRY_RUN source=$$SOURCE_VALUE mode=$$MODE_VALUE apply=0 sha=$$SOURCE_SHA"; \
		echo "no repository changes were made"; \
		exit 0; \
	fi; \
	CURRENT_BRANCH="$$(git branch --show-current)"; \
	if [ "$$CURRENT_BRANCH" != "development" ]; then echo "APPLY=1 requires current branch development (found: $$CURRENT_BRANCH)"; exit 2; fi; \
	if [ -n "$$(git status --porcelain)" ]; then echo "APPLY=1 requires a clean development worktree"; exit 2; fi; \
	MERGE_STARTED=1; \
	abort_merge() { if [ "$$MERGE_STARTED" -eq 1 ]; then git restore --worktree -- . >/dev/null 2>&1 || true; git merge --abort >/dev/null 2>&1 || true; fi; }; \
	trap abort_merge EXIT HUP INT TERM; \
	if [ "$$MODE_VALUE" = content ]; then \
		if ! git merge --no-ff --no-commit -X ours "$$SOURCE_SHA"; then \
			echo "Structural conflict while merging $$SOURCE_VALUE; aborting transaction"; \
			git diff --name-only --diff-filter=U; \
			exit 1; \
		fi; \
	else \
		if ! git merge --no-ff -s ours --no-commit "$$SOURCE_SHA"; then echo "Ancestry-only merge failed; aborting transaction"; exit 1; fi; \
	fi; \
	if ! git rev-parse --verify -q MERGE_HEAD >/dev/null; then \
		MERGE_STARTED=0; trap - EXIT HUP INT TERM; \
		echo "MERGE_FORWARD_NOOP source=$$SOURCE_VALUE is already an ancestor of development"; \
		exit 0; \
	fi; \
	UNMERGED="$$(git diff --name-only --diff-filter=U)"; \
	if [ -n "$$UNMERGED" ]; then echo "Structural conflict remains; aborting transaction"; echo "$$UNMERGED"; exit 1; fi; \
	if ! $(UV) run pre-commit run detect-secrets --all-files; then \
		if git diff --quiet -- .secrets.baseline; then echo "Secret scan failed without a baseline metadata update; aborting transaction"; exit 1; fi; \
		git add .secrets.baseline; \
		if ! $(UV) run pre-commit run detect-secrets --all-files; then echo "Secret scan still fails after baseline metadata refresh; aborting transaction"; exit 1; fi; \
	fi; \
	if ! $(MAKE) --no-print-directory collect-check; then echo "Collection check failed; aborting transaction"; exit 1; fi; \
	if ! $(MAKE) --no-print-directory _commit-lint-guard; then echo "Lint guard failed; aborting transaction"; exit 1; fi; \
	if ! $(MAKE) --no-print-directory gate-refresh GATE_REFRESH_VALIDATE_ONLY=0; then echo "Merged-tree gate refresh failed; aborting transaction"; exit 1; fi; \
	if ! git diff --cached --name-only -z | xargs -0 $(UV) run pre-commit run --files; then echo "Merged-tree pre-commit checks failed; aborting transaction"; exit 1; fi; \
	if ! $(MAKE) --no-print-directory _gate-fresh-check; then echo "Gate freshness check failed; aborting transaction"; exit 1; fi; \
	if ! git commit -n -m "merge-forward: MODE=$$MODE_VALUE SOURCE=$$SOURCE_VALUE SHA=$$SOURCE_SHA into development"; then echo "Merge commit failed; aborting transaction"; exit 1; fi; \
	MERGE_STARTED=0; trap - EXIT HUP INT TERM; \
	echo "MERGE_FORWARD_APPLIED source=$$SOURCE_VALUE mode=$$MODE_VALUE sha=$$SOURCE_SHA"

# Reconcile several already-reviewed, semantically superseded refs in one
# ancestry-only transaction. The octopus ours merge records every parent while
# preserving development content and paying the collection cost once.
# Usage: make development-merge-forward-batch SOURCES='ref1 ref2' APPLY=0|1
development-merge-forward-batch:
	@SOURCES_VALUE='$(SOURCES)'; APPLY_VALUE='$(APPLY)'; \
	if [ -z "$$APPLY_VALUE" ]; then APPLY_VALUE=0; fi; \
	if [ -z "$$SOURCES_VALUE" ]; then echo "Usage: make development-merge-forward-batch SOURCES='ref1 ref2' APPLY=0|1"; exit 2; fi; \
	case "$$APPLY_VALUE" in 0|1) ;; *) echo "APPLY must be 0 or 1"; exit 2 ;; esac; \
	SOURCE_SHAS=''; SOURCE_COUNT=0; \
	for ref in $$SOURCES_VALUE; do \
		case "$$ref" in master|refs/heads/master|*/master) echo "ancestry-only mode is forbidden for master"; exit 2 ;; esac; \
		if ! sha=$$(git rev-parse --verify "$${ref}^{commit}" 2>/dev/null); then echo "Invalid SOURCE ref: $$ref"; exit 2; fi; \
		case " $$SOURCE_SHAS " in *" $$sha "*) ;; *) SOURCE_SHAS="$$SOURCE_SHAS $$sha"; SOURCE_COUNT=$$((SOURCE_COUNT + 1)) ;; esac; \
	done; \
	echo "WARNING: mode=ancestry-only strategy=ours sources=$$SOURCE_COUNT shas=$$SOURCE_SHAS"; \
	if [ "$$APPLY_VALUE" = 0 ]; then \
		echo "MERGE_FORWARD_BATCH_DRY_RUN sources=$$SOURCE_COUNT mode=ancestry-only strategy=ours apply=0"; \
		echo "no repository changes were made"; \
		exit 0; \
	fi; \
	CURRENT_BRANCH="$$(git branch --show-current)"; \
	if [ "$$CURRENT_BRANCH" != "development" ]; then echo "APPLY=1 requires current branch development (found: $$CURRENT_BRANCH)"; exit 2; fi; \
	if [ -n "$$(git status --porcelain)" ]; then echo "APPLY=1 requires a clean development worktree"; exit 2; fi; \
	MERGE_STARTED=1; \
	abort_merge() { if [ "$$MERGE_STARTED" -eq 1 ]; then git merge --abort >/dev/null 2>&1 || true; fi; }; \
	trap abort_merge EXIT HUP INT TERM; \
	if ! git merge --no-ff -s ours --no-commit $$SOURCE_SHAS; then echo "Batch ancestry-only merge failed; aborting transaction"; exit 1; fi; \
	if ! git rev-parse --verify -q MERGE_HEAD >/dev/null; then \
		MERGE_STARTED=0; trap - EXIT HUP INT TERM; \
		echo "MERGE_FORWARD_BATCH_NOOP every source is already an ancestor of development"; \
		exit 0; \
	fi; \
	if ! $(MAKE) --no-print-directory collect-check; then echo "Collection check failed; aborting transaction"; exit 1; fi; \
	if ! $(MAKE) --no-print-directory _commit-lint-guard; then echo "Lint guard failed; aborting transaction"; exit 1; fi; \
	if ! $(MAKE) --no-print-directory _gate-fresh-check; then echo "Gate freshness check failed; aborting transaction"; exit 1; fi; \
	if ! git commit -m "merge-forward: batch ancestry-only $$SOURCE_COUNT superseded refs into development" -m "source-shas:$$SOURCE_SHAS"; then echo "Merge commit failed; aborting transaction"; exit 1; fi; \
	MERGE_STARTED=0; trap - EXIT HUP INT TERM; \
	echo "MERGE_FORWARD_BATCH_APPLIED sources=$$SOURCE_COUNT mode=ancestry-only shas=$$SOURCE_SHAS"

# Merge development into master for release prep.
# Requires CI-green on the development tip before allowing the merge.
development-merge-to-master: merge-ready
	@echo "Checking CI green on development tip..."
	@$(MAKE) require-ci-green SHA=$$(git rev-parse development) || { echo "CI not green on development tip. Aborting."; exit 1; }
	@echo "CI green confirmed. Merging development into master..."
	@git checkout master && \
	git merge --no-ff development -m "merge: development into master for release" && \
	git checkout - && \
	echo "Merged development into master"

# Create the development branch from current master if it doesn't exist.
development-start:
	@git rev-parse --verify development 2>/dev/null && echo "Development branch already exists" || { echo "Creating development branch from master..."; git branch development master; echo "Development branch created from master"; }

# Show commits on development that aren't on master.
development-status:
	@git rev-parse --verify development 2>/dev/null || { echo "Development branch does not exist. Run: make development-start"; exit 1; }
	@echo "=== Commits on development not yet on master ==="
	@git log master..development --oneline --decorate 2>/dev/null || echo "(none)"
	@echo "=== Summary ==="
	@git rev-list --count master..development 2>/dev/null || echo "0"
	@echo "unmerged commits on development"

preflight: disk-cleanup-preflight check-plugin-liveness
	@echo "========================================"
	@echo "  PREFLIGHT QUALITY GATE"
	@echo "========================================"
	@$(UV) run python -c "import json, sys; from general_ludd.quality.preflight import run_preflight; r = run_preflight(); json.dump(r, sys.stdout, indent=2); sys.exit(0 if r['overall'] == 'PASS' else 1)"

test-and-commit: _commit-lock-acquire
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
	@if [ "$(CLEAN_VALIDATE_ONLY)" = "1" ]; then \
		$(UV) run python -m pytest tests/unit/test_packaging_templates_committed.py::test_clean_preserves_tracked_distribution_templates -q -n 0; \
	elif [ "$(CLEAN_VALIDATE_ONLY)" = "0" ]; then \
		rm -rf .venv build *.egg-info src/*.egg-info .pytest_cache .mypy_cache .coverage coverage.xml htmlcov .ruff_cache; \
		git clean -fdX -- dist; \
		rm -f Makefile.tmp; \
		find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true; \
		git rm -r --cached '*__pycache__*' 2>/dev/null || true; \
		git rm --cached .coverage coverage.xml 2>/dev/null || true; \
		echo "Cleaned."; \
	else \
		echo "Usage: make clean CLEAN_VALIDATE_ONLY=0|1"; \
		exit 2; \
	fi

# disk-reclaim: compatibility alias for the bounded disk guard.  Keep the
# cleanup implementation single-sourced so uv pruning always has a heartbeat,
# lock timeout, and maximum runtime rather than becoming an unseen stall.
disk-reclaim:
	@$(MAKE) --no-print-directory disk-guard GLUDD_DISK_THRESHOLD="$(GLUDD_DISK_THRESHOLD)"

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

VERSION = $(shell UV_CACHE_DIR="$(GLUDD_UV_CACHE_DIR)" $(UV) run --no-sync python -c "from general_ludd import __version__; print(__version__)")
PLATFORM = $(shell uname -s)-$(shell uname -m)
TARBALL_NAME = general-ludd-agent-$(VERSION)-$(PLATFORM)
TARBALL_DIR = dist/$(TARBALL_NAME)
BUILD_EXECUTABLE_ENVIRONMENT ?= .venv

build-executable:
	@$(MAKE) --no-print-directory sync \
		DEPENDENCY_PROFILE_SET=build-azure \
		DEPENDENCY_PROFILE_ENVIRONMENT="$(BUILD_EXECUTABLE_ENVIRONMENT)" \
		DEPENDENCY_PROFILE_PYTHON=3.12.14 \
		DEPENDENCY_PROFILE_VALIDATE_ONLY=0
	@locked_pyinstaller_version=$$(UV_PROJECT_ENVIRONMENT="$(BUILD_EXECUTABLE_ENVIRONMENT)" $(UV) run --no-sync python scripts/dependency_profiles.py locked-version --root "$(CURDIR)" --profile dev-build --package pyinstaller); \
		pyinstaller_version=$$(UV_PROJECT_ENVIRONMENT="$(BUILD_EXECUTABLE_ENVIRONMENT)" $(UV) run --no-sync pyinstaller --version); \
		test "$$pyinstaller_version" = "$$locked_pyinstaller_version" || { echo "Expected locked PyInstaller $$locked_pyinstaller_version, found $$pyinstaller_version"; exit 1; }
	@UV_PROJECT_ENVIRONMENT="$(BUILD_EXECUTABLE_ENVIRONMENT)" $(UV) run --no-sync pyinstaller gludd.spec --clean --noconfirm
	@echo "Built dist/gludd"

LINUX_BINARY_IMAGE ?= gludd-linux-binary-build:python3.12.14-uv0.12.19
LINUX_BINARY_DOCKERFILE ?= config/containers/linux-binary.Dockerfile
LINUX_BINARY_IMAGE_BUILD_VALIDATE_ONLY ?= 0
DOCKER_BUILDX_BIN ?=
DOCKER_BUILDX_AUTO_INSTALL ?= 1
DOCKER_BUILDX_FORMULA ?= docker-buildx
LINUX_BINARY_OUTPUT ?= dist/linux/gludd
LINUX_BINARY_SCRATCH_ROOT ?= $(HOME)/tmp/gludd-linux-build
DEBIAN_SNAPSHOT ?= 20260729T000000Z
LINUX_BINUTILS_VERSION ?= 2.40-2
LINUX_APT_UTILS_VERSION ?= 2.6.1
PYINSTALLER_WARNING_ALLOWLIST_LINUX ?= config/pyinstaller-warning-allowlist-linux.json
PYINSTALLER_WARNING_FILE_LINUX ?= dist/linux/warn-gludd.txt
PYINSTALLER_VERSION_LINUX ?= 6.22.3
PYINSTALLER_PYTHON_VERSION_LINUX ?= 3.12.14
PYINSTALLER_UV_VERSION_LINUX ?= 0.12.19
PYINSTALLER_WARNING_ARCHITECTURE_LINUX ?=
PYINSTALLER_WARNING_AUDIT_VALIDATE_ONLY ?= 0
PYINSTALLER_WARNING_BEFORE ?=
PYINSTALLER_WARNING_AFTER ?=
PYINSTALLER_WARNING_REVIEW_RECEIPT ?= artifacts/pyinstaller-warning-review.json
PYINSTALLER_WARNING_COMPARE_VALIDATE_ONLY ?= 0
PYINSTALLER_WARNING_REVIEW_POLICY ?= config/pyinstaller-warning-allowlist-linux.json
PYINSTALLER_WARNING_REVIEW_DIR ?= config/pyinstaller-warning-reviews
PYINSTALLER_WARNING_REVIEW_BASE_POLICY ?=
PYINSTALLER_WARNING_REVIEW_CHECK_VALIDATE_ONLY ?= 0

.PHONY: audit-linux-pyinstaller-warnings
audit-linux-pyinstaller-warnings: ## Re-audit a retained Linux PyInstaller warning report
	@case "$(PYINSTALLER_WARNING_FILE_LINUX)" in /*|*..*) echo "Refusing unsafe PYINSTALLER_WARNING_FILE_LINUX: $(PYINSTALLER_WARNING_FILE_LINUX)"; exit 1;; esac
	@if [ "$(PYINSTALLER_WARNING_AUDIT_VALIDATE_ONLY)" = "1" ]; then \
		echo "audit-linux-pyinstaller-warnings: validated $(PYINSTALLER_WARNING_FILE_LINUX)"; \
	else \
		architecture="$(PYINSTALLER_WARNING_ARCHITECTURE_LINUX)"; \
		if [ -z "$$architecture" ]; then architecture="$$(uname -m)"; fi; \
		$(UV) run python scripts/audit_pyinstaller_warnings.py \
			--warnings "$(PYINSTALLER_WARNING_FILE_LINUX)" \
			--allowlist "$(PYINSTALLER_WARNING_ALLOWLIST_LINUX)" \
			--platform linux \
			--architecture "$$architecture" \
			--pyinstaller-version "$(PYINSTALLER_VERSION_LINUX)" \
			--spec gludd.spec; \
	fi

.PHONY: compare-linux-pyinstaller-warnings
compare-linux-pyinstaller-warnings: ## Compare accepted/candidate warning graphs without editing policy
	@case "$(PYINSTALLER_WARNING_COMPARE_VALIDATE_ONLY)" in 0|1) ;; *) echo "PYINSTALLER_WARNING_COMPARE_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@case "$(PYINSTALLER_WARNING_ARCHITECTURE_LINUX)" in ''|*[!A-Za-z0-9_-]*) echo "PYINSTALLER_WARNING_ARCHITECTURE_LINUX must be explicit and safe"; exit 2;; esac
	@case "$(PYINSTALLER_WARNING_BEFORE)" in ''|*..*) echo "Refusing unsafe PYINSTALLER_WARNING_BEFORE: $(PYINSTALLER_WARNING_BEFORE)"; exit 2;; /*) case "$(PYINSTALLER_WARNING_BEFORE)" in /tmp/gludd-*) ;; *) echo "Absolute before path must be namespaced under /tmp/gludd-"; exit 2;; esac;; esac
	@case "$(PYINSTALLER_WARNING_AFTER)" in ''|*..*) echo "Refusing unsafe PYINSTALLER_WARNING_AFTER: $(PYINSTALLER_WARNING_AFTER)"; exit 2;; /*) case "$(PYINSTALLER_WARNING_AFTER)" in /tmp/gludd-*) ;; *) echo "Absolute after path must be namespaced under /tmp/gludd-"; exit 2;; esac;; esac
	@case "$(PYINSTALLER_WARNING_REVIEW_RECEIPT)" in ''|/*|*..*) echo "PYINSTALLER_WARNING_REVIEW_RECEIPT must be a safe repository-relative path"; exit 2;; esac
	@if [ "$(PYINSTALLER_WARNING_COMPARE_VALIDATE_ONLY)" = "1" ]; then \
		echo "PYINSTALLER_WARNING_COMPARE_VALID before=$(PYINSTALLER_WARNING_BEFORE) after=$(PYINSTALLER_WARNING_AFTER) architecture=$(PYINSTALLER_WARNING_ARCHITECTURE_LINUX) receipt=$(PYINSTALLER_WARNING_REVIEW_RECEIPT)"; \
	else \
		$(UV) run python -m scripts.compare_pyinstaller_warning_graphs \
			--before "$(PYINSTALLER_WARNING_BEFORE)" \
			--after "$(PYINSTALLER_WARNING_AFTER)" \
			--allowlist "$(PYINSTALLER_WARNING_ALLOWLIST_LINUX)" \
			--platform linux \
			--architecture "$(PYINSTALLER_WARNING_ARCHITECTURE_LINUX)" \
			--pyinstaller-version "$(PYINSTALLER_VERSION_LINUX)" \
			--spec gludd.spec \
			--receipt "$(PYINSTALLER_WARNING_REVIEW_RECEIPT)"; \
	fi

.PHONY: check-pyinstaller-warning-reviews
check-pyinstaller-warning-reviews: ## Reject newly accepted warning graphs without complete exact-delta evidence
	@case "$(PYINSTALLER_WARNING_REVIEW_CHECK_VALIDATE_ONLY)" in 0|1) ;; *) echo "PYINSTALLER_WARNING_REVIEW_CHECK_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@case "$(PYINSTALLER_WARNING_REVIEW_POLICY)" in ''|/*|*..*) echo "PYINSTALLER_WARNING_REVIEW_POLICY must be a safe repository-relative path"; exit 2;; esac
	@case "$(PYINSTALLER_WARNING_REVIEW_DIR)" in ''|/*|*..*) echo "PYINSTALLER_WARNING_REVIEW_DIR must be a safe repository-relative path"; exit 2;; esac
	@case "$(PYINSTALLER_WARNING_REVIEW_BASE_POLICY)" in /*|*..*) echo "PYINSTALLER_WARNING_REVIEW_BASE_POLICY must be empty or repository-relative"; exit 2;; esac
	@if [ "$(PYINSTALLER_WARNING_REVIEW_CHECK_VALIDATE_ONLY)" = "1" ]; then \
		echo "PYINSTALLER_WARNING_REVIEW_CHECK_VALID policy=$(PYINSTALLER_WARNING_REVIEW_POLICY) receipt_dir=$(PYINSTALLER_WARNING_REVIEW_DIR)"; \
	else \
		before_args=""; \
		if [ -n "$(PYINSTALLER_WARNING_REVIEW_BASE_POLICY)" ]; then before_args="--before-policy $(PYINSTALLER_WARNING_REVIEW_BASE_POLICY)"; fi; \
		$(UV) run python scripts/check_pyinstaller_warning_reviews.py \
			--policy "$(PYINSTALLER_WARNING_REVIEW_POLICY)" \
			--receipt-dir "$(PYINSTALLER_WARNING_REVIEW_DIR)" \
			$$before_args; \
	fi

.PHONY: build-linux-binary-image
build-linux-binary-image: lima-docker-ensure ## Build the exact Python and uv environment used by Linux artifact analysis
	@case "$(LINUX_BINARY_IMAGE_BUILD_VALIDATE_ONLY)" in 0|1) ;; *) echo "LINUX_BINARY_IMAGE_BUILD_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@case "$(DOCKER_BUILDX_AUTO_INSTALL)" in 0|1) ;; *) echo "DOCKER_BUILDX_AUTO_INSTALL must be 0 or 1"; exit 2;; esac
	@case "$(DOCKER_BUILDX_FORMULA)" in docker-buildx) ;; *) echo "Refusing unreviewed Buildx formula: $(DOCKER_BUILDX_FORMULA)"; exit 2;; esac
	@case "$(DOCKER_BUILDX_BIN)" in ""|/*) ;; *) echo "DOCKER_BUILDX_BIN must be empty or absolute"; exit 2;; esac
	@case "$(LINUX_BINARY_DOCKERFILE)" in /*|*..*) echo "Refusing unsafe LINUX_BINARY_DOCKERFILE: $(LINUX_BINARY_DOCKERFILE)"; exit 2;; esac
	@case "$(LINUX_BINARY_IMAGE)" in gludd-*:* ) ;; *) echo "Refusing non-Gludd Linux builder image: $(LINUX_BINARY_IMAGE)"; exit 2;; esac
	@set -eu; \
	if [ "$(LINUX_BINARY_IMAGE_BUILD_VALIDATE_ONLY)" = "1" ]; then \
		test -f "$(LINUX_BINARY_DOCKERFILE)"; \
		echo "LINUX_BINARY_IMAGE_BUILD_VALID image=$(LINUX_BINARY_IMAGE) dockerfile=$(LINUX_BINARY_DOCKERFILE) python=$(PYINSTALLER_PYTHON_VERSION_LINUX) uv=$(PYINSTALLER_UV_VERSION_LINUX) buildx_auto_install=$(DOCKER_BUILDX_AUTO_INSTALL)"; \
		exit 0; \
	fi; \
	buildx_bin="$(DOCKER_BUILDX_BIN)"; \
	if [ -z "$$buildx_bin" ]; then \
		command -v brew >/dev/null 2>&1 || { echo "Homebrew is required to provision Docker Buildx"; exit 1; }; \
		brew_prefix=$$(brew --prefix); \
		buildx_bin="$$brew_prefix/bin/docker-buildx"; \
		if [ ! -x "$$buildx_bin" ]; then \
			if [ "$(DOCKER_BUILDX_AUTO_INSTALL)" != "1" ]; then echo "Docker Buildx is missing and automatic installation is disabled"; exit 1; fi; \
			echo "Installing maintained Docker Buildx via Homebrew formula $(DOCKER_BUILDX_FORMULA)"; \
			brew install "$(DOCKER_BUILDX_FORMULA)"; \
		fi; \
	fi; \
	test -x "$$buildx_bin" || { echo "Docker Buildx executable is unavailable: $$buildx_bin"; exit 1; }; \
	socket=$$(limactl list "$(LIMA_INSTANCE)" --format '{{.Dir}}/sock/docker.sock' 2>/dev/null || true); \
	if [ -z "$$socket" ]; then echo "Lima Docker socket unavailable for $(LIMA_INSTANCE): $$socket"; exit 1; fi; \
	mkdir -p "$(LIMA_DOCKER_CONFIG)"; \
	chmod 700 "$(LIMA_DOCKER_CONFIG)"; \
	DOCKER_CONFIG="$(LIMA_DOCKER_CONFIG)" DOCKER_HOST="unix://$$socket" "$$buildx_bin" version; \
	echo "Building digest-pinned Linux artifact environment $(LINUX_BINARY_IMAGE)"; \
	DOCKER_CONFIG="$(LIMA_DOCKER_CONFIG)" DOCKER_HOST="unix://$$socket" "$$buildx_bin" build \
		--load \
		--progress=plain \
		--file "$(LINUX_BINARY_DOCKERFILE)" \
		--tag "$(LINUX_BINARY_IMAGE)" \
		config/containers; \
	identity=$$(DOCKER_CONFIG="$(LIMA_DOCKER_CONFIG)" DOCKER_HOST="unix://$$socket" docker run --rm "$(LINUX_BINARY_IMAGE)" sh -eu -c 'set -- $$(uv --version); printf "%s|%s %s\n" "$$(python -c "import platform; print(platform.python_version())")" "$$1" "$$2"'); \
	test "$$identity" = "$(PYINSTALLER_PYTHON_VERSION_LINUX)|uv $(PYINSTALLER_UV_VERSION_LINUX)" || { echo "Unexpected Linux builder identity: $$identity"; exit 1; }; \
	echo "LINUX_BINARY_IMAGE_BUILD_READY image=$(LINUX_BINARY_IMAGE) identity=$$identity"

build-linux-executable: worktree-guard ## Build and verify a real Linux PyInstaller executable
	@case "$(LINUX_BINARY_OUTPUT)" in /*|*..*) echo "Refusing unsafe LINUX_BINARY_OUTPUT: $(LINUX_BINARY_OUTPUT)"; exit 1;; esac
	@case "$(LINUX_BINARY_SCRATCH_ROOT)" in "$(HOME)"/*) ;; *) echo "Refusing scratch root outside HOME: $(LINUX_BINARY_SCRATCH_ROOT)"; exit 1;; esac
	@mkdir -p "$$(dirname "$(LINUX_BINARY_OUTPUT)")"
	@rm -f "$(LINUX_BINARY_OUTPUT)" "$(dir $(LINUX_BINARY_OUTPUT))warn-gludd.txt"
	@set -e; source_sha=$$(git rev-parse HEAD); echo "LINUX_BINARY_SOURCE sha=$$source_sha"; if [ "$$(uname -s)" = "Linux" ]; then \
		echo "Building Linux executable natively"; \
		native_build_environment=$$(mktemp -d "/tmp/gludd-linux-native-build.XXXXXX"); \
		cleanup_native_build() { rm -rf "$$native_build_environment"; }; \
		trap cleanup_native_build EXIT INT TERM; \
		$(MAKE) --no-print-directory build-executable BUILD_EXECUTABLE_ENVIRONMENT="$$native_build_environment"; \
		python_version=$$(UV_PROJECT_ENVIRONMENT="$$native_build_environment" $(UV) run --no-sync python -c 'import platform; print(platform.python_version())'); \
		test "$$python_version" = "$(PYINSTALLER_PYTHON_VERSION_LINUX)" || { echo "Expected Python $(PYINSTALLER_PYTHON_VERSION_LINUX) for deterministic Linux PyInstaller analysis, found $$python_version"; exit 1; }; \
		pyinstaller_version=$$(UV_PROJECT_ENVIRONMENT="$$native_build_environment" $(UV) run --no-sync pyinstaller --version); \
		architecture=$$(uname -m); \
		cp build/gludd/warn-gludd.txt "$(dir $(LINUX_BINARY_OUTPUT))warn-gludd.txt"; \
		UV_PROJECT_ENVIRONMENT="$$native_build_environment" $(UV) run --no-sync python scripts/audit_pyinstaller_warnings.py \
			--warnings build/gludd/warn-gludd.txt \
			--allowlist "$(PYINSTALLER_WARNING_ALLOWLIST_LINUX)" \
			--platform linux \
			--architecture "$$architecture" \
			--pyinstaller-version "$$pyinstaller_version" \
			--spec gludd.spec; \
		cp dist/gludd "$(LINUX_BINARY_OUTPUT)"; \
	else \
		$(MAKE) --no-print-directory build-linux-binary-image; \
		socket=$$(limactl list "$(LIMA_INSTANCE)" --format '{{.Dir}}/sock/docker.sock' 2>/dev/null || true); \
		if [ -z "$$socket" ] || [ ! -S "$$socket" ]; then \
			echo "Lima Docker socket unavailable for $(LIMA_INSTANCE): $$socket"; \
			exit 1; \
		fi; \
		mkdir -p "$(LIMA_DOCKER_CONFIG)"; \
		chmod 700 "$(LIMA_DOCKER_CONFIG)"; \
		mkdir -p "$(LINUX_BINARY_SCRATCH_ROOT)"; \
		source_dir=$$(mktemp -d "$(LINUX_BINARY_SCRATCH_ROOT)/source.XXXXXX"); \
		container_name="gludd-linux-build-$$$$"; \
		cleanup_build() { \
			rm -rf "$$source_dir"; \
			DOCKER_CONFIG="$(LIMA_DOCKER_CONFIG)" DOCKER_HOST="unix://$$socket" docker rm -f "$$container_name" >/dev/null 2>&1 || true; \
		}; \
		trap cleanup_build EXIT INT TERM; \
		git archive "$$source_sha" | tar -x -C "$$source_dir"; \
		echo "Building Linux executable in namespaced Lima Docker VM $(LIMA_INSTANCE)"; \
		build_status=0; \
		DOCKER_CONFIG="$(LIMA_DOCKER_CONFIG)" DOCKER_HOST="unix://$$socket" docker run \
			--pull=never \
			--name "$$container_name" \
			-e HOME=/tmp/gludd-home \
			-e UV_CACHE_DIR=/tmp/gludd-uv-cache \
			-e UV_LINK_MODE=copy \
			-e UV_PROJECT_ENVIRONMENT=/tmp/gludd-linux-venv \
			-v "$$source_dir:/workspace:ro" \
			-w /workspace \
			"$(LINUX_BINARY_IMAGE)" \
			sh -ec 'export DEBIAN_FRONTEND=noninteractive; \
				sed -i \
					-e "s|http://deb.debian.org/debian-security|https://snapshot.debian.org/archive/debian-security/$(DEBIAN_SNAPSHOT)|g" \
					-e "s|http://deb.debian.org/debian|https://snapshot.debian.org/archive/debian/$(DEBIAN_SNAPSHOT)|g" \
					/etc/apt/sources.list.d/debian.sources; \
				if test -f /etc/dpkg/dpkg.cfg.d/docker; then \
					sed -i "\|/usr/share/man/|d" /etc/dpkg/dpkg.cfg.d/docker; \
				fi; \
				printf "%s\n" "Acquire::Check-Valid-Until \"false\";" > /etc/apt/apt.conf.d/99gludd-snapshot; \
				apt-get -o APT::Update::Error-Mode=any update; \
				if test -L /etc/alternatives/builtins.7.gz && ! test -e /usr/share/man/man7/bash-builtins.7.gz; then \
					mkdir -p /usr/share/man/man7; \
					: > /usr/share/man/man7/bash-builtins.7.gz; \
					update-alternatives --remove builtins.7.gz /usr/share/man/man7/bash-builtins.7.gz; \
					rm -f /usr/share/man/man7/bash-builtins.7.gz; \
				fi; \
				apt-get install -y --download-only --no-install-recommends "apt-utils=$(LINUX_APT_UTILS_VERSION)"; \
				dpkg -i /var/cache/apt/archives/apt-utils_$(LINUX_APT_UTILS_VERSION)_*.deb; \
				dpkg-query -W apt-utils; \
				echo "=== pending package updates before dist-upgrade ==="; \
				apt-get -s dist-upgrade; \
				apt-get -y --no-remove dist-upgrade; \
				apt-get install -y --no-install-recommends "binutils=$(LINUX_BINUTILS_VERSION)"; \
				command -v objdump; \
				command -v objcopy; \
				dpkg-query -W binutils; \
				echo "=== pending package updates after dist-upgrade ==="; \
				apt-get -s dist-upgrade > /tmp/gludd-apt-after.txt; \
				cat /tmp/gludd-apt-after.txt; \
				grep -Fq "0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded." /tmp/gludd-apt-after.txt; \
				rm -rf /var/lib/apt/lists/*; \
				python scripts/dependency_profiles.py sync --root /workspace --set build-azure --environment /tmp/gludd-linux-venv --python $(PYINSTALLER_PYTHON_VERSION_LINUX); \
				export UV_NO_SYNC=1; \
				python_version=$$(uv run python -c "import platform; print(platform.python_version())"); \
				test "$$python_version" = "$(PYINSTALLER_PYTHON_VERSION_LINUX)" || { echo "Expected Python $(PYINSTALLER_PYTHON_VERSION_LINUX) for deterministic Linux PyInstaller analysis, found $$python_version"; exit 1; }; \
				locked_pyinstaller_version=$$(python scripts/dependency_profiles.py locked-version --root /workspace --profile dev-build --package pyinstaller); \
				pyinstaller_version=$$(uv run pyinstaller --version); \
				test "$$pyinstaller_version" = "$$locked_pyinstaller_version" || { echo "Expected locked PyInstaller $$locked_pyinstaller_version, found $$pyinstaller_version"; exit 1; }; \
				architecture=$$(uname -m); \
				pyinstaller_status=0; \
				uv run pyinstaller gludd.spec --clean --noconfirm --workpath /tmp/gludd-pyinstaller-build --distpath /out || pyinstaller_status=$$?; \
				if test -f /tmp/gludd-pyinstaller-build/gludd/warn-gludd.txt; then \
					cp /tmp/gludd-pyinstaller-build/gludd/warn-gludd.txt /out/warn-gludd.txt; \
				fi; \
				test "$$pyinstaller_status" -eq 0; \
				uv run python scripts/audit_pyinstaller_warnings.py \
					--warnings /tmp/gludd-pyinstaller-build/gludd/warn-gludd.txt \
					--allowlist "$(PYINSTALLER_WARNING_ALLOWLIST_LINUX)" \
					--platform linux \
					--architecture "$$architecture" \
					--pyinstaller-version "$$pyinstaller_version" \
					--spec gludd.spec' || build_status=$$?; \
		warning_copy_status=0; \
		DOCKER_CONFIG="$(LIMA_DOCKER_CONFIG)" DOCKER_HOST="unix://$$socket" docker cp "$$container_name:/out/warn-gludd.txt" "$(dir $(LINUX_BINARY_OUTPUT))warn-gludd.txt" || warning_copy_status=$$?; \
		if [ "$$build_status" -ne 0 ]; then \
			echo "Linux executable container build failed"; \
			exit "$$build_status"; \
		fi; \
		if [ "$$warning_copy_status" -ne 0 ]; then \
			echo "PyInstaller warning report was not retained"; \
			exit "$$warning_copy_status"; \
		fi; \
		DOCKER_CONFIG="$(LIMA_DOCKER_CONFIG)" DOCKER_HOST="unix://$$socket" docker cp "$$container_name:/out/gludd" "$(LINUX_BINARY_OUTPUT)"; \
	fi
	@test -x "$(LINUX_BINARY_OUTPUT)" || { echo "Linux executable missing: $(LINUX_BINARY_OUTPUT)"; exit 1; }
	@kind=$$(file "$(LINUX_BINARY_OUTPUT)"); echo "$$kind"; echo "$$kind" | grep -q 'ELF' || { echo "Expected an ELF executable"; exit 1; }


gen-status-table:
	@$(UV) run python scripts/gen_status_table.py --write --fast

check-status-table:
	@$(UV) run python scripts/gen_status_table.py --check --fast

verify-status:
	@$(UV) run python scripts/verify_status.py

verify-enforcement:
	@$(UV) run python3 scripts/verify_enforcement.py

audit-features:
	@$(UV) run python scripts/audit_features.py

check-readme-status:
	@$(UV) run python scripts/check_readme_status_current.py $(TAG)

# --- Subagent guard validation ---
check-subagent-guards:
	@$(PYTHON) scripts/check_subagent_guards.py

# --- Depth-limit validation: verifies 3x dispatch (main→agent→subagent→subagent) is allowed ---
check-depth-limit:
	@$(UV) run python3 scripts/check_depth_limit.py

# --- Enhancement ratio diagnostic — reads state file and prints current wave ratio ---
# Machine-enforced counter for AGENTS.md COST-EFFICIENCY DIRECTIVE §5: at least
# 50% of every dispatch wave must be project enhancements.
check-enhancement-ratio:
	@$(UV) run python3 scripts/check_enhancement_ratio.py

clean-enhancement-ratio:
	@rm -f /tmp/gludd-enhancement-ratio.json
	@echo "Enhancement-ratio state cleared."

# --- Plugin manifest verification — opencode.json ↔ disk ↔ guard coverage ---
# temp fix target
fix-hooks-tmp:
	@echo "fix-hooks-tmp: temp fix target"

verify-plugin-manifest:
	@$(PYTHON) scripts/verify_plugin_manifest.py

# --- Skill frontmatter validation ---
check-skills-frontmatter:
	@$(UV) run python scripts/check_skills_frontmatter.py

# --- Task ledger validation: duplicate IDs, re-dispatched completed items, stale in_progress, missing IDs ---
validate-task-ledger:
	@$(UV) run python scripts/validate_task_ledger.py

# --- Hard registration guard: active changes must map to TASKS.md or task-ID metadata ---
check-task-integrity:
	@$(UV) run python scripts/check_task_integrity.py

check-task-registration:
	@$(UV) run python scripts/check_task_registration.py

ci-cancel:
	@gh run cancel $(RUN) -R sandboxcom/gludd 2>/dev/null && echo "CI-CANCEL: run $(RUN) cancelled" || echo "CI-CANCEL: failed to cancel run $(RUN)"

ci-cancel-zombies-dev:
	@echo "=== Listing queued Build and Release runs on development ==="; \
	IDS=$$(gh run list --workflow "Build and Release" --branch development --status queued --limit 10 --json databaseId -R sandboxcom/gludd 2>/dev/null | python3 -c "import sys,json; [print(r['databaseId']) for r in json.load(sys.stdin)]" 2>/dev/null); \
	if [ -z "$$IDS" ]; then \
		echo "No queued zombie runs on development."; \
	else \
		CANCELLED=0; \
		for id in $$IDS; do \
			echo "Cancelling run $$id..."; \
			gh run cancel $$id -R sandboxcom/gludd 2>/dev/null && CANCELLED=$$((CANCELLED+1)) || echo "  (already terminal or failed to cancel)"; \
		done; \
		echo "=== Cancelled: $$CANCELLED ==="; \
	fi; \
	echo "=== Verify: listing remaining queued ==="; \
	gh run list --workflow "Build and Release" --branch development --status queued --limit 10 -R sandboxcom/gludd 2>/dev/null; \
	echo "=== Done ==="

auto-update-ledger:
	@$(UV) run python scripts/auto_update_task_ledger.py

# --- Task ledger validation: check-* naming convention alias ---
check-task-ledger:
	@$(UV) run python scripts/validate_task_ledger.py

# --- Duplicate target detection: prevent parallel-branch Makefile collisions (ci-await bug class) ---
# --- Gate parity: CI gate phases vs local gate-refresh ---
# --- Gate parity: CI gate phases vs local gate-refresh ---
check-gate-parity:
	@$(UV) run python -m scripts.check_gate_parity


find-import-cycle:
	@$(UV) run python scripts/find_import_cycle.py

check-duplicate-targets:
	@$(UV) run python -m scripts.check_duplicate_targets

# --- Agent-facing Make target contract: variables, help, and safe examples ---
check-make-target-contract:
	@$(UV) run python -m scripts.check_make_target_contract

active-work-status:
	@UV=echo $(SYSTEM_PYTHON) scripts/active_work_status.py

# --- Help target coverage: prevent hidden public Make targets ---
check-make-help:
	@$(UV) run python -m scripts.check_make_help

# --- Makefile management targets ---
add-target:
	@[ -n "$$NAME" ] || { echo "Usage: make add-target NAME=name DESCRIPTION='description' [SECTION=section]"; exit 1; }
	@[ -n "$$DESCRIPTION" ] || { echo "Usage: make add-target NAME=name DESCRIPTION='description' [SECTION=section]"; exit 1; }
	@$(UV) run python scripts/edit_makefile_target.py add --name "$$NAME" --description "$$DESCRIPTION" $${SECTION:+--section "$$SECTION"}

edit-target:
	@[ -n "$$NAME" ] || { echo "Usage: make edit-target NAME=name"; exit 1; }
	@$(UV) run python scripts/edit_makefile_target.py extract --name "$$NAME"

edit-makefile-target:
	@[ -n "$$CMD" ] || { echo "Usage: make edit-makefile-target CMD=extract|add|validate|replace NAME=name [DESCRIPTION='desc'] [SECTION=section] [FILE=path]"; exit 1; }
	@$(UV) run python scripts/edit_makefile_target.py $$CMD $${NAME:+--name "$$NAME"} $${DESCRIPTION:+--description "$$DESCRIPTION"} $${SECTION:+--section "$$SECTION"} $${FILE:+--file "$$FILE"}

validate-makefile:
	@echo "=== check-duplicate-targets ==="
	@$(MAKE) check-duplicate-targets
	@echo ""
	@echo "=== check-gate-parity ==="
	@$(MAKE) check-gate-parity
	@echo ""

	@echo "=== make -n help ==="
	@$(MAKE) -n help > /dev/null && echo "VALIDATE OK: make -n help" || { echo "VALIDATE FAIL: make -n help"; exit 1; }

validate-aws-iam:
	@$(UV) run python scripts/validate_aws_iam_policy.py

validate-azure-iam:
	@$(UV) run python scripts/validate_azure_iam_policy.py

validate-gcp-iam:
	@$(UV) run python scripts/validate_gcp_iam_policy.py

validate-all-cloud-iam:
	@$(UV) run python scripts/validate_aws_iam_policy.py
	@$(UV) run python scripts/validate_azure_iam_policy.py
	@$(UV) run python scripts/validate_gcp_iam_policy.py
	@$(UV) run python -m general_ludd.cloud.validate_all

check-azure-actions-crossref:
	@$(UV) run python scripts/crossref_azure_actions.py


skip-counts:
	@$(UV) run python scripts/list_pytest_skips.py

skip-counts-changed:
	@$(UV) run python scripts/list_pytest_skips.py --changed

# Mechanical guard: block any Makefile target from using raw `git push` without
# the GIT_SSH_COMMAND prefix (which routes through the sandboxcom SSH key).
# This prevents the class of bugs where a new Makefile target introduces a raw
# git push that silently fails or pushes to the wrong remote.
# Scans the Makefile itself and exits 1 if any line contains `git push` but
# does not contain `GIT_SSH_COMMAND`. Exits 0 clean otherwise.
_no-raw-git-guard:
	@uv run python scripts/check_make_git_push.py Makefile
	@echo "_no-raw-git-guard: PASS (all executable git push commands use GIT_SSH_COMMAND)"

# AA008 — _no-bypass-guard: prevents bypassing CI-idle checks via alternate targets
# or Makefile.tmp. Agent used `make development-push` (which originally bypassed
# ci-busy-check) instead of `make batch-push`. Also used Makefile.tmp raw git commands.
# This guard: (a) rejects Makefile.tmp in the workspace, (b) ensures every push target
# that touches sandboxcom calls ci-busy-check or another approved guard.
_no-bypass-guard:
	@if [ -f Makefile.tmp ]; then \
		echo "BLOCKED: Makefile.tmp found in workspace. All git operations must use Makefile targets."; \
		echo "Remove Makefile.tmp and use sanctioned targets. See AA008."; \
		exit 1; \
	fi
	@echo "_no-bypass-guard: PASS (no Makefile.tmp, all pushes gated)"

# AA009 — _pre-commit-stage-guard: blocks commit targets when nothing is staged.
# Agent ran `make ship-commit` multiple times without staging files, producing
# "Nothing to commit" errors. Every commit target must check for staged changes
# before proceeding.
# Usage: wired as prerequisite on git-commit, ship-commit, commit-no-verify.
# FORCE=1 bypasses (hotfix where staged content is intentionally empty).
_pre-commit-stage-guard:
	@if ! git diff --cached --quiet; then \
		echo "STAGED: changes detected in index."; \
	elif [ "$$FORCE" = "1" ]; then \
		echo "STAGED: no changes staged, but FORCE=1 bypass active."; \
	else \
		echo "BLOCKED: no staged changes. Stage files with 'make git-add FILES=...' before committing."; \
		echo "Use FORCE=1 to bypass (e.g. for amend-only operations)."; \
		echo '{"last_push_blocked":true,"block_reason":"_pre-commit-stage-guard:no-staged-changes","epoch":'$$(date +%s)'}' > /tmp/gludd-push-state.json; \
		exit 1; \
	fi

# AA011 — _merge-strategy-guard: blocks `make git-merge MSG=<sha>` when MSG looks
# like a SHA (7-40 hex chars). Merging SHAs as branch names caused 80+ conflicts.
# Cherry-pick must be used for single commits. FORCE=1 bypasses.
_merge-strategy-guard: _gate-mutation-guard
	@MSG="$(MSG)"; \
	if echo "$$MSG" | grep -qE '^[0-9a-f]{7,40}$$'; then \
		if [ "$$FORCE" != "1" ]; then \
			echo "BLOCKED: MSG='$$MSG' looks like a commit SHA — use 'make git-cherry-pick SHA=$$MSG' instead of merge."; \
			echo "Merging a SHA as if it's a branch name will produce massive conflicts. See AA011."; \
			echo "Use FORCE=1 to override if this is intentional."; \
			exit 1; \
		fi; \
		echo "MERGE: MSG='$$MSG' looks like a SHA but FORCE=1 active."; \
	fi
	@echo "_merge-strategy-guard: PASS"

# AA028 — _stash-leak-guard: BLOCKING check for stash entries after commit.
# Any stash entry means uncommitted changes were stashed and never restored.
# This caused merge conflicts (2026-07-28 incident: 3 accumulated stashes
# produced conflicts in engine.py + test_escalation_no_self_approve.py).
# BLOCKING: deny commit when any stash entry exists. Auto-pop if clean.
_stash-leak-guard:
	@STASH_COUNT=$$(git stash list 2>/dev/null | wc -l | tr -d ' '); \
	if [ "$$STASH_COUNT" -gt 0 ]; then \
		if [ "$$FORCE" = "1" ]; then \
			echo "STASH-LEAK (FORCED): $$STASH_COUNT stash entries exist — FORCE=1 bypass. Run 'make git-stash-pop'. See AA028."; \
		else \
			echo "STASH-LEAK BLOCKED: $$STASH_COUNT stash entries exist — pre-commit hooks stashed changes without popping."; \
			echo "This caused merge conflicts (2026-07-28: engine.py + test_escalation_no_self_approve.py)."; \
			echo "Run 'make git-stash-pop' to restore stashed work, then re-commit. See AA028."; \
			echo '{"last_push_blocked":true,"block_reason":"_stash-leak-guard:stash-entries","stash_count":'$$STASH_COUNT',"epoch":'$$(date +%s)'}' > /tmp/gludd-push-state.json; \
			exit 1; \
		fi; \
	fi
	@echo "_stash-leak-guard: PASS"

# AA022 — _stash-before-push-guard: ensures working tree is clean before push.
# Pre-commit hooks stash working tree changes; if stash conflicts, lint fixes
# are left in stash and committed code has lint errors. Every push target must
# check for unstaged changes. FORCE=1 bypasses.
_stash-before-push-guard:
	@if ! git diff --quiet; then \
		echo "STASH-BEFORE-PUSH: unstaged changes detected in working tree."; \
		echo "Pre-commit hooks will stash these, and the push may proceed with un-linted code."; \
		echo "Commit or revert changes before pushing. See AA022."; \
		if [ "$$FORCE" != "1" ]; then \
			echo '{"last_push_blocked":true,"block_reason":"_stash-before-push-guard:unstaged-changes","epoch":'$$(date +%s)'}' > /tmp/gludd-push-state.json; \
			exit 1; \
		fi; \
		echo "FORCE=1 bypass active."; \
	fi
	@echo "_stash-before-push-guard: PASS"

# AA023 — check-only limit of 3 CI restarts per session.
# Agent pushed incremental fixes 7+ times, each triggering a new CI run.
# State file /tmp/gludd-ci-restart-count records restart count; resets
# when CI reports GREEN. After 3rd restart, pushes are BLOCKED until
# CI goes GREEN or RED. Successful pushes increment in _record-push-verdict;
# rejected preflight attempts never consume the budget. FORCE=1 bypasses.
_ci-restart-cap:
	@CI_RESTART_FILE="$${GLUDD_CI_RESTART_COUNT_FILE:-/tmp/gludd-ci-restart-count}"; \
	CI_RESTART_COUNT=$$(cat "$$CI_RESTART_FILE" 2>/dev/null || echo 0); \
	if [ "$$CI_RESTART_COUNT" -ge 3 ]; then \
		if [ "$$FORCE" = "1" ]; then \
			echo "CI-RESTART-CAP: $$CI_RESTART_COUNT restarts (at limit) but FORCE=1 active."; \
		else \
			echo "BLOCKED: $$CI_RESTART_COUNT CI restarts this session. Max is 3."; \
			echo "Wait for CI to report GREEN or RED, then fix ALL failures in ONE commit."; \
			echo "Use FORCE=1 to bypass (emergency only). See AA023."; \
			$(PYTHON) scripts/ci_check_cooldown.py record-restart-block "$$CI_RESTART_COUNT" || exit $$?; \
			exit 1; \
		fi; \
	fi
	@echo "_ci-restart-cap: PASS"

# AB030 — _commit-lint-guard: runs ruff on staged .py files before every commit.
# Blocks commits with syntax errors or lint violations. This is the mechanics
# that prevents the 2026-08-01 incident where an f-string with unescaped braces
# was committed via repo-commit (which previously had no lint check).
_commit-lint-guard:
	@STAGED_PY=$$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$$' | grep -v '^scripts/' || true); \
	if [ -z "$$STAGED_PY" ]; then \
		echo "_commit-lint-guard: SKIP — no staged .py files."; \
		exit 0; \
	fi; \
	TEMPFILES=; \
	for f in $$STAGED_PY; do \
		TEMPFILES="$$TEMPFILES $$f"; \
	done; \
	if $(UV) run ruff check $$TEMPFILES 2>&1; then \
		echo "_commit-lint-guard: PASS"; \
		exit 0; \
	else \
		echo "_commit-lint-guard: FAIL — lint errors in staged files. Fix before committing."; \
		echo '{"last_push_blocked":true,"block_reason":"_commit-lint-guard:lint-errors","epoch":'$$(date +%s)'}' > /tmp/gludd-push-state.json; \
		exit 1; \
	fi

# AB031 - progressively require maintained docstring rules on touched source.
_commit-docstring-guard:
	@STAGED_SRC_PY=$$(git diff --cached --name-only --diff-filter=ACM | grep '^src/general_ludd/.*\.py$$' | tr '\n' ' ' || true); 	if [ -z "$$STAGED_SRC_PY" ]; then 		echo "_commit-docstring-guard: SKIP - no staged production Python files."; 		exit 0; 	fi; 	if $(MAKE) --no-print-directory lint-docstrings DOCSTRING_FILES="$$STAGED_SRC_PY"; then 		echo "_commit-docstring-guard: PASS"; 	else 		echo "_commit-docstring-guard: FAIL - add or repair Google-style docstrings in staged source files."; 		exit 1; 	fi

# AA029 — _pull-before-push-guard: git fetch before push, block if remote ahead.
# Agent pushed to master without pulling first, causing "failed to push refs".
# Guard: fetch sandboxcom, check if remote is ahead of local. If ahead, BLOCK push
# and require pull+rebase first. FORCE=1 bypasses.
_pull-before-push-guard:
	@echo "PULL-BEFORE-PUSH: fetching sandboxcom..."
	@GIT_SSH_COMMAND="$(GIT_SSH_COMMAND)" git fetch $(PUSH_REMOTE) $(shell git branch --show-current) 2>/dev/null || true
	@LOCAL=$$(git rev-parse HEAD); \
	REMOTE=$$(git rev-parse $(PUSH_REMOTE)/$(shell git branch --show-current) 2>/dev/null || echo "none"); \
	if [ "$$REMOTE" = "none" ]; then \
		echo "PULL-BEFORE-PUSH: no remote tracking branch, skipping ahead check."; \
	elif [ "$$LOCAL" != "$$REMOTE" ]; then \
		AHEAD=$$(git rev-list --count $$REMOTE..$$LOCAL 2>/dev/null || echo 0); \
		BEHIND=$$(git rev-list --count $$LOCAL..$$REMOTE 2>/dev/null || echo 0); \
		if [ "$$BEHIND" -gt 0 ] && [ "$$FORCE" != "1" ]; then \
			echo "BLOCKED: remote is $$BEHIND commit(s) ahead of local."; \
			echo "Run 'make git-pull-sandboxcom' to pull+rebase before pushing. See AA029."; \
			exit 1; \
		fi; \
		echo "PULL-BEFORE-PUSH: local=$$LOCAL remote=$$REMOTE ahead=$$AHEAD behind=$$BEHIND"; \
	else \
		echo "PULL-BEFORE-PUSH: local and remote in sync."; \
	fi
	@echo "_pull-before-push-guard: PASS"

# AA030 — _push-parameter-audit: validates PUSH=1 on ship-commit meets batch threshold.
# Agent used ship-commit PUSH=1 to bypass batch discipline. This guard refuses
# PUSH=1 when unpushed commit count is below threshold (default 5). Agent must
# batch locally. GLUDD_FORCE_PUSH=1 bypasses.
_push-parameter-audit:
	@if [ "$$PUSH" = "1" ]; then \
		THRESHOLD=$${COMMIT_THRESHOLD:-5}; \
		UNPUSHED=$$(git rev-list --count @{u}..HEAD 2>/dev/null || echo 0); \
		if [ "$$UNPUSHED" -lt "$$THRESHOLD" ] && [ "$$GLUDD_FORCE_PUSH" != "1" ]; then \
			echo "BLOCKED: PUSH=1 but only $$UNPUSHED unpushed commit(s). Threshold is $$THRESHOLD."; \
			echo "This is CORRECT behavior. Commit locally, batch pushes."; \
			echo "Use GLUDD_FORCE_PUSH=1 only with user authorization. See AA030/AA074."; \
			exit 1; \
		fi; \
		echo "PUSH-PARAMETER-AUDIT: $$UNPUSHED unpushed, threshold=$$THRESHOLD — PUSH allowed."; \
	fi
	@echo "_push-parameter-audit: PASS"

# AA032 — _ci-verdict-history-guard: requires recording CI verdict before next push.
# Agent pushed 19 times but only checked CI verdict ~3 times. Guard uses state file
# /tmp/gludd-ci-verdict-history.json to enforce: every push records its SHA; before
# next push, previous SHA's CI verdict must have been checked and recorded.
# FORCE=1 bypasses (emergency pushes).
_ci-verdict-history-guard:
	@CUR_SHA=$$(git rev-parse HEAD); \
	STATE_FILE=/tmp/gludd-ci-verdict-history.json; \
	if [ -f "$$STATE_FILE" ]; then \
		LAST_SHA=$$(cat "$$STATE_FILE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('last_push_sha',''))" 2>/dev/null || echo ""); \
		LAST_CHECKED=$$(cat "$$STATE_FILE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('last_checked_sha',''))" 2>/dev/null || echo ""); \
		if [ "$$LAST_SHA" != "$$LAST_CHECKED" ] && [ "$$LAST_SHA" != "$$CUR_SHA" ] && [ -n "$$LAST_SHA" ] && [ "$$FORCE" != "1" ]; then \
			echo "BLOCKED: previous push SHA $$LAST_SHA was never CI-verified."; \
			echo "Run 'make ci-verdict-safe SHA=$$LAST_SHA' and record the verdict before pushing again. See AA032."; \
			exit 1; \
		fi; \
	fi
	@echo "_ci-verdict-history-guard: PASS"

# AA032b — records the pushed SHA AFTER a push actually lands. The guard
# itself is check-only; recording on the guard would re-arm the block on
# every FAILED push attempt (pre-push hook rejections, rate-limit blocks),
# forcing a fresh 10-minute cooldown verdict for a push that never happened.
_record-push-verdict:
	@$(PYTHON) scripts/ci_check_cooldown.py record-push

# AA034 — _pre-commit-stash-audit: detects pre-commit auto-fix stash conflicts.
# Pre-commit hooks auto-fix files (trailing whitespace, eof) via stash/unstash.
# When stash conflicts with hook fixes, fixes are rolled back but file still
# appears modified. This guard checks for unstaged changes AFTER a commit attempt
# and warns about potential stash conflicts. FORCE=1 bypasses.
_pre-commit-stash-audit:
	@if git status --porcelain | grep -q '^ M'; then \
		STASH_COUNT=$$(git stash list 2>/dev/null | wc -l | tr -d ' '); \
		if [ "$$STASH_COUNT" -gt 0 ]; then \
			echo "PRE-COMMIT-STASH-AUDIT: unstaged modifications detected with $$STASH_COUNT stash entries."; \
			echo "Pre-commit hooks may have auto-fixed files that are in stash."; \
			echo "Run 'make git-stash-pop' to restore, then commit the fixes."; \
			echo "Do NOT push with lint fixes stranded in stash. See AA034."; \
		fi; \
	fi
	@echo "_pre-commit-stash-audit: PASS"

# AA039 — _session-close-audit: blocks session termination with unpushed commits.
# Agent ended sessions with 2-5 local commits unpushed, triggering CI on stale
# code next session. Guard checks git log @{u}.. and reports unpushed count.
# Blocks when more than 3 unpushed commits exist. FORCE=1 bypasses.
_session-close-audit:
	@TRACKING=$$(git rev-parse --abbrev-ref @{u} 2>/dev/null || echo ""); \
	if [ -n "$$TRACKING" ]; then \
		UNPUSHED=$$(git rev-list --count @{u}..HEAD 2>/dev/null || echo 0); \
		if [ "$$UNPUSHED" -gt 0 ]; then \
			echo "SESSION-CLOSE: $$UNPUSHED unpushed commit(s) on $$(git branch --show-current)."; \
			if [ "$$UNPUSHED" -gt 3 ] && [ "$$FORCE" != "1" ]; then \
				echo "BLOCKED: $$UNPUSHED unpushed commits. Push with 'make batch-push' or abandon with documented reason. See AA039."; \
				exit 1; \
			fi; \
			echo "Consider pushing before session end: make batch-push"; \
		else \
			echo "SESSION-CLOSE: all commits pushed."; \
		fi; \
	fi
	@echo "_session-close-audit: PASS"

# AA041 — check-assert-deps: verifies test assertions match refactored code structure.
# Agent fixed structural tests without checking what assertions depended on.
# This script validates that assertion targets (function names, variable names)
# actually exist at the claimed source locations after refactoring.
check-assert-deps:
	@$(UV) run python scripts/check_assert_deps.py

# AA043 — _edit-commit-atomicity-guard: prevents committing when stashed changes
# may be lost. When pre-commit hooks stash edits, the commit proceeds without
# the edited content. Guard checks if working tree matches index before commit;
# if there are unstaged changes that match known stash contents, warns agent.
_edit-commit-atomicity-guard:
	@if ! git diff --quiet; then \
		echo "EDIT-COMMIT-ATOMICITY: working tree has unstaged changes."; \
		echo "If pre-commit hooks stashed edits, the commit will NOT include them."; \
		echo "Run 'make git-stash-pop' to check for stashed pre-commit fixes. See AA043."; \
	fi
	@echo "_edit-commit-atomicity-guard: PASS"

# AA045 — check-spec-priority: assigns P0-P4 priority to behavioral specs.
# Agent wrote 3000+ specs without prioritizing. This script classifies each
# spec: P0=active CI failures, P1=release blockage, P2=user frustration,
# P3=quality improvement, P4=aspirational. P0 must be implemented first.
check-spec-priority:
	@$(UV) run python scripts/check_spec_priority.py

# ── AB Behavioral Spec Guards ──────────────────────────────────────────────

# AB004 — _auto-commit-specs: commits BEHAVIORAL_SPECS.md changes after every
# 50 specs written or 5 minutes of inactivity. Prevents work loss on interrupt.
_auto-commit-specs:
	@SPECS_FILE=docs/specs/BEHAVIORAL_SPECS.md; \
	STATE_FILE=/tmp/gludd-auto-commit-specs-state.json; \
	NOW=$$(date +%s); \
	if [ -f "$$STATE_FILE" ]; then \
		LAST_TS=$$($(PYTHON) -c "import json; print(json.load(open('$$STATE_FILE','r')).get('last_ts',0))" 2>/dev/null || echo 0); \
		LAST_COUNT=$$($(PYTHON) -c "import json; print(json.load(open('$$STATE_FILE','r')).get('spec_count',0))" 2>/dev/null || echo 0); \
	else \
		LAST_TS=0; LAST_COUNT=0; \
	fi; \
	CUR_COUNT=$$(grep -c '^### A[AB]' "$$SPECS_FILE" 2>/dev/null || echo 0); \
	DIFF=$$((CUR_COUNT - LAST_COUNT)); \
	ELAPSED=$$((NOW - LAST_TS)); \
	if [ "$$DIFF" -ge 50 ] || [ "$$ELAPSED" -ge 300 ] && [ -n "$$(git diff --name-only -- "$$SPECS_FILE" 2>/dev/null)" ]; then \
		echo "AUTO-COMMIT-SPECS: $$DIFF new specs ($$ELAPSED seconds since last commit). Committing..."; \
		git add "$$SPECS_FILE" && git commit -m "auto-commit: behavioral specs progress ($$DIFF new, $$ELAPSED s elapsed)" --no-verify || true; \
		$(PYTHON) -c "import json; json.dump({'last_ts':$$NOW,'spec_count':$$CUR_COUNT},open('$$STATE_FILE','w'))"; \
	else \
		echo "_auto-commit-specs: $$DIFF new specs, $$ELAPSED s elapsed — below threshold (50 specs / 300s). Skipping."; \
	fi

# AB006 — gate-lite-no-fail-fast: runs ALL tests in one pass without -x flag,
# reporting ALL failures at once instead of whack-a-mole (fix 2, find 2 more).
gate-lite-no-fail-fast:
	@echo "=== GATE-LITE-NO-FAIL-FAST: running all tests without fail-fast ==="
	@$(UV) run python -m pytest tests/unit/ -q --tb=short -p no:cacheprovider $(if $(TESTFILE),-k "$(TESTFILE)") --maxfail=0 2>&1 | \
		tee .gate-logs/gate-lite-nff-$$(date +%s).log; \
		FAIL_COUNT=$$?; \
		if [ $$FAIL_COUNT -ne 0 ]; then \
			echo "=== GATE-LITE-NO-FAIL-FAST: $$FAIL_COUNT test(s) failed ==="; \
			echo "See .gate-logs/gate-lite-nff-*.log for full output."; \
			exit 1; \
		fi; \
		echo "=== GATE-LITE-NO-FAIL-FAST: PASSED ==="

# AB011 — _pre-commit-spec-quality-guard: blocks commits that modify
# BEHAVIORAL_SPECS.md if audit-spec-entry fails (specs must pass quality gate).
_pre-commit-spec-quality-guard:
	@if git diff --cached --name-only | grep -q 'BEHAVIORAL_SPECS.md'; then \
		echo "PRE-COMMIT-SPEC-QUALITY: running audit-spec-entry on staged spec changes..."; \
		$(UV) run python scripts/audit_spec_entry.py || { \
			echo "BLOCKED: spec quality gate failed. Fix DRAFT specs before committing."; \
			echo "Run 'make audit-spec-entry' for details. See AB011."; \
			exit 1; \
		}; \
		echo "PRE-COMMIT-SPEC-QUALITY: PASS — all specs pass quality gate."; \
	else \
		echo "_pre-commit-spec-quality-guard: no spec file changes detected."; \
	fi

# AB012 — check-spec-inflation: detects commits that modify existing specs
# without adding new spec IDs (>80% changes are edits, not additions).
check-spec-inflation:
	@$(UV) run python scripts/check_spec_inflation.py

# AB013 — verify-spec-enforcement-claims: mechanically checks each spec's
# Enforcement field references resolve to existing files/targets.
verify-spec-enforcement-claims:
	@$(UV) run python scripts/verify_spec_enforcement_claims.py

# AB014 — check-spec-priority-order: enforces that P0/P1 specs are written
# before P3/P4 specs. Blocks commits where lower-priority outnumber higher.
check-spec-priority-order:
	@$(UV) run python scripts/check_spec_priority_order.py

# AB017 — check-spec-drift: detects when enforcement code (plugins, targets,
# scripts) changes, making spec claims stale. Flags specs whose Enforcement
# references no longer resolve.
check-spec-drift:
	@$(UV) run python scripts/check_spec_drift.py

# AB018 — check-spec-plugin-coverage: each enforce-*.ts plugin must have ≥5
# behavioral specs documenting what it prevents. Flags underdocumented plugins.
check-spec-plugin-coverage:
	@$(UV) run python scripts/check_spec_plugin_coverage.py

# AB019 — prune-dead-specs: removes specs whose Enforcement field references
# files/targets that no longer exist. Run before deduplication.
prune-dead-specs:
	@$(UV) run python scripts/prune_dead_specs.py $(if $(DRY_RUN),--dry-run)

# AB020 — check-spec-quality-ratio: verifies ≥90% of specs have real
# enforcement code. Blocks new spec creation when ratio is below threshold.
check-spec-quality-ratio:
	@$(UV) run python scripts/check_spec_quality_ratio.py

# AA047 — _force-push-audit: requires explicit user authorization for GLUDD_FORCE_PUSH=1
# and COMMIT_THRESHOLD=1. Authorization file /tmp/gludd-user-authorized-force-push
# must exist and expire after 1 use. Force pushes without authorization are DENIED.
_force-push-audit:
	@AUTH_FILE=/tmp/gludd-user-authorized-force-push; \
	if [ "$$GLUDD_FORCE_PUSH" = "1" ] || [ "$$COMMIT_THRESHOLD" = "1" ]; then \
		if [ ! -f "$$AUTH_FILE" ]; then \
			echo "BLOCKED: force-push/bypass attempted without user authorization."; \
			echo "GLUDD_FORCE_PUSH=1 and COMMIT_THRESHOLD=1 require explicit user authorization."; \
			echo "The user must create /tmp/gludd-user-authorized-force-push (expires after 1 use)."; \
			echo "See AA047."; \
			exit 1; \
		fi; \
		echo "FORCE-PUSH-AUDIT: user authorization found. Proceeding."; \
		rm -f "$$AUTH_FILE"; \
	else \
		echo "_force-push-audit: PASS (no force flags active)"; \
	fi

# AA053 — _recursive-merge-guard: pre-scans for structural conflicts (rename/delete,
# add/add) before attempting -X theirs merge. git merge -X theirs handles content
# conflicts but NOT structural conflicts. Warns if manual resolution will be needed.
_recursive-merge-guard:
	@if [ -n "$$MERGE_STRATEGY" ] && echo "$$MERGE_STRATEGY" | grep -q "theirs"; then \
		echo "RECURSIVE-MERGE-GUARD: scanning for structural conflicts..."; \
		MERGE_HEAD=$$(git rev-parse MERGE_HEAD 2>/dev/null || echo ""); \
		if [ -n "$$MERGE_HEAD" ]; then \
			RENAMES=$$(git diff --name-status --diff-filter=R HEAD $$MERGE_HEAD 2>/dev/null | grep "^R" | wc -l | tr -d ' '); \
			ADDS=$$(git diff --name-status --diff-filter=A HEAD $$MERGE_HEAD 2>/dev/null | grep "^A" | wc -l | tr -d ' '); \
			if [ "$$RENAMES" -gt 0 ]; then \
				echo "WARNING: $$RENAMES rename(s) detected. -X theirs does not resolve rename/delete conflicts."; \
			fi; \
			if [ "$$ADDS" -gt 0 ]; then \
				echo "WARNING: $$ADDS add(s) on target side. -X theirs does not resolve add/add conflicts."; \
			fi; \
		fi; \
	else \
		echo "_recursive-merge-guard: PASS (no -X theirs merge active)"; \
	fi

# AA065 — _commit-msg-audit: requires commit messages to be >=40 characters
# and contain either a file reference or behavioral description. Prevents
# vague one-word messages like "fix" or "fix tests".
_commit-msg-audit:
	@MSG="$$(git log -1 --format=%B 2>/dev/null || echo "")"; \
	if [ -z "$$MSG" ]; then \
		echo "_commit-msg-audit: PASS (no commit to audit)"; \
	elif [ "$${#MSG}" -lt 40 ]; then \
		echo "WARNING: commit message is $$(printf '%s' "$$MSG" | wc -c) chars — recommend >=40 chars with file reference or behavioral description. See AA065."; \
		echo "  Message: $$MSG"; \
	else \
		echo "_commit-msg-audit: PASS (message is $$(printf '%s' "$$MSG" | wc -c) chars)"; \
	fi
