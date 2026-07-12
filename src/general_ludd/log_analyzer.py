"""Log analysis module: ingestion, parsing, error clustering, COT logging, reports."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import cast

_SEVERITY_PATTERN = re.compile(
    r"(?i)\b(CRITICAL|FATAL|ERROR|EXCEPTION|WARNING|WARN|INFO|DEBUG|TRACE)\b"
)
_TIMESTAMP_PATTERNS = [
    re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2})?)"),
    re.compile(r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})"),
    re.compile(r"\[(\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]"),
]
_CATEGORY_PATTERN = re.compile(
    r"(?i)\[([a-z_]+(?: [a-z_]+)*)\]|"
    r"(?:\b(?:category|component|module|service|plugin)\S{0,2}\s*\S{0,2}\s*[=:]\s*\S+)|"
    r"(?:\b(?:from|at)\s+(\w+(?:\.\w+)*))"
)

_LOG_LEVEL_RANK = {
    "TRACE": 0, "DEBUG": 1, "INFO": 2, "WARN": 3, "WARNING": 3,
    "ERROR": 4, "EXCEPTION": 4, "FATAL": 5, "CRITICAL": 5,
}


def _parse_severity(line: str) -> str:
    m = _SEVERITY_PATTERN.search(line)
    return m.group(0).upper() if m else "UNKNOWN"


def _parse_timestamp(line: str) -> str:
    for pat in _TIMESTAMP_PATTERNS:
        m = pat.search(line)
        if m:
            return m.group(1)
    return ""


def _parse_category(line: str) -> str:
    m = _CATEGORY_PATTERN.search(line)
    if m:
        for g in m.groups():
            if g:
                return g.strip().lower()
    return ""


def _is_error_line(line: str) -> bool:
    return bool(re.search(r"(?i)\b(ERROR|CRITICAL|FATAL|EXCEPTION|Traceback)", line))


def discover_logs(log_dir: str, glob_pattern: str) -> list[Path]:
    p = Path(log_dir)
    if not p.is_dir():
        return []
    pattern = Path(glob_pattern).name if "/" in glob_pattern or "*" in glob_pattern else glob_pattern
    return sorted(p.glob(pattern))


def parse_log_lines(content: str) -> list[dict[str, object]]:
    entries = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        entries.append({
            "raw": line,
            "severity": _parse_severity(line),
            "timestamp": _parse_timestamp(line),
            "category": _parse_category(line),
            "is_error": _is_error_line(line),
            "length": len(line),
        })
    return entries


def cluster_errors(
    entries: list[dict[str, object]], window_seconds: int = 300, min_size: int = 2,
) -> list[dict[str, object]]:
    errors = [e for e in entries if e["is_error"]]

    groups = defaultdict(list)
    for e in errors:
        key = (e.get("severity", "?"), e.get("category", "?"))
        groups[key].append(e)

    clusters = []
    cluster_id = 0
    for (severity, category), group in sorted(groups.items()):
        if len(group) < min_size:
            continue
        cluster_id += 1
        sample_lines = [g["raw"] for g in group[:5]]
        clusters.append({
            "cluster_id": cluster_id,
            "severity": severity,
            "category": category,
            "count": len(group),
            "sample_lines": sample_lines,
            "window_seconds_applied": window_seconds,
        })
    return clusters


def generate_reports(
    entries: list[dict[str, object]],
    clusters: list[dict[str, object]],
    total_lines: int,
    files_analysed: int,
    error_threshold: float,
    output_dir: str,
) -> dict[str, object]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    error_count = sum(1 for e in entries if e["is_error"])
    error_density = error_count / max(total_lines, 1)
    verdict = "anomalies_detected" if error_density > error_threshold or clusters else "clean"

    result = {
        "verdict": verdict,
        "files_analysed": files_analysed,
        "total_lines": total_lines,
        "error_lines": error_count,
        "error_density": round(error_density, 4),
        "error_threshold": error_threshold,
        "error_clusters": clusters,
        "cluster_count": len(clusters),
    }

    json_path = out / "log_analysis_result.json"
    json_path.write_text(json.dumps(result, indent=2))

    md_lines = [
        "# Log Analysis Report",
        "",
        f"**Verdict:** {verdict.upper()}",
        f"**Files analysed:** {files_analysed}",
        f"**Total lines:** {total_lines}",
        f"**Error lines:** {error_count}",
        f"**Error density:** {error_density:.2%}",
        f"**Error clusters:** {len(clusters)}",
        "",
        "## Error Clusters",
        "",
    ]
    if not clusters:
        md_lines.append("*No error clusters detected.*")
    else:
        for c in sorted(clusters, key=lambda x: -int(cast("int", x["count"]))):
            md_lines.append(
                f"- [{c['severity']}] {c.get('category', '?')}: "
                f"{c['count']} occurrences"
            )
        md_lines.append("")
        md_lines.append("## Sample Lines")
        md_lines.append("")
        for c in sorted(clusters, key=lambda x: -int(cast("int", x["count"]))):
            md_lines.append(f"### Cluster #{c['cluster_id']}")
            for sl in cast("list[str]", c.get("sample_lines", []))[:3]:
                md_lines.append(f"    {sl}")
            md_lines.append("")

    md_path = out / "log_analysis_report.md"
    md_path.write_text("\n".join(md_lines))

    return result


def write_cot_log(cot_dir: str, analysis_result: dict[str, object]) -> Path:
    p = Path(cot_dir)
    p.mkdir(parents=True, exist_ok=True)
    cot = p / "log_analysis_cot.log"
    lines = [
        "=== Log Analysis Chain of Thought ===",
        "",
        f"Files analysed: {analysis_result['files_analysed']}",
        f"Total lines: {analysis_result['total_lines']}",
        f"Error lines: {analysis_result['error_lines']}",
        f"Error density: {analysis_result['error_density']:.4f}",
        f"Error threshold: {analysis_result['error_threshold']}",
        f"Error clusters: {analysis_result['cluster_count']}",
        f"Verdict: {analysis_result['verdict']}",
        "",
    ]
    for c in cast("list[dict[str, object]]", analysis_result.get("error_clusters", [])):
        lines.extend([
            f"Cluster #{c['cluster_id']}: [{c['severity']}] {c.get('category', '?')} "
            f"— {c['count']} occurrences",
            f"  Sample: {cast('list[str]', c.get('sample_lines', ['']))[0][:120]}",
            "",
        ])
    cot.write_text("\n".join(lines))
    return cot


def analyze(
    log_dir: str,
    glob_str: str,
    output_dir: str,
    error_threshold: float = 0.1,
    cluster_window: int = 300,
    min_cluster_size: int = 2,
) -> dict[str, object]:
    files = discover_logs(log_dir, glob_str)
    all_entries: list[dict[str, object]] = []
    for fp in files:
        try:
            content = fp.read_text()
        except Exception:
            content = ""
        all_entries.extend(parse_log_lines(content))

    total = max(len(all_entries), 1)
    files_count = len(files)
    clusters = cluster_errors(all_entries, window_seconds=cluster_window, min_size=min_cluster_size)
    result = generate_reports(
        all_entries, clusters, total, files_count, error_threshold, output_dir
    )
    write_cot_log(output_dir, result)
    return result
