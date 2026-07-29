#!/usr/bin/env python3
"""Report executable line gaps from an existing Cobertura coverage XML file."""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CoverageGap:
    """Coverage details for one file below the requested threshold."""

    filename: str
    percentage: float
    covered_lines: int
    total_lines: int
    missing_lines: tuple[int, ...]


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", type=Path, default=Path("coverage.xml"))
    parser.add_argument("--threshold", type=float, default=75.0)
    args = parser.parse_args()

    if not args.xml.is_file():
        parser.error(f"coverage XML does not exist: {args.xml}")
    gaps = summarize_classes(args.xml, args.threshold)
    print(
        f"coverage-gap-lines threshold={args.threshold:.1f} "
        f"files_below={len(gaps)} xml={args.xml}"
    )
    for gap in gaps:
        print(
            f"{gap.percentage:5.1f}% {gap.covered_lines}/{gap.total_lines} "
            f"{gap.filename} missing={compress_ranges(gap.missing_lines)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
