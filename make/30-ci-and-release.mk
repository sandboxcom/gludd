# Keep no-commit merge branch data literal before startup or export children.
override GLUDD_GIT_MERGE_NC_BR := $(value BR)
export GLUDD_GIT_MERGE_NC_BR
unexport BR

# Item 17: Poll CI until green with periodic heartbeat
ci-wait:
	@INTERVAL=$${CI_WAIT_INTERVAL:-60}; MAX_WAIT=$${CI_WAIT_MAX:-3600}; ELAPSED=0; \
	echo "=== CI-WAIT: polling ci-verdict every $$INTERVAL seconds (max $$MAX_WAIT seconds) ==="; \
	while [ $$ELAPSED -lt $$MAX_WAIT ]; do \
		RESULT=$$(make ci-verdict BRANCH=master 2>&1 || true); \
		if echo "$$RESULT" | grep -q '^CI GREEN:'; then \
			echo "$$RESULT"; echo "=== CI GREEN after $$ELAPSED seconds ==="; exit 0; \
		fi; \
		STATUS=$$(echo "$$RESULT" | $(PYTHON) -c "import sys,re; m=re.search(r\"status='([^']+)'\", sys.stdin.read()); print(m.group(1) if m else 'unknown')"); \
		echo "[$$ELAPSED s] CI status: $$STATUS"; \
		sleep $$INTERVAL; \
		ELAPSED=$$((ELAPSED + INTERVAL)); \
	done; \
	echo "=== CI-WAIT: timed out after $$MAX_WAIT seconds ==="; exit 1

# Poll CI for an exact identity until it reaches a TERMINAL state.
# Regular wait exits: 0=terminal success, 1=failure, 2=timeout (still pending).
# Validate-only returns0 for a plan; snapshot-only returns0 for ID lookup, not observation/CI-green proof.
# SHA/workflow/event are optional for backwards-compatible branch-only use.
# Usage: make ci-await BRANCH=development TIMEOUT=3600 SHA=... CI_AWAIT_WORKFLOW='Build and Release' CI_AWAIT_EVENT=push CI_AWAIT_INTERVAL=10 CI_AWAIT_AFTER_RUN_ID=0 CI_AWAIT_VALIDATE_ONLY=0 CI_AWAIT_SNAPSHOT_ONLY=0
CI_AWAIT_OBSERVATION_OUTPUT ?= .gludd/ci-await.json
ci-await:
	@$(PYTHON) scripts/ci_await.py --ref "$(or $(BRANCH),master)" --timeout "$(or $(TIMEOUT),3600)" --sha "$(SHA)" --workflow "$(CI_AWAIT_WORKFLOW)" --event "$(CI_AWAIT_EVENT)" --poll-interval "$(or $(CI_AWAIT_INTERVAL),60)" --after-run-id "$(or $(CI_AWAIT_AFTER_RUN_ID),0)" $(if $(and $(SHA),$(CI_AWAIT_OBSERVATION_OUTPUT)),--observation-output "$(CI_AWAIT_OBSERVATION_OUTPUT)",) $(if $(filter 1,$(CI_AWAIT_VALIDATE_ONLY)),--validate-only,) $(if $(filter 1,$(CI_AWAIT_SNAPSHOT_ONLY)),--snapshot-only,)

git-pull-sandboxcom:
	@BRANCH=$$(git branch --show-current); \
	if [ -z "$$BRANCH" ]; then \
		echo "Cannot merge-forward a detached HEAD"; \
		exit 1; \
	fi; \
	GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git fetch sandboxcom "$$BRANCH"; \
	git merge --no-ff --no-edit "sandboxcom/$$BRANCH"
	@echo "Fetched and merge-forwarded the current branch from sandboxcom/gludd"

git-fetch-sandboxcom:
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git fetch sandboxcom
	@echo "Fetched from sandboxcom/gludd"

require-sandboxcom-ssh-key:
	@KEY="$(SSH_KEY)"; \
	if [ ! -f "$$KEY" ] || [ ! -r "$$KEY" ]; then \
		echo "ERROR: sandboxcom SSH key is missing or unreadable: $$KEY"; \
		echo "Set SSH_KEY=/path/to/an external deploy key (never store credentials in the repository)."; \
		exit 1; \
	fi; \
	echo "sandboxcom SSH key available: $$KEY"

# Existing GitHub CLI owns authentication and credential storage.
define _github_workflow_auth_failure
gludd_workflow_fail() { gludd_workflow_status=$$1; gludd_workflow_outcome=$$2; \
	printf '%s\n' "AGENT_NEXT target=$$GLUDD_WORKFLOW_TARGET outcome=$$gludd_workflow_outcome native_status=$$gludd_workflow_status phase=$$GLUDD_WORKFLOW_PHASE credentials_may_have_changed=$$GLUDD_WORKFLOW_WRITES gate_evidence=false release_ready=false; preserve the native reason and reconcile existing account/credential state before retry; operator owns any consent and no push or CI proof follows" >&2; \
	exit "$$gludd_workflow_status"; };
endef

define _github_workflow_gh_binary
GLUDD_WORKFLOW_GH=$$(command -v gh) || { echo "gh is unavailable in the selected PATH" >&2; gludd_workflow_fail 127 failed; }; \
case "$$GLUDD_WORKFLOW_GH" in /*) ;; *) echo "gh must resolve to an absolute executable" >&2; gludd_workflow_fail 127 refused ;; esac;
endef

.PHONY: github-auth-status github-workflow-auth
github-auth-status:
	@GLUDD_WORKFLOW_TARGET=github-auth-status; GLUDD_WORKFLOW_PHASE=gh-executable; GLUDD_WORKFLOW_WRITES=false; \
	$(_github_workflow_auth_failure) \
	$(_github_workflow_gh_binary) \
	GLUDD_WORKFLOW_PHASE=authentication; \
	"$$GLUDD_WORKFLOW_GH" auth status --hostname github.com || gludd_workflow_fail "$$?" failed; \
	echo "AGENT_NEXT target=github-auth-status outcome=observed native_status=0 gate_evidence=false release_ready=false; interpret the actual accounts and scopes: a zero exit is read authentication, not workflow write authorization; for missing workflow scope preview make github-workflow-auth GITHUB_WORKFLOW_AUTH_VALIDATE_ONLY=1 GITHUB_WORKFLOW_AUTH_ACCOUNT= and obtain operator consent before mode 0; do not repeat unchanged failed transport" >&2

# Preview is offline; apply delegates account consent to the existing GitHub CLI.
github-workflow-auth:
	@GLUDD_WORKFLOW_TARGET=github-workflow-auth; GLUDD_WORKFLOW_PHASE=validate-mode; GLUDD_WORKFLOW_WRITES=false; \
	$(_github_workflow_auth_failure) \
	GLUDD_WORKFLOW_MODE="$$GLUDD_GITHUB_WORKFLOW_AUTH_VALIDATE_ONLY"; \
	GLUDD_WORKFLOW_ACCOUNT="$$GLUDD_GITHUB_WORKFLOW_AUTH_ACCOUNT"; \
	case "$$GLUDD_WORKFLOW_MODE" in 0|1) ;; *) echo "GITHUB_WORKFLOW_AUTH_VALIDATE_ONLY must be literal 0 or 1" >&2; gludd_workflow_fail 2 refused ;; esac; \
	if [ "$$GLUDD_WORKFLOW_MODE" = 1 ]; then \
		printf 'PLAN github-workflow-auth host=github.com additional_scope=workflow expected_account=%s credentials_changed=false auth_checked=false\n' "$$GLUDD_WORKFLOW_ACCOUNT"; \
		echo "This requests additional workflow authority for the existing GitHub credential and its covered repositories. Review the selected account and obtain operator approval for GitHub consent." ; \
		echo "AGENT_NEXT target=github-workflow-auth outcome=planned native_status=0 gate_evidence=false release_ready=false; operator-owned step: use GITHUB_WORKFLOW_AUTH_VALIDATE_ONLY=0 with an explicit GITHUB_WORKFLOW_AUTH_ACCOUNT only after account consent is authorized; preview performs no authentication, credential mutation, publication or CI" >&2; \
		exit 0; \
	fi; \
	GLUDD_WORKFLOW_PHASE=account-input; \
	[ -n "$$GLUDD_WORKFLOW_ACCOUNT" ] || { echo "Select the existing account explicitly with GITHUB_WORKFLOW_AUTH_ACCOUNT" >&2; gludd_workflow_fail 2 refused; }; \
	GLUDD_WORKFLOW_PHASE=gh-executable; \
	$(_github_workflow_gh_binary) \
	GLUDD_WORKFLOW_PHASE=authentication; \
	"$$GLUDD_WORKFLOW_GH" auth status --active --hostname github.com || gludd_workflow_fail "$$?" failed; \
	GLUDD_WORKFLOW_PHASE=account-identity; \
	GLUDD_WORKFLOW_ACTUAL=$$("$$GLUDD_WORKFLOW_GH" api user --hostname github.com --jq .login) || gludd_workflow_fail "$$?" failed; \
	[ "$$GLUDD_WORKFLOW_ACTUAL" = "$$GLUDD_WORKFLOW_ACCOUNT" ] || { echo "The active GitHub account differs from the explicitly selected account" >&2; gludd_workflow_fail 2 refused; }; \
	echo "Starting native GitHub consent for the selected existing account; approval occurs at GitHub."; \
	GLUDD_WORKFLOW_PHASE=consent; GLUDD_WORKFLOW_WRITES=true; \
	"$$GLUDD_WORKFLOW_GH" auth refresh --hostname github.com --scopes workflow || gludd_workflow_fail "$$?" failed; \
	GLUDD_WORKFLOW_PHASE=post-authentication; \
	"$$GLUDD_WORKFLOW_GH" auth status --active --hostname github.com || gludd_workflow_fail "$$?" failed; \
	GLUDD_WORKFLOW_PHASE=post-identity; \
	GLUDD_WORKFLOW_ACTUAL=$$("$$GLUDD_WORKFLOW_GH" api user --hostname github.com --jq .login) || gludd_workflow_fail "$$?" failed; \
	[ "$$GLUDD_WORKFLOW_ACTUAL" = "$$GLUDD_WORKFLOW_ACCOUNT" ] || { echo "The post-consent account differs; reconcile the actual credential state" >&2; gludd_workflow_fail 2 refused; }; \
	echo "AGENT_NEXT target=github-workflow-auth outcome=returned native_status=0 gate_evidence=false release_ready=false; inspect the actual scopes and account output; native consent returns no publication or CI proof; use make verify-state, normal guarded make batch-push and full expected-SHA remote/exact-SHA CI verification after authorization is established" >&2

# Repo-local HTTPS publication; Foundation captures the mode before startup.
# Only this recipe performs local configuration. Plan performs no auth/network IO.
define _sandboxcom_publication_failure
gludd_fail() { gludd_status=$$1; gludd_outcome=$$2; \
	printf '%s\n' "AGENT_NEXT target=$$GLUDD_TARGET outcome=$$gludd_outcome native_status=$$gludd_status gate_evidence=false release_ready=false phase=$$GLUDD_PHASE writes_may_have_started=$$GLUDD_WRITES; preserve the native reason, repair that input and reconcile any partial repo-local configuration before retry; normal batch-push admission and hooks remain required" >&2; \
	exit "$$gludd_status"; };
endef

define _sandboxcom_publication_remote_binding
GLUDD_LOCAL_URL=$$(git config --local --get-all remote.sandboxcom.url) || gludd_fail "$$?" failed; \
case "$$GLUDD_LOCAL_URL" in git@github.com:sandboxcom/gludd.git|https://github.com/sandboxcom/gludd.git) ;; *) echo "ERROR: sandboxcom requires one canonical repository-local GitHub URL." >&2; gludd_fail 2 refused ;; esac; \
GLUDD_FETCH_URL=$$(git remote get-url --all sandboxcom) || gludd_fail "$$?" failed; \
test "$$GLUDD_FETCH_URL" = "$$GLUDD_LOCAL_URL" || { echo "ERROR: sandboxcom fetch URL is rewritten or ambiguous." >&2; gludd_fail 2 refused; }; \
GLUDD_PUSH_URL=$$(git remote get-url --push --all sandboxcom) || gludd_fail "$$?" failed; \
case "$$GLUDD_PUSH_URL" in git@github.com:sandboxcom/gludd.git|https://github.com/sandboxcom/gludd.git) ;; *) echo "ERROR: sandboxcom requires one canonical GitHub push URL." >&2; gludd_fail 2 refused ;; esac;
endef

define _sandboxcom_publication_gh_helper
GLUDD_GH_BIN=$$(command -v gh) || { echo "ERROR: gh is unavailable in the selected PATH." >&2; gludd_fail 127 failed; }; \
case "$$GLUDD_GH_BIN" in /*) ;; *) echo "ERROR: gh must resolve to an absolute executable." >&2; gludd_fail 127 refused ;; esac; \
GLUDD_GH_HELPER=$$("$(SYSTEM_PYTHON)" -P -c 'import shlex, sys; print("!" + shlex.quote(sys.argv[1]) + " auth git-credential")' "$$GLUDD_GH_BIN") || gludd_fail "$$?" failed;
endef

.PHONY: git-configure-sandboxcom-https _require-sandboxcom-git-auth verify-remote
git-configure-sandboxcom-https:
	@GLUDD_TARGET=git-configure-sandboxcom-https; GLUDD_PHASE=validate-mode; GLUDD_WRITES=false; \
	$(_sandboxcom_publication_failure) \
	GLUDD_MODE="$$GLUDD_SANDBOXCOM_HTTPS_VALIDATE_ONLY"; \
	case "$$GLUDD_MODE" in 0|1) ;; *) echo "SANDBOXCOM_HTTPS_VALIDATE_ONLY must be 0 or 1" >&2; gludd_fail 2 refused ;; esac; \
	GLUDD_PHASE=remote-binding; $(_sandboxcom_publication_remote_binding) \
	GLUDD_PROPOSED_URL=$$(git ls-remote --get-url https://github.com/sandboxcom/gludd.git) || gludd_fail "$$?" failed; \
	test "$$GLUDD_PROPOSED_URL" = https://github.com/sandboxcom/gludd.git || { echo "ERROR: proposed HTTPS URL is rewritten." >&2; gludd_fail 2 refused; }; \
	if [ "$$GLUDD_MODE" = 1 ]; then \
		echo "PLAN sandboxcom=https://github.com/sandboxcom/gludd.git repo_local=true credential_helper=gh writes=false auth_checked=false"; \
		echo "AGENT_NEXT target=git-configure-sandboxcom-https outcome=planned native_status=0 gate_evidence=false release_ready=false; review the repo-local helper/fetch/push changes, then use SANDBOXCOM_HTTPS_VALIDATE_ONLY=0 for native authentication and configuration; plan is no auth, remote, push or CI proof" >&2; \
		exit 0; \
	fi; \
	GLUDD_PHASE=helper-executable; $(_sandboxcom_publication_gh_helper) \
	GLUDD_PHASE=authentication; "$$GLUDD_GH_BIN" auth status --active --hostname github.com || gludd_fail "$$?" failed; \
	GLUDD_WRITES=true; GLUDD_PHASE=helper-reset; git config --local --replace-all credential.https://github.com.helper "" || gludd_fail "$$?" failed; \
	GLUDD_PHASE=helper-install; git config --local --add credential.https://github.com.helper "$$GLUDD_GH_HELPER" || gludd_fail "$$?" failed; \
	GLUDD_PHASE=fetch-url; git remote set-url sandboxcom https://github.com/sandboxcom/gludd.git || gludd_fail "$$?" failed; \
	GLUDD_PHASE=push-url; git remote set-url --push sandboxcom https://github.com/sandboxcom/gludd.git || gludd_fail "$$?" failed; \
	GLUDD_PHASE=postcheck; \
	GLUDD_FETCH_URL=$$(git remote get-url --all sandboxcom) || gludd_fail "$$?" failed; \
	GLUDD_PUSH_URL=$$(git remote get-url --push --all sandboxcom) || gludd_fail "$$?" failed; \
	test "$$GLUDD_FETCH_URL" = https://github.com/sandboxcom/gludd.git && test "$$GLUDD_PUSH_URL" = https://github.com/sandboxcom/gludd.git || { echo "ERROR: native HTTPS remote postcheck failed." >&2; gludd_fail 2 refused; }; \
	GLUDD_ACTUAL_HELPERS=$$(git config --local --get-all credential.https://github.com.helper) || gludd_fail "$$?" failed; \
	GLUDD_EXPECTED_HELPERS=$$(printf '\n%s\n' "$$GLUDD_GH_HELPER"); \
	test "$$GLUDD_ACTUAL_HELPERS" = "$$GLUDD_EXPECTED_HELPERS" || { echo "ERROR: native repo-local helper postcheck failed." >&2; gludd_fail 2 refused; }; \
	echo "CONFIGURED sandboxcom=https://github.com/sandboxcom/gludd.git repo_local=true credentials_stored=false"; \
	echo "AGENT_NEXT target=git-configure-sandboxcom-https outcome=configured native_status=0 gate_evidence=false release_ready=false; use verify-remote for an already-published exact branch/SHA; configuration supplies no remote-head, push, CI or deployment evidence and normal batch-push guards remain required" >&2

_require-sandboxcom-git-auth:
	@GLUDD_TARGET=verify-remote; GLUDD_PHASE=remote-binding; GLUDD_WRITES=false; \
	$(_sandboxcom_publication_failure) \
	$(_sandboxcom_publication_remote_binding) \
	if [ "$$GLUDD_FETCH_URL" = git@github.com:sandboxcom/gludd.git ]; then \
		GLUDD_PHASE=ssh-key; $(MAKE) --no-print-directory require-sandboxcom-ssh-key || gludd_fail "$$?" failed; \
	else \
		GLUDD_PHASE=helper-binding; $(_sandboxcom_publication_gh_helper) \
		GLUDD_ACTUAL_HELPERS=$$(git config --local --get-all credential.https://github.com.helper) || gludd_fail "$$?" failed; \
		GLUDD_EXPECTED_HELPERS=$$(printf '\n%s\n' "$$GLUDD_GH_HELPER"); \
		test "$$GLUDD_ACTUAL_HELPERS" = "$$GLUDD_EXPECTED_HELPERS" || { echo "ERROR: configure the repo-local gh credential helper before HTTPS verification." >&2; gludd_fail 2 refused; }; \
		GLUDD_PHASE=authentication; "$$GLUDD_GH_BIN" auth status --active --hostname github.com || gludd_fail "$$?" failed; \
	fi

verify-remote: _require-sandboxcom-git-auth
	@GLUDD_TARGET=verify-remote; GLUDD_PHASE=expected-head; GLUDD_WRITES=false; \
	$(_sandboxcom_publication_failure) \
	SHA=$(or $(SHA),$$(git rev-parse HEAD)) || gludd_fail "$$?" failed; BR=$(or $(BRANCH),master); \
	case "$$SHA" in ''|*[!0123456789abcdefABCDEF]*) echo "ERROR: SHA must be a nonempty hexadecimal commit identity." >&2; gludd_fail 2 refused ;; esac; \
	git check-ref-format "refs/heads/$$BR" || gludd_fail "$$?" failed; \
	GLUDD_PHASE=remote-head; \
	REMOTE_ROWS=$$(GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git ls-remote --exit-code --heads sandboxcom "refs/heads/$$BR") || gludd_fail "$$?" failed; \
	REMOTE=$$(printf '%s\n' "$$REMOTE_ROWS" | cut -f1); \
	echo "remote=$$REMOTE expected=$$SHA"; \
	SHA_LENGTH=$$(printf '%s' "$$SHA" | wc -c | tr -d ' '); REMOTE_SHORT=$$(printf '%s\n' "$$REMOTE" | cut -c1-$$SHA_LENGTH); \
	if [ "$$SHA" = "$$REMOTE_SHORT" ]; then \
		echo "VERIFIED $$BR@$$SHA"; \
		echo "AGENT_NEXT target=verify-remote outcome=verified native_status=0 gate_evidence=false release_ready=false; this exact heads-ref/SHA comparison is remote evidence only; use pipeline-status for all required exact-SHA workflows and retain normal publication/deployment gates" >&2; \
	else \
		echo "REMOTE MISMATCH: remote=$$REMOTE expected=$$SHA"; \
		echo "AGENT_NEXT target=verify-remote outcome=failed native_status=1 gate_evidence=false release_ready=false; reconcile the actual remote branch and expected SHA before retry; no VERIFIED, CI or deployment claim follows" >&2; \
		exit 1; \
	fi

# Create a signed annotated tag and push it to sandboxcom to trigger the tag-gated
# release job (version -> gate -> builds -> release). Usage:
#   make git-tag-push TAG=v0.1.0-alpha.1 COMMIT=<sha> MSG='alpha release'
git-tag-push: _push-rate-guard require-sandboxcom-ssh-key
	@[ -n "$(TAG)" ] || { echo "Usage: make git-tag-push TAG=v0.1.0-alpha.N [COMMIT=<sha>] [MSG='...']"; exit 1; }
	@git tag -s -a "$(TAG)" $(if $(COMMIT),$(COMMIT)) -m "$(if $(MSG),$(MSG),$(TAG))"
	@$(MAKE) --no-print-directory check-tag-signing TAG="$(TAG)"
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push sandboxcom "$(TAG)"
	@echo "Pushed tag $(TAG) to sandboxcom/gludd (triggers release job)"


repo-visibility:
	@gh api /repos/sandboxcom/gludd --jq '.private' 2>&1 || echo "gh-api-failed"

ci-status:
	@gh run list -R sandboxcom/gludd -L 8 2>&1 || echo "gh-run-list-failed"

pages-status:
	@gh run list --workflow pages.yml -R sandboxcom/gludd -L 1 --json conclusion,status,databaseId 2>&1 || echo "gh-run-list-failed"

# One-time Pages enablement (build_type=workflow). The pages.yml deploy job
# cannot create the Pages site itself (GITHUB_TOKEN lacks admin) — this uses
# the local gh auth, which must have repo admin. Safe to re-run (409 if exists).
pages-enable:
	@gh api -X POST repos/sandboxcom/gludd/pages -f build_type=workflow 2>&1 || echo "pages-enable-failed (already enabled, or local gh auth lacks repo admin)"
	@gh api repos/sandboxcom/gludd/pages --jq '{status: .status, html_url: .html_url, build_type: .build_type}' 2>&1 || echo "pages-get-failed"

# ci-verdict: NON-BLOCKING point-in-time CI check (returns in <1s).
# Exits 0=GREEN, 1=RED/no-run, 2=PENDING. Per AGENTS.md "CI-Poll Subagents Are
# Forbidden": call ONCE at a natural break; NEVER loop on this; NEVER dispatch
# a subagent to poll it. Use ci-wait ONLY inside release-cut.
#
# *** PREFER `make ci-verdict-safe` (cooldown-enforced) OVER this target. ***
# Bare `ci-verdict` exists for release-cut internals only.
ci-verdict:
	@case "$(CI_VERDICT_VALIDATE_ONLY)" in 0|1) ;; *) echo "CI_VERDICT_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@VERDICT_SHA="$(or $(SHA),$$(git rev-parse HEAD))"; VERDICT_BRANCH="$(or $(CI_BRANCH),$(BRANCH),$$(git branch --show-current))"; $(_CI_VERDICT_COMMAND)

# ci-verdict-safe: COOLDOWN-ENFORCED CI check. Refuses to run more than once
# per CI_CHECK_COOLDOWN_SEC (default 600s = 10 min). Prevents the anti-pattern
# of an agent dispatching a "poll CI until terminal" subagent that loops
# ci-verdict every 60-90s for 30-40 min, holding a subagent slot.
#
# The cooldown is the MACHINE-ENFORCED guardrail. It does not matter whether
# the agent thinks CI might have finished — the answer is: do real work for
# 10 more minutes, THEN check. CI runs on its own schedule.
#
# Exit codes: 0=GREEN, 1=RED/no-run (or last verdict was failure during cooldown),
# 2=PENDING, 3=COOLDOWN-ACTIVE (refused, last verdict was success/pending/unknown).
# Override: FORCE=1 (release-cut ONLY; never use for routine checks).
CI_VERDICT_VALIDATE_ONLY ?= 0
_CI_VERDICT_COMMAND = $(PYTHON) scripts/pipeline_status.py status --remote-only --repo sandboxcom/gludd --remote sandboxcom --branch "$$VERDICT_BRANCH" --sha "$$VERDICT_SHA" $(if $(filter 1,$(CI_VERDICT_VALIDATE_ONLY)),--validate-only,)

ci-verdict-safe:
	@case "$(CI_VERDICT_VALIDATE_ONLY)" in 0|1) ;; *) echo "CI_VERDICT_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@VERDICT_SHA="$(or $(SHA),$$(git rev-parse HEAD))"; VERDICT_BRANCH="$(or $(CI_BRANCH),$(BRANCH),$$(git branch --show-current))"; \
	if [ "$(CI_VERDICT_VALIDATE_ONLY)" = 1 ]; then $(_CI_VERDICT_COMMAND); exit $$?; fi; \
	$(PYTHON) scripts/ci_check_cooldown.py check $(CI_CHECK_COOLDOWN_SEC) || exit $$?; \
	$(_CI_VERDICT_COMMAND) --record-verdict

# ci-record-verdict: record a known CI verdict directly (no cooldown, no gh
# call). For adjudicating already-completed runs when the cooldown would
# block ci-verdict-safe, and for resetting the AA023 restart cap via a
# terminal verdict. VERDICT: success|failure|pending. SHA: the commit the
# verdict refers to.
ci-record-verdict:
	@[ -n "$(VERDICT)" ] || { echo "Usage: make ci-record-verdict VERDICT='failure' SHA=<sha>"; exit 1; }
	@$(PYTHON) scripts/ci_check_cooldown.py record-verdict $(VERDICT) $(SHA)
	@echo "recorded verdict $(VERDICT) for $(SHA)"

# ci-diagnose: fetch CI failure annotations and group by root cause.
# Prints a compact diagnosis: run id, conclusion, top-5 failure clusters.
# Exits 0 if CI is GREEN, 1 if RED (with diagnosis printed).
ci-diagnose:
	@$(PYTHON) scripts/ci_diagnose.py $(or $(BRANCH),master)

# deploy-and-forget: push + record timestamp + print checkback time. This is
# the fire-and-forget deployment pattern. Supports BRANCH= and a hermetic
# DEPLOY_AND_FORGET_VALIDATE_ONLY=1 routing check for local/GHA validation.
# After running this, RESUME REAL WORK — do not poll CI.
deploy-and-forget:
	@if [ "$(DEPLOY_AND_FORGET_VALIDATE_ONLY)" = "1" ]; then \
		if [ "$(BRANCH)" = "development" ] || [ "$(BRANCH)" = "dev" ]; then ROUTE=development; else ROUTE=current-branch; fi; \
		echo "DEPLOY-AND-FORGET-VALID: branch=$(BRANCH) route=$$ROUTE; no network, push, or state mutation"; \
	else \
		$(MAKE) --no-print-directory ci-busy-check BRANCH="$(BRANCH)" || exit $$?; \
		if [ "$(BRANCH)" = "development" ] || [ "$(BRANCH)" = "dev" ]; then \
			$(MAKE) --no-print-directory push-dev || exit $$?; \
		else \
			$(MAKE) --no-print-directory git-push-sandboxcom || exit $$?; \
			$(PYTHON) scripts/ci_check_cooldown.py deploy; \
		fi; \
	fi

# ci-cooldown-status: show how long until the next ci-verdict-safe is allowed.
# Read-only. Use this to decide whether to dispatch real work or check CI.
ci-cooldown-status:
	@$(PYTHON) scripts/ci_check_cooldown.py status $(CI_CHECK_COOLDOWN_SEC)

# ci-observability: single-page summary of CI pipeline health.
# Reads CI state files from /tmp (watchdog cache, cooldown state, push history,
# orchestrator state) and prints: last push time, CI verdict, cooldown remaining,
# push rate, warnings. Exits 0 if CI is healthy, 1 if CI is RED.
# Accepts optional BRANCH= (default: master).
ci-observability:
	@$(PYTHON) scripts/ci_observability.py $(or $(BRANCH),master)

# ci-poll-until-terminal: poll ci-verdict-safe until GREEN or RED, with delay.
# Usage: make ci-poll-until-terminal BRANCH=development [DELAY=60]
ci-poll-until-terminal:
	@while true; do \
	  if $(MAKE) --no-print-directory ci-verdict-safe BRANCH=$(or $(BRANCH),development) 2>/dev/null; then \
	    break; \
	  fi; \
	  RC=$$?; \
	  if [ $$RC -ne 3 ]; then \
	    echo "ci-verdict-safe exit=$$RC (non-cooldown)"; \
	    break; \
	  fi; \
	  sleep $(or $(DELAY),60); \
	done

# ci-poll-sha: poll ci-verdict for a specific SHA until terminal (GREEN/RED).
# Usage: make ci-poll-sha SHA=<full-sha> [DELAY=30] [MAX_ITER=20]
ci-poll-sha:
	@N=0; MAX=$(or $(MAX_ITER),20); DELAY=$(or $(DELAY),30); SHA=$(or $(SHA),$$(git rev-parse HEAD)); \
	while [ $$N -lt $$MAX ]; do \
	  N=$$((N + 1)); \
	  echo "=== poll $$N/$$MAX $$(date -u +%H:%M:%S) ==="; \
	  if $(MAKE) --no-print-directory ci-verdict SHA=$$SHA 2>/dev/null; then \
	    echo "CI GREEN: SHA $$SHA"; exit 0; \
	  fi; \
	  RC=$$?; \
	  if [ $$RC -eq 1 ]; then \
	    echo "CI RED (exited 1)"; exit 1; \
	  elif [ $$RC -eq 2 ]; then \
	    echo "CI PENDING (polling again in $$DELAY s)"; \
	  else \
	    echo "ci-verdict exit=$$RC (unexpected)"; \
	  fi; \
	  if [ $$N -lt $$MAX ]; then sleep $$DELAY; fi; \
	done; \
	echo "POLL EXHAUSTED after $$MAX iterations"; exit 1

# ci-dashboard: one-shot compact CI run listing. Prints one line per recent run
# with status, conclusion, branch, age, and SHA. No polling — pure read-once.
# Usage: make ci-dashboard [LIMIT=10] [BRANCH=development]
ci-dashboard: _require-gh
	@$(PYTHON) scripts/ci_dashboard.py --limit $(or $(LIMIT),5) $(if $(BRANCH),--branch $(BRANCH),)

# ci-run-summary: fetch one immutable numeric workflow-run identity and print a
# concise, terminal-only summary. Validation mode is network-free for contracts.
# Usage: make ci-run-summary RUN=<id> [CI_RUN_SUMMARY_REPO=owner/repo]
#        [CI_RUN_SUMMARY_VALIDATE_ONLY=0|1] [CI_RUN_SUMMARY_SHARD_TIMINGS=0|1]
CI_RUN_SUMMARY_REPO ?= sandboxcom/gludd
CI_RUN_SUMMARY_VALIDATE_ONLY ?= 0
CI_RUN_SUMMARY_SHARD_TIMINGS ?= 0
ci-run-summary:
	@[ -n "$(RUN)" ] || { echo "Usage: make ci-run-summary RUN=<id> [CI_RUN_SUMMARY_REPO=owner/repo] [CI_RUN_SUMMARY_VALIDATE_ONLY=0|1] [CI_RUN_SUMMARY_SHARD_TIMINGS=0|1]"; exit 2; }
	@case "$(CI_RUN_SUMMARY_VALIDATE_ONLY)" in 0|1) ;; *) echo "CI_RUN_SUMMARY_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; esac
	@case "$(CI_RUN_SUMMARY_SHARD_TIMINGS)" in 0|1) ;; *) echo "CI_RUN_SUMMARY_SHARD_TIMINGS must be 0 or 1"; exit 2 ;; esac
	@$(PYTHON) scripts/ci_run_summary.py --run "$(RUN)" --repo "$(CI_RUN_SUMMARY_REPO)" $(if $(filter 1,$(CI_RUN_SUMMARY_VALIDATE_ONLY)),--validate-only,) $(if $(filter 1,$(CI_RUN_SUMMARY_SHARD_TIMINGS)),--shard-timings,)

# Durable all-failure ownership.  The observer binds terminal evidence to one
# immutable run/SHA; the central push guard makes the ledger non-optional.
CI_FAILURE_LEDGER ?= .gludd/ci-failure-ledger.json
CI_FAILURE_REPOSITORY ?= sandboxcom/gludd
CI_FAILURE_VALIDATE_ONLY ?= 0
CI_FAILURE_BRANCH ?=
CI_FAILURE_HEAD ?=
CI_FAILURE_FAMILIES ?=
CI_REPAIR_ALL_OPEN ?= 0
CI_REPAIR_SHA ?=
CI_REPAIR_EVIDENCE_TARGET ?=
export CI_REPAIR_EVIDENCE_VARS ?=
CI_RERUN_ALLOW_UNCHANGED ?= 0
CI_RERUN_REASON ?=

ci-failure-status:
	@case "$(CI_FAILURE_VALIDATE_ONLY)" in 0|1) ;; *) echo "CI_FAILURE_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; esac
	@$(PYTHON) scripts/ci_failure_ledger.py status --ledger "$(CI_FAILURE_LEDGER)" $(if $(filter 1,$(CI_FAILURE_VALIDATE_ONLY)),--validate-only,)

ci-failure-repair:
	@# CI_REPAIR_EVIDENCE_VARS is exported whole and parsed only by the opt-in CLI.
	@case "$(CI_FAILURE_VALIDATE_ONLY)" in 0|1) ;; *) echo "CI_FAILURE_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; esac
	@case "$(CI_REPAIR_ALL_OPEN)" in 0|1) ;; *) echo "CI_REPAIR_ALL_OPEN must be 0 or 1"; exit 2 ;; esac
	@$(PYTHON) scripts/ci_failure_ledger.py repair --ledger "$(CI_FAILURE_LEDGER)" --sha "$(CI_REPAIR_SHA)" --evidence-target "$(CI_REPAIR_EVIDENCE_TARGET)" $(foreach FAMILY,$(CI_FAILURE_FAMILIES),--family "$(FAMILY)") $(if $(filter 1,$(CI_REPAIR_ALL_OPEN)),--all-open,) --evidence-vars-from-env $(if $(filter 1,$(CI_FAILURE_VALIDATE_ONLY)),--validate-only,)

ci-failure-push-guard:
	@case "$(CI_FAILURE_VALIDATE_ONLY)" in 0|1) ;; *) echo "CI_FAILURE_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; esac
	@BRANCH_VALUE='$(CI_FAILURE_BRANCH)'; \
	if [ -z "$$BRANCH_VALUE" ]; then BRANCH_VALUE='$(PUSH_BRANCH)'; fi; \
	if [ -z "$$BRANCH_VALUE" ]; then BRANCH_VALUE="$$(git branch --show-current)"; fi; \
	[ -n "$$BRANCH_VALUE" ] || { echo "CI failure push guard requires a branch"; exit 2; }; \
	$(PYTHON) scripts/ci_failure_ledger.py guard-push --ledger "$(CI_FAILURE_LEDGER)" --branch "$$BRANCH_VALUE" --head "$(CI_FAILURE_HEAD)" $(if $(filter 1,$(CI_FAILURE_VALIDATE_ONLY)),--validate-only,)

# Consolidated, read-only state report for pre-claim verification. Prints the
# working tree (CLEAN/DIRTY), HEAD identity + branch, remote sync state
# (SYNCED/DIVERGED/UNREACHABLE with unpushed commits), recent commits, and the
# CI verdict for HEAD. Fail-soft: network calls fall back to UNREACHABLE / NO
# RUN rather than erroring. Always exits 0.
.PHONY: verify-state
verify-state:
	@echo "=== GLUDD STATE REPORT $(shell date -u +%Y-%m-%dT%H:%M:%SZ) ==="
	@echo ""
	@echo "--- Working Tree ---"
	@WT=$$(git status --porcelain); \
	if [ -z "$$WT" ]; then echo "CLEAN"; \
	else echo "DIRTY ($$(echo "$$WT" | wc -l | tr -d ' ') files):"; echo "$$WT"; fi
	@echo ""
	@echo "--- HEAD ---"
	@echo "Local:  $$(git rev-parse HEAD)"
	@echo "Branch: $$(git branch --show-current)"
	@echo ""
	@echo "--- Remote ---"
	@BRANCH=$$(git branch --show-current); \
	REMOTE=$$(GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git ls-remote sandboxcom refs/heads/$$BRANCH 2>/dev/null | cut -f1); \
	if [ -z "$$REMOTE" ]; then echo "UNREACHABLE"; \
	elif [ "$$REMOTE" = "$$(git rev-parse HEAD)" ]; then echo "SYNCED: $$REMOTE"; \
	else echo "DIVERGED: local=$$(git rev-parse --short HEAD) remote=$$(echo $$REMOTE | cut -c1-12)"; \
	echo "Unpushed:"; git log --oneline $$REMOTE..HEAD 2>/dev/null | head -10; fi
	@echo ""
	@echo "--- Recent Commits ---"
	@git log --oneline -5
	@echo ""
	@echo "--- CI ---"
	@SHA=$$(git rev-parse HEAD); BRANCH=$$(git branch --show-current); \
	$(UV) run --no-sync python scripts/pipeline_status.py status --remote-only --repo sandboxcom/gludd \
		--remote sandboxcom --branch "$$BRANCH" --sha "$$SHA" || true
	@echo ""
	@echo "=== END STATE REPORT ==="

gha-usage:
	@$(PYTHON) scripts/gha_usage.py
	@echo ""
	@echo "Billing (requires admin):"
	@gh api /orgs/sandboxcom/settings/billing/actions --jq '{total_minutes_used: .total_minutes_used, total_paid_minutes_used: .total_paid_minutes_used, included_minutes: .included_minutes}' 2>/dev/null || echo "  Billing not accessible (requires admin)"

# List all GitHub releases for sandboxcom/gludd.
release-list:
	@gh release list -R sandboxcom/gludd --limit 20

# Confirm a published GitHub Release + list its downloadable assets.
release-view:
	@[ -n "$(TAG)" ] || { echo "Usage: make release-view TAG=v0.1.0-alpha.1"; exit 1; }
	@$(PYTHON) scripts/release_view.py "$(TAG)"

# Verify a GitHub Release has published assets (exit 0 only if non-draft + assets >= 1).
# A tag is NOT a release. This is the machine-enforceable definition of "shipped."
verify-release-artifact:
	@[ -n "$(TAG)" ] || { echo "Usage: make verify-release-artifact TAG=v0.1.0-alpha.1"; exit 1; }
	@$(PYTHON) scripts/verify_release_artifact.py "$(TAG)"

# Verify a release has ALL expected artifacts (platform binaries, checksums, SBOM, metadata).
# Exit 0 only when every expected artifact category is present. Extends verify-release-artifact.
verify-release-completeness:
	@[ -n "$(TAG)" ] || { echo "Usage: make verify-release-completeness TAG=v0.1.0-alpha.1"; exit 1; }
	@$(PYTHON) scripts/verify_release_completeness.py "$(TAG)"

# CI-green precondition for release-cut. Exit 0 only when every required
# workflow's newest exact-SHA push run is completed + success. Fail-closed: any
# non-success state (pending, failure, missing run) aborts the release.
# Usage: make require-ci-green [SHA=<full-sha>]
require-ci-green:
	@$(UV) run python scripts/require_ci_green.py "$(SHA)" "$(CI_BRANCH)"

# Release precondition that requires the local full-shard attestation and all
# eight GitHub-hosted shard attestations to match one exact successful SHA.
require-dual-track-green:
	@SHA_TO_VERIFY="$(SHA)"; \
	if [ -z "$$SHA_TO_VERIFY" ]; then SHA_TO_VERIFY="$$(git rev-parse HEAD)"; fi; \
	if [ "$(DUAL_TRACK_CI_VALIDATE_ONLY)" = "1" ]; then \
		$(UV) run python scripts/verify_dual_track_ci.py --sha "$$SHA_TO_VERIFY" --validate-only; \
	else \
		$(MAKE) --no-print-directory require-ci-green SHA="$$SHA_TO_VERIFY" CI_BRANCH="$(CI_BRANCH)"; \
		if [ -n "$(DUAL_TRACK_CI_LOCAL_ATTESTATION)" ]; then \
			$(UV) run python scripts/verify_dual_track_ci.py --sha "$$SHA_TO_VERIFY" --local-attestation "$(DUAL_TRACK_CI_LOCAL_ATTESTATION)"; \
		else \
			$(UV) run python scripts/verify_dual_track_ci.py --sha "$$SHA_TO_VERIFY"; \
		fi; \
	fi

# Cut a release branch only from an existing CI-green base.  Validation mode
# exercises all local checks without contacting GitHub or changing refs.
release-branch-new:
	@[ -n "$(NAME)" ] || { echo "Usage: make release-branch-new NAME=release/<version> [BASE=master] [RELEASE_BRANCH_VALIDATE_ONLY=1]"; exit 2; }
	@case "$(NAME)" in release/*) ;; *) echo "ERROR: release branch must use the release/* namespace"; exit 2;; esac
	@BASE_REF="$(or $(BASE),master)"; \
	git check-ref-format --branch "$(NAME)" >/dev/null 2>&1 || { echo "ERROR: invalid release branch name: $(NAME)"; exit 2; }; \
	git check-ref-format --branch "$$BASE_REF" >/dev/null 2>&1 || { echo "ERROR: invalid release base: $$BASE_REF"; exit 2; }; \
	BASE_SHA=$$(git rev-parse --verify --quiet "$$BASE_REF^{commit}") || { echo "ERROR: release base does not resolve to a commit: $$BASE_REF"; exit 2; }; \
	if [ "$(RELEASE_BRANCH_VALIDATE_ONLY)" = "1" ]; then echo "RELEASE-BRANCH-NEW VALIDATED name=$(NAME) base=$$BASE_REF sha=$$BASE_SHA"; exit 0; fi; \
	git show-ref --verify --quiet "refs/heads/$(NAME)" && { echo "ERROR: local branch already exists: $(NAME)"; exit 2; } || true; \
	$(MAKE) --no-print-directory require-ci-green SHA="$$BASE_SHA"; \
	git branch "$(NAME)" "$$BASE_SHA"; \
	echo "Created $(NAME) from CI-green $$BASE_REF@$$BASE_SHA"

# Create an annotated tag at HEAD and force-move it (delete old local+remote,
# create new at HEAD, push). Usage:
#   make git-tag-move TAG=v0.1.0-beta.1 MSG='release notes'
git-tag-move:
	@[ -n "$(TAG)" ] || { echo "Usage: make git-tag-move TAG=v0.1.0-beta.1 [MSG='...']"; exit 1; }
	@$(MAKE) -s git-tag-rm TAG=$(TAG)
	@$(MAKE) -s git-tag-push TAG=$(TAG) MSG="$(MSG)"
	@echo "Tag $(TAG) moved to HEAD and pushed to sandboxcom"

# Delete a tag both locally and on sandboxcom. Usage:
#   make git-tag-rm TAG=v0.1.0-alpha.1
git-tag-rm:
	@[ -n "$(TAG)" ] || { echo "Usage: make git-tag-rm TAG=v0.1.0-alpha.1"; exit 1; }
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push sandboxcom :refs/tags/$(TAG) 2>/dev/null || true
	@git tag -d "$(TAG)" 2>/dev/null || true
	@echo "Deleted tag $(TAG) locally and on sandboxcom"

# Alias for git-tag-rm. Usage:
#   make git-tag-delete TAG=v0.1.0-alpha.1
git-tag-delete: git-tag-rm

# Re-trigger a release CI job for an existing tag whose release job was skipped.
# Deletes and re-pushes the tag, awaits its exact workflow, then verifies assets.
# Usage: make release-recut TAG=v0.1.0-alpha.1
release-recut: _push-rate-guard require-sandboxcom-ssh-key
	@[ -n "$(TAG)" ] || { echo "Usage: make release-recut TAG=v0.1.0-alpha.1"; exit 1; }
	@git tag -l "$(TAG)" | grep -q "$(TAG)" || { echo "ERROR: local tag $(TAG) not found"; exit 1; }
	@$(MAKE) --no-print-directory check-tag-signing TAG="$(TAG)"
	@$(MAKE) -s require-ci-green SHA=$$(git rev-parse "$(TAG)^{commit}")
	@set -e; TAG_SHA="$$(git rev-parse "$(TAG)^{commit}")"; \
		BASELINE="$$( $(MAKE) -s ci-await BRANCH="$(TAG)" TIMEOUT="$(RELEASE_AWAIT_TIMEOUT)" SHA="$$TAG_SHA" CI_AWAIT_WORKFLOW="Build and Release" CI_AWAIT_EVENT=push CI_AWAIT_INTERVAL="$(RELEASE_AWAIT_INTERVAL)" CI_AWAIT_AFTER_RUN_ID=0 CI_AWAIT_VALIDATE_ONLY=0 CI_AWAIT_SNAPSHOT_ONLY=1 )"; \
		echo "Re-cutting release tag $(TAG) after workflow run $$BASELINE..."; \
		GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push sandboxcom :refs/tags/$(TAG) 2>/dev/null || true; \
		GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push sandboxcom "$(TAG)"; \
		echo "Waiting for exact tag workflow after run $$BASELINE before artifact verification..."; \
		$(MAKE) --no-print-directory ci-await BRANCH="$(TAG)" TIMEOUT="$(RELEASE_AWAIT_TIMEOUT)" SHA="$$TAG_SHA" CI_AWAIT_WORKFLOW="Build and Release" CI_AWAIT_EVENT=push CI_AWAIT_INTERVAL="$(RELEASE_AWAIT_INTERVAL)" CI_AWAIT_AFTER_RUN_ID="$$BASELINE" CI_AWAIT_VALIDATE_ONLY=0 CI_AWAIT_SNAPSHOT_ONLY=0
	@$(MAKE) -s verify-release-artifact TAG=$(TAG)
	@$(MAKE) -s verify-release-completeness TAG=$(TAG)

# The single release command. 6 steps, fail-closed at every gate:
#   0. require-ci-green        — abort if CI is not GREEN for HEAD (or SHA=...)
# Pre-release checklist: runs every pre-flight check and prints READY or BLOCKERS.
# Does NOT cut the release — that is still release-cut below.
# Usage: make release-checklist TAG=v0.1.0-beta.3 [--human]
release-checklist:
	@[ -n "$(TAG)" ] || { echo "Usage: make release-checklist TAG=v0.1.0-beta.N"; exit 1; }
	@$(UV) run python scripts/release_cut_checklist.py $(TAG) --human

.PHONY: check-release-failure-ledger
check-release-failure-ledger:
	@[ -n "$(RELEASE_FAILURE_LEDGER)" ] || { echo "Usage: make check-release-failure-ledger RELEASE_FAILURE_LEDGER=docs/releases/beta-release-failures.json"; exit 2; }
	@$(UV) run python scripts/check_release_failure_ledger.py --ledger "$(RELEASE_FAILURE_LEDGER)" --repository-root .

# Export literal caller values without interpolating them into shell syntax.
export GLUDD_REVIEWED_HEAD_INPUTS_ACTION := $(value REVIEWED_HEAD_INPUTS_ACTION)
export GLUDD_REVIEWED_HEAD_INPUTS_INTENT := $(value REVIEWED_HEAD_INPUTS_INTENT)
export GLUDD_REVIEWED_HEAD_INPUTS_OUTPUT := $(value REVIEWED_HEAD_INPUTS_OUTPUT)
export GLUDD_REVIEWED_HEAD_INPUTS_REPO_ROOT := $(value REVIEWED_HEAD_INPUTS_REPO_ROOT)
export GLUDD_REVIEWED_HEAD_INPUTS_GATE_ATTESTATION := $(value REVIEWED_HEAD_INPUTS_GATE_ATTESTATION)
export GLUDD_REVIEWED_HEAD_INPUTS_VALIDATE_ONLY := $(value REVIEWED_HEAD_INPUTS_VALIDATE_ONLY)
unexport REVIEWED_HEAD_INPUTS_ACTION REVIEWED_HEAD_INPUTS_INTENT REVIEWED_HEAD_INPUTS_OUTPUT REVIEWED_HEAD_INPUTS_REPO_ROOT REVIEWED_HEAD_INPUTS_GATE_ATTESTATION REVIEWED_HEAD_INPUTS_VALIDATE_ONLY
# Keep the native producer protected through post-observer authentication, even
# when a legacy task watchdog is already running in another linked worktree.
override _REVIEWED_HEAD_FOCUS_WATCHDOG_ARG :=
ifeq ($(value REVIEWED_HEAD_INPUTS_ACTION),focus)
ifeq ($(value REVIEWED_HEAD_INPUTS_VALIDATE_ONLY),0)
override _REVIEWED_HEAD_FOCUS_WATCHDOG_ARG := --watchdog-owned-reviewed-head-focus
endif
endif
.PHONY: reviewed-head-inputs
reviewed-head-inputs:
	@# Literal exports: REVIEWED_HEAD_INPUTS_ACTION, REVIEWED_HEAD_INPUTS_INTENT,
	@# REVIEWED_HEAD_INPUTS_OUTPUT, REVIEWED_HEAD_INPUTS_REPO_ROOT,
	@# REVIEWED_HEAD_INPUTS_GATE_ATTESTATION, REVIEWED_HEAD_INPUTS_VALIDATE_ONLY.
	@# Existing native progress admission: WORK_ITEM_ID, or its selected primary.
	@PYTHONPATH="$(CURDIR)/src$${PYTHONPATH:+:$$PYTHONPATH}" $(UV) run python scripts/reviewed_head_inputs.py $(_REVIEWED_HEAD_FOCUS_WATCHDOG_ARG)

.PHONY: reviewed-head-receipt
reviewed-head-receipt:
	@[ -n "$(REVIEWED_HEAD_RECEIPT_MANIFEST)" ] || { echo "Usage: make reviewed-head-receipt REVIEWED_HEAD_RECEIPT_MANIFEST=path REVIEWED_HEAD_GATE_ATTESTATION=path REVIEWED_HEAD_RECEIPT_OUTPUT=path REVIEWED_HEAD_RECEIPT_REPO_ROOT=. REVIEWED_HEAD_RECEIPT_VALIDATE_ONLY=0|1"; exit 2; }
	@[ -n "$(REVIEWED_HEAD_GATE_ATTESTATION)" ] || { echo "ERROR: REVIEWED_HEAD_GATE_ATTESTATION is required"; exit 2; }
	@[ -n "$(REVIEWED_HEAD_RECEIPT_OUTPUT)" ] || { echo "ERROR: REVIEWED_HEAD_RECEIPT_OUTPUT is required"; exit 2; }
	@[ -n "$(REVIEWED_HEAD_RECEIPT_REPO_ROOT)" ] || { echo "ERROR: REVIEWED_HEAD_RECEIPT_REPO_ROOT is required"; exit 2; }
	@case "$(REVIEWED_HEAD_RECEIPT_VALIDATE_ONLY)" in 0|1) ;; *) echo "ERROR: REVIEWED_HEAD_RECEIPT_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; esac
	@if [ "$(REVIEWED_HEAD_RECEIPT_VALIDATE_ONLY)" = "1" ]; then \
		echo "REVIEWED-HEAD-RECEIPT-PLAN manifest=$(REVIEWED_HEAD_RECEIPT_MANIFEST) gate=$(REVIEWED_HEAD_GATE_ATTESTATION) output=$(REVIEWED_HEAD_RECEIPT_OUTPUT) repo=$(REVIEWED_HEAD_RECEIPT_REPO_ROOT)"; \
	else \
		$(UV) run python scripts/build_reviewed_head_integration_receipt.py \
			--manifest "$(REVIEWED_HEAD_RECEIPT_MANIFEST)" \
			--gate-attestation "$(REVIEWED_HEAD_GATE_ATTESTATION)" \
			--repo-root "$(REVIEWED_HEAD_RECEIPT_REPO_ROOT)" \
			--output "$(REVIEWED_HEAD_RECEIPT_OUTPUT)"; \
	fi

RELEASE_READINESS_CI_BRANCH ?=

ifneq (,$(filter gate gate-resume _gate-release-proof-admission release-proof-admission,$(MAKECMDGOALS)))
unexport WORK_ITEM_ID
endif

# Scheduling admission only: existing owners remain the policy authorities.
# Do not seal/replay a candidate or start a full gate while this is blocked.
.PHONY: release-proof-admission
release-proof-admission: override UV := $(UV)

release-proof-admission:
	@WORK_TASK='$(subst ','"'"',$(value WORK_ITEM_ID))'; \
	agent_next() { printf 'AGENT_NEXT target=release-proof-admission outcome=%s phase=%s gate_evidence=false release_ready=false: %s\n' "$$1" "$$2" "$$3" >&2; }; \
	blocked_next() { \
		case "$$1" in \
			disk-cleanup-preflight) proof_next='The direct admission blocker is capacity. The canonical volume limit is hard; generated scratch is a soft cleanup trigger. Protected or recent scratch may be admitted when volume is healthy; native preflight still refuses inspection or cleanup errors. disk-check is diagnostic, not the admission owner. Use make disk for bounded diagnosis and native lifecycle owners only for proven eligible resources. HumanTodo is an escalation route when capacity needs operator action; retain an actual request ID, never a check-mode null or request configuration. A refused API request remains unfiled and needs a changed service prerequisite before API retry. Rerun documented mode0 admission after actual capacity clears, even if the request remains unfiled; all existing admission owners and final proofs remain required.' ;; \
			gate-admission-config) proof_next='Repair the reported release policy inputs with make gate-admission-config; preserve canonical budgets, coverage and runtime settings.' ;; \
			check-task-registration|check-task-integrity) proof_next='Repair the named TASKS.md or source registration input with the reported native Make owner; retain canonical IDs and immutable history.' ;; \
			check-gate-failure-promotions) proof_next='Repair the named manifest, node or Make-wiring input with make check-gate-failure-promotions; all eight child pins and terminal success remain required.' ;; \
			check-resource-ownership) proof_next='Repair the named resource owner or inventory input with make check-resource-ownership; keep RESOURCE_OWNERSHIP_WRITE=0 for admission and preserve existing leases.' ;; \
			check-test-env-writes) proof_next='Repair the named test fixture or environment writer with make check-test-env-writes; confine test output instead of changing production observations.' ;; \
			test-files) proof_next='Repair the actual failing source-contract node through the documented make test-files owner; preserve original assertions and fatal warnings.' ;; \
			_pytest-runtime-compatibility-admission) proof_next='Read the original native watched receipt from run-watched; identify the actual failed node, unmet prerequisite or timeout cause before repair. Preserve the original 90/60 owner and do not substitute a focused pass.' ;; \
			*) proof_next='Repair the reported native owner and its actual failing input.' ;; \
		esac; \
		agent_next blocked "$$1" "$$proof_next Retain failed native evidence and identify the changed input before retrying documented mode0 admission; no expensive final proof or task tick follows this result."; \
	}; \
	case "$$WORK_TASK" in *[!A-Za-z0-9._-]*) echo "ERROR: WORK_ITEM_ID must be literal task data"; echo "PROOF-ADMISSION-BLOCKED phase=release-proof-admission rc=2"; agent_next refused work-item-input 'Repair WORK_ITEM_ID as documented literal active-task data; no admission owner has run.'; exit 2;; esac; \
	case "$(RELEASE_PROOF_ADMISSION_VALIDATE_ONLY)" in 0|1) ;; *) echo "ERROR: RELEASE_PROOF_ADMISSION_VALIDATE_ONLY must be 0 or 1"; agent_next refused validate-only 'Repair RELEASE_PROOF_ADMISSION_VALIDATE_ONLY to documented 0 or 1; no admission owner has run.'; exit 2 ;; esac; \
	if [ "$(RELEASE_PROOF_ADMISSION_VALIDATE_ONLY)" = "1" ]; then \
		echo "PROOF-ADMISSION-PLAN capacity -> gate-config -> task-registration -> task-integrity -> failure-promotions -> resource-ownership -> test-env-writes -> source-contracts -> runtime-compatibility; release_ready=false gate_evidence=false"; \
		agent_next planned plan 'Capacity, configuration, resource ownership, source contracts and runtime are not executed. Inspect worktree-state and native work-item status; use documented mode0 only after actual prerequisites are changed or clear. Do not seal a candidate, run a full gate, publish or tick tasks from this plan.'; \
		exit 0; \
	fi; \
	run_owner() { \
		phase="$$1"; \
		echo "PROOF-ADMISSION-START phase=$$phase"; \
		if [ "$$phase" = "_pytest-runtime-compatibility-admission" ]; then \
			if $(UV) run python scripts/item_progress.py prepare-runtime --task "$$WORK_TASK"; then :; else \
				rc=$$?; echo "PROOF-ADMISSION-BLOCKED phase=$$phase rc=$$rc"; \
				agent_next blocked "$$phase" 'Use documented make work-item focus and active-work-status to repair the actual active-task input; runtime child has not started. Retain native reasons and identify the changed input before retrying; no expensive final proof or task tick follows.'; exit "$$rc"; \
			fi; \
			runtime_run_id="proof-admission-$$(date -u +%Y%m%dT%H%M%SZ)-$$$$"; \
			set -- run-watched WORK_ITEM_ID="$$WORK_TASK" GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1 \
				CMD='$(MAKE) --no-print-directory _pytest-runtime-compatibility-admission' \
				OBSERVED_LABEL=release-proof-runtime-admission RUN_ID="$$runtime_run_id" MAX_SECS=90 STALL_SECS=60 \
				LOG="$(OBSERVED_ROOT)/release-proof-runtime-admission/$$runtime_run_id.log" \
				OBSERVED_ROOT="$(OBSERVED_ROOT)" OBSERVED_HEARTBEAT_SECS=10 OBSERVED_RETAIN_RUNS="$(or $(OBSERVED_RETAIN_RUNS),20)"; \
		fi; \
		if $(MAKE) --no-print-directory "$$@"; then \
			echo "PROOF-ADMISSION-PHASE-PASS phase=$$phase"; \
		else \
			rc=$$?; echo "PROOF-ADMISSION-BLOCKED phase=$$phase rc=$$rc"; blocked_next "$$phase"; exit "$$rc"; \
		fi; \
	}; \
	run_owner disk-cleanup-preflight DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0 DISK_CLEANUP_PREFLIGHT_DRY_RUN=1 DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1800; \
	run_owner gate-admission-config GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1; \
	run_owner check-task-registration; \
	run_owner check-task-integrity; \
	run_owner check-gate-failure-promotions GATE_FAILURE_PROMOTION_MANIFEST=config/gate_failure_promotions.json; \
	run_owner check-resource-ownership RESOURCE_OWNERSHIP_ROOT=. RESOURCE_OWNERSHIP_PATHS='' RESOURCE_OWNERSHIP_INVENTORY=config/resource_ownership_inventory.json RESOURCE_OWNERSHIP_WRITE=0; \
	run_owner check-test-env-writes; \
	run_owner test-files TESTFILES='tests/unit/test_guardrails.py::TestBashGuardrailPrompting::test_agents_md_lists_make_targets tests/unit/test_guardrails.py::TestTDDGuardrail::test_agents_md_tdd_lists_workflow_steps tests/unit/test_guardrails.py::TestGuardrailIntegrity::test_agents_md_has_guardrail_integrity_policy tests/unit/test_guardrails.py::TestTaskCompletionGuardrail::test_agents_md_has_completion_policy tests/unit/test_guardrails.py::TestTaskCompletionGuardrail::test_agents_md_completion_policy_forbids_early_stop tests/unit/test_no_home_directory_access.py::test_agents_md_documents_allowed_boundary tests/unit/test_no_home_directory_access.py::test_agents_md_lists_structural_test_as_enforcement tests/unit/test_markdown_docs_deep.py::test_all_internal_links_resolve tests/unit/test_oversized_document_splits.py tests/unit/test_spec_enforcement_t01_t29.py::TestT22T23T24GateStatusEvidence::test_t23_cite_pass_count_with_test_claim_policy tests/unit/test_behavioral_specs.py::TestAgentsMdPolicies::test_dispatch_floor_policy tests/unit/test_behavioral_specs.py::TestAgentsMdPolicies::test_ci_poll_subagents_forbidden tests/unit/test_e4_noqa_guardrail.py::TestAgentsMdRule::test_runtime_hook_layer_documented tests/unit/test_e4_noqa_guardrail.py::TestAgentsMdRule::test_behavior_pin_test_documented tests/unit/test_e4_noqa_guardrail.py::TestAgentsMdRule::test_guardrail_integrity_policy_cross_reference tests/unit/test_watchdog_deep.py::TestTaskWatchdogProcessIdentity::test_kill_skips_when_identity_changed tests/unit/test_xdist_pollution_guard.py::test_all_setenv_literals_are_in_leaky_env_vars tests/unit/test_deadline_enforcement_deep.py::TestKillProcessEdgeCases::test_sigterm_before_sigkill_ordering tests/unit/test_deadline_enforcement_deep.py::TestKillProcessEdgeCases::test_process_exited_during_sigterm_wait tests/unit/test_deadline_enforcement_deep.py::TestWatchdogSourceInvariants::test_kill_multiple_candidates tests/unit/test_watchdog_deep.py::TestTaskWatchdogConfiguration tests/unit/test_scoped_admission_targets.py tests/integration/test_gate_resume_preflights.py::test_preflights_skip_when_exact_sha_resume_receipt_exists tests/unit/test_operational_discipline_full.py::test_dc1_dispatch_examples tests/unit/test_operational_discipline_full.py::test_dc1_zero_subagents_allowed_without_filler tests/unit/test_precommit_quality_guards.py::test_engine_probe_owns_uv_without_discarding_native_make_carriers[owned-environment-overrides] tests/unit/test_push_discipline_checks_p2.py::TestP30NeverPushFixForwardToMaster::test_agents_forbids_fix_forward_to_master' PYTEST_ARGS='-q -W error -n 0'; \
	run_owner _pytest-runtime-compatibility-admission; \
	echo "PROOF-ADMISSION-PASS admitted_for_proof=true release_ready=false gate_evidence=false"; \
	agent_next passed complete 'Admission prerequisites passed; keep one proof owner and freeze one exact candidate for independent source review, combined acceptance, canonical gate, exact-SHA hosted CI, deployment and reviewed-head promotion proof. Do not edit the frozen candidate or tick tasks from admission alone.'

release-readiness:
	@[ -n "$(TAG)" ] || { echo "Usage: make release-readiness TAG=v0.1.2 RELEASE_READINESS_VALIDATE_ONLY=0|1 RELEASE_COMPLETED_STAGES=stage,... RELEASE_OBSERVATIONS=stage=minutes,... REVIEWED_HEAD_INTEGRATION_RECEIPT=path RELEASE_CANDIDATE_SHA=full-sha RELEASE_READINESS_CI_BRANCH=source-branch-or-empty"; exit 2; }
	@RELEASE_READINESS_VALIDATE_ONLY="$(RELEASE_READINESS_VALIDATE_ONLY)" \
		$(UV) run python scripts/release_readiness.py --root "$(CURDIR)" --tag "$(TAG)" \
		--completed-stages "$(RELEASE_COMPLETED_STAGES)" \
		--observations "$(RELEASE_OBSERVATIONS)" \
		--reviewed-head-integration-receipt "$(REVIEWED_HEAD_INTEGRATION_RECEIPT)" \
		--expected-head-sha "$(RELEASE_CANDIDATE_SHA)" \
		--ci-branch "$(RELEASE_READINESS_CI_BRANCH)" \
		$(if $(filter 1,$(RELEASE_READINESS_VALIDATE_ONLY)),--validate-only,)

# === AC001-AC020 Release Pipeline Integrity Guards ===

_release-completeness-guard:
	@$(UV) run python scripts/check_release_completeness_guard.py $(TAG)

_release-branch-guard:
	@$(UV) run python scripts/check_release_branch_discipline.py

_tag-immutability-guard:
	@$(UV) run python scripts/check_tag_immutability.py $(TAG)

_release-dry-run-guard:
	@$(MAKE) --no-print-directory require-dual-track-green SHA=$$(git rev-parse HEAD)
	@$(UV) run python scripts/check_runbook_currency.py $(TAG)
	@$(UV) run python scripts/check_changelog_accuracy.py $(TAG)
	@$(UV) run python scripts/check_version_bump_atomicity.py $(TAG)
	@$(UV) run python scripts/check_prerelease_flag.py $(TAG)

_tag-signing-guard:
	@$(UV) run python scripts/check_tag_signing.py $(TAG)

check-release-completeness-guard:
	@$(UV) run python scripts/check_release_completeness_guard.py $(TAG)

check-release-branch-discipline:
	@$(UV) run python scripts/check_release_branch_discipline.py

check-tag-immutability:
	@$(UV) run python scripts/check_tag_immutability.py $(TAG)

check-prerelease-flag:
	@$(UV) run python scripts/check_prerelease_flag.py $(TAG)

validate-release-checksums:
	@$(UV) run python scripts/validate_release_checksums.py $(TAG)

check-sbom-freshness:
	@$(UV) run python scripts/check_sbom_freshness.py $(TAG)

verify-container-push:
	@$(UV) run python scripts/verify_container_push.py $(IMAGE) $(TAG)

check-rollback-procedure:
	@$(UV) run python scripts/check_rollback_procedure.py $(TAG)

check-multiplatform-consistency:
	@$(UV) run python scripts/check_multiplatform_consistency.py $(TAG)

check-provenance-attestation:
	@$(UV) run python scripts/check_provenance_attestation.py $(TAG)

check-dependency-pinning:
	@$(UV) lock --check
	@$(UV) run python scripts/check_dependency_pinning.py

check-runbook-currency:
	@$(UV) run python scripts/check_runbook_currency.py $(TAG)

check-changelog-accuracy:
	@$(UV) run python scripts/check_changelog_accuracy.py $(TAG)

check-version-bump-atomicity:
	@$(UV) run python scripts/check_version_bump_atomicity.py $(TAG)

check-tag-signing:
	@$(UV) run python scripts/check_tag_signing.py $(TAG)

RELEASE_PAGE_NOTES_LEDGER ?= config/v012_completed_backlog_reconciliation.json
RELEASE_PAGE_NOTES_OUTPUT ?= docs/releases/v0.1.2.md
RELEASE_PAGE_NOTES_VALIDATE_ONLY ?= 0

.PHONY: release-page-notes _release-page-notes-preview
release-page-notes: ## Build or validate deterministic ledger-backed GitHub release-page notes.
	@[ -n "$(TAG)" ] || { echo "Usage: make release-page-notes TAG=v0.1.2 RELEASE_PAGE_NOTES_LEDGER=path RELEASE_PAGE_NOTES_OUTPUT=path RELEASE_PAGE_NOTES_VALIDATE_ONLY=0|1"; exit 2; }
	@[ -n "$(RELEASE_PAGE_NOTES_LEDGER)" ] || { echo "ERROR: RELEASE_PAGE_NOTES_LEDGER is required"; exit 2; }
	@[ -n "$(RELEASE_PAGE_NOTES_OUTPUT)" ] || { echo "ERROR: RELEASE_PAGE_NOTES_OUTPUT is required"; exit 2; }
	@case "$(RELEASE_PAGE_NOTES_VALIDATE_ONLY)" in 0|1) ;; *) echo "ERROR: RELEASE_PAGE_NOTES_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; esac
	@$(UV) run python scripts/release_page_notes.py "$(TAG)" \
		--ledger "$(RELEASE_PAGE_NOTES_LEDGER)" \
		--output "$(RELEASE_PAGE_NOTES_OUTPUT)" \
		$(if $(filter 1,$(RELEASE_PAGE_NOTES_VALIDATE_ONLY)),--validate-only,)

_release-page-notes-preview:
	@if [ "$(TAG)" = "v0.1.2" ]; then \
		$(MAKE) --no-print-directory release-page-notes \
			TAG="$(TAG)" \
			RELEASE_PAGE_NOTES_LEDGER=config/v012_completed_backlog_reconciliation.json \
			RELEASE_PAGE_NOTES_OUTPUT=docs/releases/v0.1.2.md \
			RELEASE_PAGE_NOTES_VALIDATE_ONLY=1; \
	fi

generate-release-notes:
	@$(UV) run python scripts/generate_release_notes.py $(TAG)

check-asset-retention:
	@$(UV) run python scripts/check_asset_retention.py

check-release-audit-trail:
	@$(UV) run python scripts/check_release_audit_trail.py $(TAG)

release-dry-run: _release-page-notes-preview _release-dry-run-guard
	@echo "=== DRY RUN: All preconditions met for $(TAG) ==="
	@echo "=== Run 'make release-cut TAG=$(TAG) MSG=\"release notes\"' to cut ==="

# === End AC001-AC020 Guards ===

#   1. check-readme-status     — README status table is current for this TAG
#   2. git-push-sandboxcom     — push master
#   3. git-tag-push            — annotated tag + push (triggers CI release job)
#   4. release-view            — confirm the GitHub Release exists
#   5. ci-await               — await the exact tag workflow (bounded at 90 min)
#   6. verify release          — verify the final artifact and complete matrix
# v0.1.1/v0.1.2 require REVIEWED_HEAD_INTEGRATION_RECEIPT and revalidate
# it against the exact promoted SHA before any push or tag mutation.
# Usage: make release-cut TAG=v0.1.0-alpha.1 MSG='release notes' REVIEWED_HEAD_INTEGRATION_RECEIPT=path
release-cut:
	@[ -n "$(TAG)" ] || { echo "Usage: make release-cut TAG=v0.1.0-alpha.1 [MSG='...'] [REVIEWED_HEAD_INTEGRATION_RECEIPT=path]"; exit 1; }
	@set -eu; HEAD_SHA="$$(git rev-parse HEAD)"; SHA_TO_VERIFY="$(RELEASE_CANDIDATE_SHA)"; \
	if [ -z "$$SHA_TO_VERIFY" ]; then SHA_TO_VERIFY="$$HEAD_SHA"; fi; \
	if [ -n "$(RELEASE_CANDIDATE_SHA)$(RELEASE_CI_BRANCH)$(RELEASE_LOCAL_ATTESTATION)" ]; then \
		[ -n "$(RELEASE_CANDIDATE_SHA)" ] && [ -n "$(RELEASE_CI_BRANCH)" ] && [ -n "$(RELEASE_LOCAL_ATTESTATION)" ] || { echo "ERROR: promoted release evidence identity is incomplete"; exit 2; }; \
		[ "$$HEAD_SHA" = "$$SHA_TO_VERIFY" ] || { echo "ERROR: release-cut HEAD does not match promoted candidate"; exit 2; }; \
		[ -f "$(RELEASE_LOCAL_ATTESTATION)" ] || { echo "ERROR: promoted local attestation is missing"; exit 2; }; \
	fi; \
	case "$(TAG)" in v0.1.1|v0.1.2) \
		$(MAKE) --no-print-directory release-readiness TAG="$(TAG)" RELEASE_ALLOW_INCOMPLETE_TASKS=0 RELEASE_ALLOW_INVALID_RECEIPT=0 RELEASE_READINESS_VALIDATE_ONLY=0 RELEASE_COMPLETED_STAGES= RELEASE_OBSERVATIONS= REVIEWED_HEAD_INTEGRATION_RECEIPT="$(REVIEWED_HEAD_INTEGRATION_RECEIPT)" RELEASE_CANDIDATE_SHA="$$SHA_TO_VERIFY" RELEASE_READINESS_CI_BRANCH="$(RELEASE_CI_BRANCH)"; \
		;; \
	esac; \
	$(MAKE) -s require-dual-track-green SHA="$$SHA_TO_VERIFY" CI_BRANCH="$(RELEASE_CI_BRANCH)" DUAL_TRACK_CI_LOCAL_ATTESTATION="$(RELEASE_LOCAL_ATTESTATION)"
	@$(MAKE) -s check-readme-status TAG=$(TAG)
	@$(MAKE) -s git-push-sandboxcom
	@set -e; TAG_SHA="$$(git rev-parse HEAD)"; \
		BASELINE="$$( $(MAKE) -s ci-await BRANCH="$(TAG)" TIMEOUT="$(RELEASE_AWAIT_TIMEOUT)" SHA="$$TAG_SHA" CI_AWAIT_WORKFLOW="Build and Release" CI_AWAIT_EVENT=push CI_AWAIT_INTERVAL="$(RELEASE_AWAIT_INTERVAL)" CI_AWAIT_AFTER_RUN_ID=0 CI_AWAIT_VALIDATE_ONLY=0 CI_AWAIT_SNAPSHOT_ONLY=1 )"; \
		$(MAKE) -s git-tag-push TAG=$(TAG) MSG="$(MSG)"; \
		$(MAKE) -s release-view TAG=$(TAG) || echo "Release record not visible yet; continuing to exact workflow wait."; \
		echo "Waiting for the exact tag workflow after run $$BASELINE before artifact verification..."; \
		$(MAKE) --no-print-directory ci-await BRANCH="$(TAG)" TIMEOUT="$(RELEASE_AWAIT_TIMEOUT)" SHA="$$TAG_SHA" CI_AWAIT_WORKFLOW="Build and Release" CI_AWAIT_EVENT=push CI_AWAIT_INTERVAL="$(RELEASE_AWAIT_INTERVAL)" CI_AWAIT_AFTER_RUN_ID="$$BASELINE" CI_AWAIT_VALIDATE_ONLY=0 CI_AWAIT_SNAPSHOT_ONLY=0
	@$(MAKE) -s verify-release-artifact TAG=$(TAG)
	@$(MAKE) -s verify-release-completeness TAG=$(TAG)

# Compatibility entrypoint: release-promote is the only deployment state machine.
# Usage: make release-deploy TAG=v0.1.0-beta.N MSG=release-notes RELEASE_PROMOTE_VALIDATE_ONLY=0|1
release-deploy: _no-raw-git-guard
	@$(MAKE) --no-print-directory release-promote TAG="$(TAG)" MSG="$(MSG)" RELEASE_PROMOTE_VALIDATE_ONLY="$(RELEASE_PROMOTE_VALIDATE_ONLY)" REVIEWED_HEAD_INTEGRATION_RECEIPT="$(REVIEWED_HEAD_INTEGRATION_RECEIPT)"

# Delete a GitHub Release and its associated git tags (local + remote).
# Usage: make release-delete TAG=v0.1.0-alpha.1
release-delete:
	@[ -n "$(TAG)" ] || { echo "Usage: make release-delete TAG=v0.1.0-alpha.1"; exit 1; }
	@gh release delete "$(TAG)" -R sandboxcom/gludd --yes 2>/dev/null || echo "(release not found on GitHub)"
	@git tag -d "$(TAG)" 2>/dev/null || echo "(tag not found locally)"
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git push sandboxcom :refs/tags/"$(TAG)" 2>/dev/null || echo "(tag not found on remote)"

# Manual fallback: build the single local binary and publish a DRAFT GitHub
# Release. This path cannot produce the full artifact matrix (only CI can), so
# it is CI-green-gated and draft-only: v0.1.0-beta.1 shipped public with 1/12
# assets on a RED SHA through the old ungated version of this target. Finish a
# draft by uploading the remaining assets (release-upload-assets) and passing
# verify-release-completeness, then publish via gh release edit --draft=false.
# Usage: make release-create TAG=v0.1.0-alpha.1
release-create: _release-page-notes-preview
	@[ -n "$(TAG)" ] || { echo "Usage: make release-create TAG=v0.1.0-alpha.1"; exit 1; }
	@$(MAKE) -s require-ci-green
	@$(MAKE) -s build-executable
	@echo "NOTE: INCOMPLETE RELEASE — publishing as DRAFT (single binary only)."
	@PRE=""; echo "$(TAG)" | grep -q -- "-" && PRE="--prerelease"; \
	if [ "$(TAG)" = "v0.1.2" ]; then \
		set -- --notes-file "docs/releases/v0.1.2.md"; \
	else \
		set -- --notes "Release $(TAG) (manual single-binary draft — complete via CI artifacts before publishing)"; \
	fi; \
	gh release create "$(TAG)" -R sandboxcom/gludd dist/gludd --title "$(TAG)" "$$@" --draft $$PRE
	@echo "Draft created. Next: make release-upload-assets TAG=$(TAG) FILES='...' then make verify-release-completeness TAG=$(TAG) before un-drafting."

# Upload additional assets to an EXISTING GitHub Release — the repair path for
# an incomplete release (no other target can add assets after publish).
# --clobber replaces same-name assets so re-runs are idempotent.
# Usage: make release-upload-assets TAG=v0.1.0-beta.1 FILES='dist/a.tar.gz dist/b.deb'
release-upload-assets:
	@[ -n "$(TAG)" ] || { echo "Usage: make release-upload-assets TAG=v0.1.0-beta.1 FILES='<paths>'"; exit 1; }
	@[ -n "$(FILES)" ] || { echo "Usage: make release-upload-assets TAG=v0.1.0-beta.1 FILES='<paths>'"; exit 1; }
	@gh release upload "$(TAG)" -R sandboxcom/gludd $(FILES) --clobber
	@$(MAKE) -s release-view TAG=$(TAG)

# Mark an existing release as a prerelease (repair path: -alpha/-beta/-rc tags
# must carry the prerelease flag; verify-release-completeness enforces this).
# Usage: make release-set-prerelease TAG=v0.1.0-beta.1
release-set-prerelease:
	@[ -n "$(TAG)" ] || { echo "Usage: make release-set-prerelease TAG=v0.1.0-beta.1"; exit 1; }
	@gh release edit "$(TAG)" -R sandboxcom/gludd --prerelease
	@$(MAKE) -s release-view TAG=$(TAG)

ci-faillog:
	@if [ -z "$(RUN)" ]; then echo "Usage: make ci-faillog RUN=<id>"; exit 1; fi
	@gh run view "$(RUN)" -R sandboxcom/gludd --log-failed 2>&1 | tail -120 || echo "ci-faillog-failed"

ci-failure-log:
	@if [ -z "$(RUN)" ]; then echo "Usage: make ci-failure-log RUN=<id>"; exit 1; fi
	@gh run view "$(RUN)" -R sandboxcom/gludd --log-failed 2>&1 || echo "ci-failure-log-failed"

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

# Retirement uses the existing native agent-cleanup/wt-prune-safe owners.

# Read-only: list a worktree's uncommitted changed files (for planning a sync).
wt-changed:
	@[ -n "$(SRC)" ] || { echo "Usage: make wt-changed SRC=<worktree-root>"; exit 1; }
	@cd "$(SRC)" && { git diff --name-only HEAD; git ls-files --others --exclude-standard; } | sort -u | grep -vE '^(\.venv/|REDTEAM_|.*\.log$$)' || echo "(no tracked/untracked changes)"

# Native locks and ignored evidence stay under the existing cleanup owners.

# Reclaim disk safely: remove every CLEAN worktree (git refuses any with
# uncommitted changes, so dirty/unsynced ones are preserved). Branch refs always
# persist, so committed feature branches survive removal and can be merged by
# name or re-checked-out later. Protects the main checkout + the orchestrator cwd.
WT_PRUNE_VALIDATE_ONLY ?= 0
WT_PRUNE_RETIRE_MERGED ?= 0
WT_PRUNE_RETIRE_VALIDATE_ONLY ?= 1
# Capture the selected literal before unexport can restore ambient data under -e.
override WT_PRUNE_VALIDATE_ONLY := $(value WT_PRUNE_VALIDATE_ONLY)
override WT_PRUNE_RETIRE_MERGED := $(value WT_PRUNE_RETIRE_MERGED)
override WT_PRUNE_RETIRE_VALIDATE_ONLY := $(value WT_PRUNE_RETIRE_VALIDATE_ONLY)
unexport WT_PRUNE_VALIDATE_ONLY WT_PRUNE_RETIRE_MERGED WT_PRUNE_RETIRE_VALIDATE_ONLY
ifneq (,$(filter wt-prune-safe,$(MAKECMDGOALS)))
ifneq (,$(findstring $$,$(value BRANCH)$(value WORKTREE)$(value ACTIVE_WORKSTREAM_REGISTRY)$(value WT_PRUNE_VALIDATE_ONLY)$(value WT_PRUNE_RETIRE_MERGED)$(value WT_PRUNE_RETIRE_VALIDATE_ONLY)))
$(error wt-prune-safe inputs must be literal data, not Make expressions)
endif
ifneq ($(value WT_PRUNE_RETIRE_MERGED),0)
ifneq ($(value WT_PRUNE_RETIRE_MERGED),1)
$(error WT_PRUNE_RETIRE_MERGED must be 0 or 1)
endif
endif
ifneq ($(value WT_PRUNE_VALIDATE_ONLY),0)
ifneq ($(value WT_PRUNE_VALIDATE_ONLY),1)
$(error WT_PRUNE_VALIDATE_ONLY must be 0 or 1)
endif
endif
endif

wt-prune-safe:
	@$(if $(filter 1,$(value WT_PRUNE_RETIRE_MERGED)),exec ,)$(UV) run python -m scripts.prune_worktrees_safe \
		$(if $(filter 1,$(value WT_PRUNE_RETIRE_MERGED)),--retire-merged \
		--branch '$(subst ','"'"',$(value BRANCH))' \
		--worktree '$(subst ','"'"',$(value WORKTREE))' \
		$(if $(value ACTIVE_WORKSTREAM_REGISTRY),--registry '$(subst ','"'"',$(value ACTIVE_WORKSTREAM_REGISTRY))') \
		--retire-validate-only '$(subst ','"'"',$(value WT_PRUNE_RETIRE_VALIDATE_ONLY))',\
		$(if $(value ACTIVE_WORKSTREAM_REGISTRY),--registry '$(subst ','"'"',$(value ACTIVE_WORKSTREAM_REGISTRY))') \
		$(if $(filter 1,$(value WT_PRUNE_VALIDATE_ONLY)),--validate-only))

# Prepare a no-commit merge only from a clean checkout without an existing merge.
# Keep caller work/checkpoints intact; abort only a newly opened matching conflict.
# Refuse branch merge options/external drivers before preflight; merge with native ort.
# Usage: make git-merge-nc BR=feature/xxx
.PHONY: git-merge-nc
git-merge-nc: override GLUDD_GIT_MERGE_NC_BR = $(value BR)
git-merge-nc:
	@gludd_merge_next() { \
		if [ "$$1" = passed ]; then \
			printf 'AGENT_NEXT target=git-merge-nc outcome=%s phase=%s gate_evidence=false release_ready=false: Inspect owned FILES with make git-diff-full, then run complete affected Make test owners and normal collection before commit.\n' "$$1" "$$2" >&2; \
		else \
			printf 'AGENT_NEXT target=git-merge-nc outcome=%s phase=%s gate_evidence=false release_ready=false: Inspect make worktree-state; preserve owned state and repair the reported input before retrying. Required gate and exact-SHA proofs remain pending.\n' "$$1" "$$2" >&2; \
		fi; \
	}; \
	[ -n "$$GLUDD_GIT_MERGE_NC_BR" ] || { echo "Usage: make git-merge-nc BR=feature/xxx" >&2; gludd_merge_next refused usage; exit 2; }; \
	MERGE_HEAD_PATH=$$(git rev-parse --git-path MERGE_HEAD) || { MERGE_CHECK_STATUS=$$?; gludd_merge_next failed metadata; exit "$$MERGE_CHECK_STATUS"; }; \
	MERGE_AUTOSTASH_PATH=$$(git rev-parse --git-path MERGE_AUTOSTASH) || { MERGE_CHECK_STATUS=$$?; gludd_merge_next failed metadata; exit "$$MERGE_CHECK_STATUS"; }; \
	if [ -e "$$MERGE_HEAD_PATH" ] || [ -L "$$MERGE_HEAD_PATH" ] || \
		[ -e "$$MERGE_AUTOSTASH_PATH" ] || [ -L "$$MERGE_AUTOSTASH_PATH" ]; then \
		echo "GIT_MERGE_NC_REFUSED existing merge/autostash state" >&2; gludd_merge_next refused existing; exit 2; \
	fi; \
	MERGE_WORK_STATUS=$$(git --no-optional-locks status --porcelain=v1 --untracked-files=all --ignore-submodules=none) || { MERGE_CHECK_STATUS=$$?; gludd_merge_next failed worktree; exit "$$MERGE_CHECK_STATUS"; }; \
	if [ -n "$$MERGE_WORK_STATUS" ]; then \
		echo "GIT_MERGE_NC_REFUSED dirty index/worktree/untracked files" >&2; gludd_merge_next refused worktree; exit 2; \
	fi; \
	MERGE_START_HEAD=$$(git rev-parse --verify HEAD) || { MERGE_CHECK_STATUS=$$?; gludd_merge_next failed reference; exit "$$MERGE_CHECK_STATUS"; }; \
	MERGE_TARGET=$$(git rev-parse --verify --end-of-options "$$GLUDD_GIT_MERGE_NC_BR^{commit}") || { MERGE_CHECK_STATUS=$$?; gludd_merge_next failed reference; exit "$$MERGE_CHECK_STATUS"; }; \
	$(or $(PYTHON),python3) -c 'import os, subprocess, sys; from pathlib import PurePosixPath; \
	branch_result = subprocess.run(["git", "--no-optional-locks", "symbolic-ref", "--quiet", "HEAD"], stdout=subprocess.PIPE, check=False); \
	branch_result.returncode not in (0, 1) and sys.exit(128 - branch_result.returncode if branch_result.returncode < 0 else branch_result.returncode); \
	branch_name = os.fsdecode(branch_result.stdout.rstrip(b"\n")).removeprefix("refs/heads/"); \
	options_result = subprocess.run(["git", "--no-optional-locks", "config", "get", "--null", "branch." + branch_name + ".mergeOptions"], stdout=subprocess.PIPE, check=False) if branch_result.returncode == 0 else None; \
	options_result is not None and options_result.returncode not in (0, 1) and sys.exit(128 - options_result.returncode if options_result.returncode < 0 else options_result.returncode); \
	options_result is not None and options_result.returncode == 0 and options_result.stdout != b"\0" and (print("GIT_MERGE_NC_REFUSED branch mergeOptions", file=sys.stderr), sys.exit(2)); \
	driver_result = subprocess.run(["git", "--no-optional-locks", "config", "get", "--all", "--name-only", "--null", "--regexp", r"^merge\..*\.driver$$"], stdout=subprocess.PIPE, check=False); \
	driver_result.returncode not in (0, 1) and sys.exit(128 - driver_result.returncode if driver_result.returncode < 0 else driver_result.returncode); \
	driver_result.returncode == 0 and (print("GIT_MERGE_NC_REFUSED configured merge driver", file=sys.stderr), sys.exit(2)); \
	merge_result = subprocess.run(["git", "--no-optional-locks", "merge-tree", "--write-tree", "--no-messages", "--name-only", "-z", sys.argv[1], sys.argv[2]], stdout=subprocess.PIPE, check=False); \
	merge_result.returncode not in (0, 1) and sys.exit(128 - merge_result.returncode if merge_result.returncode < 0 else merge_result.returncode); \
	tree_oid, tree_separator, _ = merge_result.stdout.partition(b"\0"); \
	tree_separator or (print("GIT_MERGE_NC_REFUSED invalid merge-tree result", file=sys.stderr), sys.exit(2)); \
	tree_result = subprocess.run(["git", "--no-optional-locks", "rev-parse", "--verify", "--end-of-options", os.fsdecode(tree_oid) + "^{tree}"], stdout=subprocess.PIPE, check=False); \
	tree_result.returncode and sys.exit(128 - tree_result.returncode if tree_result.returncode < 0 else tree_result.returncode); \
	incoming_result = subprocess.run(["git", "--no-optional-locks", "diff-tree", "--no-commit-id", "-r", "-z", "--name-only", "--no-renames", "--diff-filter=AMT", sys.argv[1], os.fsdecode(tree_result.stdout.strip()), "--"], stdout=subprocess.PIPE, check=False); \
	incoming_result.returncode and sys.exit(128 - incoming_result.returncode if incoming_result.returncode < 0 else incoming_result.returncode); \
	ignored_result = subprocess.run(["git", "--no-optional-locks", "ls-files", "-z", "--full-name", "--others", "--ignored", "--exclude-standard"], stdout=subprocess.PIPE, check=False); \
	ignored_result.returncode and sys.exit(128 - ignored_result.returncode if ignored_result.returncode < 0 else ignored_result.returncode); \
	incoming = {PurePosixPath(os.fsdecode(item)) for item in incoming_result.stdout.split(b"\0") if item}; \
	ignored = {PurePosixPath(os.fsdecode(item)) for item in ignored_result.stdout.split(b"\0") if item}; \
	collision = bool((incoming & ignored) or (incoming & {parent for path in ignored for parent in path.parents}) or (ignored & {parent for path in incoming for parent in path.parents})); \
	collision and print("GIT_MERGE_NC_REFUSED ignored checkpoint collision", file=sys.stderr); sys.exit(2 if collision else 0)' "$$MERGE_START_HEAD" "$$MERGE_TARGET" || { MERGE_CHECK_STATUS=$$?; gludd_merge_next failed preflight; exit "$$MERGE_CHECK_STATUS"; }; \
	if git -c merge.autoStash=false merge --strategy=ort --no-autostash --no-ff --no-commit --no-overwrite-ignore -- "$$MERGE_TARGET"; then \
		printf 'merged (uncommitted): %s\n' "$$GLUDD_GIT_MERGE_NC_BR" || exit $$?; gludd_merge_next passed merge; exit $$?; \
	else MERGE_STATUS=$$?; fi; \
	printf 'GIT_MERGE_NC_FAILED merge_exit=%s\n' "$$MERGE_STATUS" >&2; \
	if [ "$$MERGE_STATUS" -eq 1 ] && [ -f "$$MERGE_HEAD_PATH" ] && \
		[ ! -L "$$MERGE_HEAD_PATH" ] && [ ! -e "$$MERGE_AUTOSTASH_PATH" ] && [ ! -L "$$MERGE_AUTOSTASH_PATH" ]; then \
		MERGE_CURRENT_HEAD=$$(git rev-parse --verify HEAD) || { MERGE_CHECK_STATUS=$$?; gludd_merge_next failed ownership; exit "$$MERGE_CHECK_STATUS"; }; \
		MERGE_OPENED_TARGET=$$(git rev-parse --verify MERGE_HEAD) || { MERGE_CHECK_STATUS=$$?; gludd_merge_next failed ownership; exit "$$MERGE_CHECK_STATUS"; }; \
		if [ "$$MERGE_CURRENT_HEAD" = "$$MERGE_START_HEAD" ] && [ "$$MERGE_OPENED_TARGET" = "$$MERGE_TARGET" ]; then \
			if git -c merge.autoStash=false merge --abort; then \
				printf 'GIT_MERGE_NC_ABORTED merge_exit=%s abort_exit=0\n' "$$MERGE_STATUS" >&2; \
			else \
				MERGE_ABORT_STATUS=$$?; \
				printf 'GIT_MERGE_NC_ABORT_FAILED merge_exit=%s abort_exit=%s\n' "$$MERGE_STATUS" "$$MERGE_ABORT_STATUS" >&2; \
				gludd_merge_next failed abort; exit "$$MERGE_ABORT_STATUS"; \
			fi; \
		else echo "GIT_MERGE_NC_STATE_RETAINED merge ownership mismatch" >&2; fi; \
	fi; \
	gludd_merge_next failed merge; exit "$$MERGE_STATUS"

# Selected merged owners retire through agent-cleanup or wt-prune-safe.
# The historical force-prune recipe is intentionally unavailable.
# Read-only: which feature/* branches still have work not yet in master.
# A branch absent here is already merged.
branches-unmerged:
	@git branch --no-merged master | sed 's/^[+* ]*//' | grep -E '^(feature/|worktree-agent-)' | grep -v worktree-agent || echo "(all feature branches merged)"

branches-unmerged-development:
	@git branch --no-merged development --format='%(refname:short)' --sort=refname | grep . || echo "(all local branches merged into development)"

branch-reconciliation-inventory:
	@[ -n "$(RECONCILE_TARGET)" ] && [ -n "$(RECONCILE_LIMIT)" ] && [ "$(origin RECONCILE_AFTER)" != "undefined" ] || { echo "Usage: make branch-reconciliation-inventory RECONCILE_TARGET=development RECONCILE_LIMIT=20 RECONCILE_AFTER=''"; exit 2; }
	@$(UV) run python scripts/branch_reconciliation_inventory.py --target "$(RECONCILE_TARGET)" --limit "$(RECONCILE_LIMIT)" --after "$(RECONCILE_AFTER)"

branch-reconciliation-summary:
	@[ -n "$(RECONCILE_TARGET)" ] && [ -n "$(RECONCILE_LIMIT)" ] && [ "$(origin RECONCILE_DETAILS)" != "undefined" ] && [ "$(origin RECONCILE_CURRENT_ONLY)" != "undefined" ] && [ "$(origin RECONCILE_QUIET_PROGRESS)" != "undefined" ] && [ "$(origin RECONCILE_HEAD_SEMANTICS)" != "undefined" ] || { echo "Usage: make branch-reconciliation-summary RECONCILE_TARGET=development RECONCILE_LIMIT=100 RECONCILE_DETAILS=0 RECONCILE_CURRENT_ONLY=0 RECONCILE_QUIET_PROGRESS=0 RECONCILE_HEAD_SEMANTICS=0"; exit 2; }
	@[ "$(RECONCILE_DETAILS)" = "0" ] || [ "$(RECONCILE_DETAILS)" = "1" ] || { echo "RECONCILE_DETAILS must be 0 or 1"; exit 2; }
	@[ "$(RECONCILE_CURRENT_ONLY)" = "0" ] || [ "$(RECONCILE_CURRENT_ONLY)" = "1" ] || { echo "RECONCILE_CURRENT_ONLY must be 0 or 1"; exit 2; }
	@[ "$(RECONCILE_QUIET_PROGRESS)" = "0" ] || [ "$(RECONCILE_QUIET_PROGRESS)" = "1" ] || { echo "RECONCILE_QUIET_PROGRESS must be 0 or 1"; exit 2; }
	@[ "$(RECONCILE_HEAD_SEMANTICS)" = "0" ] || [ "$(RECONCILE_HEAD_SEMANTICS)" = "1" ] || { echo "RECONCILE_HEAD_SEMANTICS must be 0 or 1"; exit 2; }
	@[ "$(RECONCILE_CURRENT_ONLY)" = "0" ] || [ "$(RECONCILE_DETAILS)" = "1" ] || { echo "RECONCILE_CURRENT_ONLY=1 requires RECONCILE_DETAILS=1"; exit 2; }
	@[ "$(RECONCILE_HEAD_SEMANTICS)" = "0" ] || [ "$(RECONCILE_DETAILS)" = "1" ] || { echo "RECONCILE_HEAD_SEMANTICS=1 requires RECONCILE_DETAILS=1"; exit 2; }
	@$(UV) run python scripts/branch_reconciliation_inventory.py --target "$(RECONCILE_TARGET)" --limit "$(RECONCILE_LIMIT)" --after "" --all-pages $(if $(filter 1,$(RECONCILE_DETAILS)),,--counts-only) $(if $(filter 1,$(RECONCILE_CURRENT_ONLY)),--current-only,) $(if $(filter 1,$(RECONCILE_QUIET_PROGRESS)),--quiet-progress,) $(if $(filter 1,$(RECONCILE_HEAD_SEMANTICS)),--head-semantics,)

ifneq (,$(filter branch-reconciliation-completed-backlog,$(MAKECMDGOALS)))
unexport RECONCILE_TARGET RECONCILE_LIMIT RECONCILE_BASELINE RECONCILE_STAGING_PATH RECONCILE_REPORT_LIMIT RECONCILE_QUIET_PROGRESS RECONCILE_HUMAN
endif

branch-reconciliation-completed-backlog:
	@$(foreach name,RECONCILE_TARGET RECONCILE_LIMIT RECONCILE_BASELINE RECONCILE_STAGING_PATH RECONCILE_REPORT_LIMIT RECONCILE_QUIET_PROGRESS RECONCILE_HUMAN,[ '$(origin $(name))' = 'command line' ] || [ '$(origin $(name))' = 'environment' ] || [ '$(origin $(name))' = 'environment override' ] || { echo "$(name) must be explicitly supplied"; exit 2; };)
	@[ -n '$(subst ','"'"',$(value RECONCILE_TARGET))' ] && [ -n '$(subst ','"'"',$(value RECONCILE_LIMIT))' ] && [ -n '$(subst ','"'"',$(value RECONCILE_BASELINE))' ] && [ -n '$(subst ','"'"',$(value RECONCILE_STAGING_PATH))' ] && [ -n '$(subst ','"'"',$(value RECONCILE_REPORT_LIMIT))' ] && [ -n '$(subst ','"'"',$(value RECONCILE_QUIET_PROGRESS))' ] && [ -n '$(subst ','"'"',$(value RECONCILE_HUMAN))' ] || { echo "Usage: make branch-reconciliation-completed-backlog RECONCILE_TARGET=development RECONCILE_LIMIT=100 RECONCILE_BASELINE=v0.1.1 RECONCILE_STAGING_PATH=config/v012_completed_backlog_reconciliation.json RECONCILE_REPORT_LIMIT=1000 RECONCILE_QUIET_PROGRESS=0 RECONCILE_HUMAN=1"; exit 2; }
	@[ '$(subst ','"'"',$(value RECONCILE_QUIET_PROGRESS))' = '0' ] || [ '$(subst ','"'"',$(value RECONCILE_QUIET_PROGRESS))' = '1' ] || { echo "RECONCILE_QUIET_PROGRESS must be 0 or 1"; exit 2; }
	@[ '$(subst ','"'"',$(value RECONCILE_HUMAN))' = '0' ] || [ '$(subst ','"'"',$(value RECONCILE_HUMAN))' = '1' ] || { echo "RECONCILE_HUMAN must be 0 or 1"; exit 2; }
	@$(UV) run python scripts/branch_reconciliation_inventory.py --target '$(subst ','"'"',$(value RECONCILE_TARGET))' --limit '$(subst ','"'"',$(value RECONCILE_LIMIT))' --after '' --completed-backlog --baseline '$(subst ','"'"',$(value RECONCILE_BASELINE))' --staging-path '$(subst ','"'"',$(value RECONCILE_STAGING_PATH))' --report-limit '$(subst ','"'"',$(value RECONCILE_REPORT_LIMIT))' $(if $(filter 1,$(value RECONCILE_QUIET_PROGRESS)),--quiet-progress,) $(if $(filter 1,$(value RECONCILE_HUMAN)),--human,)

# Anti-overstatement tool: the MEASURED pass-rate of recent CI runs, so
# "reliable"/"green" must be quoted as this ratio, never asserted as an adjective.
ci-greenness:
	@gh run list -R sandboxcom/gludd -L 20 --json conclusion,status 2>/dev/null | $(PYTHON) -c "import sys,json; r=json.load(sys.stdin); done=[x for x in r if x.get('status')=='completed']; g=[x for x in done if x.get('conclusion')=='success']; total=len(done); print('CI greenness (last %d completed runs): %d GREEN, %d not-green = %d%%.' % (total, len(g), total-len(g), (100*len(g)//total if total else 0))); print('  -> Do NOT call CI \"reliable/green\" without quoting this ratio.')" || echo "ci-greenness-failed"

GATE_STATUS_FILE ?= .gate-status
GATE_RELEASE_PYTEST ?= $(UV) run python -m pytest
GATE_RELEASE_MAKE ?= $(MAKE) --no-print-directory
GATE_RELEASE_WORKERS ?= 2
GATE_RELEASE_TAIL_LINES ?= 80

gate-release-phases:
	@RUN_ROOT=$$(mktemp -d /tmp/gludd-gate-release-XXXXXX); \
	trap 'rm -rf "$$RUN_ROOT"' EXIT; trap 'exit 130' INT TERM; \
	if [ -f "$(GATE_STATUS_FILE)" ]; then \
		sed -e '/^=== GATE: PASSED ===$$/d' -e '/^=== GATE: FAILED ===$$/d' \
			"$(GATE_STATUS_FILE)" > "$$RUN_ROOT/status.clean"; \
	else \
		: > "$$RUN_ROOT/status.clean"; \
	fi; \
	mv "$$RUN_ROOT/status.clean" "$(GATE_STATUS_FILE)"; \
	fail_release() { \
		FAILED_PHASE="$$1"; FAILED_RC="$$2"; \
		printf '%s FAIL %s\n' "$$FAILED_PHASE" "$$FAILED_RC" >> "$(GATE_STATUS_FILE)"; \
		printf '%s\n' '=== GATE: FAILED ===' >> "$(GATE_STATUS_FILE)"; \
		return "$$FAILED_RC"; \
	}; \
	run_command() { \
		PHASE="$$1"; shift; RC_FILE="$$RUN_ROOT/$$PHASE.rc"; LOG_FILE="$$RUN_ROOT/$$PHASE.log"; \
		rm -f "$$RC_FILE"; \
		echo "=== GATE RELEASE PHASE: $$PHASE ==="; \
		( "$$@"; COMMAND_RC=$$?; printf '%s\n' "$$COMMAND_RC" > "$$RC_FILE"; exit "$$COMMAND_RC" ) \
			2>&1 | tee -a "$$LOG_FILE"; \
		STREAM_RC=$$?; \
		if [ ! -s "$$RC_FILE" ]; then \
			echo "release phase $$PHASE did not record an exit status" >&2; \
			return 125; \
		fi; \
		COMMAND_RC=$$(cat "$$RC_FILE"); \
		if [ "$$COMMAND_RC" -eq 0 ] && [ "$$STREAM_RC" -ne 0 ]; then \
			echo "release phase $$PHASE output stream failed with exit $$STREAM_RC" >&2; \
			COMMAND_RC="$$STREAM_RC"; \
		fi; \
		if [ "$$COMMAND_RC" -ne 0 ]; then \
			echo "=== BOUNDED FAILURE TAIL: $$PHASE ===" >&2; \
			tail -n "$(GATE_RELEASE_TAIL_LINES)" "$$LOG_FILE" >&2; \
		fi; \
		return "$$COMMAND_RC"; \
	}; \
	case "$(GATE_RELEASE_WORKERS)" in 1|2) ;; *) \
		echo "GATE_RELEASE_WORKERS must be 1 or 2" >&2; \
		fail_release configuration 2; exit 2;; \
	esac; \
	run_command integration $(GATE_RELEASE_PYTEST) tests/integration/ -n "$(GATE_RELEASE_WORKERS)" --maxprocesses="$(GATE_RELEASE_WORKERS)" --maxfail=1 --tb=line --basetemp="$$RUN_ROOT/integration"; RC=$$?; \
	if [ "$$RC" -ne 0 ]; then fail_release integration "$$RC"; exit "$$RC"; fi; \
	printf '%s\n' 'integration PASS 0' >> "$(GATE_STATUS_FILE)"; \
	run_command e2e $(GATE_RELEASE_PYTEST) tests/e2e/ -n 1 --maxfail=1 --tb=line --basetemp="$$RUN_ROOT/e2e"; RC=$$?; \
	if [ "$$RC" -ne 0 ]; then fail_release e2e "$$RC"; exit "$$RC"; fi; \
	printf '%s\n' 'e2e PASS 0' >> "$(GATE_STATUS_FILE)"; \
	SCENARIOS=0; \
	for scenario_dir in molecule/playbooks/*/; do \
		[ -d "$$scenario_dir" ] || continue; \
		SCENARIOS=$$((SCENARIOS + 1)); SCENARIO=$$(basename "$$scenario_dir"); \
		if [ "$$SCENARIO" = "binary_smoke_macos" ]; then \
			run_command macos-artifact $(GATE_RELEASE_MAKE) build-executable; RC=$$?; \
			if [ "$$RC" -ne 0 ]; then fail_release macos-artifact "$$RC"; exit "$$RC"; fi; \
			printf '%s\n' 'macos-artifact PASS 0' >> "$(GATE_STATUS_FILE)"; \
		fi; \
		run_command molecule $(GATE_RELEASE_MAKE) molecule-test SCENARIO="$$SCENARIO"; RC=$$?; \
		if [ "$$RC" -ne 0 ]; then fail_release molecule "$$RC"; exit "$$RC"; fi; \
	done; \
	if [ "$$SCENARIOS" -eq 0 ]; then fail_release molecule 2; exit 2; fi; \
	printf '%s\n' 'molecule PASS 0' >> "$(GATE_STATUS_FILE)"; \
	printf '%s\n' '=== GATE: PASSED ===' >> "$(GATE_STATUS_FILE)"

gate-full: gate-refresh
	@$(MAKE) --no-print-directory gate-release-phases

test-atomic-validate:
	@echo "test-atomic-validate: verify atomic target creation with tempfile validation"

gate-check:
	@echo "gate-check: Run gate check"

chat:
	@$(UV) run python -m general_ludd.cli chat $(if $(MODEL),--model $(MODEL)) $(if $(API_BASE),--api-base $(API_BASE)) $(if $(API_KEY),--api-key $(API_KEY))

chat-eval:
	@[ -n "$(PROMPT)" ] || { echo "Usage: make chat-eval PROMPT='Your prompt here' [MODEL=deepseek] [API_KEY=...]"; exit 1; }
	@$(UV) run python -m general_ludd.cli chat --eval "$(PROMPT)" $(if $(MODEL),--model $(MODEL)) $(if $(API_BASE),--api-base $(API_BASE)) $(if $(API_KEY),--api-key $(API_KEY))

test-chat:
	@$(UV) run python -m pytest \
		tests/unit/test_chat_session.py \
		tests/unit/test_chat_formatter.py \
		tests/unit/test_chat_streaming.py \
		tests/unit/test_chat_context_window.py \
		tests/unit/test_chat_history.py \
		tests/unit/test_chat_history_model.py \
		tests/unit/test_chat_export.py \
		tests/integration/test_chat_cli.py \
		-v

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

# Batch-run multiple test files in the foreground. Accepts FILES= (space-separated paths).
test-batch:
	@if [ -z "$(FILES)" ]; then echo "Usage: make test-batch FILES='tests/unit/test_a.py tests/unit/test_b.py'"; exit 1; fi
	@$(UV) run python -m pytest $(FILES) $(_XD) -v

# Background a test run: accepts TESTFILE= (single) or FILES= (batch).
# Writes log to .gate-logs/test-bg-<ts>.log, PID to .gate-logs/test-bg.pid.
test-bg:
	@if [ -z "$(TESTFILE)" ] && [ -z "$(FILES)" ]; then echo "Usage: make test-bg TESTFILE='...' OR make test-bg FILES='...'"; exit 1; fi
	@mkdir -p .gate-logs
	@if [ -n "$(FILES)" ]; then \
		nohup $(UV) run python -m pytest $(FILES) $(_XD) -v --tb=short > .gate-logs/test-bg-$$(date +%Y%m%d%H%M%S)-$$$$.log 2>&1 & echo $$! | tee .gate-logs/test-bg-$$$$.pid; \
	else \
		nohup $(UV) run python -m pytest $(TESTFILE) -v --tb=short > .gate-logs/test-bg-$$(date +%Y%m%d%H%M%S).log 2>&1 & echo $$! | tee .gate-logs/test-bg.pid; \
	fi
	@echo "check with: make gate-logs   (or tail -f \$$(ls -t .gate-logs/test-bg-*.log | head -1))"

# Background Test Runner — wraps src/general_ludd/runner/background_test_runner.py.
# Usage: make test-bg-runner ACTION=launch TESTFILE='tests/unit/test_foo.py'
#        make test-bg-runner ACTION=status TESTFILE='tests/unit/test_foo.py'
#        make test-bg-runner ACTION=poll-all
#        make test-bg-runner ACTION=kill TESTFILE='tests/unit/test_foo.py'
#        make test-bg-runner ACTION=results TESTFILE='tests/unit/test_foo.py'
# EXTRA passes additional flags (e.g. EXTRA='--wait' or EXTRA='--force').
test-bg-runner:
	@if [ -z "$(ACTION)" ]; then echo "Usage: make test-bg-runner ACTION=launch|status|poll-all|kill|results [TESTFILE='tests/unit/test_foo.py'] [EXTRA='--wait'|'--force']"; exit 1; fi
	@$(UV) run python -m general_ludd.runner.background_test_runner $(ACTION) $(TESTFILE) $(EXTRA)

# Full-suite xdist run with a THREAD-method per-test timeout so an uninterruptible
# hang (which the gate's signal-method timeout can't catch) is force-failed and
# NAMED, instead of stalling the whole run. Diagnostic only.
test-hang-debug:
	@BT="/tmp/gludd-hangdbg"; rm -rf "$$BT"; $(UV) run python -m pytest tests/ -n 2 --dist loadgroup -p no:cacheprovider --timeout=100 --timeout-method=thread --basetemp="$$BT" -q -rf; RC=$$?; rm -rf "$$BT"; exit $$RC

# Wider lint/type scope (#35) — measures lint across ALL tracked python, not just src/tests.
lint-all:
	@$(UV) run ruff check src tests collections scripts alembic tools molecule
typecheck-all:
	@$(UV) run mypy -p general_ludd
	@$(UV) run mypy --explicit-package-bases tools

# Scoped mypy on explicit files (bypasses tree-wide blockers like graylog.py).
# Mirrors pyproject.toml [tool.mypy] strict config (picked up automatically).
# Usage: make typecheck-scope FILES='src/a.py src/b.py'
typecheck-scope: ## Run strict mypy on explicit FILES without unrelated override noise.
	@if [ -z "$(FILES)" ]; then echo "Usage: make typecheck-scope FILES='src/a.py src/b.py'"; exit 2; fi
	@MYPYPATH=src:scripts $(UV) run mypy --explicit-package-bases --no-incremental --no-warn-unused-configs $(FILES)
# Ansible/YAML lint (#36), fail-on-error (no `|| true`).
yaml-lint:
	@set -eu; \
		ANSIBLE_STATE_DIR=$$(mktemp -d "/tmp/gludd-yaml-lint.XXXXXX"); \
		mkdir -p "$$ANSIBLE_STATE_DIR/tmp"; \
		trap 'rm -rf -- "$$ANSIBLE_STATE_DIR"' EXIT INT TERM; \
		YAML_FILES=$$(git ls-files -- \
			':(glob)playbooks/**/*.yml' \
			':(glob)playbooks/**/*.yaml' \
			':(glob)collections/ansible_collections/general_ludd/agent/roles/**/*.yml' \
			':(glob)collections/ansible_collections/general_ludd/agent/roles/**/*.yaml'); \
		test -n "$$YAML_FILES" || { echo "yaml-lint: no tracked YAML files found"; exit 2; }; \
		ANSIBLE_HOME="$$ANSIBLE_STATE_DIR" \
		ANSIBLE_LOCAL_TEMP="$$ANSIBLE_STATE_DIR/tmp" \
		ANSIBLE_LINT_SKIP_SCHEMA_UPDATE=1 \
		PYTHONWARNINGS=error \
		ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=false \
		ANSIBLE_COLLECTIONS_PATH="$(CURDIR)/collections" \
		$(UV) run python scripts/stream_command.py \
			--root ".gate-logs/observed" \
			--label "yaml-lint" \
			--heartbeat-secs "10" \
			--quiet-secs "180" \
			--max-secs "900" \
			--retain-runs "20" \
			-- $(UV) run ansible-lint -q $$YAML_FILES

ci-log:
	@if [ -n "$(RUN)" ]; then \
		gh run view -R sandboxcom/gludd $(RUN) --log-failed 2>&1 || echo "gh-run-view-failed"; \
	else \
		gh run view -R sandboxcom/gludd --log-failed 2>&1 || echo "gh-run-view-failed"; \
	fi

ci-watch:
	@gh run watch -R sandboxcom/gludd $(RUN) --exit-status 2>&1 || echo "gh-run-watch-failed"

# Raw log tail for ONE job in a run, matched by a name substring (e.g.
# "unit-1a"). Needed because ci-log/ci-failed-tests only fetch logs for
# steps with conclusion=failure — a job that hit timeout-minutes gets
# conclusion=cancelled and is invisible to --log-failed. This resolves the
# job's databaseId via --json jobs, then dumps its full log (tailed to the
# last 400 lines, since -v pytest output across a 30-min hang can be huge) so
# we can see the LAST thing that printed before the job was cut off.
# Usage: make ci-job-log RUN=<run-id> JOB=<job-name-substring>
CI_JOB_LOG_VALIDATE_ONLY ?= 0
ci-job-log:
	@case "$(RUN)" in ''|*[!0-9]*) echo "RUN must be a numeric GitHub Actions run ID"; exit 2 ;; esac
	@[ -n "$(JOB)" ] || { echo "Usage: make ci-job-log RUN=<run-id> JOB=<job-name-substring>"; exit 2; }
	@case "$(CI_JOB_LOG_VALIDATE_ONLY)" in 0|1) ;; *) echo "CI_JOB_LOG_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; esac
	@if [ "$(CI_JOB_LOG_VALIDATE_ONLY)" = 1 ]; then echo "CI-JOB-LOG VALIDATED run=$(RUN) job=$(JOB) tail_lines=400 release_ready=false gate_evidence=false"; exit 0; fi; \
	JID=$$(gh run view -R sandboxcom/gludd "$(RUN)" --json jobs --jq "first(.jobs[] | select(.name | contains(\"$(JOB)\"))) | .databaseId // empty"); \
	LOOKUP_RC=$$?; if [ "$$LOOKUP_RC" -ne 0 ]; then echo "ci-job-log: job lookup failed rc=$$LOOKUP_RC"; exit "$$LOOKUP_RC"; fi; \
	case "$$JID" in ''|*[!0-9]*) echo "no valid job matching '$(JOB)' found in run $(RUN)"; exit 1 ;; esac; \
	RESOURCE_ROOT="$$( $(PYTHON) scripts/resource_arbiter.py root )" || exit $$?; \
	mkdir -p "$$RESOURCE_ROOT" || exit $$?; \
	LOG=$$(mktemp "$$RESOURCE_ROOT/ci-job-log-$(RUN)-$$JID.XXXXXX") || exit $$?; \
	trap 'rm -f "$$LOG"' EXIT INT TERM; \
	echo "--- job id: $$JID ---"; \
	gh run view -R sandboxcom/gludd --log --job=$$JID > "$$LOG" 2>&1; LOG_RC=$$?; \
	tail -400 "$$LOG"; TAIL_RC=$$?; \
	if [ "$$LOG_RC" -ne 0 ]; then echo "ci-job-log: native log fetch failed rc=$$LOG_RC; use ci-job-failure-context for a completed job in an active workflow"; exit "$$LOG_RC"; fi; \
	if [ "$$TAIL_RC" -ne 0 ]; then exit "$$TAIL_RC"; fi; \
	if [ ! -s "$$LOG" ]; then echo "ci-job-log: downloaded log is empty"; exit 1; fi

CI_JOB_CONTEXT_VALIDATE_ONLY ?= 0
ci-job-failure-context:
	@[ -n "$(RUN)" ] && [ -n "$(JOB)" ] && [ -n "$(PATTERN)" ] || { echo "Usage: make ci-job-failure-context RUN=<run-id> JOB=<numeric-job-id> PATTERN=<literal> BEFORE=10 AFTER=30 MAX_MATCHES=5"; exit 2; }
	@case "$(RUN):$(JOB):$(or $(BEFORE),10):$(or $(AFTER),30):$(or $(MAX_MATCHES),5):$(CI_JOB_CONTEXT_VALIDATE_ONLY)" in *[!0-9:]*) echo "RUN, JOB, BEFORE, AFTER, MAX_MATCHES, and CI_JOB_CONTEXT_VALIDATE_ONLY must be numeric"; exit 2 ;; esac
	@case "$(CI_JOB_CONTEXT_VALIDATE_ONLY)" in 0|1) ;; *) echo "CI_JOB_CONTEXT_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; esac
	@if [ "$(CI_JOB_CONTEXT_VALIDATE_ONLY)" = "1" ]; then echo "CI-JOB-CONTEXT VALIDATED run=$(RUN) job=$(JOB) before=$(or $(BEFORE),10) after=$(or $(AFTER),30) max_matches=$(or $(MAX_MATCHES),5)"; exit 0; fi; \
	RESOURCE_ROOT="$$( $(PYTHON) scripts/resource_arbiter.py root )"; \
	mkdir -p "$$RESOURCE_ROOT"; \
	LOG=$$(mktemp "$$RESOURCE_ROOT/ci-job-$(RUN)-$(JOB).log.XXXXXX"); \
	trap 'rm -f "$$LOG"' EXIT INT TERM; \
	BOUND=$$(gh api --method GET \
		-H "Accept: application/vnd.github+json" \
		-H "X-GitHub-Api-Version: 2026-03-10" \
		"repos/sandboxcom/gludd/actions/jobs/$(JOB)" \
		--jq 'select(.id == $(JOB) and .run_id == $(RUN)) | .id'); \
	RC=$$?; if [ $$RC -ne 0 ]; then echo "ci-job-failure-context: job lookup failed rc=$$RC"; exit $$RC; fi; \
	if [ "$$BOUND" != "$(JOB)" ]; then echo "ci-job-failure-context: job $(JOB) is not bound to run $(RUN)"; exit 1; fi; \
	gh api --method GET \
		-H "Accept: application/vnd.github+json" \
		-H "X-GitHub-Api-Version: 2026-03-10" \
		"repos/sandboxcom/gludd/actions/jobs/$(JOB)/logs" > "$$LOG"; \
	RC=$$?; if [ $$RC -ne 0 ]; then echo "ci-job-failure-context: log fetch failed rc=$$RC"; exit $$RC; fi; \
	if [ ! -s "$$LOG" ]; then echo "ci-job-failure-context: downloaded log is empty"; exit 1; fi; \
	if ! grep -F -q -- "$(PATTERN)" "$$LOG"; then echo "ci-job-failure-context: pattern not found: $(PATTERN)"; exit 1; fi; \
	$(PYTHON) scripts/ci_shards_log_context.py --artifact-root "$$RESOURCE_ROOT" --artifact-file "$$(basename "$$LOG")" --pattern "$(PATTERN)" --before "$(or $(BEFORE),10)" --after "$(or $(AFTER),30)" --max-matches "$(or $(MAX_MATCHES),5)"

CI_ARTIFACT_OUTPUT_ROOT ?= RESOURCE_ROOT
CI_ARTIFACT_HEARTBEAT_SECS ?= 10
CI_ARTIFACT_DOWNLOAD_VALIDATE_ONLY ?= 0
ci-artifact-download:
	@case "$(RUN)" in ''|*[!0-9]*) echo "RUN must be a numeric GitHub Actions run ID"; exit 2 ;; esac
	@case "$(ARTIFACT)" in ''|*[!A-Za-z0-9._-]*) echo "Refusing unsafe ARTIFACT: $(ARTIFACT)"; exit 2 ;; esac
	@case "$(CI_ARTIFACT_HEARTBEAT_SECS)" in ''|*[!0-9]*|0) echo "CI_ARTIFACT_HEARTBEAT_SECS must be a positive integer"; exit 2 ;; esac
	@case "$(CI_ARTIFACT_DOWNLOAD_VALIDATE_ONLY)" in 0|1) ;; *) echo "CI_ARTIFACT_DOWNLOAD_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; esac
	@RESOURCE_ROOT="$$( $(PYTHON) scripts/resource_arbiter.py root )"; \
	if [ "$(CI_ARTIFACT_OUTPUT_ROOT)" = "RESOURCE_ROOT" ]; then OUTPUT_ROOT="$$RESOURCE_ROOT/ci-artifacts"; else OUTPUT_ROOT="$(CI_ARTIFACT_OUTPUT_ROOT)"; fi; \
	case "$$OUTPUT_ROOT" in "$$RESOURCE_ROOT"/ci-artifacts|"$$RESOURCE_ROOT"/ci-artifacts/*) ;; *) echo "Refusing unsafe CI_ARTIFACT_OUTPUT_ROOT: $$OUTPUT_ROOT"; exit 2 ;; esac; \
	if [ "$(CI_ARTIFACT_DOWNLOAD_VALIDATE_ONLY)" = "1" ]; then echo "CI-ARTIFACT-DOWNLOAD VALIDATED run=$(RUN) artifact=$(ARTIFACT) output=$$OUTPUT_ROOT heartbeat=$(CI_ARTIFACT_HEARTBEAT_SECS)s"; exit 0; fi; \
	ROOT="$$OUTPUT_ROOT/run-$(RUN)"; DEST="$$ROOT/$(ARTIFACT)"; \
	mkdir -p "$$ROOT"; \
	if [ -e "$$DEST" ]; then echo "Refusing to overwrite existing artifact destination: $$DEST"; exit 1; fi; \
	COUNT=$$(gh api repos/sandboxcom/gludd/actions/runs/$(RUN)/artifacts --jq '[.artifacts[] | select(.name == "$(ARTIFACT)" and (.expired | not))] | length'); \
	if [ "$$COUNT" != "1" ]; then echo "Expected exactly one live artifact named $(ARTIFACT) in run $(RUN), found $$COUNT"; exit 1; fi; \
	TMP=$$(mktemp -d "$$ROOT/.download-$(ARTIFACT).XXXXXX"); DOWNLOAD_PID=""; \
	cleanup() { RC=$$?; trap - EXIT INT TERM; if [ -n "$$DOWNLOAD_PID" ] && kill -0 "$$DOWNLOAD_PID" 2>/dev/null; then kill -TERM "$$DOWNLOAD_PID"; wait "$$DOWNLOAD_PID"; fi; if [ -n "$$TMP" ]; then rm -rf "$$TMP"; fi; exit $$RC; }; \
	trap cleanup EXIT INT TERM; \
	echo "artifact-download start run=$(RUN) artifact=$(ARTIFACT)"; \
	gh run download "$(RUN)" -R sandboxcom/gludd -n "$(ARTIFACT)" -D "$$TMP" & DOWNLOAD_PID=$$!; \
	while kill -0 "$$DOWNLOAD_PID" 2>/dev/null; do echo "artifact-download heartbeat run=$(RUN) artifact=$(ARTIFACT)"; sleep "$(CI_ARTIFACT_HEARTBEAT_SECS)"; done; \
	wait "$$DOWNLOAD_PID"; DOWNLOAD_STATUS=$$?; DOWNLOAD_PID=""; \
	if [ "$$DOWNLOAD_STATUS" -ne 0 ]; then echo "artifact download failed rc=$$DOWNLOAD_STATUS"; exit "$$DOWNLOAD_STATUS"; fi; \
	mv "$$TMP" "$$DEST"; TMP=""; \
	echo "CI-ARTIFACT-DOWNLOAD COMPLETE run=$(RUN) artifact=$(ARTIFACT) path=$$DEST"

CI_ARTIFACT_FILE ?=
CI_ARTIFACT_CONTEXT_VALIDATE_ONLY ?= 0
ci-artifact-context:
	@case "$(RUN)" in ''|*[!0-9]*) echo "RUN must be a numeric GitHub Actions run ID"; exit 2 ;; esac
	@case "$(ARTIFACT)" in ''|*[!A-Za-z0-9._-]*) echo "Refusing unsafe ARTIFACT: $(ARTIFACT)"; exit 2 ;; esac
	@case "$(CI_ARTIFACT_FILE)" in ''|*[!A-Za-z0-9._-]*) echo "Refusing unsafe CI_ARTIFACT_FILE: $(CI_ARTIFACT_FILE)"; exit 2 ;; esac
	@case "$(PATTERN)" in ''|*[!A-Za-z0-9._:-]*) echo "PATTERN must use only safe literal token characters"; exit 2 ;; esac
	@case "$(or $(BEFORE),20):$(or $(AFTER),80):$(or $(MAX_MATCHES),5)" in *[!0-9:]*) echo "BEFORE, AFTER, and MAX_MATCHES must be numeric"; exit 2 ;; esac
	@case "$(or $(MAX_MATCHES),5)" in 0) echo "MAX_MATCHES must be positive"; exit 2 ;; esac
	@case "$(CI_ARTIFACT_CONTEXT_VALIDATE_ONLY)" in 0|1) ;; *) echo "CI_ARTIFACT_CONTEXT_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; esac
	@RESOURCE_ROOT="$$( $(PYTHON) scripts/resource_arbiter.py root )"; \
	ARTIFACT_ROOT="$$RESOURCE_ROOT/ci-artifacts/run-$(RUN)/$(ARTIFACT)"; \
	if [ "$(CI_ARTIFACT_CONTEXT_VALIDATE_ONLY)" = "1" ]; then echo "CI-ARTIFACT-CONTEXT VALIDATED run=$(RUN) artifact=$(ARTIFACT) file=$(CI_ARTIFACT_FILE) before=$(or $(BEFORE),20) after=$(or $(AFTER),80) matches=$(or $(MAX_MATCHES),5)"; exit 0; fi; \
	if [ ! -d "$$ARTIFACT_ROOT" ]; then echo "Downloaded artifact root not found: $$ARTIFACT_ROOT"; exit 1; fi; \
	$(PYTHON) scripts/ci_shards_log_context.py --artifact-root "$$ARTIFACT_ROOT" --artifact-file "$(CI_ARTIFACT_FILE)" --pattern "$(PATTERN)" --before "$(or $(BEFORE),20)" --after "$(or $(AFTER),80)" --max-matches "$(or $(MAX_MATCHES),5)"

CI_PYINSTALLER_WARNING_AUDIT_VALIDATE_ONLY ?= 0
ci-pyinstaller-warning-audit:
	@case "$(RUN)" in ''|*[!0-9]*) echo "RUN must be a numeric GitHub Actions run ID"; exit 2 ;; esac
	@case "$(ARTIFACT)" in ''|*[!A-Za-z0-9._-]*) echo "Refusing unsafe ARTIFACT: $(ARTIFACT)"; exit 2 ;; esac
	@case "$(PYINSTALLER_WARNING_ARCHITECTURE_LINUX)" in ''|*[!A-Za-z0-9_-]*) echo "PYINSTALLER_WARNING_ARCHITECTURE_LINUX must be explicit and safe"; exit 2 ;; esac
	@case "$(PYINSTALLER_VERSION_LINUX)" in ''|*[!0-9.]*) echo "PYINSTALLER_VERSION_LINUX must be explicit and numeric"; exit 2 ;; esac
	@case "$(CI_PYINSTALLER_WARNING_AUDIT_VALIDATE_ONLY)" in 0|1) ;; *) echo "CI_PYINSTALLER_WARNING_AUDIT_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; esac
	@RESOURCE_ROOT="$$( $(PYTHON) scripts/resource_arbiter.py root )"; \
	ARTIFACT_ROOT="$$RESOURCE_ROOT/ci-artifacts/run-$(RUN)/$(ARTIFACT)"; \
	if [ "$(CI_PYINSTALLER_WARNING_AUDIT_VALIDATE_ONLY)" = "1" ]; then echo "CI-PYINSTALLER-WARNING-AUDIT VALIDATED run=$(RUN) artifact=$(ARTIFACT) architecture=$(PYINSTALLER_WARNING_ARCHITECTURE_LINUX) PyInstaller=$(PYINSTALLER_VERSION_LINUX)"; exit 0; fi; \
	if [ ! -d "$$ARTIFACT_ROOT" ]; then echo "Downloaded artifact root not found: $$ARTIFACT_ROOT"; exit 1; fi; \
	COUNT=$$(/usr/bin/find "$$ARTIFACT_ROOT" -type f -name warn-gludd.txt -print | /usr/bin/wc -l | /usr/bin/tr -d ' '); \
	if [ "$$COUNT" != "1" ]; then echo "Expected exactly one warn-gludd.txt in $$ARTIFACT_ROOT, found $$COUNT"; exit 1; fi; \
	WARNING=$$(/usr/bin/find "$$ARTIFACT_ROOT" -type f -name warn-gludd.txt -print -quit); \
	echo "CI-PYINSTALLER-WARNING-AUDIT START run=$(RUN) artifact=$(ARTIFACT) warning=$$WARNING"; \
	$(UV) run python scripts/audit_pyinstaller_warnings.py --warnings "$$WARNING" --allowlist "$(PYINSTALLER_WARNING_ALLOWLIST_LINUX)" --platform linux --architecture "$(PYINSTALLER_WARNING_ARCHITECTURE_LINUX)" --pyinstaller-version "$(PYINSTALLER_VERSION_LINUX)" --spec gludd.spec

CI_COVERAGE_RUN ?=
CI_COVERAGE_ARTIFACT ?= coverage-merged
CI_COVERAGE_INPUT ?= xml
CI_COVERAGE_SOURCE ?= src/general_ludd
CI_COVERAGE_AGGREGATE_MIN ?= 85
CI_COVERAGE_PER_FILE_MIN ?= 75
CI_COVERAGE_AUDIT_VALIDATE_ONLY ?= 0
CI_COVERAGE_HEARTBEAT_SECS ?= 10
ci-coverage-artifact-audit:
	@case "$(CI_COVERAGE_RUN)" in ''|*[!0-9]*) echo "CI_COVERAGE_RUN must be a numeric GitHub Actions run ID"; exit 2 ;; esac
	@case "$(CI_COVERAGE_ARTIFACT)" in ''|*[!A-Za-z0-9._-]*) echo "Refusing unsafe CI_COVERAGE_ARTIFACT: $(CI_COVERAGE_ARTIFACT)"; exit 2 ;; esac
	@case "$(CI_COVERAGE_INPUT)" in xml|data) ;; *) echo "CI_COVERAGE_INPUT must be xml or data"; exit 2 ;; esac
	@case "$(CI_COVERAGE_AUDIT_VALIDATE_ONLY)" in 0|1) ;; *) echo "CI_COVERAGE_AUDIT_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; esac
	@case "$(CI_COVERAGE_HEARTBEAT_SECS)" in ''|*[!0-9]*|0) echo "CI_COVERAGE_HEARTBEAT_SECS must be a positive integer"; exit 2 ;; esac
	@RESOURCE_ROOT="$$( $(PYTHON) scripts/resource_arbiter.py root )"; \
	RUN_ROOT="$$RESOURCE_ROOT/ci-artifacts/run-$(CI_COVERAGE_RUN)"; ARTIFACT_DIR="$$RUN_ROOT/$(CI_COVERAGE_ARTIFACT)"; \
	XML="$$ARTIFACT_DIR/coverage.xml"; REPORT="$$ARTIFACT_DIR/coverage-audit.json"; \
	if [ "$(CI_COVERAGE_AUDIT_VALIDATE_ONLY)" = "1" ]; then echo "CI-COVERAGE-ARTIFACT-AUDIT VALIDATED run=$(CI_COVERAGE_RUN) artifact=$(CI_COVERAGE_ARTIFACT) input=$(CI_COVERAGE_INPUT) output=$$REPORT"; exit 0; fi; \
	if [ "$(CI_COVERAGE_INPUT)" = "xml" ]; then \
		if [ ! -f "$$XML" ]; then echo "Hosted coverage artifact is missing: $$XML"; exit 2; fi; \
		$(PYTHON) scripts/audit_coverage.py --xml-file="$$XML" --json-out="$$REPORT" --threshold="$(CI_COVERAGE_AGGREGATE_MIN)" --per-file-threshold="$(CI_COVERAGE_PER_FILE_MIN)" --source="$(CI_COVERAGE_SOURCE)"; \
	else \
		TMP=$$(mktemp -d "$$RUN_ROOT/.coverage-audit.XXXXXX"); JSON_PID=""; \
		cleanup() { RC=$$?; trap - EXIT INT TERM; if [ -n "$$JSON_PID" ] && kill -0 "$$JSON_PID" 2>/dev/null; then kill -TERM "$$JSON_PID"; wait "$$JSON_PID"; fi; rm -rf "$$TMP"; exit $$RC; }; trap cleanup EXIT INT TERM; \
		set --; \
		for NAME in coverage-other-3.11 coverage-unit-1a1-3.11 coverage-unit-1a2-3.11 coverage-unit-1b-3.11 coverage-unit-1d-3.11 coverage-unit-2-3.11 coverage-unit-3a-3.11 coverage-unit-3b-3.11; do \
			DIR="$$RUN_ROOT/$$NAME"; if [ ! -d "$$DIR" ]; then echo "Hosted coverage shard artifact is missing: $$DIR"; exit 2; fi; set -- "$$@" "$$DIR"; \
		done; \
		$(UV) run coverage combine --rcfile=config/coverage_ci_artifacts.ini --keep --data-file="$$TMP/.coverage" "$$@"; COMBINE_RC=$$?; if [ "$$COMBINE_RC" -ne 0 ]; then exit "$$COMBINE_RC"; fi; \
		$(UV) run coverage json --rcfile=config/coverage_ci_artifacts.ini --data-file="$$TMP/.coverage" --show-contexts -o "$$TMP/coverage.json" & JSON_PID=$$!; \
		while kill -0 "$$JSON_PID" 2>/dev/null; do echo "coverage-data heartbeat run=$(CI_COVERAGE_RUN)"; sleep "$(CI_COVERAGE_HEARTBEAT_SECS)"; done; \
		wait "$$JSON_PID"; JSON_RC=$$?; JSON_PID=""; if [ "$$JSON_RC" -ne 0 ]; then exit "$$JSON_RC"; fi; \
		$(PYTHON) scripts/audit_coverage.py --json-file="$$TMP/coverage.json" --json-out="$$REPORT" --threshold="$(CI_COVERAGE_AGGREGATE_MIN)" --per-file-threshold="$(CI_COVERAGE_PER_FILE_MIN)" --source="$(CI_COVERAGE_SOURCE)"; AUDIT_RC=$$?; \
		mv "$$TMP/coverage.json" "$$ARTIFACT_DIR/coverage-data.json"; exit "$$AUDIT_RC"; \
	fi

CI_COVERAGE_GAP_LIMIT ?= 20
CI_COVERAGE_GAP_PLAN_VALIDATE_ONLY ?= 0
ci-coverage-gap-plan:
	@case "$(CI_COVERAGE_RUN)" in ''|*[!0-9]*) echo "CI_COVERAGE_RUN must be a numeric GitHub Actions run ID"; exit 2 ;; esac
	@case "$(CI_COVERAGE_ARTIFACT)" in ''|*[!A-Za-z0-9._-]*) echo "Refusing unsafe CI_COVERAGE_ARTIFACT: $(CI_COVERAGE_ARTIFACT)"; exit 2 ;; esac
	@case "$(CI_COVERAGE_SOURCE)" in src/general_ludd) ;; *) echo "CI_COVERAGE_SOURCE must be src/general_ludd"; exit 2 ;; esac
	@case "$(CI_COVERAGE_GAP_LIMIT)" in ''|*[!0-9]*|0) echo "CI_COVERAGE_GAP_LIMIT must be a positive integer"; exit 2 ;; esac
	@case "$(CI_COVERAGE_GAP_PLAN_VALIDATE_ONLY)" in 0|1) ;; *) echo "CI_COVERAGE_GAP_PLAN_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; esac
	@RESOURCE_ROOT="$$( $(PYTHON) scripts/resource_arbiter.py root )"; \
	DATA="$$RESOURCE_ROOT/ci-artifacts/run-$(CI_COVERAGE_RUN)/$(CI_COVERAGE_ARTIFACT)/coverage-data.json"; \
	XML="$$RESOURCE_ROOT/ci-artifacts/run-$(CI_COVERAGE_RUN)/$(CI_COVERAGE_ARTIFACT)/coverage.xml"; \
	if [ "$(CI_COVERAGE_GAP_PLAN_VALIDATE_ONLY)" = "1" ]; then echo "CI-COVERAGE-GAP-PLAN VALIDATED run=$(CI_COVERAGE_RUN) artifact=$(CI_COVERAGE_ARTIFACT) source=$(CI_COVERAGE_SOURCE) threshold=$(CI_COVERAGE_PER_FILE_MIN) limit=$(CI_COVERAGE_GAP_LIMIT) input=$$DATA-or-$$XML"; exit 0; fi; \
	if [ -f "$$DATA" ]; then INPUT="$$DATA"; MODE="--json"; elif [ -f "$$XML" ]; then INPUT="$$XML"; MODE="--xml"; else echo "Hosted coverage report is missing: $$DATA or $$XML"; exit 2; fi; \
	echo "COVERAGE-GAP-INPUT mode=$$MODE path=$$INPUT"; \
	$(PYTHON) scripts/coverage_missing_lines.py $$MODE "$$INPUT" --threshold "$(CI_COVERAGE_PER_FILE_MIN)" --source "$(CI_COVERAGE_SOURCE)" --limit "$(CI_COVERAGE_GAP_LIMIT)"

# Just the FAILED/ERROR test ids + summary lines from a run's failed-step logs
# (ci-faillog tails raw logs; this filters the signal). Usage: make ci-failed-tests RUN=<id>
ci-failed-tests:
	@if [ -z "$(RUN)" ]; then echo "Usage: make ci-failed-tests RUN=<run-id>"; exit 1; fi
	@gh run view -R sandboxcom/gludd $(RUN) --log-failed 2>/dev/null | grep -E 'FAILED tests/|ERROR tests/|= .*(failed|error).* =' | sort -u || echo "no-failed-test-lines-found"

# Authenticated job-level breakdown plus durable ownership of every failed job
# and non-success step. Usage: make ci-view RUN=<run-id>
ci-view:
	@if [ -z "$(RUN)" ]; then echo "Usage: make ci-view RUN=<run-id>"; exit 1; fi
	@case "$(CI_FAILURE_VALIDATE_ONLY)" in 0|1) ;; *) echo "CI_FAILURE_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; esac
	@$(PYTHON) scripts/ci_failure_ledger.py observe --run "$(RUN)" --repo "$(CI_FAILURE_REPOSITORY)" --ledger "$(CI_FAILURE_LEDGER)" $(if $(filter 1,$(CI_FAILURE_VALIDATE_ONLY)),--validate-only,)

ci-run-view:
	@if [ -z "$(RUN)" ]; then echo "Usage: make ci-run-view RUN=<id>"; exit 1; fi
	@gh run view "$(RUN)" -R sandboxcom/gludd --json jobs,conclusion,headSha,status 2>&1 || echo "ci-run-view-failed"

# Re-run a specific failed run only after observing every failure. An unchanged
# rerun needs both an explicit allow bit and an auditable reason.
ci-rerun: ci-view
	@if [ -z "$(RUN)" ]; then echo "Usage: make ci-rerun RUN=<run-id>"; exit 1; fi
	@case "$(CI_RERUN_ALLOW_UNCHANGED)" in 0|1) ;; *) echo "CI_RERUN_ALLOW_UNCHANGED must be 0 or 1"; exit 2 ;; esac
	@$(PYTHON) scripts/ci_failure_ledger.py guard-rerun --run "$(RUN)" --ledger "$(CI_FAILURE_LEDGER)" $(if $(filter 1,$(CI_RERUN_ALLOW_UNCHANGED)),--allow-unchanged --reason "$(CI_RERUN_REASON)",) $(if $(filter 1,$(CI_FAILURE_VALIDATE_ONLY)),--validate-only,)
	@ci_rerun_guide() { printf '%s\n' "AGENT_NEXT target=ci-rerun outcome=$$1 native_status=$$2 run=$(RUN) reason=$(CI_RERUN_REASON) debug=$$GLUDD_CI_RERUN_DEBUG failed_only=$$GLUDD_CI_RERUN_FAILED_ONLY gate_evidence=false release_ready=false $$3" >&2; }; \
	if [ "$(CI_FAILURE_VALIDATE_ONLY)" = "1" ]; then \
		printf 'CI_RERUN_VALIDATE_ONLY_PASS debug=%s failed_only=%s rerun_requested=false\n' "$$GLUDD_CI_RERUN_DEBUG" "$$GLUDD_CI_RERUN_FAILED_ONLY"; \
		ci_rerun_guide planned 0 'next_owner=ci-rerun; offline plan performs no remote observation, ledger write or rerun request'; \
	else \
		set --; \
		if [ "$$GLUDD_CI_RERUN_DEBUG" = 1 ]; then set -- "$$@" --debug; fi; \
		if [ "$$GLUDD_CI_RERUN_FAILED_ONLY" = 1 ]; then set -- "$$@" --failed; fi; \
		gh run rerun -R "$(CI_FAILURE_REPOSITORY)" "$(RUN)" "$$@"; CI_RERUN_STATUS=$$?; \
		if [ "$$CI_RERUN_STATUS" -eq 0 ]; then \
			ci_rerun_guide requested 0 'write_outcome=accepted next_owner=ci-run-summary; native zero accepts one request only; observe the exact run and attempt before claiming queued or completed work'; \
		else \
			ci_rerun_guide failed "$$CI_RERUN_STATUS" 'write_outcome=unknown next_owner=ci-run-summary; preserve native failure and reconcile the exact run/attempt before retrying a possibly applied request'; \
		fi; \
		exit "$$CI_RERUN_STATUS"; \
	fi

.PHONY: ci-rerun ci-rerun-help
ci-rerun-help:
	@gh run rerun --help

# Recover one exact-SHA run only when all non-success jobs never started and
# GitHub annotated each with its hosted-runner acquisition failure. The guard
# admits attempt 1 only, making this a bounded retry rather than a churn loop.
ci-recover-runner-acquisition: ci-view
	@if [ -z "$(RUN)" ]; then echo "Usage: make ci-recover-runner-acquisition RUN=<run-id>"; exit 1; fi
	@$(PYTHON) scripts/ci_failure_ledger.py guard-runner-acquisition-rerun --run "$(RUN)" --repo "$(CI_FAILURE_REPOSITORY)" --ledger "$(CI_FAILURE_LEDGER)" $(if $(filter 1,$(CI_FAILURE_VALIDATE_ONLY)),--validate-only,)
	@if [ "$(CI_FAILURE_VALIDATE_ONLY)" = "1" ]; then echo "CI_RECOVER_RUNNER_ACQUISITION_VALIDATE_ONLY_PASS"; else gh run rerun -R "$(CI_FAILURE_REPOSITORY)" "$(RUN)"; fi
# Guard remote CI dispatch: the local tree must be clean and sandboxcom/<branch> must equal HEAD.
ci-remote-head-guard:
	@REF="$(REF)"; if [ -z "$$REF" ]; then REF="$$(git branch --show-current)"; fi; \
	REMOTE="$(REMOTE)"; if [ -z "$$REMOTE" ]; then REMOTE=sandboxcom; fi; \
	GIT_SSH_COMMAND="ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new" $(PYTHON) scripts/ci_remote_head_guard.py --ref "$$REF" --remote "$$REMOTE"

# Compatibility entrypoint: every dispatch uses the idempotent exact-SHA signal.
# Keeping this alias safe prevents branch-only dispatches from bypassing run
# discovery, durable dispatch ownership, or full-head confirmation.
ci-trigger: ci-trigger-committed-head
	@echo "ci-trigger: exact-SHA dispatch confirmed"

# List currently in-progress/queued runs for the Build and Release workflow —
# so we know whether a new run is already active on a SHA before re-triggering.
ci-active:
	@gh run list -R sandboxcom/gludd --workflow "Build and Release" --json databaseId,status,conclusion,headSha,createdAt,event -L 10 2>&1 || echo "ci-active-failed"

# ci-busy-check: gate before push — blocks if CI is already running on target branch.
# Prevents "push cancels running CI → zero validation" anti-pattern.
# Usage: make ci-busy-check BRANCH=development
# Exits 1 if CI is busy, 0 if safe to push. FORCE=1 bypasses (hotfix only).
ci-busy-check: _require-gh
	@BRANCH="$(BRANCH)"; if [ -z "$$BRANCH" ]; then BRANCH=$$(git branch --show-current); fi; if [ -z "$$BRANCH" ]; then echo "Cannot check CI from detached HEAD; pass BRANCH=..."; exit 1; fi; FORCE="$(FORCE)" GLUDD_FORCE_PUSH="$(GLUDD_FORCE_PUSH)" $(PYTHON) scripts/ci_push_guard.py "$$BRANCH"

# ci-safe-push: check CI idle on target branch, then push. Blocks if CI busy.
# Usage: make ci-safe-push BRANCH=development
ci-safe-push: ci-busy-check
	@if [ "$(BRANCH)" = "development" ] || [ "$(BRANCH)" = "dev" ]; then \
		$(MAKE) --no-print-directory push-dev; \
	else \
		$(MAKE) --no-print-directory git-push-sandboxcom; \
	fi

# pre-push-check: comprehensive pre-push audit. Runs before any push.
# Checks: CI idle + clean tree + gate fresh/green. Block on any failure.
# Usage: make pre-push-check BRANCH=development
pre-push-check: ci-busy-check check-clean-tree _stash-before-push-guard _pull-before-push-guard
	@if [ ! -f .gate-status ]; then \
		echo "PRE-PUSH: no .gate-status — run 'make gate' (or gate-background) first."; \
		if [ "$$FORCE" != "1" ]; then exit 1; fi; \
	fi
	@# gate-status fresh check: reject if older than 4h or gate was red
	@if [ -f .gate-status ]; then \
		AGE=$$(python3 -c "import os,time;print(int(time.time()-os.path.getmtime('.gate-status')))"); \
		STATE=$$(cat .gate-status 2>/dev/null); \
		if [ "$$AGE" -gt 14400 ] && [ "$$FORCE" != "1" ]; then \
			echo "PRE-PUSH: .gate-status is $$((AGE/3600))h old — re-run 'make gate' first."; \
			exit 1; \
		fi; \
		if echo "$$STATE" | grep -q "FAILED" && [ "$$FORCE" != "1" ]; then \
			echo "PRE-PUSH: gate is RED — fix failures before pushing."; \
			exit 1; \
		fi; \
	fi
	@echo "PRE-PUSH-CHECK: all clear. Safe to push to $(or $(BRANCH),master)."

# push-guarded: push with full pre-push-check gating.
# Usage: make push-guarded BRANCH=development
push-guarded: pre-push-check
	@$(MAKE) --no-print-directory ci-safe-push BRANCH=$(or $(BRANCH),master)

ci-auth:
	@gh auth status 2>&1 || echo "gh-auth-failed"
	@command -v gh >/dev/null 2>&1 && gh --version || echo "gh-not-installed"

# Probe for any tooling that could read the CI run without gh.
install-bats:
	@command -v bats >/dev/null 2>&1 && { echo "bats already installed: $$(bats --version)"; exit 0; } || true
	@command -v brew >/dev/null 2>&1 || { echo "brew MISSING — cannot install bats"; exit 1; }
	@echo "Installing bats-core via brew (may take a minute)..."
	@brew install bats-core 2>&1 | tail -15 || echo "brew-install-bats-failed"
	@command -v bats >/dev/null 2>&1 && bats --version || echo "bats still missing after install"

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
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' ssh -T -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new git@github.com 2>&1 | head -5 || true

ci-remotes:
	@git remote -v 2>&1 || true

# Compare local HEAD to what sandboxcom/master actually has (what CI ran).
ci-diff-since-remote:
	@echo "--- files changed between sandboxcom/master and HEAD ---"
	@git diff --name-only sandboxcom/master..HEAD 2>&1 || echo "(need fetch first)"

ci-head-compare:
	@echo "--- local HEAD ---"; git rev-parse HEAD
	@echo "--- fetching sandboxcom/master ---"
	@GIT_SSH_COMMAND='ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new' git fetch sandboxcom master:refs/remotes/sandboxcom/master 2>&1 | tail -3
	@echo "--- sandboxcom/master HEAD ---"; git rev-parse sandboxcom/master 2>&1 || echo "no sandboxcom/master ref"
	@echo "--- commits local has that remote does NOT ---"
	@git log --oneline sandboxcom/master..HEAD 2>&1 || echo "(cannot compute)"
	@echo "--- commits remote has that local does NOT ---"
	@git log --oneline HEAD..sandboxcom/master 2>&1 || echo "(cannot compute)"
# Unauthenticated API attempt (works only if the repo is public).
ci-status-anon:
	@echo "--- unauthenticated GitHub API (works only if repo public) ---"
	@curl -s -H "Accept: application/vnd.github+json" \
		"https://api.github.com/repos/sandboxcom/gludd/actions/runs?per_page=8" 2>&1 | \
		$(PYTHON) -c "import sys,json; d=json.load(sys.stdin); \
		print('MESSAGE:', d.get('message')) if 'workflow_runs' not in d else [print(r.get('id'), r.get('created_at'), r.get('head_branch'), r.get('status'), r.get('conclusion'), r.get('html_url')) for r in d['workflow_runs']]" 2>&1 || echo "ci-status-anon-failed"

# List workflows (unauthenticated).
gh-actions-workflows:
	@echo "--- workflows ---"
	@gh api /repos/sandboxcom/gludd/actions/workflows 2>&1 | head -20 || echo "gh-api-failed"

# Recent runs with jq (unauthenticated, gh CLI auth).
gh-actions-runs:
	@gh api /repos/sandboxcom/gludd/actions/runs --jq '.workflow_runs[:3] | .[] | {id, conclusion, status, created_at}' 2>&1 || echo "gh-api-failed"

# Org billing (needs org admin).
gh-actions-billing-org:
	@gh api /orgs/sandboxcom/settings/billing/actions 2>&1 || echo "gh-api-failed (likely needs admin)"

# User billing (needs admin).
gh-actions-billing-user:
	@gh api /users/sandboxcom/settings/billing/actions 2>&1 || echo "gh-api-failed (likely needs admin)"

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
