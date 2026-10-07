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
GATE_TIMEOUT ?= 3600
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
RELEASE_COMPLETED_STAGES ?=
RELEASE_OBSERVATIONS ?=
REVIEWED_HEAD_INTEGRATION_RECEIPT ?=
REVIEWED_HEAD_RECEIPT_MANIFEST ?=
REVIEWED_HEAD_GATE_ATTESTATION ?=
REVIEWED_HEAD_RECEIPT_OUTPUT ?=
REVIEWED_HEAD_RECEIPT_REPO_ROOT ?= .
REVIEWED_HEAD_RECEIPT_VALIDATE_ONLY ?= 0
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
MARKDOWNLINT_CONFIG ?= config/markdownlint-cli2.jsonc
DOCSTRING_FILES ?=
FILE_LINE_LIMIT_POLICY ?= config/file_line_limits.json
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
_NO_UV_SYNC_GOALS := \
    worktree-state all-worktree-state main-worktree-state worktree-guard main-worktree-guard \
    release-worktree-guard status-claim-guard workflow-state workflow-gate commit-ready gha-ready merge-ready \
    git-where git-show-file-to repo-status git-status git-remote-sandboxcom git-pull-sandboxcom git-fetch-sandboxcom verify-remote git-patch-equivalence \
    git-branch git-checkout git-add git-merge git-merge-nc git-merge-abort git-rebase-abort git-rebase-continue git-rebase-skip git-uncommit-last \
    git-cherry-pick git-cherry-pick-list git-cherry-pick-continue git-cherry-pick-skip git-cherry-pick-abort \
    ci-remotes ci-diff-since-remote ci-head-compare ci-remote-head-guard ci-trigger ci-shards-log-context \
    git-push-committed-head-nv ci-trigger-committed-head ci-push-committed-head git-push-current-head-to-master-nv \
    grep search show-lines cat-file copy-file mkdir-p write-text append-text replace-lines replace-text replace-all-text write-text-b64 replace-text-b64 rm-files \
    disk-cleanup-preflight check-disk check-disk-classification disk disk-check disk-guard cache-disk cache-clean disk-user-caches audit-home-tmp \
    cache-resource-inventory cache-resource-remove tmp-gludd-usage tmp-gludd-worktree-usage \
    tmp-gludd-clean-ci-shards tmp-gludd-clean-ci-shards-now tmp-gludd-clean-orphan-worktrees-now \
    clean clean-artifacts clean-worktree-venvs clean-worktree-caches active-work-status ps agent-worktree agent-worktree-base azure-self-improve-auth-args \
    development-merge-forward development-merge-forward-batch uv-cache-path
ifneq (,$(filter $(_NO_UV_SYNC_GOALS),$(MAKECMDGOALS)))
override UV := echo
else
UV := uv
endif
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
        init sync uv-cache-path migrate-up relock node-deps-sync node-deps-relock node-deps-audit check-ansible-base-image refresh-ansible-base-image install-pip lint lint-files lint-markdown lint-docstrings lint-fix check-file-line-limits split-makefile-layout test test-unit test-unit-shards test-ci-dual-track-local test-specific test-specific-pyver test-files test-count test-integration test-e2e \
         test-guardrails test-scripts test-db test-live-zai test-tui-daemon test-batch test-bg test-bg-runner \
         test-games test-multi-model-pipeline test-local-model-pipeline test-project-type-pipeline game-audit gen-mcp-tools gen-mcp-tool-ref mcp-docs-check \
        typecheck _precommit-mypy setup-dirs setup-venv clean healthcheck \
        bootstrap skeleton version check-uv check-pytest \
        ansible-syntax ansible-lint-playbooks ansible-collection-test playbook-list \
        git-status git-init git-add git-commit git-log git-diff git-reset \
        git-branch git-checkout git-merge git-staged git-stash git-stash-pop \
        git-merge-abort resolve-development-conflicts git-rebase-abort git-rebase-continue git-rebase-skip git-uncommit-last git-reset-hard git-cherry-pick git-cherry-pick-list \
        submodule-init submodule-update submodule-status submodule-pin \
        repo-status repo-diff repo-staged repo-log \
        feature-start feature-done test-and-commit preflight \
        agent-worktree agent-worktree-base agent-merge agent-cleanup agent-worktree-list \
        agent-worktree-dev agent-merge-dev \
        self-improve-local-proposal azure-self-improve-auth-args azure-self-improve-live-proof azure-accelerator-role-apply azure-accelerator-role-args azure-accelerator-role-update-args azure-accelerator-auth-args azure-accelerator-auth-store azure-containerapp-environment-bootstrap-args azure-accelerator-auth-check azure-containerapp-preflight azure-containerapp-terraform-phase azure-containerapp-live-proof test-azure-containerapp-coverage test-self-improve test-self-improve-all test-self-improve-acceptance-matrix test-self-improve-private-policy \
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
        audit-messages qa validate collect-check pre-commit-check coverage-files observed-status observed-tail gate gate-refresh gate-lite smoke install-hooks install-workflow-hook feature-spec-inventory check-generated-artifact-hygiene \
        status-snapshot audit-evidence deps-audit core-dependency-ownership-refresh dogfood-features ruff-audit check-make-help \
        skill-install skill-list bootstrap-skills scan-tool-usage \
         scan-secrets scan-secrets-baseline clean-untracked clean-hooks clean-plugins \
         secrets-scrub secrets-scan secrets-baseline secrets-baseline-check security-audit clean-artifacts health-check \
        git-remote-sandboxcom git-push-sandboxcom git-pull-sandboxcom git-fetch-sandboxcom \
        git-add-all help grep scan-secrets-fresh untrack \
         git-tracked-keys git-ls-tracked git-history-file dist-path-check git-is-ancestor git-revlist-count git-patch-equivalence branches-unmerged-development branch-reconciliation-inventory branch-reconciliation-summary check-git-hygiene cache-disk cache-clean disk-user-caches cache-resource-inventory cache-resource-remove rm-files commit-and-ship commit-and-ship-push compute-model-hashes \
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
log-agent-result disk-guard disk-check disk-cleanup-preflight check-disk check-disk-classification check-system-load disk tmp-gludd-usage tmp-gludd-clean-ci-shards tmp-gludd-clean-ci-shards-now tmp-gludd-clean-orphan-worktrees-now \
        tmp-gludd-worktree-usage clean-worktree-venvs clean-worktree-caches \
        searx-up searx-down searx-test searx-start searx-stop searx-status searx-install \
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
	@echo "Usage: make [target]"
	@echo ""
	@echo "  --- Setup ---"
	@echo "  init                  Set up project (dirs + deps)"
	@echo "  sync                  Atomically sync an explicit locked dependency profile set (DEPENDENCY_PROFILE_*)"
	@echo "  uv-cache-path         Print the sandbox-writable Gludd uv cache path"
	@echo "  migrate-up            Upgrade an explicit database URL to a revision (MIGRATE_DATABASE_URL, MIGRATE_REVISION)"
	@echo "  sync-llama-cpp        Sync locked local-inference extra (SYNC_LLAMA_CPP_VALIDATE_ONLY=0|1)"
	@echo "  test-local-model-inference  Locked optional-runtime smoke (LOCAL_MODEL_INFERENCE_MODEL_PATH, LOCAL_MODEL_INFERENCE_VALIDATE_ONLY=0|1)"
	@echo "  clean-e2e-small-model       Remove only the reproducible /tmp GGUF materialization (E2E_SMALL_MODEL_CLEAN_VALIDATE_ONLY=0|1)"
	@echo "  clean-hf-cache             Diagnose/reclaim Gludd-owned unleased models (CLEAN_HF_CACHE_ROOT, CLEAN_HF_CACHE_REQUIRED_BYTES, CLEAN_HF_CACHE_VALIDATE_ONLY=0|1)"
	@echo "  validate-ansible-runtime-boundary  Validate split core/controller/managed-host artifacts"
	@echo "  build-ansible-execution-environment  Build the locked controller EE (ANSIBLE_EE_*)"
	@echo "  verify-ansible-execution-environment Verify one digest-addressed controller EE (ANSIBLE_EE_*)"
	@echo "  check-collection-python-boundary Enforce exact/strict-zero collection migration inventory"
	@echo "  check-resource-ownership Enforce exact application acquisition-to-teardown evidence (RESOURCE_OWNERSHIP_*)"
	@echo "  update-ansible-runtime-lock Refresh deterministic EE input hashes"
	@echo "  check-ansible-base-image Prove the exact EE base manifest is still served (ANSIBLE_EE_BASE_IMAGE_CHECK_VALIDATE_ONLY=0|1)"
	@echo "  refresh-ansible-base-image Resolve, verify, and atomically pin the supported EE base (ANSIBLE_EE_BASE_IMAGE_REFRESH_VALIDATE_ONLY=0|1)"
	@echo "  update-collection-python-boundary-inventory Refresh exact legacy migration inventory"
	@echo "  deps-audit            Fail-closed Python dependency truth audit"
	@echo "  core-dependency-ownership-refresh  Reconcile direct-core ownership (CORE_DEPENDENCY_OWNERSHIP_REFRESH_VALIDATE_ONLY=0|1)"
	@echo "  node-deps-sync        Install locked Node deps (NODE_DEPS_VALIDATE_ONLY, NODE_DEPS_NPM_USERCONFIG, NODE_DEPS_NPM_CACHE, NODE_DEPS_NPM_REGISTRY, NODE_DEPS_NPM_UPDATE_NOTIFIER=true|false)"
	@echo "  node-deps-relock      Regenerate Node lock (NODE_DEPS_VALIDATE_ONLY, NODE_DEPS_NPM_USERCONFIG, NODE_DEPS_NPM_CACHE, NODE_DEPS_NPM_REGISTRY, NODE_DEPS_NPM_UPDATE_NOTIFIER=true|false)"
	@echo "  node-deps-audit       Audit locked Node deps (NODE_DEPS_NPM_UPDATE_NOTIFIER=true|false plus NODE_DEPS_AUDIT_LEVEL=low|moderate|high|critical)"
	@echo "  freellmapi-upstream-admission  Validate/refresh the non-runnable upstream candidate (FREELLMAPI_ADMISSION_TAG, FREELLMAPI_ADMISSION_COMMIT, FREELLMAPI_ADMISSION_LIVE=0|1, FREELLMAPI_ADMISSION_OUTPUT)"
	@echo "  freellmapi-upstream-build  Validate/run the exact-source upstream suite (FREELLMAPI_BUILD_LIVE=0|1, FREELLMAPI_BUILD_CANDIDATE, FREELLMAPI_BUILD_PLAN, FREELLMAPI_BUILD_REPORT)"
	@echo "  freellmapi-three-arm-replay  Build/validate/replay exact v0.11.1 without promotion (FREELLMAPI_THREE_ARM_MODE, FREELLMAPI_THREE_ARM_CANDIDATE, FREELLMAPI_THREE_ARM_PLAN, FREELLMAPI_THREE_ARM_CORPUS, FREELLMAPI_THREE_ARM_REPORT)"
	@echo "  bootstrap             init + lint + test + healthcheck"
	@echo "  install-hooks         Install pre-commit hooks (secrets, lint, collect)"
	@echo "  install-workflow-hook Validate/install the tracked GitHub workflow YAML hook (INSTALL_WORKFLOW_HOOK_VALIDATE_ONLY)"
	@echo "  install-bats          Install bats-core via Homebrew"
	@echo ""
	@echo "  --- Quality ---"
	@echo "  gate-all                full CI-matching gate: all unit + integration + e2e + molecule tests"
	@echo "  gate-full               full gate matching CI: gate-refresh + integration + e2e + molecule"
	@echo "  gate-release-phases     bounded integration + e2e + molecule release phases (GATE_RELEASE_*)"
	@echo "  test-atomic-validate    verify atomic target creation with tempfile validation"
	@echo "  gate-check              Run gate check"
	@echo "  lint                  Run ruff linter"
	@echo "  lint-python           Run the canonical Python Ruff gate (application + tests)"
	@echo "  lint-make             Run duplicate-target, parity, and Make dry-run validation"
	@echo "  lint-files            Run ruff linter on FILES only"
	@echo "  lint-markdown         Run locked markdownlint-cli2 (MARKDOWN_FILES, MARKDOWNLINT_CONFIG)"
	@echo "  lint-docstrings       Run locked Ruff docstring rules on DOCSTRING_FILES"
	@echo "  check-file-line-limits  Require every tracked text file to stay below 2500 lines (FILE_LINE_LIMIT_POLICY)"
	@echo "  split-makefile-layout  Validate/apply the ordered make/*.mk layout (MAKEFILE_SPLIT_APPLY=0|1)"
	@echo "  vendor-presentation-assets  Validate/refresh pinned Reveal.js assets (PRESENTATION_VENDOR_VALIDATE_ONLY=0|1)"
	@echo "  presentation-browser-install Check/install pinned Chromium + WebKit (PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY=0|1)"
	@echo "  presentation-browser-install-deps Validate/install Playwright browser system libraries (PRESENTATION_BROWSER_DEPS_VALIDATE_ONLY=0|1)"
	@echo "  presentation-browser-test   Validate/run bounded Chromium + WebKit acceptance (PRESENTATION_BROWSER_VALIDATE_ONLY=0|1)"
	@echo "  presentation-safari-test    Validate/run bounded native Safari smoke (PRESENTATION_SAFARI_VALIDATE_ONLY=0|1)"
	@echo "  presentation-pages-probe    Compare the public Pages artifact with an expected exact SHA"
	@echo "  lint-fix              Run ruff with auto-fix"
	@echo "  lint-fix-files        Run ruff auto-fix on FILES only"
	@echo "  typecheck             Run mypy"
	@echo "  typecheck-scope       Run strict mypy on explicit FILES without unrelated override noise"
	@echo "  check-types           Flag Any usage in Python annotations (tight types)"
	@echo "  check-types-baseline  Same scan, tolerating config/type_any_baseline.txt"
	@echo "  healthcheck           Verify imports work"
	@echo "  qa                    Run lint + typecheck + test + healthcheck"
	@echo "  validate              Full validation (lint + typecheck + test + ansible + healthcheck)"
	@echo "  add-target            Add a new Makefile target with auto-categorization"
	@echo "  edit-target           Edit an existing Makefile target recipe"
	@echo "  edit-makefile-target  Edit a Makefile target definition via a file"
	@echo "  validate-makefile     Validate Makefile targets for duplicates"
	@echo "  gate                  Full gate: lint + typecheck + collect-check + test"
	@echo "  gate-background           Launch one identity-tracked gate in an isolated session"
	@echo "  gate-background-observed  Launch the detached gate and keep its automation owner alive while polling"
	@echo "  gate-refresh          Refresh fast phases; stream fallback test node IDs (GATE_REFRESH_VALIDATE_ONLY=0|1)"
	@echo "  gate-lite             Local validation (lint+typecheck+collect+smoke+unit@2w); no OOM"
	@echo "  gate-audit            Gate + coverage audit (85% per-file threshold)"
	@echo "  coverage-files        Observable targeted coverage (COVERAGE_* plus OBSERVED_ROOT, OBSERVED_HEARTBEAT_SECS, OBSERVED_QUIET_SECS, OBSERVED_MAX_SECS, OBSERVED_RETAIN_RUNS)"
	@echo "  observed-status       Print current/exact retained command status (OBSERVED_LABEL, RUN_ID, OBSERVED_ROOT, OBSERVED_STALE_SECS)"
	@echo "  observed-tail         Print bounded current/exact retained log tail (OBSERVED_LABEL, RUN_ID, OBSERVED_ROOT, OBSERVED_TAIL_LINES)"
	@echo "  run-watched           Observe one bounded command (CMD, OBSERVED_LABEL, RUN_ID, STALL_SECS, MAX_SECS, LOG, OBSERVED_ROOT, OBSERVED_HEARTBEAT_SECS, OBSERVED_RETAIN_RUNS)"
	@echo "  gate-async            Launch gate detached (non-blocking); writes .gate-status"
	@echo "  gate-status           Print current .gate-status (RUNNING/PASS/FAIL)"
	@echo "  gate-tail             Print a bounded latest gate-log snapshot (GATE_TAIL_LINES=80)"
	@echo "  gate-lite-tail        Print a bounded latest gate-lite-log snapshot (GATE_TAIL_LINES=80)"
	@echo "  triage-failures       Incrementally group streamed failures (LOG, TRIAGE_STATE, TRIAGE_FORMAT)"
	@echo "  collect-check         Fast collection-error gate"
	@echo "  pre-commit-check      Fast lint + collection + typecheck commit preflight"
	@echo "  test-nodeids          Print bounded pytest node-id slice (START/LIMIT/TESTPATH)"
	@echo "  test-xdist-trace      Run pytest with durable xdist worker/node/resource trace (LOG, TESTPATH, PYTEST_ARGS, RUN_ID)"
	@echo "  test-xdist-trace-summary  Summarize one durable trace/run (LOG, RUN_ID)"
	@echo "  preflight             Preflight quality gate (coverage, lint, mypy, templates, etc.)"
	@echo "  check-make-help       Verify every public Makefile target is listed by make help"
	@echo "  codemod-lean-enforcement-plugins Extract bulky enforcement implementations from counted plugin entrypoints"
	@echo "  check-no-prompt-prone-edit-tools  Enforce make-target-only edit workflow"
	@echo "  codex-system-skill-read  Print a codex system skill's SKILL.md (SKILL=name [CODEX_SKILLS_ROOT=path])"
	@echo "  fix-init-drift        Fix __init__.py drift: docstrings, empty namespace inits, unsorted __all__"
	@echo "  fix-docs-drift        Fix mechanical markdown drift: whitespace, fences, stale audit links"
	@echo "  report-docs-drift     Enumerate hand-fixable markdown issues (tables, tabs, headers, links)"
	@echo "  feature-spec-inventory  Inventory all Gludd feature specs + OpenCode behavioral specs (FORMAT=human|json)"
	@echo "  migrate-test-env-writes  Rewrite test environment mutations to the guarded helper"
	@echo "  write-text-b64        Write FILE from base64 TEXT_B64 without shell quoting loss"
	@echo "  replace-text-b64      Exact old/new base64 replacement via scripts/replace_text.py"
	@echo "  mkdir-p               Create an allowed workspace or /tmp/gludd-* directory"
	@echo "  replace-lines         Replace an allowed file line range from NEW_FILE"
	@echo "  worktree-state        Emit path-qualified current git worktree state as JSON"
	@echo "  all-worktree-state    Emit path-qualified state for every registered worktree"
	@echo "  main-worktree-state   Emit canonical main checkout state as JSON"
	@echo "  worktree-guard        Fail if the current worktree is dirty"
	@echo "  main-worktree-guard   Fail if /Users/shawnwilson/gludd is dirty"
	@echo "  release-worktree-guard  Emit release evidence only when current and main worktrees are clean"
	@echo "  status-claim-guard    Emit clean tokens only when current and main worktrees are clean"
	@echo "  workflow-state        Emit local/remote/GHA git state-machine evidence as JSON"
	@echo "  workflow-gate         Fail if local workflow state is unsafe for release evidence"
	@echo "  commit-ready          Fail if current work is not clean enough to be committed/tested"
	@echo "  gha-ready             Fail if remote CI would not run the current committed HEAD"
	@echo "  merge-ready           Fail if development cannot merge to master without topology repair"
	@echo "  codemod-lean-enforcement-plugins  Slim counted enforcement plugin entrypoints"
	@echo "  sast                  Run bandit SAST"
	@echo "  sbom                  Generate CycloneDX SBOM"
	@echo "  pip-audit             Audit dependencies for vulnerabilities"
	@echo "  pip-audit-gate        Fail on every non-adjudicated Python advisory"
	@echo "  security              Full security: sast + sbom + Python/Node dependency audits"
	@echo "  test-unit             Unit tests only"
	@echo "  test-unit-shards      Unit tests in bounded serial shards (SHARDS=12 SHARD=1)"
	@echo "  test-ci-dual-track-local  Canonical local all-shard evidence (DUAL_TRACK_LOCAL_VALIDATE_ONLY, PYTEST_ARGS, MAX_FILES_PER_BATCH)"
	@echo "  test-integration      Integration tests"
	@echo "  integration-health   Run the observable integration gate with isolated temp paths"
	@echo "  test-e2e              End-to-end tests"
	@echo "  test-e2e-azure        Azure E2E — env-pointer (CI-friendly)"
	@echo "  test-e2e-azure-provision  Azure full-provision E2E (opt-in, costly)"
	@echo "  test-e2e-azure-provision-sourced  Source AZURE_E2E_ENV_FILE, then provision (AZURE_E2E_VALIDATE_ONLY=0|1)"
	@echo "  test-e2e-aws          AWS E2E — env-pointer (CI-friendly)"
	@echo "  test-e2e-gcp          GCP E2E — env-pointer (CI-friendly)"
	@echo "  test-e2e-runpod       RunPod E2E — env-pointer (CI-friendly)"
	@echo "  test-e2e-providers    All E2E provider tests"
	@echo "  test-e2e-games        Game generation E2E — AI generates games, compares frames (no Azure provision)"
	@echo "  test-e2e-games-local  Game unit tests only — video compare, game gen, no Azure needed"
	@echo "  test-e2e-games-local-model  Hermetic/managed/external game E2E (LOCAL_MODEL_E2E_MODE, LOCAL_MODEL_PATH, LOCAL_MODEL_BASE_URL, LOCAL_MODEL_NAME, LOCAL_MODEL_KEY, LOCAL_MODEL_GAME, PYTEST_ARGS)"
	@echo "  test-e2e-game-pipeline  Full game-dev pipeline — all 24 models × 4 games (CI_SAFE=1 for CI-safe subset)"
	@echo "  game-reference-preflight  Acquire/verify approved FPS clips before Azure provisioning"
	@echo "  test-e2e-games-provision  Source AZURE_E2E_ENV_FILE; Azure game E2E (GAME_E2E_TIMEOUT_SECS>=3600)"
	@echo "  e2e-audit-azure     List all E2E runs with PASS/FAIL/RUNNING status"
	@echo "  e2e-latest-log      Show exit code and error summary for latest E2E run"
	@echo "  azure-cleanup-e2e   Delete gludd-gpu* groups and visibly verify absence"
	@echo "  test-e2e-postgres-multiworker  Live Postgres 16 + two-worker Gunicorn acceptance"
	@echo "  podman-project-up   Start an explicit project Podman machine and wait for readiness"
	@echo "  podman-project-recreate  Recreate one gludd-namespaced Podman test machine"
	@echo "  podman-project-delete  Delete one gludd-namespaced Podman machine with bounded progress"
	@echo "  test-multi-model-pipeline  All multi-model pipeline E2E + integration tests"
	@echo "  test-local-model-pipeline  Local model pipeline E2E tests"
	@echo "  test-project-type-pipeline E2E: test_project_type_pipeline.py"
	@echo "  test-opencode-e2e     .opencode/ plugin load+invocation tests"
	@echo "  test-opencode-e2e-hour  1-hour E2E spawner test (TIMEOUT=3600)"
	@echo "  test-specific         Single test (TESTFILE=path::TestClass::test_name)"
	@echo "  test-specific-pyver   Single test under explicit Python (TESTFILE, PYTHON_VERSION)"
	@echo "  test-files            Multiple tests (TESTFILES=tests/unit/a.py tests/unit/b.py)"
	@echo "  grep                  Repository text search (Q=regex SEARCH_PATH=path)"
	@echo "  ci-shards-log-context Show local shard log context (LOG=.gate-logs/ci.log PATTERN=FAILED)"
	@echo "  task                  Run CMD with timeout (CMD=make test-unit, GLUDD_TASK_TIMEOUT=300)"
	@echo "  test-count            Count collected tests"
	@echo "  test-failures         Show bounded cached failures (TEST_FAILURES_CACHE, TEST_FAILURES_LIMIT)"
	@echo   provider-smoke        Run gludd smoke PROVIDER=aws SMOKE_TEST=ec2-a100 ARGS=--json
	@echo "  local-accelerator-inventory  Discover local GPU/TPU resources without provisioning"
	@echo "  mac-unified-memory-smoke  Local Apple unified-memory smoke (LIVE=1 BACKEND=mps ARGS=...)"
	@echo "  gpu-hardware-smoke        Local AMD/NVIDIA GPU smoke (LIVE=1 BACKEND=cuda|rocm ARGS=...)"
	@echo "  provider-harness      Validate Azure/RunPod credentials, billing bounds, and optional Gludd telemetry"
	@echo "  azure-harness         Azure provider harness (LIVE=1 for read-only credential check)"
	@echo "  azure-cleanup-inspect Read-only provisioning states for Gludd Azure E2E resource groups"
	@echo "  runpod-harness        RunPod provider harness (LIVE=1 for read-only credential check)"
	@echo "  test-opa-policies     Execute Rego policy tests when opa is installed"
	@echo "  check-make-target-contract  Validate target variables, help, and behavioral examples"
	@echo "  check-generated-artifact-hygiene  Validate tracked generated Markdown/JSON/YAML postconditions"
	@echo "  active-work-status    Emit auditable PIDs, gate state, hashes, and open tasks"
	@echo "  ps                    Report repository-owned test, audit, and supervisor processes"
	@echo "  list-plugins          Report the active enforcement plugin roster"
	@echo "  terminate-project-process-tree  Identity-check and preview/apply one project process tree"
	@echo "  kill-worktree-e2e    Stop one verified local E2E tree (PID=; validate with KILL_WORKTREE_E2E_VALIDATE_ONLY=1)"
	@echo "  status-snapshot       Rewrite SESSION.md gate evidence (STATUS_SNAPSHOT_VALIDATE_ONLY=1 for read-only validation)"
	@echo "  codex-stop-guard      Fail closed when tracked work remains; emit a Codex stop challenge token"
	@echo "  codex-stop-confirm    Confirm a previously issued token before recording a valid stop"
	@echo "  iam-headless-smoke    Validate least-privilege provider manifests without credentials"
	@echo "  check-task-integrity  Require changed files to map to registered tasks"
	@echo "  validate-task-ledger  Validate TASKS.md metadata and completion evidence"
	@echo "  check-dispatch-dedup Validate the persistent content-addressed dispatch ledger"
	@echo "  test-and-commit       Run tests then commit if green (MSG='msg')"
	@echo "  audit-coverage        Run coverage audit: pytest --cov + per-file threshold check"
	@echo "  test-live-zai         Live GLM model test (requires API key)"
	@echo "  test-guardrails       Test guardrail infrastructure"
	@echo "  test-install          Run install.sh bats tests"
	@echo "  test-language-expert  Language collection E2E: schema + unit + integration + coverage (>=85%)"
	@echo ""
	@echo "  --- Terraform ---"
	@echo "  tf-cache-warm         Download all providers ONCE into the shared plugin cache"
	@echo "  tf-init STACK=s/n     Init a stack using the shared cache (no re-download)"
	@echo "  tf-init-local         State-free Azure stack init (STACK, TF_INIT_LOCAL_VALIDATE_ONLY)"
	@echo "  tf-validate STACK=s/n Validate a stack against the shared cache"
	@echo "  tf-versions-check     Enforce stacks match infra/terraform/versions.tf"
	@echo "  tf-clean              Remove the shared plugin cache"
	@echo ""
	@echo "  --- Git ---"
	@echo "  ci-cancel               cancel a CI run by id — use for zombie runs blocking push"
	@echo "  (Single-source policy: features land on development first, then merge to master)"
	@echo "  git-status            Show git status"
	@echo "  git-diff              Show diff stats"
	@echo "  git-staged            Show staged changes"
	@echo "  git-log               Show recent commits"
	@echo "  git-show-commit C=<sha>  Show hash, parents, committer time, subject, and files"
	@echo "  git-show-full SHA=<sha>  Show a host-independent canonical patch"
	@echo "  git-patch-equivalence PATCH_UPSTREAM=<ref> PATCH_HEAD=<ref> PATCH_LIMIT=<n>  Compare patch identity"
	@echo "  branches-unmerged-development  List every local branch tip not reachable from development"
	@echo "  branch-reconciliation-inventory RECONCILE_TARGET=<ref> RECONCILE_LIMIT=<n> RECONCILE_AFTER=<ref|empty>  Page bounded local branch reconciliation state as JSON"
	@echo "  branch-reconciliation-summary RECONCILE_TARGET=<ref> RECONCILE_LIMIT=<n> RECONCILE_DETAILS=0|1 RECONCILE_CURRENT_ONLY=0|1 RECONCILE_QUIET_PROGRESS=0|1 RECONCILE_HEAD_SEMANTICS=0|1  Exhaustively classify and optionally summarize deduplicated heads"
	@echo "  git-add FILES='...'   Stage specific files"
	@echo "  git-add-all           Stage all changes"
	@echo "  git-commit MSG='...'  Commit staged changes"
	@echo "  commit-and-ship MSG='...'  Lint-fix, stage, and commit through the guarded shipping target"
	@echo "  commit-and-ship-push MSG='...'  Commit, push development, and request the guarded CI verdict"
	@echo "  rm-files FILES='...'  Remove only explicitly named workspace paths"
	@echo "  git-reset FILES='...' Reset to ref (soft by default)"
	@echo "  git-uncommit-last CONFIRM=1  Uncommit local HEAD while preserving all files"
	@echo "  git-branch MSG='...'  Create branch"
	@echo "  git-checkout MSG='...' Switch branch"
	@echo "  git-merge MSG='...'   Merge branch with --no-ff"
	@echo "  resolve-development-conflicts MERGE_SOURCE=<branch> APPLY=0|1  Preserve development on conflicts"
	@echo "  git-rebase-abort      Abort an in-progress rebase"
	@echo "  git-rebase-continue   Continue after resolving rebase conflicts"
	@echo "  git-rebase-skip       Skip duplicate/current rebase commit"
	@echo "  git-cherry-pick SHA=<commit> Cherry-pick a specific commit"
	@echo "  git-cherry-pick-list SHAS='a b ...' Cherry-pick commits in order"
	@echo "  feature-start MSG='...' Create and switch to feature branch"
	@echo "  feature-done MSG='...' Test, merge to master with --no-ff"
	@echo "  agent-worktree BRANCH=<name>  Isolated git worktree for a subagent (no shared-tree races)"
	@echo "  agent-worktree-base BRANCH=<name> BASE=<ref>  Isolated worktree from an explicit base ref"
	@echo "  workstream-register BRANCH=<name> WORKTREE=<path>  Protect an active logical workstream"
	@echo "  workstream-unregister BRANCH=<name>  Release a completed logical workstream"
	@echo "  wt-prune-safe         Prune clean worktrees except registered active workstreams (ACTIVE_WORKSTREAM_REGISTRY, WT_PRUNE_VALIDATE_ONLY)"
	@echo "  agent-merge BRANCH=<name>     Merge a subagent worktree branch into master (--no-ff)"
	@echo "  agent-cleanup BRANCH=<name>   Remove a subagent worktree + branch after merge"
	@echo "  agent-worktree-list           List active git worktrees"
	@echo "  self-improve-local-proposal  Owned local GGUF proposal worker (SELF_IMPROVE_MODEL_PATH/PROMPT_FILE/PROPOSAL_FILE)"
	@echo "  azure-self-improve-auth-args  Emit validated NUL arguments for one least-privilege Azure SP command"
	@echo "  azure-self-improve-live-proof  Discover, select, deploy, evaluate, and tear down one bounded Azure candidate"
	@echo "  azure-accelerator-role-apply Apply/validate the exact GPU role through Microsoft SDKs (AZURE_ACCELERATOR_*)"
	@echo "  azure-accelerator-role-args  Deprecated compatibility argv for Azure CLI role creation"
	@echo "  azure-accelerator-role-update-args  Emit validated NUL arguments to narrow an existing GPU role"
	@echo "  azure-accelerator-auth-args  Emit validated NUL arguments for its Azure SP credential command"
	@echo "  azure-containerapp-environment-bootstrap-args  Emit one operator-owned shared GPU environment deployment"
	@echo "  azure-accelerator-auth-check Secret-safe validation of Azure CLI --json-auth output"
	@echo "  azure-accelerator-auth-store Preserve stdin/source JSON as immutable protected generations"
	@echo "  azure-containerapp-preflight Traced read-only named-environment GPU sizing and quota proof"
	@echo "  azure-containerapp-terraform-phase  Owned app-only Terraform phase (AZURE_CONTAINERAPP_TF_*)"
	@echo "  azure-containerapp-live-proof  Hermetic/live bounded deploy-infer-destroy proof (AZURE_CONTAINERAPP_LIVE_PROOF_*)"
	@echo "  test-azure-containerapp-coverage  Hermetic Azure Container Apps tests with 85/75 coverage gates"
	@echo "  test-self-improve TARGET=<name>  Compare an auto-managed local model with Codex (optional SELF_IMPROVE_MODEL_PATH override)"
	@echo "  test-self-improve-catalog-truth  Replay pinned catalog fixture (SELF_IMPROVE_CATALOG_LIVE=0|1)"
	@echo "  test-self-improve-multifile      Replay pinned multi-file fixture (SELF_IMPROVE_MULTIFILE_LIVE=0|1)"
	@echo "  test-self-improve-failure-corpus Replay typed local failures offline (SELF_IMPROVE_FAILURE_CORPUS_FILE)"
	@echo "  test-self-improve-acceptance-matrix Validate/run the serial ten-shape contract (SELF_IMPROVE_ACCEPTANCE_MATRIX_*)"
	@echo "  test-self-improve-private-policy  Run hermetic fake-local/fake-Azure project privacy E2E coverage"
	@echo "  test-self-improve-all            Deprecated alias for one explicit Codex-reference benchmark"
	@echo "  self-improve-promotion-marker    Verify an exact promotion marker on development (SELF_IMPROVE_PROMOTION_* variables)"
	@echo "  git-index                    Index git log into SQLite (.gludd/git_history.db)"
	@echo "  git-search Q='...'           Search indexed git history"
	@echo "  git-stats                    Show git history index statistics"
	@echo "  agent-report                 Agent activity dashboard (reads /tmp/gludd-agent-results.jsonl)"
	@echo "  check-duplicate-targets           Detect Makefile targets declared on parallel branches"
	@echo "  agent-worktree-dev BRANCH=<name>  Isolated git worktree from development branch"
	@echo "  agent-merge-dev BRANCH=<name>     Merge a subagent worktree branch into development"
	@echo "  development-push             Push the development branch to remote"
	@echo "  development-merge-forward SOURCE=<ref> MODE=content|ancestry-only APPLY=0|1  Transactional reconciliation into development (dry-run default)"
	@echo "  development-merge-forward-batch SOURCES='<refs>' APPLY=0|1  Atomic ancestry-only reconciliation for multiple superseded refs"
	@echo "  development-merge-to-master  Merge development into master (release prep; CI-green required)"
	@echo "  development-start            Create development branch from master if it doesn't exist"
	@echo "  development-status           Show commits on development not yet on master"
	@echo "  git-tag-push TAG=<t> [COMMIT=<sha>] [MSG='...']  Create annotated tag + push to sandboxcom"
	@echo "  git-tag-rm TAG=<t>           Delete tag locally and on sandboxcom"
	@echo "  git-tag-delete TAG=<t>       Alias for git-tag-rm"
	@echo "  git-tag-move TAG=<t> MSG='..'  Delete old tag + create new at HEAD + push"
	@echo "  submodule-init        Initialize submodules or validate configuration (SUBMODULE_INIT_VALIDATE_ONLY=0|1)"
	@echo "  submodule-update      Update submodules to latest remote (--merge)"
	@echo "  submodule-status      Show status of each submodule"
	@echo "  submodule-pin REPO=.. TAG=..  Pin a submodule to a tag/commit"
	@echo ""
	@echo "  --- Secrets + Security ---"
	@echo "  secrets-scan          Scan for secrets against baseline (read-only)"
	@echo "  secrets-scrub         Interactive secret audit + scrub"
	@echo "  secrets-baseline      Refresh and verify canonical .secrets.baseline"
	@echo "  secrets-baseline-check Verify canonical .secrets.baseline without mutation (SECRETS_BASELINE_FILE, POLICY, REPO_ROOT, EXECUTABLE)"
	@echo "  scan-secrets          Alias for secrets-scan"
	@echo "  scan-secrets-baseline Alias for secrets-baseline"
	@echo "  sast-summary          Summarize Bandit JSON by severity/rule/file with baseline deltas (SAST_REPORT, SAST_SUMMARY, SAST_BASELINE)"
	@echo "  security-audit        Observable secrets/SAST/dependency/backlog audit (SECURITY_AUDIT_HEARTBEAT_SECS, SECURITY_AUDIT_PHASE_TIMEOUT_SECS, SECURITY_AUDIT_VALIDATE_ONLY, SECURITY_AUDIT_SUMMARY)"
	@echo "  clean-artifacts       Clean build artifacts, caches, temp files (replaces direct rm)"
	@echo "  health-check          Verify imports and basic system health"
	@echo "  clean-untracked       Remove reinvention-of-wheel files"
	@echo "  clean-hooks           Remove legacy hook scripts"
	@echo "  clean-plugins         No-op (false-done merged into enforce-stop.ts)"
	@echo ""
	@echo "  --- Release ---"
	@echo "  release-list          List all GitHub releases"
	@echo "  reviewed-head-receipt ...  Build exact-topology integration evidence after the final gate"
	@echo "  release-readiness TAG=..  Fail-closed blockers + exact reviewed-head receipt for v0.1.1"
	@echo "  check-release-failure-ledger RELEASE_FAILURE_LEDGER=..  Validate immutable beta failure mappings"
	@echo "  release-branch-new    Cut a release/* branch from a CI-green base (NAME, BASE, RELEASE_BRANCH_VALIDATE_ONLY)"
	@echo "  require-dual-track-green Require exact-SHA local + hosted CI attestations (SHA, DUAL_TRACK_CI_VALIDATE_ONLY)"
	@echo "  release-view TAG=..   Show a published GitHub Release + its assets"
	@echo "  release-create TAG=.. CI-green-gated DRAFT release (single binary; complete via CI)"
	@echo "  release-upload-assets TAG=.. FILES='..'  Add assets to an existing release (repair path)"
	@echo "  release-cut TAG=.. MSG=.. REVIEWED_HEAD_INTEGRATION_RECEIPT=..  The single release command (6 fail-closed steps; exact-run wait up to 90m)"
	@echo "  release-promote TAG=.. MSG=.. REVIEWED_HEAD_INTEGRATION_RECEIPT=..  Exact-SHA ff-only development promotion (validate-only supported)"
	@echo "  release-recut TAG=..  Re-trigger and await the exact tag release workflow"
	@echo "  release-deploy TAG=.. MSG=..  Auto-deploy: merge dev->master, push, tag, wait for CI"
	@echo "  release-delete TAG=.. Delete GitHub Release + local + remote git tags"
	@echo "  verify-release-artifact       TAG=..  Confirm a release has published assets (exit 0 = shipped)"
	@echo "  verify-release-completeness   TAG=..  Verify all 28 categories / 30 beta4 assets present"
	@echo ""
	@echo "  --- Build + Deploy ---"
	@echo "  dist                  Build distribution tarball"
	@echo "  build-executable      Build standalone executable (pyinstaller)"
	@echo "  audit-linux-pyinstaller-warnings  Validate/replay the Linux PyInstaller warning policy"
	@echo "  compare-linux-pyinstaller-warnings  Emit an exact old/new warning-graph review receipt"
	@echo "  check-pyinstaller-warning-reviews  Require an exact receipt for every newly accepted graph"
	@echo "  build-linux-binary-image  Build the digest-pinned Python/uv artifact environment (LINUX_BINARY_*, DOCKER_BUILDX_*, LIMA_*)"
	@echo "  lima-docker-ensure    Provision/reuse a namespaced Lima Docker engine (LIMA_INSTANCE, LIMA_DOCKER_CONFIG, LIMA_DOCKER_TEMPLATE, LIMA_DOCKER_START_TIMEOUT_SECS, LIMA_DOCKER_VALIDATE_ONLY)"
	@echo "  lima-docker-start     Start an existing namespaced Lima Docker engine (LIMA_INSTANCE, LIMA_DOCKER_CONFIG, LIMA_DOCKER_START_TIMEOUT_SECS, LIMA_DOCKER_VALIDATE_ONLY)"
	@echo "  lima-docker-stop      Gracefully stop an existing namespaced Lima Docker engine (LIMA_INSTANCE, LIMA_DOCKER_STOP_TIMEOUT_SECS, LIMA_DOCKER_STOP_KILL_AFTER_SECS, LIMA_DOCKER_VALIDATE_ONLY)"
	@echo "  lima-docker-status    Inspect the namespaced Lima Docker engine (LIMA_INSTANCE, LIMA_DOCKER_CONFIG, LIMA_DOCKER_VALIDATE_ONLY)"
	@echo "  lima-docker-pull      Pull one image into namespaced Lima Docker (LIMA_INSTANCE, LIMA_IMAGE, LIMA_DOCKER_CONFIG, LIMA_DOCKER_VALIDATE_ONLY)"
	@echo "  podman-legacy-default-delete  Remove only a stopped legacy default VM (PODMAN_LEGACY_MACHINE, PODMAN_LEGACY_DELETE_TIMEOUT_SECS, PODMAN_LEGACY_DELETE_VALIDATE_ONLY)"
	@echo "  container-build       Build container image"
	@echo "  container-run         Run container locally"
	@echo "  container-push        Push container image"
	@echo "  deb-package           Build .deb package from dist/gludd binary"
	@echo "  deb-install-deps      apt-get install dependencies from debian/control"
	@echo "  build-sandbox-image       Build Firecracker rootfs image (Alpine + gludd deps)"
	@echo "  vm-image-build            Build VM sandbox images (Firecracker + gVisor)"
	@echo "  vm-image-list             List cached VM sandbox images"
	@echo "  vm-image-clean            Remove all cached VM sandbox images"
	@echo "  verify-sandbox-image      Integrity check on cached sandbox rootfs"
	@echo "  clean-sandbox-images      Remove cached sandbox images"
	@echo "  sandbox-state-dir         Print sandbox runtime-state directory"
	@echo "  sandbox-state-list        List sandbox runtime-state contents"
	@echo "  sandbox-state-clean       Clean sandbox runtime-state for current project"
	@echo "  compute-model-hashes      Recompute the tracked model-artifact hash inventory"
	@echo "  download-1.5b-model       Download the namespaced Qwen 1.5B test model"
	@echo "  download-deepseek-1.3b    Download the namespaced DeepSeek 1.3B test model"
	@echo "  benchmark-codegen-quality Compare local code-generation model quality"
	@echo "  benchmark-local-model    Benchmark the configured local model"
	@echo "  benchmark-models         Compare all configured local models"
	@echo "  run-game-gen-1.5b        Run game generation with the namespaced Qwen 1.5B model"
	@echo "  compare-models           Compare local and hosted model quality"
	@echo ""
	@echo "  --- Governance ---"
	@echo "  test-governance       Run governance collection unit tests"
	@echo "  governance-syntax     Validate governance role YAML syntax"
	@echo "  governance-health     Check governance module_utils imports"
	@echo ""
	@echo "  --- Ansible ---"
	@echo "  ansible-syntax        Validate playbook syntax"
	@echo "  playbook-list         List registered playbooks"
	@echo "  molecule-test SCENARIO=<name>   Run one canonical Molecule scenario"
	@echo "  molecule-reset SCENARIO=<name>  Clear one scenario's Molecule-owned state"
	@echo ""
	@echo "  --- CI ---"
	@echo "  ci-kill-zombie          cancel a CI run via gh run cancel"
	@echo "  ci-job-failure-context  bounded authenticated failure context (RUN, JOB, PATTERN, BEFORE, AFTER, MAX_MATCHES)"
	@echo "  ci-artifact-download    atomically download one exact run-bound GHA artifact (RUN, ARTIFACT, CI_ARTIFACT_OUTPUT_ROOT, CI_ARTIFACT_HEARTBEAT_SECS, CI_ARTIFACT_DOWNLOAD_VALIDATE_ONLY)"
	@echo "  ci-artifact-context     bounded context from one downloaded exact-run artifact (RUN, ARTIFACT, CI_ARTIFACT_FILE, PATTERN, BEFORE, AFTER, MAX_MATCHES, CI_ARTIFACT_CONTEXT_VALIDATE_ONLY)"
	@echo "  ci-pyinstaller-warning-audit replay the complete warning graph from one exact-run artifact (RUN, ARTIFACT, PYINSTALLER_WARNING_*, CI_PYINSTALLER_WARNING_AUDIT_VALIDATE_ONLY)"
	@echo "  ci-coverage-artifact-audit audit one externally stored hosted Cobertura report (CI_COVERAGE_*)"
	@echo "  ci-coverage-gap-plan       print a bounded exact-run line/branch remediation plan (CI_COVERAGE_*)"
	@echo "  ci-run-summary RUN=<id> show one immutable CI run; CI_RUN_SUMMARY_VALIDATE_ONLY=0|1"
	@echo "  ci-failure-status        Show every durable hosted failure family"
	@echo "  ci-failure-repair        Run make-based evidence and receipt selected repairs"
	@echo "  ci-failure-push-guard    Block pushes with open or non-ancestral repairs"
	@echo "  ci-await BRANCH=<ref> TIMEOUT=<s> [SHA=.. CI_AWAIT_*]  Await one exact CI identity"
	@echo "  ci-verdict-safe        Cooldown-enforced CI check (prefer over bare ci-verdict)"
	@echo "  ci-dashboard           One-shot compact CI run listing"
	@echo "  ci-diagnose            Fetch CI failure annotations and group by root cause"
	@echo "  ci-cooldown-status     Show remaining cooldown seconds"
	@echo "  ci-view RUN=<id>       Show CI run details (jobs, steps, failures)"
	@echo "  ci-rerun RUN=<id>      Guard and rerun one observed immutable CI run"
	@echo "  ci-recover-runner-acquisition RUN=<id>  Retry attempt 1 only when every failure is hosted-runner acquisition"
	@echo "  ci-active              List active/in-flight CI runs"
	@echo "  ci-greenness           CI reliability ratio (green / total completed)"
	@echo "  ci-trigger-committed-head [REF=<b>]  Idempotently signal + return exact-SHA GHA run URL"
	@echo "  ci-record-verdict      Record a known CI verdict directly, bypassing cooldown (VERDICT=success|failure|pending, SHA=<sha>)"
	@echo "  deploy-and-forget      Single guarded push + cooldown record (BRANCH, DEPLOY_AND_FORGET_VALIDATE_ONLY=0|1)"
	@echo ""
	@echo "  --- Git Remote ---"
	@echo "  git-remote-sandboxcom Configure sandboxcom GitHub remote with SSH key"
	@echo "  git-push-sandboxcom   Push to sandboxcom/gludd mirror"
	@echo "  git-pull-sandboxcom   Pull and rebase from sandboxcom/gludd"
	@echo "  git-fetch-sandboxcom  Fetch from sandboxcom/gludd"
	@echo "  ship-async REF=<hash> [TARGET=master]  Run gate in background job; ff-only merge on green"
	@echo ""
	@echo "  --- SearXNG Research Backend ---"
	@echo "  searx-up              Start SearXNG via Docker Compose"
	@echo "  searx-down            Stop SearXNG and remove volumes"
	@echo "  searx-test            Health-check the SearXNG JSON API"
	@echo ""
	@echo "  --- Disk ---"
	@echo "  fix-hooks-tmp           temp fix target"
	@echo "  disk-guard            Check disk usage + clean caches if above threshold (default 95%)"
	@echo "  disk-check            Check disk usage only, exit 1 if above threshold"
	@echo "  disk-cleanup-preflight  Auto-reclaim proven-idle Gludd storage, then recheck thresholds (DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0|1, DISK_CLEANUP_PREFLIGHT_DRY_RUN=0|1, DISK_CLEANUP_RECEIPT_GRACE_SECONDS>=1800)"
	@echo "  check-disk            Pre-commit automatic cleanup guard (CHECK_DISK_VALIDATE_ONLY=0; set 1 for deterministic contract test)"
	@echo "  check-disk-classification  Bounded JSON-lines proof of counted vs exempt /tmp/gludd-* roots"
	@echo "  check-system-load     Read-only system load diagnostic (1m avg, CPU count, verdict)"
	@echo "  disk                  Print disk usage + gludd footprint"
	@echo "  disk-reclaim          Run bounded, heartbeat-emitting cache cleanup"
	@echo "  cache-disk            Show bounded user-cache directory sizes"
	@echo "  cache-clean           Remove the explicitly enumerated tool caches"
	@echo "  disk-user-caches      Show accessible user-cache and data-root sizes"
	@echo "  cache-resource-inventory  List largest children of one allowlisted cache root"
	@echo "  cache-resource-remove     Validate or remove one exact allowlisted cache child"
	@echo "  uv-cache-prune-status List uv cache-prune PID, parent, age, and command"
	@echo "  tmp-gludd-usage       Print largest /tmp/gludd-* entries sorted by size"
	@echo "  tmp-gludd-worktree-usage  Print largest generated entries under /tmp/gludd-worktrees"
	@echo "  tmp-gludd-clean-ci-shards  Remove stale generated CI shard scratch dirs"
	@echo "  tmp-gludd-clean-ci-shards-now  Remove inactive CI/gate shard roots (TMP_GLUDD_CLEAN_VALIDATE_ONLY=0; set 1 for contract test)"
	@echo "  tmp-gludd-clean-orphan-worktrees-now  Validate or remove proven orphan roots (TMP_GLUDD_ORPHAN_CLEAN_VALIDATE_ONLY=1; use 0 only after merge)"
	@echo "  clean-worktree-venvs  Preserve invoking/active worktrees; reclaim inactive registered venvs (CLEAN_WORKTREE_VENVS_VALIDATE_ONLY=0|1)"
	@echo "  clean-worktree-caches  Remove generated venv/test/tool caches from worktrees"
	@echo ""
	@echo "  --- OpenCode Database Maintenance ---"
	@echo "  opencode-disk         Bounded data usage using the authoritative OpenCode DB path"
	@echo "  opencode-clean        Offline bounded DB/cache cleanup; refuses while OpenCode runs"
	@echo "  opencode-clean-hard   Offline aggressive cache/log cleanup; refuses while OpenCode runs"
	@echo "  opencode-db-stats     Bounded read-only table counts"
	@echo "  opencode-db-schema    Bounded read-only schema report"
	@echo "  opencode-db-sample    Bounded read-only timestamp sample"
	@echo "  opencode-db-prune     Offline bounded recursive session/event prune"
	@echo "  opencode-db-vacuum-incremental  Safe PRAGMA incremental_vacuum (online)"
	@echo "  opencode-db-vacuum-full        Full VACUUM (needs OPENCODE_MAINTENANCE_FORCE=1 while online)"
	@echo "  opencode-db-compact     Aggressive prune then compact via sqlite3 backup API (OPENCODE_RETENTION_DAYS, OPENCODE_MAINTENANCE_FORCE)"
	@echo ""
	@echo "  --- Recovery ---"
	@echo "  reap-orphan-pytest    Report stale orphan pytest trees (APPLY=1 to terminate)"
	@echo "  reap-stale-collection-locks  Reap only old project-owned collection/gate-refresh locks (APPLY=1)"
	@echo "  replay-codex-file-changes  Atomically validate/replay a bounded Codex file-change range"
	@echo "  backup-opencode       Backup .opencode/ -> .opencode.orig/ (excludes node_modules/)"
	@echo "  check-opencode-backup  Warn if .opencode.orig/ is stale (>24h older than .opencode/)"
	@echo "  restore-opencode      Restore .opencode/ (backup then git fallback) + clear cache"
	@echo "  verify-opencode-backup Verify .opencode.orig/ is current (files + shared.ts exports)"
	@echo ""
	@echo "  --- Other ---"
	@echo "  smoke                 Quick daemon boot health check"
	@echo "  clean                 Remove ignored build artifacts while preserving tracked templates (CLEAN_VALIDATE_ONLY=0|1)"
	@echo "  dist-clean            Remove distribution artifacts"
	@echo "  gated-merge           flock-guarded multi-branch merge with manifest (BASE/BRANCHES/MERGE_STRATEGY/MANIFEST)"
	@echo ""
	@echo "  --- Complete Target Index ---"
	@$(PYTHON) -m scripts.check_make_help --print-index
	@echo "  --- New Targets ---"

	@echo "  normalize-task-integrityNormalize legacy TASKS metadata and reopen unsupported completions"
	@echo "  install-opa             install opa via brew"
	@echo "  gate-local              fast local gate: lint + typecheck + collect + hook-runtime + fast structural tests"
	@echo "  bump-version            bump version in all files (pyproject.toml, __init__.py, README) at once"
	@echo "  check-version-consistencyverify version matches across pyproject.toml, __init__.py, and README"
	@echo "  check-gate-fresh        validate .gate-status is fresh and all phases pass — replaces broken _gate-fresh-check inline shell"
	@echo "  pipeline-health         verify both local and remote pipelines are actually running (not stalled/zombie)"
	@echo "  pipeline-status         exact pushed-SHA local/all-workflow status (PIPELINE_STATUS_*)"
	@echo "  gate-all-background     run gate-all in background, poll with gate-status-check"
	@echo "  target-two              Second test target"
	@echo "  target-one              First test target"
	@echo "  my-target               Duplicate target"
	@echo "  my-secret-scanner       Scan for secrets"
	@echo "  zzyx-test               A test target with no keyword match"
	@echo "  debug-test-target       Debug test"
	@echo "  foo-test                Test"
	@echo ""
	@echo ""

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
ANSIBLE_EE_DOCKER_CONFIG ?=
ANSIBLE_EE_DOCKER_HOST ?=
COLLECTION_PYTHON_BOUNDARY_ROOT ?= collections/ansible_collections
COLLECTION_PYTHON_BOUNDARY_INVENTORY ?= config/ansible/collection-python-boundary-inventory.json
COLLECTION_PYTHON_BOUNDARY_STRICT_ZERO ?= 0
RESOURCE_OWNERSHIP_ROOT ?= .
RESOURCE_OWNERSHIP_PATHS ?= src/general_ludd scripts
RESOURCE_OWNERSHIP_INVENTORY ?= config/resource_ownership_inventory.json
SECRETS_EXCLUDE_FILES ?= sandboxcom_github_rsa|sandboxcom_github_rsa.pub|^config/resource_ownership_inventory\.json$$
RESOURCE_OWNERSHIP_WRITE ?= 0

validate-ansible-runtime-boundary:
	@$(UV) run python scripts/ansible_runtime_artifacts.py validate

update-ansible-runtime-lock:
	@$(UV) run python scripts/ansible_runtime_artifacts.py write-lock

ANSIBLE_EE_BASE_IMAGE_CHECK_VALIDATE_ONLY ?= 1
check-ansible-base-image:
	@case "$(ANSIBLE_EE_BASE_IMAGE_CHECK_VALIDATE_ONLY)" in 0|1) ;; *) echo "ANSIBLE_EE_BASE_IMAGE_CHECK_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@if [ "$(ANSIBLE_EE_BASE_IMAGE_CHECK_VALIDATE_ONLY)" = "1" ]; then echo "ANSIBLE_BASE_IMAGE_CHECK_VALIDATED source=quay.io/centos/centos:stream9"; else $(UV) run python scripts/ansible_runtime_artifacts.py check-base-image; fi

ANSIBLE_EE_BASE_IMAGE_REFRESH_VALIDATE_ONLY ?= 1
refresh-ansible-base-image:
	@case "$(ANSIBLE_EE_BASE_IMAGE_REFRESH_VALIDATE_ONLY)" in 0|1) ;; *) echo "ANSIBLE_EE_BASE_IMAGE_REFRESH_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@if [ "$(ANSIBLE_EE_BASE_IMAGE_REFRESH_VALIDATE_ONLY)" = "1" ]; then echo "ANSIBLE_BASE_IMAGE_REFRESH_VALIDATED source=quay.io/centos/centos:stream9"; else $(UV) run python scripts/ansible_runtime_artifacts.py refresh-base-image; fi

build-ansible-execution-environment:
	@case "$(ANSIBLE_EE_VALIDATE_ONLY)" in 0|1) ;; *) echo "ANSIBLE_EE_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@$(if $(strip $(ANSIBLE_EE_DOCKER_CONFIG)),DOCKER_CONFIG="$(ANSIBLE_EE_DOCKER_CONFIG)") $(if $(strip $(ANSIBLE_EE_DOCKER_HOST)),DOCKER_HOST="$(ANSIBLE_EE_DOCKER_HOST)") $(UV) run python scripts/ansible_runtime_artifacts.py build --runtime "$(ANSIBLE_EE_RUNTIME)" --image "$(ANSIBLE_EE_IMAGE)" --context "$(ANSIBLE_EE_CONTEXT)" $(if $(filter 1,$(ANSIBLE_EE_VALIDATE_ONLY)),--validate-only,)

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
relock:
	@case "$(DEPENDENCY_PROFILE_RELOCK_VALIDATE_ONLY)" in 0|1) ;; *) echo "DEPENDENCY_PROFILE_RELOCK_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@UV_NO_SYNC=0 $(UV) run --no-project --python 3.11 python scripts/dependency_profiles.py $(if $(filter 1,$(DEPENDENCY_PROFILE_RELOCK_VALIDATE_ONLY)),check,lock) --root "$(CURDIR)" --manifest config/dependency_profiles.toml --uv "$(UV)"

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
	@$(UV) run python scripts/check_file_line_limits.py --root "$(CURDIR)" --config "$(FILE_LINE_LIMIT_POLICY)"

split-makefile-layout:
	@case "$(MAKEFILE_SPLIT_APPLY)" in 0|1) ;; *) echo "MAKEFILE_SPLIT_APPLY must be 0 or 1"; exit 2;; esac
	@$(UV) run python -m scripts.split_makefile --entrypoint "$(CURDIR)/Makefile" $(if $(filter 1,$(MAKEFILE_SPLIT_APPLY)),--apply,)

# AGENTS.md OD.10 fast commit preflight.  Keep this intentionally bounded:
# the release/full-suite gate remains a separate workflow.
pre-commit-check:
	@# AGENTS.md OD.10 fast pre-commit contract.
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
	@if [ -z "$(MARKDOWN_FILES)" ] || [ -z "$(MARKDOWNLINT_CONFIG)" ]; then \
		echo "Usage: make lint-markdown MARKDOWN_FILES='README.md docs/file.md' MARKDOWNLINT_CONFIG=config/markdownlint-cli2.jsonc"; \
		exit 2; \
	fi
	@if [ ! -f "$(MARKDOWNLINT_CONFIG)" ]; then echo "ERROR: Markdown config not found: $(MARKDOWNLINT_CONFIG)"; exit 2; fi
	@if [ ! -x ".opencode/node_modules/.bin/markdownlint-cli2" ]; then \
		echo "INFO: locked markdownlint-cli2 not found; syncing locked Node deps"; \
		$(MAKE) node-deps-sync || { echo "ERROR: locked markdownlint-cli2 is unavailable and node-deps-sync failed"; exit 2; }; \
	fi
	@.opencode/node_modules/.bin/markdownlint-cli2 --config "$(MARKDOWNLINT_CONFIG)" $(MARKDOWN_FILES)

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
