# Gludd Make Target Catalog

Auto-generated from Makefile (3252 lines). Subagents: use ONLY these targets. Any target not listed here DOES NOT EXIST.

## Git

| Target | Params | Description |
|--------|--------|-------------|
| `git-status` | | Short git status |
| `git-log` | | Last 10 commits (oneline) |
| `git-log-n` | `N=20` | Last N commits |
| `git-diff` | | Diff stats |
| `git-diff-full` | `FILES='...'` | Full patch diff |
| `git-staged` | | Staged diff stats |
| `git-stash` | | Stash working tree |
| `git-stash-pop` | | Pop stashed changes |
| `git-show` | `SHA=...` | Show commit stat |
| `git-show-full` | `SHA=...` | Full commit diff |
| `git-show-name-only` | `SHA=...` | Files changed in commit |
| `git-show-commit` | `C=...` | Commit parent + touched files |
| `git-where` | | Branch/HEAD/worktree layout |
| `git-add` | `FILES='...'` | Stage specific files |
| `git-add-all` | | Stage all changes |
| `git-commit` | `MSG='...'` | Commit (requires gate) |
| `git-commit-no-verify` | `MSG='...'` | Commit sans pre-commit hook (gate required) |
| `git-commit-file` | `FILE=...` | Commit using message file |
| `git-amend-msg` | `MSG='...'` | Amend last commit message |
| `commit-no-verify` | `MSG='...'` | Commit -n (gate required) |
| `commit-bootstrap` | `MSG='...'` | Commit with collect-check |
| `repo-commit` | `MSG='...'` | Commit without gate check |
| `ship-commit` | `MSG='...'` | Commit + batch-push |
| `ship-commit-files` | `FILES='...' MSG='...'` | Atomic add+commit+push |
| `test-and-commit` | `MSG='...'` | Full test suite + commit if green |
| `git-reset` | `FILES='HEAD~1'` | Soft reset |
| `git-restore` | `FILES='...'` | Restore files to HEAD |
| `git-rm` | `FILES='...'` | git rm -r |
| `git-rm-cached` | `FILES='...'` | Untrack files |
| `git-mv` | `FROM='...' TO='...'` | git mv |
| `git-branch` | `MSG='name'` | Create branch |
| `git-checkout` | `MSG='name'` | Switch branch |
| `git-merge` | `MSG='name'` | Merge --no-ff |
| `git-merge-nc` | `BR='...'` | Merge --no-commit |
| `git-rebranch-onto` | `NEW='...' BASE='...' RANGE='...'` | Re-root branch |
| `git-revert-files` | `FILES='...'` | Revert working tree files |
| `git-resolve-ours` | `FILES='...'` | Resolve merge conflict (ours) |
| `git-is-ancestor` | `A='...' B='...'` | Check A is ancestor of B |
| `git-revlist-count` | `A='...' B='...'` | Count commits between refs |
| `git-history-file` | `Q='...'` | Full history of a file |
| `git-tracked-keys` | | List tracked key files |
| `git-ls-tracked` | `Q='pattern'` | List tracked files matching pattern |
| `untrack` | `FILES='...'` | git rm --cached |
| `git-index` | | Index git log into SQLite |
| `git-search` | `Q='...' [AUTHOR=] [SINCE=] [LIMIT=100]` | Search indexed history |
| `git-stats` | | Git history index stats |
| `submodule-init` | | Init submodules recursively |
| `submodule-update` | | Update submodules |
| `submodule-status` | | Submodule status |
| `submodule-pin` | `REPO='...' TAG='...'` | Pin submodule to tag |
| `repo-status` | | Alias for git-status |
| `repo-diff` | | Alias for git-diff |
| `repo-staged` | | Alias for git-staged |
| `repo-log` | | Alias for git-log |
| `repo-add-all` | | Alias for git add -A |

## Remote / Push

| Target | Params | Description |
|--------|--------|-------------|
| `git-remote-sandboxcom` | | Configure sandboxcom remote |
| `git-push-sandboxcom` | | Push master to sandboxcom (gate-guarded) |
| `git-push-sandboxcom-nv` | | Push --no-verify (rate-guarded) |
| `git-pull-sandboxcom` | | Pull + rebase from sandboxcom |
| `git-fetch-sandboxcom` | | Fetch from sandboxcom |
| `batch-push` | `COMMIT_THRESHOLD=5` | Push after N+ unpushed commits |
| `batch-push-nv` | | Batch push --no-verify |
| `ci-push` | | Push + wait for CI |
| `ci-push-and-verify` | | Push + poll CI until green |
| `force-push` | | GLUDD_FORCE_PUSH=1 push |
| `push-dev` | | Push development branch |
| `verify-remote` | `SHA=... BRANCH=master` | Verify remote tip matches SHA |
| `deploy-and-forget` | | Push + record timestamp, resume work |
| `git-tag-push` | `TAG='v...' [COMMIT=] [MSG=]` | Create + push annotated tag |
| `git-tag-rm` | `TAG='v...'` | Delete tag locally + remote |

## Testing

| Target | Params | Description |
|--------|--------|-------------|
| `test` | `[TESTFILE=...]` | Full test suite with coverage |
| `test-unit` | `[TESTFILE=...]` | Unit tests only |
| `test-specific` | `TESTFILE='path::Test::method'` | Single test with args |
| `test-integration` | | Integration tests |
| `test-e2e` | | End-to-end tests |
| `test-games` | | E2E game-building tests |
| `test-db` | | DB models tests |
| `test-scripts` | | Skeleton script test |
| `test-guardrails` | | Guardrail infrastructure tests |
| `test-hooks-live` | | Live hook-liveness harness (needs node) |
| `test-tui-daemon` | | TUI daemon start test |
| `test-install` | | Bats install.sh tests |
| `test-count` | | Count collected tests |
| `test-failures` | | Show test failures |
| `test-iso` | `TESTFILE='...' [ID=...]` | Isolated single-file pytest |
| `test-xdist` | `TESTFILE='...'` | Isolated xdist test (--n 2) |
| `test-batch` | `FILES='...'` | Batch-run multiple test files |
| `test-bg` | `TESTFILE='...'` or `FILES='...'` | Background test run |
| `test-bg-runner` | `ACTION=launch\|status\|poll-all\|kill\|results` | Background test runner |
| `test-hang-debug` | | xdist run with thread-method timeout |
| `test-pyver` | `VER=3.11` | Reproduce CI gate under version |
| `test-live-zai` | | Live Z.AI integration tests |
| `test-zai-identity` | | Z.AI identity test |
| `ci-test-1worker` | `VER=3.11` | CI worker-count reproduction |
| `ci-test-eventbus` | | CI eventbus serial-worker test |
| `ci-gate-exact` | `VER=3.11` | Exact CI gate command sequence |

## Quality / Gate

| Target | Params | Description |
|--------|--------|-------------|
| `lint` | | Ruff check src + tests |
| `lint-fix` | | Ruff auto-fix |
| `lint-all` | | Lint all tracked python |
| `typecheck` | | Mypy on src + tests |
| `typecheck-all` | | Mypy on src + scripts + tools |
| `typecheck-scope` | `FILES='...'` | Scoped mypy on explicit files |
| `yaml-lint` | | Ansible-lint playbooks + roles |
| `collect-check` | | Fast collection-error gate |
| `collect-check-e2e-live` | | Collect e2e/live tests only |
| `gate` | | Full gate: lint + typecheck + collect + test + smoke |
| `gate-lite` | | Local validation (unit@2w, no OOM) |
| `gate-audit` | | Gate + coverage audit |
| `gate-async` | `REF=...` | Launch gate detached |
| `gate-background` | | Launch gate via nohup, returns PID |
| `gate-status` | | Print .gate-status |
| `gate-status-check` | | Probe background gate phase |
| `gate-wait` | | Poll gate until terminal |
| `gate-tail` | | Live tail of gate log |
| `gate-logs` | | List gate logs with status |
| `gate-kill` | | Force-kill background gate |
| `gate-cleanup` | | Kill + remove stale PID + old logs |
| `smoke` | | Quick daemon boot health check |
| `healthcheck` | | Verify imports work |
| `qa` | | lint + typecheck + test + healthcheck |
| `validate` | | Full validation (lint + ansible + typecheck + test + smoke) |
| `preflight` | | Preflight quality gate |
| `ruff-audit` | | Return type checker |
| `check-types` | | Flag Any usage in annotations |
| `check-types-baseline` | | Same, tolerating baseline |
| `check-skills-frontmatter` | | Validate skill frontmatter |
| `check-test-env-writes` | | Forbid bare os.environ[] = in tests |
| `check-readme-status` | `TAG='...'` | README version matches tag |
| `scan-secrets` | | Detect-secrets scan against baseline |
| `scan-secrets-baseline` | | Create/update baseline |
| `scan-secrets-fresh` | | Fresh scan (no baseline, no exclusions) |
| `scan-conflicts` | | Scan for merge conflict markers |
| `sast` | | Bandit SAST |
| `sbom` | | CycloneDX SBOM |
| `pip-audit` | | Dependency vulnerability audit |
| `pip-audit-gate` | | Gating audit (fails on new advisories) |
| `pip-upgrade` | | Upgrade pip |
| `security` | | Full security: sast + sbom + pip-audit |
| `security-backlog-gate` | | Landed-guard regression gate |
| `audit-evidence` | | Run evidence tests from TASKS.md |
| `audit-features` | | Feature audit |
| `audit-findings` | | Completion audit |
| `audit-messages` | | OpenCode DB message audit |
| `audit-schema` | | DB schema audit |
| `deps-audit` | | deptry dependency audit |
| `audit-coverage` | `THRESHOLD=85 SOURCE=src/...` | Per-file coverage check |
| `coverage-json` | | Parse existing coverage.json |
| `coverage-key-files` | | Targeted coverage on key files |
| `coverage-key-files-noansible` | | Same, skipping ansible imports |
| `static-coverage` | `THRESHOLD=85` | Static source->test import match |
| `gen-status-table` | | Generate README status table |
| `check-status-table` | | Verify status table |
| `verify-status` | | Verify project status |
| `verify-feature-claims` | | Evidence verification via ansible |

## State / CI

| Target | Params | Description |
|--------|--------|-------------|
| `verify-state` | | Consolidated state report (tree+HEAD+remote+CI) |
| `ci-verdict` | `SHA=...` | Point-in-time CI verdict (0=g, 1=r, 2=p) |
| `ci-verdict-safe` | | Cooldown-enforced CI check |
| `ci-cooldown-status` | | Show remaining cooldown |
| `ci-status` | | Recent CI runs list |
| `ci-view` | `RUN=...` | Job-level CI run breakdown |
| `ci-rerun` | `RUN=...` | Re-run CI jobs |
| `ci-trigger` | | Dispatch Build and Release workflow |
| `ci-active` | | List in-progress/queued runs |
| `ci-faillog` | `RUN=...` | Tail failed CI logs |
| `ci-failed-tests` | `RUN=...` | Extract FAILED/ERROR test IDs |
| `ci-log` | `RUN=...` | Log-failed for latest or specific run |
| `ci-job-log` | `RUN=... JOB=...` | Full log for specific job |
| `ci-watch` | `RUN=...` | gh run watch |
| `ci-wait` | | Poll ci-verdict until green |
| `ci-wait-anon` | `RUN=...` | Poll run-level conclusion |
| `ci-watch-head` | | Watch CI for current HEAD |
| `ci-probe` | | Tool availability probe |
| `ci-auth` | | gh auth status |
| `ci-install-gh` | | Install gh CLI via brew |
| `ci-remotes` | | Show git remotes |
| `ci-diff-since-remote` | | Files changed vs sandboxcom/master |
| `ci-head-compare` | | Local HEAD vs remote |
| `ci-status-anon` | | Unauthenticated CI API |
| `ci-status-api` | | Authenticated CI API |
| `ci-ssh-test` | | Test SSH to GitHub |
| `ci-greenness` | | Measured CI pass rate |
| `gha-usage` | | GitHub Actions usage |
| `pages-status` | | Pages deploy status |
| `pages-enable` | | Enable GitHub Pages |
| `repo-visibility` | | Check repo private/public |
| `gh-actions-workflows` | | List workflows |
| `gh-actions-runs` | | Recent runs via API |
| `gh-actions-billing-org` | | Org billing |
| `gh-actions-billing-user` | | User billing |
| `gh-tags` | `REPO='owner/name'` | Resolve action repo tags->SHAs |
| `gh-action-node` | `REPO='..' TAG='..'` | Node runtime for action tag |
| `ci-verify-wait` | | Dry-run CI verify on HEAD |
| `ci-jobs-anon` | `RUN=...` | Job list (unauthenticated) |
| `ci-annotations-anon` | `RUN=...` | Check-run annotations |
| `ci-joblog-anon` | `JOB=...` | Download job log |
| `ci-checkrun-anno` | `CHECK=...` | Check-run annotations |
| `ci-pyver-list` | | List available Python versions |
| `ci-version-sim` | | Simulate CI version injection |
| `release-view` | `TAG='v...'` | View GitHub Release + assets |
| `release-cut` | `TAG='v...' MSG='...'` | Full release pipeline |
| `release-recut` | `TAG='v...'` | Re-trigger release |
| `release-create` | `TAG='v...'` | Manual release creation |
| `release-validate` | | Build and validate release |
| `verify-release-artifact` | `TAG='v...'` | Confirm published assets |
| `require-ci-green` | `SHA=...` | CI-green precondition |

## Plugin / Enforcement

| Target | Params | Description |
|--------|--------|-------------|
| `write-plugin-manifest` | | Regenerate plugin hash manifest |
| `check-plugin-versions` | | Detect stale plugin hashes |
| `check-plugin-versions-quiet` | | Same, quiet |
| `check-plugin-liveness` | | Structural + runtime plugin check |
| `check-plugin-heartbeats` | | Verify plugins actually firing |
| `write-gate-safe-hook` | | Regenerate gate-safe hook |
| `check-all-guardrails` | | All enforcement checks |
| `check-clean-tree` | | Check working tree is clean |
| `check-clean-tree-status` | | Same via Python script |
| `disengage-enforcement` | | Suspend enforcement for 1 hour |
| `restart-opencode` | | Print restart procedure |

## Watchdog

| Target | Params | Description |
|--------|--------|-------------|
| `watchdog-auto` | | Start agent + task watchdogs |
| `watchdog-start` | | Start agent watchdog (10s poll) |
| `watchdog-status` | | Watchdog status + log tail |
| `watchdog-stop` | | Stop agent watchdog |
| `watchdog-log` | | Last 50 watchdog log lines |
| `task-watchdog-start` | | Start task watchdog (5s poll) |
| `task-watchdog-status` | | Task watchdog status + kill log |
| `task-watchdog-stop` | | Stop task watchdog |
| `task-watchdog-log` | | Last 50 task watch log lines |

## Agent / Worktree

| Target | Params | Description |
|--------|--------|-------------|
| `agent-worktree` | `BRANCH=agent-...` | Create isolated worktree (from master) |
| `agent-worktree-dev` | `BRANCH=agent-...` | Create isolated worktree (from development) |
| `agent-merge` | `BRANCH=agent-...` | Merge worktree branch into master |
| `agent-merge-dev` | `BRANCH=agent-...` | Merge worktree branch into development |
| `agent-cleanup` | `BRANCH=agent-...` | Remove worktree + branch |
| `agent-worktree-list` | | List active worktrees |
| `clean-stale-worktrees` | | Bulk cleanup stale worktrees |
| `clean-worktree-venvs` | | Remove .venv from worktrees |
| `wt-import` | `SRC='...' DST='...'` | Copy file from worktree |
| `wt-sync` | `SRC='...'` | Sync uncommitted worktree changes |
| `wt-sync-all` | `SRCS='wt1 wt2 ...'` | Bulk sync worktrees |
| `wt-apply` | `SRC='...' FILES='...'` | 3-way apply specific files |
| `wt-remove` | `SRC='...'` | Force-remove integrated worktree |
| `wt-remove-locked` | `SRC='...'` | Unlock + force-remove worktree |
| `wt-remove-locked-many` | `SRCS='...'` | Bulk remove locked worktrees |
| `wt-remove-many` | `SRCS='...'` | Bulk remove worktrees |
| `wt-reap` | `KEEP='id1 id2'` | Sync + reclaim completed worktrees |
| `wt-changed` | `SRC='...'` | List worktree's uncommitted files |
| `wt-prune-safe` | | Remove clean worktrees |
| `wt-prune-force-merged` | | Remove worktrees with merged HEAD |

## Branch / Development

| Target | Params | Description |
|--------|--------|-------------|
| `feature-start` | `MSG='feature/name'` | Create + switch to feature branch |
| `feature-done` | `MSG='feature/name'` | Test + merge to master |
| `development-start` | | Create development branch from master |
| `development-status` | | Show unmerged dev commits |
| `development-push` | | Push development to remote |
| `development-merge-to-master` | | Merge dev into master (CI-green required) |
| `branches-unmerged` | | List unmerged feature branches |

## Build / Dist

| Target | Params | Description |
|--------|--------|-------------|
| `build-executable` | | PyInstaller build |
| `bundle-binaries` | | Bundle OpenBao + OpenTofu binaries |
| `bundle-ripgrep` | | Bundle ripgrep binary |
| `dist` | | Full distribution tarball |
| `dist-clean` | | Remove dist artifacts |
| `dist-path-check` | | Scan tarball for leaked local paths |
| `container-build` | | Build container image |
| `container-run` | | Run container locally |
| `container-push` | | Push container image |
| `podman-up` | | Init + start podman machine |
| `podman-restart` | | Restart podman machine |
| `podman-resize` | `VMEM=4096 VCPU=4` | Recreate podman VM |
| `podman-diag` | | Podman diagnostic |
| `ci-repro-linux` | `PYV=3.11` | Reproduce CI Linux gate in container |

## Setup

| Target | Params | Description |
|--------|--------|-------------|
| `init` | | Set up project (dirs + deps) |
| `sync` | | uv sync --locked |
| `relock` | | Regenerate uv.lock + sync |
| `install-pip` | | pip-based setup |
| `setup-dirs` | | Create directory structure |
| `bootstrap` | | init + lint + test + healthcheck |
| `install-hooks` | | Install pre-commit hooks |
| `install-bats` | | Install bats-core via brew |
| `clean` | | Remove build artifacts |
| `clean-tmp` | | Remove scratch tmp dirs |
| `clean-hooks` | | Remove legacy hook scripts |
| `clean-plugins` | | No-op |
| `clean-untracked` | | Remove reinvention-of-wheel files |
| `disk-reclaim` | | Free disk headroom |
| `disk-guard` | | Check disk + clean if above threshold |
| `disk-check` | | Check disk usage only |
| `disk` | | Print disk usage + footprint |
| `version` | | Print version |
| `check-uv` | | Verify uv installed |
| `check-pytest` | | Verify pytest |

## Ansible

| Target | Params | Description |
|--------|--------|-------------|
| `ansible-syntax` | | Validate playbook syntax |
| `ansible-lint-playbooks` | | Lint playbooks with ansible-lint |
| `ansible-collection-test` | | Collection integration tests |
| `playbook-list` | | List registered playbooks |
| `molecule-version` | | Print molecule version |
| `molecule-test` | `SCENARIO=name` | Run single molecule scenario |
| `molecule-test-all` | | Run all molecule scenarios |
| `molecule-test-shard` | `SHARD=1/4` | Sharded molecule tests |
| `molecule-clean` | | Remove stray molecule dirs |
| `collection-roles` | | List collection roles |
| `collection-modules` | | List collection modules |
| `molecule-scenarios` | | List molecule scenarios |

## Misc / Utility

| Target | Params | Description |
|--------|--------|-------------|
| `grep` | `Q='pattern' [PATH_='dir']` | grep -rn in src/tests |
| `grepf` | `Q='pattern' [DIR='dir'] [OUT=/tmp/x]` | Scoped grep to file |
| `lsd` | `DIR='...' [DEPTH=2] [OUT=...]` | List directories to file |
| `lsf` | `DIR='...' [DEPTH=1] [OUT=...]` | List python files to file |
| `lsa` | `DIR='...' [OUT=...]` | ls -la to file |
| `list-tests` | | List all test files |
| `script-count` | | File counts |
| `plan` | `WORK=...` | Orchestration planner |
| `task` | `CMD='...'` | Run command with timeout |
| `run-watched` | `CMD='...' [STALL_SECS=180] [MAX_SECS=3600]` | Stall-watchdog runner |
| `gated-merge` | `BASE='...' BRANCHES='...' [MERGE_STRATEGY=]` | Multi-branch merge |
| `ship-async` | `REF='...' [TARGET=master]` | Background gate + ff-merge |
| `file-executable` | `FILE='path'` | chmod +x |
| `delete-file` | `FILES='...'` | rm files |
| `patch-test` | `FILE='...' MATCH='...' REPLACE='...'` | String replace in file |
| `skill-list` | | List installed skills |
| `skill-install` | `NAME='skill-name'` | Install a skill |
| `bootstrap-skills` | | Install default skills |
| `collect-prompts` | | Collect system prompts |
| `dogfood` | | Run dogfood script |
| `dogfood-features` | | Run dogfood features |
| `analyze-jsonl` | | Analyze tool usage JSONL |
| `bench-langgraph` | `WARMUP=5 ITERS=50` | LangGraph benchmark |
| `game-audit` | | Game audit script |
| `gen-mcp-tools` | | Generate MCP tools |
| `mcp-docs-check` | | MCP docs check |
| `skeleton` | | Generate project skeleton |
| `scan-tool-usage` | | Scan tool usage |
| `status-snapshot` | | Snapshot project status |

## Search / DB

| Target | Params | Description |
|--------|--------|-------------|
| `db-sample-message` | | Sample opencode messages |
| `db-sample-part` | | Sample opencode parts |
| `db-tables` | | List opencode DB tables |
| `db-count` | | Count opencode messages |
| `search-opencode` | `SEARCH='...' [MAX_RESULTS=10]` | Search opencode messages |

## Process Control

| Target | Params | Description |
|--------|--------|-------------|
| `ps-pytest` | | List running pytest/gate processes |
| `ps-gludd` | | Census of gludd processes (PID/PPID/state) |
| `kill-stray` | | Kill stray pytest/gate processes |
| `kill-stale` | | Safe reap of orphaned childless gludd processes |
| `kill-gate-force` | | Force-kill gate lock holder |
| `floor-status` | | Agent-floor counter + liveness |
| `floor-plan` | `STATE=...` | Composite orchestration plan |

## Terraform

| Target | Params | Description |
|--------|--------|-------------|
| `tf-cache-setup` | | Create plugin cache dir |
| `tf-cache-warm` | | Download all providers once |
| `tf-init` | `STACK=stacks/name` | Init a stack with shared cache |
| `tf-validate` | `STACK=stacks/name` | Validate a stack |
| `tf-versions-check` | | Enforce provider version contract |
| `tf-clean` | | Remove shared cache |

## Deck

| Target | Params | Description |
|--------|--------|-------------|
| `deck` | | Build reveal.js deck |
| `deck-build` | | Regenerate deck HTML |
| `deck-serve` | | Serve resolved preview |
| `deck-preview` | | Build scratch preview (no server) |
| `deck-data` | | Generate deck-data.json |
| `deck-honesty` | | Lint deck for banned tokens |
| `deck-clean-assets` | | Remove legacy SVG assets |

## SDD (DevSpark)

| Target | Params | Description |
|--------|--------|-------------|
| `sdd-constitution` | | Show constitution command |
| `sdd-discover` | | Show discover command |
| `sdd-specify` | | Show specify command |
| `sdd-plan` | | Show plan command |
| `sdd-tasks` | | Show tasks command |
| `sdd-implement` | | Show implement command + run gate |
| `sdd-pr` | | Show create-pr command |
| `sdd-release` | | Show release command |
| `sdd-audit` | | Show audit command |
| `sdd-critic` | | Show critic command |
| `sdd-harvest` | | Show harvest command |
| `sdd-quickfix` | | Show quickfix command |

## SearXNG

| Target | Params | Description |
|--------|--------|-------------|
| `searx-up` | | Start SearXNG via docker compose |
| `searx-down` | | Stop SearXNG, remove volumes |
| `searx-test` | | Health-check SearXNG JSON API |

## Help

| Target | Params | Description |
|--------|--------|-------------|
| `help` | | Print usage summary (built-in) |

---

**Total: ~280 targets**

**CRITICAL for subagents:**
- Bash tool = `make <target>` ONLY. No bare commands.
- Use `make test-specific TESTFILE='...'` for single tests.
- Use `make git-commit MSG='...'` for commits (not bare `git commit`).
- Use `make grep Q='pattern'` for code search (not bare `grep`).
- Targets NOT listed here DO NOT EXIST. Do not guess target names.
