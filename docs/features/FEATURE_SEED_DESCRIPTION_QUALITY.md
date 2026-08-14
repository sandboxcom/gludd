# Feature Seed Description Quality

## Status

Implemented for the `0.1.0-beta.4` release train. Canonical feature seed
descriptions remain human-readable, deterministic metadata and must identify at
least one word from their stable feature key.

## Problem

`FEATURE_SEED` is the canonical bootstrap catalog consumed by the feature
repository, verifier, dogfood command, and feature API. Its `ci_green` entry had
a substantial description and valid evidence, but the description did not
contain either stable key word: lowercase `ci` or `green`. The description
quality audit therefore failed correctly. Weakening that audit would allow seed
records to become difficult to relate to their keys in logs, reviews, exports,
and catalog search.

The repair changes only the canonical description. It calls the record the
`ci_green` feature and identifies the local gate status as its artifact. The
name, category, acceptance criteria, evidence, verifier kind, status, and
request provenance remain byte-for-byte compatible.

## Practitioner evidence

Backstage practitioners describe maintaining
[catalog metadata-quality pipelines in issue 26093](https://github.com/backstage/backstage/issues/26093),
including validation of required metadata and surfacing discrepancies to entity
owners. Gludd applies the same principle at its smaller seed boundary: reject a
bad canonical record in a deterministic unit audit instead of accepting it and
repairing database rows later.

The multi-year Backstage
[API discoverability report 22802](https://github.com/backstage/backstage/issues/22802)
also records the operational consequence of weak catalog metadata: teams cannot
find existing functionality and may duplicate it. A self-identifying feature
description gives both operators and future search/index consumers a stable
textual bridge back to the canonical key.

## Contract

- Every seed description is a non-blank string.
- Every description contains at least one case-sensitive token derived from its
  underscore-separated stable `name`.
- Implemented-feature descriptions remain longer than 40 characters.
- The `ci_green` description names `ci_green`, the local gate artifact and its
  lint, typecheck, collection, test, and smoke outcomes, while honestly stating
  that GitHub Actions verification is not yet connected.
- The quality audit remains generic and unchanged. New seed records must satisfy
  it through their canonical metadata; tests must never be weakened to admit a
  failing record.
- Import order, list order, record count, evidence grammar, and serialized field
  shape remain unchanged.

## Security and resource boundaries

Descriptions are trusted, static repository metadata that may be returned by
the feature API. They must contain no credentials, user-controlled markup,
terminal escapes, remote content, or dynamically evaluated values. The repair
adds no new render path and grants no capability; it only makes an existing
record easier to attribute during review and observability.

Loading `FEATURE_SEED` remains an in-memory list construction. This change opens
no database, file, socket, subprocess, thread, or network connection and creates
no checkout artifact. It does not invoke the repository seeder, mutate an
existing feature row, or add migration work. The description-size increase is
bounded and operationally negligible.

## Observability

The existing audit reports the exact stable key whose description is deficient.
After seeding, normal feature API and dogfood output expose the corrected
description using their existing logging and response paths. No new metric or
log stream is warranted for a static metadata-only repair.

## Zero-downtime rollout and rollback

No schema or data migration is required. New processes and fresh databases read
the corrected seed metadata; already-running processes and previously seeded
rows continue using their current description until their existing explicit
reseed/update workflow runs. Mixed-version workers remain compatible because
the record shape and stable key do not change.

Rollout follows normal development promotion after the exact regression,
focused seed suite, coverage, and static gates pass. Rollback is a commit revert
of the source description, this contract, and its task evidence. It requires no
daemon restart orchestration or database rollback; existing rows are not
automatically rewritten in either direction.

## Verification

The authoritative regression is
`TestFeatureSeedDescriptionQuality::test_descriptions_mention_key_artifact`.
It must fail on the old `ci_green` metadata and pass without any audit change.
The complete seed suite verifies record structure, stable ordering assumptions,
evidence paths, acceptance criteria, categories, and status/evidence
consistency. Focused production coverage must be at least 85 percent aggregate,
with at least 75 percent line and branch coverage for every touched source file;
warnings, Ruff, strict mypy, source docstrings, Markdown, feature-spec, and task
ledger checks are fail-closed.
