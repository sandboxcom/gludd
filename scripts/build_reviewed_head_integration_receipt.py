#!/usr/bin/env python3
"""Build immutable reviewed-head release evidence from real Git topology."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from general_ludd.git_release.reviewed_head_integration import (
    AppliedHeadEvidence,
    ExactGateEvidence,
    FocusedValidationEvidence,
    HeadApplicationMode,
    PrerequisiteAncestor,
    PrerequisiteResolution,
    ReviewedHead,
    ReviewedHeadIntegrationReceipt,
    build_reviewed_head_integration_plan,
    encode_reviewed_head_integration_receipt,
)

_MAX_EVIDENCE_BYTES = 1_048_576
_MANIFEST_SCHEMA_VERSION = 1
_GATE_ATTESTATION_SCHEMA_VERSION = 3


class _DuplicateJsonKey(ValueError):
    """Internal marker for ambiguous JSON evidence."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise ValueError(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{label} must be an array")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _strings(value: object, label: str) -> tuple[str, ...]:
    return tuple(
        _string(item, f"{label} entry") for item in _sequence(value, label)
    )


def _read_bounded_json(path: Path, label: str) -> tuple[object, bytes]:
    try:
        encoded = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{label} could not be read") from exc
    if not encoded or len(encoded) > _MAX_EVIDENCE_BYTES:
        raise ValueError(f"{label} is empty or exceeds the size limit")
    try:
        value = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey) as exc:
        raise ValueError(f"{label} is not unambiguous UTF-8 JSON") from exc
    return value, encoded


def _owned_evidence_path(manifest_dir: Path, value: object) -> Path:
    relative = Path(_string(value, "review receipt path"))
    if relative.is_absolute():
        raise ValueError("review receipt path must be relative to the manifest")
    root = manifest_dir.resolve()
    try:
        candidate = (root / relative).resolve(strict=True)
    except OSError as exc:
        raise ValueError("review receipt path could not be resolved") from exc
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise ValueError("review receipt path escapes the manifest directory")
    return candidate


def _git(repo_root: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *arguments],
            input=input_bytes,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ValueError("repository evidence could not be inspected") from exc
    if completed.returncode != 0:
        raise ValueError("repository evidence does not match the integration manifest")
    return completed.stdout


def _commit_parents(repo_root: Path, sha: str) -> tuple[str, ...]:
    fields = _git(repo_root, "rev-list", "--parents", "-n", "1", sha).decode(
        "ascii"
    ).split()
    if not fields or fields[0] != sha:
        raise ValueError("repository parent topology is unavailable")
    return tuple(fields[1:])


def _patch_id(repo_root: Path, sha: str) -> str:
    patch = _git(
        repo_root,
        "show",
        "--format=",
        "--binary",
        "--full-index",
        sha,
    )
    fields = _git(
        repo_root,
        "patch-id",
        "--stable",
        input_bytes=patch,
    ).decode("ascii", errors="ignore").split()
    if not fields:
        raise ValueError("repository patch identity is unavailable")
    return fields[0]


def _verify_gate_attestation(
    raw: object,
    *,
    expected_final_sha: str,
) -> None:
    gate = _mapping(raw, "gate attestation")
    if gate.get("schema_version") != _GATE_ATTESTATION_SCHEMA_VERSION:
        raise ValueError("gate attestation schema version is unsupported")
    if gate.get("status") != "pass" or gate.get("returncode") != 0:
        raise ValueError("receipt generation requires a passing exact gate")
    if gate.get("lane") != "local":
        raise ValueError("receipt generation requires a local exact gate")
    identity = _mapping(gate.get("identity"), "gate attestation identity")
    if (
        identity.get("head_sha") != expected_final_sha
        or identity.get("expected_sha") != expected_final_sha
        or identity.get("clean") is not True
        or identity.get("exact_sha") is not True
        or identity.get("queries_ok") is not True
    ):
        raise ValueError("gate attestation does not bind the exact clean candidate")


def _verify_review_receipt(
    raw: object,
    *,
    source_ref: str,
    source_sha: str,
    reviewed_base_sha: str,
    focused_validation_ids: tuple[str, ...],
) -> None:
    review = _mapping(raw, "review receipt")
    if review.get("schema_version") != 1 or review.get("verdict") != "approved":
        raise ValueError("review receipt is not an approved versioned review")
    if not isinstance(review.get("reviewer"), str) or not review.get("reviewer"):
        raise ValueError("review receipt reviewer is missing")
    if (
        review.get("source_ref") != source_ref
        or review.get("source_sha") != source_sha
        or review.get("reviewed_base_sha") != reviewed_base_sha
        or _strings(
            review.get("focused_validation_ids"),
            "review focused validation IDs",
        )
        != focused_validation_ids
    ):
        raise ValueError("review receipt identity does not match the manifest")


def _verify_repository_topology(
    repo_root: Path,
    *,
    base_sha: str,
    heads: tuple[ReviewedHead, ...],
    applications: tuple[AppliedHeadEvidence, ...],
) -> None:
    final_sha = applications[-1].after_sha
    head_sha = _git(repo_root, "rev-parse", "HEAD").decode("ascii").strip()
    if head_sha != final_sha:
        raise ValueError("repository HEAD does not match the integrated candidate")
    if applications[0].before_sha != base_sha:
        raise ValueError("repository integration chain does not start at the base")

    for head, evidence in zip(heads, applications, strict=True):
        resolved_source = _git(
            repo_root,
            "rev-parse",
            "--verify",
            f"{head.source_ref}^{{commit}}",
        ).decode("ascii").strip()
        if resolved_source != head.source_sha:
            raise ValueError("repository source ref does not match the reviewed SHA")
        if _commit_parents(repo_root, evidence.after_sha) != evidence.parent_shas:
            raise ValueError("repository parent topology disagrees with the manifest")
        if evidence.application_mode is HeadApplicationMode.CHERRY_PICK and (
            _patch_id(repo_root, head.source_sha)
            != _patch_id(repo_root, evidence.after_sha)
        ):
            raise ValueError("repository cherry-pick patch identity does not match")
        for prerequisite in head.prerequisite_ancestry:
            if prerequisite.resolution is PrerequisiteResolution.ALREADY_REACHABLE:
                _git(
                    repo_root,
                    "merge-base",
                    "--is-ancestor",
                    prerequisite.ancestor_sha,
                    base_sha,
                )
            elif prerequisite.resolution is PrerequisiteResolution.PATCH_EQUIVALENT:
                raise ValueError(
                    "patch-equivalent prerequisite generation is not supported"
                )


def build_receipt_from_manifest(
    raw_manifest: object,
    *,
    manifest_dir: Path,
    gate_attestation: object,
    repo_root: Path,
) -> ReviewedHeadIntegrationReceipt:
    """Validate release evidence and construct the canonical typed receipt."""
    manifest = _mapping(raw_manifest, "integration manifest")
    if manifest.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
        raise ValueError("integration manifest schema version is unsupported")
    base_sha = _string(manifest.get("base_sha"), "integration base SHA")
    raw_heads = _sequence(manifest.get("heads"), "reviewed heads")
    if not raw_heads:
        raise ValueError("reviewed heads must be nonempty")

    heads: list[ReviewedHead] = []
    applications: list[AppliedHeadEvidence] = []
    for ordinal, raw_head in enumerate(raw_heads, start=1):
        item = _mapping(raw_head, "reviewed head")
        source_ref = _string(item.get("source_ref"), "source ref")
        source_sha = _string(item.get("source_sha"), "source SHA")
        reviewed_base_sha = _string(
            item.get("reviewed_base_sha"), "reviewed base SHA"
        )
        focused_ids = _strings(
            item.get("focused_validation_ids"), "focused validation IDs"
        )
        application_mode = HeadApplicationMode(
            _string(item.get("application_mode"), "application mode")
        )
        prerequisites = tuple(
            PrerequisiteAncestor(
                ancestor_sha=_string(
                    _mapping(raw, "prerequisite").get("ancestor_sha"),
                    "prerequisite ancestor SHA",
                ),
                resolution=PrerequisiteResolution(
                    _string(
                        _mapping(raw, "prerequisite").get("resolution"),
                        "prerequisite resolution",
                    )
                ),
            )
            for raw in _sequence(
                item.get("prerequisite_ancestry"), "prerequisite ancestry"
            )
        )
        review_path = _owned_evidence_path(
            manifest_dir, item.get("review_receipt_file")
        )
        review_raw, review_bytes = _read_bounded_json(review_path, "review receipt")
        _verify_review_receipt(
            review_raw,
            source_ref=source_ref,
            source_sha=source_sha,
            reviewed_base_sha=reviewed_base_sha,
            focused_validation_ids=focused_ids,
        )
        review_digest = hashlib.sha256(review_bytes).hexdigest()
        head = ReviewedHead(
            source_ref=source_ref,
            source_sha=source_sha,
            reviewed_base_sha=reviewed_base_sha,
            review_receipt_sha256=review_digest,
            focused_validation_ids=focused_ids,
            application_mode=application_mode,
            prerequisite_ancestry=prerequisites,
        )
        heads.append(head)
        applications.append(
            AppliedHeadEvidence(
                ordinal=ordinal,
                source_ref=source_ref,
                source_sha=source_sha,
                review_receipt_sha256=review_digest,
                application_mode=application_mode,
                before_sha=_string(item.get("before_sha"), "before SHA"),
                after_sha=_string(item.get("after_sha"), "after SHA"),
                parent_shas=_strings(item.get("parent_shas"), "parent SHAs"),
            )
        )

    exact_gate_id = _string(manifest.get("exact_gate_id"), "exact gate ID")
    plan = build_reviewed_head_integration_plan(
        base_sha=base_sha,
        heads=tuple(heads),
        exact_gate_id=exact_gate_id,
    )
    focused_raw = _mapping(
        manifest.get("focused_validation"), "focused validation evidence"
    )
    focused = FocusedValidationEvidence(
        tip_sha=_string(focused_raw.get("tip_sha"), "focused tip SHA"),
        source_shas=_strings(focused_raw.get("source_shas"), "focused source SHAs"),
        command_ids=_strings(
            focused_raw.get("command_ids"), "focused command IDs"
        ),
        run_count=focused_raw.get("run_count"),  # type: ignore[arg-type]
        passed=focused_raw.get("passed"),  # type: ignore[arg-type]
    )
    final_sha = applications[-1].after_sha
    _verify_gate_attestation(gate_attestation, expected_final_sha=final_sha)
    receipt = ReviewedHeadIntegrationReceipt(
        plan=plan,
        applied_heads=tuple(applications),
        focused_validation=focused,
        exact_gate=ExactGateEvidence(
            tip_sha=final_sha,
            command_id=exact_gate_id,
            run_count=1,
            passed=True,
        ),
    )
    _verify_repository_topology(
        repo_root.resolve(),
        base_sha=base_sha,
        heads=tuple(heads),
        applications=tuple(applications),
    )
    return receipt


def write_receipt(path: Path, receipt: ReviewedHeadIntegrationReceipt) -> None:
    """Atomically write one canonical receipt without following output symlinks."""
    destination = path.resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("receipt output must not be a symbolic link")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encode_reviewed_head_integration_receipt(receipt))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--gate-attestation", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest, _ = _read_bounded_json(args.manifest, "integration manifest")
        gate, _ = _read_bounded_json(args.gate_attestation, "gate attestation")
        receipt = build_receipt_from_manifest(
            manifest,
            manifest_dir=args.manifest.resolve().parent,
            gate_attestation=gate,
            repo_root=args.repo_root,
        )
        if not args.validate_only:
            write_receipt(args.output, receipt)
        print(f"reviewed-head receipt valid final_sha={receipt.final_sha}")
        return 0
    except (OSError, TypeError, ValueError) as exc:
        print(f"reviewed-head receipt rejected: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
