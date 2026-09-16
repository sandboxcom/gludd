# Project-private self-improvement policy

Status: implemented with hermetic end-to-end proof.

## Contract

Each project may own a strict `.gludd/self-improve-policy.json` document. A
missing document retains the historical public behavior. A present document is
parsed fail closed, private rules win over public rules, and a
`default_access: private` policy acts as an explicit public allowlist.

The approved policy digest is bound to the plan and reloaded before repository
observation, provider generation, proposal evaluation, and durable learning.
Present-but-malformed policy data, policy drift, unsafe path resolution, or any
private member in a mixed scope blocks the whole operation. Events contain only
reason codes, counts, policy digests, and one-way path hashes.

Protected-project learning is scoped by project identity, repository identity,
baseline, and policy digest. This keeps calibration failures, training outcomes,
and persistent memory from crossing between projects even when they use the
same repository-relative path.

## Hermetic end-to-end proof

[`tests/e2e/test_self_improve_private_policy_e2e.py`](../../tests/e2e/test_self_improve_private_policy_e2e.py)
drives the production plan-preparation and repository-bound runner composition
with only provider, evaluator, model, and storage edges replaced by in-process
fakes. The suite proves:

- missing-policy compatibility and public-sibling completion;
- default-private explicit allowlisting;
- exact, glob, and mixed public/private denial before patch materialization;
- malformed-policy and post-approval policy-drift failure;
- opposite policies in two projects sharing the same relative path; and
- canary-byte absence from provider prompts, policy events, process logs,
  results, calibration inputs, training records, and persistent memory.

Blocked cases assert zero provider calls. Allowed cases must complete an
accepted public attempt and record only the scoped public outcome.

## User reports that shape the threat model

These reports are product-specific rather than evidence about Gludd, but they
show why privacy cannot depend on an assistant honoring an ignore hint:

- A [GitHub Community allowlist request](https://github.com/orgs/community/discussions/120113)
  opened in April 2024 remained active through 2025. Users described exclusion
  patterns as insufficient for organizations that need a small explicit set of
  public repositories. This motivates the default-private allowlist mode.
- A [VS Code Copilot mounted-folder issue](https://github.com/microsoft/vscode-copilot-release/issues/1486)
  reported in August 2024 that excluded mounted content could still be attached
  to Chat. This motivates canonical path checks and rejecting aliases before
  any content read.
- A [Copilot agent-mode exclusion request](https://github.com/microsoft/vscode-copilot-release/issues/12711)
  reported in June 2025 that agent tools could search files excluded from other
  modes. This motivates enforcement at every local effect boundary, before the
  provider is invoked.
- A [cross-repository exclusion discussion](https://github.com/orgs/community/discussions/159971)
  reported in May 2025 that a rule for one repository affected Chat in another.
  This motivates repository-bound policies and the two-project opposite-policy
  E2E case.
- A [Copilot Cloud Agent report](https://github.com/orgs/community/discussions/194256)
  from April 2026 described an agent reading a configured excluded path. This
  motivates the test requirement that blocked scopes make zero provider calls,
  rather than merely checking a response for leaked text.

