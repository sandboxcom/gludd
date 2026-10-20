#!/usr/bin/env python3
"""Build a bounded, deterministic GitHub release-page preview from a ledger."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

MAX_LEDGER_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_COMPLETED_ITEMS = 64
MAX_IMPLEMENTED_ITEMS = 64
MAX_EXCLUDED_ITEMS = 64
MAX_EVIDENCE_COMMITS = 8
RELEASE_CATEGORIES = ("Features", "Improvements")

_RELEASE_RE = re.compile(r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?\Z")
_TASK_RE = re.compile(r"S\d+(?:\.[0-9A-Za-z]+)+\Z")
_ITEM_RE = re.compile(r"(?:S\d+(?:\.[0-9A-Za-z]+)*|#[1-9]\d*|[A-Z][A-Z0-9_-]{1,31})\Z")
_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
_ROLE_RE = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_REPOSITORY_RE = re.compile(r"[0-9A-Za-z_.-]+/[0-9A-Za-z_.-]+\Z")

_V012_INVENTORY_RE = re.compile(
    rb"<!-- v012-integration-inventory:start -->\s*```json\s*(.*?)\s*"
    rb"```\s*<!-- v012-integration-inventory:end -->",
    re.DOTALL,
)
_V012_STREAM_BOUNDARIES = {
    "natural_language_config": (
        "partial_branch_local", "blocked_pending_reconciliation", "disabled"
    ),
    "typed_domain_adapters": (
        "partial_branch_local", "feature_only_review_ready", "unwired"
    ),
    "openjev_laya": (
        "partial_branch_local", "feature_only_review_ready", "disabled"
    ),
    "freellmapi_upstream_sync": (
        "contract_only", "feature_only_review_ready", "unadmitted"
    ),
    "azure_accelerator_acceptance": (
        "design_only", "feature_only_review_ready", "unadmitted"
    ),
}

# The historical integration-preview contract remains part of every v0.1.2
# rendering. Only the versioned manifest supplies its source heads and status.
_V012_BOUNDARIES = """\
## Bounded change summary

Each normative claim points to the manifest, contract, or runbook that defines
its limit. A complete source snapshot records source work only; its tests do not
transfer to the combined candidate.

<!-- v012-release-claims:start -->
| Claim | Boundary for this preview | Source |
| --- | --- | --- |
| Release state | The integration inventory does not establish combined release proof. | [Manifest proof \
status](v0.1.2-integration-manifest.md#proof-status) |
| Natural-language head | Only the exact inventoried source head is included; any later pending descendant needs \
a deliberate inventory refresh. | [Manifest snapshot](v0.1.2-integration-manifest.md#scope-and-snapshot-boundary) |
| Natural-language integration | The independent roots conflict and remain blocked pending reconciliation. | \
[Manifest verified ancestry](v0.1.2-integration-manifest.md#verified-ancestry) |
| Natural-language authority | v0.1.2 stops at non-mutating plan/status; approval, apply, verification, and \
managed-host rollback are outside the tranche. | [Natural-language acceptance \
contract](../features/NATURAL_LANGUAGE_CONFIGURATION_ACCEPTANCE.md) |
| Local model profiles | Ollama, vLLM, and llama.cpp require exact identities, strict output validation, local \
endpoints, and no provider fallback. | [Natural-language acceptance \
contract](../features/NATURAL_LANGUAGE_CONFIGURATION_ACCEPTANCE.md#provider-neutral-local-profiles) |
| Natural-language evaluation | The older evaluator is diagnostic until thresholds and receipts are reconciled \
with the acceptance contract. | [Natural-language \
evaluation](../features/NATURAL_LANGUAGE_CONFIGURATION_EVALUATION.md) |
| Typed-domain proof | The inventoried source snapshot has only partial branch-local evidence. | [Manifest proof \
status](v0.1.2-integration-manifest.md#proof-status) |
| Typed artifact authority | The universal artifact is provider-neutral and recommendation-only; it cannot \
authorize execution. | [Typed-decision contract](../features/TYPED_DECISION_IMPLEMENTATION_CONTRACT.md) |
| Universal projections | Narrow QCSchema, SARIF, Redfish, and configuration-plan/CUE inputs normalize to Choice, \
Score, or Noul under exact routes. | [Typed-decision \
contract](../features/TYPED_DECISION_IMPLEMENTATION_CONTRACT.md#domain-output-adapters) |
| Typed evaluation | The 28-case evaluator is hermetic and grants only offline comparison and shadow evidence. | \
[Domain-adapter runbook](../features/TYPED_DECISION_DOMAIN_ADAPTER_EVALUATION_RUNBOOK.md) |
| OpenJev/Laya proof | Its exact source snapshot has only partial branch-local evidence. | [Manifest proof \
status](v0.1.2-integration-manifest.md#proof-status) |
| OpenJev/Laya capability | The candidate covers offline contract, normalization, compatibility, reporting, \
evidence persistence, and replay only. | [OpenJev/Laya runbook](../operations/OPENJEV_LAYA_ADAPTER_RUNBOOK.md) |
| OpenJev/Laya runtime | It cannot discover, download, send traffic, admit a profile, manage a model, promote a \
canary, or execute runtime rollback. | [OpenJev/Laya contract](v0.1.2-openjev-laya-adapter-contract.md) |
| Default state | Natural-language intake and OpenJev/Laya profiles remain disabled; typed-domain readers remain \
unwired. | [Manifest ZDD boundary](v0.1.2-integration-manifest.md#zdd-and-rollback) |
| Coverage and gate | A combined SHA needs at least 85% aggregate and 75% per file coverage, stricter feature \
thresholds, and the full gate. | [Manifest proof status](v0.1.2-integration-manifest.md#proof-status) |
| Rollback | Retain evidence, stop feature intake first, and revert feature commits in reverse order without \
touching operator-owned services. | [Manifest ZDD and rollback](v0.1.2-integration-manifest.md#zdd-and-rollback) |
| FreeLLMAPI and Azure | The former is contract-only and the latter design-only; neither stream grants candidate \
admission, transport, allocation, live inference, or a route. | [Manifest trust \
boundaries](v0.1.2-integration-manifest.md#trust-boundaries) |
<!-- v012-release-claims:end -->

## Natural-language local configuration

The intended opt-in local-model experience turns a configuration request into a
typed preview that an operator can inspect and read back. The v0.1.2 state
machine ends at `plan_only_opt_in`; every catalog action retains
`apply_supported: false`.
[Natural-language acceptance contract](../features/NATURAL_LANGUAGE_CONFIGURATION_ACCEPTANCE.md#release-states)

This is a non-mutating plan/status capability. Managed-host mutation remains forbidden.
No approve, apply, verify, or rollback route is admitted. Ollama, vLLM, and
llama.cpp are candidates only when exact model, runtime, prompt, schema, and
catalog identities pass strict conformance without cloud or alternate-provider
fallback.
[Natural-language acceptance \
contract](../features/NATURAL_LANGUAGE_CONFIGURATION_ACCEPTANCE.md#provider-neutral-local-profiles)

The manifest records the inventoried source descendant; every later pending
descendant remains outside this snapshot. The independently rooted acceptance
implementation overlaps the earlier catalog, contracts, planner, collection,
roles, and tests. Integration is blocked pending reconciliation. This inventory
does not establish API, CLI, collection, Molecule, or live local-model authority
on the release branch.
[Manifest conflict inventory](v0.1.2-integration-manifest.md#conflict-and-overlap-inventory)

## Universal typed-domain work

The strict provider-neutral artifact covers narrow chemistry/materials
QCSchema, code/firmware SARIF, Redfish hardware observations, and
configuration-plan projections using CUE as the external validation model.
Adapters normalize those inputs into Choice, Score, or Noul while retaining
exact provenance and rejecting ambiguous, cross-domain, malformed, or
non-finite data.
[Typed-decision implementation \
contract](../features/TYPED_DECISION_IMPLEMENTATION_CONTRACT.md#domain-output-adapters)

The evaluator uses 28 deterministic cases with no model, provider, network,
filesystem, or wall-clock authority. Its receipts support offline comparison
and shadow admission only. This remains recommendation-only, with partial
branch-local evidence and runtime unwired. Append-only artifacts make changed
semantics and replay mismatches visible; they grant no route, task policy,
endpoint, model identity, credential, deployment, or mutation authority.
[Universal domain-adapter evaluation runbook](../features/TYPED_DECISION_DOMAIN_ADAPTER_EVALUATION_RUNBOOK.md)

## OpenJev/Laya offline integration

The candidate consumes already-local immutable evidence. Its branch-local
surface checks contracts, normalizes typed results, plans compatibility and
residency, renders a dry-run, optionally writes a create-only pre-projection
handoff, and replays offline recovery evidence.
[OpenJev/Laya operator runbook](../operations/OPENJEV_LAYA_ADAPTER_RUNBOOK.md)

Runtime admission is not implemented. Recovery execution is not implemented.
The workflow cannot discover endpoints, resolve tags, fetch source or models,
send traffic, apply a projection, preload or evict a model, change profile state,
promote a canary, or invoke post-projection rollback.
[OpenJev/Laya contract](v0.1.2-openjev-laya-adapter-contract.md)

OpenJev endpoints remain operator-owned. Laya may appear only behind a future
Gludd-owned universal worker. Long-lived evidence is not established: the
snapshot's OpenJev and Laya reports were days old and define regression
hypotheses rather than an availability or safety record.
[OpenJev/Laya practitioner ledger](../operations/OPENJEV_LAYA_ADAPTER_RUNBOOK.md#practitioner-issues-ledger)

## Compatibility and upgrade notes

- Python 3.11 remains the minimum supported interpreter for this draft; no new minimum is asserted. [Project \
metadata](../../pyproject.toml)
- The historical integration inventory does not change v0.1.1 or authorize a schema/database migration, API, CLI, \
collection, route, daemon, port, dependency, or model process. [Manifest \
scope](v0.1.2-integration-manifest.md#scope-and-snapshot-boundary)
- There is no automatic enablement. Keep authoritative behavior active while natural-language intake stays dark, \
typed-domain readers stay unwired, and OpenJev/Laya candidates stay disabled. [Manifest ZDD \
boundary](v0.1.2-integration-manifest.md#zdd-and-rollback)
- Use exact feature-only selections: typed-domain work, OpenJev/Laya, reviewed natural-language reconciliation, \
and then release-planning documentation and the refreshed manifest. Whole source branches are not authorized. \
[Manifest integration sequence](v0.1.2-integration-manifest.md#next-integration-sequence)
- Retain immutable digests, exact schema/runtime/profile versions, and original append-only receipts across \
upgrades. A mutable tag, branch, endpoint label, or provider name does not prove compatibility. [Manifest trust \
boundaries](v0.1.2-integration-manifest.md#trust-boundaries)
- Operators should make no runtime change from this inventory. Upgrade instructions need exact combined-SHA \
receipts and gate evidence. [Manifest proof status](v0.1.2-integration-manifest.md#proof-status)

## Security and privacy

- Natural-language calls require exact loopback, RFC 1918, or IPv6 unique-local profiles. Redirects, proxies, \
credential-bearing URLs, mutable aliases, public endpoints, output repair, and cloud fallback fail closed. \
[Natural-language acceptance \
contract](../features/NATURAL_LANGUAGE_CONFIGURATION_ACCEPTANCE.md#provider-neutral-local-profiles)
- Provider output remains untrusted after HTTP success. Enforce envelope, size, encoding, schema, identity, \
semantic-grounding, timeout, cancellation, and target-scope checks before accepting a preview. [Natural-language \
adversarial contract](../features/NATURAL_LANGUAGE_CONFIGURATION_ACCEPTANCE.md#security-adversarial-matrix)
- Natural-language receipts retain digests and stable reason codes rather than prompts, raw provider output, \
endpoint URLs, credentials, diffs, or configuration values. [Natural-language receipt \
contract](../features/NATURAL_LANGUAGE_CONFIGURATION_ACCEPTANCE.md#coverage-and-evidence-receipts)
- Typed-domain redaction precedes persistence and observation. Unknown fields, cross-request reuse, cross-domain \
data, digest drift, non-finite values, and changed semantics reject rather than coerce. [Typed-decision \
contract](../features/TYPED_DECISION_IMPLEMENTATION_CONTRACT.md#domain-output-adapters)
- OpenJev/Laya accepts only already-local immutable evidence. Never acquire, mutate, stop, or delete \
operator-owned endpoints or artifacts. [OpenJev/Laya \
preconditions](../operations/OPENJEV_LAYA_ADAPTER_RUNBOOK.md#authority-and-preconditions)
- Practitioner reports describe version-scoped failures. Closure does not establish that an arbitrary candidate \
pin contains a fix. [Manifest practitioner reports](v0.1.2-integration-manifest.md#practitioner-reports)

## Disabled by default

All three original integration streams are disabled by default at their authority
boundary. No recorded source status permits silently advancing a state.
[Manifest trust and ZDD boundaries](v0.1.2-integration-manifest.md#zdd-and-rollback)

| Surface | Default | What remains unavailable | Future admission requirement |
| --- | --- | --- | --- |
| Natural-language configuration | Disabled/dark | New plan intake, API/CLI/collection registration, and every \
mutation path | Reconciled final descendant, exact profile receipts, focused/API/Molecule/E2E/security/ZDD proof, \
coverage, and full gate. [Acceptance contract](../features/NATURAL_LANGUAGE_CONFIGURATION_ACCEPTANCE.md) |
| Universal typed-domain adapters | Unwired | Runtime routing, provider selection, task-policy authority, and \
execution | Combined adapter/ledger/evaluator proof and separately reviewed shadow-only admission. [Evaluation \
runbook](../features/TYPED_DECISION_DOMAIN_ADAPTER_EVALUATION_RUNBOOK.md) |
| OpenJev/Laya | Candidate profiles disabled | Discovery, transport, model lifecycle, traffic, canary, promotion, \
and runtime rollback | Maturity/security review, live implementation, independent approvals, hermetic and \
pinned-live proof, rollback drill, and full gate. [Operator \
runbook](../operations/OPENJEV_LAYA_ADAPTER_RUNBOOK.md#phase-5-staged-admission-future-gate) |

## ZDD and rollback

Zero-downtime deployment keeps the current path authoritative while this work is
dark, unwired, or offline. It supplies no evidence of live rollout or rollback.
[Manifest ZDD and rollback](v0.1.2-integration-manifest.md#zdd-and-rollback)

| Stream | ZDD posture | Fail-closed rollback |
| --- | --- | --- |
| Natural-language configuration | Integrate dark; permit at most one plan-only tenant/profile after exact \
receipts; managed-host change count stays zero. | Stop new plan intake, preserve status reads and digest-bound \
receipts, disable tuple/router/CLI/collection intake, drain read-only calls, and revert reconciled feature \
commits in reverse order. [Manifest](v0.1.2-integration-manifest.md#zdd-and-rollback) |
| Universal typed-domain adapters | Keep readers unwired and evaluation hermetic; add no provider process, route, \
port, migration, or policy switch. | Stop shadow evaluation, retain append-only ledger/artifact/receipt evidence, \
unregister readers, and revert provider freeze, runbook, evaluator, adapters, ledger, and core in reverse order. \
[Manifest](v0.1.2-integration-manifest.md#zdd-and-rollback) |
| OpenJev/Laya | Keep candidates offline and disabled; no source acquisition, request, model lifecycle, canary, \
or recovery change. | Stop offline intake, preserve source/profile/report/update receipts, remove only \
workflow-owned temporary output, and revert selected commits. Leave operator-owned endpoints and artifacts \
untouched. [Manifest](v0.1.2-integration-manifest.md#zdd-and-rollback) |
| FreeLLMAPI and Azure | Keep the admitted generation serving; these inventory entries are contract/design only. \
| Any future generation must restore the old route first, stop candidate intake, drain bounded work, retain \
receipts, remove only lease-owned resources, and prove absence. Cleanup uncertainty remains CLEANUP_PENDING. \
[Manifest](v0.1.2-integration-manifest.md#zdd-and-rollback) |

The OpenJev/Laya recovery receipt records a disabled pre-projection boundary;
it is not callable runtime rollback. Every future mutating/live tranche needs a
generation-bound canary, drain, exact-candidate rollback drill, and retained
original evidence before promotion.
[OpenJev/Laya rollback handoff](../operations/OPENJEV_LAYA_ADAPTER_RUNBOOK.md#phase-6-rollback-and-zdd-handoff)

## Evidence status

| Stream | Retained evidence | Evidence still required |
| --- | --- | --- |
| Natural-language configuration | Separate source branches contain focused artifacts and tests. | Two-root \
reconciliation, exact combined-head coverage and focused/API/Molecule/E2E/security/ZDD receipts, and combined \
gate. [Manifest](v0.1.2-integration-manifest.md#proof-status) |
| Universal typed-domain work | The source task reported a 32-test runbook/evaluator slice plus lint, strict \
typing, and Markdown checks. | Combined core/ledger/domain coverage, integration receipt, and exact-candidate \
full gate; the retained results remain partial branch-local evidence. \
[Manifest](v0.1.2-integration-manifest.md#proof-status) |
| OpenJev/Laya | The source task reported focused/structural tests, lint, typing, Markdown, collection, and \
test-count checks. | Combined coverage, release receipts, live runtime and rollback proof, and full gate; \
retained results remain partial branch-local evidence. [Manifest](v0.1.2-integration-manifest.md#proof-status) |
| FreeLLMAPI and Azure | Source contracts and design ancestry only. | Separate exact candidate, runtime, \
security, lifecycle, rollback, cleanup, coverage, and gate proofs. \
[Manifest](v0.1.2-integration-manifest.md#proof-status) |
| Combined v0.1.2 | Ancestry, selection, exclusion, conflict, trust, and rollback inventory only. | At least 85% \
aggregate and 75% per file coverage, stricter feature thresholds, zero warnings/collection errors, \
integration/Molecule/E2E proof, and full gate. [Manifest](v0.1.2-integration-manifest.md#proof-status) |

The full gate is pending. Collection discovers tests; source summaries do not
transfer to another combined SHA.
[Manifest proof status](v0.1.2-integration-manifest.md#proof-status)

## Known limitations

- Only exact inventoried heads are covered. Any later pending descendant requires an explicit manifest refresh. \
[Manifest snapshot](v0.1.2-integration-manifest.md#scope-and-snapshot-boundary)
- Natural-language foundation and acceptance have independent roots and overlapping implementations. Automatic \
conflict resolution cannot establish semantic parity. [Manifest \
conflicts](v0.1.2-integration-manifest.md#conflict-and-overlap-inventory)
- No combined release-grade API/Molecule/E2E, exact local-model, installed typed-decision collection, admitted \
OpenJev/Laya runtime, or rollback-drill receipt follows from this inventory. [Manifest proof \
status](v0.1.2-integration-manifest.md#proof-status)
- Typed-domain and OpenJev/Laya source heads are feature-review inputs. Their branch-local receipts do not \
establish future integration-SHA proof. [Manifest proof status](v0.1.2-integration-manifest.md#proof-status)
- The Ansible and schema/runtime reports have multi-year history. Snapshot OpenJev/Laya reports were days old; \
long-term availability, security response, and reproducibility remain unknown. [Manifest practitioner \
reports](v0.1.2-integration-manifest.md#practitioner-reports)
- The relative source contracts below are restored from exact native source artifacts. Their presence supplies \
documentation, without integrating source implementations or granting runtime authority. [Manifest feature-only \
selections](v0.1.2-integration-manifest.md#feature-only-selections)

## Source map

The manifest alone supplies integration status; the ledger supplies the
Features/Improvements inventory. Restored contracts explain behavior without
promoting a stream. Exact native source views retain document provenance.
[Manifest integration sequence](v0.1.2-integration-manifest.md#next-integration-sequence)

| Authority | Target-branch path | Exact reviewed document source |
| --- | --- | --- |
| Integration heads, selections, proof, trust, ZDD, rollback, reports | [Integration \
manifest](v0.1.2-integration-manifest.md) | Manifest base `__V012_MANIFEST_COMMIT__` |
| Natural-language release boundary | [Acceptance \
contract](../features/NATURAL_LANGUAGE_CONFIGURATION_ACCEPTANCE.md) | \
[`ea12b54b`](https://github.com/sandboxcom/gludd/blob/ea12b54b97e3ce389395bc69fb43343362564522/docs/features/NATU\
RAL_LANGUAGE_CONFIGURATION_ACCEPTANCE.md) |
| Natural-language diagnostic evaluation | [Evaluation \
contract](../features/NATURAL_LANGUAGE_CONFIGURATION_EVALUATION.md) | \
[`43efab68`](https://github.com/sandboxcom/gludd/blob/43efab68e4872b34997b77a3b18a680185e7598c/docs/features/NATU\
RAL_LANGUAGE_CONFIGURATION_EVALUATION.md) |
| Universal artifact/adapters | [Typed-decision contract](../features/TYPED_DECISION_IMPLEMENTATION_CONTRACT.md) \
| \
[`0769f40e`](https://github.com/sandboxcom/gludd/blob/0769f40e105ec1b829c9e31c7033706380577cba/docs/features/TYPE\
D_DECISION_IMPLEMENTATION_CONTRACT.md) |
| Universal evaluation/rollback | [Domain-adapter \
runbook](../features/TYPED_DECISION_DOMAIN_ADAPTER_EVALUATION_RUNBOOK.md) | \
[`0769f40e`](https://github.com/sandboxcom/gludd/blob/0769f40e105ec1b829c9e31c7033706380577cba/docs/features/TYPE\
D_DECISION_DOMAIN_ADAPTER_EVALUATION_RUNBOOK.md) |
| OpenJev/Laya offline contract | [Adapter contract](v0.1.2-openjev-laya-adapter-contract.md) | \
[`c48c7619`](https://github.com/sandboxcom/gludd/blob/c48c7619552e5b5bfc42937937476874fef8fcb6/docs/releases/v0.1\
.2-openjev-laya-adapter-contract.md) |
| OpenJev/Laya workflow/recovery | [Operator runbook](../operations/OPENJEV_LAYA_ADAPTER_RUNBOOK.md) | \
[`c48c7619`](https://github.com/sandboxcom/gludd/blob/c48c7619552e5b5bfc42937937476874fef8fcb6/docs/operations/OP\
ENJEV_LAYA_ADAPTER_RUNBOOK.md) |
"""


class ReleaseNotesError(ValueError):
    """Raised when ledger evidence cannot produce safe release-page notes."""


@dataclass(frozen=True)
class EvidenceCommit:
    """One immutable implementation receipt linked from release notes."""

    role: str
    sha: str


@dataclass(frozen=True)
class CompletedItem:
    """One completed task and its public release-page classification."""

    task_id: str
    title: str
    category: str
    evidence_commits: tuple[EvidenceCommit, ...]


@dataclass(frozen=True)
class ImplementedItem:
    """One implemented candidate that still lacks release-completion proof."""

    item_id: str
    title: str
    category: str
    evidence_commits: tuple[EvidenceCommit, ...]


@dataclass(frozen=True)
class ReleaseLedger:
    """Validated release-page projection of the completed-backlog ledger."""

    schema_version: int
    release: str
    status: str
    repository: str
    baseline_ref: str
    baseline_commit: str
    completed_items: tuple[CompletedItem, ...]
    implemented_items: tuple[ImplementedItem, ...]
    excluded_open_tasks: tuple[str, ...]


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ReleaseNotesError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, label: str, maximum: int) -> Sequence[object]:
    if not isinstance(value, list):
        raise ReleaseNotesError(f"{label} must be an array")
    if not value or len(value) > maximum:
        raise ReleaseNotesError(f"{label} must contain between 1 and {maximum} entries")
    return cast(Sequence[object], value)


def _text(
    record: Mapping[str, object],
    key: str,
    label: str,
    *,
    maximum: int,
) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ReleaseNotesError(f"{label}.{key} must be a non-empty trimmed string")
    if len(value) > maximum or any(ord(character) < 32 for character in value):
        raise ReleaseNotesError(f"{label}.{key} exceeds its safe text boundary")
    return value


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    decoded: dict[str, object] = {}
    for key, value in pairs:
        if key in decoded:
            raise ReleaseNotesError(f"duplicate JSON key is not allowed: {key}")
        decoded[key] = value
    return decoded


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _load_json(
    path: Path, *, label: str = "ledger", markdown_inventory: bool = False
) -> Mapping[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ReleaseNotesError(f"{label} must be a regular non-symlink file: {path}")
    before = path.stat()
    if before.st_size > MAX_LEDGER_BYTES:
        raise ReleaseNotesError(
            f"{label} size {before.st_size} exceeds {MAX_LEDGER_BYTES} bytes"
        )
    raw = path.read_bytes()
    try:
        after = path.stat()
    except OSError as exc:
        raise ReleaseNotesError(f"{label} changed while it was being read") from exc
    if (
        path.is_symlink()
        or len(raw) != before.st_size
        or _file_identity(before) != _file_identity(after)
    ):
        raise ReleaseNotesError(f"{label} changed while it was being read")
    if markdown_inventory:
        matches = _V012_INVENTORY_RE.findall(raw)
        if len(matches) != 1:
            raise ReleaseNotesError(f"{label} must contain exactly one inventory block")
        raw = matches[0]
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseNotesError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    return _mapping(cast(object, decoded), label)


def _parse_evidence(value: object, item_label: str) -> tuple[EvidenceCommit, ...]:
    records = _sequence(
        value,
        f"{item_label}.evidence_commits",
        MAX_EVIDENCE_COMMITS,
    )
    evidence: list[EvidenceCommit] = []
    seen: set[str] = set()
    for index, raw_record in enumerate(records):
        label = f"{item_label}.evidence_commits[{index}]"
        record = _mapping(raw_record, label)
        role = _text(record, "role", label, maximum=32)
        sha = _text(record, "sha", label, maximum=40)
        if not _ROLE_RE.fullmatch(role):
            raise ReleaseNotesError(f"{label}.role has an invalid identifier")
        if not _SHA_RE.fullmatch(sha):
            raise ReleaseNotesError(f"{label}.sha must be 40 lowercase hexadecimal characters")
        if sha in seen:
            raise ReleaseNotesError(f"{item_label} evidence commits must be unique")
        seen.add(sha)
        evidence.append(EvidenceCommit(role=role, sha=sha))
    return tuple(evidence)


def _parse_item_fields(
    record: Mapping[str, object],
    label: str,
    *,
    identifier_key: str,
    identifier_pattern: re.Pattern[str],
) -> tuple[str, str, str, tuple[EvidenceCommit, ...]]:
    identifier = _text(record, identifier_key, label, maximum=32)
    title = _text(record, "title", label, maximum=180)
    category = _text(record, "release_page_category", label, maximum=16)
    if not identifier_pattern.fullmatch(identifier):
        raise ReleaseNotesError(f"{label}.{identifier_key} is invalid")
    if category not in RELEASE_CATEGORIES:
        raise ReleaseNotesError(
            f"{label}.release_page_category must be Features or Improvements"
        )
    evidence = _parse_evidence(record.get("evidence_commits"), label)
    return identifier, title, category, evidence


def _parse_completed_items(value: object) -> tuple[CompletedItem, ...]:
    records = _sequence(value, "completed_items", MAX_COMPLETED_ITEMS)
    items: list[CompletedItem] = []
    task_ids: set[str] = set()
    categories: set[str] = set()
    for index, raw_record in enumerate(records):
        label = f"completed_items[{index}]"
        record = _mapping(raw_record, label)
        task_id, title, category, evidence = _parse_item_fields(
            record,
            label,
            identifier_key="task_id",
            identifier_pattern=_TASK_RE,
        )
        if task_id in task_ids:
            raise ReleaseNotesError("completed task identifiers must be unique")
        task_ids.add(task_id)
        categories.add(category)
        items.append(
            CompletedItem(
                task_id=task_id,
                title=title,
                category=category,
                evidence_commits=evidence,
            )
        )
    if categories != set(RELEASE_CATEGORIES):
        raise ReleaseNotesError("completed items must include both Features and Improvements")
    return tuple(items)


def _parse_implemented_items(value: object) -> tuple[ImplementedItem, ...]:
    if value is None:
        return ()
    records = _sequence(value, "implemented_items", MAX_IMPLEMENTED_ITEMS)
    items: list[ImplementedItem] = []
    item_ids: set[str] = set()
    for index, raw_record in enumerate(records):
        label = f"implemented_items[{index}]"
        record = _mapping(raw_record, label)
        item_id, title, category, evidence = _parse_item_fields(
            record,
            label,
            identifier_key="item_id",
            identifier_pattern=_ITEM_RE,
        )
        if item_id in item_ids:
            raise ReleaseNotesError("implemented item identifiers must be unique")
        item_ids.add(item_id)
        items.append(
            ImplementedItem(
                item_id=item_id,
                title=title,
                category=category,
                evidence_commits=evidence,
            )
        )
    return tuple(items)


def _parse_excluded(value: object, completed: set[str]) -> tuple[str, ...]:
    records = _sequence(value, "excluded_open_tasks", MAX_EXCLUDED_ITEMS)
    excluded: list[str] = []
    for index, raw_task_id in enumerate(records):
        if not isinstance(raw_task_id, str) or not _TASK_RE.fullmatch(raw_task_id):
            raise ReleaseNotesError(f"excluded_open_tasks[{index}] is invalid")
        if raw_task_id in completed or raw_task_id in excluded:
            raise ReleaseNotesError("excluded task identifiers must be unique and open")
        excluded.append(raw_task_id)
    return tuple(excluded)


def load_release_ledger(path: Path, expected_release: str) -> ReleaseLedger:
    """Load and fully validate a completed-backlog ledger for one release."""
    if not _RELEASE_RE.fullmatch(expected_release):
        raise ReleaseNotesError("expected release must be a v-prefixed semantic version")
    root = _load_json(path)
    schema_version = root.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise ReleaseNotesError("ledger.schema_version must be 1")
    release = _text(root, "release", "ledger", maximum=64)
    if release != expected_release:
        raise ReleaseNotesError(
            f"ledger release {release!r} does not match expected release {expected_release!r}"
        )
    page = _mapping(root.get("release_page"), "ledger.release_page")
    status = _text(page, "status", "ledger.release_page", maximum=16)
    if status != "unreleased":
        raise ReleaseNotesError("release_page.status must remain unreleased")
    repository = _text(page, "repository", "ledger.release_page", maximum=128)
    if not _REPOSITORY_RE.fullmatch(repository):
        raise ReleaseNotesError("release_page.repository must be an owner/name pair")
    baseline = _mapping(root.get("baseline"), "ledger.baseline")
    baseline_ref = _text(baseline, "ref", "ledger.baseline", maximum=64)
    baseline_commit = _text(baseline, "commit", "ledger.baseline", maximum=40)
    if not _RELEASE_RE.fullmatch(baseline_ref) or not _SHA_RE.fullmatch(baseline_commit):
        raise ReleaseNotesError("ledger baseline must contain a release ref and full commit")
    completed_items = _parse_completed_items(root.get("completed_items"))
    implemented_items = _parse_implemented_items(root.get("implemented_items"))
    completed_ids = {item.task_id for item in completed_items}
    if completed_ids & {item.item_id for item in implemented_items}:
        raise ReleaseNotesError(
            "implemented item identifiers must not duplicate completed tasks"
        )
    excluded = _parse_excluded(
        root.get("excluded_open_tasks"),
        completed_ids,
    )
    return ReleaseLedger(
        schema_version=schema_version,
        release=release,
        status=status,
        repository=repository,
        baseline_ref=baseline_ref,
        baseline_commit=baseline_commit,
        completed_items=completed_items,
        implemented_items=implemented_items,
        excluded_open_tasks=excluded,
    )


def _v012_release_status(manifest_path: Path) -> dict[str, object]:
    """Project the versioned integration inventory without promoting its proof."""
    inventory = _load_json(
        manifest_path, label="integration manifest", markdown_inventory=True
    )
    if type(inventory.get("schema_version")) is not int or inventory["schema_version"] != 1:
        raise ReleaseNotesError("integration manifest.schema_version must be 1")
    manifest_commit = _text(inventory, "manifest_base", "integration manifest", maximum=40)
    if not _SHA_RE.fullmatch(manifest_commit):
        raise ReleaseNotesError("integration manifest.manifest_base must be a full commit")
    if inventory.get("combined_release_proof") != "pending":
        raise ReleaseNotesError("integration manifest combined release proof must be pending")
    streams = _mapping(inventory.get("streams"), "integration manifest.streams")
    if set(streams) != set(_V012_STREAM_BOUNDARIES):
        raise ReleaseNotesError("integration manifest must retain every reviewed stream")
    projected_streams: dict[str, object] = {}
    for name, (proof, integration, default) in _V012_STREAM_BOUNDARIES.items():
        label = f"integration manifest.streams.{name}"
        source = _mapping(streams[name], label)
        stream: dict[str, object] = {
            field: _text(source, field, label, maximum=64)
            for field in ("head", "status", "proof_status", "integration_status")
        }
        if not _SHA_RE.fullmatch(cast(str, stream["head"])):
            raise ReleaseNotesError(f"{label}.head must be a full commit")
        if stream["status"] not in ("complete_source_snapshot", "pending_descendant"):
            raise ReleaseNotesError(f"{label}.status must remain a source snapshot")
        if stream["proof_status"] != proof or stream["integration_status"] != integration:
            raise ReleaseNotesError(f"{label} cannot promote proof or integration authority")
        stream["default_state"] = default
        if name == "openjev_laya":
            stream.update(
                runtime_admission="not_implemented",
                recovery_execution="not_implemented",
                long_lived_evidence="not_established",
            )
        projected_streams[name] = stream
    return {
        "schema_version": 1,
        "manifest_commit": manifest_commit,
        "release_status": "draft_integration_preview",
        "combined_release_proof": "pending",
        "full_gate": "pending",
        "streams": projected_streams,
    }


def _v012_integration_notes(manifest_path: Path) -> str:
    """Retain the historical user/operator boundary beside the ledger inventory."""
    status = _v012_release_status(manifest_path)
    return (
        "## Release status\n\n"
        "This is a draft integration preview: integration inventory, not release evidence.\n"
        "The ledger above records completed and implemented source items; this\n"
        "historical manifest separately records integration and proof boundaries.\n"
        "Neither authorizes a release, whole-branch merge, enablement, or change to v0.1.1.\n"
        "Any pending descendant remains excluded until deliberately inventoried.\n"
        "[Manifest scope](v0.1.2-integration-manifest.md#scope-and-snapshot-boundary)\n\n"
        "The following status is projected from the versioned manifest, never from\n"
        "branch names, current observations, source summaries, or clean worktrees.\n\n"
        "<!-- v012-release-status:start -->\n```json\n"
        + json.dumps(status, indent=2)
        + "\n```\n<!-- v012-release-status:end -->\n\n"
        + _V012_BOUNDARIES.replace(
            "__V012_MANIFEST_COMMIT__", cast(str, status["manifest_commit"])
        )
    )


def build_release_page_notes(
    ledger: ReleaseLedger, *, integration_manifest: Path | None = None
) -> str:
    """Render stable Markdown accepted by ``gh release --notes-file``."""
    lines = [
        f"# Gludd {ledger.release}",
        "",
        "> **Status: Unreleased.** This deterministic preview comes from the",
        "> release ledger and does not create or publish a GitHub release. Candidate",
        "> entries do not claim an exact-head gate, release completion, or publication.",
        "",
    ]
    for category in RELEASE_CATEGORIES:
        lines.extend((f"## {category}", ""))
        lines.extend(("### Formally completed backlog", ""))
        for completed_item in ledger.completed_items:
            if completed_item.category != category:
                continue
            links = "; ".join(
                "["
                + evidence.role.replace("_", " ")
                + f" `{evidence.sha[:9]}`](https://github.com/{ledger.repository}/commit/{evidence.sha})"
                for evidence in completed_item.evidence_commits
            )
            lines.append(
                f"- **{completed_item.task_id} — {completed_item.title}.** "
                f"Evidence: {links}."
            )
        if ledger.implemented_items:
            lines.extend(("", "### Implemented candidate scope", ""))
        for implemented_item in ledger.implemented_items:
            if implemented_item.category != category:
                continue
            links = "; ".join(
                "["
                + evidence.role.replace("_", " ")
                + f" `{evidence.sha[:9]}`](https://github.com/{ledger.repository}/commit/{evidence.sha})"
                for evidence in implemented_item.evidence_commits
            )
            lines.append(
                f"- **{implemented_item.item_id} — {implemented_item.title}.** "
                f"Implementation evidence; exact-head/release proof pending. Evidence: {links}."
            )
        lines.append("")
    excluded = ", ".join(f"`{task_id}`" for task_id in ledger.excluded_open_tasks)
    lines.extend(
        (
            "## Release scope",
            "",
            f"- Formally completed backlog items: {len(ledger.completed_items)}.",
            "- Implemented candidate items pending exact-head/release proof: "
            f"{len(ledger.implemented_items)}.",
            f"- Open work excluded from completion claims: {excluded}.",
            "- Baseline: "
            f"[`{ledger.baseline_ref}`](https://github.com/{ledger.repository}/releases/tag/{ledger.baseline_ref}) "
            f"at `{ledger.baseline_commit}`.",
            "",
            "<!-- release-page-source: completed-backlog-ledger; "
            f"schema={ledger.schema_version}; status={ledger.status} -->",
            "",
        )
    )
    if ledger.release == "v0.1.2":
        manifest_path = integration_manifest or (
            Path(__file__).resolve().parents[1]
            / "docs/releases/v0.1.2-integration-manifest.md"
        )
        lines.extend(_v012_integration_notes(manifest_path).splitlines())
        lines.append("")
    rendered = "\n".join(lines)
    size = len(rendered.encode("utf-8"))
    if size > MAX_OUTPUT_BYTES:
        raise ReleaseNotesError(
            f"release-page notes size {size} exceeds {MAX_OUTPUT_BYTES} bytes"
        )
    return rendered


def _atomic_write(path: Path, content: str) -> None:
    if not path.parent.is_dir() or path.is_symlink():
        raise ReleaseNotesError("output parent must exist and output must not be a symlink")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sync_release_page_notes(
    ledger_path: Path,
    output_path: Path,
    expected_release: str,
    *,
    validate_only: bool,
) -> str:
    """Write notes atomically or prove an existing preview is byte-exact."""
    rendered = build_release_page_notes(
        load_release_ledger(ledger_path, expected_release)
    )
    if validate_only:
        if output_path.is_symlink() or not output_path.is_file():
            raise ReleaseNotesError(f"release-page preview is missing: {output_path}")
        current = output_path.read_bytes()
        if len(current) > MAX_OUTPUT_BYTES or current != rendered.encode("utf-8"):
            raise ReleaseNotesError(f"release-page preview is out of date: {output_path}")
    else:
        _atomic_write(output_path, rendered)
    return rendered


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", help="v-prefixed release version")
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the bounded file builder without contacting GitHub."""
    args = _parser().parse_args(argv)
    try:
        sync_release_page_notes(
            args.ledger,
            args.output,
            args.release,
            validate_only=args.validate_only,
        )
    except (OSError, ReleaseNotesError) as exc:
        print(f"release-page-notes: ERROR: {exc}")
        return 2
    action = "validated" if args.validate_only else "wrote"
    print(f"release-page-notes: {action} {args.output} for {args.release}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
