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
    events = _events(path)
    unfinished: dict[tuple[str, str], dict[str, str]] = {}
    failures: list[dict[str, Any]] = []
    failure_nodeids: list[str] = []
    seen_failure_nodeids: set[str] = set()
    last_by_worker: dict[str, str] = {}
    memory_by_worker: dict[str, dict[str, Any]] = {}
    largest_rss_increases: list[dict[str, Any]] = []
    started = 0
    finished = 0

    for event in events:
        event_name = str(event.get("event", ""))
        worker = str(event.get("worker") or "controller")
        nodeid = event.get("nodeid")
        rss_kb = event.get("rss_kb")
        if isinstance(rss_kb, int) and rss_kb >= 0:
            memory = memory_by_worker.setdefault(
                worker,
                {
                    "first_rss_kb": rss_kb,
                    "peak_rss_kb": rss_kb,
                    "growth_rss_kb": 0,
                    "peak_nodeid": nodeid,
                    "_previous_rss_kb": rss_kb,
                },
            )
            previous_rss_kb = int(memory["_previous_rss_kb"])
            increase_rss_kb = rss_kb - previous_rss_kb
            if increase_rss_kb > 0:
                largest_rss_increases.append(
                    {
                        "worker": worker,
                        "nodeid": nodeid,
                        "event": event_name,
                        "rss_kb": rss_kb,
                        "increase_rss_kb": increase_rss_kb,
                    }
                )
            memory["_previous_rss_kb"] = rss_kb
            if rss_kb > int(memory["peak_rss_kb"]):
                memory["peak_rss_kb"] = rss_kb
                memory["peak_nodeid"] = nodeid
            memory["growth_rss_kb"] = int(memory["peak_rss_kb"]) - int(memory["first_rss_kb"])
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
            if isinstance(nodeid, str) and nodeid not in seen_failure_nodeids:
                seen_failure_nodeids.add(nodeid)
                failure_nodeids.append(nodeid)

    for memory in memory_by_worker.values():
        memory.pop("_previous_rss_kb", None)
    largest_rss_increases.sort(key=lambda value: int(value["increase_rss_kb"]), reverse=True)

    return {
        "path": str(path),
        "events": len(events),
        "started": started,
        "finished": finished,
        "unfinished": sorted(unfinished.values(), key=lambda value: (value["worker"], value["nodeid"])),
        "last_by_worker": dict(sorted(last_by_worker.items())),
        "failure_report_count": len(failures),
        "failure_nodeid_count": len(failure_nodeids),
        "failure_nodeids": failure_nodeids,
        "failures": failures[:50],
        "memory_by_worker": dict(sorted(memory_by_worker.items())),
        "largest_rss_increases": largest_rss_increases[:25],
    }


def compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Return complete failure/crash evidence without verbose tracebacks."""
    keys = (
        "path",
        "events",
        "started",
        "finished",
        "failure_report_count",
        "failure_nodeid_count",
        "failure_nodeids",
        "unfinished",
        "last_by_worker",
        "memory_by_worker",
        "largest_rss_increases",
    )
    return {key: summary[key] for key in keys}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    verbose = "--verbose" in args
    positional = [arg for arg in args if arg != "--verbose"]
    path = Path(positional[0]) if positional else Path(DEFAULT_TRACE_LOG)
    summary = summarize_log(path)
    output = summary if verbose else compact_summary(summary)
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
