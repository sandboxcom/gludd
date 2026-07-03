# git_commit_push

Complete commit-and-push workflow with idempotent tree-clean gate, workflow-aware SSH/HTTPS remote selection, and optional push verification.

## What it does

1. **Gathers daemon facts** (`gludd_facts`) for system context.
2. **Checks tree cleanliness** — runs `git status --porcelain`; if no uncommitted changes, ends the play (idempotent).
3. **Detects workflow-file changes** — checks if any changed path matches `.github/workflows/`.
4. **Commits** via `gludd_git` op=commit (which also stages via `git add -A`).
5. **Selects push remote** — `ssh_remote` (sandboxcom) when workflow files changed; `https_remote` (origin) otherwise.
6. **Pushes** via `gludd_git` op=push — only when `enable_git_push=true` AND not `check_mode`.
7. **Verifies push** via `make verify-remote` — optional, gated by `enable_push_verify`.
8. **Writes artifacts** — `git_commit_push.json` + `git_commit_push.md`.
9. **Reports outcome** via `debug`.

## Safety model

| Guard | Default | Effect |
|---|---|---|
| `enable_git_commit` | `true` | Commit runs (local only, non-destructive) |
| `enable_git_push` | `false` | Push NEVER runs unless explicitly set to `true` |
| `enable_push_verify` | `false` | Verification step is opt-in |
| Tree clean gate | — | Role exits early with `nothing_to_commit` when tree is clean |

## Outcome states

| Outcome | Meaning |
|---|---|
| `nothing_to_commit` | Tree was clean — nothing to do |
| `commit_disabled` | Uncommitted changes exist but `enable_git_commit=false` (play fails) |
| `committed_no_push` | Committed locally; push not requested |
| `pushed` | Committed and pushed successfully |
| `push_failed` | Committed but push failed |
| `check_mode` | Running in check mode — nothing mutated |

## Remote selection logic

```
changed_files ∩ .github/workflows/*  →  ssh_remote (sandboxcom)
otherwise                            →  https_remote (origin)
```

Override with `ssh_remote` / `https_remote` variables.

## Key variables

| Variable | Default | Description |
|---|---|---|
| `enable_git_push` | `false` | Set `true` to push after commit |
| `enable_git_commit` | `true` | Set `false` to skip commit (role will fail if tree is dirty) |
| `enable_push_verify` | `false` | Set `true` to run `make verify-remote` after push |
| `commit_message` | `"auto: apply change"` | Commit message |
| `push_branch` | `"master"` | Branch to push |
| `ssh_remote` | `"sandboxcom"` | Remote for workflow-file changes |
| `https_remote` | `"origin"` | Remote for all other changes |
| `workflow_glob` | `'^.github/workflows/'` | Regex for workflow-file detection |
| `repo_path` | `"."` | Path to the git repository |
| `artifact_dir` | `"/tmp/gludd-git-commit-push"` | Output directory for artifacts |

## Example

```yaml
- name: Commit and push feature work
  ansible.builtin.include_role:
    name: general_ludd.agent.git_commit_push
  vars:
    commit_message: "feat: add workflow-driven deploy"
    enable_git_push: true
    push_branch: "feature/deploy-workflow"
```
