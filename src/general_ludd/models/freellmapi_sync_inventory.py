"""Validate and diff the Gludd-owned FreeLLMAPI shim inventory."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from general_ludd.models.freellmapi_sync_contracts import (
    FreeLLMAPISyncFault,
    exact_mapping,
    fail_sync,
    nonempty,
    sha,
    string_list,
)

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_EXPORT = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]{0,127}$")
_SHIM_KEYS = {
    "id",
    "path",
    "kind",
    "owner",
    "sha256",
    "upstream_exports",
    "copied_upstream_source",
    "file_type",
}


def _normalise_path(value: object) -> str:
    fault = FreeLLMAPISyncFault.OWNED_SHIM_BOUNDARY
    path = nonempty(value, fault)
    if not path.isascii() or "\\" in path or "\x00" in path:
        fail_sync(fault)
    parsed = PurePosixPath(path)
    parts = parsed.parts
    if (
        parsed.is_absolute()
        or parsed.as_posix() != path
        or parts[:2] != ("src", "general_ludd")
        or any(part in {"", ".", ".."} for part in parts)
        or any(part.casefold() == "vendor" for part in parts)
        or not path.endswith((".py", ".json", ".md"))
    ):
        fail_sync(fault)
    return path


def _normalise_shim(value: object) -> dict[str, object]:
    shim = exact_mapping(
        value,
        keys=_SHIM_KEYS,
        fault=FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID,
    )
    shim_id = nonempty(shim["id"], FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID)
    if _IDENTIFIER.fullmatch(shim_id) is None:
        fail_sync(FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID)
    path = _normalise_path(shim["path"])
    if (
        shim["owner"] != "gludd"
        or type(shim["kind"]) is not str
        or shim["kind"] not in ("adapter", "schema", "migration", "test_bridge")
        or shim["copied_upstream_source"] is not False
        or shim["file_type"] != "regular"
    ):
        fail_sync(FreeLLMAPISyncFault.OWNED_SHIM_BOUNDARY)
    return {
        "id": shim_id,
        "path": path,
        "kind": shim["kind"],
        "owner": "gludd",
        "sha256": sha(shim["sha256"], FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID),
        "upstream_exports": string_list(
            shim["upstream_exports"],
            FreeLLMAPISyncFault.OWNED_SHIM_BOUNDARY,
            pattern=_EXPORT,
        ),
        "copied_upstream_source": False,
        "file_type": "regular",
    }


def _normalise_shim_list(value: object) -> list[dict[str, object]]:
    fault = FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID
    if type(value) is not list or not value or len(value) > 256:
        fail_sync(fault)
    raw_paths = [
        item.get("path") if type(item) is dict else None for item in value
    ]
    folded_paths = [
        path.casefold() for path in raw_paths if type(path) is str
    ]
    if len(folded_paths) != len(raw_paths) or len(folded_paths) != len(
        set(folded_paths)
    ):
        fail_sync(fault)
    result = [_normalise_shim(item) for item in value]
    ids = [str(item["id"]) for item in result]
    paths = [str(item["path"]) for item in result]
    if len(ids) != len(set(ids)) or len(paths) != len(set(paths)):
        fail_sync(fault)
    return sorted(result, key=lambda item: str(item["id"]))


def normalise_inventory(value: object) -> dict[str, list[dict[str, object]]]:
    """Validate and sort the complete baseline/candidate shim inventory."""
    inventory = exact_mapping(
        value,
        keys={"baseline", "candidate"},
        fault=FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID,
    )
    return {
        "baseline": _normalise_shim_list(inventory["baseline"]),
        "candidate": _normalise_shim_list(inventory["candidate"]),
    }


def diff_normalised(
    inventory: Mapping[str, Sequence[Mapping[str, object]]],
) -> dict[str, object]:
    """Build a stable ID-only diff from a normalized inventory."""
    baseline = {str(item["id"]): item for item in inventory["baseline"]}
    candidate = {str(item["id"]): item for item in inventory["candidate"]}
    old_ids = set(baseline)
    new_ids = set(candidate)
    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)
    changed = sorted(key for key in old_ids & new_ids if baseline[key] != candidate[key])
    unchanged = sorted((old_ids & new_ids) - set(changed))
    return {
        "added": added,
        "changed": changed,
        "removed": removed,
        "unchanged": unchanged,
        "counts": {
            "added": len(added),
            "changed": len(changed),
            "removed": len(removed),
            "unchanged": len(unchanged),
        },
    }


def build_owned_shim_diff(inventory: Mapping[str, object]) -> dict[str, object]:
    """Return a stable ID-only diff after validating the owned-shim boundary."""
    return diff_normalised(normalise_inventory(inventory))
