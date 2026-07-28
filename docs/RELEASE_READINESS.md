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

## Long-lived GitHub Actions artifact failures

Research was rechecked on 2026-07-28. Dates in the evidence column are the
opening dates of the user report or discussion; GitHub documentation links
were accessed on the research date.

| Failure mode | External evidence | v0.1.0-beta.3 action |
| --- | --- | --- |
| An older pending run disappears even when `cancel-in-progress` is false. | GitHub's current [concurrency documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency) says the default `queue: single` replaces an existing pending run, while `queue: max` retains up to 100. [Community discussion #5435](https://github.com/orgs/community/discussions/5435), opened 2021-09-02, records release/deployment users hitting the default through 2026-02-24. | Keep push and tag runs SHA-scoped so a later development push cannot evict the beta.3 SHA. If branch-scoped serialization is restored, add `queue: max`; `cancel-in-progress: false` alone is not a queue. A cancelled or missing run remains “no verdict,” never green evidence. |
| Matrix jobs reuse an artifact name, or merged artifacts contain the same filename. | The official [artifact v4 migration guide](https://github.com/actions/upload-artifact/blob/main/docs/MIGRATION.md) makes artifacts immutable and warns that `merge-multiple` uses last-writer-wins for duplicate filenames. [upload-artifact issue #478](https://github.com/actions/upload-artifact/issues/478), opened 2023-12-18, shows the resulting `409 Conflict` when several jobs reuse one name. | Preserve the per-platform `gludd-*` artifact names and versioned payload filenames. The release fan-in may merge them only after every name is unique, then must run the 12-category pre-publish gate. Do not restore the old shared matrix artifact pattern. |
| An upload step is green but produced no artifact, or artifact storage rejects the upload. | The official [upload-artifact usage](https://github.com/actions/upload-artifact) says a path with no matches succeeds with a warning by default. [Issue #307](https://github.com/actions/upload-artifact/issues/307), opened 2022-03-13, contains recurring `403` storage-quota reports and user reports that deleted storage was not reclaimed immediately. GitHub documents both [custom retention and artifact deletion](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/remove-workflow-artifacts). | Never treat an upload step's green status as release evidence. The beta.3 fan-in must fail on any missing category; release-producing uploads should use `if-no-files-found: error` when edited. Diagnose `403` as quota/storage state, keep transient workflow artifacts on short retention, and do not tag around the failure. |
| The artifact is listed with the expected name but cannot be retrieved. | [upload-artifact issue #417](https://github.com/actions/upload-artifact/issues/417), opened 2023-07-21, reports a PyInstaller matrix whose correctly named artifacts stalled for more than 30 minutes in both browser and CLI downloads. | A UI listing is not completion evidence. Retain the post-publish `gh release download` smoke tests, execute the Linux binary, inspect the `.deb`, and require the version to match the tag. |
| Separate platform jobs publish or repair the same release independently. | [action-gh-release issue #323](https://github.com/softprops/action-gh-release/issues/323), opened 2023-02-09, demonstrates separate invocations creating confusing duplicate draft and non-draft releases. The action's current [release-asset documentation](https://github.com/softprops/action-gh-release#-uploading-release-assets) says an existing release for the tag is updated with assets. | Keep one fan-in publisher after all platform jobs; matrix jobs upload workflow artifacts only. Repair a partial release only with CI-built files from the tagged SHA, then rerun the same completeness and download checks. |
