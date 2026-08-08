# E2E Test Project — Minimal Makefile
# All tasks are trivial sub-second or 1-second operations.

.PHONY: all help clean \
	task1 task2 task3 task4 task5 task6 task7 task8 task9 task10 \
	task11 task12 task13 task14 task15 task16 task17 task18

all: task1 task2 task3 task4 task5 task6 task7 task8 task9 task10 \
     task11 task12 task13 task14 task15 task16 task17 task18

task1:
	@mkdir -p output
	@echo "hello world" > output/task1.txt

task2:
	@mkdir -p output
	@date > output/task2.txt

task3:
	@mkdir -p output
	@for i in $$(seq 1 10); do echo $$i; done > output/task3.txt

task4:
	@mkdir -p output
	@echo "pong" > output/task4.txt

task5:
	@mkdir -p output
	@sleep 1 && echo "awake" > output/task5.txt

task6:
	@mkdir -p output
	@for i in $$(seq 1 5); do echo $$i; done > output/task6.txt

task7:
	@mkdir -p output
	@echo "lorem ipsum" > output/task7.txt

task8:
	@mkdir -p output
	@ls output/ 2>/dev/null | wc -l | tr -d ' ' > output/task8.txt

task9:
	@mkdir -p output
	@echo "foo bar baz" > output/task9.txt

task10:
	@mkdir -p output
	@echo "quick brown fox" > output/task10.txt

task11:
	@mkdir -p output
	@echo "alpha beta gamma" > output/task11.txt

task12:
	@mkdir -p output
	@echo "one two three four five" > output/task12.txt

task13:
	@mkdir -p output
	@echo "test 13 complete" > output/task13.txt

task14:
	@mkdir -p output
	@echo "task fourteen done" > output/task14.txt

task15:
	@mkdir -p output
	@echo "@15 -- first wave final" > output/task15.txt

task16:
	@mkdir -p output
	@wc -l < output/task6.txt 2>/dev/null | tr -d ' ' > output/task16.txt

task17:
	@mkdir -p output
	@echo "seventeen" > output/task17.txt

task18:
	@mkdir -p output
	@echo "all eighteen done" > output/task18.txt

clean:
	rm -rf output/

help:
	@echo "Targets: task1..task18, all, clean, help"

## Shim targets for enforcement plugin compatibility

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
