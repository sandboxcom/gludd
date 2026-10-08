"""Fail-closed local dataset admission through the Frictionless Python API."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from frictionless import Schema, validate

FRICTIONLESS_VERSION = "5.19.1"
MAX_RESOURCES = 32
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_FIELDS = 256
MAX_ERRORS = 100
MAX_CARD_BYTES = 64 * 1024
_REMOTE_PATH = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_ERROR_TYPE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_>])(?:[A-Za-z]:[\\/]|/)[^\s\"'<>]+"
)
_ALLOWED_ARGS = frozenset(
    {
        "root",
        "resources",
        "schema",
        "name",
        "description",
        "license",
    }
)


@dataclass(frozen=True)
class AdmissionLimits:
    """Internal, downward-only limits used to make resource bounds testable."""

    max_resources: int = MAX_RESOURCES
    max_total_bytes: int = MAX_TOTAL_BYTES
    max_fields: int = MAX_FIELDS
    max_errors: int = MAX_ERRORS
    max_card_bytes: int = MAX_CARD_BYTES

    def validate(self) -> None:
        """Reject non-positive limits or any attempt to raise a hard ceiling."""
        pairs = (
            (self.max_resources, MAX_RESOURCES),
            (self.max_total_bytes, MAX_TOTAL_BYTES),
            (self.max_fields, MAX_FIELDS),
            (self.max_errors, MAX_ERRORS),
            (self.max_card_bytes, MAX_CARD_BYTES),
        )
        if any(value < 1 or value > ceiling for value, ceiling in pairs):
            raise DatasetAdmissionError("internal admission limits may only lower hard ceilings")


_DEFAULT_LIMITS = AdmissionLimits()


class DatasetAdmissionError(ValueError):
    """A bounded dataset rejection safe to return through Ansible."""

    def __init__(
        self,
        message: str,
        *,
        errors: Sequence[Mapping[str, object]] = (),
        truncated: bool = False,
    ) -> None:
        super().__init__(message)
        self.errors = [dict(item) for item in errors[:MAX_ERRORS]]
        self.truncated = truncated or len(errors) > MAX_ERRORS

    def as_result(self) -> dict[str, object]:
        """Render a stable failed Ansible result without internal paths."""
        return {
            "admitted": False,
            "changed": False,
            "error_count": len(self.errors),
            "errors": self.errors,
            "failed": True,
            "msg": str(self),
            "truncated": self.truncated,
        }


ValidationRunner = Callable[..., object]


@dataclass(frozen=True)
class _Fingerprint:
    path: Path
    relative: str
    size: int
    sha256: str
    identity: tuple[int, int, int, int]


def _safe_text(value: object, *, root: Path, limit: int = 512) -> str:
    """Collapse control whitespace, redact the root, and bound one message."""
    text = str(value).replace(str(root), "<root>")
    text = _ABSOLUTE_PATH.sub("<path>", text)
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _require_metadata(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return " ".join(value.split())


def _root_path(value: object) -> tuple[Path, Path]:
    if not isinstance(value, str) or not value.strip():
        raise DatasetAdmissionError("root must be a non-empty absolute local path")
    if _REMOTE_PATH.match(value):
        raise DatasetAdmissionError("root must be a local path")
    supplied = Path(value)
    if not supplied.is_absolute():
        raise DatasetAdmissionError("root must be an absolute local path")
    try:
        if supplied.is_symlink():
            raise DatasetAdmissionError("root must not be a symlink")
        resolved = supplied.resolve(strict=True)
    except OSError as exc:
        raise DatasetAdmissionError("root is unavailable") from exc
    if not resolved.is_dir():
        raise DatasetAdmissionError("root must be a directory")
    return supplied, resolved


def _reject_symlink_components(root: Path, candidate: Path) -> None:
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise DatasetAdmissionError("dataset paths must not contain a symlink")


def _regular_file(root_input: Path, root: Path, value: object, label: str) -> tuple[Path, str]:
    if not isinstance(value, str) or not value.strip():
        raise DatasetAdmissionError(f"{label} must be a non-empty local path")
    if "\x00" in value or _REMOTE_PATH.match(value):
        raise DatasetAdmissionError(f"{label} must be a local path")
    supplied = Path(value)
    if ".." in supplied.parts:
        raise DatasetAdmissionError(f"{label} escapes root")
    candidate = supplied if supplied.is_absolute() else root_input / supplied
    _reject_symlink_components(root_input, candidate)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise DatasetAdmissionError(f"{label} is unavailable") from exc
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise DatasetAdmissionError(f"{label} escapes root") from exc
    try:
        mode = resolved.stat().st_mode
    except OSError as exc:
        raise DatasetAdmissionError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(mode):
        raise DatasetAdmissionError(f"{label} must be a regular file")
    return resolved, relative


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise DatasetAdmissionError("dataset file became unavailable") from exc
    return digest.hexdigest()


def _fingerprint(
    path: Path,
    relative: str,
    *,
    max_bytes: int | None = None,
) -> _Fingerprint:
    try:
        before = path.stat()
        if max_bytes is not None and before.st_size > max_bytes:
            raise DatasetAdmissionError(
                "schema and resources must total no more than 512 MiB"
            )
        digest = _hash_file(path)
        after = path.stat()
    except OSError as exc:
        raise DatasetAdmissionError("dataset file became unavailable") from exc
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_identity != after_identity or not stat.S_ISREG(after.st_mode):
        raise DatasetAdmissionError("dataset file changed during admission")
    return _Fingerprint(
        path=path,
        relative=relative,
        size=after.st_size,
        sha256=digest,
        identity=after_identity,
    )


def _unchanged(original: _Fingerprint) -> _Fingerprint:
    current = _fingerprint(original.path, original.relative)
    if current.identity != original.identity or current.sha256 != original.sha256:
        raise DatasetAdmissionError("dataset file changed during admission")
    return current


def _error_descriptor(value: object, *, root: Path) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {"type": "invalid-error", "message": "validator returned a malformed error"}
    raw_type = value.get("type", value.get("code", "validation-error"))
    error_type = str(raw_type).lower()
    if _ERROR_TYPE.fullmatch(error_type) is None:
        error_type = "validation-error"
    result: dict[str, object] = {
        "type": error_type,
        "message": _safe_text(value.get("message", error_type), root=root),
    }
    for source, target in (("rowNumber", "row_number"), ("fieldNumber", "field_number")):
        number = value.get(source)
        if isinstance(number, int) and not isinstance(number, bool) and number > 0:
            result[target] = number
    return result


def _as_mapping(report: object) -> Mapping[str, object]:
    convert = getattr(report, "to_descriptor", None)
    if not callable(convert):
        raise DatasetAdmissionError("validator returned no structured report")
    try:
        descriptor = convert()
    except Exception as exc:
        raise DatasetAdmissionError("validator report serialization failed") from exc
    if not isinstance(descriptor, Mapping):
        raise DatasetAdmissionError("validator returned a malformed report")
    return descriptor


def _sequence(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise DatasetAdmissionError(f"validator report {label} must be a list")
    return value


def _is_truncated(
    report: Mapping[str, object],
    task: Mapping[str, object],
    error_count: int,
) -> bool:
    warnings = _sequence(report.get("warnings", []), "warnings") + _sequence(
        task.get("warnings", []), "task warnings"
    )
    if any("limit" in str(warning).lower() for warning in warnings):
        return True
    declared: list[int] = []
    for container in (report.get("stats"), task.get("stats")):
        if isinstance(container, Mapping):
            count = container.get("errors")
            if isinstance(count, int) and not isinstance(count, bool):
                declared.append(count)
    return any(count > error_count for count in declared)


def _report_outcome(
    report: object,
    *,
    root: Path,
    limits: AdmissionLimits,
) -> tuple[int, int]:
    descriptor = _as_mapping(report)
    tasks = _sequence(descriptor.get("tasks"), "tasks")
    if len(tasks) != 1 or not isinstance(tasks[0], Mapping):
        raise DatasetAdmissionError("validator report must contain exactly one task")
    task = tasks[0]
    if task.get("type") != "table":
        raise DatasetAdmissionError("validator report must contain one table task")
    top_errors = _sequence(descriptor.get("errors", []), "errors")
    task_errors = _sequence(task.get("errors", []), "task errors")
    raw_errors = top_errors + task_errors
    truncated = _is_truncated(descriptor, task, len(raw_errors)) or len(raw_errors) > limits.max_errors
    errors = [
        _error_descriptor(item, root=root)
        for item in raw_errors[: limits.max_errors]
    ]
    report_valid = descriptor.get("valid") is True
    task_valid = task.get("valid") is True
    if not report_valid or not task_valid or errors or truncated:
        if not errors:
            errors = [
                {
                    "type": "invalid-report",
                    "message": "validator rejected the resource without error details",
                }
            ]
        raise DatasetAdmissionError(
            "dataset validation failed",
            errors=errors,
            truncated=truncated,
        )
    labels = _sequence(task.get("labels", []), "labels")
    if any(not isinstance(label, str) for label in labels):
        raise DatasetAdmissionError("validator returned malformed field labels")
    stats = task.get("stats", {})
    if not isinstance(stats, Mapping):
        raise DatasetAdmissionError("validator returned malformed task statistics")
    fields = stats.get("fields", len(labels))
    rows = stats.get("rows", 0)
    for value, label in ((fields, "fields"), (rows, "rows")):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise DatasetAdmissionError(f"validator returned invalid {label} statistics")
    if fields > limits.max_fields or len(labels) > limits.max_fields:
        raise DatasetAdmissionError("validated resources may contain at most 256 fields")
    if fields != len(labels):
        raise DatasetAdmissionError("validator returned incoherent field statistics")
    return fields, rows


def _schema_descriptor(path: Path, limits: AdmissionLimits) -> tuple[dict[str, object], int]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetAdmissionError("schema must be one valid UTF-8 JSON object") from exc
    if not isinstance(loaded, dict):
        raise DatasetAdmissionError("schema must be one valid UTF-8 JSON object")
    fields = loaded.get("fields")
    if not isinstance(fields, list):
        raise DatasetAdmissionError("schema fields must be a list")
    if len(fields) > limits.max_fields:
        raise DatasetAdmissionError("schema may define at most 256 fields")
    try:
        Schema.from_descriptor(copy.deepcopy(loaded))
    except Exception as exc:
        raise DatasetAdmissionError("Frictionless rejected the schema descriptor") from exc
    return loaded, len(fields)


def _resource_values(value: object, limits: AdmissionLimits) -> list[str]:
    if not isinstance(value, list) or not value:
        raise DatasetAdmissionError("resources must contain at least one CSV path")
    if len(value) > limits.max_resources:
        raise DatasetAdmissionError("resources may contain at most 32 CSV paths")
    if any(not isinstance(item, str) for item in value):
        raise TypeError("resources must contain only strings")
    return list(value)


def _validate_resource(
    resource: _Fingerprint,
    schema_descriptor: Mapping[str, object],
    *,
    root: Path,
    validation_runner: ValidationRunner,
    limits: AdmissionLimits,
) -> tuple[int, int]:
    try:
        report = validation_runner(
            resource.relative,
            type="table",
            format="csv",
            scheme="file",
            basepath=str(root),
            schema=Schema.from_descriptor(copy.deepcopy(dict(schema_descriptor))),
            limit_errors=limits.max_errors,
        )
    except DatasetAdmissionError:
        raise
    except Exception as exc:
        raise DatasetAdmissionError(
            f"Frictionless validation could not complete ({type(exc).__name__})"
        ) from exc
    fields, rows = _report_outcome(report, root=root, limits=limits)
    expected_fields = schema_descriptor.get("fields")
    if not isinstance(expected_fields, list) or fields != len(expected_fields):
        raise DatasetAdmissionError("validator report does not match the schema field count")
    return fields, rows


def _card_bytes(card: Mapping[str, object]) -> bytes:
    return json.dumps(
        card,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def admit_dataset(
    *,
    root: object,
    resources: object,
    schema: object,
    name: object = "",
    description: object = "",
    license: object = "",
    validation_runner: ValidationRunner = validate,
    _limits: AdmissionLimits = _DEFAULT_LIMITS,
) -> dict[str, object]:
    """Validate local CSV resources and return a deterministic bound data card."""
    _limits.validate()
    if version("frictionless") != FRICTIONLESS_VERSION:
        raise DatasetAdmissionError("controller Frictionless version does not match the locked runtime")
    root_input, resolved_root = _root_path(root)
    resource_values = _resource_values(resources, _limits)
    schema_path, schema_relative = _regular_file(
        root_input,
        resolved_root,
        schema,
        "schema",
    )
    schema_fingerprint = _fingerprint(
        schema_path,
        schema_relative,
        max_bytes=_limits.max_total_bytes,
    )
    descriptor, field_count = _schema_descriptor(schema_path, _limits)

    resource_fingerprints: list[_Fingerprint] = []
    seen: set[Path] = set()
    remaining_bytes = _limits.max_total_bytes - schema_fingerprint.size
    for index, value in enumerate(resource_values, start=1):
        path, relative = _regular_file(
            root_input,
            resolved_root,
            value,
            f"resource {index}",
        )
        if path.suffix.lower() != ".csv":
            raise DatasetAdmissionError("dataset resources must be CSV files")
        if path in seen:
            raise DatasetAdmissionError("dataset resource paths must be unique")
        seen.add(path)
        fingerprint = _fingerprint(path, relative, max_bytes=remaining_bytes)
        resource_fingerprints.append(fingerprint)
        remaining_bytes -= fingerprint.size

    total_bytes = schema_fingerprint.size + sum(item.size for item in resource_fingerprints)
    if total_bytes > _limits.max_total_bytes:
        raise DatasetAdmissionError("schema and resources must total no more than 512 MiB")

    card_resources: list[dict[str, object]] = []
    for resource in resource_fingerprints:
        fields, rows = _validate_resource(
            resource,
            descriptor,
            root=resolved_root,
            validation_runner=validation_runner,
            limits=_limits,
        )
        stable = _unchanged(resource)
        card_resources.append(
            {
                "bytes": stable.size,
                "fields": fields,
                "path": stable.relative,
                "rows": rows,
                "sha256": stable.sha256,
            }
        )
    stable_schema = _unchanged(schema_fingerprint)
    card: dict[str, object] = {
        "description": _require_metadata(description, "description"),
        "license": _require_metadata(license, "license"),
        "limits": {
            "card_bytes": MAX_CARD_BYTES,
            "errors": MAX_ERRORS,
            "fields": MAX_FIELDS,
            "resources": MAX_RESOURCES,
            "total_bytes": MAX_TOTAL_BYTES,
        },
        "name": _require_metadata(name, "name"),
        "resources": card_resources,
        "schema": {
            "fields": field_count,
            "path": stable_schema.relative,
            "sha256": stable_schema.sha256,
        },
        "schema_version": "gludd.dataset-card/v1",
        "validator": {"name": "frictionless", "version": FRICTIONLESS_VERSION},
    }
    encoded_card = _card_bytes(card)
    if len(encoded_card) > _limits.max_card_bytes:
        raise DatasetAdmissionError("generated data card must not exceed 64 KiB")
    return {
        "admitted": True,
        "changed": False,
        "data_card": card,
        "data_card_sha256": hashlib.sha256(encoded_card).hexdigest(),
        "error_count": 0,
        "errors": [],
        "resource_count": len(card_resources),
        "truncated": False,
    }


def action_arguments(args: Mapping[str, object]) -> dict[str, object]:
    """Validate the closed action surface before any file or validator work."""
    unknown = sorted(set(args) - _ALLOWED_ARGS)
    if unknown:
        raise DatasetAdmissionError(f"unsupported argument: {unknown[0]}")
    missing = sorted({"root", "resources", "schema"} - set(args))
    if missing:
        raise DatasetAdmissionError(f"missing required argument: {missing[0]}")
    return dict(args)


__all__ = [
    "FRICTIONLESS_VERSION",
    "AdmissionLimits",
    "DatasetAdmissionError",
    "action_arguments",
    "admit_dataset",
]
