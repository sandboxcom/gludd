# E2E Test Project — Minimal Makefile
# All tasks are simple, sub-second operations.

.PHONY: all t1-hello t2-count t3-math t4-list-txt t5-date t6-identity t7-numbers t8-line-count t9-multiply t10-json clean help

all: t1-hello t2-count t3-math t4-list-txt t5-date t6-identity t7-numbers t8-line-count t9-multiply t10-json

## Task targets

t1-hello:
	@mkdir -p output
	@echo "hello world" > output/hello.txt

t2-count:
	@ls output/ | wc -l | tr -d ' ' > output/count.txt

t3-math:
	@mkdir -p output
	@echo "4" > output/math.txt

t4-list-txt:
	@ls *.txt 2>/dev/null | sort > output/txt_files.txt

t5-date:
	@mkdir -p output
	@date -u +%Y-%m-%dT%H:%M:%SZ > output/date.txt

t6-identity:
	@mkdir -p output
	@echo 'import sys; print(sys.argv[1])' > output/identity.py

t7-numbers:
	@mkdir -p output
	@for i in $$(seq 1 10); do echo $$i; done > output/numbers.txt

t8-line-count:
	@wc -l < output/numbers.txt | tr -d ' ' > output/line_count.txt

t9-multiply:
	@echo "21" >> output/math.txt

t10-json:
	@mkdir -p output
	@echo '{"greeting": "hello", "target": "world"}' > output/hello.json

## Utility targets

clean:
	rm -rf output/

help:
	@echo "Targets: all t1-hello t2-count t3-math t4-list-txt t5-date"
	@echo "        t6-identity t7-numbers t8-line-count t9-multiply t10-json"

## Shim targets for enforcement plugin compatibility
## (plugins reference these; they must exist but can be no-ops here)

.PHONY: git-status git-log git-diff git-staged git-add git-add-all git-commit git-reset
.PHONY: git-stash git-stash-pop git-branch git-checkout git-merge git-rm git-mv
.PHONY: git-push-sandboxcom git-pull-sandboxcom git-fetch-sandboxcom
.PHONY: ship-commit batch-push verify-remote verify-state verify-release-completeness
.PHONY: ci-verdict ci-verdict-safe ci-verdict-fast list-plugins verify-enforcement
.PHONY: check-node-v26-compat check-plugin-hook-invoke check-plugin-validate check-plugin-runtime
.PHONY: check-disk clean-tmp crash-recovery reload-enforcement disengage-enforcement
.PHONY: gate gate-background gate-status-check gate-kill gate-status
.PHONY: lint typecheck collect-check test test-unit test-count task
.PHONY: check-system-load check-enhancement-ratio check-duplicate-targets
.PHONY: check-readme-status check-tdd-compliance test-hook-runtime
.PHONY: active-work-status ps ps-gludd watchdog-auto
.PHONY: agent-worktree agent-merge agent-cleanup agent-worktree-list
.PHONY: agent-merge-dev agent-worktree-dev
.PHONY: check-make-target-contract git-index git-search git-stats
.PHONY: worktree-health-check worktree-merge-all

git-status git-log git-diff git-staged:
	@true

git-add git-add-all git-commit git-reset:
	@true

git-stash git-stash-pop git-branch git-checkout git-merge git-rm git-mv:
	@true

git-push-sandboxcom git-pull-sandboxcom git-fetch-sandboxcom:
	@true

ship-commit batch-push verify-remote verify-state verify-release-completeness:
	@true

ci-verdict ci-verdict-safe ci-verdict-fast:
	@true

list-plugins verify-enforcement:
	@true

check-node-v26-compat check-plugin-hook-invoke check-plugin-validate check-plugin-runtime:
	@true

check-disk clean-tmp crash-recovery reload-enforcement disengage-enforcement:
	@true

gate gate-background gate-status-check gate-kill gate-status:
	@true

lint typecheck collect-check test test-unit test-count task:
	@true

check-system-load check-enhancement-ratio check-duplicate-targets:
	@true

check-readme-status check-tdd-compliance test-hook-runtime:
	@true

active-work-status ps ps-gludd watchdog-auto:
	@true

agent-worktree agent-merge agent-cleanup agent-worktree-list:
	@true

agent-merge-dev agent-worktree-dev:
	@true

check-make-target-contract git-index git-search git-stats:
	@true

worktree-health-check worktree-merge-all:
	@true
