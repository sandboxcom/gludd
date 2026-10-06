# Patch-Equivalent Cherry-Pick Guard

## Operator contract

`make git-cherry-pick` and `make git-cherry-pick-list` reject a commit when
the patch it introduces is already represented in `HEAD` under another commit
ID. Both targets retain the active-gate mutation guard. The list target checks
every requested commit before applying the first one, so a duplicate later in
the list cannot leave an earlier unique commit partially integrated.

The guard delegates patch identity to Git itself. It resolves the requested
commit and reads `git cherry HEAD <resolved-commit>`; Git marks the requested
commit `-` when an equivalent patch is already upstream and `+` when the patch
is unique. An invalid ref, an unclassifiable history, an already-reachable
commit, or a `-` result fails closed before `git cherry-pick` runs. The guard
does not reproduce Git's patch-ID algorithm in project code.

Use the non-mutating target-contract examples to validate the command surface:

```console
make git-cherry-pick SHA=HEAD CHERRY_PICK_VALIDATE_ONLY=1
make git-cherry-pick-list SHAS=HEAD CHERRY_PICK_VALIDATE_ONLY=1
```

Set `CHERRY_PICK_VALIDATE_ONLY=0` only for the intended mutation. A `+` result
does not promise that a genuinely unique patch will merge without conflict; it
only proves that Git did not find an equivalent patch in the current history.

## Zero-downtime and rollback

The preflight is read-only and runs before index, worktree, or `HEAD` mutation.
It starts no service, changes no schema or listener, and requires no restart.
For a list, the complete patch-equivalence pass finishes before the existing
shared-file checks and ordered cherry-picks begin. If a unique patch later
encounters an ordinary content conflict, use `make git-cherry-pick-abort` to
restore the pre-operation state. Rolling back this guard is a normal source
revert; no running application process or deployment needs interruption.

## Practitioner findings

Long-lived reports show why SHA comparison and optimistic replay are not enough:

- In [Why cherry-pick shows conflicts in lines not touched by the commit?](https://stackoverflow.com/questions/26833195/why-cherry-pick-shows-conflicts-in-lines-not-touched-by-the-commit), practitioners explain that cherry-pick performs a three-way merge and nearby evolution can create a conflict even when a textual patch looks familiar.
- In [Cherry-pick the same commit twice](https://stackoverflow.com/questions/78792860/cherry-pick-the-same-commit-twice-then-merge-current-branch-to-a-new-branch-th), users reproduce a repeated patch becoming empty or failing because its original context no longer matches.
- Git's maintained [`git cherry` documentation](https://git-scm.com/docs/git-cherry.html) defines `-` as an equivalent patch already present upstream and explains that equivalence is based on the diff rather than commit identity.

These findings support a non-mutating, Git-native identity check before replay.
They do not justify auto-dropping a request: the target returns a visible error
so the operator can confirm that the existing patch is the intended one.
