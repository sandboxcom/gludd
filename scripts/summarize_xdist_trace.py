from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_TRACE_LOG = "/tmp/gludd-xdist-progress.log"


def _events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    parsed: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            parsed.append({"event": "PARSE_ERROR", "line": line[:500]})
            continue
        if isinstance(value, dict):
            parsed.append(value)
    return parsed


def summarize_log(path: Path) -> dict[str, Any]:
    unfinished: dict[tuple[str, str], dict[str, str]] = {}
    failures: list[dict[str, Any]] = []
    last_by_worker: dict[str, str] = {}
    started = 0
    finished = 0

    for event in _events(path):
        event_name = str(event.get("event", ""))
        worker = str(event.get("worker") or "controller")
        nodeid = event.get("nodeid")
        if event_name == "START" and isinstance(nodeid, str):
            started += 1
            unfinished[(worker, nodeid)] = {"worker": worker, "nodeid": nodeid}
            last_by_worker[worker] = nodeid
        elif event_name == "FINISH" and isinstance(nodeid, str):
            finished += 1
            unfinished.pop((worker, nodeid), None)
        elif event_name == "REPORT" and event.get("outcome") == "failed":
            failures.append(
                {
                    "worker": worker,
                    "nodeid": nodeid,
                    "when": event.get("when"),
                    "duration": event.get("duration"),
                    "longrepr": event.get("longrepr"),
                }
            )

    return {
        "path": str(path),
        "events": len(_events(path)),
        "started": started,
        "finished": finished,
        "unfinished": sorted(unfinished.values(), key=lambda value: (value["worker"], value["nodeid"])),
        "last_by_worker": dict(sorted(last_by_worker.items())),
        "failures": failures[:50],
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    path = Path(args[0]) if args else Path(DEFAULT_TRACE_LOG)
    print(json.dumps(summarize_log(path), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
