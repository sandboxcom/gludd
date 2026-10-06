## Key Make Targets

Run `make help` for the full categorized list (~100 targets). Key targets below.

### Setup
- `make init` - Set up the project (dirs + deps)
- `make sync` - Sync uv dependencies
- `make bootstrap` - init + lint + test + healthcheck
- `make install-hooks` - Install pre-commit hooks (secrets, lint, collect)
- `make clean` - Remove build artifacts

### Quality
- `make lint` - Run ruff linter
- `make lint-fix` - Run ruff with auto-fix
- `make typecheck` - Run mypy
- `make check-types` - Flag `Any` usage in annotations (tight types)
- `make healthcheck` - Verify imports work
- `make collect-check` - Fast collection-error gate (use before every commit)
- `make preflight` - Preflight quality gate (coverage, lint, mypy, templates)
- `make gate` - Full gate: lint + typecheck + collect-check + test; writes `.gate-status`
- `make gate-lite` - Local validation (lint+typecheck+collect+smoke+unit@2w); no OOM. NOT the gate of record. Use between commits for fast feedback.
- `make gate-audit` - Gate + coverage audit (85% per-file threshold)
- `make gate-async` - Launch gate detached (non-blocking); writes `.gate-status`
- `make gate-status` - Print current .gate-status (RUNNING/PASS/FAIL)
- `make qa` - Run lint + typecheck + test + healthcheck
- `make validate` - Full validation including ansible syntax
- `make security` - Full security: sast + sbom + pip-audit
- `make sast` - Run bandit SAST
- `make sbom` - Generate CycloneDX SBOM
- `make pip-audit` - Audit dependencies for vulnerabilities
- `make check-node-v26-compat` - Check Node v26 `--experimental-strip-types` compatibility for plugin code

### Testing
- `make test` - Full test suite with coverage
- `make test-unit` - Unit tests only
- `make test-integration` - Integration tests
- `make test-e2e` - End-to-end tests
- `make test-specific TESTFILE='path::TestClass::test_name'` - Single test
- `make test-count` - Count collected tests
- `make test-failures` - Show test failures
- `make test-guardrails` - Test guardrail infrastructure
- `make test-hook-runtime` - Functional hook runtime tests
- `make test-and-commit` - Run tests then commit if green (`MSG="msg"`)
- `make task CMD='make test-unit'` - Run CMD with timeout (GLUDD_TASK_TIMEOUT=300)
- `make audit-coverage` - Coverage audit with per-file threshold check

### Secrets + Security
- `make secrets-scan` - Scan for secrets against baseline (read-only)
- `make secrets-scrub` - Interactive secret audit + scrub
- `make secrets-baseline` - Rebuild .secrets.baseline
- `make security-audit` - Comprehensive: secrets + sast + pip-audit + backlog gate
- `make clean-artifacts` - Clean build artifacts, caches, temp files

### Git (use ONLY these — NEVER raw git commands)
- `make git-status` - Show git status
- `make git-diff` - Show diff stats
- `make git-staged` - Show staged changes
- `make git-log` - Show recent commits
- `make git-show` - Show last commit diff
- `make git-add FILES='f1 f2 ...'` - Stage specific files
- `make git-add-all` - Stage all changes
- `make git-commit MSG='message'` - Commit staged changes
- `make git-reset FILES='HEAD~1'` - Reset to ref (soft by default)
- `make git-branch MSG='name'` - Create branch
- `make git-checkout MSG='branch'` - Switch branch
- `make git-merge MSG='branch'` - Merge branch with --no-ff
- `make git-stash` - Stash changes
- `make git-stash-pop` - Pop stashed changes
- `make git-rm FILES='...'` - Remove files from git
- `make git-mv OLD='...' NEW='...'` - Rename/move tracked files
- `make git-rebranch-onto` - Rebase current branch onto a new base

### Feature Branch & Worktree Workflow
- `make feature-start MSG='feature/short-name'` - Create and switch to feature branch
- `make feature-done MSG='feature/short-name'` - Test, merge to master with --no-ff
- `make agent-worktree BRANCH=<name>` - Isolated git worktree for a subagent
- `make agent-merge BRANCH=<name>` - Merge a subagent worktree branch into master
- `make agent-cleanup BRANCH=<name>` - Remove a subagent worktree + branch
- `make agent-worktree-list` - List active git worktrees
- `make agent-worktree-dev BRANCH=<name>` - Worktree from development branch
- `make agent-merge-dev BRANCH=<name>` - Merge worktree branch into development

### Development Branch
- `make development-start` - Create development branch from master
- `make development-push` - Push development branch to remote
- `make development-status` - Show commits on development not yet on master
- `make development-merge-to-master` - Merge development into master (CI-green required)

### Git Remote (sandboxcom)
- `make git-remote-sandboxcom` - Configure sandboxcom GitHub remote with SSH key
- `make git-push-sandboxcom` - Push to sandboxcom/gludd mirror
- `make git-pull-sandboxcom` - Pull and rebase from sandboxcom/gludd
- `make git-fetch-sandboxcom` - Fetch from sandboxcom/gludd
- `make ship-async REF=<hash> [TARGET=master]` - Gate in background; ff-only merge on green

### Build + Deploy
- `make dist` - Build distribution tarball
- `make build-executable` - Build standalone executable (pyinstaller)
- `make container-build` - Build container image
- `make container-run` - Run container locally
- `make container-push` - Push container image

### Ansible
- `make ansible-syntax` - Validate playbook syntax
- `make playbook-list` - List registered playbooks
- `make molecule-test` - Run molecule tests

### Terraform
- `make tf-cache-warm` - Download all providers into shared plugin cache
- `make tf-init STACK=s/n` - Init a stack using shared cache
- `make tf-validate STACK=s/n` - Validate a stack
- `make tf-versions-check` - Enforce stacks match infra/terraform/versions.tf
- `make tf-clean` - Remove shared plugin cache

### Submodules
- `make submodule-init` - Initialize all git submodules (recursive)
- `make submodule-update` - Update submodules to latest remote (--merge)
- `make submodule-status` - Show status of each submodule
- `make submodule-pin REPO=.. TAG=..` - Pin a submodule to a tag/commit

### Disk
- `make disk` - Print disk usage + gludd footprint
- `make disk-guard` - Check disk + clean caches if above threshold (95%)
- `make disk-check` - Check disk usage, exit 1 if above threshold
- `make check-disk` - Pre-commit check (fail if /tmp/gludd-* >100MB or disk >90%)
- `make clean-tmp` - Clean /tmp/gludd-* files

### Recovery / Other
- `make crash-recovery` - Reset enforcement state files (`/tmp/gludd-session-start.json` et al.) after a crashed session leaves stale state (PID mismatch / age-gated)
- `make backup-opencode` - Snapshot .opencode/ -> .opencode.orig/ (run before long sessions)
- `make check-opencode-backup` - Warn if .opencode.orig/ is stale (>24h older than .opencode/)
- `make verify-opencode-backup` - Verify backup is current (file listing + shared.ts export parity)
- `make restore-opencode` - Restore .opencode/ (from .opencode.orig/ first, git HEAD fallback) + clear cache
- `make smoke` - Quick daemon boot health check
- `make gated-merge` - Guarded multi-branch merge with manifest
- `make git-index` - Index git log into SQLite (.gludd/git_history.db)
- `make git-search Q='...'` - Search indexed git history
- `make git-stats` - Show git history index statistics

