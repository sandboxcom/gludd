#!/usr/bin/env python3
"""Spec deduplication tool for BEHAVIORAL_SPECS.md.

Parses BEHAVIORAL_SPECS.md, extracts all specs with IDs/titles/bodies,
computes Jaccard similarity between spec bodies, flags duplicates (>80%
similarity), and outputs a report with recommendations.

Usage:
    python spec_deduplicator.py                        # print report
    python spec_deduplicator.py --deduplicate           # report + deduplicate the specs file
    python spec_deduplicator.py --deduplicate --dry-run # show what would be removed
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parent.parent
SPECS_PATH = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"

# Regex to match a spec header: ### X01 — Title
SPEC_HEADER_RE = re.compile(
    r"^###\s+([A-Z]\d{2,3})\s+[:—\-]\s+(.+)$"
)

# Matches the body of the spec (everything between header and **Enforcement:**
SPEC_BODY_RE = re.compile(r"^### .+?\n(.+?)\n\*\*Enforcement:", re.MULTILINE | re.DOTALL)


@dataclass
class Spec:
    spec_id: str
    title: str
    body: str   # The behavioral invariant text, without enforcement/test lines
    enforcement: str  # The enforcement mechanism
    test: str   # The test name
    start_line: int
    end_line: int

    @property
    def body_hash(self) -> str:
        return hashlib.sha256(self.body.strip().encode()).hexdigest()[:16]

    @property
    def group(self) -> str:
        return re.match(r"^[A-Z]+", self.spec_id).group()


def parse_specs(filepath: Path | str) -> list[Spec]:
    """Parse BEHAVIORAL_SPECS.md into a list of Spec objects."""
    text = Path(filepath).read_text()
    lines = text.split("\n")

    specs: list[Spec] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = SPEC_HEADER_RE.match(line)
        if not m:
            i += 1
            continue

        spec_id = m.group(1)
        title = m.group(2).strip()
        start_line = i + 1

        # Collect body lines (until **Enforcement:**)
        body_lines = []
        j = i + 1
        while j < len(lines):
            if lines[j].startswith("**Enforcement:**"):
                break
            if lines[j].startswith("###") or lines[j].startswith("## "):
                break
            if lines[j].strip():
                body_lines.append(lines[j].strip())
            j += 1

        body = " ".join(body_lines)

        # Collect enforcement line
        enforcement = ""
        if j < len(lines) and lines[j].startswith("**Enforcement:**"):
            enforcement = lines[j].replace("**Enforcement:**", "").strip()
            j += 1

        # Collect test line
        test = ""
        if j < len(lines) and lines[j].startswith("**Test:**"):
            test = lines[j].replace("**Test:**", "").strip()
            j += 1

        # Find end line (next ### or ##  or end of file)
        end_line = j
        while end_line < len(lines):
            if lines[end_line].startswith("###") or lines[end_line].startswith("## "):
                if lines[end_line].startswith("### "):
                    break
            end_line += 1
        if end_line >= len(lines):
            end_line = len(lines)

        specs.append(Spec(
            spec_id=spec_id,
            title=title,
            body=body,
            enforcement=enforcement,
            test=test,
            start_line=start_line,
            end_line=end_line,
        ))
        i = j

    return specs


def jaccard_similarity(a: str, b: str) -> float:
    """Compute Jaccard similarity between two strings based on word sets."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a and not words_b:
        return 1.0
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def find_duplicates(specs: list[Spec], threshold: float = 0.80) -> list[tuple[Spec, Spec, float]]:
    """Find pairs of specs with body similarity above threshold.

    Groups specs by body_hash first (O(1) exact match), then checks
    Jaccard similarity for close but not exact matches.
    """
    # Fast path: exact body hash match = 1.0 similarity
    by_hash: dict[str, list[Spec]] = defaultdict(list)
    for s in specs:
        by_hash[s.body_hash].append(s)

    duplicates: list[tuple[Spec, Spec, float]] = []

    # Report exact duplicates (same body hash)
    for h, group in by_hash.items():
        if len(group) > 1:
            # All specs with same hash are 1.0 duplicates
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    duplicates.append((group[i], group[j], 1.0))

    # Also check near-duplicates (body text slightly different but >80% similar)
    # Only check across groups to find cross-group overlap
    by_group: dict[str, list[Spec]] = defaultdict(list)
    for s in specs:
        by_group[s.group].append(s)

    # Within-group near-duplicate check (skip exact-match groups already handled)
    group_keys = sorted(by_group.keys())
    for g in group_keys:
        group_specs = by_group[g]
        for i in range(len(group_specs)):
            for j in range(i + 1, len(group_specs)):
                sim = jaccard_similarity(group_specs[i].body, group_specs[j].body)
                if sim > threshold and sim < 0.99:  # < 0.99 to skip exact matches
                    duplicates.append((group_specs[i], group_specs[j], sim))

    return duplicates


def compute_stats(specs: list[Spec]) -> dict:
    """Compute statistics about specs."""
    total = len(specs)
    by_group = defaultdict(int)
    by_hash = defaultdict(int)
    for s in specs:
        by_group[s.group] += 1
        by_hash[s.body_hash] += 1

    unique_bodies = len(by_hash)
    duplicate_hashes = sum(1 for h, c in by_hash.items() if c > 1)
    duplicate_specs = sum(c for c in by_hash.values() if c > 1)

    return {
        "total_specs": total,
        "total_groups": len(by_group),
        "unique_bodies": unique_bodies,
        "body_hashes_with_duplicates": duplicate_hashes,
        "specs_sharing_body": duplicate_specs,
        "by_group": dict(by_group),
    }


def generate_report(specs: list[Spec], threshold: float = 0.80) -> str:
    """Generate a human-readable dedup report."""
    stats = compute_stats(specs)
    duplicates = find_duplicates(specs, threshold)

    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("  BEHAVIORAL SPECS DEDUPLICATION REPORT")
    lines.append("=" * 70)
    lines.append(f"  Total specs:            {stats['total_specs']}")
    lines.append(f"  Unique body texts:      {stats['unique_bodies']}")
    lines.append(f"  Specs sharing bodies:   {stats['specs_sharing_body']}")
    lines.append(f"  Body hashes with dups:  {stats['body_hashes_with_duplicates']}")
    lines.append(f"  Similarity threshold:   {threshold:.0%}")
    lines.append("")

    # Group counts
    lines.append("  Specs per group:")
    for group, count in sorted(stats["by_group"].items()):
        lines.append(f"    {group}: {count}")
    lines.append("")

    # Duplicate clusters by body hash
    lines.append("-" * 70)
    lines.append("  EXACT DUPLICATES (same body text)")
    lines.append("-" * 70)

    # Group exact dups by body hash
    by_hash: dict[str, list[Spec]] = defaultdict(list)
    for s in specs:
        by_hash[s.body_hash].append(s)

    exact_dup_hashes = [(h, group) for h, group in by_hash.items() if len(group) > 1]
    # Sort by group size descending
    exact_dup_hashes.sort(key=lambda x: len(x[1]), reverse=True)

    total_exact_dup_specs = 0
    for h, group in exact_dup_hashes:
        sample_body = group[0].body[:80]
        ids = sorted(s.spec_id for s in group)
        lines.append(f"\n  Body hash {h} ({len(group)} specs): \"{sample_body}...\"")
        lines.append(f"    Spec IDs: {', '.join(ids[:20])}")
        if len(ids) > 20:
            lines.append(f"    ... and {len(ids) - 20} more")
        lines.append(f"    Recommendation: KEEP {ids[0]}, REMOVE {len(group) - 1} duplicates")
        total_exact_dup_specs += len(group) - 1

    lines.append("")
    lines.append(f"  Total exact duplicates removable: {total_exact_dup_specs}")
    lines.append(f"  Specs after exact dedup: {stats['total_specs'] - total_exact_dup_specs}")
    lines.append("")

    # Near-duplicate pairs (>80% but <100%)
    if duplicates:
        lines.append("-" * 70)
        lines.append("  NEAR-DUPLICATES (>80% similarity)")
        lines.append("-" * 70)
        for a, b, sim in sorted(duplicates, key=lambda x: -x[2])[:50]:
            lines.append(f"  {a.spec_id} ↔ {b.spec_id}: {sim:.1%} similarity")
            lines.append(f"    A: {a.body[:100]}")
            lines.append(f"    B: {b.body[:100]}")
    else:
        lines.append("  No near-duplicates found beyond exact matches.")

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


def deduplicate_specs(
    specs: list[Spec],
    filepath: str,
    threshold: float = 0.80,
    dry_run: bool = False,
) -> int:
    """Remove duplicate specs and write back the file.

    Strategy: For specs with identical body text within the same group,
    keep one representative and merge enforcement mechanisms from all.
    """
    text_lines = Path(filepath).read_text().split("\n")

    # Group specs by (body_hash, group)
    by_body_group: dict[tuple[str, str], list[Spec]] = defaultdict(list)
    for s in specs:
        by_body_group[(s.body_hash, s.group)].append(s)

    # Identify duplicate clusters
    duplicate_clusters: dict[tuple[str, str], list[Spec]] = {}
    for (h, g), cluster in by_body_group.items():
        if len(cluster) > 1:
            duplicate_clusters[(h, g)] = cluster

    # Track which specs to remove (all but the first in each cluster)
    specs_to_remove: set[str] = set()
    merged_enforcements: dict[str, str] = {}  # spec_id -> merged enforcement

    for (h, g), cluster in duplicate_clusters.items():
        keeper = cluster[0]
        removed = cluster[1:]

        # Merge enforcement mechanisms from all specs
        all_enforcements = [s.enforcement for s in cluster if s.enforcement]
        merged = "; ".join(sorted(set(all_enforcements)))

        for s in cluster:
            merged_enforcements[s.spec_id] = merged

        for s in removed:
            specs_to_remove.add(s.spec_id)

    if dry_run:
        print(f"Would remove {len(specs_to_remove)} duplicate specs:")
        for sid in sorted(specs_to_remove):
            print(f"  {sid}")
        return len(specs_to_remove)

    if not specs_to_remove:
        print("No duplicates to remove.")
        return 0

    # Build new file: keep lines of non-removed specs, update enforcement on kept specs
    # Create a set of line ranges to remove
    remove_ranges: list[tuple[int, int]] = []
    kept_spec_ids = {s.spec_id for s in specs if s.spec_id not in specs_to_remove}

    for s in specs:
        if s.spec_id in specs_to_remove:
            remove_ranges.append((s.start_line, s.end_line))

    # Sort by start line descending so we can remove from end
    remove_ranges.sort(key=lambda x: -x[0])

    # Remove lines for duplicate specs
    for start, end in remove_ranges:
        # Remove lines (start_line is 1-indexed, we need 0-indexed)
        start_idx = start - 1
        end_idx = min(end, len(text_lines))
        del text_lines[start_idx:end_idx]

    # Update enforcement on kept specs to be merged
    # We need to re-find the enforcement lines in the modified text
    for s in specs:
        if s.spec_id in specs_to_remove:
            continue
        if s.spec_id not in merged_enforcements:
            continue
        if s.enforcement == merged_enforcements[s.spec_id]:
            continue

        # Find the spec in the new text
        header_pattern = f"### {s.spec_id} "
        for li in range(len(text_lines)):
            if text_lines[li].startswith(header_pattern):
                # Find the enforcement line
                for ej in range(li + 1, min(li + 10, len(text_lines))):
                    if text_lines[ej].startswith("**Enforcement:**"):
                        text_lines[ej] = f"**Enforcement:** {merged_enforcements[s.spec_id]}"
                        break
                break

    new_text = "\n".join(text_lines)
    Path(filepath).write_text(new_text)

    print(f"Removed {len(specs_to_remove)} duplicate specs.")
    print(f"Updated enforcement on {len(kept_spec_ids)} kept specs.")
    return len(specs_to_remove)


def main() -> None:
    parser = argparse.ArgumentParser(description="Deduplicate BEHAVIORAL_SPECS.md")
    parser.add_argument(
        "--specs-path",
        default=str(SPECS_PATH),
        help="Path to BEHAVIORAL_SPECS.md",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.80,
        help="Jaccard similarity threshold for flagging duplicates (default 0.80)",
    )
    parser.add_argument(
        "--deduplicate",
        action="store_true",
        help="Remove duplicates from the specs file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without modifying files",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output report as JSON",
    )
    args = parser.parse_args()

    if not os.path.exists(args.specs_path):
        print(f"Error: {args.specs_path} not found", file=sys.stderr)
        sys.exit(1)

    specs = parse_specs(args.specs_path)
    stats = compute_stats(specs)

    if args.json:
        import json
        duplicates = find_duplicates(specs, args.threshold)
        dup_map: list[dict] = []
        for a, b, sim in duplicates:
            dup_map.append({
                "spec_a": a.spec_id,
                "spec_b": b.spec_id,
                "similarity": round(sim, 4),
                "body_a": a.body[:120],
                "body_b": b.body[:120],
            })
        output = {
            "stats": stats,
            "duplicates_count": len(duplicates),
            "duplicates": dup_map,
        }
        print(json.dumps(output, indent=2, default=str))
        return

    if args.deduplicate or args.dry_run:
        removed = deduplicate_specs(
            specs, args.specs_path, args.threshold, dry_run=args.dry_run
        )
        print(f"\nBefore: {stats['total_specs']} specs")
        print(f"After:  {stats['total_specs'] - removed} specs")
        print(f"Delta:  -{removed}")
    else:
        print(generate_report(specs, args.threshold))


if __name__ == "__main__":
    main()
