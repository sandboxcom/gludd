# Artifact verification role

`general_ludd.git_release.artifact_verify` verifies one release artifact and
its dependency lock on the managed host. It is a local, read-only operation:
there is no daemon request, subprocess, listener, credential, or signing-key
input.

## Required variables

| Variable | Meaning |
|---|---|
| `git_release_artifact_verify_enabled` | Must be explicitly `true`. |
| `git_release_artifact_verify_root` | Absolute, normalized root without symbolic-link components. |
| `git_release_artifact_verify_path` | Artifact path relative to the root. |
| `git_release_artifact_verify_sha256` | Exact 64-character lowercase SHA-256. |
| `git_release_artifact_verify_lock_path` | Dependency-lock path relative to the root. |
| `git_release_artifact_verify_lock_sha256` | Exact 64-character lowercase SHA-256. |

The role publishes `git_release_artifact_verify_result` and the fact named by
`git_release_artifact_verify_result_fact`. Check mode performs the identical
verification and reports `changed: false`.

## Failure and rollback

Any unsafe path, non-regular file, input mutation, size breach, or digest
mismatch fails closed before deployment. The role never changes the candidate
or active release. A rollout therefore keeps serving the prior verified
artifact; operators correct the candidate evidence and rerun the same check.
