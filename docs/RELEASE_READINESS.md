# Beta.3 Release Readiness

`scripts/release_readiness.py` is the fail-closed preflight for the
`development` branch before cutting beta.3. It prints one JSON diagnostics
object and returns a stable exit code:

| Code | Blocking evidence |
| ---: | --- |
| 0 | All checks pass. |
| 2 | No successful CI run is attached to the exact local HEAD. |
| 3 | The worktree contains staged, modified, or untracked paths. |
| 4 | A detached or unintegrated sibling worktree/branch exists. |
| 5 | The canonical version-consistency helper reports a mismatch. |
| 6 | The task ledger is invalid or an unchecked `T-BETA3-*` task remains. |
| 7 | Required evidence could not be collected. |

Run it from the repository root:

```sh
uv run python scripts/release_readiness.py --gha-head-sha "$GHA_HEAD_SHA"
```

`--gha-head-sha` is optional for local use, but supplying the SHA observed by
GHA makes a mismatch explicit. CI status is queried through
`scripts/require_ci_green.py`; worktree topology is collected through
`scripts/workflow_state_guard.py`; version and ledger checks call their
existing canonical helpers. The preflight never mutates the repository.

The release-critical task set intentionally follows the `T-BETA3-*` namespace.
This keeps a newly registered beta.3 release blocker from being silently
omitted from the preflight.

Long-lived issue note: GitHub users commonly report that a release job can be
green for an earlier commit while a local checkout has advanced. Exact-SHA CI
matching and a clean-worktree requirement address that failure mode rather
than trusting a branch-level green badge.
