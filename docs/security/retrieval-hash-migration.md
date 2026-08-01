# Retrieval dense-vector hash migration

Status: implemented in the `blake2b-256-v2` mapping.

## Finding and decision

Bandit B324 identified the deterministic dense-vector stub in
`general_ludd.ai_ml.retrieval` as a high-severity use of MD5. Although the
digest was used only to choose a vector bucket, retaining MD5 had two concrete
costs: attacker-selected tokens inherited a digest with known collision
weaknesses, and some FIPS-enabled Python builds can block MD5 entirely.

The mapping now uses standard-library BLAKE2b with a 32-byte digest and the
fixed `gludd-retrieval` personalization domain. Every `RetrievalResult` records
`dense_vector_version="blake2b-256-v2"`, so stored results and evaluation
artifacts can distinguish the new mapping from the legacy implicit MD5 map.
The algorithm is intentionally not runtime-selectable: allowing an unversioned
MD5 downgrade would recreate the finding. A future configurable algorithm must
use a collision-resistant `hashlib` constructor, receive a new mapping version,
and add deterministic-vector and ranking-regression tests before rollout.

This is not a claim that a 64-dimensional hashing vector is collision-free.
Modulo reduction deliberately maps many tokens to each bucket. BLAKE2b removes
the avoidable weak-digest dependency; authorization, signatures, passwords,
and integrity decisions must never depend on this retrieval stub.

## Migration and zero-downtime rollout

The current service stores only source text in memory and recomputes vectors on
each search, so there is no persisted vector index to rewrite. The source API
remains compatible, including manually constructed `RetrievalResult` objects,
which receive the new version by default.

Hash buckets can change and may alter dense ties or hybrid ranking. Operators
must invalidate cached retrieval results and regenerate ranking goldens that do
not carry `dense_vector_version`. Re-indexing source text is unnecessary.
For zero-downtime deployment, canary the new release, compare recall@k, MRR,
and nDCG against the prior release on the same corpus, then shift traffic only
after quality thresholds pass. During a mixed-version rollout, never combine
or compare vectors unless their version fields match. Rollback restores the
prior process and its cached results as one unit; do not feed legacy cached
vectors to the new process.

## Primary and operator evidence

- Python's official [`hashlib` documentation](https://docs.python.org/3/library/hashlib.html)
  warns that MD5 has known collision weaknesses, lists BLAKE2b among the
  constructors guaranteed across platforms, and documents BLAKE2
  personalization and digest sizing.
- The long-running CPython operator discussion
  [FIPS support for hashlib](https://github.com/python/cpython/issues/53462)
  began in 2010, accumulated 47 migrated messages and multiple implementation
  pull requests, and ultimately drove the `usedforsecurity` compatibility
  mechanism. The operational lesson is that even nominally non-security MD5
  calls create deployment failures under restricted crypto policy. Gludd avoids
  that compatibility escape hatch here because a secure deterministic digest is
  available without adding a dependency.

## Verification

`tests/unit/test_retrieval_secure_hash.py` pins the personalized BLAKE2b bucket,
rejects accidental MD5 calls, checks empty, Unicode, and long inputs, verifies
normalization and determinism, and requires the mapping version in results.
The existing retrieval suite remains the ranking-contract regression gate.
`make sast` is the release gate for recurrence of Bandit B324.
