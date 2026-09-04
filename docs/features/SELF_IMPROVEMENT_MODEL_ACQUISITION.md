# Self-Improvement Model Acquisition

Status: implemented on the self-improvement feature branch.

## Decision

Managed self-improvement keeps public Hugging Face models usable without an
operator login. A network operation selects exactly one explicit mode:

- `anonymous_public`: neither `HF_TOKEN` nor the compatibility
  `HUGGING_FACE_HUB_TOKEN` variable contains a token. Gludd passes
  `token=False` to the locked Hub client so a token saved elsewhere on the
  machine is not attached implicitly.
- `explicit_env_token`: one of those environment variables contains a token.
  Gludd forwards the exact value to the Hub API without placing it in an event,
  process argument, ownership manifest, diagnostic, or log message.
  `HF_TOKEN` has precedence.

The default policy permits both modes. Operators that require every network
request to be authenticated set
`GLUDD_SELF_IMPROVE_HF_TOKEN_REQUIRED=true`. Accepted true values are
`1`, `true`, `yes`, and `on`; false values are `0`, `false`, `no`,
`off`, and the empty value. Any other value fails manager construction.

Strict mode is an admission policy, not a download workaround. If no explicit
environment token is present, Gludd emits the typed anonymous selection and a
typed validation failure, then stops before revision metadata or model bytes
can contact the Hub.

## Observable lifecycle

Each cache-miss Hub operation has one correlation identity and this order:

1. `revision_resolution_started` or `download_started`;
2. exactly one `anonymous_public` or `explicit_env_token` event whose
   `auth_mode` has the same typed value;
3. zero or more bounded progress heartbeats;
4. one completed or failed event.

A full cold acquisition normally performs two Hub operations, immutable
revision resolution and artifact transfer. It therefore emits one auth-mode
event for each operation. This is deliberate: the event proves the choice
immediately before the specific external call rather than implying that one
earlier choice governed later I/O.

Events contain only hashed repository/model correlation keys, immutable
revision when known, elapsed time, the enum mode, and a bounded failure class.
They never contain the credential or raw repository identifier.

An owned cache hit follows a narrower path. Gludd revalidates its manifest,
contained path, byte size, and digest, emits `cache_hit`, and creates the
lease. It does not select auth, call a token provider, resolve a Hub revision,
construct a downloader, or start an acquisition worker. Strict mode therefore
does not make an already admitted artifact unusable while the network is
unavailable or the token is intentionally absent.

## Credential and process boundary

Gludd does not serialize a credential into the arguments sent to its bounded
spawn worker. The parent observes only whether an explicit environment value
exists. The child inherits the process environment and reads the credential
at the last responsible moment:

- revision resolution constructs `HfApi(token=<environment value>)`, or
  `HfApi(token=False)` when absent;
- GGUF transfer constructs the existing `ModelDownloader`, which forwards the
  same explicit environment value or `False` to `hf_hub_download`.

The worker still has the existing finite deadline, process name, termination,
join, and partial-transfer cleanup. Missing strict auth fails before worker
creation. No authentication daemon, login subprocess, token cache, or
additional cleanup task is introduced.

## Upstream warnings are evidence

Anonymous public access and silent access are different contracts. The locked
`huggingface_hub==1.17.0` can complete a public request while the Hub returns
an `X-HF-Warning` explaining unauthenticated rate limits. Gludd does not:

- set `HF_HUB_VERBOSITY` to hide that message;
- install a warning or logging filter around Hub calls;
- redirect the Hub HTTP client;
- rewrite the server warning into a success claim; or
- retry with an ambient saved credential.

The upstream message remains visible so an operator can decide whether higher
authenticated rate limits are worth configuring. It is informational for a
successful public download, not evidence that Gludd requires a token.

## Zero-downtime deployment and rollback

The policy is read when a new `ModelLeaseManager` is constructed. Existing
workers and leases finish under the mode they already selected. New cache hits
continue without auth or network I/O. This avoids stopping the Gludd daemon or
invalidating a model merely to change authentication policy.

To roll back strict admission without changing cached bytes, unset
`GLUDD_SELF_IMPROVE_HF_TOKEN_REQUIRED` or set it to `false`, then let the
next manager use the default policy. To roll back authenticated acquisition,
remove the token from the daemon's environment before its next managed
operation. Neither rollback deletes manifests, revisions, or leases.

Code rollback is also data-compatible: the acquisition event adds an optional
field and does not change the ownership-manifest schema. Older consumers can
ignore the new event field, and existing cache entries remain valid.

## Resource and failure guarantees

- Cache reuse performs no Hub/auth operation and starts no child process.
- A strict missing-token refusal starts no child process and transfers zero
  bytes.
- A cold permitted operation retains the existing 600-second default deadline,
  observable heartbeats, deterministic process namespace, and always-joined
  cleanup.
- Authentication events cannot leak repository names or token values.
- A failing event sink is disabled without changing acquisition ownership.
- Quota, reserve, lease, digest, and partial-transfer rules are unchanged.
- The dedicated cache root is expanded and resolved before it reaches
  `huggingface_hub`; Gludd never passes a literal tilde path to the client.

## Verification

The focused tests cover:

- `token=False` for public revision and GGUF paths;
- exact forwarding from each supported environment variable;
- typed and secret-free mode events before injected Hub operations;
- strict refusal before both revision resolution and download;
- strict-policy parsing and invalid-value refusal;
- cold-worker argument inspection proving that no token is serialized;
- owned cache reuse with zero revision, downloader, and auth-mode activity;
- upstream warning visibility; and
- the pre-existing deadline, heartbeat, lease, quota, integrity, and cleanup
  matrix under `-W error`.

Coverage is measured with branch coverage. The project gate remains responsible
for the repository-wide 85 percent aggregate and 75 percent per-file minimums.

## Evidence and practitioner reports

Official evidence:

- The [Hugging Face authentication quickstart](https://huggingface.co/docs/huggingface_hub/en/quick-start#authentication)
  documents implicit saved-token use, `HF_TOKEN` precedence, and explicit
  method parameters.
- The [official header builder source](https://github.com/huggingface/huggingface_hub/blob/main/src/huggingface_hub/utils/_headers.py)
  defines a string token as explicit auth and `token=False` as the supported
  way to omit the authorization header.
- The [official HTTP warning source](https://github.com/huggingface/huggingface_hub/blob/main/src/huggingface_hub/utils/_http.py)
  turns server `X-HF-Warning` headers into visible logger warnings.
- The project lock records `huggingface_hub==1.17.0`, so this contract is
  tested against the actual dependency boundary rather than an assumed older
  client.

Practitioner evidence:

- [huggingface_hub issue 4292](https://github.com/huggingface/huggingface_hub/issues/4292)
  reproduces the locked 1.17 client on macOS. Its trace records
  `authenticated: False`, the server's unauthenticated-request warning, and a
  failure caused by a literal tilde in an explicit cache path. Gludd preserves
  the warning and resolves its owned cache path before calling the client.
- The long-running
  [Hugging Face forum report about newly visible unauthenticated warnings](https://discuss.huggingface.co/t/embeddings-position-ids-unexpected-warning-started-showing/173102)
  shows public model downloads remaining functional while users see the
  rate-limit warning, including duplicate-looking output through layered
  logging. That is why Gludd emits its own single typed mode event but leaves
  the upstream message untouched rather than trying to silence vendor output.

## Candidate-plan reservation and cache churn

One self-improvement run resolves the immutable revision for every bounded
candidate before it acquires the first candidate. The complete plan is then
reserved atomically. This closes the lifecycle gap observed in live runs:
StarCoder2 and Qwen 3B artifacts were evicted and subsequently downloaded
again, adding 148.60 and 142.09 seconds respectively. A future candidate is no
longer mistaken for unused cache merely because its lease has not started yet.

The lifecycle module owns a `ModelArtifactIdentity` containing the exact model
ID, repository, filename, and immutable 40-hex revision. The runner only adapts
planner candidates into that public identity; the lifecycle does not import or
depend on planner types. One bounded JSON reservation represents the entire
candidate plan and records the owner's PID and process-birth identity. The
context manager creates it before the first candidate acquisition and removes
it on success, ordinary failure, cancellation, or interruption.

Each planned identity has one monotonic execution state:

- `planned` is protected because a later retry can still need it;
- `eligible` has finished generation and returns to ordinary LRU policy; and
- `failed` is eligible for eviction ahead of ordinary LRU artifacts.

Generation and evaluation failures move only the exact current identity to
`failed`. Historical failure evidence is converted to exact identities only
when an owned manifest proves repository, filename, and revision. A model ID
alone never authorizes deletion. When concurrent plans disagree, the union of
all live `planned` protection is applied before any failure-priority hint, so
live use always wins.

## Reservation admission and stale-owner handling

Reclamation loads and validates every ownership manifest, active lease, and
plan reservation before it can plan a deletion. Reservation JSON is bounded,
uses a schema independent from the ownership-manifest schema, and is rejected
for unknown fields, ambiguous identities, invalid states, symlinks, oversized
content, or malformed owner metadata. Corruption fails closed before the first
deletion.

A reservation is stale only after checking both PID liveness and process birth.
A missing PID or a birth mismatch proves that the original owner is gone and
allows the reservation file to be reaped. An inaccessible or otherwise
unverifiable live owner blocks reclamation; age alone is never used to infer
staleness. This prevents PID reuse and slow valid plans from losing protection.

Quota and disk-reserve admission use the same protected set as reclamation. If
pressure can be resolved only by deleting leased or planned artifacts, Gludd
emits an eviction refusal and fails before starting the model download. It
does not download first and hope that cleanup later creates enough space.

## Exact Hugging Face cache deletion

Default reclamation uses the supported Hugging Face cache inventory and
deletion-plan APIs. The adapter scans the configured cache root and requires
one exact match for repository, immutable revision, filename, and resolved
artifact path. It validates the dry-run plan is confined to that exact
repository and revision, executes the vendor strategy, and verifies the exact
artifact is absent afterward. Scanner warnings, corrupt metadata, missing
matches, path escapes, broadened plans, or incomplete deletion are bounded
failures.

Gludd does not recursively remove cache directories and does not treat a
revision string alone as deletion authority. Injected deletion collaborators
remain available for deterministic tests, while production uses the verified
inventory-plan boundary.

## Zero-downtime deployment, rollback, and resources

The reservation schema is additive and separate, so existing owned artifacts
and leases require no migration. General daemon service remains available
during deployment, but self-improvement admission must be briefly gated while
old in-flight jobs drain: an older worker does not understand reservations and
could evict a candidate protected by a new worker. Resume jobs only after every
worker runs reservation-aware code.

Rollback uses the same controlled handoff. Gate new self-improvement jobs,
allow current reservations and leases to close, verify the reservations
directory is empty, roll workers back, and then resume. Never roll an old
binary into a mixed-version cache while a reservation is live. Rollback does
not delete model bytes or ownership manifests.

Resource ownership stays bounded:

- one small reservation file exists per active plan, with at most the runner's
  bounded candidate count;
- atomic replacement prevents readers from observing partial state;
- context exit owns normal cleanup, while verified stale-owner discovery owns
  crash cleanup;
- no reservation daemon, background janitor, or raw filesystem deletion is
  introduced; and
- cache scans and deletion plans are scoped to the configured cache root and
  complete before the next download begins.

## Reservation verification

The focused regression matrix proves:

- a cached future candidate survives pressure and is reused with zero additional
  downloader calls;
- exact failed artifacts precede older LRU entries while concurrent live
  protection overrides every failure hint;
- corrupt, ambiguous, or unverifiable reservation metadata blocks deletion;
- only a missing PID or verified PID-birth mismatch permits stale reaping;
- protected-only pressure refuses before acquisition starts;
- planned, eligible, and failed transitions are atomic and monotonic;
- proposal failure, evaluation rejection, evaluation interruption, and
  cancellation release the whole-plan reservation; and
- default reclamation passes exact owned coordinates through dry-run,
  execution, and post-delete verification.

## Cache lifecycle evidence

Official evidence:

- The
  [Hugging Face cache-system reference](https://huggingface.co/docs/huggingface_hub/en/package_reference/cache)
  documents `scan_cache_dir`, cache metadata warnings, dry-run deletion
  strategies, and strategy execution.
- The
  [official cache-manager source](https://github.com/huggingface/huggingface_hub/blob/main/src/huggingface_hub/utils/_cache_manager.py)
  is the pinned behavioral boundary used to validate exact repository and
  revision deletion plans.

Long-lived practitioner evidence:

- [huggingface_hub issue 1738](https://github.com/huggingface/huggingface_hub/issues/1738)
  has tracked unchanged large files being downloaded again since 2023. It
  demonstrates that expensive re-download churn is not a transient network
  anomaly and motivates protecting every immutable future candidate.
- [huggingface_hub issue 4420](https://github.com/huggingface/huggingface_hub/issues/4420)
  documents cache scans under-reporting data when repositories are corrupted.
  Gludd therefore treats scan warnings or incomplete metadata as a deletion
  blocker rather than assuming unseen files are safe to remove.

## Reload-stable download contracts

`DownloadSource` is a public identity-bearing enum. It is defined in the small,
side-effect-free `small_models.download_types` contract module and re-exported
from `small_models.download`, so existing imports remain compatible. Reloading
the downloader implementation can replace functions and classes without
creating a second enum class whose visually equal members fail identity checks.

This extraction is additive and requires no daemon restart, stored-data
migration, cache rewrite, or caller change. Old and new workers serialize the
same string values. Rollback restores the in-module definition without touching
model artifacts, manifests, leases, or reservations; operators should first
drain any process that has performed an in-place code reload so one interpreter
does not retain both class identities.

The regression matrix runs the environment-sensitive downloader reload before
all cache-binding checks in one interpreter. It proves the public enum class and
members retain identity, exact cache hits still skip auth and network I/O, and
GGUF results still report the correct source. The isolated reload regression
also runs in a child interpreter so its state cannot contaminate neighboring
tests. The complete former gate batch passes under warnings-as-errors.

Upstream evidence explains the boundary:

- The [Python `importlib.reload` documentation](https://docs.python.org/3/library/importlib.html#importlib.reload)
  states that module-level code is re-executed and notes that references held
  outside the module are not rebound automatically. A public class defined in
  the reloaded module can therefore diverge from an earlier imported reference.
- [CPython issue 126548](https://github.com/python/cpython/issues/126548)
  records practitioner reproductions of reload hazards across Python 3.9
  through 3.14 and led to an explicit thread-safety warning. Gludd does not add
  concurrent reloads here; it instead keeps identity-bearing contracts outside
  the implementation reload boundary.
