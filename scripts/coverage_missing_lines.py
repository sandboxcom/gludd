#!/usr/bin/env python3
"""Report executable line gaps from an existing Cobertura coverage XML file."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import cast


@dataclass(frozen=True)
class CoverageGap:
    """Coverage details for one file below the requested threshold."""

    filename: str
    percentage: float
    covered_lines: int
    total_lines: int
    missing_lines: tuple[int, ...]
    branch_percentage: float = 100.0
    covered_branches: int = 0
    total_branches: int = 0
    missing_branches: tuple[tuple[int, int], ...] = ()


def compress_ranges(lines: list[int] | tuple[int, ...]) -> str:
    """Render sorted line numbers as compact inclusive ranges."""

    if not lines:
        return "-"
    ordered = sorted(set(lines))
    ranges: list[str] = []
    start = previous = ordered[0]
    for line in ordered[1:]:
        if line == previous + 1:
            previous = line
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = line
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)


def summarize_classes(xml_path: Path, threshold: float) -> list[CoverageGap]:
    """Return every covered class whose executable-line percentage is too low."""

    root = ET.parse(xml_path).getroot()
    gaps: list[CoverageGap] = []
    for class_node in root.findall(".//class"):
        filename = class_node.attrib.get("filename", "")
        line_nodes = class_node.findall("./lines/line")
        if not filename or not line_nodes:
            continue
        line_hits = {
            int(node.attrib["number"]): int(node.attrib.get("hits", "0"))
            for node in line_nodes
        }
        total = len(line_hits)
        covered = sum(hits > 0 for hits in line_hits.values())
        percentage = round(100.0 * covered / total, 1)
        if percentage >= threshold:
            continue
        missing = tuple(line for line, hits in sorted(line_hits.items()) if hits == 0)
        gaps.append(
            CoverageGap(
                filename=filename,
                percentage=percentage,
                covered_lines=covered,
                total_lines=total,
                missing_lines=missing,
            )
        )
    return sorted(gaps, key=lambda gap: (gap.percentage, gap.filename))


def summarize_json(
    json_path: Path,
    threshold: float,
    source: Path,
) -> list[CoverageGap]:
    """Return line or branch gaps from one coverage.py JSON report."""
    payload = cast(
        dict[str, object],
        json.loads(json_path.read_text(encoding="utf-8")),
    )
    files = cast(dict[str, dict[str, object]], payload.get("files", {}))
    source_root = source.resolve()
    gaps: list[CoverageGap] = []
    for filename, details in files.items():
        try:
            relative = Path(filename).resolve().relative_to(source_root)
        except ValueError:
            continue
        summary = cast(dict[str, object], details.get("summary", {}))
        total_lines = cast(int, summary.get("num_statements", 0))
        covered_lines = cast(
            int,
            summary.get("covered_lines", summary.get("num_lines_covered", 0)),
        )
        total_branches = cast(int, summary.get("num_branches", 0))
        covered_branches = cast(int, summary.get("covered_branches", 0))
        line_percentage = (
            round(100.0 * covered_lines / total_lines, 1) if total_lines else 100.0
        )
        branch_percentage = (
            round(100.0 * covered_branches / total_branches, 1)
            if total_branches
            else 100.0
        )
        if line_percentage >= threshold and branch_percentage >= threshold:
            continue
        missing_lines = tuple(
            int(line) for line in cast(list[int], details.get("missing_lines", []))
        )
        missing_branches = tuple(
            (int(arc[0]), int(arc[1]))
            for arc in cast(list[list[int]], details.get("missing_branches", []))
        )
        gaps.append(
            CoverageGap(
                filename=str(relative),
                percentage=line_percentage,
                covered_lines=covered_lines,
                total_lines=total_lines,
                missing_lines=missing_lines,
                branch_percentage=branch_percentage,
                covered_branches=covered_branches,
                total_branches=total_branches,
                missing_branches=missing_branches,
            )
        )
    return sorted(
        gaps,
        key=lambda gap: (
            min(gap.percentage, gap.branch_percentage),
            gap.filename,
        ),
    )


def _format_arcs(arcs: tuple[tuple[int, int], ...]) -> str:
    """Render missing branch arcs in a compact stable form."""
    return ",".join(f"{source}->{destination}" for source, destination in arcs) or "-"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--xml", type=Path)
    inputs.add_argument("--json", type=Path)
    parser.add_argument("--threshold", type=float, default=75.0)
    parser.add_argument("--source", type=Path, default=Path("src/general_ludd"))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if args.limit < 0:
        parser.error("limit must be zero or a positive integer")
    input_path = args.json or args.xml or Path("coverage.xml")
    if not input_path.is_file():
        parser.error(f"coverage report does not exist: {input_path}")
    gaps = (
        summarize_json(input_path, args.threshold, args.source)
        if args.json is not None
        else summarize_classes(input_path, args.threshold)
    )
    displayed = gaps[: args.limit] if args.limit else gaps
    print(
        f"coverage-gap-lines threshold={args.threshold:.1f} "
        f"files_below={len(gaps)} displayed={len(displayed)} input={input_path}"
    )
    for gap in displayed:
        line = (
            f"{gap.percentage:5.1f}% {gap.covered_lines}/{gap.total_lines} "
            f"{gap.filename} missing={compress_ranges(gap.missing_lines)}"
        )
        if gap.total_branches:
            line += (
                f" branches={gap.branch_percentage:.1f}% "
                f"{gap.covered_branches}/{gap.total_branches} "
                f"missing-arcs={_format_arcs(gap.missing_branches)}"
            )
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
