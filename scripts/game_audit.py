#!/usr/bin/env python3
"""Game audit — run game-building tests and analyze observability data.

Produces a comprehensive report of:
- Token consumption per game
- Latency breakdown per phase (model_call, extract_code, ast_parse, game_verify)
- Pass/fail rates by verification check
- Improvement hints based on observed data

Usage:
    uv run python scripts/game_audit.py [--report-only]

    --report-only    Read existing .game-audit-report.json without running tests.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).parent.parent
_REPORT_PATH = _REPO_ROOT / ".game-audit-report.json"


def run_tests() -> int:
    """Run the game-building tests. Returns exit code."""
    result = subprocess.run(
        [
            "uv", "run", "python", "-m", "pytest",
            "tests/e2e/test_game_building_deepseek.py",
            "-v", "-s",
            "-k", "TestDeepSeekGameBuilding or TestGameBuildingGapAnalysis",
        ],
        cwd=_REPO_ROOT,
        capture_output=False,
    )
    return result.returncode


def load_report() -> dict[str, Any] | None:
    if not _REPORT_PATH.exists():
        print(f"Error: No report found at {_REPORT_PATH}")
        print("Run tests first with: uv run python scripts/game_audit.py")
        return None
    try:
        loaded = json.loads(_REPORT_PATH.read_text())
    except json.JSONDecodeError as exc:
        print(f"Error: Invalid report JSON: {exc}")
        return None
    if not isinstance(loaded, dict):
        print("Error: Report JSON must contain an object.")
        return None
    return {str(key): value for key, value in loaded.items()}


def print_header(title: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def print_separator() -> None:
    print(f"{'-' * 70}")


def analyze_report(report: dict[str, Any]) -> None:
    games = report.get("games", {})
    summary = report.get("summary", {})

    if not games:
        print("No game data in report. Run tests first.")
        return

    print_header("GAME AUDIT REPORT")
    print(f"Report generated: {report.get('report_generated', 'unknown')}")
    print(f"Games tested: {summary.get('total_games', len(games))}")

    # -------------------------------------------------------------------
    # 1. Token Analysis
    # -------------------------------------------------------------------
    print_header("TOKEN CONSUMPTION")

    total_in = summary.get("total_tokens_in", 0)
    total_out = summary.get("total_tokens_out", 0)
    print(f"  Total input tokens:  {total_in:,}")
    print(f"  Total output tokens: {total_out:,}")
    print(f"  Combined:            {total_in + total_out:,}")
    print()

    token_rows: list[tuple[str, int, int, int]] = []
    for game_id, data in games.items():
        tin = data.get("tokens_in", 0)
        tout = data.get("tokens_out", 0)
        combined = tin + tout
        if tin == 0 and tout == 0:
            continue
        token_rows.append((game_id, tin, tout, combined))
    token_rows.sort(key=lambda r: r[3], reverse=True)

    print(f"  {'Game':<20} {'Input':>8} {'Output':>8} {'Total':>8} {'Share':>7}")
    print_separator()
    for game_id, tin, tout, combined in token_rows:
        share = (combined / max(total_in + total_out, 1)) * 100
        print(f"  {game_id:<20} {tin:>8,} {tout:>8,} {combined:>8,} {share:>6.1f}%")

    if token_rows:
        token_top = token_rows[0]
        token_bottom = token_rows[-1]
        print(f"\n  Most tokens:  {token_top[0]} ({token_top[3]:,} total)")
        print(f"  Fewest tokens: {token_bottom[0]} ({token_bottom[3]:,} total)")
        print(
            f"  Ratio (max/min): "
            f"{token_top[3]/max(token_bottom[3],1):.1f}x"
        )

    # -------------------------------------------------------------------
    # 2. Latency Analysis
    # -------------------------------------------------------------------
    print_header("LATENCY BREAKDOWN (ms)")

    phases = ["model_call", "extract_code", "ast_parse", "game_verify"]
    latency_rows: list[tuple[str, float, float, float, float, float]] = []
    for game_id, data in games.items():
        ph = data.get("phases", {})
        total_ms = sum(ph.get(p, 0) for p in phases)
        if total_ms == 0:
            continue
        latency_rows.append((
            game_id,
            ph.get("model_call", 0),
            ph.get("extract_code", 0),
            ph.get("ast_parse", 0),
            ph.get("game_verify", 0),
            total_ms,
        ))
    latency_rows.sort(key=lambda r: r[5], reverse=True)

    print(f"  {'Game':<20} {'Model':>8} {'Extract':>8} {'AST':>8} {'Verify':>8} {'Total':>8}")
    print_separator()
    for game_id, mc, ec, ap, gv, total in latency_rows:
        print(f"  {game_id:<20} {mc:>8.0f} {ec:>8.0f} {ap:>8.0f} {gv:>8.0f} {total:>8.0f}")

    if latency_rows:
        total_lat = summary.get("total_latency_ms", 0)
        print(f"\n  Total latency: {total_lat:,.0f} ms ({total_lat/1000:,.1f}s)")

        print("\n  Phase averages:")
        for phase in phases:
            vals = [
                float(r[phases.index(phase) + 1]) for r in latency_rows
            ]
            if vals:
                avg = sum(vals) / len(vals)
                pct = (sum(vals) / max(total_lat, 1)) * 100
                print(f"    {phase:<25} avg={avg:>8.0f} ms  ({pct:>5.1f}% of total)")

        latency_top = latency_rows[0]
        print(
            f"\n  Slowest game:  {latency_top[0]} "
            f"({latency_top[5]:,.0f} ms)"
        )
        latency_bottom = latency_rows[-1]
        print(
            f"  Fastest game:  {latency_bottom[0]} "
            f"({latency_bottom[5]:,.0f} ms)"
        )

    # -------------------------------------------------------------------
    # 3. Verification Results
    # -------------------------------------------------------------------
    print_header("VERIFICATION RESULTS")

    imported = summary.get("games_imported", 0)
    verified = summary.get("games_fully_verified", 0)
    total = summary.get("total_games", len(games))
    print(f"  Games imported:       {imported}/{total}")
    print(f"  Fully verified:       {verified}/{total}")
    print()

    print(f"  {'Game':<20} {'Imported':>9} {'Instanced':>10} {'Checks':>8} {'Status':>10}")
    print_separator()
    for game_id, data in sorted(games.items()):
        imp = "YES" if data.get("imported") else "NO"
        ins = "YES" if data.get("instantiated") else "NO"
        p = data.get("checks_passed", 0)
        t = data.get("checks_total", 0)
        checks = f"{p}/{t}"
        if not data.get("imported"):
            status = "IMPORT_ERR"
        elif not data.get("instantiated"):
            status = "INST_ERR"
        elif t > 0 and p == t:
            status = "PASS"
        elif t > 0 and p > 0:
            status = "PARTIAL"
        else:
            status = "NO_CHECKS"
        print(f"  {game_id:<20} {imp:>9} {ins:>10} {checks:>8} {status:>10}")

    # Per-check failure analysis
    check_failures: dict[str, list[str]] = {}
    for game_id, data in games.items():
        for check_id, check_data in data.get("checks", {}).items():
            if not check_data.get("passed"):
                check_failures.setdefault(check_id, []).append(game_id)

    if check_failures:
        print("\n  Failing checks by type:")
        for check_id, failing_games in sorted(check_failures.items(), key=lambda kv: -len(kv[1])):
            print(f"    {check_id}: {len(failing_games)} games ({', '.join(failing_games)})")

    # -------------------------------------------------------------------
    # 4. Errors
    # -------------------------------------------------------------------
    print_header("ERRORS")
    erring_games = [g for g in games.values() if g.get("errors")]
    if not erring_games:
        print("  No errors recorded.")
    else:
        print(f"  Games with errors: {len(erring_games)}/{total}")
        for game_id, data in sorted(games.items()):
            for err in data.get("errors", []):
                print(f"  [{game_id}] {err[:150]}")

    # -------------------------------------------------------------------
    # 5. Gaps (both main and persistence)
    # -------------------------------------------------------------------
    print_header("GAPS")
    all_gaps: list[dict[str, Any]] = []
    for data in games.values():
        for gap in data.get("gaps", []):
            all_gaps.append(gap)

    if not all_gaps:
        print("  No gaps recorded.")
    else:
        by_cat: dict[str, list[dict[str, Any]]] = {}
        for g in all_gaps:
            by_cat.setdefault(g.get("category", "unknown"), []).append(g)
        print(f"  Total gaps: {len(all_gaps)}")
        for cat, items in sorted(by_cat.items()):
            games_list = sorted(set(i.get("game", "") for i in items))
            print(f"    {cat}: {len(items)} gaps ({', '.join(games_list)})")

    # -------------------------------------------------------------------
    # 6. Improvement Hints
    # -------------------------------------------------------------------
    print_header("IMPROVEMENT HINTS")

    hints: list[str] = []

    # Token efficiency
    if total_in > 0 and len(token_rows) > 0:
        avg_tokens_in = total_in / len(token_rows)
        avg_tokens_out = total_out / len(token_rows)
        hints.append(
            f"Average tokens per game: {avg_tokens_in:,.0f} in / {avg_tokens_out:,.0f} out. "
            f"Consider prompt optimization for games consuming >2x average."
        )

    # High-variance token games
    if len(token_rows) >= 3:
        high = token_rows[0]
        low = token_rows[-1]
        if high[3] > low[3] * 3:
            hints.append(
                f"Token variance is high: {high[0]} uses {high[3]/(max(low[3],1)):.1f}x more tokens than "
                f"{low[0]}. Investigate whether {high[0]}'s prompt can be simplified."
            )

    # Latency bottlenecks
    if latency_rows:
        phase_avgs: dict[str, float] = {}
        for phase in phases:
            vals = [
                float(r[phases.index(phase) + 1]) for r in latency_rows
            ]
            if vals:
                phase_avgs[phase] = sum(vals) / len(vals)

        bottleneck = (
            max(phase_avgs, key=phase_avgs.__getitem__) if phase_avgs else ""
        )
        if bottleneck and phase_avgs.get(bottleneck, 0) > 0:
            recommendation = (
                "Consider caching or model selection changes."
                if bottleneck == "model_call"
                else "Consider optimizing the extraction/verification code."
            )
            hints.append(
                f"Primary latency bottleneck: '{bottleneck}' phase "
                f"({phase_avgs[bottleneck]:.0f} ms avg). "
                f"{recommendation}"
            )

    # Import failures are the critical blocker
    if imported < total:
        failures = [gid for gid, d in games.items() if not d.get("imported")]
        hints.append(
            f"{total - imported} games failed to import: {', '.join(failures)}. "
            f"Import failures block ALL downstream checks. Prioritize code extraction "
            f"robustness and prompt engineering for these games."
        )

    # Missing verification coverage
    if check_failures:
        top_failure = max(check_failures, key=lambda k: len(check_failures[k]))
        hints.append(
            f"'{top_failure}' check fails most often ({len(check_failures[top_failure])} games). "
            f"This verification may be too strict or the prompt doesn't emphasize it enough."
        )

    # Persistence hints
    for game_id, data in sorted(games.items()):
        if data.get("instantiated") and data.get("checks_passed", 0) == data.get("checks_total", 0):
            hints.append(
                f"GOOD: {game_id} passes all verifications — use as few-shot example for "
                f"struggling games."
            )

    # Output hints
    for i, hint in enumerate(hints, 1):
        print(f"  {i}. {hint}")

    if not hints:
        print("  No improvement hints — all metrics are nominal.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Game audit — analyze game-building observability")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Read existing report without running tests",
    )
    args = parser.parse_args()

    if not args.report_only:
        print("Running game-building tests...")
        print("(Set DEEPSEEK_API_KEY to enable actual model calls)")
        rc = run_tests()
        if rc != 0 and not args.report_only:
            print(f"\nTests exited with code {rc} — report may be incomplete.")
        else:
            print("\nTests completed.")

    report = load_report()
    if report is None:
        return 1

    analyze_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
