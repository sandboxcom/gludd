# `general_ludd.git_release` — Git Mastery & Release Captain Collection

Ansible collection providing evidence-driven Git and release-captain roles.
MVP scope is **read-only**: every role observes and reports, none mutate.

Implements the MVP slice of `docs/specs/FEATURE_GIT_RELEASE_CAPTAIN_EXPERT.md`
(GRC-001). The Python evidence layer wraps `general_ludd.git_automation` — it
never re-implements git operations.

## MVP Roles

| Role | Purpose | Phase |
|---|---|---|
| `repo_assess` | Collect `RepoEvidence` (HEAD, branch, dirty paths, worktrees, upstreams, in-progress operations, repo policies) without mutation | GRC-P1 |
| `history_investigate` | Read-only log/blame/range-diff probes (scaffold — delegates to `git_automation.git_stats`) | GRC-P1 |
| `work_recover` | Survey reflog, stashes, dangling objects; produce a reversible recovery plan (scaffold — read-only survey only in MVP) | GRC-P1/P2 |
| `helper_discover` | Discover Makefile, CI, build, and runbook entry points with authority ranking (scaffold) | GRC-P3 |
| `branch_plan` | Plan branch create/sync/backport/merge with collision evidence (scaffold — plan only, no mutation) | GRC-P2 |

The remaining 9 roles from the spec (`conflict_resolve`, `helper_select`,
`helper_build`, `release_plan`, `pipeline_triage`, `artifact_build`,
`artifact_verify`, `deploy_orchestrate`, `release_recover`) land in later
phases. Each mutating role ships default-off behind a capability flag.

## Python Evidence Layer

| Module | Exports |
|---|---|
| `general_ludd.git_release.evidence` | `RepoEvidence`, `collect_repo_evidence(repo_path)` |
| `general_ludd.git_release.topology` | `describe_topology(repo_path)` — branch + worktree summary |

`collect_repo_evidence` returns the normalized record defined in spec §5.1.
It wraps `GitAutomation.is_repo`, `current_branch`, `get_current_commit`,
`workflow_state`, `list_worktrees`, and `list_branches` — read-only, no
fetches, no index writes, no ref mutation.

## Quick start

```yaml
- name: Assess repository state without mutation
  hosts: localhost
  vars:
    repo_path: "/Users/shawnwilson/gludd"
  roles:
    - general_ludd.git_release.repo_assess
```

## Safety contract

- Every role is read-only in MVP. No mutating git operation is invoked.
- `RepoEvidence` is JSON-serializable and deterministic for a fixed tree state.
- The collector fails closed on a non-repo path (`NotARepoError`).

## Dependencies

- `general_ludd.git_automation` (bundled — no external git libraries)
