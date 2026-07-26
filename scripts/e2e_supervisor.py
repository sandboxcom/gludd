"""Durable supervisor state for restartable, memory-bounded E2E runs."""

from __future__ import annotations

import argparse
import json
import os
import signal
import tempfile
import time
from pathlib import Path
from typing import Any

_COMPLETED = {"PASS", "SKIP"}
_VALID = _COMPLETED | {"FAIL", "RUNNING"}


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, state: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(state, handle, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return state


def ensure_state(path: Path, *, revision: str) -> dict[str, Any]:
    state = _read(path)
    if state.get("revision") != revision:
        state = {"revision": revision, "files": {}, "last_heartbeat": 0.0}
        return _write(path, state)
    state.setdefault("files", {})
    state.setdefault("last_heartbeat", 0.0)
    return state


def record_status(path: Path, file_name: str, status: str) -> dict[str, Any]:
    if status not in _VALID:
        raise ValueError(f"invalid E2E status: {status}")
    state = _read(path)
    state.setdefault("files", {})[file_name] = {"status": status, "updated_at": time.time()}
    state.setdefault("last_heartbeat", 0.0)
    return _write(path, state)


def pending_files(path: Path, files: list[str]) -> list[str]:
    state = _read(path)
    entries = state.get("files", {})
    return [file_name for file_name in files if entries.get(file_name, {}).get("status") not in _COMPLETED]


def heartbeat(path: Path) -> dict[str, Any]:
    state = _read(path)
    state.setdefault("files", {})
    state["last_heartbeat"] = time.time()
    return _write(path, state)


def _files(root: Path, pattern: str) -> list[str]:
    return [str(path) for path in sorted(root.rglob(pattern)) if path.is_file()]


def _heartbeat_loop(path: Path, interval: int) -> int:
    running = True

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while running:
        state = heartbeat(path)
        print(f"E2E_HEARTBEAT state={path} ts={state['last_heartbeat']:.0f}", flush=True)
        for _ in range(max(1, interval)):
            if not running:
                break
            time.sleep(1)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("ensure", "pending"):
        command = subparsers.add_parser(name)
        command.add_argument("--state", type=Path, required=True)
        command.add_argument("--revision", required=True)
        if name == "pending":
            command.add_argument("--root", type=Path, required=True)
            command.add_argument("--glob", required=True)
    record = subparsers.add_parser("record")
    record.add_argument("--state", type=Path, required=True)
    record.add_argument("--file", required=True)
    record.add_argument("--status", choices=sorted(_VALID), required=True)
    beat = subparsers.add_parser("heartbeat-loop")
    beat.add_argument("--state", type=Path, required=True)
    beat.add_argument("--interval", type=int, default=300)
    args = parser.parse_args()
    if args.command == "ensure":
        print(json.dumps(ensure_state(args.state, revision=args.revision), sort_keys=True))
    elif args.command == "pending":
        ensure_state(args.state, revision=args.revision)
        print("\n".join(pending_files(args.state, _files(args.root, args.glob))))
    elif args.command == "record":
        record_status(args.state, args.file, args.status)
    else:
        return _heartbeat_loop(args.state, args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
