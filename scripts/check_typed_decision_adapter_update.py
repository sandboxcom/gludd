#!/usr/bin/env python3
"""Validate pinned typed-decision fixtures and fail-closed profile updates.

This checker is intentionally offline.  It validates the documentation contract,
normalization examples, and immutable profile transitions without contacting an
endpoint, loading a model, or enabling runtime admission.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, SchemaError

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = Path(
    "docs/releases/typed-decision-adapter-contract-v1.schema.json",
)
DEFAULT_EXAMPLES = Path(
    "docs/releases/typed-decision-adapter-contract-v1.examples.json",
)
MAX_JSON_BYTES = 2 * 1024 * 1024

_DIGEST_FIELDS = (
    "fingerprint",
    "server_digest",
    "model_digest",
    "tokenizer_digest",
    "renderer_digest",
    "calibration_digest",
    "runtime_digest",
)
_IDENTITY_STABLE_FIELDS = (
    "contract_version",
    "backend_implementation",
    "endpoint_trust",
)
_IDENTITY_BOUND_FIELDS = (
    "backend_protocol_version",
    "server_digest",
    "model_revision",
    "model_digest",
    "tokenizer_digest",
    "renderer_digest",
    "calibration_digest",
    "runtime_digest",
)
_MOVING_ALIAS = re.compile(
    r"(?:^|[-_.@/])(latest|preview|main|master|example|unresolved)(?:$|[-_.@/])",
    re.IGNORECASE,
)


class ContractInputError(RuntimeError):
    """Raised when a bounded local contract input cannot be trusted."""


class CheckStatus(StrEnum):
    """Stable checker outcomes for automation and review evidence."""

    VALID_EXAMPLES = "VALID_EXAMPLES"
    COMPATIBLE_NOOP = "COMPATIBLE_NOOP"
    COMPATIBLE_PINNED_UPDATE = "COMPATIBLE_PINNED_UPDATE"
    INCOMPATIBLE = "INCOMPATIBLE"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class CheckReport:
    """Deterministic compatibility result with no runtime side effects."""

    ok: bool
    status: CheckStatus
    findings: tuple[str, ...]
    profile_count: int = 0
    example_count: int = 0


def _sorted_unique(findings: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(findings)))


def load_json_object(path: Path) -> dict[str, object]:
    """Read one bounded, regular UTF-8 JSON object without following symlinks."""

    if path.is_symlink():
        raise ContractInputError(f"{path}: input must not be a symlink")
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_JSON_BYTES + 1)
    except OSError as exc:
        raise ContractInputError(f"{path}: input is missing or unreadable") from exc
    if len(data) > MAX_JSON_BYTES:
        raise ContractInputError(
            f"{path}: input exceeds {MAX_JSON_BYTES} byte limit",
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractInputError(f"{path}: input is not valid UTF-8") from exc
    try:
        loaded: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractInputError(
            f"{path}:{exc.lineno}: invalid JSON: {exc.msg}",
        ) from exc
    if not isinstance(loaded, dict):
        raise ContractInputError(f"{path}: input must contain a JSON object")
    return {str(key): value for key, value in loaded.items()}


def _format_error_path(prefix: str, path: Sequence[object]) -> str:
    rendered = prefix
    for part in path:
        rendered += f"[{part}]" if isinstance(part, int) else f".{part}"
    return rendered


def _schema_findings(
    schema: Mapping[str, object],
    instance: object,
    *,
    profile_only: bool,
) -> tuple[str, ...]:
    validation_schema: Mapping[str, object]
    if profile_only:
        definitions = schema.get("$defs")
        if not isinstance(definitions, dict) or "profile" not in definitions:
            return ("invalid JSON Schema: missing $defs.profile",)
        validation_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": definitions,
            "$ref": "#/$defs/profile",
        }
        prefix = "profile"
    else:
        validation_schema = schema
        prefix = "contract"

    try:
        Draft202012Validator.check_schema(validation_schema)
    except SchemaError as exc:
        return (f"invalid JSON Schema: {exc.message}",)

    validator = Draft202012Validator(
        validation_schema,
        format_checker=FormatChecker(),
    )
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.message,
        ),
    )
    return tuple(
        f"{_format_error_path(prefix, list(error.absolute_path))}: {error.message}"
        for error in errors
    )


def validate_profile(
    schema: Mapping[str, object],
    profile: Mapping[str, object],
) -> tuple[str, ...]:
    """Validate one profile against the normative Draft 2020-12 definition."""

    return _schema_findings(schema, profile, profile_only=True)


def _mapping(parent: Mapping[str, object], key: str) -> Mapping[str, object] | None:
    value = parent.get(key)
    return value if isinstance(value, dict) else None


def _is_repeated_sentinel(value: str, *, prefix: str = "") -> bool:
    payload = value.removeprefix(prefix)
    if not payload:
        return True
    for width in (1, 2, 4, 5, 8, 10, 16, 20):
        if len(payload) % width == 0 and payload == payload[:width] * (
            len(payload) // width
        ):
            return True
    return False


def _has_moving_alias(value: object) -> bool:
    return isinstance(value, str) and _MOVING_ALIAS.search(value) is not None


def check_candidate_pins(profile: Mapping[str, object]) -> tuple[str, ...]:
    """Reject example, enabled, moving, sentinel, or otherwise unpinned profiles."""

    findings: list[str] = []
    if profile.get("example_only") is not False:
        findings.append("candidate profile is example-only")
    if profile.get("profile_state") != "disabled":
        findings.append("candidate profile must remain disabled")

    source = _mapping(profile, "source_pin")
    if source is None:
        findings.append("source_pin must be an object")
    else:
        repository = source.get("repository")
        if not isinstance(repository, str) or not repository.startswith("https://"):
            findings.append("source repository must use https")
        commit = source.get("commit")
        if not isinstance(commit, str) or _is_repeated_sentinel(commit):
            findings.append("source commit is a sentinel")
        archive = source.get("source_archive_digest")
        if not isinstance(archive, str) or _is_repeated_sentinel(
            archive,
            prefix="sha256:",
        ):
            findings.append("source_archive_digest is a sentinel")
        release = source.get("release")
        if _has_moving_alias(release):
            findings.append("source release contains a moving alias")

    identity = _mapping(profile, "identity")
    if identity is None:
        findings.append("identity must be an object")
    else:
        if _has_moving_alias(identity.get("model_revision")):
            findings.append("model_revision contains a moving alias")
        for field in _DIGEST_FIELDS:
            value = identity.get(field)
            if not isinstance(value, str) or _is_repeated_sentinel(
                value,
                prefix="sha256:",
            ):
                findings.append(f"identity.{field} is a sentinel digest")

    transport = _mapping(profile, "transport")
    if transport is None:
        findings.append("transport must be an object")
    elif _has_moving_alias(transport.get("model_id")):
        findings.append("transport model_id contains a moving alias")
    return _sorted_unique(findings)


def _normalization_kinds(bundle: Mapping[str, object]) -> list[str]:
    examples = bundle.get("normalization_examples")
    if not isinstance(examples, list):
        return []
    kinds: list[str] = []
    for example in examples:
        if not isinstance(example, dict):
            continue
        request = _mapping(example, "upstream_request")
        questions = _mapping(request, "questions") if request is not None else None
        if questions is None:
            continue
        for question in questions.values():
            if isinstance(question, dict) and isinstance(question.get("type"), str):
                kinds.append(question["type"])
    return kinds


def check_example_bundle(
    schema: Mapping[str, object],
    bundle: Mapping[str, object],
) -> tuple[str, ...]:
    """Validate canonical examples and prove that none is runtime-admissible."""

    findings = list(_schema_findings(schema, bundle, profile_only=False))
    profiles = bundle.get("profiles")
    examples = bundle.get("normalization_examples")
    known_profiles: set[str] = set()
    if isinstance(profiles, list):
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            profile_id = profile.get("profile_id")
            if not isinstance(profile_id, str):
                continue
            known_profiles.add(profile_id)
            if profile.get("example_only") is not True:
                findings.append(f"example profile {profile_id} must be example-only")
            if profile.get("profile_state") != "disabled":
                findings.append(f"example profile {profile_id} must remain disabled")

    if sorted(_normalization_kinds(bundle)) != ["choice", "noul", "score"]:
        findings.append(
            "normalization examples must cover choice, noul, and score exactly",
        )

    if isinstance(examples, list):
        for example in examples:
            if not isinstance(example, dict):
                continue
            name = example.get("name", "<unknown>")
            profile_id = example.get("profile_id")
            if profile_id not in known_profiles:
                findings.append(f"example {name} references an unknown profile")
    return _sorted_unique(findings)


def check_canonical_contract(schema_path: Path, examples_path: Path) -> CheckReport:
    """Validate the tracked schema and non-runnable normalization examples."""

    try:
        schema = load_json_object(schema_path)
        examples = load_json_object(examples_path)
    except ContractInputError as exc:
        return CheckReport(False, CheckStatus.INVALID, (str(exc),))

    findings = check_example_bundle(schema, examples)
    profiles = examples.get("profiles")
    normalization = examples.get("normalization_examples")
    profile_count = len(profiles) if isinstance(profiles, list) else 0
    example_count = len(normalization) if isinstance(normalization, list) else 0
    return CheckReport(
        not findings,
        CheckStatus.VALID_EXAMPLES if not findings else CheckStatus.INVALID,
        findings,
        profile_count,
        example_count,
    )


def _value_changed(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
    field: str,
) -> bool:
    return baseline.get(field) != candidate.get(field)


def _review_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def check_profile_update(
    schema: Mapping[str, object],
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
) -> CheckReport:
    """Approve only a disabled, schema-equivalent, atomically re-pinned update."""

    findings: list[str] = []
    findings.extend(f"baseline {item}" for item in validate_profile(schema, baseline))
    findings.extend(f"candidate {item}" for item in validate_profile(schema, candidate))
    findings.extend(f"baseline {item}" for item in check_candidate_pins(baseline))
    findings.extend(check_candidate_pins(candidate))
    if findings:
        return CheckReport(False, CheckStatus.INCOMPATIBLE, _sorted_unique(findings))

    if baseline == candidate:
        return CheckReport(True, CheckStatus.COMPATIBLE_NOOP, ())

    if _value_changed(baseline, candidate, "profile_id"):
        findings.append("profile_id changed")
    if _value_changed(baseline, candidate, "example_only"):
        findings.append("example_only changed")

    baseline_source = _mapping(baseline, "source_pin")
    candidate_source = _mapping(candidate, "source_pin")
    baseline_identity = _mapping(baseline, "identity")
    candidate_identity = _mapping(candidate, "identity")
    if None in (
        baseline_source,
        candidate_source,
        baseline_identity,
        candidate_identity,
    ):
        findings.append("required update sections are missing")
        return CheckReport(False, CheckStatus.INCOMPATIBLE, _sorted_unique(findings))

    assert baseline_source is not None
    assert candidate_source is not None
    assert baseline_identity is not None
    assert candidate_identity is not None

    if baseline_source.get("repository") != candidate_source.get("repository"):
        findings.append("source repository changed")
    if baseline_source.get("license") != candidate_source.get("license"):
        findings.append("source license changed")

    commit_changed = baseline_source.get("commit") != candidate_source.get("commit")
    archive_changed = baseline_source.get(
        "source_archive_digest",
    ) != candidate_source.get("source_archive_digest")
    if commit_changed != archive_changed:
        findings.append("source commit and archive digest must change together")

    old_reviewed = _review_date(baseline_source.get("reviewed_at"))
    new_reviewed = _review_date(candidate_source.get("reviewed_at"))
    if old_reviewed is None or new_reviewed is None or new_reviewed < old_reviewed:
        findings.append("review date moved backward")

    if any(
        baseline_identity.get(field) != candidate_identity.get(field)
        for field in _IDENTITY_STABLE_FIELDS
    ):
        findings.append("identity contract changed")

    protected_sections = {
        "transport": "transport contract changed",
        "capabilities": "capability contract changed",
        "limits": "limit contract changed",
        "batching": "batching contract changed",
        "residency": "residency contract changed",
        "redaction": "redaction contract changed",
        "compatibility": "compatibility contract changed",
        "update_policy": "update policy changed",
        "rollout": "rollout contract changed",
    }
    for section, message in protected_sections.items():
        if _value_changed(baseline, candidate, section):
            findings.append(message)

    source_identity_changed = any(
        baseline_source.get(field) != candidate_source.get(field)
        for field in ("release", "commit", "source_archive_digest")
    ) or any(
        baseline_identity.get(field) != candidate_identity.get(field)
        for field in _IDENTITY_BOUND_FIELDS
    )
    fingerprint_changed = baseline_identity.get(
        "fingerprint",
    ) != candidate_identity.get("fingerprint")
    if source_identity_changed and not fingerprint_changed:
        findings.append("identity-bound inputs changed without a new fingerprint")
    if fingerprint_changed and not source_identity_changed:
        findings.append("fingerprint changed without identity-bound inputs")

    normalized = _sorted_unique(findings)
    if normalized:
        return CheckReport(False, CheckStatus.INCOMPATIBLE, normalized)
    return CheckReport(True, CheckStatus.COMPATIBLE_PINNED_UPDATE, ())


def _print_report(report: CheckReport, *, label: str) -> None:
    for finding in report.findings:
        print(f"{label}: {finding}")
    rendered_status = (
        "ERROR" if report.status == CheckStatus.INVALID else report.status.value
    )
    summary = f"{label}: {rendered_status}"
    if report.status == CheckStatus.VALID_EXAMPLES:
        summary += (
            f" profiles={report.profile_count} examples={report.example_count}"
        )
    target = sys.stdout if report.ok else sys.stderr
    print(summary, file=target)


def main(argv: list[str] | None = None) -> int:
    """Run canonical validation and an optional baseline/candidate comparison."""

    parser = argparse.ArgumentParser(
        description="Check pinned typed-decision adapter contract updates.",
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--schema", type=Path)
    parser.add_argument("--examples", type=Path)
    parser.add_argument("--baseline-profile", type=Path)
    parser.add_argument("--candidate-profile", type=Path)
    args = parser.parse_args(argv)

    if (args.baseline_profile is None) != (args.candidate_profile is None):
        print(
            "typed-decision-adapter-update: ERROR: --baseline-profile and "
            "--candidate-profile must be provided together",
            file=sys.stderr,
        )
        return 2

    schema_path = args.schema or args.root / DEFAULT_SCHEMA
    examples_path = args.examples or args.root / DEFAULT_EXAMPLES
    canonical = check_canonical_contract(schema_path, examples_path)
    _print_report(canonical, label="typed-decision-adapter-contract")
    if not canonical.ok:
        return 2
    if args.baseline_profile is None or args.candidate_profile is None:
        return 0

    try:
        schema = load_json_object(schema_path)
        baseline = load_json_object(args.baseline_profile)
        candidate = load_json_object(args.candidate_profile)
    except ContractInputError as exc:
        print(f"typed-decision-adapter-update: ERROR: {exc}", file=sys.stderr)
        return 2

    update = check_profile_update(schema, baseline, candidate)
    _print_report(update, label="typed-decision-adapter-update")
    return 0 if update.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
