# Git Workflow State Machine

This project treats git workflow hygiene as executable state, not as an agent memory rule.

## Commands

- `make workflow-state` prints local branch, HEAD, dirty counts, remote ref state, and merge-topology evidence as JSON.
- `make workflow-gate` fails if local state is unsafe for release evidence.
- `make commit-ready` fails when dirty work would make local tests differ from committed code.
- `make gha-ready` fails unless the remote branch points at the exact local HEAD and the local tree is clean.
- `make merge-ready` fails unless `development` contains `master`, so `development -> master` can proceed by normal merge topology instead of cherry-pick repair.

## Ansible Parity

The same guard is exposed through `general_ludd.agent.gludd_git`:

```yaml
- name: Collect and enforce git workflow state
  general_ludd.agent.gludd_git:
    path: /path/to/repo
    op: state
    remote: sandboxcom
    state_assert_clean: true
    state_assert_no_feature_on_master: true
    state_assert_merge_ready: true
    state_assert_remote_head: true
  register: git_state
```

The `general_ludd.agent.git_automation` role exposes the same surface with `git_op: state` and stores `git_automation_state_result`.

## Guarded Failure Classes

- Dirty local tree while claiming local test evidence.
- Remote CI dispatch against a stale branch head.
- GHA evidence whose `head_sha` does not match the local tested commit.
- Feature or guardrail edits directly on `master`.
- `master` commits not contained in `development`, which forces cherry-pick repair instead of release merge.

## Community Findings

Long-lived Git community threads consistently describe cherry-pick as a copy of a change, not a topology-preserving merge. In Stack Overflow discussions, maintainers point out that merge records branch history while cherry-pick creates a different commit identity, and later merges can leave duplicated or confusing history. See:

- https://stackoverflow.com/questions/1241720/git-cherry-pick-vs-merge-workflow
- https://stackoverflow.com/questions/48767783/git-cherry-pick-vs-merge-branches
- https://stackoverflow.com/questions/14486122/how-does-git-merge-after-cherry-pick-work
- https://stackoverflow.com/questions/53972594/what-is-the-difference-between-a-git-merge-and-git-cherry-pick-for-a-specific-co

The project rule is therefore: cherry-pick is emergency recovery, not the release path. If `merge-ready` fails, repair topology first.
