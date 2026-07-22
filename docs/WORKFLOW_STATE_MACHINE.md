# Git Workflow State Machine

This project treats git workflow hygiene as executable state, not as an agent memory rule.

## Commands

- `make workflow-state` prints local branch, HEAD, dirty counts, remote ref state, and merge-topology evidence as JSON.
- `make workflow-gate` fails if local state is unsafe for release evidence.
- `make commit-ready` fails when dirty work would make local tests differ from committed code.
- `make gha-ready` fails unless the remote branch points at the exact local HEAD and the local tree is clean.
- `make merge-ready` fails unless `development` contains `master`, so `development -> master` can proceed by normal merge topology instead of cherry-pick repair.

## Ansible Parity

The same state machine is exposed through `general_ludd.agent.gludd_git` with `op: state`. It returns the same branch, HEAD, dirty/staged/untracked, remote-head, GHA-head, merge-topology, sibling-worktree, and preserved-branch evidence used by the local guard.

```yaml
- name: Collect and enforce git workflow state
  general_ludd.agent.gludd_git:
    path: /path/to/repo
    op: state
    remote: sandboxcom
    state_ref: fix/example
    state_gha_head_sha: 9cf41e400491f4987cd4e1d8c5bfdc8e57cd8e3c
    state_assert_clean: true
    state_assert_no_feature_on_master: true
    state_assert_merge_ready: true
    state_assert_remote_head: true
    state_assert_gha_matches_local: true
    state_worktree_target_ref: HEAD
    state_assert_no_unintegrated_worktrees: true
    state_assert_no_unintegrated_branches: true
    state_reconciled_preserve_head_file: config/reconciled_preserved_heads.txt
  register: git_state
```

The `general_ludd.agent.git_automation` role exposes the same surface with `git_op: state` and stores `git_automation_state_result`. Role variables mirror the module parameters: `state_ref`, `state_remote`, `state_gha_head_sha`, `state_worktree_target_ref`, `state_preserve_branch_patterns`, `state_reconciled_preserve_heads`, `state_reconciled_preserve_head_file`, and every `state_assert_*` flag.

The role also shares the guarded mutation primitives codified for the agent workflow. `git_op: clone`, `git_op: branch`, `git_op: worktree`, `git_op: commit`, `git_op: merge`, and `git_op: push` all delegate to `general_ludd.agent.gludd_git` rather than shelling out in role YAML. `branch_op: list/create/delete` maps to `op: branch_list`, `op: branch`, and `op: branch_delete`; `worktree_op: list/create/remove` maps to `op: worktree_list`, `op: worktree_create`, and `op: worktree_remove`. A non-empty `gate_cmd` or explicit `git_op: gated_commit` routes commits to `op: gated_commit` and stages `commit_files` before committing. A non-empty `gate_cmd` or explicit `git_op: gated_merge` routes merges to `op: gated_merge`, validates the merged tree, and rolls back on gate failure.

`general_ludd.agent.gludd_git` also exposes the remaining local git control-plane helpers used by the agent workflow: `op: current_branch`, `op: tag_release`, `op: tag_checkpoint`, `op: release_tag`, and `op: checkpoint_tag`. These role and module paths intentionally avoid direct git shell tasks for control-plane operations so per-repo locking, non-interactive credentials, bounded subprocesses, leading-dash ref rejection, end-of-options separators, fail-closed gate handling, and typed results stay centralized in `general_ludd.git_automation`.

Preserved branches are reconciled by exact HEAD SHA, not by branch name. Add only audited preserve-branch HEADs to `config/reconciled_preserved_heads.txt`; a later commit on that branch will block again until its new HEAD is reviewed.

## Guarded Failure Classes

- Dirty local tree while claiming local test evidence.
- Remote CI dispatch against a stale branch head.
- GHA evidence whose `head_sha` does not match the local tested commit.
- Feature or guardrail edits directly on `master`.
- `master` commits not contained in `development`, which forces cherry-pick repair instead of release merge.
- Sibling worktrees with dirty or unmerged changes.
- Preserved local branches with unreconciled unique commits.

## Community Findings

Long-lived Git community threads consistently describe cherry-pick as a copy of a change, not a topology-preserving merge. In Stack Overflow discussions, maintainers point out that merge records branch history while cherry-pick creates a different commit identity, and later merges can leave duplicated or confusing history. See:

- https://stackoverflow.com/questions/1241720/git-cherry-pick-vs-merge-workflow
- https://stackoverflow.com/questions/48767783/git-cherry-pick-vs-merge-branches
- https://stackoverflow.com/questions/14486122/how-does-git-merge-after-cherry-pick-work
- https://stackoverflow.com/questions/53972594/what-is-the-difference-between-a-git-merge-and-git-cherry-pick-for-a-specific-co

The project rule is therefore: cherry-pick is emergency recovery, not the release path. If `merge-ready` fails, repair topology first.


### Ansible git role context

The Ansible collection deliberately delegates workflow checks to `general_ludd.git_automation` instead of relying on plain `ansible.builtin.git` state alone. Ansible documents `GIT_TERMINAL_PROMPT=0` / `GIT_ASKPASS` as the way to avoid authentication hangs on missing credentials, which matches the gludd non-interactive git environment: https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/git_module.html

Long-lived Ansible forum threads also show why gludd keeps explicit state evidence. Operators have reported git tasks marked changed even when `before` and `after` SHAs match (`remote_url_changed: true`), and checkout/submodule flows can fail on unexpected local dirty state. These are exactly the classes guarded by `state_assert_clean`, `state_assert_remote_head`, `state_assert_gha_matches_local`, `state_assert_no_unintegrated_worktrees`, and `state_assert_no_unintegrated_branches`:

- https://forum.ansible.com/t/git-module-often-marked-as-changed-even-when-nothing-changed/31555
- https://forum.ansible.com/t/error-your-local-changes-to-the-following-files-would-be-overwritten-by-checkout/20465
