# Content-addressed pure-unit cache lane

## Outcome

S48 pilots cross-commit test reuse for one deliberately small, pure-Python unit
slice. Pants 2.33.0 owns the dependency graph, sandbox, transitive-input digest,
and local content-addressed process cache. Gludd does not maintain a second
dependency walker and does not treat its signed terminal receipt as cache
authorization.

The allowlist initially contains only `tests/pants_cached_unit:tests`, which
covers the stdlib-only hash-set and radix-sort implementations. The lane is an
admission accelerator, not a replacement for the canonical cold hosted lane or
the exact-head release gate.

## Operator commands

Acquire the official launcher into the ignored project-local directory, using
the reviewed v0.13.2 asset digest for the current platform:

```console
make pants-launcher-bootstrap \
  PANTS_LAUNCHER_CONFIG=config/pants_launcher_assets.json \
  PANTS_LAUNCHER_DESTINATION=.pants.d/bin/pants \
  PANTS_LAUNCHER_ASSET=auto \
  PANTS_LAUNCHER_VALIDATE_ONLY=0 \
  PANTS_LAUNCHER_METADATA_ONLY=0
```

The bootstrap accepts only the official GitHub HTTPS release host, bounds the
download at 64 MiB, verifies the pinned SHA-256, writes atomically, and never
installs into a user-global directory. Metadata-only mode compares a reviewed
pin with the immutable upstream checksum sidecar without installing bytes.
Then validate the Gludd policy without starting Pants:

```console
make pants-unit-cache-lane \
  PANTS_UNIT_CACHE_MANIFEST=config/pants_unit_cache_lane.json \
  PANTS_UNIT_CACHE_BIN=.pants.d/bin/pants \
  PANTS_UNIT_CACHE_MODE=cached \
  PANTS_UNIT_CACHE_HOSTED_CI=0 \
  PANTS_UNIT_CACHE_VALIDATE_ONLY=1 \
  PANTS_UNIT_CACHE_POLICY_ENVIRONMENT=v1
```

Set `PANTS_UNIT_CACHE_VALIDATE_ONLY=0` for the local cached lane. Force a cold
test process by setting `PANTS_UNIT_CACHE_MODE=fresh`. The nightly comparison
hook runs the cached result and then Pants' `--test-force` path, normalizes their
JUnit node/outcome sets and branch-coverage reports, and fails on any mismatch:

```console
make pants-unit-cache-nightly \
  PANTS_UNIT_CACHE_MANIFEST=config/pants_unit_cache_lane.json \
  PANTS_UNIT_CACHE_BIN=.pants.d/bin/pants \
  PANTS_UNIT_CACHE_HOSTED_CI=0 \
  PANTS_UNIT_CACHE_VALIDATE_ONLY=0 \
  PANTS_UNIT_CACHE_POLICY_ENVIRONMENT=v1
```

The three supported launcher artifacts and their digests are reviewed in
`config/pants_launcher_assets.json`; no `latest` URL is accepted. The launcher
bootstrap is independent of the reusable test result and cannot authorize a
cache hit.

## Safety boundary

Pants computes the reusable process key from the hermetic sandbox and the
transitive target graph. The BUILD targets explicitly declare:

- the two production source files;
- the lane test and its local `conftest.py` fixture;
- the pinned Pants/toolchain policy and branch-coverage configuration;
- the only test-visible environment variable,
  `GLUDD_PANTS_UNIT_POLICY`;
- CPython 3.11 and Pants 2.33.0; and
- no dynamic inputs, external resources, or remote cache.

The controller first asks Pants for `dependencies --transitive`. Strict unowned
dependency behavior and no ambiguity resolution make incomplete graphs fail
closed. An ambiguous, unowned, errored, dynamically declared, or externally
declared target is never admitted for reuse; it runs through `--test-force`.
The test still fails if Pants cannot construct its sandbox, which is preferable
to silently blessing an incomplete graph.

Mutation acceptance covers source, test, `conftest.py`, lock, Pants config,
interpreter, plugin, platform, relevant environment, and coverage inputs. The
test models the opaque process input supplied to Pants; production code never
discovers or hashes transitive Python dependencies itself.

The 2026-10-08 acceptance exercised the real Pants binary and persistent local
CAS. The first run passed six warning-fatal tests with 95.87% aggregate branch
coverage and every measured source file above 75%; the second run reported a
local test-result cache hit. The nightly hook then compared that cached result
with `--test-force` and reported `equivalent=true`, and hosted mode proved a
forced-fresh, no-local-cache run with one worker.

Local execution is capped at two Pants processes. `PANTS_UNIT_CACHE_HOSTED_CI=1`
forces `--no-local-cache`, `--test-force`, and one worker. Remote cache reads and
writes are disabled in `pants.toml`. Pants' three local-store classes are each
bounded at 1 GiB, allowing its own garbage collection to control disk growth.

## Exact provenance and observability

Every phase emits `PANTS-CACHE-*` markers for the graph probe, cache decision,
owned command, elapsed time, normalized comparison, and signed receipt path.
The receipt has two deliberately separate sections:

1. `execution_identity` describes Pants as the cache authority and records the
   observed control-plane identity. It cannot contain the candidate SHA.
2. `candidate_provenance` records the exact Git SHA, repository-state digest,
   cleanliness, and expected-SHA match after execution.

The established Gludd HMAC receipt primitive signs a digest of the execution
identity and the complete terminal content. A receipt is release-eligible only
when the candidate is clean and exact, the lane passes, coverage remains at
least 85% aggregate and 75% per file, and a requested nightly comparison is
equivalent. The receipt proves what happened; only Pants may authorize a cache
hit.

## ZDD rollout and rollback

This lane is additive and has no serving process, database, or deployment
cutover. Rollout is therefore ZDD by construction:

1. keep normal hosted CI and the exact-head release gate unchanged;
2. run the local lane in cached mode on development trains;
3. run the forced-fresh comparison hook nightly on a persistent, namespaced
   runner; and
4. widen the allowlist only after repeated exact equivalence and bounded resource
   evidence.

Rollback is immediate: use `PANTS_UNIT_CACHE_MODE=fresh`, set hosted mode, or
remove a target from the manifest. Deleting or garbage-collecting Pants' local
store changes only performance; the next run executes cold. Release cutting
still requires the ordinary exact-head gate, so a cache problem is detected
before release rather than deferred until tagging.

## Upstream and practitioner evidence

The design follows the [official Pants test documentation](https://www.pantsbuild.org/stable/docs/python/goals/test),
which states that tests execute hermetically, that transitive changes invalidate
only dependent tests, that environment values must be explicitly admitted, and
that `--test-force` bypasses the final cached test result. The
[official dependency troubleshooting guide](https://www.pantsbuild.org/stable/docs/using-pants/troubleshooting-common-issues)
recommends inspecting `dependencies --transitive` and explains why undeclared
inputs break hermetic sandboxes. The
[official lockfile guide](https://www.pantsbuild.org/stable/docs/python/overview/lockfiles)
documents deterministic, hash-verified tool and dependency inputs.
The [official launcher installation guide](https://www.pantsbuild.org/stable/docs/getting-started/installing-pants)
documents checksum verification, and the
[immutable scie-pants v0.13.2 release](https://github.com/pantsbuild/scie-pants/releases/tag/v0.13.2)
provides the exact three platform assets pinned by this pilot.

Long-lived practitioner reports informed the fail-closed edges:

- Pants discussion [#17762](https://github.com/pantsbuild/pants/discussions/17762)
  documents the broad invalidation caused by ancestor `conftest.py` files and
  the tradeoff between precise fixture layout and safe over-invalidation. This
  pilot disables implicit ancestor discovery and explicitly owns one local
  fixture target.
- Pants issue [#10379](https://github.com/pantsbuild/pants/issues/10379)
  records the long-standing need to force Pytest past cached results. The modern
  supported answer is `--test-force`, which the fresh and nightly paths use.
- The Pants community cache thread on
  [GitHub cache eviction](https://chat.pantsbuild.org/t/27553563/we-re-running-into-issues-with-premature-cache-eviction-on-g)
  reports coarse-cache size and eviction problems. The pilot therefore uses no
  remote cache, namespaces the local CAS, bounds its store classes, and relies
  on Pants rather than archiving the whole LMDB store.

## Expansion criteria

Do not add a target merely because it is fast or currently passing. An expansion
must have explicit source and fixture ownership, no live network/service/time
input, no unresolved or dynamic import, deterministic branch coverage, a
warning-fatal focused test, and a nightly cached-versus-fresh equivalence record.
The exact-head gate remains the final release-ready evidence after expansion.
