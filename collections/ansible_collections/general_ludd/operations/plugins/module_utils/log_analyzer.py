"""Bounded, deterministic log analysis for the operations collection."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

_SEVERITY_RE = re.compile(
    r"(?i)\b(CRITICAL|FATAL|ERROR|EXCEPTION|WARNING|WARN|INFO|DEBUG|TRACE)\b"
)
_TIMESTAMP_RES = (
    re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)"),
    re.compile(r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})"),
    re.compile(r"\[(\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]"),
)
_CATEGORY_RE = re.compile(
    r"(?i)\[([a-z_]+(?: [a-z_]+)*)\]|"
    r"(?:\b(?:from|at)\s+(\w+(?:\.\w+)*))"
)
_ERROR_RE = re.compile(r"(?i)\b(ERROR|CRITICAL|FATAL|EXCEPTION|Traceback)")


def discover_logs(log_dir: str, glob_pattern: str, max_files: int = 1000) -> list[Path]:
    """Return a sorted, bounded list below ``log_dir`` without traversal."""
    root = Path(log_dir)
    if not root.is_dir():
        return []
    pattern = Path(glob_pattern).name
    if not pattern or pattern in {".", ".."}:
        raise ValueError("glob pattern must select files")
    return [path for path in sorted(root.glob(pattern)) if path.is_file()][
        :max_files
    ]


def parse_log_lines(content: str) -> list[dict[str, Any]]:
    """Normalize non-empty log lines into stable evidence records."""
    entries: list[dict[str, Any]] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        severity_match = _SEVERITY_RE.search(line)
        timestamp = ""
        for pattern in _TIMESTAMP_RES:
            match = pattern.search(line)
            if match:
                timestamp = match.group(1)
                break
        category_match = _CATEGORY_RE.search(line)
        category = ""
        if category_match:
            category = next(
                (group for group in category_match.groups() if group),
                "",
            ).strip().lower()
        entries.append(
            {
                "raw": line,
                "severity": severity_match.group(1).upper()
                if severity_match
                else "UNKNOWN",
                "timestamp": timestamp,
                "category": category,
                "is_error": bool(_ERROR_RE.search(line)),
                "length": len(line),
            }
        )
    return entries


def cluster_errors(
    entries: list[dict[str, Any]],
    window_seconds: int = 300,
    min_size: int = 2,
) -> list[dict[str, Any]]:
    """Group errors by severity/category with a stable bounded sample."""
    groups: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        if entry["is_error"]:
            groups[(str(entry["severity"]), str(entry["category"]))].append(entry)
    clusters: list[dict[str, Any]] = []
    for (severity, category), group in sorted(groups.items()):
        if len(group) < min_size:
            continue
        clusters.append(
            {
                "cluster_id": len(clusters) + 1,
                "severity": severity,
                "category": category,
                "count": len(group),
                "sample_lines": [str(item["raw"]) for item in group[:5]],
                "window_seconds_applied": window_seconds,
            }
        )
    return clusters


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def analyze(
    log_dir: str,
    glob_pattern: str,
    output_dir: str,
    *,
    error_threshold: float = 0.1,
    cluster_window: int = 300,
    min_cluster_size: int = 2,
    max_files: int = 1000,
    max_bytes_per_file: int = 10_000_000,
) -> dict[str, Any]:
    """Analyze bounded local logs and atomically publish JSON/Markdown reports."""
    files = discover_logs(log_dir, glob_pattern, max_files=max_files)
    entries: list[dict[str, Any]] = []
    for path in files:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            entries.extend(parse_log_lines(handle.read(max_bytes_per_file)))
    clusters = cluster_errors(
        entries,
        window_seconds=cluster_window,
        min_size=min_cluster_size,
    )
    error_count = sum(1 for entry in entries if entry["is_error"])
    total_lines = len(entries)
    density = error_count / max(total_lines, 1)
    result: dict[str, Any] = {
        "verdict": "anomalies_detected"
        if density > error_threshold or clusters
        else "clean",
        "files_analysed": len(files),
        "total_lines": total_lines,
        "error_lines": error_count,
        "error_density": round(density, 4),
        "error_threshold": error_threshold,
        "error_clusters": clusters,
        "cluster_count": len(clusters),
    }
    output = Path(output_dir)
    _atomic_write(
        output / "log_analysis_result.json",
        json.dumps(result, indent=2, sort_keys=True) + "\n",
    )
    markdown = [
        "# Log Analysis Report",
        "",
        f"**Verdict:** {str(result['verdict']).upper()}",
        f"**Files analysed:** {len(files)}",
        f"**Total lines:** {total_lines}",
        f"**Error lines:** {error_count}",
        f"**Error density:** {density:.2%}",
        "",
        "## Error Clusters",
        "",
    ]
    markdown.extend(
        f"- [{cluster['severity']}] {cluster['category'] or '?'}: "
        f"{cluster['count']} occurrences"
        for cluster in clusters
    )
    if not clusters:
        markdown.append("*No error clusters detected.*")
    _atomic_write(output / "log_analysis_report.md", "\n".join(markdown) + "\n")
    return result


__all__ = ["analyze", "cluster_errors", "discover_logs", "parse_log_lines"]
