# Beta release-failure ledger

## Contract

The beta release-failure ledger is the authoritative, machine-checked map from a
discoverable failed release-candidate job to the code that prevents its recurrence.
Each discovery records an immutable GitHub Actions run, job, exact head SHA, target
release tag, conclusions, trigger, URLs, and the `make ci-view` command used to ingest
the evidence. Each incident adds a bounded failure signature, descendant fix commit,
existing pytest node, and the earliest warnings-as-errors preflight that selects it.

Run the checker before advancing a beta candidate:

```console
make check-release-failure-ledger RELEASE_FAILURE_LEDGER=docs/releases/beta-release-failures.json
```

Missing, stale, duplicate, unknown, or unmapped records fail closed. The checker
also rejects non-local commits, a fix that does not descend from the failed SHA,
mutable or mismatched GitHub URLs, nonexistent regression nodes, and preflights
that omit `-W error`. A `workflow_dispatch` run targeting `v0.1.0-beta.4` remains a
release-candidate run; the ledger does not mislabel it as a tag-triggered run.

## Proven beta4 incidents

The 2026-08-31 review admitted only histories for which the repository and GitHub
evidence prove the complete chain:

| Failure | Run / job | Failed SHA | Fix | Regression node |
| --- | --- | --- | --- | --- |
| CBC tamper acceptance | `32904013362` / `97985787888` | `c51d791b7bbc5f014eb53c77b06da5c92e46096b` | `a8969daba137e11c8a217826509e4811bad0fcbf` | `TestTamperDetection::test_padding_malleability_cannot_forge_valid_frame` |
| Salsa20 zero-keystream assertion | `32934741442` / `98074766623` | `042932a515a39614fd3508d34a88dd51d89c1cd3` | `dc094cb57d895a53e05aa1cd9175fe89dd3df0dc` | `TestStreamEncrypt::test_zero_keystream_byte_may_preserve_plaintext` |

Older prose-only task notes are not silently promoted into immutable incidents.
Where an exact job ID, head SHA, fix ancestry, or named regression node cannot be
proven, history remains explicitly unadmitted until `make ci-view RUN=...` and local
commit evidence establish the complete tuple. This boundary prevents guessed data
from making the release check green.

## Evidence reviewed

- GitHub's official [workflow-runs REST documentation](https://docs.github.com/en/rest/actions/workflow-runs)
  and [workflow-jobs REST documentation](https://docs.github.com/en/rest/actions/workflow-jobs),
  reviewed 2026-08-31, distinguish run metadata from job identity and expose the
  immutable run/job IDs, head SHA, event, and conclusions used here.
- The long-lived GitHub Community discussion
  ["Get PR head commit ID from Actions"](https://github.com/orgs/community/discussions/25191),
  opened 2020-03-19 and reviewed 2026-08-31, documents practitioner confusion between
  the tested head commit and synthesized merge commits. The ledger therefore records
  and locally verifies the exact `head_sha` instead of inferring it from a branch.

## Zero-downtime operation

The checker is read-only and runs before publication. It never creates, moves, or
deletes a tag or release; it does not stop a running application or mutate an active
deployment. A candidate with incomplete evidence remains blocked while the currently
available Gludd version continues serving users.

## Resource bounds

The ledger is capped at 2 MB. Validation is single-process apart from bounded local
Git probes with ten-second timeouts, performs no network calls, and walks only the
listed pytest files. Each discovery requires an observable `make ci-view` ingestion
command, so network history collection stays explicit and separately auditable.

## Rollback

Revert the checker, ledger, target contract, and this document as one commit if the
format itself must be rolled back. Do not delete or weaken an incident to make a
candidate pass. Keep the candidate blocked, restore the last known checker version,
and re-enter new evidence through its documented `make` target before retrying.
