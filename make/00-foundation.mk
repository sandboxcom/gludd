# This one-shot mode is selected only by the owning file-defined alias.
unexport _TEST_FILES_CPROFILE
ifneq ($(filter command line environment override,$(origin _TEST_FILES_CPROFILE)),)
$(error _TEST_FILES_CPROFILE is internal; use test-files-cprofile)
endif
override _TEST_FILES_CPROFILE := 0

MSG ?=
FILES ?=
TESTFILE ?=
REF ?=
TARGET ?= master
MYPY_MAX := 0
MYPY_NULL_CACHE := $(if $(filter Windows_NT,$(OS)),nul,/dev/null)
OPENCODE_DB ?= ~/.local/share/opencode/opencode.db
OPENCODE_DATA_DIR ?=
OPENCODE_RETENTION_DAYS ?= 30
OPENCODE_DB_BATCH_SIZE ?= 500
OPENCODE_DB_MAX_SESSIONS ?= 50000
OPENCODE_DB_TIMEOUT_SECONDS ?= 60
OPENCODE_DB_BUSY_TIMEOUT_MS ?= 1000
OPENCODE_DB_INCREMENTAL_PAGES ?= 1000
OPENCODE_MAX_FILE_ENTRIES ?= 100000
OPENCODE_MAINTENANCE_VALIDATE_ONLY ?= 0
OPENCODE_MAINTENANCE_FORCE ?= 0
GLUDD_TASK_TIMEOUT ?= 300
TIMEOUT ?= 3600
GATE_POLL_INTERVAL ?= 60
GATE_TIMEOUT ?= 21600
GATE_BACKGROUND_VALIDATE_ONLY ?= 0
GATE_BACKGROUND_OBSERVED_VALIDATE_ONLY ?= 0
GATE_EXPECTED_PID ?=
# Expand MAKE before recipe execution so launcher recipes are ordinary commands
# under `make -n`; direct $(MAKE) references would execute despite dry-run mode.
_GATE_MAKE := $(MAKE)
INTERVAL ?= 300
RELEASE_AWAIT_TIMEOUT ?= 5400
RELEASE_AWAIT_INTERVAL ?= 10
COUNT ?= 1
NODE_DEPS_NPM_USERCONFIG ?= /dev/null
NODE_DEPS_NPM_CACHE ?= /tmp/gludd-npm-cache-public-v1
NODE_DEPS_NPM_REGISTRY ?= https://registry.npmjs.org
NODE_DEPS_NPM_UPDATE_NOTIFIER ?= false
NODE_DEPS_AUDIT_LEVEL ?= moderate
FREELLMAPI_ADMISSION_TAG ?= v0.11.1
FREELLMAPI_ADMISSION_COMMIT ?= 4191d8e7abef39fcd93fab009123467036f39750
FREELLMAPI_ADMISSION_LIVE ?= 0
FREELLMAPI_ADMISSION_OUTPUT ?= config/freellmapi/upstream_candidate.json
FREELLMAPI_BUILD_LIVE ?= 0
FREELLMAPI_BUILD_CANDIDATE ?= config/freellmapi/upstream_candidate.json
FREELLMAPI_BUILD_PLAN ?= config/freellmapi/upstream_build_plan.json
FREELLMAPI_BUILD_REPORT ?= /tmp/gludd-freellmapi-upstream-build/evidence.json
FREELLMAPI_THREE_ARM_MODE ?= replay
FREELLMAPI_THREE_ARM_CANDIDATE ?= config/freellmapi/upstream_candidate.json
FREELLMAPI_THREE_ARM_PLAN ?= config/freellmapi/three_arm_plan.json
FREELLMAPI_THREE_ARM_CORPUS ?= config/freellmapi/three_arm_corpus.json
FREELLMAPI_THREE_ARM_REPORT ?= /tmp/gludd-freellmapi-three-arm/evidence.json
ifneq (,$(findstring $$,$(value FREELLMAPI_ADMISSION_TAG)))
$(error FREELLMAPI_ADMISSION_TAG contains forbidden input)
endif
ifneq (,$(findstring $$,$(value FREELLMAPI_ADMISSION_COMMIT)))
$(error FREELLMAPI_ADMISSION_COMMIT contains forbidden input)
endif
ifneq (,$(findstring $$,$(value FREELLMAPI_ADMISSION_LIVE)))
$(error FREELLMAPI_ADMISSION_LIVE contains forbidden input)
endif
ifneq (,$(findstring $$,$(value FREELLMAPI_ADMISSION_OUTPUT)))
$(error FREELLMAPI_ADMISSION_OUTPUT contains forbidden input)
endif
ifneq (,$(findstring $$,$(value FREELLMAPI_BUILD_LIVE)))
$(error FREELLMAPI_BUILD_LIVE contains forbidden input)
endif
ifneq (,$(findstring $$,$(value FREELLMAPI_BUILD_CANDIDATE)))
$(error FREELLMAPI_BUILD_CANDIDATE contains forbidden input)
endif
ifneq (,$(findstring $$,$(value FREELLMAPI_BUILD_PLAN)))
$(error FREELLMAPI_BUILD_PLAN contains forbidden input)
endif
ifneq (,$(findstring $$,$(value FREELLMAPI_BUILD_REPORT)))
$(error FREELLMAPI_BUILD_REPORT contains forbidden input)
endif
ifneq (,$(findstring $$,$(value FREELLMAPI_THREE_ARM_MODE)))
$(error FREELLMAPI_THREE_ARM_MODE contains forbidden input)
endif
ifneq (,$(findstring $$,$(value FREELLMAPI_THREE_ARM_CANDIDATE)))
$(error FREELLMAPI_THREE_ARM_CANDIDATE contains forbidden input)
endif
ifneq (,$(findstring $$,$(value FREELLMAPI_THREE_ARM_PLAN)))
$(error FREELLMAPI_THREE_ARM_PLAN contains forbidden input)
endif
ifneq (,$(findstring $$,$(value FREELLMAPI_THREE_ARM_CORPUS)))
$(error FREELLMAPI_THREE_ARM_CORPUS contains forbidden input)
endif
ifneq (,$(findstring $$,$(value FREELLMAPI_THREE_ARM_REPORT)))
$(error FREELLMAPI_THREE_ARM_REPORT contains forbidden input)
endif
export FREELLMAPI_ADMISSION_TAG
export FREELLMAPI_ADMISSION_COMMIT
export FREELLMAPI_ADMISSION_LIVE
export FREELLMAPI_ADMISSION_OUTPUT
export FREELLMAPI_BUILD_LIVE
export FREELLMAPI_BUILD_CANDIDATE
export FREELLMAPI_BUILD_PLAN
export FREELLMAPI_BUILD_REPORT
export FREELLMAPI_THREE_ARM_MODE
export FREELLMAPI_THREE_ARM_CANDIDATE
export FREELLMAPI_THREE_ARM_PLAN
export FREELLMAPI_THREE_ARM_CORPUS
export FREELLMAPI_THREE_ARM_REPORT
GLUDD_UV_CACHE_DIR ?= /tmp/gludd-uv-cache-public-v2
override UV_CACHE_DIR := $(GLUDD_UV_CACHE_DIR)
export UV_CACHE_DIR
# Profile environments are deliberately composed from multiple independent locks.
# Every automatic `uv run` exact-sync would otherwise erase later profile layers;
# explicit `make sync` remains the only environment mutation entry point.
override UV_NO_SYNC := 1
export UV_NO_SYNC
RELEASE_READINESS_VALIDATE_ONLY ?= 0
RELEASE_PROOF_ADMISSION_VALIDATE_ONLY ?= 0
RELEASE_COMPLETED_STAGES ?=
RELEASE_OBSERVATIONS ?=
REVIEWED_HEAD_INTEGRATION_RECEIPT ?=
REVIEWED_HEAD_RECEIPT_MANIFEST ?=
REVIEWED_HEAD_GATE_ATTESTATION ?=
REVIEWED_HEAD_RECEIPT_OUTPUT ?=
REVIEWED_HEAD_RECEIPT_REPO_ROOT ?= .
REVIEWED_HEAD_RECEIPT_VALIDATE_ONLY ?= 0
REVIEWED_HEAD_INPUTS_ACTION ?= admit
REVIEWED_HEAD_INPUTS_INTENT ?=
REVIEWED_HEAD_INPUTS_OUTPUT ?=
REVIEWED_HEAD_INPUTS_REPO_ROOT ?= .
REVIEWED_HEAD_INPUTS_GATE_ATTESTATION ?=
REVIEWED_HEAD_INPUTS_VALIDATE_ONLY ?= 1
RELEASE_CANDIDATE_SHA ?=
RELEASE_FAILURE_LEDGER ?= docs/releases/beta-release-failures.json
SELF_IMPROVE_MODEL_PATH ?=
SELF_IMPROVE_PROMPT_FILE ?=
SELF_IMPROVE_PROPOSAL_FILE ?=
SELF_IMPROVE_CONTRACT_FILE ?=
SELF_IMPROVE_ENVELOPE_FILE ?=
SELF_IMPROVE_WORKER_VALIDATE_ONLY ?= 0
SELF_IMPROVE_BASELINE_REF ?=
SELF_IMPROVE_REFERENCE_REF ?=
SELF_IMPROVE_TASK_FILE ?=
SELF_IMPROVE_MAX_ATTEMPTS ?= 2
SELF_IMPROVE_VALIDATE_ONLY ?= 0
SELF_IMPROVE_CONFIG_FILE ?=
AZURE_SELF_IMPROVE_MODEL_POLICY ?= config/self-improve/azure-model-selection-policy.json
AZURE_SELF_IMPROVE_MODEL_CATALOG ?= config/self-improve/azure-model-catalog-ci.json
AZURE_SELF_IMPROVE_EVIDENCE_FILE ?= .gludd/capability-evidence.json
AZURE_SELF_IMPROVE_REGISTRY_CACHE ?= .gludd/model-registry-cache
AZURE_SELF_IMPROVE_TASK_FILE ?= config/self-improve/catalog-truth.json
SELF_IMPROVE_CATALOG_LIVE ?= 0
SELF_IMPROVE_MULTIFILE_LIVE ?= 0
SELF_IMPROVE_FAILURE_CORPUS_FILE ?= config/self-improve/failure-corpus.json
SELF_IMPROVE_ACCEPTANCE_MATRIX_FILE ?= config/self-improve/acceptance-matrix.json
SELF_IMPROVE_ACCEPTANCE_MATRIX_MODEL_PATH ?=
SELF_IMPROVE_ACCEPTANCE_MATRIX_LIVE ?= 0
AZURE_SELF_IMPROVE_SUBSCRIPTION_ID ?=
AZURE_SELF_IMPROVE_RESOURCE_GROUP ?=
AZURE_SELF_IMPROVE_ACCOUNT ?=
AZURE_SELF_IMPROVE_SP_NAME ?=
ifneq (,$(findstring $$,$(value AZURE_SELF_IMPROVE_SUBSCRIPTION_ID)))
$(error AZURE_SELF_IMPROVE_SUBSCRIPTION_ID contains forbidden input)
endif
ifneq (,$(findstring $$,$(value AZURE_SELF_IMPROVE_RESOURCE_GROUP)))
$(error AZURE_SELF_IMPROVE_RESOURCE_GROUP contains forbidden input)
endif
ifneq (,$(findstring $$,$(value AZURE_SELF_IMPROVE_ACCOUNT)))
$(error AZURE_SELF_IMPROVE_ACCOUNT contains forbidden input)
endif
ifneq (,$(findstring $$,$(value AZURE_SELF_IMPROVE_SP_NAME)))
$(error AZURE_SELF_IMPROVE_SP_NAME contains forbidden input)
endif
override _GLUDD_AZURE_SELF_IMPROVE_SUBSCRIPTION_ID_RAW := $(value AZURE_SELF_IMPROVE_SUBSCRIPTION_ID)
override _GLUDD_AZURE_SELF_IMPROVE_RESOURCE_GROUP_RAW := $(value AZURE_SELF_IMPROVE_RESOURCE_GROUP)
override _GLUDD_AZURE_SELF_IMPROVE_ACCOUNT_RAW := $(value AZURE_SELF_IMPROVE_ACCOUNT)
override _GLUDD_AZURE_SELF_IMPROVE_SP_NAME_RAW := $(value AZURE_SELF_IMPROVE_SP_NAME)
export _GLUDD_AZURE_SELF_IMPROVE_SUBSCRIPTION_ID_RAW
export _GLUDD_AZURE_SELF_IMPROVE_RESOURCE_GROUP_RAW
export _GLUDD_AZURE_SELF_IMPROVE_ACCOUNT_RAW
export _GLUDD_AZURE_SELF_IMPROVE_SP_NAME_RAW
ifneq (,$(findstring $$,$(value AZURE_ACCELERATOR_SUBSCRIPTION_ID)))
$(error AZURE_ACCELERATOR_SUBSCRIPTION_ID contains forbidden input)
endif
ifneq (,$(findstring $$,$(value AZURE_ACCELERATOR_RESOURCE_GROUP)))
$(error AZURE_ACCELERATOR_RESOURCE_GROUP contains forbidden input)
endif
ifneq (,$(findstring $$,$(value AZURE_ACCELERATOR_SP_NAME)))
$(error AZURE_ACCELERATOR_SP_NAME contains forbidden input)
endif
ifneq (,$(findstring $$,$(value AZURE_ACCELERATOR_OPERATOR_AUTH)))
$(error AZURE_ACCELERATOR_OPERATOR_AUTH contains forbidden input)
endif
ifneq (,$(findstring $$,$(value AZURE_ACCELERATOR_PRINCIPAL_OBJECT_ID)))
$(error AZURE_ACCELERATOR_PRINCIPAL_OBJECT_ID contains forbidden input)
endif
ifneq (,$(findstring $$,$(value AZURE_ACCELERATOR_ROLE_APPLY_LIVE)))
$(error AZURE_ACCELERATOR_ROLE_APPLY_LIVE contains forbidden input)
endif
ifneq (,$(findstring $$,$(value AZURE_ACCELERATOR_LOCATION)))
$(error AZURE_ACCELERATOR_LOCATION contains forbidden input)
endif
ifneq (,$(findstring $$,$(value AZURE_CONTAINERAPP_ENVIRONMENT)))
$(error AZURE_CONTAINERAPP_ENVIRONMENT contains forbidden input)
endif
ifneq (,$(findstring $$,$(value AZURE_CONTAINERAPP_WORKLOAD_PROFILE_NAME)))
$(error AZURE_CONTAINERAPP_WORKLOAD_PROFILE_NAME contains forbidden input)
endif
ifneq (,$(findstring $$,$(value AZURE_CONTAINERAPP_WORKLOAD_PROFILE_TYPE)))
$(error AZURE_CONTAINERAPP_WORKLOAD_PROFILE_TYPE contains forbidden input)
endif
ifneq (,$(findstring $$,$(value AZURE_CONTAINERAPP_LOCATION)))
$(error AZURE_CONTAINERAPP_LOCATION contains forbidden input)
endif
override _GLUDD_AZURE_ACCELERATOR_SUBSCRIPTION_ID_RAW := $(value AZURE_ACCELERATOR_SUBSCRIPTION_ID)
override _GLUDD_AZURE_ACCELERATOR_RESOURCE_GROUP_RAW := $(value AZURE_ACCELERATOR_RESOURCE_GROUP)
override _GLUDD_AZURE_ACCELERATOR_SP_NAME_RAW := $(value AZURE_ACCELERATOR_SP_NAME)
override _GLUDD_AZURE_ACCELERATOR_OPERATOR_AUTH_RAW := $(value AZURE_ACCELERATOR_OPERATOR_AUTH)
override _GLUDD_AZURE_ACCELERATOR_PRINCIPAL_OBJECT_ID_RAW := $(value AZURE_ACCELERATOR_PRINCIPAL_OBJECT_ID)
override _GLUDD_AZURE_ACCELERATOR_ROLE_APPLY_LIVE_RAW := $(value AZURE_ACCELERATOR_ROLE_APPLY_LIVE)
override _GLUDD_AZURE_ACCELERATOR_LOCATION_RAW := $(value AZURE_ACCELERATOR_LOCATION)
override _GLUDD_AZURE_CONTAINERAPP_ENVIRONMENT_RAW := $(value AZURE_CONTAINERAPP_ENVIRONMENT)
override _GLUDD_AZURE_CONTAINERAPP_WORKLOAD_PROFILE_NAME_RAW := $(value AZURE_CONTAINERAPP_WORKLOAD_PROFILE_NAME)
override _GLUDD_AZURE_CONTAINERAPP_WORKLOAD_PROFILE_TYPE_RAW := $(value AZURE_CONTAINERAPP_WORKLOAD_PROFILE_TYPE)
override _GLUDD_AZURE_CONTAINERAPP_LOCATION_RAW := $(value AZURE_CONTAINERAPP_LOCATION)
export _GLUDD_AZURE_ACCELERATOR_SUBSCRIPTION_ID_RAW
export _GLUDD_AZURE_ACCELERATOR_RESOURCE_GROUP_RAW
export _GLUDD_AZURE_ACCELERATOR_SP_NAME_RAW
export _GLUDD_AZURE_ACCELERATOR_OPERATOR_AUTH_RAW
export _GLUDD_AZURE_ACCELERATOR_PRINCIPAL_OBJECT_ID_RAW
export _GLUDD_AZURE_ACCELERATOR_ROLE_APPLY_LIVE_RAW
export _GLUDD_AZURE_ACCELERATOR_LOCATION_RAW
export _GLUDD_AZURE_CONTAINERAPP_ENVIRONMENT_RAW
export _GLUDD_AZURE_CONTAINERAPP_WORKLOAD_PROFILE_NAME_RAW
export _GLUDD_AZURE_CONTAINERAPP_WORKLOAD_PROFILE_TYPE_RAW
export _GLUDD_AZURE_CONTAINERAPP_LOCATION_RAW
RECONCILE_QUIET_PROGRESS ?= 0
MARKDOWN_FILES ?=
RUMDL_CONFIG ?=
MARKDOWNLINT_CONFIG ?=
CSS_FILES ?=
ESLINT_CSS_CONFIG ?= config/eslint-css.config.mjs
DOCSTRING_FILES ?=
FILE_LINE_LIMIT_POLICY ?= config/file_line_limits.json
FILE_LINE_LIMIT_STAGED ?= 0
DUPLICATE_CODE_CONFIG ?= config/duplicate_code.json
DUPLICATE_CODE_ENGINE ?= .opencode/node_modules/.bin/jscpd
# The clone owner forwards this tool as a CLI argument. It is not pytest state.
# Keep native recursive Make inheritance for owning targets; only pytest
# consumers omit this exact override record, preserving other overrides/flags.
unexport DUPLICATE_CODE_ENGINE
override _GLUDD_DUPLICATE_CODE_ENGINE_RAW := $(value DUPLICATE_CODE_ENGINE)
ifneq (,$(findstring $$,$(_GLUDD_DUPLICATE_CODE_ENGINE_RAW)))
$(error unsupported duplicate-code engine literal: dollar-bearing input)
endif
ifneq (,$(findstring `,$(_GLUDD_DUPLICATE_CODE_ENGINE_RAW)))
$(error unsupported duplicate-code engine literal: backtick-bearing input)
endif
ifneq (,$(findstring ",$(_GLUDD_DUPLICATE_CODE_ENGINE_RAW)))
$(error unsupported duplicate-code engine literal: double-quote-bearing input)
endif
ifneq (,$(findstring \,$(_GLUDD_DUPLICATE_CODE_ENGINE_RAW)))
$(error unsupported duplicate-code engine literal: backslash-bearing input)
endif
# Single-quote the verified raw path; Make and the recipe shell must not
# reinterpret it. Embedded apostrophes are data through the native POSIX form.
override _GLUDD_DUPLICATE_CODE_ENGINE_ARG := $(subst ','"'"',$(_GLUDD_DUPLICATE_CODE_ENGINE_RAW))
ifeq ($(origin DUPLICATE_CODE_ENGINE),command line)
_GLUDD_DUPLICATE_CODE_EMPTY :=
_GLUDD_DUPLICATE_CODE_SPACE := $(_GLUDD_DUPLICATE_CODE_EMPTY) $(_GLUDD_DUPLICATE_CODE_EMPTY)
_GLUDD_DUPLICATE_CODE_ESCAPED := $(subst $(_GLUDD_DUPLICATE_CODE_SPACE),\$(_GLUDD_DUPLICATE_CODE_SPACE),$(_GLUDD_DUPLICATE_CODE_ENGINE_RAW))
_GLUDD_DUPLICATE_CODE_SPACE_TOKEN := __GLUDD_DUPLICATE_CODE_ESCAPED_SPACE__
ifneq (,$(findstring $(_GLUDD_DUPLICATE_CODE_SPACE_TOKEN),$(MAKEOVERRIDES)))
$(error unsupported duplicate-code Make override encoding: reserved space token)
endif
# Protect escaped spaces before identifying whole records. Never reconstruct
# other overrides from whitespace-split words, or remove matching text in them.
_GLUDD_DUPLICATE_CODE_OVERRIDES := $(subst \$(_GLUDD_DUPLICATE_CODE_SPACE),$(_GLUDD_DUPLICATE_CODE_SPACE_TOKEN),$(MAKEOVERRIDES))
_GLUDD_DUPLICATE_CODE_RECORD := DUPLICATE_CODE_ENGINE=$(subst \$(_GLUDD_DUPLICATE_CODE_SPACE),$(_GLUDD_DUPLICATE_CODE_SPACE_TOKEN),$(_GLUDD_DUPLICATE_CODE_ESCAPED))
ifneq ($(filter DUPLICATE_CODE_ENGINE=%,$(_GLUDD_DUPLICATE_CODE_OVERRIDES)),$(_GLUDD_DUPLICATE_CODE_RECORD))
$(error unsupported duplicate-code Make override encoding: expected one exact engine record)
endif
test-count collect-check test-files coverage-files: override MAKEOVERRIDES := $(subst $(_GLUDD_DUPLICATE_CODE_SPACE_TOKEN),\$(_GLUDD_DUPLICATE_CODE_SPACE),$(subst $(_GLUDD_DUPLICATE_CODE_SPACE)$(_GLUDD_DUPLICATE_CODE_RECORD)$(_GLUDD_DUPLICATE_CODE_SPACE),$(_GLUDD_DUPLICATE_CODE_SPACE),$(_GLUDD_DUPLICATE_CODE_SPACE)$(_GLUDD_DUPLICATE_CODE_OVERRIDES)$(_GLUDD_DUPLICATE_CODE_SPACE)))
endif
DUPLICATE_CODE_SOURCE ?= staged
DUPLICATE_CODE_BASE_REF ?= HEAD
DUPLICATE_CODE_CURRENT_REF ?= HEAD
DUPLICATE_CODE_VALIDATE_ONLY ?= 0
MAKEFILE_SPLIT_APPLY ?= 0
GATE_REFRESH_VALIDATE_ONLY ?= 0
GATE_RUN_LOCK ?= .gate-logs/gate-run.lock
INSTALL_WORKFLOW_HOOK_VALIDATE_ONLY ?= 0
COVERAGE_TESTFILES ?=
COVERAGE_CONFIG ?= config/coverage_gate_runtime.ini
COVERAGE_REPORT ?= .gate-logs/coverage-files.json
COVERAGE_AGGREGATE_MIN ?= 85
COVERAGE_PER_FILE_MIN ?= 75
OBSERVED_ROOT ?= .gate-logs/observed
OBSERVED_HEARTBEAT_SECS ?= 30
OBSERVED_STALE_SECS ?= 90
OBSERVED_QUIET_SECS ?= 900
OBSERVED_MAX_SECS ?= 3600
OBSERVED_TAIL_LINES ?= 80
OBSERVED_RETAIN_RUNS ?= 20
OBSERVED_LABEL ?=
RUN_ID ?=
CLEAN_VALIDATE_ONLY ?= 0
CLEAN_WORKTREE_VENVS_VALIDATE_ONLY ?= 0
DISK_MIN_FREE_GIB ?= 8
# Preserve a capable caller terminal; supply a stable terminfo fallback when
# workers or CI provide an empty, explicitly limited, or uninstalled TERM value.
_TERMINFO_OK := $(shell infocmp >/dev/null 2>&1 && printf yes)
ifeq (,$(filter-out dumb unknown,$(strip $(TERM))))
override TERM := xterm-256color
else ifeq (,$(_TERMINFO_OK))
override TERM := xterm-256color
endif
export TERM
# SSH deploy keys are credentials and must live outside the repository.
# Override with `make ... SSH_KEY=/path/to/key` for another external key.
SSH_KEY ?= $(HOME)/.ssh/sandboxcom_gludd_rsa

_MULTIWORD_VALUE_GOALS := \
    copy-file feature-done feature-start git-add git-branch git-checkout git-cherry-pick-list \
    git-commit git-commit-file git-commit-files git-merge git-reset git-restore git-tag-move \
    git-tag-push lint-files lint-fix-files lint-markdown lint-docstrings release-cut release-deploy release-promote release-upload-assets \
    replace-all-text replace-lines replace-text search ship-commit test-and-commit test-ci-shards-parallel \
    test-ci-shards-parallel-bg test-files ci-shards-log-context
_FIRST_MAKE_GOAL := $(firstword $(MAKECMDGOALS))
_EXTRA_MAKE_GOALS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
ifneq (,$(filter $(_FIRST_MAKE_GOAL),$(_MULTIWORD_VALUE_GOALS)))
ifneq (,$(_EXTRA_MAKE_GOALS))
$(error Quote multi-word variable values for $(_FIRST_MAKE_GOAL); stray make goals: $(_EXTRA_MAKE_GOALS))
endif
endif

PYTHON := python3
override SYSTEM_PYTHON := /usr/bin/python3
# Execution stays real for every explicit goal and prerequisite. UV_NO_SYNC
# above prevents implicit environment changes without silencing owners/guards.
UV := uv
PROJECT_SRC := src/general_ludd
TESTS_DIR := tests
# Export xdist worker-count overrides so command-line NPROC=/GLUDD_XDIST_WORKERS= reach
# the adaptive_test.py subprocess used by the gate.
export NPROC
export GLUDD_XDIST_WORKERS
# Worker count: env GLUDD_XDIST_WORKERS overrides (CI sets it so the suite isn't run on a
# single worker — a 4-vCPU runner's cpu//4=1 made the gate sit ~38min near the
# 40min wall). Local default stays cpu//4. Accepts an int or "auto".
_XDIST_WORKERS := $(shell if [ -n "$(GLUDD_XDIST_WORKERS)" ]; then echo "$(GLUDD_XDIST_WORKERS)"; else n=$$(getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4); v=$$((n / 4)); if [ "$$v" -lt 1 ]; then v=1; fi; echo "$$v"; fi)
ifeq ($(_XDIST_WORKERS),0)
_XD :=
else
_XD := -n $(_XDIST_WORKERS) --dist loadgroup
endif
PYTEST_VERBOSITY ?= -v

.PHONY: \
        backlog-audit test-files-cprofile \
        init sync uv-cache-path migrate-up relock node-deps-sync node-deps-relock node-deps-audit check-ansible-base-image refresh-ansible-base-image install-pip lint lint-css lint-files lint-markdown lint-docstrings lint-fix check-file-line-limits check-duplicate-code split-makefile-layout test test-unit test-unit-shards test-ci-dual-track-local test-specific test-specific-pyver test-files test-count test-integration test-e2e \
         test-guardrails test-scripts test-db test-live-zai test-tui-daemon test-batch test-bg test-bg-runner \
         test-games test-multi-model-pipeline test-local-model-pipeline test-project-type-pipeline game-audit gen-mcp-tools gen-mcp-tool-ref mcp-docs-check \
        typecheck _precommit-mypy setup-dirs setup-venv clean healthcheck \
        bootstrap skeleton version check-uv check-pytest \
        ansible-syntax ansible-lint-playbooks ansible-collection-test playbook-list \
        git-status git-init git-add git-commit git-log git-diff git-reset \
        git-branch git-checkout git-merge git-staged git-stash git-stash-list git-stash-show git-stash-drop git-stash-pop \
        git-merge-abort resolve-development-conflicts git-rebase-abort git-rebase-continue git-rebase-skip git-uncommit-last git-reset-hard git-cherry-pick git-cherry-pick-list \
        submodule-init submodule-update submodule-status submodule-pin \
        repo-status repo-diff repo-staged repo-log \
        feature-start feature-done test-and-commit preflight \
        agent-worktree agent-worktree-base agent-worktree-retain agent-merge agent-merge-dev agent-cleanup agent-worktree-list worktree-commit-changes worktree-cleanup-merged merge-commit-nv \
        agent-worktree-dev agent-merge-dev \
        self-improve-local-proposal azure-self-improve-auth-args azure-self-improve-live-proof azure-accelerator-role-apply azure-accelerator-role-args azure-accelerator-role-update-args azure-accelerator-auth-args azure-accelerator-auth-store azure-containerapp-environment-bootstrap-args azure-accelerator-auth-check azure-containerapp-preflight azure-containerapp-terraform-phase azure-containerapp-environment-guard azure-containerapp-live-proof test-azure-containerapp-coverage test-self-improve test-self-improve-all test-self-improve-acceptance-matrix test-self-improve-private-policy \
          development-push development-merge-forward development-merge-forward-batch development-merge-to-master development-start development-status require-sandboxcom-ssh-key workstream-register workstream-unregister wt-prune-safe \
        git-commit-no-verify git-amend-msg \
_commit-lock-acquire _commit-docstring-guard check-clean-tree worktree-state all-worktree-state main-worktree-state worktree-guard main-worktree-guard \
        release-worktree-guard status-claim-guard workflow-state workflow-gate commit-ready gha-ready merge-ready ship-commit-files remove-workspace-file-b64 \
        molecule-version molecule-test molecule-test-all \
        collection-roles collection-modules molecule-scenarios \
        test-binary-re test-radio test-os-expert test-e2e-test-gen test-language test-language-expert test-collections \
          test-e2e-azure test-e2e-azure-provision game-reference-preflight test-e2e-games-provision test-e2e-providers \
          test-e2e-aws test-e2e-gcp test-e2e-runpod \
         e2e-audit-azure e2e-latest-log \
        molecule-test-binary-re molecule-test-radio molecule-test-os-expert molecule-test-e2e-test-gen molecule-test-language \
        move-ansible-roles \
        container-build container-run container-push \
         file-executable build-executable deb-package deb-install-deps rpm-package macos-dmg windows-installer release-artifacts dist-clean bundle-binaries bundle-ripgrep \
        sast sast-summary sbom pip-audit security security-backlog-gate \
        audit-messages qa validate collect-check pre-commit-check check-gate-failure-promotions integration-admission coverage-files observed-status observed-tail gate gate-refresh gate-lite smoke install-hooks install-workflow-hook feature-spec-inventory check-generated-artifact-hygiene \
        status-snapshot audit-evidence deps-audit core-dependency-ownership-refresh dogfood-features ruff-audit check-make-help \
        skill-install skill-list bootstrap-skills scan-tool-usage \
         scan-secrets scan-secrets-baseline clean-untracked clean-hooks clean-plugins \
         secrets-scrub secrets-scan secrets-baseline secrets-baseline-check security-audit clean-artifacts health-check \
        git-remote-sandboxcom git-push-sandboxcom git-pull-sandboxcom git-fetch-sandboxcom \
        git-add-all help grep scan-secrets-fresh untrack \
         git-tracked-keys git-ls-tracked git-history-file dist-path-check git-is-ancestor git-revlist-count git-patch-equivalence branches-unmerged-development branch-reconciliation-inventory branch-reconciliation-summary branch-reconciliation-completed-backlog check-git-hygiene git-object-store-reclaim cache-disk cache-clean disk-user-caches cache-resource-inventory cache-resource-remove rm-files commit-and-ship commit-and-ship-push compute-model-hashes \
        molecule-clean plan ps ps-gludd kill-stale terminate-project-process-tree reap-stale-collection-locks reap-orphan-pytest kill-gate-force \
        gate-async gate-status floor-plan gated-merge ship-async write-gate-safe-hook \
        repo-visibility \
         watchdog-read watchdog-start watchdog-status watchdog-stop agent-watchdog-stop watchdog-log \
        task-watchdog-start task-watchdog-stop task-watchdog-status task-watchdog-log task \
        check-readme-status check-types check-types-baseline check-plugin-versions check-plugin-versions-quiet \
         check-plugin-liveness check-plugin-health list-plugins write-plugin-manifest codemod-lean-enforcement-plugins restart-opencode disengage-enforcement disengage-next reload-enforcement \
        rearm-enforcement enforcement-status \
        hot-reload-plugins hot-reload-status hot-reload-clean check-plugin-restart-needed \
          verify-release-artifact verify-release-completeness git-tag-rm git-tag-delete git-tag-move release-branch-new release-cut release-recut release-create release-delete \
         release-upload-assets git-restore-from release-deploy release-promote \
        build-sandbox-image verify-sandbox-image clean-sandbox-images \
        sandbox-state-dir sandbox-state-list sandbox-state-clean \
        vm-image-build vm-image-list vm-image-clean \
        verify-feature-claims audit-coverage gate-audit coverage-json \
        tf-cache-setup tf-init tf-init-local tf-validate tf-cache-warm tf-versions-check tf-clean \
         deck deck-serve deck-preview deck-data deck-honesty vendor-presentation-assets presentation-browser-install presentation-browser-install-deps presentation-browser-test presentation-safari-test presentation-pages-probe \
        script-count strip-enforce-stop test-hooks-live test-hook-runtime e2e-setup-test-project test-opencode-e2e test-opencode-e2e-hour \
        verify-enforcement \
    ci-view ci-rerun ci-recover-runner-acquisition ci-failure-status ci-failure-repair ci-failure-push-guard ci-trigger ci-active ci-job-log ci-job-failure-context ci-artifact-download ci-artifact-context ci-pyinstaller-warning-audit ci-coverage-artifact-audit ci-coverage-gap-plan ci-shards-log-context \
        ci-busy-check ci-safe-push pre-push-check push-guarded ci-await \
log-agent-result disk-guard disk-check disk-cleanup-preflight clean-stale-resource-namespaces check-disk check-disk-classification check-system-load disk tmp-gludd-usage tmp-gludd-clean-ci-shards tmp-gludd-clean-ci-shards-now tmp-gludd-clean-orphan-worktrees-now \
        tmp-gludd-worktree-usage clean-worktree-venvs clean-worktree-caches \
        searx-up searx-down searx-test searx-molecule searx-start searx-stop searx-status searx-install \
        networking-role-lint networking-role-syntax test-scapy-adapter networking-validate \
        networking-healthcheck \
        install-bats test-install check-subagent-guards verify-plugin-manifest \
         check-task-ledger \
         check-task-integrity check-make-target-contract check-dispatch-dedup active-work-status \
         codex-stop-guard \
         codex-stop-confirm \
         test-service-discovery service-discover service-catalog \
         subagent-init subagent-cleanup \
         chat chat-eval test-chat \
git-tag-delete git-tag-move release-deploy append-text write-text-b64 replace-text-b64 mkdir-p replace-lines _no-raw-git-guard _no-bypass-guard _pre-commit-stage-guard _merge-strategy-guard _stash-leak-guard \
          _force-push-audit _recursive-merge-guard _commit-msg-audit \
          check-spec-enforcement-coverage check-structural-test-fragility lint-specs triage-failures audit-spec-completeness \
          git-push-committed-head-nv ci-trigger-committed-head ci-push-committed-head provider-smoke local-accelerator-inventory mac-unified-memory-smoke gpu-hardware-smoke check-no-prompt-prone-edit-tools add-target edit-target edit-makefile-target validate-makefile \
         build-llamacpp-tools

help:
	@printf '%s\n' \
		"Usage: make [target]" \
		"" \
		"  --- Setup ---" \
		"  init                  Set up project (dirs + deps)" \
		"  sync                  Atomically sync an explicit locked dependency profile set (DEPENDENCY_PROFILE_*)" \
		"  relock                Check/regenerate native locks; optional DEPENDENCY_PROFILE_RELOCK_PROFILE" \
		"  acquire-pinned-uv      Acquire hosted uv in a private native leaf (PINNED_UV_VALIDATE_ONLY=1 plans)" \
		"  inspect-dependency-runtime Read-only registered runtime/profile metadata (DEPENDENCY_RUNTIME_*, plan by default)" \
		"  sync-ansible-native-format Native locked/inexact dev tooling sync (ANSIBLE_FORMAT_SYNC_*, dry-plan by default)" \
		"  uv-cache-path         Print the sandbox-writable Gludd uv cache path" \
		"  migrate-up            Upgrade an explicit database URL to a revision (MIGRATE_DATABASE_URL, MIGRATE_REVISION)" \
		"  sync-llama-cpp        Sync locked local-inference extra (SYNC_LLAMA_CPP_VALIDATE_ONLY=0|1)" \
		"  test-local-model-inference  Locked optional-runtime smoke (LOCAL_MODEL_INFERENCE_MODEL_PATH, LOCAL_MODEL_INFERENCE_VALIDATE_ONLY=0|1)" \
		"  clean-e2e-small-model       Remove only the reproducible /tmp GGUF materialization (E2E_SMALL_MODEL_CLEAN_VALIDATE_ONLY=0|1)" \
		"  clean-hf-cache             Diagnose/reclaim Gludd-owned unleased models (CLEAN_HF_CACHE_ROOT, CLEAN_HF_CACHE_REQUIRED_BYTES, CLEAN_HF_CACHE_VALIDATE_ONLY=0|1)" \
		"  validate-ansible-runtime-boundary  Validate split core/controller/managed-host artifacts" \
		"  build-ansible-execution-environment  Build the locked controller EE (ANSIBLE_EE_*)" \
		"  prepare-ansible-execution-environment-context Prepare one reviewed native context without image build (ANSIBLE_EE_*)" \
		"  verify-ansible-execution-environment Verify one digest-addressed controller EE (ANSIBLE_EE_*)" \
		"  check-collection-python-boundary Enforce exact/strict-zero collection migration inventory" \
		"  check-resource-ownership Enforce exact application acquisition-to-teardown evidence (RESOURCE_OWNERSHIP_*)" \
		"  update-ansible-runtime-lock Refresh deterministic EE input hashes" \
		"  stage-ansible-runtime-collections Prepare/replay explicit native Galaxy artifacts (ANSIBLE_COLLECTION_STAGE_*, validate-only by default)" \
		"  resolve-ansible-runtime-requirements Compile complete staged EE inputs (ANSIBLE_EE_COLLECTION_ROOT, ANSIBLE_EE_RESOLUTION_OUTPUT, ANSIBLE_EE_RESOLUTION_VALIDATE_ONLY=1)" \
		"  check-ansible-base-image Prove the exact EE base manifest is still served (ANSIBLE_EE_BASE_IMAGE_CHECK_VALIDATE_ONLY=0|1)" \
		"  inspect-ansible-searxng-source Read eleven exact frozen source/default-build inputs (ANSIBLE_SEARXNG_SOURCE_INSPECT_VALIDATE_ONLY=0|1)" \
		"  refresh-ansible-base-image Resolve, verify, and atomically pin the supported EE base (ANSIBLE_EE_BASE_IMAGE_REFRESH_VALIDATE_ONLY=0|1)" \
		"  update-collection-python-boundary-inventory Refresh exact legacy migration inventory" \
		"  deps-audit            Fail-closed Python dependency truth audit" \
		"  core-dependency-ownership-refresh  Reconcile direct-core ownership (CORE_DEPENDENCY_OWNERSHIP_REFRESH_VALIDATE_ONLY=0|1)" \
		"  node-deps-sync        Install locked Node deps (NODE_DEPS_VALIDATE_ONLY, NODE_DEPS_NPM_USERCONFIG, NODE_DEPS_NPM_CACHE, NODE_DEPS_NPM_REGISTRY, NODE_DEPS_NPM_UPDATE_NOTIFIER=true|false)" \
		"  node-deps-relock      Regenerate Node lock (NODE_DEPS_VALIDATE_ONLY, NODE_DEPS_NPM_USERCONFIG, NODE_DEPS_NPM_CACHE, NODE_DEPS_NPM_REGISTRY, NODE_DEPS_NPM_UPDATE_NOTIFIER=true|false)" \
		"  node-deps-audit       Audit locked Node deps (NODE_DEPS_NPM_UPDATE_NOTIFIER=true|false plus NODE_DEPS_AUDIT_LEVEL=low|moderate|high|critical)" \
		"  freellmapi-upstream-admission  Validate/refresh the non-runnable upstream candidate (FREELLMAPI_ADMISSION_TAG, FREELLMAPI_ADMISSION_COMMIT, FREELLMAPI_ADMISSION_LIVE=0|1, FREELLMAPI_ADMISSION_OUTPUT)" \
		"  freellmapi-upstream-build  Validate/run the exact-source upstream suite (FREELLMAPI_BUILD_LIVE=0|1, FREELLMAPI_BUILD_CANDIDATE, FREELLMAPI_BUILD_PLAN, FREELLMAPI_BUILD_REPORT)" \
		"  freellmapi-three-arm-replay  Build/validate/replay exact v0.11.1 without promotion (FREELLMAPI_THREE_ARM_MODE, FREELLMAPI_THREE_ARM_CANDIDATE, FREELLMAPI_THREE_ARM_PLAN, FREELLMAPI_THREE_ARM_CORPUS, FREELLMAPI_THREE_ARM_REPORT)" \
		"  bootstrap             init + lint + test + healthcheck" \
		"  install-hooks         Install pre-commit hooks (secrets, lint, collect)" \
		"  install-workflow-hook Validate/install the tracked GitHub workflow YAML hook (INSTALL_WORKFLOW_HOOK_VALIDATE_ONLY)" \
		"  install-bats          Install bats-core via Homebrew" \
		"" \
		"  --- Quality ---" \
		"  gate-all                full CI-matching gate: all unit + integration + e2e + molecule tests" \
		"  gate-full               full gate matching CI: gate-refresh + integration + e2e + molecule" \
		"  gate-release-phases     bounded integration + e2e + molecule release phases (GATE_RELEASE_*)" \
		"  test-atomic-validate    verify atomic target creation with tempfile validation" \
		"  gate-check              Run gate check" \
		"  lint                  Run ruff linter" \
		"  lint-css              Run locked ESLint CSS checks (CSS_FILES, ESLINT_CSS_CONFIG)" \
		"  lint-python           Run the canonical Python Ruff gate (application + tests)" \
		"  lint-make             Run duplicate-target, parity, and Make dry-run validation" \
		"  lint-files            Run ruff linter on FILES only" \
		"  check-molecule-yaml   Complete native inputs (MOLECULE_YAML_LINT_TIMEOUT_SECONDS=1..120, MOLECULE_YAML_VALIDATE_ONLY=0|1)" \
		"  lint-markdown         Run locked Rumdl checks (MARKDOWN_FILES, RUMDL_CONFIG; MARKDOWNLINT_CONFIG alias)" \
		"  lint-docstrings       Run locked Ruff docstring rules on DOCSTRING_FILES" \
		"  check-file-line-limits  Enforce <2500 lines on tracked or staged content (FILE_LINE_LIMIT_POLICY, FILE_LINE_LIMIT_STAGED=0|1)" \
		"  check-duplicate-code  Reject new staged/committed production clones with locked jscpd (DUPLICATE_CODE_*)" \
		"  split-makefile-layout  Validate/apply the ordered make/*.mk layout (MAKEFILE_SPLIT_APPLY=0|1)" \
		"  vendor-presentation-assets  Validate/refresh pinned Reveal.js assets (PRESENTATION_VENDOR_VALIDATE_ONLY=0|1)" \
		"  presentation-browser-install Check/install pinned Chromium + WebKit (PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY=0|1)" \
		"  presentation-browser-install-deps Validate/install Playwright browser system libraries (PRESENTATION_BROWSER_DEPS_VALIDATE_ONLY=0|1)" \
		"  presentation-browser-test   Validate/run bounded Chromium + WebKit acceptance (PRESENTATION_BROWSER_VALIDATE_ONLY=0|1); optional Chromium JS coverage (PRESENTATION_BROWSER_JS_COVERAGE=0|1)" \
		"  presentation-safari-test    Validate/run bounded native Safari smoke (PRESENTATION_SAFARI_VALIDATE_ONLY=0|1)" \
		"  presentation-pages-probe    Compare the public Pages artifact with an expected exact SHA" \
		"  lint-fix              Run ruff with auto-fix" \
		"  lint-fix-files        Run ruff auto-fix on FILES only" \
		"  typecheck             Run mypy" \
		"  typecheck-scope       Run strict mypy on explicit FILES without unrelated override noise" \
		"  check-types           Flag Any usage in Python annotations (tight types)" \
		"  check-types-baseline  Same scan, tolerating config/type_any_baseline.txt" \
		"  healthcheck           Verify imports work" \
		"  backlog-audit         Observed checked-task audit (BACKLOG_AUDIT_VALIDATE_ONLY, BACKLOG_AUDIT_COLLECT, BACKLOG_AUDIT_VERDICTS, OBSERVED_*)" \
		"  qa                    Run lint + typecheck + test + healthcheck" \
		"  validate              Full validation (lint + typecheck + test + ansible + healthcheck)" \
		"  add-target            Add a new Makefile target with auto-categorization" \
		"  edit-target           Edit an existing Makefile target recipe" \
		"  edit-makefile-target  Edit a Makefile target definition via a file" \
		"  validate-makefile     Validate Makefile targets for duplicates" \
		"  gate                  Full gate: GATE_TEST_PROFILE=native|release (release uses canonical CPython3.11/-W error/one-worker evidence); GATE_EXACT_SHA_RESUME=0|1, GATE_MAX_FILES_PER_BATCH=1..32, GATE_BATCH_WORKERS=1..2; unchanged 85/75 floors" \
		"  gate-admission-config Safely validate GATE_TEST_PROFILE=native|release and bounded resume/batch/worker policy without running a gate" \
		"  pants-launcher-bootstrap Acquire/verify the exact project-local Pants launcher (PANTS_LAUNCHER_*)" \
		"  pants-unit-cache-lane Pants hermetic cross-commit pure-unit lane (PANTS_UNIT_CACHE_*)" \
		"  pants-unit-cache-nightly Compare cached and forced-fresh nodes/outcomes/coverage (PANTS_UNIT_CACHE_*)" \
		"  gate-background           Launch one identity-tracked gate in an isolated session (forwards GATE_TEST_PROFILE and GATE_* policy)" \
		"  gate-resume             Authenticate current-state/policy before reuse; GATE_BACKGROUND_VALIDATE_ONLY=1 plans without writes" \
		"  gate-background-observed  Launch the selected gate profile and keep its automation owner alive while polling" \
		"  gate-refresh          Refresh fast phases; stream fallback test node IDs (GATE_REFRESH_VALIDATE_ONLY=0|1)" \
		"  gate-lite             Local validation (lint+typecheck+collect+smoke+unit@2w); no OOM" \
		"  gate-audit            Gate + coverage audit (85% per-file threshold)" \
		"  coverage-files        Observable targeted coverage (COVERAGE_* plus OBSERVED_ROOT, OBSERVED_HEARTBEAT_SECS, OBSERVED_QUIET_SECS, OBSERVED_MAX_SECS, OBSERVED_RETAIN_RUNS)" \
		"  observed-status       Print current/exact retained command status (OBSERVED_LABEL, RUN_ID, OBSERVED_ROOT, OBSERVED_STALE_SECS)" \
		"  observed-tail         Print bounded current/exact retained log tail (OBSERVED_LABEL, RUN_ID, OBSERVED_ROOT, OBSERVED_TAIL_LINES)" \
		"  run-watched           Observe one bounded command with quoted child arguments (CMD, OBSERVED_LABEL, RUN_ID, STALL_SECS, MAX_SECS, LOG, OBSERVED_ROOT, OBSERVED_HEARTBEAT_SECS, OBSERVED_RETAIN_RUNS)" \
		"  runtime-startup-diagnostic Plan a fixed serial collection diagnostic (RUNTIME_DIAGNOSTIC_*, OBSERVED_*, RUN_ID, LOG, WORK_ITEM_ID)" \
		"  runtime-startup-report Read one exact retained/archive startup run; never launches work (RUNTIME_DIAGNOSTIC_*, OBSERVED_ROOT, OBSERVED_LABEL, RUN_ID)" \
		"  runtime-startup-archive Plan an exclusive external raw startup archive (RUNTIME_DIAGNOSTIC_*, OBSERVED_ROOT, OBSERVED_LABEL, RUN_ID)" \
		"  gate-async            Launch gate detached (non-blocking); writes .gate-status" \
		"  gate-status           Print current .gate-status (RUNNING/PASS/FAIL)" \
		"  gate-tail             Print a bounded latest gate-log snapshot (GATE_TAIL_LINES=80)" \
		"  gate-lite-tail        Print a bounded latest gate-lite-log snapshot (GATE_TAIL_LINES=80)" \
		"  gate-progress         Estimate gate completion percent and ETA from historical logs (GATE_PROGRESS_MAX_HISTORICAL=10)" \
		"  triage-failures       Incrementally group streamed failures (LOG, TRIAGE_STATE, TRIAGE_FORMAT)" \
		"  collect-check         Fast collection-error gate" \
		"  pre-commit-check      Fast lint + collection + typecheck commit preflight" \
		"  check-gate-failure-promotions  Validate owned fast-admission failure nodes" \
		"  integration-admission  Fail-fast feature-branch checks before the full gate" \
		"  test-nodeids          Print bounded pytest node-id slice (START/LIMIT/TESTPATH)" \
		"  test-xdist-trace      Run pytest with durable xdist worker/node/resource trace (LOG, TESTPATH, PYTEST_ARGS, RUN_ID)" \
		"  test-xdist-trace-summary  Summarize one durable trace/run (LOG, RUN_ID)" \
		"  preflight             Preflight quality gate (coverage, lint, mypy, templates, etc.)" \
		"  check-make-help       Verify every public Makefile target is listed by make help" \
		"  codemod-lean-enforcement-plugins Extract bulky enforcement implementations from counted plugin entrypoints" \
		"  check-no-prompt-prone-edit-tools  Enforce make-target-only edit workflow" \
		"  codex-system-skill-read  Print a codex system skill's SKILL.md (SKILL=name [CODEX_SKILLS_ROOT=path])" \
		"  fix-init-drift        Fix __init__.py drift: docstrings, empty namespace inits, unsorted __all__" \
		"  fix-docs-drift        Fix mechanical markdown drift: whitespace, fences, stale audit links" \
		"  report-docs-drift     Enumerate hand-fixable markdown issues (tables, tabs, headers, links)" \
		"  feature-spec-inventory  Inventory all Gludd feature specs + OpenCode behavioral specs (FORMAT=human|json)" \
		"  migrate-test-env-writes  Rewrite test environment mutations to the guarded helper" \
		"  write-text-b64        Write FILE from base64 TEXT_B64 without shell quoting loss" \
		"  replace-text-b64      Exact old/new base64 replacement via scripts/replace_text.py" \
		"  mkdir-p               Create an allowed workspace or /tmp/gludd-* directory" \
		"  replace-lines         Replace an allowed file line range from NEW_FILE" \
		"  worktree-state        Emit path-qualified current git worktree state as JSON" \
		"  verify-state          Read working tree, HEAD, remote and exact-SHA CI with the selected project runtime; does not install dependencies" \
		"  all-worktree-state    Emit path-qualified state for every registered worktree" \
		"  main-worktree-state   Emit canonical main checkout state as JSON" \
		"  worktree-guard        Fail if the current worktree is dirty" \
		"  main-worktree-guard   Fail if /Users/shawnwilson/gludd is dirty" \
		"  release-worktree-guard  Emit release evidence only when current and main worktrees are clean" \
		"  status-claim-guard    Emit clean tokens only when current and main worktrees are clean" \
		"  workflow-state        Emit local/remote/GHA git state-machine evidence as JSON" \
		"  workflow-gate         Fail if local workflow state is unsafe for release evidence" \
		"  commit-ready          Fail if current work is not clean enough to be committed/tested" \
		"  gha-ready             Fail if remote CI would not run the current committed HEAD" \
		"  merge-ready           Fail if development cannot merge to master without topology repair" \
		"  codemod-lean-enforcement-plugins  Slim counted enforcement plugin entrypoints" \
		"  sast                  Run bandit SAST" \
		"  sbom                  Generate CycloneDX SBOM" \
		"  pip-audit             Audit dependencies for vulnerabilities" \
		"  pip-audit-gate        Fail on every non-adjudicated Python advisory" \
		"  security              Full security: sast + sbom + Python/Node dependency audits" \
		"  test-unit             Unit tests only" \
		"  test-unit-shards      Unit tests in bounded serial shards (SHARDS=12 SHARD=1)" \
		"  test-ci-dual-track-local  Canonical local all-shard evidence (DUAL_TRACK_LOCAL_VALIDATE_ONLY, PYTEST_ARGS, MAX_FILES_PER_BATCH, DUAL_TRACK_RESUME, DUAL_TRACK_WATCHDOG_OWNED_GATE)" \
		"  test-integration      Integration tests" \
		"  integration-health   Run the observable integration gate with isolated temp paths" \
		"  test-e2e              End-to-end tests" \
		"  test-e2e-azure        Azure E2E — env-pointer (CI-friendly)" \
		"  test-e2e-azure-provision  Azure full-provision E2E (opt-in, costly)" \
		"  test-e2e-azure-provision-sourced  Source AZURE_E2E_ENV_FILE, then provision (AZURE_E2E_VALIDATE_ONLY=0|1)" \
		"  test-e2e-aws          AWS E2E — env-pointer (CI-friendly)" \
		"  test-e2e-gcp          GCP E2E — env-pointer (CI-friendly)" \
		"  test-e2e-runpod       RunPod E2E — env-pointer (CI-friendly)" \
		"  test-e2e-providers    All E2E provider tests" \
		"  test-e2e-games        Game generation E2E — AI generates games, compares frames (no Azure provision)" \
		"  test-e2e-games-local  Game unit tests only — video compare, game gen, no Azure needed" \
		"  test-e2e-games-local-model  Hermetic/managed/external game E2E (LOCAL_MODEL_E2E_MODE, LOCAL_MODEL_PATH, LOCAL_MODEL_BASE_URL, LOCAL_MODEL_NAME, LOCAL_MODEL_KEY, LOCAL_MODEL_GAME, PYTEST_ARGS)" \
		"  test-e2e-game-pipeline  Full game-dev pipeline — all 24 models × 4 games (CI_SAFE=1 for CI-safe subset)" \
		"  game-reference-preflight  Acquire/verify approved FPS clips before Azure provisioning" \
		"  test-e2e-games-provision  Source AZURE_E2E_ENV_FILE; Azure game E2E (GAME_E2E_TIMEOUT_SECS>=3600)" \
		"  e2e-audit-azure     List all E2E runs with PASS/FAIL/RUNNING status" \
		"  e2e-latest-log      Show exit code and error summary for latest E2E run" \
		"  azure-cleanup-e2e   Delete gludd-gpu* groups and visibly verify absence" \
		"  test-e2e-postgres-multiworker  Live Postgres 16 + two-worker Gunicorn acceptance" \
		"  podman-project-up   Start an explicit project Podman machine and wait for readiness" \
		"  podman-project-recreate  Recreate one gludd-namespaced Podman test machine" \
		"  podman-project-delete  Delete one gludd-namespaced Podman machine with bounded progress" \
		"  test-multi-model-pipeline  All multi-model pipeline E2E + integration tests" \
		"  test-local-model-pipeline  Local model pipeline E2E tests" \
		"  test-project-type-pipeline E2E: test_project_type_pipeline.py" \
		"  test-opencode-e2e     .opencode/ plugin load+invocation tests" \
		"  test-opencode-e2e-hour  1-hour E2E spawner test (TIMEOUT=3600)" \
		"  test-specific         Single test (TESTFILE=path::TestClass::test_name)" \
		"  test-specific-pyver   Single test under explicit Python (TESTFILE, PYTHON_VERSION)" \
		"  test-files            Multiple tests (TESTFILES, PYTEST_ARGS, UV_PROJECT_ENVIRONMENT); explicit environments route nested UV consistently" \
		"  test-files-cprofile   Opt-in process-local pytest profile (TESTFILES, PYTEST_ARGS, UV_PROJECT_ENVIRONMENT); immutable namespaced diagnostics" \
		"  grep                  Repository text search (Q=regex SEARCH_PATH=path)" \
		"  ci-shards-diagnostic Serial collect-all diagnostics (CI_SHARDS_DIAGNOSTIC_SHARDS, CI_SHARDS_DIAGNOSTIC_VALIDATE_ONLY=0|1; default 1)" \
		"  audit-agent-context-size Measure explicit JSON file set or default AGENTS/docs CLAUDE (AGENT_CONTEXT_FILES, AGENT_CONTEXT_JSON=0|1)" \
		"  ci-shards-log-context Show local shard log context (LOG=.gate-logs/ci.log PATTERN=FAILED)" \
		"  task                  Run CMD with timeout (CMD=make test-unit, GLUDD_TASK_TIMEOUT=300)" \
		"  test-count            Count collected tests" \
		"  test-failures         Show bounded cached failures (TEST_FAILURES_CACHE, TEST_FAILURES_LIMIT)" \
		"provider-smoke Run gludd smoke PROVIDER=aws SMOKE_TEST=ec2-a100 ARGS=--json" \
		"  local-accelerator-inventory  Discover local GPU/TPU resources without provisioning" \
		"  mac-unified-memory-smoke  Local Apple unified-memory smoke (LIVE=1 BACKEND=mps ARGS=...)" \
		"  gpu-hardware-smoke        Local AMD/NVIDIA GPU smoke (LIVE=1 BACKEND=cuda|rocm ARGS=...)" \
		"  provider-harness      Validate Azure/RunPod credentials, billing bounds, and optional Gludd telemetry" \
		"  azure-harness         Azure provider harness (LIVE=1 for read-only credential check)" \
		"  azure-cleanup-inspect Read-only provisioning states for Gludd Azure E2E resource groups" \
		"  runpod-harness        RunPod provider harness (LIVE=1 for read-only credential check)" \
		"  test-opa-policies     Execute Rego policy tests when opa is installed" \
		"  check-make-target-contract  Validate target variables, help, and behavioral examples" \
		"  check-generated-artifact-hygiene  Validate tracked generated Markdown/JSON/YAML postconditions" \
		"  active-work-status    Emit auditable PIDs, gate state, hashes, and open tasks" \
		"  task-watchdog-start   Acknowledge detached startup (TASK_WATCHDOG_START_VALIDATE_ONLY=0 default, 1 plans)" \
		"  task-watchdog-stop    Verify and drain this checkout daemon (TASK_WATCHDOG_VALIDATE_ONLY=1 default, 0 applies)" \
		"  ps                    Report repository-owned test, audit, and supervisor processes" \
		"  list-plugins          Report the active enforcement plugin roster" \
		"  terminate-project-process-tree  Identity-check and preview/apply one project process tree" \
		"  kill-worktree-e2e    Stop one verified local E2E tree (PID=; validate with KILL_WORKTREE_E2E_VALIDATE_ONLY=1)" \
		"  status-snapshot       Rewrite SESSION.md gate evidence (STATUS_SNAPSHOT_VALIDATE_ONLY=1 for read-only validation)" \
		"  work-item             Administrative focus/support/status; WORK_ITEM_ACTION, WORK_ITEM_ID, WORK_ITEM_REASON, WORK_ITEM_VALIDATE_ONLY" \
		"  codex-stop-guard      Fail closed when tracked work remains; emit a Codex stop challenge token" \
		"  codex-stop-confirm    Confirm a previously issued token before recording a valid stop" \
		"  iam-headless-smoke    Validate least-privilege provider manifests without credentials" \
		"  check-task-integrity  Require changed files to map to registered tasks" \
		"  validate-task-ledger  Validate TASKS.md metadata and completion evidence" \
		"  check-dispatch-dedup Validate the persistent content-addressed dispatch ledger" \
		"  test-and-commit       Run tests then commit if green (MSG='msg')" \
		"  audit-coverage        Run coverage audit: pytest --cov + per-file threshold check" \
		"  test-live-zai         Live GLM model test (requires API key)" \
		"  test-guardrails       Test guardrail infrastructure" \
		"  test-install          Run install.sh bats tests" \
		"  test-language-expert  Language collection E2E: schema + unit + integration + coverage (>=85%)" \
		"" \
		"  --- Terraform ---" \
		"  tf-cache-warm         Download all providers ONCE into the shared plugin cache" \
		"  tf-init STACK=s/n     Init a stack using the shared cache (no re-download)" \
		"  tf-init-local         State-free Azure stack init (STACK, TF_INIT_LOCAL_VALIDATE_ONLY)" \
		"  tf-validate STACK=s/n Validate a stack against the shared cache" \
		"  tf-versions-check     Enforce stacks match infra/terraform/versions.tf" \
		"  tf-clean              Remove the shared plugin cache" \
		"" \
		"  --- Git ---" \
		"  ci-cancel               cancel a CI run by id — use for zombie runs blocking push" \
		"  (Single-source policy: features land on development first, then merge to master)" \
		"  git-status            Show git status" \
		"  git-owned-checkpoint-push     Checkpoint explicit owned files (FILES, GIT_CHECKPOINT_LABEL, GIT_CHECKPOINT_VALIDATE_ONLY)" \
		"  git-owned-checkpoint-restore  Restore exact top checkpoint, retaining evidence (FILES, GIT_CHECKPOINT_LABEL, GIT_CHECKPOINT_SHA, GIT_CHECKPOINT_VALIDATE_ONLY)" \
		"  git-diff              Show diff stats" \
		"  git-staged            Show staged changes" \
		"  git-log               Show recent commits" \
		"  git-show-commit C=<sha>  Show hash, parents, committer time, subject, and files" \
		"  git-show-full SHA=<sha>  Show a host-independent canonical patch" \
		"  git-patch-equivalence PATCH_UPSTREAM=<ref> PATCH_HEAD=<ref> PATCH_LIMIT=<n>  Compare patch identity" \
		"  branches-unmerged-development  List every local branch tip not reachable from development" \
		"  branch-reconciliation-inventory RECONCILE_TARGET=<ref> RECONCILE_LIMIT=<n> RECONCILE_AFTER=<ref|empty>  Page bounded local branch reconciliation state as JSON" \
		"  branch-reconciliation-summary RECONCILE_TARGET=<ref> RECONCILE_LIMIT=<n> RECONCILE_DETAILS=0|1 RECONCILE_CURRENT_ONLY=0|1 RECONCILE_QUIET_PROGRESS=0|1 RECONCILE_HEAD_SEMANTICS=0|1  Exhaustively classify and optionally summarize deduplicated heads" \
		"  branch-reconciliation-completed-backlog RECONCILE_TARGET=<ref> RECONCILE_LIMIT=<n> RECONCILE_BASELINE=<ref> RECONCILE_STAGING_PATH=<path> RECONCILE_REPORT_LIMIT=1..1000 RECONCILE_QUIET_PROGRESS=0|1 RECONCILE_HUMAN=0|1  Compare current branch completion claims; diagnostic only" \
		"  git-add FILES='...'   Stage specific files" \
		"  git-add-all           Stage all changes" \
		"  git-commit MSG='...'  Commit staged changes" \
		"  commit-and-ship MSG='...'  Lint-fix, stage, and commit through the guarded shipping target" \
		"  commit-and-ship-push MSG='...'  Commit, push development, and request the guarded CI verdict" \
		"  rm-files FILES='...'  Remove only explicitly named workspace paths" \
		"  git-reset FILES='...' Reset to ref (soft by default)" \
		"  git-uncommit-last CONFIRM=1  Uncommit local HEAD while preserving all files" \
		"  git-branch MSG='...'  Create branch" \
		"  git-checkout MSG='...' Switch branch" \
		"  git-merge MSG='...'   Merge branch with --no-ff" \
		"  resolve-development-conflicts MERGE_SOURCE=<branch> APPLY=0|1  Preserve development on conflicts" \
		"  git-rebase-abort      Abort an in-progress rebase" \
		"  git-rebase-continue   Continue after resolving rebase conflicts" \
		"  git-rebase-skip       Skip duplicate/current rebase commit" \
		"  git-cherry-pick SHA=<commit> Cherry-pick a specific commit" \
		"  git-cherry-pick-list SHAS='a b ...' Cherry-pick commits in order" \
		"  feature-start MSG='...' Create and switch to feature branch" \
		"  feature-done MSG='...' Test, merge to master with --no-ff" \
		"  agent-worktree BRANCH=<name>  Isolated git worktree for a subagent (no shared-tree races)" \
		"  agent-worktree-base BRANCH=<name> BASE=<ref>  Isolated worktree from an explicit base ref" \
		"  agent-worktree-retain  Plan a native retention lock (BRANCH, WORKTREE, WORKTREE_RETAIN_REASON, WORKTREE_RETAIN_VALIDATE_ONLY=1)" \
		"  workstream-register BRANCH=<name> WORKTREE=<path>  Protect an active logical workstream" \
		"  workstream-unregister BRANCH=<name>  Release a completed logical workstream" \
		"  wt-prune-safe         Default clean pruning; WT_PRUNE_RETIRE_MERGED=1 retires one merged BRANCH/WORKTREE with archived proof (WT_PRUNE_RETIRE_VALIDATE_ONLY=1 plans; ACTIVE_WORKSTREAM_REGISTRY, WT_PRUNE_VALIDATE_ONLY)" \
		"  agent-merge BRANCH=<name>     Merge a subagent worktree branch into master (--no-ff)" \
		"  agent-cleanup BRANCH=<name>   Remove a subagent worktree + branch after merge" \
		"  agent-worktree-list           List active git worktrees" \
		"  worktree-commit-changes MSG='...'  Commit all changes in the current worktree" \
		"  worktree-cleanup-merged       Remove worktrees whose branches are merged into development" \
		"  merge-commit-nv MSG='...'     Complete an in-progress merge commit without pre-commit hooks" \
		"  self-improve-local-proposal  Owned local GGUF proposal worker (SELF_IMPROVE_MODEL_PATH/PROMPT_FILE/PROPOSAL_FILE)" \
		"  azure-self-improve-auth-args  Emit validated NUL arguments for one least-privilege Azure SP command" \
		"  azure-self-improve-live-proof  Discover, select, deploy, evaluate, and tear down one bounded Azure candidate" \
		"  azure-accelerator-role-apply Apply/validate the exact GPU role through Microsoft SDKs (AZURE_ACCELERATOR_*)" \
		"  azure-accelerator-role-args  Deprecated compatibility argv for Azure CLI role creation" \
		"  azure-accelerator-role-update-args  Emit validated NUL arguments to narrow an existing GPU role" \
		"  azure-accelerator-auth-args  Emit validated NUL arguments for its Azure SP credential command" \
		"  azure-containerapp-environment-bootstrap-args  Emit one operator-owned shared GPU environment deployment" \
		"  azure-accelerator-auth-check Secret-safe validation of Azure CLI --json-auth output" \
		"  azure-accelerator-auth-store Preserve stdin/source JSON as immutable protected generations" \
		"  azure-containerapp-preflight Traced read-only named-environment GPU sizing and quota proof" \
		"  azure-containerapp-terraform-phase  Owned app-only Terraform phase (AZURE_CONTAINERAPP_TF_*)" \
		"  azure-containerapp-environment-guard  Read-only protected GitHub Environment admission" \
		"  azure-containerapp-live-proof  Hermetic/live bounded deploy-infer-destroy proof (AZURE_CONTAINERAPP_LIVE_PROOF_*)" \
		"  test-azure-containerapp-coverage  Hermetic Azure Container Apps tests with 85/75 coverage gates" \
		"  test-self-improve TARGET=<name>  Compare an auto-managed local model with Codex (optional SELF_IMPROVE_MODEL_PATH override)" \
		"  test-self-improve-catalog-truth  Replay pinned catalog fixture (SELF_IMPROVE_CATALOG_LIVE=0|1)" \
		"  test-self-improve-multifile      Replay pinned multi-file fixture (SELF_IMPROVE_MULTIFILE_LIVE=0|1)" \
		"  test-self-improve-failure-corpus Replay typed local failures offline (SELF_IMPROVE_FAILURE_CORPUS_FILE)" \
		"  test-self-improve-acceptance-matrix Validate/run the serial ten-shape contract (SELF_IMPROVE_ACCEPTANCE_MATRIX_*)" \
		"  test-self-improve-private-policy  Run hermetic fake-local/fake-Azure project privacy E2E coverage" \
		"  test-self-improve-all            Deprecated alias for one explicit Codex-reference benchmark" \
		"  self-improve-promotion-marker    Verify an exact promotion marker on development (SELF_IMPROVE_PROMOTION_* variables)" \
		"  git-index                    Index git log into SQLite (.gludd/git_history.db)" \
		"  git-search Q='...'           Search indexed git history" \
		"  git-stats                    Show git history index statistics" \
		"  agent-report                 Agent activity dashboard (reads /tmp/gludd-agent-results.jsonl)" \
		"  check-duplicate-targets           Detect Makefile targets declared on parallel branches" \
		"  agent-worktree-dev BRANCH=<name>  Isolated git worktree from development branch" \
		"  agent-merge-dev BRANCH=<name>     Merge a subagent worktree branch into development" \
		"  development-push             Push the development branch to remote" \
		"  development-merge-forward SOURCE=<ref> MODE=content|ancestry-only APPLY=0|1  Transactional reconciliation into development (dry-run default)" \
		"  development-merge-forward-batch SOURCES='<refs>' APPLY=0|1  Atomic ancestry-only reconciliation for multiple superseded refs" \
		"  development-fast-forward SOURCE=<ref> EXPECTED_BEFORE_SHA=<sha> EXPECTED_AFTER_SHA=<sha> APPLY=0|1  Guarded development fast-forward (default 0; APPLY=1 requires full 40-character SHAs)" \
		"  development-merge-to-master  Merge development into master (release prep; CI-green required)" \
		"  development-start            Create development branch from master if it doesn't exist" \
		"  development-status           Show commits on development not yet on master" \
		"  git-tag-push TAG=<t> [COMMIT=<sha>] [MSG='...']  Create annotated tag + push to sandboxcom" \
		"  git-tag-rm TAG=<t>           Delete tag locally and on sandboxcom" \
		"  git-tag-delete TAG=<t>       Alias for git-tag-rm" \
		"  git-tag-move TAG=<t> MSG='..'  Delete old tag + create new at HEAD + push" \
		"  submodule-init        Initialize submodules or validate configuration (SUBMODULE_INIT_VALIDATE_ONLY=0|1)" \
		"  submodule-update      Update submodules to latest remote (--merge)" \
		"  submodule-status      Show status of each submodule" \
		"  submodule-pin REPO=.. TAG=..  Pin a submodule to a tag/commit" \
		"" \
		"  --- Secrets + Security ---" \
		"  secrets-scan          Scan for secrets against baseline (read-only; GLUDD_WORKER_LIMIT, ARGS)" \
		"  secrets-scrub         Interactive secret audit + scrub" \
		"  secrets-baseline      Refresh and verify canonical .secrets.baseline" \
		"  secrets-baseline-check Verify canonical .secrets.baseline without mutation (SECRETS_BASELINE_FILE, POLICY, REPO_ROOT, EXECUTABLE)" \
		"  check-secrets-files   Native read-only hook on explicit files (SECRETS_FILES, SECRETS_BASELINE_FILE)" \
		"  git-unstage-files     Remove explicit files from index, preserving content (FILES)" \
		"  scan-secrets          Alias for secrets-scan (GLUDD_WORKER_LIMIT, ARGS)" \
		"  scan-secrets-baseline Alias for secrets-baseline" \
		"  sast-summary          Summarize Bandit JSON by severity/rule/file with baseline deltas (SAST_REPORT, SAST_SUMMARY, SAST_BASELINE)" \
		"  security-audit        Observable secrets/SAST/dependency/backlog audit (SECURITY_AUDIT_HEARTBEAT_SECS, SECURITY_AUDIT_PHASE_TIMEOUT_SECS, SECURITY_AUDIT_VALIDATE_ONLY, SECURITY_AUDIT_SUMMARY, GLUDD_WORKER_LIMIT)" \
		"  clean-artifacts       Clean build artifacts, caches, temp files (replaces direct rm)" \
		"  health-check          Verify imports and basic system health" \
		"  clean-untracked       Remove reinvention-of-wheel files" \
		"  clean-hooks           Remove legacy hook scripts" \
		"  clean-plugins         No-op (false-done merged into enforce-stop.ts)" \
		"" \
		"  --- Release ---" \
		"  release-list          List all GitHub releases" \
		"  reviewed-head-receipt ...  Build exact-topology integration evidence after the final gate" \
		"  release-readiness TAG=..  Fail-closed blockers + exact reviewed-head receipt for v0.1.2" \
		"  release-proof-admission RELEASE_PROOF_ADMISSION_VALIDATE_ONLY=0|1  Scheduling only; uses current focus or explicit WORK_ITEM_ID" \
		"  release-page-notes TAG=..  Build/validate bounded ledger-backed GitHub release-page notes" \
		"  check-release-failure-ledger RELEASE_FAILURE_LEDGER=..  Validate immutable beta failure mappings" \
		"  release-branch-new    Cut a release/* branch from a CI-green base (NAME, BASE, RELEASE_BRANCH_VALIDATE_ONLY)" \
		"  require-dual-track-green Require exact-SHA local + hosted CI attestations (SHA, DUAL_TRACK_CI_VALIDATE_ONLY)" \
		"  reviewed-head-inputs   Native review inputs: admit|record|focus|finalize (REVIEWED_HEAD_INPUTS_*, dry-plan by default; no release authority)" \
		"  release-view TAG=..   Show a published GitHub Release + its assets" \
		"  release-create TAG=.. CI-green-gated DRAFT release (single binary; complete via CI)" \
		"  release-upload-assets TAG=.. FILES='..'  Add assets to an existing release (repair path)" \
		"  release-cut TAG=.. MSG=.. REVIEWED_HEAD_INTEGRATION_RECEIPT=..  The single release command (6 fail-closed steps; exact-run wait up to 90m)" \
		"  release-promote TAG=.. MSG=.. REVIEWED_HEAD_INTEGRATION_RECEIPT=..  Exact-SHA ff-only development promotion; RELEASE_PROMOTE_PUBLISH=0|1 (default 1; 0 prepares guarded merge only; validate-only supported)" \
		"  release-recut TAG=..  Re-trigger and await the exact tag release workflow" \
		"  release-deploy TAG=.. MSG=..  Auto-deploy: merge dev->master, push, tag, wait for CI" \
		"  release-delete TAG=.. Delete GitHub Release + local + remote git tags" \
		"  verify-release-artifact       TAG=..  Confirm a release has published assets (exit 0 = shipped)" \
		"  verify-release-completeness   TAG=..  Verify all 28 categories / 30 beta4 assets present" \
		"" \
		"  --- Build + Deploy ---" \
		"  dist                  Build distribution tarball" \
		"  build-executable      Build standalone executable (pyinstaller)" \
		"  audit-linux-pyinstaller-warnings  Validate/replay the Linux PyInstaller warning policy" \
		"  compare-linux-pyinstaller-warnings  Emit an exact old/new warning-graph review receipt" \
		"  check-pyinstaller-warning-reviews  Require an exact receipt for every newly accepted graph" \
		"  build-linux-binary-image  Build the digest-pinned Python/uv artifact environment (LINUX_BINARY_*, DOCKER_BUILDX_*, LIMA_*)" \
		"  lima-docker-ensure    Provision/reuse a namespaced Lima Docker engine (LIMA_INSTANCE, LIMA_DOCKER_CONFIG, LIMA_DOCKER_TEMPLATE, LIMA_DOCKER_START_TIMEOUT_SECS, LIMA_DOCKER_VALIDATE_ONLY)" \
		"  lima-docker-start     Start an existing namespaced Lima Docker engine (LIMA_INSTANCE, LIMA_DOCKER_CONFIG, LIMA_DOCKER_START_TIMEOUT_SECS, LIMA_DOCKER_VALIDATE_ONLY)" \
		"  lima-docker-stop      Gracefully stop an existing namespaced Lima Docker engine (LIMA_INSTANCE, LIMA_DOCKER_STOP_TIMEOUT_SECS, LIMA_DOCKER_STOP_KILL_AFTER_SECS, LIMA_DOCKER_VALIDATE_ONLY)" \
		"  lima-docker-delete    Delete one stopped, exactly confirmed namespaced Lima VM (LIMA_INSTANCE, LIMA_DOCKER_DELETE_CONFIRM, LIMA_DOCKER_DELETE_KILL_AFTER_SECS, LIMA_DOCKER_DELETE_TIMEOUT_SECS, LIMA_DOCKER_DELETE_VALIDATE_ONLY)" \
		"  lima-docker-status    Inspect the namespaced Lima Docker engine (LIMA_INSTANCE, LIMA_DOCKER_CONFIG, LIMA_DOCKER_VALIDATE_ONLY)" \
		"  lima-docker-pull      Pull one image into namespaced Lima Docker (LIMA_INSTANCE, LIMA_IMAGE, LIMA_DOCKER_CONFIG, LIMA_DOCKER_VALIDATE_ONLY)" \
		"  podman-legacy-default-delete  Remove only a stopped legacy default VM (PODMAN_LEGACY_MACHINE, PODMAN_LEGACY_DELETE_TIMEOUT_SECS, PODMAN_LEGACY_DELETE_VALIDATE_ONLY)" \
		"  container-build       Build container image" \
		"  container-run         Run container locally" \
		"  container-push        Push container image" \
		"  deb-package           Build .deb package from dist/gludd binary" \
		"  deb-install-deps      apt-get install dependencies from debian/control" \
		"  build-sandbox-image       Build Firecracker rootfs image (Alpine + gludd deps)" \
		"  vm-image-build            Build VM sandbox images (Firecracker + gVisor)" \
		"  vm-image-list             List cached VM sandbox images" \
		"  vm-image-clean            Remove all cached VM sandbox images" \
		"  verify-sandbox-image      Integrity check on cached sandbox rootfs" \
		"  clean-sandbox-images      Remove cached sandbox images" \
		"  sandbox-state-dir         Print sandbox runtime-state directory" \
		"  sandbox-state-list        List sandbox runtime-state contents" \
		"  sandbox-state-clean       Clean sandbox runtime-state for current project" \
		"  compute-model-hashes      Recompute the tracked model-artifact hash inventory" \
		"  download-1.5b-model       Download the namespaced Qwen 1.5B test model" \
		"  download-deepseek-1.3b    Download the namespaced DeepSeek 1.3B test model" \
		"  benchmark-codegen-quality Compare local code-generation model quality" \
		"  benchmark-local-model    Benchmark the configured local model" \
		"  benchmark-models         Compare all configured local models" \
		"  run-game-gen-1.5b        Run game generation with the namespaced Qwen 1.5B model" \
		"  compare-models           Compare local and hosted model quality" \
		"" \
		"  --- Governance ---" \
		"  test-governance       Run governance collection unit tests" \
		"  governance-syntax     Validate governance role YAML syntax" \
		"  governance-health     Check governance module_utils imports" \
		"" \
		"  --- Ansible ---" \
		"  ansible-syntax        Validate playbook syntax" \
		"  playbook-list         List registered playbooks" \
		"  molecule-test SCENARIO=<name>   Run one canonical Molecule scenario" \
		"  molecule-reset SCENARIO=<name>  Clear one scenario's Molecule-owned state" \
		"" \
		"  --- CI ---" \
		"  ci-kill-zombie          cancel a CI run via gh run cancel" \
		"  ci-job-failure-context  bounded authenticated failure context (RUN, JOB, PATTERN, BEFORE, AFTER, MAX_MATCHES)" \
		"  ci-artifact-download    atomically download one exact run-bound GHA artifact (RUN, ARTIFACT, CI_ARTIFACT_OUTPUT_ROOT, CI_ARTIFACT_HEARTBEAT_SECS, CI_ARTIFACT_DOWNLOAD_VALIDATE_ONLY)" \
		"  ci-artifact-context     bounded context from one downloaded exact-run artifact (RUN, ARTIFACT, CI_ARTIFACT_FILE, PATTERN, BEFORE, AFTER, MAX_MATCHES, CI_ARTIFACT_CONTEXT_VALIDATE_ONLY)" \
		"  ci-pyinstaller-warning-audit replay the complete warning graph from one exact-run artifact (RUN, ARTIFACT, PYINSTALLER_WARNING_*, CI_PYINSTALLER_WARNING_AUDIT_VALIDATE_ONLY)" \
		"  ci-coverage-artifact-audit audit one externally stored hosted Cobertura report (CI_COVERAGE_*)" \
		"  ci-coverage-gap-plan       print a bounded exact-run line/branch remediation plan (CI_COVERAGE_*)" \
		"  ci-run-summary RUN=<id> show one immutable CI run; CI_RUN_SUMMARY_VALIDATE_ONLY=0|1 CI_RUN_SUMMARY_SHARD_TIMINGS=0|1" \
		"  ci-failure-status        Show every durable hosted failure family" \
		"  ci-failure-repair        Run make-based evidence and receipt selected repairs" \
		"  ci-failure-push-guard    Block pushes with open or non-ancestral repairs" \
		"  ci-await BRANCH=<ref> TIMEOUT=<s> [SHA=.. CI_AWAIT_*]  Await one exact CI identity" \
		"  ci-verdict-safe        Exact all-workflow check (SHA, CI_BRANCH, legacy BRANCH, CI_CHECK_COOLDOWN_SEC, CI_VERDICT_VALIDATE_ONLY=0|1); validation never queries or records" \
		"  ci-dashboard           One-shot compact CI run listing" \
		"  ci-diagnose            Fetch CI failure annotations and group by root cause" \
		"  ci-cooldown-status     Show remaining cooldown seconds" \
		"  ci-view RUN=<id>       Show CI run details (jobs, steps, failures)" \
		"  ci-rerun RUN=<id>      Guard and rerun one observed immutable CI run" \
		"  ci-recover-runner-acquisition RUN=<id>  Retry attempt 1 only when every failure is hosted-runner acquisition" \
		"  ci-active              List active/in-flight CI runs" \
		"  ci-greenness           CI reliability ratio (green / total completed)" \
		"  ci-trigger-committed-head [REF=<b>]  Idempotently signal + return exact-SHA GHA run URL" \
		"  ci-record-verdict      Record a known CI verdict directly, bypassing cooldown (VERDICT=success|failure|pending, SHA=<sha>)" \
		"  deploy-and-forget      Single guarded push + cooldown record (BRANCH, DEPLOY_AND_FORGET_VALIDATE_ONLY=0|1)" \
		"" \
		"  --- Git Remote ---" \
		"  git-remote-sandboxcom Configure sandboxcom GitHub remote with SSH key" \
		"  git-push-sandboxcom   Guarded mirror push (PUSH_REMOTE, SSH_KEY; dry-run is not remote evidence)" \
		"  git-pull-sandboxcom   Pull and rebase from sandboxcom/gludd" \
		"  git-fetch-sandboxcom  Fetch from sandboxcom/gludd" \
		"  ship-async REF=<hash> [TARGET=master]  Run gate in background job; ff-only merge on green" \
		"" \
		"  --- SearXNG Research Backend ---" \
		"  searx-up              Admit the controller-native SearXNG runtime" \
		"  searx-down            Release controller-native SearXNG resources" \
		"  searx-test            Run the native runtime/action/index contract suite" \
		"  searx-molecule        Validate/run delegated setup role (SEARX_MOLECULE_VALIDATE_ONLY=0|1)" \
		"" \
		"  --- Disk ---" \
		"  fix-hooks-tmp           temp fix target" \
		"  disk-guard            Canonical disk preflight + ownership-checked cleanup (95% hard volume limit)" \
		"  disk-check            Read-only canonical volume and scratch check (95% / 100 MB)" \
		"  disk-cleanup-preflight  Auto-reclaim proven-idle Gludd storage, then recheck thresholds (DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0|1, DISK_CLEANUP_PREFLIGHT_DRY_RUN=0|1, DISK_CLEANUP_RECEIPT_GRACE_SECONDS>=1800)" \
		"  clean-stale-resource-namespaces  Inventory/reclaim proven-inactive Gludd namespaces (STALE_RESOURCE_NAMESPACE_*; PYTHON selects an existing trusted runtime); never bypass unknown-owner or link refusal" \
		"  check-disk            Pre-commit automatic cleanup guard (CHECK_DISK_VALIDATE_ONLY=0; set 1 for deterministic contract test)" \
		"  check-disk-classification  Bounded JSON-lines proof of counted vs exempt /tmp/gludd-* roots" \
		"  check-system-load     Read-only system load diagnostic (1m avg, CPU count, verdict)" \
		"  disk                  Print disk usage + gludd footprint" \
		"  disk-reclaim          Run bounded, heartbeat-emitting cache cleanup" \
		"  git-object-store-reclaim  Inventory or conservatively reclaim Git objects (GIT_OBJECT_STORE_*)" \
		"  cache-disk            Show bounded user-cache directory sizes" \
		"  cache-clean           Remove the explicitly enumerated tool caches" \
		"  disk-user-caches      Show accessible user-cache and data-root sizes" \
		"  cache-resource-inventory  List largest children of one allowlisted cache root" \
		"  cache-resource-remove     Validate or remove one exact allowlisted cache child" \
		"  native-temp-artifact-inspect  Read-only exact native-cache identity (NATIVE_TEMP_ARTIFACT_PATH, NATIVE_TEMP_ARTIFACT_VALIDATE_ONLY=0|1); no cleanup authority" \
		"  uv-cache-prune-status List uv cache-prune PID, parent, age, and command" \
		"  tmp-gludd-usage       Print largest /tmp/gludd-* entries sorted by size" \
		"  tmp-gludd-worktree-usage  Print largest generated entries under /tmp/gludd-worktrees" \
		"  tmp-gludd-clean-ci-shards  Remove stale generated CI shard scratch dirs" \
		"  tmp-gludd-clean-ci-shards-now  Remove inactive CI/gate shard roots (TMP_GLUDD_CLEAN_VALIDATE_ONLY=0; set 1 for contract test)" \
		"  tmp-gludd-clean-orphan-worktrees-now  Validate or remove proven orphan roots (TMP_GLUDD_ORPHAN_CLEAN_VALIDATE_ONLY=1; use 0 only after merge)" \
		"  clean-worktree-venvs  Preserve invoking/active worktrees; reclaim inactive registered venvs (CLEAN_WORKTREE_VENVS_VALIDATE_ONLY=0|1)" \
		"  clean-worktree-caches  Remove generated venv/test/tool caches from worktrees" \
		"" \
		"  --- OpenCode Database Maintenance ---" \
		"  opencode-disk         Bounded data usage using the authoritative OpenCode DB path" \
		"  opencode-clean        Offline bounded DB/cache cleanup; refuses while OpenCode runs" \
		"  opencode-clean-hard   Offline aggressive cache/log cleanup; refuses while OpenCode runs" \
		"  opencode-db-stats     Bounded read-only table counts" \
		"  opencode-db-schema    Bounded read-only schema report" \
		"  opencode-db-sample    Bounded read-only timestamp sample" \
		"  opencode-db-prune     Offline bounded recursive session/event prune" \
		"  opencode-db-vacuum-incremental  Safe PRAGMA incremental_vacuum (online)" \
		"  opencode-db-vacuum-full        Full VACUUM (needs OPENCODE_MAINTENANCE_FORCE=1 while online)" \
		"  opencode-db-compact     Aggressive prune then compact via sqlite3 backup API (OPENCODE_RETENTION_DAYS, OPENCODE_MAINTENANCE_FORCE)" \
		"" \
		"  --- Recovery ---" \
		"  reap-orphan-pytest    Report stale orphan pytest trees (APPLY=1 to terminate)" \
		"  reap-stale-collection-locks  Reap only old project-owned collection/gate-refresh locks (APPLY=1)" \
		"  replay-codex-file-changes  Atomically validate/replay a bounded Codex file-change range" \
		"  backup-opencode       Backup .opencode/ -> .opencode.orig/ (excludes node_modules/)" \
		"  check-opencode-backup  Warn if .opencode.orig/ is stale (>24h older than .opencode/)" \
		"  restore-opencode      Restore .opencode/ (backup then git fallback) + clear cache" \
		"  verify-opencode-backup Verify .opencode.orig/ is current (files + shared.ts exports)" \
		"" \
		"  --- Other ---" \
		"  smoke                 Quick daemon boot health check" \
		"  clean                 Remove ignored build artifacts while preserving tracked templates (CLEAN_VALIDATE_ONLY=0|1)" \
		"  dist-clean            Remove distribution artifacts" \
		"  gated-merge           flock-guarded multi-branch merge with manifest (BASE/BRANCHES/MERGE_STRATEGY/MANIFEST)" \
		"" \
		"  --- Complete Target Index ---" && \
	$(PYTHON) -m scripts.check_make_help --print-index && \
	printf '%s\n' \
		"  --- New Targets ---" \
		"  normalize-task-integrityNormalize legacy TASKS metadata and reopen unsupported completions" \
		"  install-opa             install opa via brew" \
		"  gate-local              fast local gate: lint + typecheck + collect + hook-runtime + fast structural tests" \
		"  bump-version            bump version in all files (pyproject.toml, __init__.py, README) at once" \
		"  check-version-consistencyverify version matches across pyproject.toml, __init__.py, and README" \
		"  check-gate-fresh        validate .gate-status is fresh and all phases pass — replaces broken _gate-fresh-check inline shell" \
		"  pipeline-health         verify both local and remote pipelines are actually running (not stalled/zombie)" \
		"  pipeline-status         exact pushed-SHA local/all-workflow status and automatic JSON handoff (PIPELINE_STATUS_*)" \
		"  status-heartbeat         Refresh producer-owned proof state (INTERVAL, COUNT, STATUS_HEARTBEAT_VALIDATE_ONLY, STATUS_HEARTBEAT_STOP_ON_TERMINAL=0|1, PIPELINE_STATUS_*)" \
		"  gate-all-background     run gate-all in background, poll with gate-status-check" \
		"  target-two              Second test target" \
		"  target-one              First test target" \
		"  my-target               Duplicate target" \
		"  my-secret-scanner       Scan for secrets" \
		"  zzyx-test               A test target with no keyword match" \
		"  debug-test-target       Debug test" \
		"  foo-test                Test" \
		"" \
		""

sdd-constitution:
	@test -f AGENTS.md || touch AGENTS.md
	@echo "SDD constitution ready"

sdd-discover:
	@echo "SDD discover ready"

sdd-specify:
	@echo "SDD specify ready"

sdd-plan:
	@echo "SDD plan ready"

sdd-tasks:
	@echo "SDD tasks ready"

sdd-implement:
	@echo "GATE: SDD implement verification delegated to make gate"

sdd-pr:
	@echo "SDD PR ready"

sdd-release:
	@echo "SDD release ready"

sdd-audit:
	@echo "SDD audit ready"

sdd-critic:
	@echo "SDD critic ready"

sdd-harvest:
	@echo "SDD harvest ready"

sdd-quickfix:
	@echo "SDD quickfix ready"

skeleton:
	@$(PYTHON) scripts/skeleton.py

scan-tool-usage:
	@$(PYTHON) scripts/scan_tool_usage.py

script-count:
	@echo "Source files: $$(find src -name '*.py' | wc -l)"
	@echo "Test files: $$(find tests -name '*.py' | wc -l)"
	@echo "Ansible roles: $$(ls -d collections/ansible_collections/general_ludd/agent/roles/*/ | wc -l)"
	@echo "Ansible modules: $$(ls collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_*.py 2>/dev/null | wc -l)"
	@echo "Enforcement plugins: $$(ls .opencode/plugin/*.ts 2>/dev/null | wc -l)"
	@echo "Make targets: $$(grep -c '^[a-z].*:' Makefile)"

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
	@command -v $(UV) >/dev/null 2>&1 || { echo "uv is required for locked dependency profiles"; exit 1; }
	@$(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=development DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON= DEPENDENCY_PROFILE_VALIDATE_ONLY=0
	@$(MAKE) --no-print-directory install-hooks

.PHONY: acquire-pinned-uv
PINNED_UV_VALIDATE_ONLY ?= 1
override GLUDD_PINNED_UV_VALIDATE_ONLY := $(value PINNED_UV_VALIDATE_ONLY)
# Explicit assignment keeps the existing environment-source audit authoritative.
export GLUDD_PINNED_UV_VALIDATE_ONLY := $(value PINNED_UV_VALIDATE_ONLY)
unexport PINNED_UV_VALIDATE_ONLY

acquire-pinned-uv:
	@case "$$GLUDD_PINNED_UV_VALIDATE_ONLY" in 0|1) ;; *) echo 'PINNED_UV_VALIDATE_ONLY must be 0 or 1'; exit 2;; esac; \
	$(UV) run python -B -m scripts.dependency_profiles acquire-uv --root "$(CURDIR)" \
		$$(if [ "$$GLUDD_PINNED_UV_VALIDATE_ONLY" = 1 ]; then printf '%s' --validate-only; fi)

DEPENDENCY_RUNTIME_ENVIRONMENT ?=
DEPENDENCY_RUNTIME_PYTHON ?= 3.12.15
DEPENDENCY_RUNTIME_SET ?= ci
DEPENDENCY_RUNTIME_VALIDATE_ONLY ?= 1
export GLUDD_DEPENDENCY_RUNTIME_ENVIRONMENT := $(value DEPENDENCY_RUNTIME_ENVIRONMENT)
export GLUDD_DEPENDENCY_RUNTIME_PYTHON := $(value DEPENDENCY_RUNTIME_PYTHON)
export GLUDD_DEPENDENCY_RUNTIME_SET := $(value DEPENDENCY_RUNTIME_SET)
export GLUDD_DEPENDENCY_RUNTIME_VALIDATE_ONLY := $(value DEPENDENCY_RUNTIME_VALIDATE_ONLY)
unexport DEPENDENCY_RUNTIME_ENVIRONMENT DEPENDENCY_RUNTIME_PYTHON DEPENDENCY_RUNTIME_SET DEPENDENCY_RUNTIME_VALIDATE_ONLY

inspect-dependency-runtime:
	@case "$$GLUDD_DEPENDENCY_RUNTIME_VALIDATE_ONLY" in 0|1) ;; *) echo 'DEPENDENCY_RUNTIME_VALIDATE_ONLY must be 0 or 1'; exit 2;; esac; \
	case "$$GLUDD_DEPENDENCY_RUNTIME_PYTHON" in 3.11.[0-9]*|3.12.[0-9]*|3.13.[0-9]*|3.14.[0-9]*) ;; *) echo 'exact supported Python patch is required'; exit 2;; esac; \
	case "$$GLUDD_DEPENDENCY_RUNTIME_SET" in ci|development) ;; *) echo 'DEPENDENCY_RUNTIME_SET must be ci or development'; exit 2;; esac; \
	case "$$GLUDD_DEPENDENCY_RUNTIME_ENVIRONMENT" in ''|/*) ;; *) echo 'registered absolute runtime environment is required'; exit 2;; esac; \
	$(UV) run python -B scripts/dependency_profiles.py inspect --root "$(CURDIR)" \
		--set "$$GLUDD_DEPENDENCY_RUNTIME_SET" --python "$$GLUDD_DEPENDENCY_RUNTIME_PYTHON" \
		$${GLUDD_DEPENDENCY_RUNTIME_ENVIRONMENT:+--environment "$$GLUDD_DEPENDENCY_RUNTIME_ENVIRONMENT"} \
		$$(if [ "$$GLUDD_DEPENDENCY_RUNTIME_VALIDATE_ONLY" = 1 ]; then printf '%s' --validate-only; fi)

DEPENDENCY_PROFILE_SET ?= development
DEPENDENCY_PROFILE_ENVIRONMENT ?= .venv
DEPENDENCY_PROFILE_PYTHON ?=
DEPENDENCY_PROFILE_VALIDATE_ONLY ?= 0

sync:
	@case "$(DEPENDENCY_PROFILE_VALIDATE_ONLY)" in 0|1) ;; *) echo "DEPENDENCY_PROFILE_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@UV_NO_SYNC=0 $(UV) run --no-project --python 3.11 python scripts/dependency_profiles.py sync \
		--root "$(CURDIR)" \
		--manifest config/dependency_profiles.toml \
		--uv "$(UV)" \
		--set "$(DEPENDENCY_PROFILE_SET)" \
		--environment "$(DEPENDENCY_PROFILE_ENVIRONMENT)" \
		$(if $(strip $(DEPENDENCY_PROFILE_PYTHON)),--python "$(DEPENDENCY_PROFILE_PYTHON)",) \
		$(if $(filter 1,$(DEPENDENCY_PROFILE_VALIDATE_ONLY)),--validate-only,)

ANSIBLE_FORMAT_SYNC_ENVIRONMENT ?= .venv
ANSIBLE_FORMAT_SYNC_VALIDATE_ONLY ?= 1
export GLUDD_ANSIBLE_FORMAT_SYNC_ENVIRONMENT := $(value ANSIBLE_FORMAT_SYNC_ENVIRONMENT)
export GLUDD_ANSIBLE_FORMAT_SYNC_VALIDATE_ONLY := $(value ANSIBLE_FORMAT_SYNC_VALIDATE_ONLY)
export GLUDD_ANSIBLE_FORMAT_SYNC_CALLER_ROOT := $(value CURDIR)
unexport ANSIBLE_FORMAT_SYNC_ENVIRONMENT ANSIBLE_FORMAT_SYNC_VALIDATE_ONLY
.PHONY: sync-ansible-native-format
# ANSIBLE_FORMAT_SYNC_ENVIRONMENT -> literal GLUDD_ANSIBLE_FORMAT_SYNC_ENVIRONMENT
# ANSIBLE_FORMAT_SYNC_VALIDATE_ONLY -> literal GLUDD_ANSIBLE_FORMAT_SYNC_VALIDATE_ONLY
sync-ansible-native-format:
	@set -eu; \
	case "$$GLUDD_ANSIBLE_FORMAT_SYNC_VALIDATE_ONLY" in \
		1) set -- --dry-run ;; 0) set -- ;; \
		*) echo "ANSIBLE_FORMAT_SYNC_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; \
	esac; \
	case "$$GLUDD_ANSIBLE_FORMAT_SYNC_ENVIRONMENT" in \
		/*) gludd_native_format_environment="$$GLUDD_ANSIBLE_FORMAT_SYNC_ENVIRONMENT" ;; \
		*) gludd_native_format_environment="$$GLUDD_ANSIBLE_FORMAT_SYNC_CALLER_ROOT/$$GLUDD_ANSIBLE_FORMAT_SYNC_ENVIRONMENT" ;; \
	esac; \
	test -f "$$gludd_native_format_environment/pyvenv.cfg" && \
		test -x "$$gludd_native_format_environment/bin/python" || \
		{ echo "ANSIBLE_FORMAT_SYNC_ENVIRONMENT must be an existing tooling environment"; exit 2; }; \
	UV_PROJECT_ENVIRONMENT="$$gludd_native_format_environment" \
		VIRTUAL_ENV="$$gludd_native_format_environment" UV_NO_SYNC=0 UV_PYTHON_DOWNLOADS=never \
		$(UV) sync --project "$(CURDIR)/requirements/profiles/dev-ansible" --locked --inexact \
		--python "$$gludd_native_format_environment/bin/python" "$$@"

uv-cache-path:
	@printf '%s\n' "$$UV_CACHE_DIR"

migrate-up:
	@test -n "$(strip $(MIGRATE_DATABASE_URL))" || { echo "MIGRATE_DATABASE_URL is required"; exit 2; }
	@test -n "$(strip $(MIGRATE_REVISION))" || { echo "MIGRATE_REVISION is required"; exit 2; }
	@DATABASE_URL="$(MIGRATE_DATABASE_URL)" $(UV) run alembic upgrade "$(MIGRATE_REVISION)"

sync-local-inference:
	@$(MAKE) --no-print-directory sync \
		DEPENDENCY_PROFILE_SET=local-inference \
		DEPENDENCY_PROFILE_ENVIRONMENT=.venv \
		DEPENDENCY_PROFILE_PYTHON= \
		DEPENDENCY_PROFILE_VALIDATE_ONLY=0

SYNC_LLAMA_CPP_VALIDATE_ONLY ?= 0
sync-llama-cpp:
	@case "$(SYNC_LLAMA_CPP_VALIDATE_ONLY)" in 0|1) ;; *) echo "SYNC_LLAMA_CPP_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@$(MAKE) --no-print-directory sync \
		DEPENDENCY_PROFILE_SET=local-inference \
		DEPENDENCY_PROFILE_ENVIRONMENT=.venv \
		DEPENDENCY_PROFILE_PYTHON= \
		DEPENDENCY_PROFILE_VALIDATE_ONLY=$(SYNC_LLAMA_CPP_VALIDATE_ONLY)

ANSIBLE_EE_VALIDATE_ONLY ?= 1
ANSIBLE_EE_RUNTIME ?= podman
ANSIBLE_EE_IMAGE ?= gludd-ansible-ee:0.1.0-beta.4
ANSIBLE_EE_CONTEXT ?= /tmp/gludd-ansible-ee-context
ANSIBLE_EE_ARTIFACT_ROOT ?= dist/collections/third-party
ANSIBLE_EE_DOCKER_CONFIG ?=
ANSIBLE_EE_DOCKER_HOST ?=
export GLUDD_EE_BUILD_VALIDATE_ONLY := $(value ANSIBLE_EE_VALIDATE_ONLY)
export GLUDD_EE_BUILD_RUNTIME := $(value ANSIBLE_EE_RUNTIME)
export GLUDD_EE_BUILD_IMAGE := $(value ANSIBLE_EE_IMAGE)
export GLUDD_EE_BUILD_CONTEXT := $(value ANSIBLE_EE_CONTEXT)
export GLUDD_EE_BUILD_ARTIFACT_ROOT := $(value ANSIBLE_EE_ARTIFACT_ROOT)
export GLUDD_EE_BUILD_DOCKER_CONFIG := $(value ANSIBLE_EE_DOCKER_CONFIG)
export GLUDD_EE_BUILD_DOCKER_HOST := $(value ANSIBLE_EE_DOCKER_HOST)
unexport ANSIBLE_EE_VALIDATE_ONLY ANSIBLE_EE_RUNTIME ANSIBLE_EE_IMAGE ANSIBLE_EE_CONTEXT
unexport ANSIBLE_EE_ARTIFACT_ROOT ANSIBLE_EE_DOCKER_CONFIG ANSIBLE_EE_DOCKER_HOST
COLLECTION_PYTHON_BOUNDARY_ROOT ?= collections/ansible_collections
COLLECTION_PYTHON_BOUNDARY_INVENTORY ?= config/ansible/collection-python-boundary-inventory.json
COLLECTION_PYTHON_BOUNDARY_STRICT_ZERO ?= 0
RESOURCE_OWNERSHIP_ROOT ?= .
RESOURCE_OWNERSHIP_PATHS ?=
RESOURCE_OWNERSHIP_INVENTORY ?= config/resource_ownership_inventory.json
SECRETS_EXCLUDE_FILES ?= sandboxcom_github_rsa|sandboxcom_github_rsa.pub|^config/resource_ownership_inventory\.json$$
RESOURCE_OWNERSHIP_WRITE ?= 0

validate-ansible-runtime-boundary:
	@$(UV) run python scripts/ansible_runtime_artifacts.py validate

update-ansible-runtime-lock:
	@$(UV) run python scripts/ansible_runtime_artifacts.py write-lock

ANSIBLE_COLLECTION_STAGE_MODE ?= offline
ANSIBLE_COLLECTION_STAGE_LOCK ?= config/ansible/collections.lock.json
ANSIBLE_COLLECTION_STAGE_LOCK_SHA256 ?=
ANSIBLE_COLLECTION_STAGE_REQUIREMENTS ?= config/ansible/requirements.yml
ANSIBLE_COLLECTION_STAGE_ARTIFACT_ROOT ?= dist/collections/third-party
ANSIBLE_COLLECTION_STAGE_ORIGIN ?= https://galaxy.ansible.com/
ANSIBLE_COLLECTION_STAGE_TIMEOUT_SECONDS ?= 120
ANSIBLE_COLLECTION_STAGE_VALIDATE_ONLY ?= 1
export GLUDD_COLLECTION_STAGE_MODE := $(value ANSIBLE_COLLECTION_STAGE_MODE)
export GLUDD_COLLECTION_STAGE_LOCK := $(value ANSIBLE_COLLECTION_STAGE_LOCK)
export GLUDD_COLLECTION_STAGE_LOCK_SHA256 := $(value ANSIBLE_COLLECTION_STAGE_LOCK_SHA256)
export GLUDD_COLLECTION_STAGE_REQUIREMENTS := $(value ANSIBLE_COLLECTION_STAGE_REQUIREMENTS)
export GLUDD_COLLECTION_STAGE_ARTIFACT_ROOT := $(value ANSIBLE_COLLECTION_STAGE_ARTIFACT_ROOT)
export GLUDD_COLLECTION_STAGE_ORIGIN := $(value ANSIBLE_COLLECTION_STAGE_ORIGIN)
export GLUDD_COLLECTION_STAGE_TIMEOUT_SECONDS := $(value ANSIBLE_COLLECTION_STAGE_TIMEOUT_SECONDS)
export GLUDD_COLLECTION_STAGE_VALIDATE_ONLY := $(value ANSIBLE_COLLECTION_STAGE_VALIDATE_ONLY)
unexport ANSIBLE_COLLECTION_STAGE_MODE ANSIBLE_COLLECTION_STAGE_LOCK ANSIBLE_COLLECTION_STAGE_LOCK_SHA256
unexport ANSIBLE_COLLECTION_STAGE_REQUIREMENTS ANSIBLE_COLLECTION_STAGE_ARTIFACT_ROOT
unexport ANSIBLE_COLLECTION_STAGE_ORIGIN ANSIBLE_COLLECTION_STAGE_TIMEOUT_SECONDS ANSIBLE_COLLECTION_STAGE_VALIDATE_ONLY
.PHONY: stage-ansible-runtime-collections
stage-ansible-runtime-collections:
	@set -eu; \
	test -n "$$GLUDD_COLLECTION_STAGE_MODE" || { echo "ANSIBLE_COLLECTION_STAGE_MODE is required"; exit 2; }; \
	test -n "$$GLUDD_COLLECTION_STAGE_LOCK" || { echo "ANSIBLE_COLLECTION_STAGE_LOCK is required"; exit 2; }; \
	test -n "$$GLUDD_COLLECTION_STAGE_REQUIREMENTS" || { echo "ANSIBLE_COLLECTION_STAGE_REQUIREMENTS is required"; exit 2; }; \
	test -n "$$GLUDD_COLLECTION_STAGE_ARTIFACT_ROOT" || { echo "ANSIBLE_COLLECTION_STAGE_ARTIFACT_ROOT is required"; exit 2; }; \
	test -n "$$GLUDD_COLLECTION_STAGE_ORIGIN" || { echo "ANSIBLE_COLLECTION_STAGE_ORIGIN is required"; exit 2; }; \
	test -n "$$GLUDD_COLLECTION_STAGE_TIMEOUT_SECONDS" || { echo "ANSIBLE_COLLECTION_STAGE_TIMEOUT_SECONDS is required"; exit 2; }; \
	if [ "$$GLUDD_COLLECTION_STAGE_VALIDATE_ONLY" = "0" ] && [ "$$GLUDD_COLLECTION_STAGE_MODE" != "refresh" ]; then \
		test -n "$$GLUDD_COLLECTION_STAGE_LOCK_SHA256" || { echo "ANSIBLE_COLLECTION_STAGE_LOCK_SHA256 is required for reviewed execution"; exit 2; }; \
	fi; \
	case "$$GLUDD_COLLECTION_STAGE_VALIDATE_ONLY" in \
		0) set -- ;; 1) set -- --validate-only ;; \
		*) echo "ANSIBLE_COLLECTION_STAGE_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; \
	esac; \
	$(UV) run python scripts/ansible_runtime_artifacts.py stage-collections \
		--collection-stage-mode "$$GLUDD_COLLECTION_STAGE_MODE" \
		--collection-lock "$$GLUDD_COLLECTION_STAGE_LOCK" \
		--collection-lock-sha256 "$$GLUDD_COLLECTION_STAGE_LOCK_SHA256" \
		--collection-requirements "$$GLUDD_COLLECTION_STAGE_REQUIREMENTS" \
		--artifact-root "$$GLUDD_COLLECTION_STAGE_ARTIFACT_ROOT" \
		--galaxy-origin "$$GLUDD_COLLECTION_STAGE_ORIGIN" \
		--collection-timeout-seconds "$$GLUDD_COLLECTION_STAGE_TIMEOUT_SECONDS" "$$@"

ANSIBLE_EE_COLLECTION_ROOT ?= .gludd/ansible-ee-staged-collections
ANSIBLE_EE_RESOLUTION_OUTPUT ?= .gludd/ansible-ee-resolved-requirements.txt
ANSIBLE_EE_RESOLUTION_VALIDATE_ONLY ?= 1
export GLUDD_EE_RESOLUTION_COLLECTION_ROOT := $(value ANSIBLE_EE_COLLECTION_ROOT)
export GLUDD_EE_RESOLUTION_OUTPUT := $(value ANSIBLE_EE_RESOLUTION_OUTPUT)
export GLUDD_EE_RESOLUTION_VALIDATE_ONLY := $(value ANSIBLE_EE_RESOLUTION_VALIDATE_ONLY)
unexport ANSIBLE_EE_COLLECTION_ROOT ANSIBLE_EE_RESOLUTION_OUTPUT ANSIBLE_EE_RESOLUTION_VALIDATE_ONLY
resolve-ansible-runtime-requirements:
	@set -eu; \
	case "$$GLUDD_EE_RESOLUTION_VALIDATE_ONLY" in 0|1) ;; *) echo "ANSIBLE_EE_RESOLUTION_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac; \
	test -n "$$GLUDD_EE_RESOLUTION_COLLECTION_ROOT" || { echo "ANSIBLE_EE_COLLECTION_ROOT is required"; exit 2; }; \
	test -n "$$GLUDD_EE_RESOLUTION_OUTPUT" || { echo "ANSIBLE_EE_RESOLUTION_OUTPUT is required"; exit 2; }; \
	if [ "$$GLUDD_EE_RESOLUTION_VALIDATE_ONLY" = "1" ]; then set -- --validate-only; else set --; fi; \
	$(UV) run python scripts/ansible_runtime_artifacts.py resolve-requirements \
		--collection-root "$$GLUDD_EE_RESOLUTION_COLLECTION_ROOT" \
		--resolution-output "$$GLUDD_EE_RESOLUTION_OUTPUT" "$$@"

ANSIBLE_EE_BASE_IMAGE_CHECK_VALIDATE_ONLY ?= 1
check-ansible-base-image:
	@case "$(ANSIBLE_EE_BASE_IMAGE_CHECK_VALIDATE_ONLY)" in 0|1) ;; *) echo "ANSIBLE_EE_BASE_IMAGE_CHECK_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@if [ "$(ANSIBLE_EE_BASE_IMAGE_CHECK_VALIDATE_ONLY)" = "1" ]; then echo "ANSIBLE_BASE_IMAGE_CHECK_VALIDATED source=quay.io/centos/centos:stream9"; else $(UV) run python scripts/ansible_runtime_artifacts.py check-base-image; fi

ANSIBLE_EE_BASE_IMAGE_REFRESH_VALIDATE_ONLY ?= 1
refresh-ansible-base-image:
	@case "$(ANSIBLE_EE_BASE_IMAGE_REFRESH_VALIDATE_ONLY)" in 0|1) ;; *) echo "ANSIBLE_EE_BASE_IMAGE_REFRESH_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@if [ "$(ANSIBLE_EE_BASE_IMAGE_REFRESH_VALIDATE_ONLY)" = "1" ]; then echo "ANSIBLE_BASE_IMAGE_REFRESH_VALIDATED source=quay.io/centos/centos:stream9"; else $(UV) run python scripts/ansible_runtime_artifacts.py refresh-base-image; fi

ANSIBLE_SEARXNG_SOURCE_INSPECT_VALIDATE_ONLY ?= 1
inspect-ansible-searxng-source:
	@case "$(ANSIBLE_SEARXNG_SOURCE_INSPECT_VALIDATE_ONLY)" in 0|1) ;; *) echo "ANSIBLE_SEARXNG_SOURCE_INSPECT_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@set -eu; revision=$$(sed -n 's|^searxng @ git+https://github.com/searxng/searxng.git@||p' config/ansible/requirements.txt); \
		test "$${#revision}" = 40; case "$$revision" in *[!0-9a-f]*) echo "invalid frozen SearXNG revision" >&2; exit 2;; esac; \
		if [ "$(ANSIBLE_SEARXNG_SOURCE_INSPECT_VALIDATE_ONLY)" = "1" ]; then \
			echo "ANSIBLE_SEARXNG_SOURCE_INSPECTION plan_only=true files=11 revision=$$revision"; exit 0; \
		fi; \
		for source_file in setup.py searx/__init__.py searx/settings_loader.py searx/version.py requirements.txt searx/settings_defaults.py searx/unixthreadname.py searx/exceptions.py searx/sxng_locales.py Dockerfile searx/settings.yml; do \
			printf '\nANSIBLE_SEARXNG_SOURCE_INSPECTION file=%s revision=%s\n' "$$source_file" "$$revision"; \
			curl --fail --silent --show-error --connect-timeout 10 --max-time 20 --proto '=https' --tlsv1.2 \
				"https://raw.githubusercontent.com/searxng/searxng/$$revision/$$source_file"; \
		done

build-ansible-execution-environment:
	@# Literal ANSIBLE_EE_RUNTIME -> GLUDD_EE_BUILD_RUNTIME; ANSIBLE_EE_IMAGE -> GLUDD_EE_BUILD_IMAGE.
	@# Literal ANSIBLE_EE_CONTEXT -> GLUDD_EE_BUILD_CONTEXT; optional Docker fields preserve inherited defaults.
	@# ANSIBLE_EE_DOCKER_CONFIG -> GLUDD_EE_BUILD_DOCKER_CONFIG; ANSIBLE_EE_DOCKER_HOST -> GLUDD_EE_BUILD_DOCKER_HOST.
	@set -eu; \
	case "$$GLUDD_EE_BUILD_VALIDATE_ONLY" in 0) set -- ;; 1) set -- --validate-only ;; \
		*) echo "ANSIBLE_EE_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; esac; \
	test -n "$$GLUDD_EE_BUILD_ARTIFACT_ROOT" || { echo "ANSIBLE_EE_ARTIFACT_ROOT is required"; exit 2; }; \
	if [ -n "$$GLUDD_EE_BUILD_DOCKER_CONFIG" ]; then export DOCKER_CONFIG="$$GLUDD_EE_BUILD_DOCKER_CONFIG"; fi; \
	if [ -n "$$GLUDD_EE_BUILD_DOCKER_HOST" ]; then export DOCKER_HOST="$$GLUDD_EE_BUILD_DOCKER_HOST"; fi; \
	$(UV) run python scripts/ansible_runtime_artifacts.py build \
		--runtime "$$GLUDD_EE_BUILD_RUNTIME" --image "$$GLUDD_EE_BUILD_IMAGE" \
		--context "$$GLUDD_EE_BUILD_CONTEXT" --artifact-root "$$GLUDD_EE_BUILD_ARTIFACT_ROOT" "$$@"

.PHONY: prepare-ansible-execution-environment-context
prepare-ansible-execution-environment-context:
	@# Literal ANSIBLE_EE_RUNTIME -> GLUDD_EE_BUILD_RUNTIME; ANSIBLE_EE_IMAGE -> GLUDD_EE_BUILD_IMAGE.
	@# Literal ANSIBLE_EE_CONTEXT -> GLUDD_EE_BUILD_CONTEXT; optional Docker fields preserve inherited defaults.
	@# ANSIBLE_EE_DOCKER_CONFIG -> GLUDD_EE_BUILD_DOCKER_CONFIG; ANSIBLE_EE_DOCKER_HOST -> GLUDD_EE_BUILD_DOCKER_HOST.
	@set -eu; \
	case "$$GLUDD_EE_BUILD_VALIDATE_ONLY" in 0) set -- ;; 1) set -- --validate-only ;; \
		*) echo "ANSIBLE_EE_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; esac; \
	test -n "$$GLUDD_EE_BUILD_ARTIFACT_ROOT" || { echo "ANSIBLE_EE_ARTIFACT_ROOT is required"; exit 2; }; \
	if [ -n "$$GLUDD_EE_BUILD_DOCKER_CONFIG" ]; then export DOCKER_CONFIG="$$GLUDD_EE_BUILD_DOCKER_CONFIG"; fi; \
	if [ -n "$$GLUDD_EE_BUILD_DOCKER_HOST" ]; then export DOCKER_HOST="$$GLUDD_EE_BUILD_DOCKER_HOST"; fi; \
	$(UV) run python scripts/ansible_runtime_artifacts.py prepare-context \
		--runtime "$$GLUDD_EE_BUILD_RUNTIME" --image "$$GLUDD_EE_BUILD_IMAGE" \
		--context "$$GLUDD_EE_BUILD_CONTEXT" --artifact-root "$$GLUDD_EE_BUILD_ARTIFACT_ROOT" "$$@"

verify-ansible-execution-environment:
	@case "$(ANSIBLE_EE_VALIDATE_ONLY)" in 0|1) ;; *) echo "ANSIBLE_EE_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@$(UV) run python scripts/ansible_runtime_artifacts.py verify --runtime "$(ANSIBLE_EE_RUNTIME)" --image "$(ANSIBLE_EE_IMAGE)" $(if $(filter 1,$(ANSIBLE_EE_VALIDATE_ONLY)),--validate-only,)

check-collection-python-boundary:
	@case "$(COLLECTION_PYTHON_BOUNDARY_STRICT_ZERO)" in 0|1) ;; *) echo "COLLECTION_PYTHON_BOUNDARY_STRICT_ZERO must be 0 or 1"; exit 2;; esac
	@$(UV) run python scripts/check_collection_python_boundary.py --collections-root "$(COLLECTION_PYTHON_BOUNDARY_ROOT)" --inventory "$(COLLECTION_PYTHON_BOUNDARY_INVENTORY)" $(if $(filter 1,$(COLLECTION_PYTHON_BOUNDARY_STRICT_ZERO)),--strict-zero,)

check-resource-ownership:
	@case "$(RESOURCE_OWNERSHIP_WRITE)" in 0|1) ;; *) echo "RESOURCE_OWNERSHIP_WRITE must be 0 or 1"; exit 2;; esac
	@$(UV) run python scripts/check_resource_ownership.py --root "$(RESOURCE_OWNERSHIP_ROOT)" --inventory "$(RESOURCE_OWNERSHIP_INVENTORY)" $(if $(filter 1,$(RESOURCE_OWNERSHIP_WRITE)),--write-inventory,) $(RESOURCE_OWNERSHIP_PATHS)

update-collection-python-boundary-inventory:
	@$(UV) run python scripts/check_collection_python_boundary.py --collections-root "$(COLLECTION_PYTHON_BOUNDARY_ROOT)" --inventory "$(COLLECTION_PYTHON_BOUNDARY_INVENTORY)" --write-inventory

sync-models:
	@$(PYTHON) scripts/sync_local_models.py

# Regenerate the root and every independent profile lock. The profile manager
# validates the hard line ceiling after uv has generated each artifact.
DEPENDENCY_PROFILE_RELOCK_VALIDATE_ONLY ?= 0
DEPENDENCY_PROFILE_RELOCK_PROFILE ?=
override GLUDD_DEPENDENCY_PROFILE_RELOCK_PROFILE := $(value DEPENDENCY_PROFILE_RELOCK_PROFILE)
export GLUDD_DEPENDENCY_PROFILE_RELOCK_PROFILE := $(value DEPENDENCY_PROFILE_RELOCK_PROFILE)
unexport DEPENDENCY_PROFILE_RELOCK_PROFILE
relock:
	@case "$(DEPENDENCY_PROFILE_RELOCK_VALIDATE_ONLY)" in 0|1) ;; *) echo "DEPENDENCY_PROFILE_RELOCK_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@set -eu; set --; \
	if [ -n "$$GLUDD_DEPENDENCY_PROFILE_RELOCK_PROFILE" ]; then \
		set -- --profile "$$GLUDD_DEPENDENCY_PROFILE_RELOCK_PROFILE"; \
	fi; \
	UV_NO_SYNC=0 $(UV) run --no-project --python 3.11 python scripts/dependency_profiles.py $(if $(filter 1,$(DEPENDENCY_PROFILE_RELOCK_VALIDATE_ONLY)),check,lock) --root "$(CURDIR)" --manifest config/dependency_profiles.toml --uv "$(UV)" "$$@"

node-deps-sync:
	@if [ "$(NODE_DEPS_VALIDATE_ONLY)" = "1" ]; then \
		test -f .opencode/package.json && test -f .opencode/package-lock.json; \
		node -e 'const p=require("./.opencode/package.json"),l=require("./.opencode/package-lock.json"); if (!p.devDependencies?.esbuild || l.packages?.[""]?.devDependencies?.esbuild !== p.devDependencies.esbuild) process.exit(1)'; \
		echo "NODE_DEPS_VALIDATED lock=.opencode/package-lock.json"; \
	else \
		NPM_CONFIG_USERCONFIG="$(NODE_DEPS_NPM_USERCONFIG)" NPM_CONFIG_CACHE="$(NODE_DEPS_NPM_CACHE)" NPM_CONFIG_REGISTRY="$(NODE_DEPS_NPM_REGISTRY)" NPM_CONFIG_UPDATE_NOTIFIER="$(NODE_DEPS_NPM_UPDATE_NOTIFIER)" npm ci --prefix .opencode --no-audit --no-fund; \
	fi

node-deps-relock:
	@if [ "$(NODE_DEPS_VALIDATE_ONLY)" = "1" ]; then \
		test -f .opencode/package.json && test -f .opencode/package-lock.json; \
		node -e 'const p=require("./.opencode/package.json"),l=require("./.opencode/package-lock.json"); if (!p.devDependencies?.esbuild || l.packages?.[""]?.devDependencies?.esbuild !== p.devDependencies.esbuild) process.exit(1)'; \
		echo "NODE_DEPS_RELOCK_VALIDATED lock=.opencode/package-lock.json"; \
	else \
		LOCK_TMP="$$(mktemp -d /tmp/gludd-node-lock.XXXXXX)"; \
		LOCK_TMP="$$(cd "$$LOCK_TMP" && pwd -P)"; \
		trap 'rm -rf "$$LOCK_TMP"' EXIT HUP INT TERM; \
		cp .opencode/package.json "$$LOCK_TMP/package.json"; \
		NPM_CONFIG_USERCONFIG="$(NODE_DEPS_NPM_USERCONFIG)" NPM_CONFIG_CACHE="$(NODE_DEPS_NPM_CACHE)" NPM_CONFIG_REGISTRY="$(NODE_DEPS_NPM_REGISTRY)" NPM_CONFIG_UPDATE_NOTIFIER="$(NODE_DEPS_NPM_UPDATE_NOTIFIER)" npm install --prefix "$$LOCK_TMP" --package-lock-only --no-audit --no-fund; \
		cp "$$LOCK_TMP/package-lock.json" .opencode/package-lock.json; \
	fi

node-deps-audit:
	@case "$(NODE_DEPS_AUDIT_LEVEL)" in low|moderate|high|critical) ;; *) echo "NODE_DEPS_AUDIT_LEVEL must be low, moderate, high, or critical"; exit 2;; esac
	@if [ "$(NODE_DEPS_VALIDATE_ONLY)" = "1" ]; then \
		test -f .opencode/package.json && test -f .opencode/package-lock.json; \
		node -e 'const p=require("./.opencode/package.json"),l=require("./.opencode/package-lock.json"); if (!p.devDependencies?.esbuild || l.packages?.[""]?.devDependencies?.esbuild !== p.devDependencies.esbuild) process.exit(1)'; \
		echo "NODE_DEPS_AUDIT_VALIDATED level=$(NODE_DEPS_AUDIT_LEVEL)"; \
	else \
		NPM_CONFIG_USERCONFIG="$(NODE_DEPS_NPM_USERCONFIG)" NPM_CONFIG_CACHE="$(NODE_DEPS_NPM_CACHE)" NPM_CONFIG_REGISTRY="$(NODE_DEPS_NPM_REGISTRY)" NPM_CONFIG_UPDATE_NOTIFIER="$(NODE_DEPS_NPM_UPDATE_NOTIFIER)" npm audit --prefix .opencode --audit-level="$(NODE_DEPS_AUDIT_LEVEL)"; \
	fi

freellmapi-upstream-admission:
	@case "$$FREELLMAPI_ADMISSION_LIVE" in 0|1) ;; *) echo "FREELLMAPI_ADMISSION_LIVE must be 0 or 1"; exit 2;; esac; \
		MODE=validate; \
		if [ "$$FREELLMAPI_ADMISSION_LIVE" = "1" ]; then MODE=refresh; fi; \
		$(UV) run python scripts/freellmapi_upstream_admission.py \
			--mode "$$MODE" \
			--tag "$$FREELLMAPI_ADMISSION_TAG" \
			--commit "$$FREELLMAPI_ADMISSION_COMMIT" \
			--output "$$FREELLMAPI_ADMISSION_OUTPUT" \
			--repository-root "$(CURDIR)"

freellmapi-upstream-build:
	@case "$$FREELLMAPI_BUILD_LIVE" in 0|1) ;; *) echo "FREELLMAPI_BUILD_LIVE must be 0 or 1"; exit 2;; esac; \
		MODE=validate; \
		if [ "$$FREELLMAPI_BUILD_LIVE" = "1" ]; then MODE=live; fi; \
		$(UV) run python -m scripts.freellmapi_upstream_build \
			--mode "$$MODE" \
			--candidate "$$FREELLMAPI_BUILD_CANDIDATE" \
			--plan "$$FREELLMAPI_BUILD_PLAN" \
			--report "$$FREELLMAPI_BUILD_REPORT" \
			--repository-root "$(CURDIR)"

freellmapi-three-arm-replay:
	@case "$$FREELLMAPI_THREE_ARM_MODE" in validate|replay|refresh-bundle|refresh-config) ;; *) echo "FREELLMAPI_THREE_ARM_MODE must be validate, replay, refresh-bundle, or refresh-config"; exit 2;; esac; \
		$(UV) run python -m scripts.replay_freellmapi_three_arm \
			--mode "$$FREELLMAPI_THREE_ARM_MODE" \
			--candidate "$$FREELLMAPI_THREE_ARM_CANDIDATE" \
			--plan "$$FREELLMAPI_THREE_ARM_PLAN" \
			--corpus "$$FREELLMAPI_THREE_ARM_CORPUS" \
			--report "$$FREELLMAPI_THREE_ARM_REPORT" \
			--repository-root "$(CURDIR)"

install-pip:
	@echo "install-pip is a compatibility alias for the locked development profile"
	@$(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=development DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON= DEPENDENCY_PROFILE_VALIDATE_ONLY=0

version:
	@$(UV) run python -c "from general_ludd import __version__; print(f'general-ludd-agent {__version__}')"

check-uv:
	@command -v $(UV) >/dev/null 2>&1 || (echo "uv not found"; exit 1)
	@$(UV) --version

check-pytest:
	@$(UV) run python -c "import pytest; print(f'pytest {pytest.__version__}')"

lint: check-file-line-limits
	@$(UV) run ruff check src tests

lint-python: lint

lint-make: validate-makefile

check-file-line-limits:
	@case "$(FILE_LINE_LIMIT_STAGED)" in 0|1) ;; *) echo "FILE_LINE_LIMIT_STAGED must be 0 or 1"; exit 2;; esac
	@$(UV) run python scripts/check_file_line_limits.py --root "$(CURDIR)" --config "$(FILE_LINE_LIMIT_POLICY)" $(if $(filter 1,$(FILE_LINE_LIMIT_STAGED)),--staged,)

check-duplicate-code:
	@case "$(DUPLICATE_CODE_SOURCE)" in staged|committed) ;; *) echo "DUPLICATE_CODE_SOURCE must be staged or committed"; exit 2;; esac
	@case "$(DUPLICATE_CODE_VALIDATE_ONLY)" in 0|1) ;; *) echo "DUPLICATE_CODE_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@test -n "$(strip $(DUPLICATE_CODE_CONFIG))" || { echo "DUPLICATE_CODE_CONFIG is required"; exit 2; }
	@test -n '$(strip $(_GLUDD_DUPLICATE_CODE_ENGINE_ARG))' || { echo "DUPLICATE_CODE_ENGINE is required"; exit 2; }
	@test -n "$(strip $(DUPLICATE_CODE_BASE_REF))" || { echo "DUPLICATE_CODE_BASE_REF is required"; exit 2; }
	@test -n "$(strip $(DUPLICATE_CODE_CURRENT_REF))" || { echo "DUPLICATE_CODE_CURRENT_REF is required"; exit 2; }
	@$(UV) run python scripts/check_duplicate_code.py \
		--root "$(CURDIR)" \
		--config "$(DUPLICATE_CODE_CONFIG)" \
		--engine '$(_GLUDD_DUPLICATE_CODE_ENGINE_ARG)' \
		--source "$(DUPLICATE_CODE_SOURCE)" \
		--base-ref "$(DUPLICATE_CODE_BASE_REF)" \
		--current-ref "$(DUPLICATE_CODE_CURRENT_REF)" \
		$(if $(filter 1,$(DUPLICATE_CODE_VALIDATE_ONLY)),--validate-only,)

# Bind the selected runner before recursive -e can re-export an ambient UV.
check-duplicate-code: override UV := $(UV)
test-count: override UV := $(UV)
check-duplicate-code: override MAKEFLAGS = $(MFLAGS) -- $(MAKEOVERRIDES)
test-count: override MAKEFLAGS = $(MFLAGS) -- $(MAKEOVERRIDES)

split-makefile-layout:
	@case "$(MAKEFILE_SPLIT_APPLY)" in 0|1) ;; *) echo "MAKEFILE_SPLIT_APPLY must be 0 or 1"; exit 2;; esac
	@$(UV) run python -m scripts.split_makefile --entrypoint "$(CURDIR)/Makefile" $(if $(filter 1,$(MAKEFILE_SPLIT_APPLY)),--apply,)

# AGENTS.md OD.10 fast commit preflight.  Keep this intentionally bounded:
# the release/full-suite gate remains a separate workflow.
pre-commit-check:
	@# AGENTS.md OD.10 fast pre-commit contract.
	@$(MAKE) --no-print-directory check-file-line-limits FILE_LINE_LIMIT_POLICY="$(FILE_LINE_LIMIT_POLICY)" FILE_LINE_LIMIT_STAGED=1
	@$(MAKE) --no-print-directory check-duplicate-code DUPLICATE_CODE_CONFIG="$(DUPLICATE_CODE_CONFIG)" DUPLICATE_CODE_ENGINE="$(DUPLICATE_CODE_ENGINE)" DUPLICATE_CODE_SOURCE=staged DUPLICATE_CODE_BASE_REF=HEAD DUPLICATE_CODE_CURRENT_REF=HEAD DUPLICATE_CODE_VALIDATE_ONLY=0
	@$(MAKE) --no-print-directory lint
	@$(MAKE) --no-print-directory collect-check
	@$(MAKE) --no-print-directory typecheck
	@echo "PRE-COMMIT-CHECK: PASSED"

lint-files:
	@[ -n "$$FILES" ] || { echo "Usage: make lint-files FILES=path"; exit 1; }
	@$(UV) run ruff check $$FILES

lint-docstrings:
	@if [ -z "$(DOCSTRING_FILES)" ]; then 		echo "Usage: make lint-docstrings DOCSTRING_FILES='src/general_ludd/module.py scripts/check_enforcement_floor.py'"; 		exit 2; 	fi
	@for file in $(DOCSTRING_FILES); do 		case "$$file" in 			src/general_ludd/*.py|scripts/*.py) ;; 			*) echo "ERROR: lint-docstrings only accepts tracked production Python files under src/general_ludd or scripts: $$file"; exit 2 ;; 		esac; 		if [ ! -f "$$file" ]; then echo "ERROR: docstring source file not found: $$file"; exit 2; fi; 		if ! git ls-files --error-unmatch "$$file" >/dev/null 2>&1; then 			echo "ERROR: docstring source file is not tracked: $$file"; 			exit 2; 		fi; 	done
	@$(UV) run ruff check --select D --config pyproject.toml $(DOCSTRING_FILES)

lint-markdown:
	@set -eu; \
	if [ -z "$(MARKDOWN_FILES)" ]; then \
		echo "Usage: make lint-markdown MARKDOWN_FILES='README.md docs/file.md' RUMDL_CONFIG=config/rumdl.toml [MARKDOWNLINT_CONFIG=config/rumdl.toml]"; \
		exit 2; \
	fi; \
	if [ -n "$(RUMDL_CONFIG)" ] && [ -n "$(MARKDOWNLINT_CONFIG)" ] && [ "$(RUMDL_CONFIG)" != "$(MARKDOWNLINT_CONFIG)" ]; then \
		echo "ERROR: RUMDL_CONFIG and MARKDOWNLINT_CONFIG disagree"; \
		exit 2; \
	fi; \
	config_path="$(if $(strip $(RUMDL_CONFIG)),$(strip $(RUMDL_CONFIG)),$(if $(strip $(MARKDOWNLINT_CONFIG)),$(strip $(MARKDOWNLINT_CONFIG)),config/rumdl.toml))"; \
	if [ ! -f "$$config_path" ]; then echo "ERROR: Rumdl config not found: $$config_path"; exit 2; fi; \
	if grep -En -- '<!--[[:space:]]*((rumdl|markdownlint)-|prettier-ignore)' $(MARKDOWN_FILES); then \
		echo "ERROR: inline Markdown lint directives are forbidden; configure the tracked Rumdl policy instead"; \
		exit 2; \
	fi; \
	if [ ! -x ".venv/bin/rumdl" ]; then \
		echo "ERROR: locked Rumdl executable not found. Run: make sync DEPENDENCY_PROFILE_SET=development DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON= DEPENDENCY_PROFILE_VALIDATE_ONLY=0"; \
		exit 2; \
	fi; \
	.venv/bin/rumdl check --config "$$config_path" $(MARKDOWN_FILES)

lint-css:
	@if [ -z "$(CSS_FILES)" ] || [ -z "$(ESLINT_CSS_CONFIG)" ]; then \
		echo "Usage: make lint-css CSS_FILES='path/to/file.css' ESLINT_CSS_CONFIG=config/eslint-css.config.mjs"; \
		exit 2; \
	fi
	@if [ ! -f "$(ESLINT_CSS_CONFIG)" ]; then echo "ERROR: ESLint CSS config not found: $(ESLINT_CSS_CONFIG)"; exit 2; fi
	@if [ ! -x ".opencode/node_modules/.bin/eslint" ]; then \
		echo "INFO: locked ESLint CSS linter not found; syncing locked Node deps"; \
		$(MAKE) node-deps-sync NODE_DEPS_VALIDATE_ONLY=0 NODE_DEPS_NPM_USERCONFIG="$(NODE_DEPS_NPM_USERCONFIG)" NODE_DEPS_NPM_CACHE="$(NODE_DEPS_NPM_CACHE)" NODE_DEPS_NPM_REGISTRY="$(NODE_DEPS_NPM_REGISTRY)" NODE_DEPS_NPM_UPDATE_NOTIFIER="$(NODE_DEPS_NPM_UPDATE_NOTIFIER)" || { echo "ERROR: locked ESLint CSS linter is unavailable and node-deps-sync failed"; exit 2; }; \
	fi
	@.opencode/node_modules/.bin/eslint --no-error-on-unmatched-pattern --config "$(ESLINT_CSS_CONFIG)" $(CSS_FILES)

lint-fix:
	@$(UV) run ruff check --fix --unsafe-fixes src tests

lint-fix-files:
	@[ -n "$$FILES" ] || { echo "Usage: make lint-fix-files FILES=path"; exit 1; }
	@$(UV) run ruff check --fix $$FILES

fix-logger-imports:
	@$(UV) run python scripts/add_missing_logger_imports.py \
		src/general_ludd/connectors/appdynamics.py \
		src/general_ludd/connectors/aws_observability.py \
		src/general_ludd/connectors/cloudflare.py \
		src/general_ludd/connectors/containerd.py \
		src/general_ludd/connectors/datadog.py \
		src/general_ludd/connectors/dmesg.py \
		src/general_ludd/connectors/docker_engine.py \
		src/general_ludd/connectors/grafana_oncall.py \
		src/general_ludd/connectors/graphite.py \
		src/general_ludd/connectors/influxdb.py \
		src/general_ludd/connectors/journald.py \
		src/general_ludd/connectors/kafka_exporter.py \
		src/general_ludd/connectors/kubernetes.py \
		src/general_ludd/connectors/local_files.py \
		src/general_ludd/connectors/mac_unified_log.py \
		src/general_ludd/connectors/macos_log.py \
		src/general_ludd/connectors/nats.py \
		src/general_ludd/connectors/openshift.py \
		src/general_ludd/connectors/opentsdb.py \
		src/general_ludd/connectors/osquery.py \
		src/general_ludd/connectors/parca.py \
		src/general_ludd/connectors/podman.py \
		src/general_ludd/connectors/proc_sys.py \
		src/general_ludd/connectors/prom_scrape.py \
		src/general_ludd/connectors/pyroscope.py \
		src/general_ludd/connectors/rabbitmq.py \
		src/general_ludd/connectors/rollbar.py \
		src/general_ludd/connectors/thanos.py \
		src/general_ludd/connectors/victoriametrics.py \
		src/general_ludd/connectors/windows_event_log.py \
		src/general_ludd/connectors/zabbix.py \
		src/general_ludd/connectors/zipkin.py

ruff-audit:
	@$(UV) run python scripts/ruff_plugins/return_type_checker.py

typecheck:
	@$(UV) run mypy -p general_ludd
	@$(UV) run mypy --config-file config/mypy-tests.toml tests/unit/test_config_gaps.py

# Pre-commit needs the full package analysis without writing a disposable cache.
# os.devnull is /dev/null on Unix and nul on Windows; make selects equivalently.
_precommit-mypy:
	@$(UV) run mypy --cache-dir="$(MYPY_NULL_CACHE)" -p general_ludd

test:
	@if [ -n "$(TESTFILE)" ]; then \
		BT="/tmp/gludd-test-$${ID:-$$$$}"; rm -rf "$$BT"; $(UV) run python -m pytest $(TESTFILE) $(PYTEST_VERBOSITY) $(PYTEST_ARGS) --basetemp="$$BT"; RC=$$?; rm -rf "$$BT"; exit $$RC; \
	else \
		BT="/tmp/gludd-test-$${ID:-$$$$}"; rm -rf "$$BT"; $(UV) run python -m pytest tests/ --cov=general_ludd --cov-report=term-missing --cov-report=xml $(_XD) $(PYTEST_VERBOSITY) $(PYTEST_ARGS) --basetemp="$$BT"; RC=$$?; rm -rf "$$BT"; exit $$RC; \
	fi

test-unit:
	@if [ -n "$(TESTFILE)" ]; then \
		BT="/tmp/gludd-testunit-$${ID:-$$$$}"; rm -rf "$$BT"; $(UV) run python -m pytest $(TESTFILE) $(_XD) $(PYTEST_VERBOSITY) $(PYTEST_ARGS) --basetemp="$$BT"; RC=$$?; rm -rf "$$BT"; exit $$RC; \
	elif [ "$$GLUDD_E2E_ACTIVE" = "1" ]; then \
		echo "nested full test-unit blocked during E2E"; exit 0; \
	else \
		BT="/tmp/gludd-testunit-$${ID:-$$$$}"; rm -rf "$$BT"; $(UV) run python -m pytest tests/unit/ $(_XD) $(PYTEST_VERBOSITY) $(PYTEST_ARGS) --basetemp="$$BT"; RC=$$?; rm -rf "$$BT"; exit $$RC; \
	fi
