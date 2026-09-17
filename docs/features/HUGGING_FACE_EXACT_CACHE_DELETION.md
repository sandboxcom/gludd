# Exact Hugging Face Cache Deletion

## Purpose

Self-improvement can reacquire a model at an immutable Hub revision, but it must
never trade disk pressure for an ambiguous deletion. The Hugging Face API deletes
by revision hash, while Gludd's ownership evidence identifies a repository,
revision, filename, and local path. This adapter closes that difference before
the lifecycle manager uses the upstream deletion strategy.

The adapter is intentionally independent of model planning and lifecycle state.
A caller maps its owned artifact into:

```python
CacheArtifactIdentity(
    repo_id="owner/model",
    revision="<40 lowercase hex characters>",
    filename="nested/model.gguf",
    path=Path("/absolute/cache/snapshot/path/nested/model.gguf"),
)
```

`HuggingFaceCacheDeletion(cache_root).plan(identity)` returns an immutable
`CacheDeletionPlan`. Its `dry_run()` method has no side effects.
`execute_and_verify()` rescans before execution and again afterward.

## Safety contract

Planning fails closed unless all of these statements are true:

1. The cache root exists and is a directory.
2. The repository identifier, immutable revision, relative filename, and absolute
   artifact path are syntactically valid.
3. Both the lexical artifact path and its resolved blob stay under the selected
   cache root.
4. The scan has no corruption warnings. Hugging Face omits corrupted repositories
   from its valid inventory, so continuing after a warning would turn incomplete
   evidence into authority to delete.
5. Exactly one scanned model repository owns the revision. A duplicate revision
   in another repository is rejected even though upstream normally treats commit
   hashes as globally unique.
6. The exact repository, revision, full relative filename, snapshot path, and
   resolved blob all agree. Upstream reports `CachedFileInfo.file_name` as a
   basename, so the adapter combines that check with the full snapshot path.
7. Every path in the immutable upstream `DeleteCacheStrategy` stays inside the
   exact repository. It must delete either that repository alone or that exact
   snapshot with only its `refs/` and `blobs/` descendants.
8. The strategy exposes a finite, nonnegative whole-byte estimate and a callable
   executor.

Execution repeats planning immediately before invoking the upstream strategy.
Afterward, the adapter rescans without using the network and requires both the
artifact path and exact revision to be absent. Scan, planning, execution, and
verification failures use bounded error categories; external exception text and
paths are not chained into the public failure.

The adapter never calls `unlink`, `rmtree`, or a shell deletion command.
Hugging Face owns the physical deletion order through its immutable strategy.

## Ownership and lifecycle

The self-improvement lifecycle owns candidate selection, plan reservation, active
leases, eviction ordering, and the decision to reclaim. This adapter owns only the
following short transaction:

```text
owned, unleased artifact
        |
        v
exact scan -> immutable preview -> exact rescan -> upstream execute -> absence proof
```

A lifecycle caller must keep its reservation or cache lock across the transaction.
The adapter does not create processes, threads, network requests, daemons, or
persistent locks. It performs at most three complete cache scans: one to create
the preview, one immediately before execution, and one after execution. The scan
cost is bounded by the chosen cache root's current inventory.

The reported `expected_freed_bytes` is preview evidence, not quota truth.
Filesystem free space and Gludd's owned manifest remain authoritative because an
upstream scan can omit data.

## Zero-downtime deployment

This is an additive boundary. Existing injected deletion callbacks remain usable
for tests while the production default maps owned lifecycle evidence into
`CacheArtifactIdentity`. Rollout follows these stages:

1. Deploy the adapter and its hermetic tests without changing active eviction.
2. Wire only the production default deletion path to the adapter.
3. Keep existing active leases and candidate-plan reservations authoritative.
4. Exercise validate-only cache cleanup and a disposable real Hub cache layout.
5. Enable automatic eviction only after focused, lifecycle, and full gates agree.

A rollout failure leaves acquisition and already loaded models available. The
operator can restore the previous default deletion adapter without stopping the
daemon. A completed cache deletion is not restored locally; the immutable
repository revision makes it reacquirable. The lifecycle must never select a
leased or reserved artifact for deletion, so rollback never depends on terminating
a serving model.

## Test evidence

The focused suite covers:

- dry-run behavior and a fresh pre-execution rescan;
- an actual temporary Hugging Face `blobs/`, `snapshots/`, and `refs/` layout;
- exact nested filename and path binding;
- cross-repository revision collisions and strategy path escapes;
- malformed scan objects, warnings, missing and noncanonical paths;
- whole-repository and partial-revision strategies;
- invalid size estimates, missing executors, execution failures, and failed
  post-delete verification;
- bounded public exceptions with suppressed external causes.

All filesystem effects are contained under pytest temporary directories. The real
upstream integration test is offline and deletes only its disposable cache.

## Upstream and practitioner evidence

The [Hugging Face cache-system reference](https://huggingface.co/docs/huggingface_hub/en/package_reference/cache)
documents that scans capture corruption as warnings, that revision deletion first
returns an immutable dry-run strategy, and that callers must execute that strategy.
The [cache guide](https://huggingface.co/docs/huggingface_hub/guides/manage-cache)
documents snapshot, ref, and shared-blob deletion behavior. The
[upstream cache manager source](https://github.com/huggingface/huggingface_hub/blob/main/src/huggingface_hub/utils/_cache_manager.py)
is the primary evidence for basename-only `CachedFileInfo.file_name`, captured
scan warnings, strategy construction, and ordered execution.

Long-lived reports explain why Gludd adds policy around that mature API instead of
reimplementing it:

- [Hub issue 1738](https://github.com/huggingface/huggingface_hub/issues/1738)
  tracked repeated downloads and evolving `local_dir` metadata behavior from
  October 2023 onward. Gludd therefore uses an explicit Hub `cache_dir` and
  immutable revision identity instead of inferring ownership from a copied local
  directory.
- [The July 2023 forum discussion about removing old snapshot revisions](https://discuss.huggingface.co/t/how-to-sync-to-the-latest-version-with-snapshot-download-old-files-removed/47406)
  records both user demand for automatic space recovery and the maintainer's
  reason for keeping deletion separate from download: which revision is obsolete
  is application policy. Gludd keeps that choice in its lease and reservation
  lifecycle, then delegates physical deletion to `delete_revisions`.
- [The January 2023 report of the Hub cache filling a system disk](https://discuss.huggingface.co/t/huggingface-hub-filled-my-c-disk-where-are-the-files/30354)
  shows that operators need bounded automatic ownership rather than an unexplained
  ambient cache. Gludd namespaces the cache and exposes a dry run before reclaim.
- [Hub issue 4420](https://github.com/huggingface/huggingface_hub/issues/4420)
  demonstrates that a cache listing and its total can omit a repository and
  incomplete downloads. Gludd treats a warning, omission, or not-found target as
  refusal, and never uses the upstream total as proof of physical disk usage.

These sources support exact orchestration around Hugging Face's implementation;
they do not justify raw path deletion or warning suppression.
