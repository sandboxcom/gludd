"""Task-shape identity and bounded evidence selection for self-improvement."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from general_ludd.routing_roles.small_model_policy import DEFAULT_TASK_CONTRACTS
from general_ludd.schemas.benchmark import TaskType
from general_ludd.scoring.task_embeddings import CANONICAL_TASK_DESCRIPTIONS
from general_ludd.skills.embeddings import HashEmbedder, cosine_similarity

_MAX_REPRESENTATIVE_CASES = len(TaskType)
_TASK_TYPE_ORDER = {task_type: index for index, task_type in enumerate(TaskType)}
_EMBEDDER = HashEmbedder()
_CANONICAL_VECTORS = {
    task_type: _EMBEDDER.embed(description)
    for task_type, description in CANONICAL_TASK_DESCRIPTIONS.items()
}


def infer_task_type(task_text: str) -> TaskType:
    """Classify text against the repository's canonical TaskType descriptions.

    The dependency-free repository hash embedder makes classification stable and
    local. Empty or vocabulary-disjoint text fails closed instead of assigning
    evidence to an arbitrary task shape.
    """
    if not isinstance(task_text, str) or not task_text.strip():
        raise ValueError("task_text must be a non-empty string")
    query = _EMBEDDER.embed(task_text)
    ranked = sorted(
        (
            (cosine_similarity(query, vector), _TASK_TYPE_ORDER[task_type], task_type)
            for task_type, vector in _CANONICAL_VECTORS.items()
        ),
        key=lambda item: (-item[0], item[1]),
    )
    if not ranked or ranked[0][0] <= 0.0:
        raise ValueError("task_text does not match an existing TaskType")
    return ranked[0][2]


def select_representative_evidence(
    records: Sequence[Mapping[str, Any]],
    *,
    max_cases: int = _MAX_REPRESENTATIVE_CASES,
) -> tuple[dict[str, Any], ...]:
    """Select a deterministic, bounded, task-shape-diverse evidence sample.

    A task shape combines the existing TaskType and an existing default task
    contract kind. Legacy records without either dimension are intentionally
    excluded. The newest record represents each exact shape, then round-robin
    ordering across TaskType prevents one task family consuming the sample.
    """
    if (
        isinstance(max_cases, bool)
        or not isinstance(max_cases, int)
        or not 1 <= max_cases <= _MAX_REPRESENTATIVE_CASES
    ):
        raise ValueError(
            f"max_cases must be between 1 and {_MAX_REPRESENTATIVE_CASES}"
        )
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise ValueError("records must be a sequence of evidence mappings")

    newest_by_shape: dict[
        tuple[str, TaskType, str],
        tuple[dict[str, Any], str],
    ] = {}
    for raw_record in records:
        if not isinstance(raw_record, Mapping):
            continue
        model_profile_id = raw_record.get("model_profile_id")
        task_type_raw = raw_record.get("task_type")
        task_kind = raw_record.get("task_kind")
        if (
            not isinstance(model_profile_id, str)
            or not model_profile_id
            or not isinstance(task_type_raw, str)
            or not isinstance(task_kind, str)
        ):
            continue
        try:
            task_type = TaskType(task_type_raw)
        except ValueError:
            continue
        if task_kind not in DEFAULT_TASK_CONTRACTS:
            continue
        record = dict(raw_record)
        stable_identity = _stable_record_identity(record)
        shape = (model_profile_id, task_type, task_kind)
        current = newest_by_shape.get(shape)
        if current is None or _record_order_key(
            record,
            stable_identity=stable_identity,
        ) > _record_order_key(current[0], stable_identity=current[1]):
            newest_by_shape[shape] = (record, stable_identity)

    by_task_type: dict[
        TaskType,
        list[tuple[str, str, str, dict[str, Any]]],
    ] = defaultdict(list)
    for (
        model_profile_id,
        task_type,
        task_kind,
    ), (record, stable_identity) in newest_by_shape.items():
        by_task_type[task_type].append(
            (task_kind, model_profile_id, stable_identity, record)
        )
    for queue in by_task_type.values():
        queue.sort(key=lambda item: item[:3])

    selected: list[dict[str, Any]] = []
    round_index = 0
    while len(selected) < max_cases:
        added = False
        for task_type in TaskType:
            queue = by_task_type.get(task_type, [])
            if round_index < len(queue):
                selected.append(dict(queue[round_index][3]))
                added = True
                if len(selected) == max_cases:
                    break
        if not added:
            break
        round_index += 1
    return tuple(selected)


def export_representative_evidence(
    records: Sequence[Mapping[str, Any]],
    *,
    max_cases: int = _MAX_REPRESENTATIVE_CASES,
) -> bytes:
    """Export bounded representative evidence as canonical UTF-8 JSON bytes.

    Selection is delegated to :func:`select_representative_evidence`, so exports
    retain its stable task-shape ordering and bounds. Values outside the JSON data
    model fail closed instead of being stringified into a lossy evidence record.
    """
    evidence = select_representative_evidence(records, max_cases=max_cases)
    _validate_mapping_keys(evidence)
    payload = {"schema_version": 1, "evidence": evidence}
    try:
        rendered = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("evidence contains a value outside canonical JSON") from exc
    return rendered.encode("utf-8")


def _validate_mapping_keys(value: object) -> None:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("canonical JSON mappings require string keys")
        for nested in value.values():
            _validate_mapping_keys(nested)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            _validate_mapping_keys(nested)


def _record_order_key(
    record: Mapping[str, Any],
    *,
    stable_identity: str | None = None,
) -> tuple[float, str]:
    registered_at = record.get("registered_at", 0.0)
    timestamp = (
        float(registered_at)
        if isinstance(registered_at, (int, float))
        and not isinstance(registered_at, bool)
        else 0.0
    )
    if not math.isfinite(timestamp):
        timestamp = 0.0
    identity = (
        stable_identity
        if stable_identity is not None
        else _stable_record_identity(record)
    )
    return timestamp, identity


def _stable_record_identity(record: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(record),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        return repr(sorted((str(key), repr(value)) for key, value in record.items()))


__all__ = [
    "export_representative_evidence",
    "infer_task_type",
    "select_representative_evidence",
]
