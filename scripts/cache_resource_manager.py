"""Inventory and remove allowlisted user-cache children without path escape."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path


class CacheResourceError(ValueError):
    """Report an invalid or unsafe cache-resource operation."""


@dataclass(frozen=True)
class CacheEntry:
    """Describe one immediate cache child and its allocated size."""

    path: Path
    allocated_bytes: int
    status: str = "measured"
    error: str | None = None

    def to_json(self) -> str:
        """Serialize the entry as one stable JSON object."""
        payload = asdict(self)
        payload["path"] = str(self.path)
        return json.dumps(payload, sort_keys=True)


def _removable_roots() -> frozenset[Path]:
    home = Path.home().resolve(strict=False)
    return frozenset(
        {
            (home / ".cache").resolve(strict=False),
            (home / ".local" / "share" / "containers").resolve(strict=False),
            (home / "Library" / "Caches").resolve(strict=False),
        }
    )


def _inventory_roots() -> frozenset[Path]:
    home = Path.home().resolve(strict=False)
    return _removable_roots() | frozenset({(home / "tmp").resolve(strict=False)})


def _validate_root(root: Path, *, removal: bool = False) -> Path:
    expanded = root.expanduser()
    if expanded.is_symlink():
        raise CacheResourceError("root must not be a symlink")
    canonical = expanded.resolve(strict=False)
    if removal:
        allowed = canonical in _removable_roots()
    else:
        temp_root = (Path.home().resolve(strict=False) / "tmp").resolve(strict=False)
        allowed = (
            canonical in _inventory_roots()
            or canonical.parent == temp_root
            or canonical.parent.parent == temp_root
        )
    if not allowed:
        operation = " for removal" if removal else ""
        raise CacheResourceError(f"root is not allowlisted{operation}: {canonical}")
    if not canonical.is_dir():
        raise CacheResourceError(f"root is not a directory: {canonical}")
    return canonical


def _allocated_bytes(path: Path) -> int:
    try:
        result = subprocess.run(
            ["/usr/bin/du", "-sk", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        kibibytes = int(result.stdout.split(maxsplit=1)[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        raise CacheResourceError(f"could not measure cache child {path}: {exc}") from exc
    return kibibytes * 1024


def inventory_cache_children(root: Path, *, limit: int) -> list[CacheEntry]:
    """Return the largest immediate children of one allowlisted cache root."""
    if not 1 <= limit <= 100:
        raise CacheResourceError("limit must be between 1 and 100")
    canonical = _validate_root(root)
    rows: list[CacheEntry] = []
    for child in canonical.iterdir():
        try:
            rows.append(CacheEntry(path=child, allocated_bytes=_allocated_bytes(child)))
        except CacheResourceError as exc:
            rows.append(
                CacheEntry(
                    path=child,
                    allocated_bytes=0,
                    status="error",
                    error=str(exc),
                )
            )
    rows.sort(
        key=lambda row: (
            row.status != "error",
            -row.allocated_bytes,
            str(row.path),
        )
    )
    return rows[:limit]


def remove_cache_child(root: Path, candidate: Path, *, apply: bool) -> bool:
    """Validate and optionally remove exactly one immediate cache child."""
    canonical_root = _validate_root(root, removal=True)
    expanded = candidate.expanduser()
    if expanded.is_symlink():
        raise CacheResourceError("candidate must not be a symlink")
    canonical_candidate = expanded.resolve(strict=False)
    if canonical_candidate.parent != canonical_root:
        raise CacheResourceError("candidate must be an exact immediate child of root")
    if not canonical_candidate.exists():
        raise CacheResourceError(f"candidate does not exist: {canonical_candidate}")
    if not apply:
        return False
    if canonical_candidate.is_dir():
        shutil.rmtree(canonical_candidate)
    else:
        canonical_candidate.unlink()
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("--root", type=Path, required=True)
    inventory.add_argument("--limit", type=int, default=20)
    remove = subparsers.add_parser("remove")
    remove.add_argument("--root", type=Path, required=True)
    remove.add_argument("--candidate", type=Path, required=True)
    remove.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded cache inventory or exact-child removal."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "inventory":
            rows = inventory_cache_children(args.root, limit=args.limit)
            for row in rows:
                print(row.to_json())
            print(json.dumps({"entries": len(rows), "status": "complete"}))
            return 0
        removed = remove_cache_child(args.root, args.candidate, apply=args.apply)
        print(
            json.dumps(
                {
                    "candidate": str(args.candidate),
                    "removed": removed,
                    "status": "complete",
                },
                sort_keys=True,
            )
        )
        return 0
    except CacheResourceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
