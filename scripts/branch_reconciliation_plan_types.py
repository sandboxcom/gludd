"""JSON schema types and pure builders for branch reconciliation planning."""

import hashlib
import hmac
import json
import subprocess
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Literal, TypedDict, cast


class BranchRecord(TypedDict):
    """Machine-readable classification for one local branch."""

    classification: Literal["ancestor", "patch-equivalent", "unique"]
    head: str
    lifecycle: Literal["current", "historical"]
    name: str
    patch_equivalent_commits: int
    ref: str
    unique_commits: int


class InventoryCounts(TypedDict):
    """Counts for one bounded branch inventory."""

    ancestor: int
    current: int
    historical: int
    patch_equivalent: int
    returned: int
    unique: int


class TargetRecord(TypedDict):
    """Resolved symbolic target identity."""

    head: str
    input: str
    ref: str


class InventoryBounds(TypedDict):
    """Hard bounds applied to Git traversal and output."""

    branch_limit: int
    commit_scan_limit: int
    local_ref_scan_limit: int


class InventoryPayload(TypedDict):
    """One bounded page of classified local branches."""

    after: str | None
    bounds: InventoryBounds
    branches: list[BranchRecord]
    counts: InventoryCounts
    limit: int
    next_cursor: str | None
    ok: bool
    schema_version: int
    target: TargetRecord
    truncated: bool


class SummaryCounts(InventoryCounts):
    """Counts for a terminal deduplicated inventory."""

    deduplicated_heads: int


class SummaryGroup(TypedDict):
    """Branches sharing one classified commit identity."""

    branch_count: int
    classification: Literal["ancestor", "patch-equivalent", "unique"]
    head: str
    lifecycle: Literal["current", "historical"]
    names: list[str]
    patch_equivalent_commits: int
    refs: list[str]
    unique_commits: int


class HeadSemanticSummary(TypedDict):
    """Bounded semantic evidence for one deduplicated head."""

    changed_path_count: int
    changed_paths: list[str]
    changed_paths_truncated: bool
    head: str
    path_redactions: int
    subject: str
    subject_truncated: bool


class SummaryPayload(TypedDict):
    """Terminal inventory with expanded branch groups."""

    bounds: InventoryBounds
    counts: SummaryCounts
    groups: list[SummaryGroup]
    mode: Literal["exhaustive-summary"]
    ok: bool
    page_size: int
    pages: int
    schema_version: int
    target: TargetRecord
    terminal: bool
    truncated: bool


class SummaryCountsPayload(TypedDict):
    """Terminal inventory without expanded branch groups."""

    bounds: InventoryBounds
    counts: SummaryCounts
    mode: Literal["exhaustive-counts"]
    ok: bool
    page_size: int
    pages: int
    schema_version: int
    target: TargetRecord
    terminal: bool
    truncated: bool


class CurrentSummaryPayload(TypedDict):
    """Terminal inventory restricted to unique current heads."""

    bounds: InventoryBounds
    counts: SummaryCounts
    groups: list[SummaryGroup]
    mode: Literal["exhaustive-current"]
    ok: bool
    page_size: int
    pages: int
    schema_version: int
    selected_branches: int
    selected_heads: int
    target: TargetRecord
    terminal: bool
    truncated: bool


class SemanticSummaryPayload(SummaryPayload):
    """Expanded terminal summary with semantic head evidence."""

    head_summaries: list[HeadSemanticSummary]


class SemanticCurrentSummaryPayload(CurrentSummaryPayload):
    """Current-only terminal summary with semantic head evidence."""

    head_summaries: list[HeadSemanticSummary]


class ConflictPreflight(TypedDict):
    """Bounded native-Git conflict evidence for one exact queue head."""

    conflict_path_count: int
    conflict_paths: list[str]
    conflict_paths_truncated: bool
    expected_source: str
    expected_target: str
    path_redactions: int
    result_tree: str
    status: Literal["clean", "conflicted"]


class MergeQueueBounds(TypedDict):
    """Independent bounds for a sequential merge queue."""

    branch_limit: int
    commit_scan_limit: int
    conflict_path_char_limit: int
    conflict_path_limit: int
    conflict_path_scan_limit: int
    local_ref_scan_limit: int
    merge_queue_head_limit: int
    merge_tree_output_char_limit: int


class MergeQueueCounts(TypedDict):
    """Terminal accounting across queued and already-integrated branches."""

    ancestor_branches: int
    ancestor_heads: int
    collapsed_branches: int
    collapsed_heads: int
    observed_branches: int
    observed_heads: int
    patch_equivalent_branches: int
    patch_equivalent_heads: int
    queued_branches: int
    queued_heads: int


class MergeQueueEntry(TypedDict):
    """One exact novel head to integrate sequentially."""

    branch_count: int
    expected_tip: str
    order: int
    preflight: ConflictPreflight
    refs: list[str]
    source_ref: str
    unique_commits: int


class CollapsedMergeQueueGroup(TypedDict):
    """One historical head excluded from the novel merge queue."""

    branch_count: int
    classification: Literal["ancestor", "patch-equivalent"]
    expected_tip: str
    refs: list[str]


class ReconciliationReceiptTarget(TypedDict):
    """Target identity at queue creation and the latest valid checkpoint."""

    base_head: str
    checkpoint_head: str
    input: str
    ref: str


class ReconciliationReceiptBody(TypedDict):
    """Canonical resumable state covered by one receipt digest."""

    collapsed: list[CollapsedMergeQueueGroup]
    cursor: int
    queue: list[MergeQueueEntry]
    target: ReconciliationReceiptTarget
    version: int


class ReconciliationReceipt(TypedDict):
    """Content-addressed checkpoint for sequential reconciliation."""

    algorithm: Literal["sha256"]
    body: ReconciliationReceiptBody
    digest: str


class MergeQueuePayload(TypedDict):
    """Terminal queue of novel heads and collapsed historical evidence."""

    bounds: MergeQueueBounds
    collapsed: list[CollapsedMergeQueueGroup]
    counts: MergeQueueCounts
    mode: Literal["sequential-merge-queue"]
    ok: bool
    page_size: int
    pages: int
    queue: list[MergeQueueEntry]
    receipt: ReconciliationReceipt
    schema_version: int
    target: TargetRecord
    terminal: bool
    truncated: bool


class ReceiptReplayBounds(TypedDict):
    """Hard bounds applied while replaying a reconciliation receipt."""

    conflict_path_char_limit: int
    conflict_path_limit: int
    conflict_path_scan_limit: int
    local_ref_scan_limit: int
    merge_queue_head_limit: int
    merge_tree_output_char_limit: int
    receipt_json_char_limit: int


class ReceiptReplayPayload(TypedDict):
    """Bounded result of replaying one reconciliation receipt."""

    bounds: ReceiptReplayBounds
    complete: bool
    cursor: int
    integrated: list[MergeQueueEntry]
    mode: Literal["reconciliation-receipt-replay"]
    newly_integrated: list[MergeQueueEntry]
    next: MergeQueueEntry | None
    ok: bool
    receipt: ReconciliationReceipt
    remaining_heads: int
    schema_version: int
    target: TargetRecord


class MergePlanHead(TypedDict):
    """Bounded changed-path evidence for one current unique head."""

    branch_count: int
    changed_path_count: int
    changed_paths: list[str]
    changed_paths_truncated: bool
    expected_tip: str
    path_redactions: int
    refs: list[str]
    shared_infrastructure_path_count: int
    shared_infrastructure_paths: list[str]
    shared_infrastructure_paths_truncated: bool
    source_ref: str
    unique_commits: int


class MergePlanGroupEntry(TypedDict):
    """Exact identity for one head in a conservative parallel group."""

    expected_tip: str
    refs: list[str]
    source_ref: str


class MergeRehearsalFreshnessCheck(TypedDict):
    """One exact ref-to-object assertion required before rehearsal or admission."""

    argv: list[str]
    expected_object: str
    ref: str


class MergeRehearsalPathCheck(TypedDict):
    """One exact branch-delta manifest assertion for shared-path admission."""

    argv: list[str]
    expected_path_count: int
    expected_path_sha256: str
    source_ref: str


class MergeRehearsalCommand(TypedDict):
    """A bounded command template emitted for an external rehearsal consumer."""

    argv: list[str]
    capture: str
    environment: dict[str, str]
    expected_exit_codes: list[int]
    purpose: str


class MergeRehearsalStep(TypedDict):
    """One synthetic, unreferenced merge commit in a group rehearsal."""

    candidate_input: str
    commit_tree: MergeRehearsalCommand
    expected_source: str
    merge_tree: MergeRehearsalCommand
    order: int
    source_ref: str


class MergeRehearsalPreflight(TypedDict):
    """Freshness and path evidence that must remain exact for one group."""

    changed_path_count: int
    freshness_checks: list[MergeRehearsalFreshnessCheck]
    pair_count: int
    path_checks: list[MergeRehearsalPathCheck]
    path_redactions: int
    shared_infrastructure_path_count: int
    shared_infrastructure_paths: list[str]
    shared_infrastructure_paths_truncated: bool
    shared_path_conflict_count: int
    shared_path_conflicts: list[str]


class MergeRehearsalAdmissionCommand(TypedDict):
    """One exact, non-integration command required for candidate admission."""

    argv: list[str]
    expected_exit_codes: list[int]
    purpose: str


class MergeRehearsalAdmission(TypedDict):
    """Commands and evidence required before separately authorized integration."""

    commands: list[MergeRehearsalAdmissionCommand]
    evidence_requirements: list[str]


class MergePredictionCheck(TypedDict):
    """One bounded native merge-tree prediction between exact commits."""

    conflict_path_count: int
    conflict_paths: list[str]
    conflict_paths_truncated: bool
    left: str
    path_redactions: int
    relation: Literal["target-source", "source-source"]
    result_tree: str
    right: str
    status: Literal["clean", "conflicted"]


class MergePredictionBounds(TypedDict):
    """Runtime and output ceilings for native group prediction."""

    check_limit: int
    conflict_path_display_limit: int
    conflict_path_scan_limit: int
    maximum_runtime_seconds: int
    output_char_limit: int
    timeout_seconds: int


MergePredictionBlockedReason = Literal[
    "ambiguous-output",
    "freshness-drift",
    "output-bound-exceeded",
    "prediction-check-bound-exceeded",
    "runtime-bound-exceeded",
    "unsupported-git",
]


class MergeConflictPrediction(TypedDict):
    """Pairwise Git-native evidence without a mergeability claim."""

    attempted_check_count: int
    blocked_reason: MergePredictionBlockedReason | None
    bounds: MergePredictionBounds
    checks: list[MergePredictionCheck]
    engine: Literal["git-merge-tree-write-tree"]
    required_check_count: int
    status: Literal["predicted-clean", "conflicted", "blocked"]


class MergeRehearsalBounds(TypedDict):
    """Independent ceilings for additive per-group rehearsal plans."""

    branch_limit: int
    group_limit: int
    head_limit: int
    path_display_limit: int
    path_limit: int


class MergeRehearsalPlan(TypedDict):
    """Deterministic recipe for an unreferenced cumulative group candidate."""

    admission: MergeRehearsalAdmission
    base_head: str
    bounds: MergeRehearsalBounds
    candidate_id: str
    candidate_kind: Literal["unreferenced-commit-chain"]
    conflict_prediction: MergeConflictPrediction
    final_candidate: str
    preflight: MergeRehearsalPreflight
    recipe: list[MergeRehearsalStep]
    source_heads: list[str]


class MergePlanGroup(TypedDict):
    """One pairwise-disjoint group of current heads."""

    branch_count: int
    entries: list[MergePlanGroupEntry]
    head_count: int
    order: int
    rehearsal: MergeRehearsalPlan


class MergePlanCollision(TypedDict):
    """Bounded evidence explaining why two heads cannot share a group."""

    changed_path_count: int
    changed_paths: list[str]
    changed_paths_truncated: bool
    left_head: str
    left_ref: str
    path_redactions: int
    reasons: list[Literal["changed-path", "shared-infrastructure"]]
    right_head: str
    right_ref: str
    shared_infrastructure_path_count: int
    shared_infrastructure_paths: list[str]
    shared_infrastructure_paths_truncated: bool


class MergePlanCounts(TypedDict):
    """Accounting for an exhaustive current-head plan."""

    ancestor_branches: int
    ancestor_heads: int
    candidate_branches: int
    candidate_heads: int
    changed_path_collision_pairs: int
    collision_pairs: int
    observed_branches: int
    observed_heads: int
    patch_equivalent_branches: int
    patch_equivalent_heads: int
    planned_groups: int
    shared_infrastructure_collision_pairs: int


class MergePlanBounds(TypedDict):
    """Resource ceilings for merge-queue planning."""

    branch_limit: int
    collision_limit: int
    collision_path_limit: int
    commit_scan_limit: int
    git_output_char_limit: int
    json_char_limit: int
    local_ref_scan_limit: int
    merge_queue_head_limit: int
    path_display_limit: int
    path_scan_limit: int
    rehearsal_branch_limit: int
    rehearsal_group_limit: int
    rehearsal_path_display_limit: int
    rehearsal_path_limit: int


class MergePlanTarget(TypedDict):
    """Resolved target identity copied from the exhaustive inventory."""

    head: str
    input: str
    ref: str


PlanSnapshotState = Literal[
    "already-satisfied", "planned-blocked", "planned-ready"
]
ReconciliationStatus = Literal[
    "blocked", "explicitly-retired", "merged", "pending", "stale"
]
RetirementBasis = Literal[
    "fresh-ancestor", "fresh-patch-equivalent", "operator-approval"
]
RetirementReasonCode = Literal["abandoned", "out-of-scope", "superseded"]


class PlanSnapshotSourceGroup(TypedDict):
    """Minimal exhaustive-inventory group used to seal plan accounting."""

    branch_count: int
    classification: Literal["ancestor", "patch-equivalent", "unique"]
    head: str
    lifecycle: Literal["current", "historical"]
    names: list[str]
    patch_equivalent_commits: int
    refs: list[str]
    unique_commits: int


class PlanSnapshotEntry(TypedDict):
    """One exact branch identity retained from initial planning."""

    expected_tip: str
    initial_classification: Literal["ancestor", "patch-equivalent", "unique"]
    initial_lifecycle: Literal["current", "historical"]
    plan_state: PlanSnapshotState
    ref: str


class PlanSnapshotBody(TypedDict):
    """Canonical plan state required for later no-branch-left-behind checks."""

    entries: list[PlanSnapshotEntry]
    target: MergePlanTarget
    version: int


class PlanSnapshotBasis(TypedDict):
    """Digest-sealed accounting basis embedded in a machine-readable plan."""

    algorithm: Literal["sha256"]
    body: PlanSnapshotBody
    digest: str


class MergePlanPayload(TypedDict):
    """Read-only grouping plan over exhaustive unique/current heads."""

    bounds: MergePlanBounds
    candidates: list[MergePlanHead]
    collisions: list[MergePlanCollision]
    collisions_truncated: bool
    counts: MergePlanCounts
    groups: list[MergePlanGroup]
    mode: Literal["merge-queue-plan"]
    ok: bool
    page_size: int
    pages: int
    schema_version: int
    snapshot_basis: PlanSnapshotBasis
    target: MergePlanTarget
    terminal: bool
    truncated: bool


class RetirementApprovalBody(TypedDict):
    """Exact operator assertion protected by an Ed25519 signature."""

    expires_at: str
    expected_tip: str
    issued_at: str
    key_id: str
    plan_digest: str
    reason_code: RetirementReasonCode
    ref: str
    reviewer_identity_digest: str
    version: int


class RetirementApproval(TypedDict):
    """Immutable signed operator-approval envelope."""

    algorithm: Literal["ed25519"]
    body: RetirementApprovalBody
    signature: str


class RetirementApprovalKey(TypedDict):
    """One bounded signer record from the operator-selected trust store."""

    key_id: str
    public_key: str
    reviewer_identity_digest: str
    status: Literal["active", "revoked"]


class ReconciliationRetirement(TypedDict):
    """Exact branch disappearance with derived or operator evidence."""

    approval: RetirementApproval | None
    expected_tip: str
    ref: str


class ValidatedRetirement(TypedDict):
    """Normalized proof retained in the deterministic snapshot output."""

    approval_digest: str | None
    approval_expires_at: str | None
    approval_issued_at: str | None
    approval_key_id: str | None
    basis: RetirementBasis
    reason_code: RetirementReasonCode | None


class ReconciliationSnapshotBranch(TypedDict):
    """Fresh disposition of one branch from the sealed plan basis."""

    approval_digest: str | None
    approval_expires_at: str | None
    approval_issued_at: str | None
    approval_key_id: str | None
    current_head: str | None
    expected_tip: str
    fresh_classification: str | None
    ref: str
    retirement_basis: RetirementBasis | None
    retirement_reason_code: RetirementReasonCode | None
    status: ReconciliationStatus


class ReconciliationSnapshotHead(TypedDict):
    """Conservative aggregate disposition for one deduplicated planned head."""

    expected_tip: str
    refs: list[str]
    status: ReconciliationStatus


class ReconciliationSnapshotCounts(TypedDict):
    """Complete status accounting across branches and deduplicated heads."""

    blocked: int
    explicitly_retired: int
    merged: int
    pending: int
    stale: int
    total_branches: int
    total_heads: int


class ReconciliationSnapshotBounds(TypedDict):
    """Hard input and output ceilings for snapshot reconciliation."""

    approval_key_limit: int
    approval_keyring_json_char_limit: int
    approval_signature_limit: int
    entry_limit: int
    json_char_limit: int
    retirement_limit: int


class ReconciliationSnapshotPayload(TypedDict):
    """Digest-bound, deterministic reconciliation status without Git mutation."""

    approval_keyring_digest: str | None
    bounds: ReconciliationSnapshotBounds
    branches: list[ReconciliationSnapshotBranch]
    complete: bool
    counts: ReconciliationSnapshotCounts
    freshness_digest: str
    heads: list[ReconciliationSnapshotHead]
    mode: Literal["reconciliation-snapshot"]
    ok: bool
    plan_digest: str
    schema_version: int
    snapshot_digest: str
    target: MergePlanTarget
    verification_time: str | None


RemoteClassification = Literal[
    "deleted-upstream", "diverged", "equal", "local-only", "remote-only"
]
RemoteRecommendation = Literal["delete-candidate", "retain", "review"]


class RemoteTrackingBranch(TypedDict):
    """One local/remote branch-name comparison and non-mutating recommendation."""

    classification: RemoteClassification
    local_head: str | None
    local_ref: str | None
    name: str
    recommendation: RemoteRecommendation
    remote_head: str | None
    remote_ref: str | None


class RemoteTrackingPayload(TypedDict):
    """Freshness-bound inventory of one locally available remote namespace."""

    bounds: dict[str, int]
    branches: list[RemoteTrackingBranch]
    counts: dict[str, int]
    fetch_completed_at: str
    freshness_digest: str
    freshness_expires_at: str
    mode: Literal["remote-tracking-reconciliation"]
    ok: bool
    remote: str
    schema_version: int
    verification_time: str


DisplayPathsFn = Callable[[Sequence[str], int], tuple[list[str], bool, int]]
ErrorFactory = Callable[[str], Exception]
ObjectValidator = Callable[[str], bool]
RefValidator = Callable[[object], str]
PredictionRunFn = Callable[
    [Sequence[str], str | None], subprocess.CompletedProcess[str]
]
ApprovalSignatureVerifier = Callable[[str, str, str], bool]
APPROVAL_KEY_LIMIT = 128
APPROVAL_KEYRING_JSON_CHAR_LIMIT = 65_536
APPROVAL_SIGNATURE_LIMIT = 256
SNAPSHOT_ENTRY_LIMIT = 10_000
SNAPSHOT_JSON_CHAR_LIMIT = 16_777_216
REMOTE_REF_SCAN_LIMIT = 10_000
REMOTE_GIT_OUTPUT_CHAR_LIMIT = 2_097_152
REMOTE_JSON_CHAR_LIMIT = 4_194_304
_RETIREMENT_REASON_CODES = frozenset({"abandoned", "out-of-scope", "superseded"})
_RETIREMENT_APPROVAL_DOMAIN = "gludd.reconciliation-retirement-approval/v1\0"
_SHARED_INFRASTRUCTURE_EXACT = frozenset(
    (".claude/settings.json", "AGENTS.md", "Makefile", "opencode.json")
)


def _snapshot_error() -> ValueError:
    """Return one content-free validation failure for untrusted snapshot data."""
    return ValueError("invalid reconciliation snapshot")


def _snapshot_dict(
    value: object, keys: frozenset[str] | None = None
) -> dict[str, object]:
    """Return a string-key dictionary, optionally requiring its exact schema."""
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise _snapshot_error()
    result = cast(dict[str, object], value)
    if keys is not None and set(result) != keys:
        raise _snapshot_error()
    return result


def _snapshot_integer(value: object, *, minimum: int, maximum: int) -> int:
    """Return one bounded integer without accepting bool as an integer."""
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        raise _snapshot_error()
    return value


def _canonical_document(value: object, char_limit: int) -> str:
    """Serialize one bounded document with the project's canonical JSON rules."""
    try:
        canonical = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise _snapshot_error() from exc
    if len(canonical) > char_limit:
        raise _snapshot_error()
    return canonical


def canonical_document_digest(
    value: object, *, char_limit: int = SNAPSHOT_JSON_CHAR_LIMIT
) -> str:
    """Return the canonical SHA-256 digest used by snapshot handoffs."""
    canonical = _canonical_document(value, char_limit)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _snapshot_digest(value: object) -> str:
    """Validate one lowercase SHA-256 value without echoing untrusted text."""
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _snapshot_error()
    return value


def _snapshot_object_id(value: object, valid_object_id: ObjectValidator) -> str:
    """Return one object ID accepted by the inventory's canonical validator."""
    if not isinstance(value, str) or not valid_object_id(value):
        raise _snapshot_error()
    return value


_REMOTE_REF_FORMAT = "%(refname)%00%(objectname)%00%(upstream)%00%(symref)"


def _remote_name(value: object) -> str:
    """Return one bounded unambiguous Git remote name."""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 64
        or not value[0].isascii()
        or not value[0].isalnum()
        or value.endswith((".", ".lock"))
        or ".." in value
        or any(
            not character.isascii()
            or (not character.isalnum() and character not in "._-")
            for character in value
        )
    ):
        raise _snapshot_error()
    return value


def _remote_ref_identity(
    value: object,
    *,
    valid_ref: RefValidator,
    expected_remote: str | None = None,
) -> tuple[str, str]:
    """Validate one full remote-tracking ref and return remote/name identity."""
    if not isinstance(value, str) or not value.startswith("refs/remotes/"):
        raise _snapshot_error()
    remainder = value.removeprefix("refs/remotes/")
    remote, separator, name = remainder.partition("/")
    if not separator or _remote_name(remote) != remote or name == "HEAD":
        raise _snapshot_error()
    if expected_remote is not None and remote != expected_remote:
        raise _snapshot_error()
    valid_ref(f"refs/heads/{name}")
    return remote, name


def _remote_git_output(
    argv: Sequence[str],
    *,
    run: PredictionRunFn,
    cwd: str | None,
    char_limit: int,
) -> str:
    """Run one bounded read-only Git ref scan without exposing failure detail."""
    try:
        result = run(argv, cwd)
    except Exception as exc:
        raise _snapshot_error() from exc
    if (
        result.returncode != 0
        or not isinstance(result.stdout, str)
        or not isinstance(result.stderr, str)
        or result.stderr
        or len(result.stdout) > char_limit
        or len(result.stderr) > char_limit
    ):
        raise _snapshot_error()
    return result.stdout


def _remote_rows(
    output: str,
    *,
    namespace: Literal["local", "remote"],
    remote: str,
    valid_ref: RefValidator,
    valid_object_id: ObjectValidator,
    ref_limit: int,
) -> dict[str, tuple[str, str, str | None]]:
    """Parse one sorted NUL-delimited `for-each-ref` snapshot."""
    if output and not output.endswith("\n"):
        raise _snapshot_error()
    lines = output.splitlines()
    if len(lines) > ref_limit:
        raise _snapshot_error()
    rows: dict[str, tuple[str, str, str | None]] = {}
    previous_ref = ""
    for line in lines:
        fields = line.split("\0")
        if len(fields) != 4:
            raise _snapshot_error()
        ref, raw_head, upstream, symref = fields
        if not ref or ref <= previous_ref or symref:
            raise _snapshot_error()
        head = _snapshot_object_id(raw_head, valid_object_id)
        if namespace == "remote":
            if upstream:
                raise _snapshot_error()
            _, name = _remote_ref_identity(
                ref, valid_ref=valid_ref, expected_remote=remote
            )
            normalized_upstream = None
        else:
            local_ref = valid_ref(ref)
            name = local_ref.removeprefix("refs/heads/")
            normalized_upstream = upstream or None
            if upstream:
                upstream_remote, upstream_name = _remote_ref_identity(
                    upstream, valid_ref=valid_ref
                )
                if upstream_remote == remote and upstream_name != name:
                    raise _snapshot_error()
        if name in rows:
            raise _snapshot_error()
        rows[name] = (ref, head, normalized_upstream)
        previous_ref = ref
    return rows


def _remote_freshness(
    value: object,
    *,
    remote: str,
    observed: dict[str, tuple[str, str, str | None]],
    verification_time: object,
    valid_ref: RefValidator,
    valid_object_id: ObjectValidator,
    ref_limit: int,
    json_char_limit: int,
) -> tuple[str, str, str]:
    """Verify one digest-sealed, expiring fetch snapshot against local refs."""
    envelope = _snapshot_dict(value, frozenset({"algorithm", "body", "digest"}))
    body = _snapshot_dict(
        envelope["body"],
        frozenset({"expires_at", "fetched_at", "refs", "remote", "version"}),
    )
    refs_value = body["refs"]
    if not isinstance(refs_value, list) or len(refs_value) > ref_limit:
        raise _snapshot_error()
    normalized_refs: list[dict[str, str]] = []
    previous_ref = ""
    for raw_entry in refs_value:
        entry = _snapshot_dict(raw_entry, frozenset({"head", "ref"}))
        ref_value = entry["ref"]
        _, name = _remote_ref_identity(
            ref_value, valid_ref=valid_ref, expected_remote=remote
        )
        ref = cast(str, ref_value)
        head = _snapshot_object_id(entry["head"], valid_object_id)
        if ref <= previous_ref or observed.get(name) != (ref, head, None):
            raise _snapshot_error()
        normalized_refs.append({"head": head, "ref": ref})
        previous_ref = ref
    if len(normalized_refs) != len(observed):
        raise _snapshot_error()
    fetched_at = _retirement_timestamp(body["fetched_at"])
    expires_at = _retirement_timestamp(body["expires_at"])
    checked_at = _retirement_timestamp(verification_time)
    normalized = {
        "expires_at": expires_at,
        "fetched_at": fetched_at,
        "refs": normalized_refs,
        "remote": remote,
        "version": _snapshot_integer(body["version"], minimum=1, maximum=1),
    }
    digest = _snapshot_digest(envelope["digest"])
    if (
        envelope["algorithm"] != "sha256"
        or body["remote"] != remote
        or not hmac.compare_digest(
            digest,
            canonical_document_digest(normalized, char_limit=json_char_limit),
        )
        or fetched_at > checked_at
        or checked_at >= expires_at
        or fetched_at >= expires_at
    ):
        raise _snapshot_error()
    return digest, fetched_at, expires_at


def build_remote_tracking_inventory(
    remote_value: object,
    freshness_evidence: object,
    verification_time: object,
    *,
    run: PredictionRunFn,
    cwd: str | None,
    valid_ref: RefValidator,
    valid_object_id: ObjectValidator,
    ref_limit: int = REMOTE_REF_SCAN_LIMIT,
    git_output_char_limit: int = REMOTE_GIT_OUTPUT_CHAR_LIMIT,
    json_char_limit: int = REMOTE_JSON_CHAR_LIMIT,
) -> RemoteTrackingPayload:
    """Compare local and locally available remote refs without network or mutation."""
    remote = _remote_name(remote_value)
    argv_prefix = [
        "git",
        "for-each-ref",
        "--sort=refname",
        f"--count={ref_limit + 1}",
        f"--format={_REMOTE_REF_FORMAT}",
    ]
    local_output = _remote_git_output(
        [*argv_prefix, "refs/heads/"],
        run=run,
        cwd=cwd,
        char_limit=git_output_char_limit,
    )
    remote_output = _remote_git_output(
        [*argv_prefix, f"refs/remotes/{remote}/"],
        run=run,
        cwd=cwd,
        char_limit=git_output_char_limit,
    )
    local = _remote_rows(
        local_output,
        namespace="local",
        remote=remote,
        valid_ref=valid_ref,
        valid_object_id=valid_object_id,
        ref_limit=ref_limit,
    )
    tracked = _remote_rows(
        remote_output,
        namespace="remote",
        remote=remote,
        valid_ref=valid_ref,
        valid_object_id=valid_object_id,
        ref_limit=ref_limit,
    )
    freshness_digest, fetched_at, expires_at = _remote_freshness(
        freshness_evidence,
        remote=remote,
        observed=tracked,
        verification_time=verification_time,
        valid_ref=valid_ref,
        valid_object_id=valid_object_id,
        ref_limit=ref_limit,
        json_char_limit=json_char_limit,
    )
    branches: list[RemoteTrackingBranch] = []
    for name in sorted(set(local) | set(tracked)):
        local_row = local.get(name)
        remote_row = tracked.get(name)
        if local_row is None:
            classification: RemoteClassification = "remote-only"
            recommendation: RemoteRecommendation = "review"
        elif remote_row is None:
            expected_upstream = f"refs/remotes/{remote}/{name}"
            classification = (
                "deleted-upstream"
                if local_row[2] == expected_upstream
                else "local-only"
            )
            recommendation = (
                "delete-candidate"
                if classification == "deleted-upstream"
                else "retain"
            )
        elif local_row[1] == remote_row[1]:
            classification, recommendation = "equal", "retain"
        else:
            classification, recommendation = "diverged", "review"
        branches.append(
            {
                "classification": classification,
                "local_head": local_row[1] if local_row else None,
                "local_ref": local_row[0] if local_row else None,
                "name": name,
                "recommendation": recommendation,
                "remote_head": remote_row[1] if remote_row else None,
                "remote_ref": remote_row[0] if remote_row else None,
            }
        )
    classes = (
        "deleted-upstream",
        "diverged",
        "equal",
        "local-only",
        "remote-only",
    )
    counts = {
        **{
            classification: sum(
                branch["classification"] == classification for branch in branches
            )
            for classification in classes
        },
        "total": len(branches),
    }
    payload: RemoteTrackingPayload = {
        "bounds": {
            "git_output_char_limit": git_output_char_limit,
            "json_char_limit": json_char_limit,
            "local_ref_limit": ref_limit,
            "remote_ref_limit": ref_limit,
        },
        "branches": branches,
        "counts": counts,
        "fetch_completed_at": fetched_at,
        "freshness_digest": freshness_digest,
        "freshness_expires_at": expires_at,
        "mode": "remote-tracking-reconciliation",
        "ok": True,
        "remote": remote,
        "schema_version": 2,
        "verification_time": cast(str, verification_time),
    }
    _canonical_document(payload, json_char_limit)
    return payload


def _snapshot_target(
    value: object, *, valid_ref: RefValidator, valid_object_id: ObjectValidator
) -> MergePlanTarget:
    """Validate the exact symbolic target identity shared by both documents."""
    target = _snapshot_dict(value, frozenset({"head", "input", "ref"}))
    target_input = target["input"]
    if (
        not isinstance(target_input, str)
        or not target_input
        or len(target_input) > 1_024
    ):
        raise _snapshot_error()
    try:
        target_ref = valid_ref(target["ref"])
    except Exception as exc:
        raise _snapshot_error() from exc
    return {
        "head": _snapshot_object_id(target["head"], valid_object_id),
        "input": target_input,
        "ref": target_ref,
    }


def build_plan_snapshot_basis(
    summary_groups: Sequence[PlanSnapshotSourceGroup],
    target: MergePlanTarget,
    plan_groups: Sequence[MergePlanGroup],
    *,
    entry_limit: int = SNAPSHOT_ENTRY_LIMIT,
    json_char_limit: int = SNAPSHOT_JSON_CHAR_LIMIT,
) -> PlanSnapshotBasis:
    """Seal every initially observed branch into the additive planning payload."""
    planned_states: dict[str, PlanSnapshotState] = {}
    for plan_group in plan_groups:
        state: PlanSnapshotState = (
            "planned-ready"
            if plan_group["rehearsal"]["conflict_prediction"]["status"]
            == "predicted-clean"
            else "planned-blocked"
        )
        for entry in plan_group["entries"]:
            head = entry["expected_tip"]
            if head in planned_states and planned_states[head] != state:
                raise _snapshot_error()
            planned_states[head] = state

    entries: list[PlanSnapshotEntry] = []
    seen_refs: set[str] = set()
    for summary_group in summary_groups:
        refs = summary_group["refs"]
        if (
            summary_group["branch_count"] != len(refs)
            or refs != sorted(set(refs))
            or any(ref in seen_refs for ref in refs)
        ):
            raise _snapshot_error()
        seen_refs.update(refs)
        current = (
            summary_group["classification"] == "unique"
            and summary_group["lifecycle"] == "current"
        )
        if current and summary_group["head"] not in planned_states:
            raise _snapshot_error()
        state = (
            planned_states[summary_group["head"]]
            if current
            else "already-satisfied"
        )
        entries.extend(
            {
                "expected_tip": summary_group["head"],
                "initial_classification": summary_group["classification"],
                "initial_lifecycle": summary_group["lifecycle"],
                "plan_state": state,
                "ref": ref,
            }
            for ref in refs
        )
    entries.sort(key=lambda entry: entry["ref"])
    if len(entries) > entry_limit:
        raise _snapshot_error()
    body: PlanSnapshotBody = {
        "entries": entries,
        "target": {
            "head": target["head"],
            "input": target["input"],
            "ref": target["ref"],
        },
        "version": 1,
    }
    return {
        "algorithm": "sha256",
        "body": body,
        "digest": canonical_document_digest(body, char_limit=json_char_limit),
    }


def _parse_plan_snapshot_basis(
    plan_value: object,
    plan_digest_value: object,
    *,
    valid_ref: RefValidator,
    valid_object_id: ObjectValidator,
    entry_limit: int,
    json_char_limit: int,
) -> PlanSnapshotBody:
    """Validate the sealed branch accounting embedded in a prior plan."""
    plan = _snapshot_dict(plan_value)
    if (
        plan.get("mode") != "merge-queue-plan"
        or plan.get("schema_version") != 2
        or plan.get("ok") is not True
        or plan.get("terminal") is not True
        or plan.get("truncated") is not False
    ):
        raise _snapshot_error()
    basis = _snapshot_dict(
        plan.get("snapshot_basis"), frozenset({"algorithm", "body", "digest"})
    )
    digest = _snapshot_digest(basis["digest"])
    plan_digest = _snapshot_digest(plan_digest_value)
    if basis["algorithm"] != "sha256" or not hmac.compare_digest(digest, plan_digest):
        raise _snapshot_error()
    body_data = _snapshot_dict(
        basis["body"], frozenset({"entries", "target", "version"})
    )
    if body_data["version"] != 1:
        raise _snapshot_error()
    target = _snapshot_target(
        body_data["target"], valid_ref=valid_ref, valid_object_id=valid_object_id
    )
    plan_target = _snapshot_target(
        plan.get("target"), valid_ref=valid_ref, valid_object_id=valid_object_id
    )
    if plan_target != target:
        raise _snapshot_error()
    raw_entries = body_data["entries"]
    if not isinstance(raw_entries, list) or len(raw_entries) > entry_limit:
        raise _snapshot_error()
    entries: list[PlanSnapshotEntry] = []
    seen_refs: set[str] = set()
    for raw_entry in raw_entries:
        entry = _snapshot_dict(
            raw_entry,
            frozenset(
                {
                    "expected_tip",
                    "initial_classification",
                    "initial_lifecycle",
                    "plan_state",
                    "ref",
                }
            ),
        )
        try:
            ref = valid_ref(entry["ref"])
        except Exception as exc:
            raise _snapshot_error() from exc
        classification = entry["initial_classification"]
        lifecycle = entry["initial_lifecycle"]
        plan_state = entry["plan_state"]
        if (
            ref in seen_refs
            or classification not in {"ancestor", "patch-equivalent", "unique"}
            or lifecycle not in {"current", "historical"}
            or plan_state not in {
                "already-satisfied",
                "planned-blocked",
                "planned-ready",
            }
            or (classification == "unique") != (lifecycle == "current")
            or (classification == "unique") == (plan_state == "already-satisfied")
        ):
            raise _snapshot_error()
        seen_refs.add(ref)
        entries.append(
            {
                "expected_tip": _snapshot_object_id(
                    entry["expected_tip"], valid_object_id
                ),
                "initial_classification": classification,
                "initial_lifecycle": lifecycle,
                "plan_state": plan_state,
                "ref": ref,
            }
        )
    if entries != sorted(entries, key=lambda entry: entry["ref"]):
        raise _snapshot_error()
    body: PlanSnapshotBody = {"entries": entries, "target": target, "version": 1}
    expected_digest = canonical_document_digest(body, char_limit=json_char_limit)
    if not hmac.compare_digest(digest, expected_digest):
        raise _snapshot_error()
    return body


def _parse_fresh_inventory(
    value: object,
    freshness_digest_value: object,
    plan_target: MergePlanTarget,
    *,
    valid_ref: RefValidator,
    valid_object_id: ObjectValidator,
    entry_limit: int,
    json_char_limit: int,
) -> tuple[MergePlanTarget, dict[str, tuple[str, str]]]:
    """Validate a complete fresh inventory and return exact ref observations."""
    freshness_digest = _snapshot_digest(freshness_digest_value)
    actual_digest = canonical_document_digest(value, char_limit=json_char_limit)
    if not hmac.compare_digest(freshness_digest, actual_digest):
        raise _snapshot_error()
    inventory = _snapshot_dict(
        value,
        frozenset(
            {
                "bounds",
                "counts",
                "groups",
                "mode",
                "ok",
                "page_size",
                "pages",
                "schema_version",
                "target",
                "terminal",
                "truncated",
            }
        ),
    )
    if (
        inventory["mode"] != "exhaustive-summary"
        or inventory["schema_version"] != 2
        or inventory["ok"] is not True
        or inventory["terminal"] is not True
        or inventory["truncated"] is not False
    ):
        raise _snapshot_error()
    target = _snapshot_target(
        inventory["target"], valid_ref=valid_ref, valid_object_id=valid_object_id
    )
    if (target["input"], target["ref"]) != (
        plan_target["input"],
        plan_target["ref"],
    ):
        raise _snapshot_error()
    groups = inventory["groups"]
    if not isinstance(groups, list) or len(groups) > entry_limit:
        raise _snapshot_error()
    current: dict[str, tuple[str, str]] = {}
    class_counts = {"ancestor": 0, "patch-equivalent": 0, "unique": 0}
    lifecycle_counts = {"current": 0, "historical": 0}
    for raw_group in groups:
        group = _snapshot_dict(
            raw_group,
            frozenset(
                {
                    "branch_count",
                    "classification",
                    "head",
                    "lifecycle",
                    "names",
                    "patch_equivalent_commits",
                    "refs",
                    "unique_commits",
                }
            ),
        )
        classification = group["classification"]
        lifecycle = group["lifecycle"]
        if (
            classification not in class_counts
            or lifecycle not in lifecycle_counts
            or (classification == "unique") != (lifecycle == "current")
            or not isinstance(group["refs"], list)
            or not isinstance(group["names"], list)
        ):
            raise _snapshot_error()
        refs: list[str] = []
        try:
            refs = [valid_ref(ref) for ref in group["refs"]]
        except Exception as exc:
            raise _snapshot_error() from exc
        branch_count = _snapshot_integer(
            group["branch_count"], minimum=1, maximum=entry_limit
        )
        if (
            refs != sorted(set(refs))
            or branch_count != len(refs)
            or len(group["names"]) != len(refs)
            or len(current) + len(refs) > entry_limit
            or any(ref in current for ref in refs)
        ):
            raise _snapshot_error()
        head = _snapshot_object_id(group["head"], valid_object_id)
        for ref in refs:
            current[ref] = (head, classification)
        class_counts[classification] += len(refs)
        lifecycle_counts[lifecycle] += len(refs)
    counts = _snapshot_dict(inventory["counts"])
    expected_counts = {
        "ancestor": class_counts["ancestor"],
        "current": lifecycle_counts["current"],
        "deduplicated_heads": len(groups),
        "historical": lifecycle_counts["historical"],
        "patch_equivalent": class_counts["patch-equivalent"],
        "returned": len(current),
        "unique": class_counts["unique"],
    }
    if counts != expected_counts:
        raise _snapshot_error()
    return target, current


def _retirement_timestamp(value: object) -> str:
    """Validate one second-precision canonical RFC 3339 UTC timestamp."""
    if not isinstance(value, str) or len(value) != 20:
        raise _snapshot_error()
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise _snapshot_error() from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise _snapshot_error()
    return value


def _approval_key_id(value: object) -> str:
    """Validate one bounded opaque signer identifier."""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 64
        or not value[0].isascii()
        or not value[0].isalnum()
        or any(
            not character.isascii()
            or (not character.isalnum() and character not in "._-")
            for character in value
        )
    ):
        raise _snapshot_error()
    return value


def _parse_approval_trust_store(
    value: object,
) -> tuple[dict[str, RetirementApprovalKey], str]:
    """Validate the operator-selected bounded Ed25519 trust and revocation set."""
    store = _snapshot_dict(value, frozenset({"keys", "schema_version"}))
    keys_value = store["keys"]
    if (
        store["schema_version"] != 1
        or not isinstance(keys_value, list)
        or not 1 <= len(keys_value) <= APPROVAL_KEY_LIMIT
    ):
        raise _snapshot_error()
    keys: dict[str, RetirementApprovalKey] = {}
    order: list[str] = []
    for raw_key in keys_value:
        key_data = _snapshot_dict(
            raw_key,
            frozenset(
                {"key_id", "public_key", "reviewer_identity_digest", "status"}
            ),
        )
        key_id = _approval_key_id(key_data["key_id"])
        status_value = key_data["status"]
        if key_id in keys or status_value not in {"active", "revoked"}:
            raise _snapshot_error()
        key: RetirementApprovalKey = {
            "key_id": key_id,
            "public_key": _snapshot_digest(key_data["public_key"]),
            "reviewer_identity_digest": _snapshot_digest(
                key_data["reviewer_identity_digest"]
            ),
            "status": status_value,
        }
        keys[key_id] = key
        order.append(key_id)
    if order != sorted(order):
        raise _snapshot_error()
    digest = canonical_document_digest(
        {"keys": [keys[key_id] for key_id in order], "schema_version": 1},
        char_limit=APPROVAL_KEYRING_JSON_CHAR_LIMIT,
    )
    return keys, digest


def _approval_signature(value: object) -> str:
    """Validate one canonical lowercase Ed25519 signature."""
    if (
        not isinstance(value, str)
        or len(value) != 128
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _snapshot_error()
    return value


def _parse_retirement_approval(
    value: object,
    *,
    plan_digest: str,
    ref: str,
    expected_tip: str,
    keyring: dict[str, RetirementApprovalKey],
    verification_time: str,
    verify_signature: ApprovalSignatureVerifier,
    valid_ref: RefValidator,
    valid_object_id: ObjectValidator,
    json_char_limit: int,
) -> ValidatedRetirement:
    """Verify an immutable approval bound to one exact planned identity."""
    approval = _snapshot_dict(value, frozenset({"algorithm", "body", "signature"}))
    body_data = _snapshot_dict(
        approval["body"],
        frozenset(
            {
                "expected_tip",
                "expires_at",
                "issued_at",
                "key_id",
                "plan_digest",
                "reason_code",
                "ref",
                "reviewer_identity_digest",
                "version",
            }
        ),
    )
    try:
        approval_ref = valid_ref(body_data["ref"])
    except Exception as exc:
        raise _snapshot_error() from exc
    reason_value = body_data["reason_code"]
    if not isinstance(reason_value, str) or reason_value not in _RETIREMENT_REASON_CODES:
        raise _snapshot_error()
    reason_code = cast(RetirementReasonCode, reason_value)
    issued_at = _retirement_timestamp(body_data["issued_at"])
    expires_at = _retirement_timestamp(body_data["expires_at"])
    key_id = _approval_key_id(body_data["key_id"])
    body: RetirementApprovalBody = {
        "expires_at": expires_at,
        "expected_tip": _snapshot_object_id(
            body_data["expected_tip"], valid_object_id
        ),
        "issued_at": issued_at,
        "key_id": key_id,
        "plan_digest": _snapshot_digest(body_data["plan_digest"]),
        "reason_code": reason_code,
        "ref": approval_ref,
        "reviewer_identity_digest": _snapshot_digest(
            body_data["reviewer_identity_digest"]
        ),
        "version": _snapshot_integer(body_data["version"], minimum=2, maximum=2),
    }
    signature = _approval_signature(approval["signature"])
    key = keyring.get(key_id)
    if (
        approval["algorithm"] != "ed25519"
        or key is None
        or key["status"] != "active"
        or key["reviewer_identity_digest"] != body["reviewer_identity_digest"]
        or body["plan_digest"] != plan_digest
        or body["ref"] != ref
        or body["expected_tip"] != expected_tip
        or issued_at > verification_time
        or verification_time >= expires_at
        or issued_at >= expires_at
    ):
        raise _snapshot_error()
    message = _RETIREMENT_APPROVAL_DOMAIN + _canonical_document(body, json_char_limit)
    try:
        verified = verify_signature(message, signature, key["public_key"])
    except Exception as exc:
        raise _snapshot_error() from exc
    if not verified:
        raise _snapshot_error()
    normalized = {"algorithm": "ed25519", "body": body, "signature": signature}
    return {
        "approval_digest": canonical_document_digest(
            normalized, char_limit=json_char_limit
        ),
        "approval_expires_at": expires_at,
        "approval_issued_at": issued_at,
        "approval_key_id": key_id,
        "basis": "operator-approval",
        "reason_code": reason_code,
    }


def _parse_retirements(
    value: object,
    plan_entries: dict[str, PlanSnapshotEntry],
    current: dict[str, tuple[str, str]],
    *,
    approval_trust: object | None,
    verification_time: object | None,
    verify_signature: ApprovalSignatureVerifier,
    plan_digest: str,
    valid_ref: RefValidator,
    valid_object_id: ObjectValidator,
    retirement_limit: int,
    json_char_limit: int,
) -> tuple[dict[str, ValidatedRetirement], str | None, str | None]:
    """Require digest-verified fresh evidence or immutable operator approval."""
    if not isinstance(value, list) or len(value) > retirement_limit:
        raise _snapshot_error()
    retired: dict[str, ValidatedRetirement] = {}
    approval_digests: set[str] = set()
    keyring: dict[str, RetirementApprovalKey] | None = None
    keyring_digest: str | None = None
    checked_at: str | None = None
    for raw_entry in value:
        entry = _snapshot_dict(
            raw_entry, frozenset({"approval", "expected_tip", "ref"})
        )
        try:
            ref = valid_ref(entry["ref"])
        except Exception as exc:
            raise _snapshot_error() from exc
        expected_tip = _snapshot_object_id(entry["expected_tip"], valid_object_id)
        if (
            ref in retired
            or ref not in plan_entries
            or ref in current
            or plan_entries[ref]["expected_tip"] != expected_tip
        ):
            raise _snapshot_error()
        classifications = {
            classification
            for head, classification in current.values()
            if head == expected_tip
        }
        if entry["approval"] is None:
            if "ancestor" in classifications:
                retirement: ValidatedRetirement = {
                    "approval_digest": None,
                    "approval_expires_at": None,
                    "approval_issued_at": None,
                    "approval_key_id": None,
                    "basis": "fresh-ancestor",
                    "reason_code": None,
                }
            elif "patch-equivalent" in classifications:
                retirement = {
                    "approval_digest": None,
                    "approval_expires_at": None,
                    "approval_issued_at": None,
                    "approval_key_id": None,
                    "basis": "fresh-patch-equivalent",
                    "reason_code": None,
                }
            else:
                raise _snapshot_error()
        else:
            if len(approval_digests) >= APPROVAL_SIGNATURE_LIMIT:
                raise _snapshot_error()
            if keyring is None:
                if approval_trust is None or verification_time is None:
                    raise _snapshot_error()
                keyring, keyring_digest = _parse_approval_trust_store(approval_trust)
                checked_at = _retirement_timestamp(verification_time)
            retirement = _parse_retirement_approval(
                entry["approval"],
                plan_digest=plan_digest,
                ref=ref,
                expected_tip=expected_tip,
                keyring=keyring,
                verification_time=cast(str, checked_at),
                verify_signature=verify_signature,
                valid_ref=valid_ref,
                valid_object_id=valid_object_id,
                json_char_limit=json_char_limit,
            )
            approval_digest = cast(str, retirement["approval_digest"])
            if approval_digest in approval_digests:
                raise _snapshot_error()
            approval_digests.add(approval_digest)
        retired[ref] = retirement
    if not approval_digests and (
        approval_trust is not None or verification_time is not None
    ):
        raise _snapshot_error()
    return retired, keyring_digest, checked_at


def build_reconciliation_snapshot(
    request_value: object,
    *,
    valid_ref: RefValidator,
    valid_object_id: ObjectValidator,
    verify_signature: ApprovalSignatureVerifier,
    approval_trust: object | None = None,
    verification_time: object | None = None,
    entry_limit: int = SNAPSHOT_ENTRY_LIMIT,
    json_char_limit: int = SNAPSHOT_JSON_CHAR_LIMIT,
) -> ReconciliationSnapshotPayload:
    """Classify every sealed branch/head against one exact fresh inventory."""
    request = _snapshot_dict(
        request_value,
        frozenset(
            {
                "fresh_inventory",
                "freshness_digest",
                "plan",
                "plan_digest",
                "retired",
            }
        ),
    )
    body = _parse_plan_snapshot_basis(
        request["plan"],
        request["plan_digest"],
        valid_ref=valid_ref,
        valid_object_id=valid_object_id,
        entry_limit=entry_limit,
        json_char_limit=json_char_limit,
    )
    target, current = _parse_fresh_inventory(
        request["fresh_inventory"],
        request["freshness_digest"],
        body["target"],
        valid_ref=valid_ref,
        valid_object_id=valid_object_id,
        entry_limit=entry_limit,
        json_char_limit=json_char_limit,
    )
    plan_entries = {entry["ref"]: entry for entry in body["entries"]}
    if set(current) - set(plan_entries):
        raise _snapshot_error()
    plan_digest = _snapshot_digest(request["plan_digest"])
    retired, approval_keyring_digest, checked_at = _parse_retirements(
        request["retired"],
        plan_entries,
        current,
        approval_trust=approval_trust,
        verification_time=verification_time,
        verify_signature=verify_signature,
        plan_digest=plan_digest,
        valid_ref=valid_ref,
        valid_object_id=valid_object_id,
        retirement_limit=entry_limit,
        json_char_limit=json_char_limit,
    )
    if set(plan_entries) - set(current) - set(retired):
        raise _snapshot_error()

    branches: list[ReconciliationSnapshotBranch] = []
    for ref, entry in sorted(plan_entries.items()):
        observation = current.get(ref)
        if observation is None:
            status: ReconciliationStatus = "explicitly-retired"
            current_head = None
            fresh_classification = None
            retirement = retired[ref]
            approval_digest = retirement["approval_digest"]
            approval_expires_at = retirement["approval_expires_at"]
            approval_issued_at = retirement["approval_issued_at"]
            approval_key_id = retirement["approval_key_id"]
            retirement_basis: RetirementBasis | None = retirement["basis"]
            retirement_reason_code = retirement["reason_code"]
        else:
            current_head, fresh_classification = observation
            approval_digest = None
            approval_expires_at = None
            approval_issued_at = None
            approval_key_id = None
            retirement_basis = None
            retirement_reason_code = None
            if current_head != entry["expected_tip"]:
                status = "stale"
            elif fresh_classification in {"ancestor", "patch-equivalent"}:
                status = "merged"
            elif entry["plan_state"] == "planned-ready":
                status = "pending"
            else:
                status = "blocked"
        branches.append(
            {
                "approval_digest": approval_digest,
                "approval_expires_at": approval_expires_at,
                "approval_issued_at": approval_issued_at,
                "approval_key_id": approval_key_id,
                "current_head": current_head,
                "expected_tip": entry["expected_tip"],
                "fresh_classification": fresh_classification,
                "ref": ref,
                "retirement_basis": retirement_basis,
                "retirement_reason_code": retirement_reason_code,
                "status": status,
            }
        )

    priority = {
        "explicitly-retired": 0,
        "merged": 1,
        "pending": 2,
        "stale": 3,
        "blocked": 4,
    }
    branches_by_head: dict[str, list[ReconciliationSnapshotBranch]] = {}
    for branch in branches:
        branches_by_head.setdefault(branch["expected_tip"], []).append(branch)
    heads: list[ReconciliationSnapshotHead] = []
    for expected_tip, aliases in sorted(branches_by_head.items()):
        head_status = max(
            (alias["status"] for alias in aliases), key=priority.__getitem__
        )
        heads.append(
            {
                "expected_tip": expected_tip,
                "refs": sorted(alias["ref"] for alias in aliases),
                "status": head_status,
            }
        )
    status_counts = {
        status: sum(branch["status"] == status for branch in branches)
        for status in priority
    }
    counts: ReconciliationSnapshotCounts = {
        "blocked": status_counts["blocked"],
        "explicitly_retired": status_counts["explicitly-retired"],
        "merged": status_counts["merged"],
        "pending": status_counts["pending"],
        "stale": status_counts["stale"],
        "total_branches": len(branches),
        "total_heads": len(heads),
    }
    freshness_digest = _snapshot_digest(request["freshness_digest"])
    snapshot_body = {
        "approval_keyring_digest": approval_keyring_digest,
        "branches": branches,
        "counts": counts,
        "freshness_digest": freshness_digest,
        "heads": heads,
        "plan_digest": plan_digest,
        "target": target,
        "verification_time": checked_at,
    }
    payload: ReconciliationSnapshotPayload = {
        "approval_keyring_digest": approval_keyring_digest,
        "bounds": {
            "approval_key_limit": APPROVAL_KEY_LIMIT,
            "approval_keyring_json_char_limit": APPROVAL_KEYRING_JSON_CHAR_LIMIT,
            "approval_signature_limit": APPROVAL_SIGNATURE_LIMIT,
            "entry_limit": entry_limit,
            "json_char_limit": json_char_limit,
            "retirement_limit": entry_limit,
        },
        "branches": branches,
        "complete": not any(
            branch["status"] in {"blocked", "pending", "stale"}
            for branch in branches
        ),
        "counts": counts,
        "freshness_digest": freshness_digest,
        "heads": heads,
        "mode": "reconciliation-snapshot",
        "ok": True,
        "plan_digest": plan_digest,
        "schema_version": 2,
        "snapshot_digest": canonical_document_digest(
            snapshot_body, char_limit=json_char_limit
        ),
        "target": target,
        "verification_time": checked_at,
    }
    _canonical_document(payload, json_char_limit)
    return payload


def is_shared_infrastructure_path(path: str) -> bool:
    """Return whether one path belongs to the repository single-writer surface."""
    if path in _SHARED_INFRASTRUCTURE_EXACT:
        return True
    for prefix in ("config/", ".github/workflows/"):
        if path.startswith(prefix):
            relative = path.removeprefix(prefix)
            return "/" not in relative and relative.endswith(".yml")
    return False


def plan_collision(
    left: MergePlanHead,
    left_paths: frozenset[str],
    left_infrastructure: frozenset[str],
    right: MergePlanHead,
    right_paths: frozenset[str],
    right_infrastructure: frozenset[str],
    *,
    collision_path_limit: int,
    display_paths: DisplayPathsFn,
) -> MergePlanCollision | None:
    """Return bounded pair evidence, or None when heads are safely disjoint."""
    overlapping = sorted(left_paths & right_paths)
    infrastructure = (
        sorted(left_infrastructure | right_infrastructure)
        if left_infrastructure and right_infrastructure
        else []
    )
    if not overlapping and not infrastructure:
        return None
    reasons: list[Literal["changed-path", "shared-infrastructure"]] = []
    if overlapping:
        reasons.append("changed-path")
    if infrastructure:
        reasons.append("shared-infrastructure")
    changed_paths, changed_truncated, changed_redactions = display_paths(
        overlapping, collision_path_limit
    )
    shared_paths, shared_truncated, shared_redactions = display_paths(
        infrastructure, collision_path_limit
    )
    return {
        "changed_path_count": len(overlapping),
        "changed_paths": changed_paths,
        "changed_paths_truncated": changed_truncated,
        "left_head": left["expected_tip"],
        "left_ref": left["source_ref"],
        "path_redactions": changed_redactions + shared_redactions,
        "reasons": reasons,
        "right_head": right["expected_tip"],
        "right_ref": right["source_ref"],
        "shared_infrastructure_path_count": len(infrastructure),
        "shared_infrastructure_paths": shared_paths,
        "shared_infrastructure_paths_truncated": shared_truncated,
    }


def _prediction_bounds(
    *,
    check_limit: int,
    conflict_path_display_limit: int,
    conflict_path_scan_limit: int,
    output_char_limit: int,
    timeout_seconds: int,
) -> MergePredictionBounds:
    """Return the explicit aggregate and per-command prediction ceilings."""
    return {
        "check_limit": check_limit,
        "conflict_path_display_limit": conflict_path_display_limit,
        "conflict_path_scan_limit": conflict_path_scan_limit,
        "maximum_runtime_seconds": check_limit * timeout_seconds,
        "output_char_limit": output_char_limit,
        "timeout_seconds": timeout_seconds,
    }


def _blocked_prediction(
    reason: MergePredictionBlockedReason,
    bounds: MergePredictionBounds,
    required: int,
    attempted: int,
) -> MergeConflictPrediction:
    """Discard partial native evidence and emit one fail-closed status."""
    return {
        "attempted_check_count": attempted,
        "blocked_reason": reason,
        "bounds": bounds,
        "checks": [],
        "engine": "git-merge-tree-write-tree",
        "required_check_count": required,
        "status": "blocked",
    }


def predict_group_conflicts(
    target: str,
    sources: Sequence[str],
    *,
    run: PredictionRunFn,
    cwd: str | None,
    check_limit: int,
    conflict_path_display_limit: int,
    conflict_path_scan_limit: int,
    output_char_limit: int,
    timeout_seconds: int,
    display_paths: DisplayPathsFn,
    valid_object_id: ObjectValidator,
) -> MergeConflictPrediction:
    """Predict bounded pair conflicts with Git, without refs or worktrees."""
    pairs: list[tuple[str, str, Literal["target-source", "source-source"]]] = [
        (target, source, "target-source") for source in sources
    ]
    pairs.extend(
        (left, right, "source-source")
        for offset, left in enumerate(sources)
        for right in sources[offset + 1 :]
    )
    bounds = _prediction_bounds(
        check_limit=check_limit,
        conflict_path_display_limit=conflict_path_display_limit,
        conflict_path_scan_limit=conflict_path_scan_limit,
        output_char_limit=output_char_limit,
        timeout_seconds=timeout_seconds,
    )
    required = len(pairs)
    if required > check_limit:
        return _blocked_prediction(
            "prediction-check-bound-exceeded", bounds, required, 0
        )

    checks: list[MergePredictionCheck] = []
    for attempted, (left, right, relation) in enumerate(pairs, start=1):
        argv = [
            "git",
            "merge-tree",
            "--write-tree",
            "--name-only",
            "--no-messages",
            "-z",
            left,
            right,
        ]
        try:
            result = run(argv, cwd)
        except subprocess.TimeoutExpired:
            return _blocked_prediction(
                "runtime-bound-exceeded", bounds, required, attempted
            )
        except (OSError, UnicodeError):
            return _blocked_prediction("unsupported-git", bounds, required, attempted)
        if len(result.stdout) > output_char_limit or len(result.stderr) > output_char_limit:
            return _blocked_prediction(
                "output-bound-exceeded", bounds, required, attempted
            )
        if result.returncode == 124:
            return _blocked_prediction(
                "runtime-bound-exceeded", bounds, required, attempted
            )
        if result.returncode not in {0, 1}:
            return _blocked_prediction("unsupported-git", bounds, required, attempted)
        if result.stderr or not result.stdout.endswith("\0"):
            return _blocked_prediction("ambiguous-output", bounds, required, attempted)

        records = result.stdout.split("\0")
        result_tree = records[0]
        raw_paths = records[1:-1]
        clean = result.returncode == 0
        if (
            not valid_object_id(result_tree)
            or (clean and records != [result_tree, ""])
            or (not clean and not raw_paths)
            or len(raw_paths) > conflict_path_scan_limit
            or len(raw_paths) != len(set(raw_paths))
            or any(
                not path
                or path.startswith("/")
                or any(part in {"", ".", ".."} for part in path.split("/"))
                for path in raw_paths
            )
        ):
            return _blocked_prediction("ambiguous-output", bounds, required, attempted)
        displayed, truncated, redactions = display_paths(
            raw_paths, conflict_path_display_limit
        )
        checks.append(
            {
                "conflict_path_count": len(raw_paths),
                "conflict_paths": displayed,
                "conflict_paths_truncated": truncated,
                "left": left,
                "path_redactions": redactions,
                "relation": relation,
                "result_tree": result_tree,
                "right": right,
                "status": "clean" if clean else "conflicted",
            }
        )
    return {
        "attempted_check_count": required,
        "blocked_reason": None,
        "bounds": bounds,
        "checks": checks,
        "engine": "git-merge-tree-write-tree",
        "required_check_count": required,
        "status": (
            "conflicted"
            if any(check["status"] == "conflicted" for check in checks)
            else "predicted-clean"
        ),
    }


def block_rehearsal_prediction(rehearsal: MergeRehearsalPlan) -> None:
    """Invalidate native evidence after a terminal freshness failure."""
    prediction = rehearsal["conflict_prediction"]
    rehearsal["conflict_prediction"] = _blocked_prediction(
        "freshness-drift",
        prediction["bounds"],
        prediction["required_check_count"],
        prediction["attempted_check_count"],
    )


def _freshness_argv(ref: str) -> list[str]:
    """Return an exact commit-resolution command for one validated ref."""
    return [
        "git",
        "rev-parse",
        "--verify",
        "--quiet",
        "--end-of-options",
        f"{ref}^{{commit}}",
    ]


def merge_plan_diff_argv(target: str, head: str) -> list[str]:
    """Return the fixed Git argv for one exact branch-delta manifest."""
    return [
        "git",
        "diff",
        "--name-only",
        "--no-ext-diff",
        "--no-renames",
        "--no-textconv",
        "-z",
        f"{target}...{head}",
        "--",
    ]


def _path_digest(paths: Sequence[str]) -> str:
    """Bind one sorted NUL path manifest without expanding the output JSON."""
    manifest = "".join(f"{path}\0" for path in paths)
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def build_merge_rehearsal(
    order: int,
    indexes: Sequence[int],
    candidates: Sequence[MergePlanHead],
    raw_path_sets: Sequence[frozenset[str]],
    infrastructure_sets: Sequence[frozenset[str]],
    target: MergePlanTarget,
    *,
    branch_limit: int,
    group_limit: int,
    head_limit: int,
    path_display_limit: int,
    path_limit: int,
    display_paths: DisplayPathsFn,
    error_factory: ErrorFactory,
    prediction_run: PredictionRunFn,
    prediction_cwd: str | None,
    prediction_check_limit: int,
    prediction_conflict_path_display_limit: int,
    prediction_conflict_path_scan_limit: int,
    prediction_output_char_limit: int,
    prediction_timeout_seconds: int,
    valid_object_id: ObjectValidator,
) -> MergeRehearsalPlan:
    """Build a deterministic, unexecuted synthetic-candidate recipe."""
    if len(indexes) > head_limit:
        raise error_factory("merge rehearsal head bound exceeded")
    branch_count = sum(candidates[index]["branch_count"] for index in indexes)
    if branch_count > branch_limit:
        raise error_factory("merge rehearsal branch bound exceeded")
    group_paths = sorted(path for index in indexes for path in raw_path_sets[index])
    if len(group_paths) > path_limit:
        raise error_factory("merge rehearsal path bound exceeded")
    shared_paths = sorted(
        path for index in indexes for path in infrastructure_sets[index]
    )
    shared_display, shared_truncated, shared_redactions = display_paths(
        shared_paths, path_display_limit
    )

    pair_count = 0
    for offset, left_index in enumerate(indexes):
        for right_index in indexes[offset + 1 :]:
            pair_count += 1
            path_overlap = raw_path_sets[left_index] & raw_path_sets[right_index]
            infrastructure_overlap = (
                infrastructure_sets[left_index] and infrastructure_sets[right_index]
            )
            if path_overlap or infrastructure_overlap:
                raise error_factory(
                    "merge rehearsal shared-path conflict survived grouping"
                )

    identity = {
        "entries": [
            {
                "expected_tip": candidates[index]["expected_tip"],
                "refs": candidates[index]["refs"],
                "source_ref": candidates[index]["source_ref"],
            }
            for index in indexes
        ],
        "order": order,
        "target": {"head": target["head"], "ref": target["ref"]},
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    candidate_id = f"merge-rehearsal-sha256:{digest}"

    freshness_checks: list[MergeRehearsalFreshnessCheck] = [
        {
            "argv": _freshness_argv(target["ref"]),
            "expected_object": target["head"],
            "ref": target["ref"],
        }
    ]
    for index in indexes:
        candidate = candidates[index]
        freshness_checks.extend(
            {
                "argv": _freshness_argv(ref),
                "expected_object": candidate["expected_tip"],
                "ref": ref,
            }
            for ref in candidate["refs"]
        )
    path_checks: list[MergeRehearsalPathCheck] = [
        {
            "argv": merge_plan_diff_argv(
                target["head"], candidates[index]["expected_tip"]
            ),
            "expected_path_count": len(raw_path_sets[index]),
            "expected_path_sha256": _path_digest(sorted(raw_path_sets[index])),
            "source_ref": candidates[index]["source_ref"],
        }
        for index in indexes
    ]

    deterministic_environment = {
        "GIT_AUTHOR_DATE": "@0 +0000",
        "GIT_AUTHOR_EMAIL": "rehearsal@invalid",
        "GIT_AUTHOR_NAME": "Gludd Merge Rehearsal",
        "GIT_COMMITTER_DATE": "@0 +0000",
        "GIT_COMMITTER_EMAIL": "rehearsal@invalid",
        "GIT_COMMITTER_NAME": "Gludd Merge Rehearsal",
    }
    recipe: list[MergeRehearsalStep] = []
    candidate_input = target["head"]
    source_heads: list[str] = []
    for step_order, index in enumerate(indexes, start=1):
        candidate = candidates[index]
        source = candidate["expected_tip"]
        source_heads.append(source)
        tree_capture = f"result_tree_{step_order}"
        commit_capture = f"candidate_commit_{step_order}"
        recipe.append(
            {
                "candidate_input": candidate_input,
                "commit_tree": {
                    "argv": [
                        "git",
                        "commit-tree",
                        f"${{{tree_capture}}}",
                        "-p",
                        candidate_input,
                        "-p",
                        source,
                        "-m",
                        f"gludd merge rehearsal {candidate_id} step {step_order}",
                    ],
                    "capture": commit_capture,
                    "environment": dict(deterministic_environment),
                    "expected_exit_codes": [0],
                    "purpose": "synthetic-candidate-commit",
                },
                "expected_source": source,
                "merge_tree": {
                    "argv": [
                        "git",
                        "merge-tree",
                        "--write-tree",
                        "--name-only",
                        "--no-messages",
                        "-z",
                        candidate_input,
                        source,
                    ],
                    "capture": tree_capture,
                    "environment": {},
                    "expected_exit_codes": [0],
                    "purpose": "synthetic-candidate-tree",
                },
                "order": step_order,
                "source_ref": candidate["source_ref"],
            }
        )
        candidate_input = f"${{{commit_capture}}}"

    conflict_prediction = predict_group_conflicts(
        target["head"],
        source_heads,
        run=prediction_run,
        cwd=prediction_cwd,
        check_limit=prediction_check_limit,
        conflict_path_display_limit=prediction_conflict_path_display_limit,
        conflict_path_scan_limit=prediction_conflict_path_scan_limit,
        output_char_limit=prediction_output_char_limit,
        timeout_seconds=prediction_timeout_seconds,
        display_paths=display_paths,
        valid_object_id=valid_object_id,
    )
    final_candidate = candidate_input
    admission_commands: list[MergeRehearsalAdmissionCommand] = [
        {
            "argv": [
                "git",
                "rev-parse",
                "--verify",
                "--quiet",
                "--end-of-options",
                "HEAD^{commit}",
            ],
            "expected_exit_codes": [0],
            "purpose": "candidate-identity",
        }
    ]
    for ancestor in [target["head"], *source_heads]:
        admission_commands.append(
            {
                "argv": [
                    "git",
                    "merge-base",
                    "--is-ancestor",
                    ancestor,
                    final_candidate,
                ],
                "expected_exit_codes": [0],
                "purpose": "candidate-ancestry",
            }
        )
    admission_commands.append(
        {
            "argv": ["make", "gate"],
            "expected_exit_codes": [0],
            "purpose": "exact-candidate-gate",
        }
    )
    return {
        "admission": {
            "commands": admission_commands,
            "evidence_requirements": [
                "terminal-ref-snapshot-exact",
                "ref-freshness-objects-exact",
                "path-manifest-counts-and-digests-exact",
                "shared-path-conflict-count-zero",
                "merge-tree-and-commit-tree-exit-zero",
                "candidate-object-ids-recorded",
                "candidate-ancestry-complete",
                "native-conflict-prediction-clean",
                "exact-candidate-gate",
                "authorized-single-writer-integration",
            ],
        },
        "base_head": target["head"],
        "bounds": {
            "branch_limit": branch_limit,
            "group_limit": group_limit,
            "head_limit": head_limit,
            "path_display_limit": path_display_limit,
            "path_limit": path_limit,
        },
        "candidate_id": candidate_id,
        "candidate_kind": "unreferenced-commit-chain",
        "conflict_prediction": conflict_prediction,
        "final_candidate": final_candidate,
        "preflight": {
            "changed_path_count": len(group_paths),
            "freshness_checks": freshness_checks,
            "pair_count": pair_count,
            "path_checks": path_checks,
            "path_redactions": shared_redactions,
            "shared_infrastructure_path_count": len(shared_paths),
            "shared_infrastructure_paths": shared_display,
            "shared_infrastructure_paths_truncated": shared_truncated,
            "shared_path_conflict_count": 0,
            "shared_path_conflicts": [],
        },
        "recipe": recipe,
        "source_heads": source_heads,
    }
