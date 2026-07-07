#!/usr/bin/env python3
"""
gen_status_table.py — generate the README "Feature & Task Completion Status" table.

Reads docs/features.yml, verifies each evidence ref via FeatureVerifier, renders
a Markdown table per section, and writes it between STATUS-TABLE markers in README.md.

Usage:
    python3 scripts/gen_status_table.py [options]

Options:
    --write         Write generated table into README.md (between markers, in-place).
                    If markers are absent, finds the heading and wraps the section.
    --check         Exit nonzero if on-disk table between markers differs from freshly
                    generated. Does NOT write. Use as a gate / CI check.
    --fast          Skip running pytest for test: refs — instead check only that the
                    test FILE exists. Always used by make check-status-table.
                    Combine with --write to produce a CI-stable committed table.
    --out PATH      Also write the standalone generated block to this path.
    --manifest PATH Override the default manifest path (default: docs/features.yml).

Exit codes:
    0   Success / --check: table is current
    0   README has neither markers nor the heading — table intentionally absent
        (--write and --check become a no-op; --out / stdout still produce a block)
    1   --check: table is stale, or any generation error

Markers in README.md:
    <!-- STATUS-TABLE:START -->
    ...generated content (do not edit manually)...
    <!-- STATUS-TABLE:END -->

If the README intentionally has no status table (markers absent AND no
"## Feature & Task Completion Status" heading), --write and --check exit 0
without modifying or complaining about the README. This lets projects remove
the table without breaking CI.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

# Allow running as a script from the repo root without installing the package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:
    print("ERROR: PyYAML not installed — run: uv sync", file=sys.stderr)
    sys.exit(1)

from general_ludd.quality.feature_verifier import FeatureVerifier  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_START_MARKER = "<!-- STATUS-TABLE:START -->"
_END_MARKER = "<!-- STATUS-TABLE:END -->"

# Badge printed in the "Verified %" column
_STATUS_BADGE: dict[str, str] = {
    "verified": "✓",
    "implemented": "~",
    "requested": "✗",
    "regressed": "✗!",
}

# Human-readable label (shown in Evidence cell prefix)
_STATUS_LABEL: dict[str, str] = {
    "verified": "PASS",
    "implemented": "PARTIAL",
    "requested": "PENDING",
    "regressed": "FAIL",
}

_HEADING_PATTERN = re.compile(
    r"(## Feature & Task Completion Status\n)",
    re.IGNORECASE,
)

# Generation-mode artifacts — differ between --fast and full mode but are NOT
# part of the table-body contract. ``--check`` normalizes these away so a
# README written in one mode passes a check run in the other (see
# ``_strip_mode_artifacts``).
_HEADER_LINE_PATTERN = re.compile(
    r"^[ \t]*\*\(auto-generated.*\)\*[ \t]*(?:\n|$)",
    re.MULTILINE,
)
_FILE_REFS_SUFFIX_PATTERN = re.compile(r" \*\(file-refs only\)\*")

# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


def _load_manifest(manifest_path: Path) -> list[dict[str, Any]]:
    """Load docs/features.yml; return the list of section dicts."""
    with manifest_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    sections: list[dict[str, Any]] = data.get("sections", [])
    return sections


# ---------------------------------------------------------------------------
# Fast-mode test-ref conversion
# ---------------------------------------------------------------------------


def _fast_check_test_ref(ref: str, repo_root: Path) -> tuple[bool, str]:
    """In --fast mode: check that the test file referenced by test: ref exists.

    Strips ``::ClassName::test_name`` selector to get the bare file path.
    Does NOT run pytest.  Returns (met, detail).
    """
    node_id = ref[len("test:"):]
    file_path = node_id.split("::")[0]  # strip node selectors
    target = (repo_root / file_path).resolve()
    if not target.is_relative_to(repo_root.resolve()):
        return False, f"test path escapes repo root: {file_path}"
    if target.exists():
        return True, f"test file present: {file_path}"
    return False, f"test file not found: {file_path}"


# ---------------------------------------------------------------------------
# Feature verification (wraps FeatureVerifier with fast-mode support)
# ---------------------------------------------------------------------------


def _verify_feature(
    verifier: FeatureVerifier,
    feature: dict[str, Any],
    repo_root: Path,
    fast: bool,
) -> dict[str, Any]:
    """Verify one feature dict.  Returns a FeatureVerifier-shaped result dict."""
    refs: list[str] = list(feature.get("evidence_refs", []))
    prior_status: str = str(feature.get("status", "requested")).lower()

    if not refs:
        # No refs → fail-closed: PENDING (never VERIFIED without evidence)
        return {
            "status": "requested",
            "verified_at": None,
            "evidence_results": {
                "all_met": False,
                "met_count": 0,
                "total_count": 0,
                "per_ref": [],
            },
        }

    if fast:
        # Fast mode: substitute test: refs with file-existence checks (no pytest).
        per_ref: list[dict[str, Any]] = []
        met_count = 0
        for ref in refs:
            if ref.startswith("test:"):
                met, detail = _fast_check_test_ref(ref, repo_root)
            else:
                met, detail = verifier._check_ref(ref)  # type: ignore[attr-defined]
            per_ref.append({"ref": ref, "met": met, "detail": detail})
            if met:
                met_count += 1
        total = len(per_ref)
        all_met = met_count == total and total > 0
        if all_met:
            new_status = "verified"
        elif met_count > 0:
            new_status = "implemented"
        else:
            new_status = "regressed" if prior_status in {"verified", "implemented"} else "requested"
        return {
            "status": new_status,
            "verified_at": None,
            "evidence_results": {
                "all_met": all_met,
                "met_count": met_count,
                "total_count": total,
                "per_ref": per_ref,
            },
        }
    else:
        # Full mode: hand off to FeatureVerifier (runs pytest for test: refs).
        feat = dict(feature)
        feat["evidence"] = refs
        return verifier.verify_feature(feat)


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


def _badge(status: str) -> str:
    return _STATUS_BADGE.get(status, "?")


def _render_section(
    section: dict[str, Any],
    results: list[dict[str, Any]],
    fast: bool,
) -> str:
    """Render one section as a Markdown table block."""
    lines: list[str] = []
    lines.append(f"\n### {section['title']}\n")
    lines.append("| Feature / Task | Verified % | Evidence |")
    lines.append("|---|---|---|")

    features: list[dict[str, Any]] = section.get("features", [])
    for feat, result in zip(features, results):
        pct = feat.get("pct", "?")
        pct_note = feat.get("pct_note", "")
        notes = feat.get("notes", "")
        title = feat.get("title", "")
        status = result.get("status", "requested")
        badge = _badge(status)
        label = _STATUS_LABEL.get(status, status.upper())

        pct_col = f"{badge} {pct}%{pct_note}"
        fast_suffix = " *(file-refs only)*" if fast and feat.get("evidence_refs") else ""
        ev_col = f"**{label}**{fast_suffix}: {notes}" if notes else f"**{label}**{fast_suffix}"

        lines.append(f"| {title} | {pct_col} | {ev_col} |")

    return "\n".join(lines)


def _generate_block(
    sections: list[dict[str, Any]],
    verifier: FeatureVerifier,
    repo_root: Path,
    fast: bool,
) -> str:
    """Generate the full table block that goes BETWEEN the markers."""
    parts: list[str] = []

    if fast:
        header = (
            "*(auto-generated with `--fast`; `test:` refs checked by file existence only —"
            " run `make gen-status-table` locally to verify tests pass)*\n"
        )
    else:
        header = (
            "*(auto-generated — do not edit between markers; regenerate with `make gen-status-table`)*\n"
        )
    parts.append(header)

    for section in sections:
        features: list[dict[str, Any]] = section.get("features", [])
        results = [
            _verify_feature(verifier, feat, repo_root, fast)
            for feat in features
        ]
        parts.append(_render_section(section, results, fast))

    parts.append("\n")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# README injection helpers
# ---------------------------------------------------------------------------


def _extract_between_markers(text: str) -> str | None:
    """Return the content between STATUS-TABLE markers, or None if absent."""
    start = text.find(_START_MARKER)
    end = text.find(_END_MARKER)
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start + len(_START_MARKER) : end]


def _strip_mode_artifacts(block: str) -> str:
    """Strip generation-mode artifacts so ``--check`` is mode-agnostic.

    The following differ between --fast and full mode but are NOT part of
    the table-body contract:

    1. The header line ("*(auto-generated ... )*") which differs in wording
       between --fast mode ("auto-generated with --fast; test: refs checked
       by file existence only ...") and full mode ("auto-generated — do not
       edit between markers; ...").
    2. The " *(file-refs only)*" suffix appended to evidence cells in --fast
       mode (absent in full mode).

    The table BODY — section headings, feature rows, status badges,
    percentages, evidence notes — is still strictly compared by --check.
    Only the two generation-mode annotations above are normalized away, so a
    README written in --fast mode passes a full-mode --check (the CI
    scenario) and vice versa.
    """
    without_header = _HEADER_LINE_PATTERN.sub("", block, count=1)
    return _FILE_REFS_SUFFIX_PATTERN.sub("", without_header)


def _readme_has_status_section(text: str) -> bool:
    """Return True if README has STATUS-TABLE markers OR the status-table heading.

    A README with neither is treated as "table intentionally removed" — callers
    should short-circuit with exit 0 rather than failing.
    """
    if _START_MARKER in text and _END_MARKER in text:
        return True
    if _HEADING_PATTERN.search(text):
        return True
    return False


def _inject_into_readme(readme_path: Path, block: str) -> str:
    """Return updated README content with *block* between STATUS-TABLE markers.

    Strategy:
    1. Markers already present → replace content between them.
    2. Markers absent + heading found → insert markers immediately after the
       heading line, replacing existing section content up to the next ## heading.
    3. Neither found → raise ValueError.
    """
    text = readme_path.read_text(encoding="utf-8")

    start_idx = text.find(_START_MARKER)
    end_idx = text.find(_END_MARKER)

    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        # Case 1: markers present — replace content between them.
        return (
            text[: start_idx + len(_START_MARKER)]
            + "\n"
            + block
            + text[end_idx:]
        )

    # Case 2: markers absent — find heading and wrap the section.
    m = _HEADING_PATTERN.search(text)
    if m is None:
        raise ValueError(
            "README.md has neither STATUS-TABLE markers nor a "
            "'## Feature & Task Completion Status' heading. Cannot inject."
        )

    section_start = m.end()
    # Find the next same-level (##) heading after the section_start.
    next_sec = re.search(r"\n## ", text[section_start:])
    if next_sec:
        section_end = section_start + next_sec.start() + 1  # keep the \n before ##
    else:
        section_end = len(text)

    return (
        text[: section_start]
        + _START_MARKER
        + "\n"
        + block
        + _END_MARKER
        + "\n"
        + text[section_end:]
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the README Feature & Task Completion Status table.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write generated table into README.md between markers (in-place).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit nonzero if README.md table is stale vs freshly generated. Does not write.",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help=(
            "Skip running pytest for test: refs; check only that test FILE exists. "
            "Suitable for CI and pre-commit checks."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="PATH",
        help="Also write standalone generated block to this file.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        metavar="PATH",
        help="Override manifest path (default: <repo_root>/docs/features.yml).",
    )
    args = parser.parse_args(argv)

    repo_root = _REPO_ROOT
    manifest_path: Path = args.manifest or (repo_root / "docs" / "features.yml")
    readme_path = repo_root / "README.md"

    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    if not readme_path.exists():
        print(f"ERROR: README.md not found: {readme_path}", file=sys.stderr)
        return 1

    readme_text = readme_path.read_text(encoding="utf-8")

    # If the README intentionally has no status table (no markers AND no heading),
    # --write and --check are no-ops that exit 0. This lets projects remove the
    # table without breaking CI. Default mode (stdout) and --out still work.
    if (args.write or args.check) and not _readme_has_status_section(readme_text):
        print(
            "[gen-status-table] README.md has no STATUS-TABLE markers and no "
            "'## Feature & Task Completion Status' heading — table intentionally "
            "absent. Nothing to do.",
        )
        return 0

    # Load manifest and create verifier.
    try:
        sections = _load_manifest(manifest_path)
    except Exception as exc:
        print(f"ERROR: failed to load manifest {manifest_path}: {exc}", file=sys.stderr)
        return 1

    verifier = FeatureVerifier(repo_root=str(repo_root))

    # Generate the table block.
    try:
        block = _generate_block(sections, verifier, repo_root, fast=args.fast)
    except Exception as exc:
        print(f"ERROR: table generation failed: {exc}", file=sys.stderr)
        return 1

    # --out: write standalone output.
    if args.out:
        try:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(block, encoding="utf-8")
            print(f"[gen-status-table] wrote standalone table → {args.out}")
        except Exception as exc:
            print(f"ERROR: could not write --out {args.out}: {exc}", file=sys.stderr)
            return 1

    # --write: inject into README.md.
    if args.write:
        try:
            new_text = _inject_into_readme(readme_path, block)
            readme_path.write_text(new_text, encoding="utf-8")
            print("[gen-status-table] README.md updated with fresh status table.")
        except Exception as exc:
            print(f"ERROR: could not write README.md: {exc}", file=sys.stderr)
            return 1

    # --check: compare on-disk table vs freshly generated.
    if args.check:
        current_text = readme_path.read_text(encoding="utf-8")
        current_block = _extract_between_markers(current_text)
        if current_block is None:
            print(
                "ERROR: README.md has no STATUS-TABLE markers.\n"
                "  The status table has never been generated.\n"
                "  Fix: make gen-status-table",
                file=sys.stderr,
            )
            return 1
        if current_block.strip() != block.strip():
            # Before declaring stale, normalize generation-mode artifacts
            # (header line + `*(file-refs only)*` suffix) that differ between
            # --fast and full mode. The table BODY is the actual contract.
            disk_norm = _strip_mode_artifacts(current_block).strip()
            fresh_norm = _strip_mode_artifacts(block).strip()
            if disk_norm == fresh_norm:
                print(
                    "[check-status-table] OK — README.md status table is current "
                    "(mode-agnostic: header/suffix differences ignored).",
                )
                return 0
            print(
                "ERROR: README.md status table is stale (on-disk body differs from freshly generated).\n"
                "  Fix: make gen-status-table",
                file=sys.stderr,
            )
            # Show a diff summary (first differing line) on the NORMALIZED
            # bodies so the message reflects the real contract violation,
            # not a harmless mode-specific annotation.
            on_disk_lines = disk_norm.splitlines()
            fresh_lines = fresh_norm.splitlines()
            for i, (a, b) in enumerate(zip(on_disk_lines, fresh_lines)):
                if a != b:
                    print(
                        f"  First diff at line {i+1}:\n"
                        f"    disk: {a[:120]!r}\n"
                        f"    new:  {b[:120]!r}",
                        file=sys.stderr,
                    )
                    break
            else:
                n_disk = len(on_disk_lines)
                n_fresh = len(fresh_lines)
                print(
                    f"  Line count differs: disk={n_disk}, fresh={n_fresh}",
                    file=sys.stderr,
                )
            return 1
        print("[check-status-table] OK — README.md status table is current.")
        return 0

    # Default (no --write, no --check, no --out): print to stdout.
    if not args.write and not args.check and args.out is None:
        print(block)

    return 0


if __name__ == "__main__":
    sys.exit(main())
