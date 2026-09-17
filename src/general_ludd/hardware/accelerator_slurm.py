"""Normalize scheduler-declared Slurm GPU and TPU resources."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence

from general_ludd.hardware.accelerator_types import (
    AcceleratorKind,
    AcceleratorLocation,
    AcceleratorResource,
    safe_runtime_text,
)

_MAX_ACCELERATORS = 100_000
_GRES_RE = re.compile(
    r"^(?P<kind>gpu|tpu):(?:(?P<model>[A-Za-z0-9_.+-]+):)?"
    r"(?P<count>[0-9]+)(?:\([^\r\n]*\))?$",
    re.IGNORECASE,
)
_MEMORY_SUFFIX_RE = re.compile(
    r"(?P<size>[0-9]+(?:\.[0-9]+)?)gb(?:$|[_+.-])",
    re.IGNORECASE,
)
_UNAVAILABLE_STATES = frozenset(
    {
        "DOWN",
        "DRAIN",
        "DRAINED",
        "FAIL",
        "FAILING",
        "FUTURE",
        "INVAL",
        "MAINT",
        "NO_RESPOND",
        "POWER_DOWN",
        "POWERING_DOWN",
        "UNKNOWN",
    }
)
_KNOWN_STATES = frozenset({"ALLOCATED", "COMPLETING", "IDLE", "MIXED", "PLANNED"})


def _gres_tokens(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(token.strip() for token in value.split(",") if token.strip())
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        return tuple(token.strip() for token in value if isinstance(token, str) and token.strip())
    return ()


def _parse_gres(value: object) -> dict[tuple[AcceleratorKind, str], int]:
    parsed: dict[tuple[AcceleratorKind, str], int] = {}
    for token in _gres_tokens(value):
        match = _GRES_RE.fullmatch(token)
        if match is None:
            continue
        count = int(match.group("count"))
        if not 1 <= count <= _MAX_ACCELERATORS:
            continue
        kind = AcceleratorKind(match.group("kind").casefold())
        model = (match.group("model") or "unspecified").casefold()
        key = (kind, model)
        parsed[key] = min(_MAX_ACCELERATORS, parsed.get(key, 0) + count)
    return parsed


def _states(value: object) -> frozenset[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        values = [item for item in value if isinstance(item, str)]
    else:
        return frozenset({"UNKNOWN"})
    tokens = {
        token
        for item in values
        for token in re.findall(r"[A-Z_]+", item.upper())
    }
    return frozenset(tokens or {"UNKNOWN"})


def _partitions(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        candidates = [item for item in value if isinstance(item, str)]
    else:
        candidates = []
    return tuple(sorted({safe_runtime_text(item, "unspecified") for item in candidates if item.strip()}))


def _model_memory_gb(model: str) -> float | None:
    match = _MEMORY_SUFFIX_RE.search(model)
    if match is None:
        return None
    value = float(match.group("size"))
    return value if value > 0 and math.isfinite(value) else None


def parse_slurm_nodes(nodes: Sequence[Mapping[str, object]]) -> tuple[AcceleratorResource, ...]:
    """Normalize scheduler-declared GPU/TPU GRES without inferring SKU facts."""
    if not isinstance(nodes, Sequence) or isinstance(nodes, bytes | bytearray | str):
        raise ValueError("nodes must be a sequence of mappings")
    resources: list[AcceleratorResource] = []
    for raw_node in nodes:
        if not isinstance(raw_node, Mapping):
            continue
        node = safe_runtime_text(
            raw_node.get("name") or raw_node.get("hostname") or raw_node.get("node_name"),
            "unknown-node",
        )
        configured = _parse_gres(raw_node.get("gres"))
        used_value = raw_node.get("gres_used", raw_node.get("gres_used_detail"))
        used = _parse_gres(used_value)
        state = _states(raw_node.get("state"))
        unhealthy = bool(state & _UNAVAILABLE_STATES) or not bool(state & _KNOWN_STATES)
        usage_known = used_value is not None
        partitions = _partitions(raw_node.get("partitions", raw_node.get("partition")))
        for (kind, model), total_count in configured.items():
            used_count = used.get((kind, model), 0)
            available_count = (
                0
                if unhealthy or ("IDLE" not in state and not usage_known)
                else max(0, total_count - used_count)
            )
            resources.append(
                AcceleratorResource(
                    kind=kind,
                    location=AcceleratorLocation.SLURM,
                    backend="slurm-gres",
                    model=model,
                    vendor="unspecified",
                    resource_key=f"slurm:{node}:{kind.value}:{model}",
                    total_count=total_count,
                    available_count=available_count,
                    memory_gb=_model_memory_gb(model),
                    source="slurm-gres",
                    node=node,
                    partitions=partitions,
                )
            )
    return tuple(sorted(resources, key=lambda item: item.resource_key))


__all__ = ("parse_slurm_nodes",)
