"""Diagnostic completed-backlog views within the existing branch inventory owner."""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, NoReturn, TypedDict, cast

if TYPE_CHECKING:
    from branch_reconciliation_plan_types import (
        BranchRecord,
        SummaryGroup,
        SummaryPayload,
        TargetRecord,
    )

    from scripts import branch_reconciliation_inventory as owner
    from scripts import validate_task_ledger as ledger_parser
else:
    _prefix = f"{__package__}." if __package__ else ""
    owner = importlib.import_module(f"{_prefix}branch_reconciliation_inventory")
    ledger_parser = importlib.import_module(f"{_prefix}validate_task_ledger")

LEDGER_BYTE_LIMIT = 8 * 1024 * 1024
STAGING_BYTE_LIMIT = 1024 * 1024
TASK_SCAN_LIMIT = 10_000
CLAIM_SCAN_LIMIT = 100_000
REPORT_LIMIT = 1_000
REPORT_JSON_CHAR_LIMIT = 8 * 1024 * 1024
TITLE_CHAR_LIMIT = 240
HISTORY_EXAMPLE_LIMIT = 20
ISSUE_LIMIT = 20
_MARKER = re.compile(r"^\s*[-+*]\s+\[[^\]]\]")
_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


class Claim(TypedDict):
    """One canonical checkbox claim, never test or release proof."""

    title: str
    title_truncated: bool
    line: int
    explicit_status: str | None


@dataclass(frozen=True)
class Blob:
    """Bounded immutable object content or an explicit unknown condition."""

    status: str
    text: str = ""
    issues: tuple[str, ...] = ()


@dataclass
class Ledger:
    """Canonical per-SHA interpretation; unknown counts stay nullable."""

    head: str
    status: str
    task_count: int | None = None
    claims: dict[str, Claim] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        """Return bounded evidence metadata without repeating historical rows."""
        return {
            "head": self.head, "path": "TASKS.md", "status": self.status,
            "task_count": self.task_count,
            "completed_count": len(self.claims) if self.status == "available" else None,
            "issues": self.issues[:ISSUE_LIMIT],
            "issues_truncated": len(self.issues) > ISSUE_LIMIT,
        }


def _read_blob(
    head: str, path: str, byte_limit: int, *, run: owner.RunFn, cwd: str | None,
) -> Blob:
    """Read a regular blob by immutable tree/object IDs with size validation."""
    tree = run(["git", "ls-tree", "-z", "--full-tree", head, "--", path], cwd)
    if tree.returncode != 0:
        return Blob("unavailable", issues=("immutable tree lookup failed",))
    if not tree.stdout:
        return Blob("missing", issues=("path is absent from the immutable tree",))
    if len(tree.stdout) > 4096 or not tree.stdout.endswith("\0"):
        return Blob("malformed", issues=("malformed immutable tree entry",))
    metadata, separator, found_path = tree.stdout[:-1].partition("\t")
    fields = metadata.split(" ")
    if (
        not separator or found_path != path or len(fields) != 3
        or fields[0] not in {"100644", "100755"} or fields[1] != "blob"
        or not _SHA.fullmatch(fields[2])
    ):
        return Blob("malformed", issues=("expected one regular immutable blob",))
    size_result = run(["git", "cat-file", "-s", fields[2]], cwd)
    if size_result.returncode != 0:
        return Blob("unavailable", issues=("immutable blob size unavailable",))
    if not re.fullmatch(r"[0-9]+\n?", size_result.stdout):
        return Blob("malformed", issues=("malformed immutable blob size",))
    size = int(size_result.stdout)
    if size > byte_limit:
        return Blob("truncated", issues=(f"blob exceeds {byte_limit} byte bound",))
    content = run(["git", "cat-file", "blob", fields[2]], cwd)
    if content.returncode != 0:
        return Blob("unavailable", issues=("immutable blob content unavailable",))
    if len(content.stdout) > byte_limit:
        return Blob("truncated", issues=("immutable blob content exceeded bound",))
    if len(content.stdout.encode("utf-8")) != size:
        return Blob("malformed", issues=("immutable blob size/content mismatch",))
    return Blob("available", content.stdout)


def _claim(task: ledger_parser.TaskRecord, line_number: int) -> Claim:
    body = task["line"][5:].strip()
    for task_id in task["ids"]:
        body = body.removeprefix(task_id).lstrip()
    title = body.split("|", 1)[0].strip(" :—-")
    return {
        "title": title[:TITLE_CHAR_LIMIT],
        "title_truncated": len(title) > TITLE_CHAR_LIMIT,
        "line": line_number, "explicit_status": task["status"],
    }


def _read_ledger(
    head: str, *, run: owner.RunFn, cwd: str | None,
) -> Ledger:
    blob = _read_blob(head, "TASKS.md", LEDGER_BYTE_LIMIT, run=run, cwd=cwd)
    if blob.status != "available":
        return Ledger(head, blob.status, issues=list(blob.issues))
    lines = blob.text.splitlines()
    noncanonical = [
        (number, line, marker.end())
        for number, line in enumerate(lines, start=1)
        if (marker := _MARKER.match(line))
        and not line.strip().startswith(("- [x]", "- [ ]"))
    ]
    issues = [
        f"noncanonical task marker at line {number}"
        for number, _line, _end in noncanonical
    ]
    # The canonical owner takes a Path. This private bounded adapter changes
    # neither repository content nor refs and retains exactly that grammar.
    with tempfile.TemporaryDirectory(prefix="gludd-completed-ledger-") as temporary:
        path = Path(temporary) / "TASKS.md"
        path.write_text(blob.text, encoding="utf-8")
        checked, unchecked = ledger_parser.extract_tasks(path)
        tainted: list[ledger_parser.TaskRecord] = []
        if noncanonical:
            # Canonical unchecked probes identify contaminated primary IDs
            # only. An invalid marker can never become a completion claim.
            path = Path(temporary) / "tainted-identities.md"
            path.write_text(
                "\n".join(f"- [ ] {line[end:].lstrip()}" for _number, line, end in noncanonical),
                encoding="utf-8",
            )
            _never_checked, tainted = ledger_parser.extract_tasks(path)
    tasks = [*checked, *unchecked]
    if len(tasks) + len(tainted) > TASK_SCAN_LIMIT:
        return Ledger(head, "truncated", issues=["task scan bound exceeded"])
    if not tasks and not re.search(r"^#+\s+.*tasks?\b", blob.text, re.I | re.M):
        issues.append("no canonical task records or TASKS heading")
    excluded_ids = {task_id for task in tainted for task_id in task["ids"]}
    identities = Counter(task_id for task in tasks for task_id in task["ids"])
    for task_id, count in sorted(identities.items()):
        if count > 1:
            excluded_ids.add(task_id)
            issues.append(f"duplicate task ID: {task_id[:128]}")
    for task in tasks:
        if not task["ids"] or any(len(task_id) > 128 for task_id in task["ids"]):
            excluded_ids.update(task["ids"])
            issues.append("missing or overlong canonical task identity")
    for task in checked:
        if not ledger_parser.task_is_effectively_complete(task, checked=True):
            excluded_ids.update(task["ids"])
            issues.append(f"checkbox/status disagreement: {','.join(task['ids'])[:128]}")
    line_numbers = {line.strip(): number for number, line in enumerate(lines, start=1)}
    claims = {
        task_id: _claim(task, line_numbers[task["line"]])
        for task in checked
        if ledger_parser.task_is_effectively_complete(task, checked=True)
        for task_id in task["ids"]
        if task_id not in excluded_ids
    }
    return Ledger(
        head, "malformed" if issues else "available",
        None if issues else len(tasks), claims, issues,
    )


def _staging_entries(value: object) -> dict[str, str]:
    """Validate the existing v0.1.2 declaration without importing its claims."""
    if not isinstance(value, dict):
        raise ValueError("staging document must be an object")
    data = cast(dict[str, object], value)
    if data.get("schema_version") != 1 or data.get("release") != "v0.1.2":
        raise ValueError("unrecognized staging document")
    entries: dict[str, str] = {}
    for field_name, id_field, status in (
        ("completed_items", "task_id", "completed"),
        ("implemented_items", "item_id", "implemented"),
        ("excluded_open_tasks", "", "excluded-open"),
    ):
        rows = data.get(field_name)
        if not isinstance(rows, list) or len(rows) > TASK_SCAN_LIMIT:
            raise ValueError("missing or overbound staging entries")
        field_ids: set[str] = set()
        for row in rows:
            if id_field:
                if not isinstance(row, dict):
                    raise ValueError("malformed staging entry")
                task_id = row.get(id_field)
                title = row.get("title")
                if not isinstance(title, str) or not title.strip():
                    raise ValueError("malformed staging title")
            else:
                task_id = row
            if (
                not isinstance(task_id, str) or not task_id
                or len(task_id) > 128 or task_id != task_id.strip()
                or any(not character.isprintable() for character in task_id)
                or task_id in field_ids
                or (task_id in entries and not (
                    status == "excluded-open" and entries[task_id] == "implemented"
                ))
            ):
                raise ValueError("malformed or duplicate staging identity")
            field_ids.add(task_id)
            entries[task_id] = (
                "implemented-open" if task_id in entries else status
            )
    return entries


def _read_staging(
    target_head: str, baseline: TargetRecord, path: str,
    *, run: owner.RunFn, cwd: str | None,
) -> tuple[dict[str, object], dict[str, str]]:
    blob = _read_blob(target_head, path, STAGING_BYTE_LIMIT, run=run, cwd=cwd)
    status, issues = blob.status, list(blob.issues)
    entries: dict[str, str] = {}
    if status == "available":
        try:
            value = json.loads(blob.text)
            entries = _staging_entries(value)
            declared = value.get("baseline")
            if not isinstance(declared, dict) or declared != {
                "commit": baseline["head"], "ref": baseline["input"],
            }:
                raise ValueError("staging baseline does not match resolved baseline")
        except (ValueError, UnicodeError):
            status, issues, entries = "malformed", ["invalid staging declaration"], {}
    return {
        "head": target_head, "path": path, "status": status, "issues": issues,
        "completed_ids": sorted(tid for tid, state in entries.items() if state == "completed"),
        "implemented_ids": sorted(
            tid for tid, state in entries.items() if state in {"implemented", "implemented-open"}
        ),
        "excluded_open_ids": sorted(
            tid for tid, state in entries.items() if state in {"excluded-open", "implemented-open"}
        ),
    }, entries


def _source_claim(group: SummaryGroup, claim: Claim, ledger: Ledger) -> dict[str, object]:
    return {
        **claim, "head": group["head"], "refs": group["refs"], "names": group["names"],
        "ledger": ledger.summary(), "ledger_evidence_complete": ledger.status == "available",
        "classification": group["classification"], "lifecycle": group["lifecycle"],
        "implementation_integration": "unknown",
    }


def _verify_after_report(
    summary: SummaryPayload, baseline: TargetRecord,
    *, run: owner.RunFn, cwd: str | None,
) -> None:
    branches: list[BranchRecord] = [
        {
            "classification": group["classification"], "head": group["head"],
            "lifecycle": group["lifecycle"], "name": name, "ref": ref,
            "patch_equivalent_commits": group["patch_equivalent_commits"],
            "unique_commits": group["unique_commits"],
        }
        for group in summary["groups"]
        for name, ref in zip(group["names"], group["refs"], strict=True)
    ]
    owner._verify_terminal_snapshot(
        sorted(branches, key=lambda branch: branch["ref"]), summary["target"],
        run=run, cwd=cwd,
    )
    if owner._resolve_target(baseline["input"], run=run, cwd=cwd) != baseline:
        raise owner.InventoryError("baseline changed during completed backlog report")


def build_report(
    summary: SummaryPayload, baseline: TargetRecord,
    staging_path: str, report_limit: int, *, run: owner.RunFn,
    cwd: str | None = None, progress: owner.ProgressFn | None = None,
) -> dict[str, object]:
    """Compare every current unique source with exact target/baseline claims."""
    if not summary["terminal"] or summary["truncated"] or not summary["ok"]:
        raise owner.InventoryError("completed backlog report requires complete inventory")
    groups = sorted(
        (group for group in summary["groups"] if group["classification"] == "unique"),
        key=lambda group: (group["head"], group["refs"]),
    )
    heads = list(dict.fromkeys([
        summary["target"]["head"], baseline["head"], *(group["head"] for group in groups),
    ]))
    ledgers: dict[str, Ledger] = {}
    claim_count = 0
    for index, head in enumerate(heads, start=1):
        if progress is not None:
            progress(f"completed-ledger={index}/{len(heads)} head={head}")
        ledger = _read_ledger(head, run=run, cwd=cwd)
        claim_count += len(ledger.claims)
        if claim_count > CLAIM_SCAN_LIMIT:
            raise owner.InventoryError("completed backlog claim scan bound exceeded")
        ledgers[head] = ledger
    target = ledgers[summary["target"]["head"]]
    prior = ledgers[baseline["head"]]
    target_known = target.status == "available"
    prior_known = prior.status == "available"
    comparisons_known = target_known and prior_known
    staging, staged = _read_staging(
        target.head, baseline, staging_path, run=run, cwd=cwd,
    )
    stage_known = staging["status"] == "available"
    target_ids, prior_ids = set(target.claims), set(prior.claims)
    source_ids: set[str] = set()
    claims_by_id: dict[str, list[dict[str, object]]] = {}
    current_heads: list[dict[str, object]] = []
    for group in groups:
        ledger = ledgers[group["head"]]
        completed_ids = set(ledger.claims)
        source_ids.update(completed_ids)
        known = comparisons_known and ledger.status == "available"
        for task_id in sorted(completed_ids - target_ids - prior_ids):
            claims_by_id.setdefault(task_id, []).append(
                _source_claim(group, ledger.claims[task_id], ledger)
            )
        current_heads.append({
            "head": group["head"], "refs": group["refs"], "names": group["names"],
            "classification": group["classification"], "lifecycle": group["lifecycle"],
            "target_head": target.head, "ledger": ledger.summary(),
            "implementation_integration": "unknown",
            "completed_in_target_count": (
                len(completed_ids & target_ids)
                if target_known and ledger.status == "available" else None
            ),
            "baseline_history_count": (
                len(completed_ids & prior_ids)
                if prior_known and ledger.status == "available" else None
            ),
            "branch_only_completion_count": len(
                completed_ids - target_ids - prior_ids
            ) if known else None,
        })
    findings: list[dict[str, object]] = [
        {
            "kind": (
                "branch-only-completion-claim" if comparisons_known and all(
                    source["ledger_evidence_complete"] is True for source in sources
                )
                else "completion-claim-with-unknown-comparison"
            ),
            "task_id": task_id,
            "completed_in_target": False if target_known else None,
            "completed_in_baseline": False if prior_known else None,
            "title": sources[0]["title"], "source_claims": sources,
            "target_head": target.head, "baseline_head": prior.head,
            "staged_for_v012": staged.get(task_id, "not-staged") if stage_known else "unknown",
            "implementation_integration": "unknown",
        }
        for task_id, sources in sorted(claims_by_id.items())
    ]
    for head, ledger in ledgers.items():
        if ledger.status != "available":
            findings.append({
                "kind": "unknown-ledger", "head": head, "ledger": ledger.summary(),
                "refs": sorted({
                    ref for group in summary["groups"] if group["head"] == head
                    for ref in group["refs"]
                } | ({summary["target"]["ref"]} if head == target.head else set())
                  | ({baseline["ref"]} if head == prior.head else set())),
            })
    if not stage_known:
        findings.append({"kind": "unknown-staging", "staging": staging})
    recent = sorted(target_ids - prior_ids) if comparisons_known else []
    baseline_only = sorted(prior_ids - target_ids - source_ids) if comparisons_known else []
    since_items = [
        {
            "task_id": task_id, **target.claims[task_id],
            "staged_for_v012": staged.get(task_id, "not-staged") if stage_known else "unknown",
        }
        for task_id in recent[:report_limit]
    ]
    all_ledgers_known = all(ledger.status == "available" for ledger in ledgers.values())
    truncated = len(findings) > report_limit or len(recent) > report_limit
    complete = all_ledgers_known and stage_known and not truncated
    report: dict[str, object] = {
        "schema_version": 1, "mode": "completed-backlog", "diagnostic_only": True,
        "gate_evidence": False, "release_ready": False,
        "ok": complete, "evidence_complete": complete, "truncated": truncated,
        "target": summary["target"], "baseline": baseline,
        "inventory_terminal": summary["terminal"], "inventory_truncated": summary["truncated"],
        "inventory_pages": summary["pages"],
        "branch_evidence": "current-heads-inspected" if groups else "no-current-heads",
        "bounds": {
            "report_limit": report_limit, "ledger_bytes": LEDGER_BYTE_LIMIT,
            "staging_bytes": STAGING_BYTE_LIMIT, "task_scan_limit": TASK_SCAN_LIMIT,
            "claim_scan_limit": CLAIM_SCAN_LIMIT, "json_chars": REPORT_JSON_CHAR_LIMIT,
            "local_ref_scan_limit": owner.LOCAL_REF_SCAN_LIMIT,
        },
        "counts": {
            "current_heads": len(groups),
            "current_branches": sum(group["branch_count"] for group in groups),
            "unknown_current_heads": sum(
                ledgers[group["head"]].status != "available" for group in groups
            ),
            "observed_branch_only_completed_ids": sum(
                finding["kind"] == "branch-only-completion-claim" for finding in findings
            ),
            "observed_unknown_comparison_completed_ids": sum(
                finding["kind"] == "completion-claim-with-unknown-comparison" for finding in findings
            ),
            "branch_only_completed_ids": len(claims_by_id) if all_ledgers_known else None,
        },
        "completed_in_target": {
            "count": len(target_ids) if target.status == "available" else None,
            "ledger": target.summary(),
            "since_baseline_count": len(recent) if comparisons_known else None,
            "since_baseline_items": since_items,
            "items_truncated": len(recent) > report_limit,
        },
        "baseline_history": {
            "count": len(prior_ids) if prior.status == "available" else None,
            "ledger": prior.summary(),
            "baseline_only_count": len(baseline_only) if all_ledgers_known else None,
            "baseline_only_examples": [
                {"task_id": task_id, **prior.claims[task_id]}
                for task_id in baseline_only[:HISTORY_EXAMPLE_LIMIT]
            ],
            "examples_truncated": len(baseline_only) > HISTORY_EXAMPLE_LIMIT,
        },
        "current_heads": current_heads, "staging": staging,
        "findings": findings[:report_limit], "total_findings": len(findings),
        "omitted_findings": max(0, len(findings) - report_limit),
        "limitations": [
            "Checkbox/status claims do not prove tests, gate, CI, release readiness or missing implementation.",
            "Unique source commits can contain metadata-only or equivalent implementation changes.",
            "Only the exhaustive local ref snapshot is inspected; no network freshness is asserted.",
        ],
    }
    if progress is not None:
        progress("verify=completed-report-terminal-ref-snapshot")
    _verify_after_report(summary, baseline, run=run, cwd=cwd)
    if len(json.dumps(report, sort_keys=True)) > REPORT_JSON_CHAR_LIMIT:
        raise owner.InventoryError("completed backlog report output exceeded bound")
    return report


class _ReportParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise owner.InventoryError(f"invalid completed backlog options: {message}")


def _human(report: dict[str, object]) -> str:
    """Render bounded findings with escaped untrusted strings and exact SHAs."""
    def text(value: object) -> str:
        if isinstance(value, str):
            return "".join(character if character.isprintable() else "�" for character in value)
        return json.dumps(value, sort_keys=True, ensure_ascii=True)

    target = cast(dict[str, object], report["target"])
    baseline = cast(dict[str, object], report["baseline"])
    counts = cast(dict[str, object], report["counts"])
    lines = [
        "Completed backlog diagnostic; gate_evidence=false; release_ready=false",
        f"Target {text(target['ref'])}@{text(target['head'])}",
        f"Baseline {text(baseline['ref'])}@{text(baseline['head'])}",
        f"Current heads {text(counts['current_heads'])}; branches {text(counts['current_branches'])}; "
        f"branch-only completed IDs {text(counts['branch_only_completed_ids'])}",
        f"Evidence complete={text(report['evidence_complete'])}; truncated={text(report['truncated'])}; "
        f"omitted findings={text(report['omitted_findings'])}",
        "Completion claims leave actual implementation integration unknown.",
    ]
    for finding in cast(list[dict[str, object]], report["findings"]):
        if finding["kind"] in {
            "branch-only-completion-claim", "completion-claim-with-unknown-comparison",
        }:
            lines.append(
                f"{text(finding['task_id'])}: {text(finding['title'])}; "
                f"v0.1.2={text(finding['staged_for_v012'])}; implementation=unknown"
            )
            for source in cast(list[dict[str, object]], finding["source_claims"]):
                lines.append(
                    f"  {text(source['head'])} refs={text(source['refs'])} "
                    f"classification={text(source['classification'])}"
                )
        else:
            lines.append(text(finding))
    target_summary = cast(dict[str, object], report["completed_in_target"])
    lines.append(
        f"Completed in target={text(target_summary['count'])}; "
        f"since baseline={text(target_summary['since_baseline_count'])}"
    )
    for item in cast(list[dict[str, object]], target_summary["since_baseline_items"]):
        lines.append(
            f"  {text(item['task_id'])}: {text(item['title'])}; "
            f"v0.1.2={text(item['staged_for_v012'])}"
        )
    history = cast(dict[str, object], report["baseline_history"])
    lines.append(
        f"Baseline completed={text(history['count'])}; "
        f"baseline-only history={text(history['baseline_only_count'])}"
    )
    return "\n".join(lines)


def main(argv: Sequence[str], *, run: owner.RunFn) -> int:
    """Select the diagnostic mode without adding a second inventory or runner."""
    parser = _ReportParser(description=__doc__)
    for flag in ("--target", "--limit", "--after", "--baseline", "--staging-path", "--report-limit"):
        parser.add_argument(flag, required=True)
    for flag in ("--completed-backlog", "--quiet-progress", "--human"):
        parser.add_argument(flag, action="store_true")
    try:
        args = parser.parse_args(list(argv))
        limit, report_limit = int(args.limit), int(args.report_limit)
        path = PurePosixPath(args.staging_path)
        if (
            args.after or limit < 1 or limit > owner.MAX_LIMIT
            or report_limit < 1 or report_limit > REPORT_LIMIT
            or not args.staging_path or path.is_absolute() or ".." in path.parts
            or str(path) != args.staging_path
            or any(ord(character) < 32 for character in args.staging_path)
        ):
            raise owner.InventoryError("invalid completed backlog bounds/path/cursor")
        # Validate both symbolic inputs before starting an expensive inventory.
        owner._resolve_target(args.target, run=run, cwd=None)
        baseline = owner._resolve_target(args.baseline, run=run, cwd=None)
        progress = None if args.quiet_progress else owner._progress
        summary = owner.collect_summary(args.target, limit, run=run, progress=progress)
        report = build_report(
            summary, baseline, args.staging_path, report_limit, run=run, progress=progress,
        )
    except (owner.InventoryError, ValueError, OSError, UnicodeError) as exc:
        json.dump({
            "error": str(exc)[:400], "ok": False, "schema_version": 1,
            "diagnostic_only": True, "gate_evidence": False, "release_ready": False,
        }, sys.stdout, sort_keys=True)
        print()
        return 2
    print(_human(report) if args.human else json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 2
