#!/usr/bin/env python3
"""verify_coverage — run pytest-cov on generated test files, verify thresholds.

Usage:
    python verify_coverage.py --test-dir <dir> --source-module <path> --output <json>
    python verify_coverage.py --test-dir <dir> --source-module <path> --output <json> \\
        --scenarios-file scenarios.json --symbols-file module_symbols.json

Runs pytest --cov on generated test files against the source module, checks
coverage threshold, parses the resulting XML/JSON report, identifies uncovered
code paths, cross-references them with scenario coverage_targets, and writes
a structured coverage report including a ``gap_report`` section.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _find_test_files(test_dir: Path, prefix: str) -> list[str]:
    if not test_dir.is_dir():
        return []
    return sorted(
        str(p) for p in test_dir.glob(f"{prefix}*.py") if p.is_file()
    )


# ── Coverage report parsers ────────────────────────────────────────────────

def _parse_coverage_json(cov_path: Path) -> dict:
    """Parse a coverage.py JSON report.

    Returns a normalized dict with shape::

        {"totals": {"percent_covered": float},
         "files": {<path>: {"executed_lines": [...], "missing_lines": [...]}}}

    Defaults to an empty report when the file is absent or unreadable.
    """
    empty = {
        "totals": {"percent_covered": 0.0},
        "files": {},
    }
    if not cov_path.is_file():
        return empty
    try:
        with open(cov_path) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return empty
    files: dict[str, dict] = {}
    for fpath, info in raw.get("files", {}).items():
        files[fpath] = {
            "executed_lines": list(info.get("executed_lines", []) or info.get("summary", {}).get("executed_lines", [])),
            "missing_lines": list(info.get("missing_lines", []) or []),
            "summary": dict(info.get("summary", {})),
        }
    return {
        "totals": raw.get("totals", {"percent_covered": 0.0}),
        "files": files,
    }


def _parse_coverage_xml(cov_path: Path) -> dict:
    """Parse a coverage.py XML (Clover-ish) report into the same shape as the JSON parser.

    Each ``<class filename="...">`` contributes ``executed_lines`` (hits>=1)
    and ``missing_lines`` (hits==0).
    """
    empty = {
        "totals": {"percent_covered": 0.0},
        "files": {},
    }
    if not cov_path.is_file():
        return empty
    try:
        tree = ET.parse(cov_path)
    except ET.ParseError:
        return empty
    root = tree.getroot()
    files: dict[str, dict] = {}
    total_executed = 0
    total_missing = 0
    for cls in root.iter("class"):
        fpath = cls.get("filename")
        if not fpath:
            continue
        executed: list[int] = []
        missing: list[int] = []
        for line in cls.iter("line"):
            try:
                number = int(line.get("number", "0"))
            except ValueError:
                continue
            try:
                hits = int(line.get("hits", "0"))
            except ValueError:
                hits = 0
            if hits > 0:
                executed.append(number)
                total_executed += 1
            else:
                missing.append(number)
                total_missing += 1
        files[fpath] = {
            "executed_lines": executed,
            "missing_lines": missing,
            "summary": {
                "covered_lines": len(executed),
                "missing_lines": len(missing),
            },
        }
    total_lines = total_executed + total_missing
    pct = round(100.0 * total_executed / total_lines, 4) if total_lines else 0.0
    return {
        "totals": {"percent_covered": pct},
        "files": files,
    }


# ── Symbol classification ──────────────────────────────────────────────────

def _classify_range(
    line_start: int, line_end: int,
    executed: set[int], missing: set[int],
) -> tuple[str, list[int]]:
    """Return (state, missing_lines_within_range).

    state is one of: ``covered`` (no missing lines in range),
    ``partial`` (some missing), ``missing`` (all missing / no executed).
    """
    span = list(range(line_start, line_end + 1))
    missing_in_range = sorted(ln for ln in span if ln in missing)
    executed_in_range = [ln for ln in span if ln in executed]
    if not missing_in_range:
        return ("covered", [])
    if executed_in_range:
        return ("partial", missing_in_range)
    return ("missing", missing_in_range)


def _identify_uncovered_symbols(
    symbols: dict, coverage: dict, source_file: str,
) -> dict[str, dict]:
    """Map module symbols to per-symbol coverage state.

    Returns ``{<symbol_name>: {coverage_state, line_range, missing_lines}}``
    for symbols that are NOT fully covered. Fully-covered symbols are omitted.
    Methods are keyed as ``ClassName.method``.
    """
    files = coverage.get("files", {})
    file_cov = files.get(source_file) or files.get(Path(source_file).name)
    if file_cov is None:
        executed: set[int] = set()
        missing: set[int] = set()
        no_data = True
    else:
        executed = set(file_cov.get("executed_lines", []))
        missing = set(file_cov.get("missing_lines", []))
        no_data = False

    result: dict[str, dict] = {}

    def _add(name: str, line_start: int, line_end: int) -> None:
        if no_data:
            result[name] = {
                "coverage_state": "missing",
                "line_range": [line_start, line_end],
                "missing_lines": list(range(line_start, line_end + 1)),
            }
            return
        state, missing_in_range = _classify_range(line_start, line_end, executed, missing)
        if state == "covered":
            return
        result[name] = {
            "coverage_state": state,
            "line_range": [line_start, line_end],
            "missing_lines": missing_in_range,
        }

    for fn in symbols.get("functions", []):
        try:
            ls = int(fn.get("line_start", 0))
            le = int(fn.get("line_end", ls))
        except (TypeError, ValueError):
            continue
        _add(fn.get("name", "<anon>"), ls, le)

    for cls in symbols.get("classes", []):
        cls_name = cls.get("name", "<anon>")
        for m in cls.get("methods", []):
            try:
                ls = int(m.get("line_start", 0))
                le = int(m.get("line_end", ls))
            except (TypeError, ValueError):
                continue
            _add(f"{cls_name}.{m.get('name', '<anon>')}", ls, le)

    return result


# ── Scenario coverage-target cross-reference ───────────────────────────────

def _cross_reference_targets(
    targets: list[str],
    uncovered_symbols: dict[str, dict],
    known_symbols: set[str] | None = None,
) -> dict[str, list[str]]:
    """Classify scenario ``coverage_targets`` against measured coverage.

    Returns ``{covered: [...], uncovered: [...], unresolved: [...]}``:
      * covered   — target symbol exists and was fully covered.
      * uncovered — target symbol exists but is partial or missing.
      * unresolved — target is not a known symbol at all (likely a bug in
        scenario generation).

    ``known_symbols`` is the full set of symbol names from module_symbols.json.
    When omitted, behavior degrades: unknown targets are classified as
    ``covered`` rather than ``unresolved`` (callers should always pass it).
    """
    covered: list[str] = []
    uncovered: list[str] = []
    unresolved: list[str] = []
    for t in targets:
        if t in uncovered_symbols or _resolve_symbol_alias(t, uncovered_symbols):
            uncovered.append(t)
        elif known_symbols is not None and t not in known_symbols:
            unresolved.append(t)
        else:
            covered.append(t)
    return {"covered": covered, "uncovered": uncovered, "unresolved": unresolved}


def _resolve_symbol_alias(target: str, uncovered: dict[str, dict]) -> bool:
    """Allow ``Worker.start`` to match either exact key or unqualified tail."""
    if target in uncovered:
        return True
    if "." in target:
        tail = target.rsplit(".", 1)[-1]
        if any(key.endswith("." + tail) for key in uncovered):
            return True
    return False


def _collect_known_symbol_names(symbols: dict) -> set[str]:
    """Return the set of all known function/method names from module symbols."""
    names: set[str] = set()
    for fn in symbols.get("functions", []):
        name = fn.get("name")
        if name:
            names.add(name)
    for cls in symbols.get("classes", []):
        cls_name = cls.get("name")
        if cls_name:
            names.add(cls_name)
            for m in cls.get("methods", []):
                mname = m.get("name")
                if mname:
                    names.add(f"{cls_name}.{mname}")
                    names.add(mname)
    return names


# ── Gap report builder ─────────────────────────────────────────────────────

def _build_gap_report(
    *,
    coverage_pct: float,
    threshold: int,
    uncovered_symbols: dict[str, dict],
    cross_reference: dict[str, list[str]],
) -> dict:
    """Produce a structured gap report.

    Keys:
      * overall_verdict   — ``meets_threshold`` | ``below_threshold``.
      * coverage_gap_pp   — percentage points below threshold (0 if met).
      * missing_symbols   — symbols with zero measured coverage.
      * partial_symbols   — symbols with some uncovered lines.
      * covered_targets   — scenario coverage_targets fully exercised.
      * uncovered_targets — scenario coverage_targets not fully exercised.
      * unresolved_targets — scenario coverage_targets with no known symbol.
      * suggested_scenarios — one entry per missing symbol proposing a follow-up.
    """
    missing = sorted(
        name for name, info in uncovered_symbols.items()
        if info.get("coverage_state") == "missing"
    )
    partial = sorted(
        name for name, info in uncovered_symbols.items()
        if info.get("coverage_state") == "partial"
    )
    gap_pp = max(0.0, round(threshold - coverage_pct, 1))
    verdict = "meets_threshold" if coverage_pct >= threshold else "below_threshold"

    suggestions = [
        {
            "target": name,
            "kind": "missing",
            "line_range": uncovered_symbols[name].get("line_range"),
            "rationale": (
                f"Symbol '{name}' has no exercised lines; generate a scenario "
                f"that invokes it directly to lift coverage."
            ),
        }
        for name in missing
    ]
    for name in partial:
        suggestions.append({
            "target": name,
            "kind": "partial",
            "missing_lines": uncovered_symbols[name].get("missing_lines", []),
            "rationale": (
                f"Symbol '{name}' is partially covered; add assertions "
                f"exercising branches at the listed lines."
            ),
        })

    prioritized = prioritize_scenarios(suggestions)

    return {
        "overall_verdict": verdict,
        "coverage_gap_pp": gap_pp,
        "missing_symbols": missing,
        "partial_symbols": partial,
        "covered_targets": list(cross_reference.get("covered", [])),
        "uncovered_targets": list(cross_reference.get("uncovered", [])),
        "unresolved_targets": list(cross_reference.get("unresolved", [])),
        "suggested_scenarios": suggestions,
        "prioritized_scenarios": prioritized,
    }


# ── Coverage gap heatmap ────────────────────────────────────────────────────

_GAP_GLYPHS = {"covered": "covered", "partial": "partial", "missing": "missing"}


def coverage_gap_heatmap(modules: list[dict]) -> list[dict]:
    """Build a per-module, per-symbol visual representation of coverage gaps.

    Each entry in ``modules`` is expected to have the shape::

        {"name": <module path>,
         "coverage_pct": <float>,
         "symbols": [{"name": <str>, "state": "covered|partial|missing",
                      "missing_lines": [<int>, ...]}]}

    Returns one row per module with ``cells`` mapping each symbol to a glyph
    state plus a ``missing_count``. Symbols lacking an explicit ``state``
    default to ``"missing"`` (a symbol with no measured coverage data is
    treated as uncovered, matching ``_identify_uncovered_symbols`` semantics).
    """
    rows: list[dict] = []
    for mod in modules:
        cells: list[dict] = []
        for sym in mod.get("symbols", []):
            state = sym.get("state") or "missing"
            glyph = _GAP_GLYPHS.get(state, "missing")
            missing_lines = sym.get("missing_lines", []) or []
            cells.append({
                "symbol": sym.get("name", "<anon>"),
                "glyph": glyph,
                "missing_count": len(missing_lines),
            })
        rows.append({
            "module": mod.get("name", "<unknown>"),
            "coverage_pct": mod.get("coverage_pct", 0.0),
            "cells": cells,
        })
    return rows


_GAP_RENDER_CHARS = {"covered": ".", "partial": "~", "missing": "X"}


def render_heatmap(heatmap: list[dict]) -> str:
    """Render a heatmap produced by :func:`coverage_gap_heatmap` as an ASCII grid.

    Each module becomes one row; symbols are columns. The cell character is
    ``.`` (covered), ``~`` (partial) or ``X`` (missing). A legend is prepended.
    """
    if not heatmap:
        return "(no modules)\n"
    lines = ["coverage heatmap:  . covered  ~ partial  X missing", ""]
    for row in heatmap:
        mod = row.get("module", "<unknown>")
        pct = row.get("coverage_pct", 0.0)
        cells = row.get("cells", [])
        if not cells:
            lines.append(f"{mod}  ({pct}%):  (no symbols)")
            continue
        glyphs = "".join(_GAP_RENDER_CHARS.get(c.get("glyph", "missing"), "X") for c in cells)
        names = "  ".join(str(c.get("symbol", "")) for c in cells)
        lines.append(f"{mod}  ({pct}%):")
        lines.append(f"    {glyphs}")
        lines.append(f"    {names}")
    return "\n".join(lines) + "\n"


# ── Scenario prioritization ─────────────────────────────────────────────────

_KIND_WEIGHTS = {"missing": 3.0, "partial": 1.0}
_PER_LINE_WEIGHT = 0.1


def prioritize_scenarios(gaps: list[dict]) -> list[dict]:
    """Rank coverage gaps into prioritized scenario recommendations.

    Each ``gap`` is expected to have ``target``, ``kind`` ("missing" or
    "partial"), ``missing_lines`` (list[int]) and ``line_range`` ([start, end]).
    Unknown kinds are treated as ``"partial"``.

    The priority score blends:
      * kind weight — ``missing`` (3.0) outranks ``partial`` (1.0);
      * per-missing-line weight (0.1) — larger gaps rank higher;
      * span weight — a wider ``line_range`` implies more uncovered surface.

    Ties are broken alphabetically by ``target`` so ordering is deterministic.
    """
    scored: list[tuple[float, str, dict]] = []
    for gap in gaps:
        kind = gap.get("kind") or "partial"
        kind_weight = _KIND_WEIGHTS.get(kind, _KIND_WEIGHTS["partial"])
        missing_lines = gap.get("missing_lines", []) or []
        line_range = gap.get("line_range") or [0, 0]
        try:
            span = max(0, int(line_range[1]) - int(line_range[0]) + 1)
        except (TypeError, ValueError, IndexError):
            span = 0
        score = kind_weight + _PER_LINE_WEIGHT * len(missing_lines) + 0.01 * span
        target = gap.get("target", "<anon>")
        rationale = (
            f"Target '{target}' is fully uncovered ({kind}); exercising it "
            f"would close {len(missing_lines)} missing line(s) over a {span}-line span."
            if kind == "missing"
            else f"Target '{target}' is partially covered ({kind}); "
            f"{len(missing_lines)} branch line(s) need additional assertions."
        )
        scored.append((score, target, {
            "target": target,
            "kind": kind,
            "priority_score": round(score, 3),
            "missing_lines": list(missing_lines),
            "line_range": list(line_range),
            "rationale": rationale,
        }))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [entry for _, _, entry in scored]


def _load_symbols(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {"functions": [], "classes": []}
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"functions": [], "classes": []}


def _load_scenario_targets(path: Path | None) -> list[str]:
    if path is None or not path.is_file():
        return []
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    targets = list(data.get("coverage_targets", []))
    for scen in data.get("scenarios", []):
        for t in scen.get("coverage_targets", []):
            if t not in targets:
                targets.append(t)
    return targets


def _normalize_source_key(source: Path, coverage_files: dict) -> str:
    """Pick the coverage-files key that corresponds to the source module."""
    sstr = str(source)
    if sstr in coverage_files:
        return sstr
    name = source.name
    if name in coverage_files:
        return name
    stem = source.stem
    for key in coverage_files:
        if Path(key).stem == stem:
            return key
    return sstr


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run pytest-cov, verify coverage thresholds, emit a gap report"
    )
    parser.add_argument("--test-dir", required=True, help="Directory with generated test files")
    parser.add_argument("--source-module", required=True, help="Source module to measure coverage for")
    parser.add_argument("--output", required=True, help="Path for coverage_report.json")
    parser.add_argument("--threshold", type=int, default=85, help="Coverage threshold percentage")
    parser.add_argument("--timeout", type=int, default=300, help="Pytest timeout per test")
    parser.add_argument("--test-file-prefix", default="test_e2e_generated_", help="Prefix for test file glob")
    parser.add_argument(
        "--scenarios-file",
        help="Path to validated_scenarios.json for coverage-target cross-referencing",
    )
    parser.add_argument(
        "--symbols-file",
        help="Path to module_symbols.json for per-symbol coverage classification",
    )

    args = parser.parse_args()

    test_dir = Path(args.test_dir)
    source = Path(args.source_module)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    symbols = _load_symbols(Path(args.symbols_file) if args.symbols_file else None)
    scenario_targets = _load_scenario_targets(
        Path(args.scenarios_file) if args.scenarios_file else None
    )

    test_files = _find_test_files(test_dir, args.test_file_prefix)
    if not test_files:
        report = {
            "module": str(source),
            "test_output_dir": str(test_dir),
            "coverage_percent": 0.0,
            "threshold": args.threshold,
            "verdict": "skip",
            "verdict_reason": "No generated test files found",
            "pytest_exit_code": -1,
            "coverage_targets": [],
            "gap_report": _build_gap_report(
                coverage_pct=0.0,
                threshold=args.threshold,
                uncovered_symbols={},
                cross_reference={"covered": [], "uncovered": [], "unresolved": scenario_targets},
            ),
            "status": "completed",
        }
        with open(output, "w") as f:
            json.dump(report, f, indent=2)
        print(json.dumps(report))
        sys.exit(0)

    cov_json_path = output.parent / ".coverage_raw.json"
    cov_xml_path = output.parent / ".coverage_raw.xml"
    cmd = [
        sys.executable, "-m", "pytest",
        *test_files,
        f"--cov={source}",
        f"--cov-report=json:{cov_json_path}",
        f"--cov-report=xml:{cov_xml_path}",
        "--cov-report=term",
        f"--cov-fail-under={args.threshold}",
        f"--timeout={args.timeout}",
        "-v",
        "--tb=short",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout + 60)
    pytest_passed = result.returncode == 0

    # Prefer JSON; fall back to XML if JSON absent (e.g. older pytest-cov).
    cov_data = _parse_coverage_json(cov_json_path)
    if not cov_data.get("files"):
        xml_data = _parse_coverage_xml(cov_xml_path)
        if xml_data.get("files"):
            cov_data = xml_data

    coverage_pct = round(
        cov_data.get("totals", {}).get("percent_covered", 0.0), 1
    )

    source_key = _normalize_source_key(source, cov_data.get("files", {}))
    uncovered_symbols = _identify_uncovered_symbols(symbols, cov_data, source_key)
    known_symbols = _collect_known_symbol_names(symbols)
    cross_ref = _cross_reference_targets(
        scenario_targets, uncovered_symbols, known_symbols=known_symbols,
    )
    gap_report = _build_gap_report(
        coverage_pct=coverage_pct,
        threshold=args.threshold,
        uncovered_symbols=uncovered_symbols,
        cross_reference=cross_ref,
    )

    verdict = "pass" if pytest_passed else "fail"
    reason = (
        f"All tests pass. Coverage {coverage_pct}% meets threshold {args.threshold}%."
        if pytest_passed
        else f"pytest failed with rc={result.returncode}. Coverage {coverage_pct}% vs threshold {args.threshold}%."
    )

    report = {
        "module": str(source),
        "test_output_dir": str(test_dir),
        "coverage_percent": coverage_pct,
        "threshold": args.threshold,
        "verdict": verdict,
        "verdict_reason": reason,
        "pytest_exit_code": result.returncode,
        "coverage_targets": scenario_targets,
        "gap_report": gap_report,
        "pytest_output_tail": result.stdout[-2000:] if result.stdout else "",
        "status": "completed",
    }

    with open(output, "w") as f:
        json.dump(report, f, indent=2)

    pytest_log = output.parent / "pytest_output.log"
    with open(pytest_log, "w") as f:
        f.write(result.stdout or "")
        f.write("\n")
        f.write(result.stderr or "")

    print(json.dumps(report))


if __name__ == "__main__":
    main()
