"""End-to-end game-building test harness with SearX-powered research phase.

Extends the DeepSeek game-building pipeline with a research-before-build step:
  1. SearX research phase — query SearX for game rules/mechanics/implementation
  2. Augmented build — feed research summary into the DeepSeek builder prompt
  3. Research verification — confirm generated features align with research
  4. Observability — export research_effectiveness.json

Run:
    DEEPSEEK_API_KEY="sk-..." uv run pytest tests/e2e/test_game_building_searx.py -v -s

The test skips (not fails) if SearX is not reachable.
"""

from __future__ import annotations

import json
import os
import re
import textwrap
import time
from pathlib import Path
from typing import Any, ClassVar

import pytest

from general_ludd.connectors.searx import SearXConnector, SearXResult
from general_ludd.searx.server import SearXServer
from tests.e2e._game_lifecycle import run_lifecycle_checks
from tests.e2e.test_game_building_deepseek import (
    GAME_DEFINITIONS,
    _build_deepseek_gateway,
    _call_deepseek,
    _extract_python_module,
    _load_generated_module,
    _parse_ast,
    _run_game_tests,
    verify_features,
)

# ---------------------------------------------------------------------------
# Keystore
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent


def _load_deepseek_key() -> str | None:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    key_file = _REPO_ROOT / ".deepseek.key"
    if key_file.exists():
        v = key_file.read_text().strip()
        return v if v else None
    return None

_DEEPSEEK_KEY = _load_deepseek_key()
_DS_SKIP_REASON = (
    "DEEPSEEK_API_KEY not set and .deepseek.key not found"
)

# ---------------------------------------------------------------------------
# SearX fixture
# ---------------------------------------------------------------------------

_SEARX_CONNECTOR: SearXConnector | None = None
_SEARX_SERVER: SearXServer | None = None
_SEARX_AVAILABLE: bool | None = None


def _init_searx() -> SearXConnector | None:
    global _SEARX_CONNECTOR, _SEARX_SERVER, _SEARX_AVAILABLE
    if _SEARX_AVAILABLE is not None:
        return _SEARX_CONNECTOR

    try:
        _SEARX_SERVER = SearXServer()
        if _SEARX_SERVER.ensure_started():
            _SEARX_CONNECTOR = SearXConnector.from_local_server(_SEARX_SERVER)
            _SEARX_AVAILABLE = True
            return _SEARX_CONNECTOR
    except Exception:
        pass
    _SEARX_AVAILABLE = False
    return None


def _stop_searx() -> None:
    global _SEARX_SERVER, _SEARX_AVAILABLE
    if _SEARX_SERVER is not None:
        _SEARX_SERVER.stop()
    _SEARX_SERVER = None
    _SEARX_AVAILABLE = None


# ---------------------------------------------------------------------------
# Research phase
# ---------------------------------------------------------------------------

_RESEARCH_RECORD: dict[str, Any] = {}


def _research_game(game_id: str, connector: SearXConnector) -> dict[str, Any]:
    """Run two SearX queries per game and return structured research summary."""
    queries: list[dict[str, str]] = [
        {"query": f"{game_id.replace('_', ' ')} game rules mechanics features", "label": "rules"},
        {"query": f"{game_id.replace('_', ' ')} implementation requirements python", "label": "implementation"},
    ]

    all_results: dict[str, list[SearXResult]] = {}
    for q in queries:
        results = connector.search(q["query"])
        all_results[q["label"]] = results
        time.sleep(0.3)

    return {
        "game_id": game_id,
        "queries": queries,
        "results": {
            label: [
                {"title": r.title, "url": r.url, "snippet": r.snippet, "engine": r.engine}
                for r in results
            ]
            for label, results in all_results.items()
        },
        "summary": _summarize_research(game_id, all_results),
    }


def _summarize_research(
    game_id: str, all_results: dict[str, list[SearXResult]],
) -> str:
    """Distill search results into a concise structured summary for the prompt."""
    parts: list[str] = []
    for label, results in all_results.items():
        snippets = [r.snippet for r in results if r.snippet]
        if snippets:
            combined = " ".join(snippets[:5])
            parts.append(f"**{label}** findings: {combined}")

    if not parts:
        return f"No research results available for {game_id}"

    return "\n".join(parts)


def _augment_prompt(base_prompt: str, research: dict[str, Any]) -> str:
    """Prepend research summary to the game-building prompt."""
    summary = research.get("summary", "")
    header = textwrap.dedent("""\
        ## RESEARCH SUMMARY (SearX-derived — use these findings to improve the implementation)


        """)
    if not summary.strip():
        return base_prompt
    return header + summary + "\n\n" + base_prompt


# ---------------------------------------------------------------------------
# Research verification
# ---------------------------------------------------------------------------

def _verify_research_usage(
    game_id: str, source: str, research: dict[str, Any],
) -> dict[str, Any]:
    """After a game is built, check that research findings are reflected.

    Returns a dict with:
      - keywords_matched: list of (keyword, found) tuples
      - keywords_total: total unique keywords from research
      - match_rate: fraction matched
      - ignored_findings: findings with no keyword match in source
    """
    summary = research.get("summary", "")
    if not summary.strip():
        return {"match_rate": 1.0, "keywords_matched": [], "keywords_total": 0,
                "ignored_findings": [], "note": "no research to verify"}

    keywords: set[str] = set()
    findings_map: dict[str, str] = {}

    for passages in research.get("results", {}).values():
        for item in passages:
            snippet = item.get("snippet", "") or ""
            for word in re.findall(r"[a-zA-Z]{4,}", snippet):
                wl = word.lower()
                if wl not in {
                    "that", "this", "from", "with", "when", "been", "have",
                    "they", "which", "their", "will", "what", "about", "into",
                    "over", "than", "then", "also", "only",
                }:
                    keywords.add(wl)
                    if wl not in findings_map and snippet:
                        findings_map[wl] = snippet[:80]

    source_lower = source.lower()
    matched: list[tuple[str, bool]] = []
    ignored: list[dict[str, str]] = []

    for kw in sorted(keywords):
        found = kw in source_lower
        matched.append((kw, found))
        if not found:
            ignored.append({"keyword": kw, "context": findings_map.get(kw, "")[:80]})

    match_rate = sum(1 for _, f in matched if f) / len(matched) if matched else 1.0

    return {
        "match_rate": round(match_rate, 3),
        "keywords_matched": len([1 for _, f in matched if f]),
        "keywords_total": len(matched),
        "ignored_findings": ignored[:15],
    }


# ---------------------------------------------------------------------------
# Observability report
# ---------------------------------------------------------------------------

_OBS_DATA: dict[str, dict[str, Any]] = {}


def _export_research_effectiveness(out_dir: Path) -> Path:
    report = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "games": _OBS_DATA,
        "summary": {
            "total_games": len(_OBS_DATA),
            "games_with_research_available": sum(
                1 for g in _OBS_DATA.values() if g.get("research_available")
            ),
            "avg_match_rate": round(
                sum(g.get("match_rate", 0) for g in _OBS_DATA.values())
                / max(len(_OBS_DATA), 1),
                3,
            ),
            "avg_keywords_total": round(
                sum(g.get("keywords_total", 0) for g in _OBS_DATA.values())
                / max(len(_OBS_DATA), 1),
                1,
            ),
            "improved_feature_completeness": {
                gid: {
                    "research_used": g.get("match_rate", 0),
                    "checks_passed": g.get("checks_passed", 0),
                    "checks_total": g.get("checks_total", 0),
                    "feature_failures": len(g.get("feature_failures", [])),
                }
                for gid, g in _OBS_DATA.items()
            },
        },
    }
    out_path = out_dir / "research_effectiveness.json"
    out_path.write_text(json.dumps(report, indent=2))
    print(f"research_effectiveness written to {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_TARGET_GAMES = ("snake", "tetris", "minesweeper")


@pytest.mark.skipif(not _DEEPSEEK_KEY, reason=_DS_SKIP_REASON)
class TestGameBuildingWithResearch:
    """Build games with SearX research as a pre-build phase.

    Each game: research → augmented build → lifecycle checks → research verification.
    Skips if SearX is not running (does NOT fail).
    """

    _gaps: ClassVar[list[dict[str, Any]]] = []

    @pytest.fixture(scope="class")
    def gateway(self):
        return _build_deepseek_gateway()

    @pytest.fixture(scope="class")
    def searx(self):
        connector = _init_searx()
        if connector is None:
            pytest.skip("SearX instance not available — skipping research tests")
        yield connector
        _stop_searx()

    @classmethod
    def _record_gap(cls, game_id: str, category: str, detail: str) -> None:
        cls._gaps.append({"game": game_id, "category": category, "detail": detail})

    # ---- Snake ----
    def test_build_snake_with_research(self, gateway, searx, tmp_path):
        self._build_with_research(gateway, searx, tmp_path, "snake")

    # ---- Tetris ----
    def test_build_tetris_with_research(self, gateway, searx, tmp_path):
        self._build_with_research(gateway, searx, tmp_path, "tetris")

    # ---- Minesweeper ----
    def test_build_minesweeper_with_research(self, gateway, searx, tmp_path):
        self._build_with_research(gateway, searx, tmp_path, "minesweeper")

    # ---- Shared build + verify logic ----
    def _build_with_research(
        self, gateway: Any, searx: SearXConnector, tmp_path: Path, game_id: str,
    ) -> None:
        game_def = GAME_DEFINITIONS[game_id]
        class_name = game_def["class_name"]
        verifications = game_def["verifications"]

        obs: dict[str, Any] = {
            "game_id": game_id,
            "research_available": True,
            "research_queries": 0,
            "research_results_total": 0,
            "keywords_total": 0,
            "keywords_matched": 0,
            "match_rate": 0.0,
            "checks_passed": 0,
            "checks_total": 0,
            "feature_failures": [],
            "lifecycle_failures": [],
            "errors": [],
        }
        _OBS_DATA[game_id] = obs

        print(f"\n\n{'='*70}")
        print(f"BUILDING WITH RESEARCH: {game_id} ({class_name})")
        print(f"{'='*70}")

        # Phase 1: SearX research
        print(f"\n--- Phase 1: SearX Research for {game_id} ---")
        t0 = time.time()
        research = _research_game(game_id, searx)
        research_time_ms = (time.time() - t0) * 1000

        searx_results = research.get("results", {})
        total_results = sum(len(v) for v in searx_results.values())
        obs["research_queries"] = len(research.get("queries", []))
        obs["research_results_total"] = total_results
        obs["research_summary_preview"] = research.get("summary", "")[:200]
        obs["research_phase_ms"] = round(research_time_ms, 1)

        print(f"  queries: {obs['research_queries']}")
        print(f"  total results: {total_results}")
        print(f"  research phase: {research_time_ms:.0f}ms")
        for label, results in searx_results.items():
            urls = [r.get("url", "")[:60] for r in results[:3]]
            print(f"  [{label}]: {len(results)} results — {urls}")

        _RESEARCH_RECORD[game_id] = research

        # Phase 2: Augmented build
        print(f"\n--- Phase 2: Augmented Build for {game_id} ---")
        augmented_prompt = _augment_prompt(game_def["prompt"], research)

        try:
            response = _call_deepseek(gateway, augmented_prompt)
        except Exception as exc:
            obs["errors"].append(f"DeepSeek call failed: {exc}")
            self._record_gap(game_id, "model_call", str(exc))
            return

        print(f"  tokens_in={response['tokens_in']} tokens_out={response['tokens_out']}")

        source = _extract_python_module(response["content"])
        if source is None:
            obs["errors"].append("Could not extract Python module from model output")
            self._record_gap(game_id, "code_extraction", "No extractable Python code")
            return

        print(f"  extracted {len(source)} chars of Python")

        # Phase 3: AST parse + game tests
        print(f"\n--- Phase 3: Game Verification for {game_id} ---")
        ast_result = _parse_ast(source)
        if not ast_result["parseable"]:
            obs["errors"].append(f"AST error: {ast_result.get('error')}")
            self._record_gap(game_id, "ast_parse", str(ast_result.get("error")))
            return

        game_dir = tmp_path / game_id
        game_dir.mkdir(exist_ok=True)
        test_results = _run_game_tests(source, class_name, verifications, game_id, game_dir)

        checks = test_results.get("checks", {})
        passed = sum(1 for c in checks.values() if c["passed"])
        obs["checks_passed"] = passed
        obs["checks_total"] = len(checks)

        print(f"  checks: {passed}/{len(checks)} passed")
        for check_id, check_data in checks.items():
            status = "PASS" if check_data["passed"] else "FAIL"
            if not check_data["passed"]:
                print(f"    [{status}] {check_id}: {check_data['desc']}")

        if test_results["errors"]:
            for err in test_results["errors"]:
                obs["errors"].append(str(err)[:200])

        # Phase 4: Lifecycle checks (the 7-check harness)
        print(f"\n--- Phase 4: Lifecycle Checks for {game_id} ---")
        if ast_result["parseable"]:
            feature_dir = tmp_path / f"{game_id}_lifecycle"
            feature_dir.mkdir(exist_ok=True)
            try:
                mod = _load_generated_module(source, f"{game_id}_lifecycle_check", feature_dir)
                lc_failures = run_lifecycle_checks(game_id, mod)
                obs["lifecycle_failures"] = lc_failures
                if lc_failures:
                    print(f"  lifecycle failures ({len(lc_failures)}):")
                    for f in lc_failures:
                        print(f"    - {f}")
                else:
                    print("  all 7 lifecycle checks passed")
            except Exception as exc:
                obs["lifecycle_failures"] = [f"lifecycle crashed: {exc}"]
                print(f"  lifecycle crashed: {exc}")

        # Phase 5: Research usage verification
        print(f"\n--- Phase 5: Research Usage Verification for {game_id} ---")
        usage = _verify_research_usage(game_id, source, research)
        obs["match_rate"] = usage["match_rate"]
        obs["keywords_matched"] = usage["keywords_matched"]
        obs["keywords_total"] = usage["keywords_total"]
        obs["ignored_findings"] = usage.get("ignored_findings", [])

        print(f"  keyword match rate: {usage['match_rate']:.1%} "
              f"({usage['keywords_matched']}/{usage['keywords_total']})")

        if usage.get("ignored_findings"):
            ignored = usage["ignored_findings"]
            print(f"  top ignored findings ({len(ignored)} total, showing≤5):")
            for ig in ignored[:5]:
                print(f"    - [{ig['keyword']}] {ig.get('context', '')[:80]}")

        # Phase 6: Feature verification (name-agnostic contract)
        print(f"\n--- Phase 6: Feature Contract Verification for {game_id} ---")
        if ast_result["parseable"]:
            feat_dir = tmp_path / f"{game_id}_features"
            feat_dir.mkdir(exist_ok=True)
            try:
                feat_mod = _load_generated_module(source, f"{game_id}_feature_check", feat_dir)
                feature_failures = verify_features(game_id, feat_mod)
                obs["feature_failures"] = feature_failures
                if feature_failures:
                    print(f"  feature failures ({len(feature_failures)}):")
                    for f in feature_failures:
                        print(f"    - {f}")
                    self._record_gap(game_id, "features",
                                     f"{len(feature_failures)} feature failures")
                else:
                    print("  all required features verified")
            except Exception as exc:
                obs["feature_failures"] = [f"feature verifier crashed: {exc}"]
                print(f"  feature verifier crashed: {exc}")

        print(f"\n{'='*70}")
        print(f"RESULT: {game_id} — {passed}/{len(checks)} checks, "
              f"{usage['match_rate']:.1%} research used, "
              f"{len(obs.get('feature_failures', []))} feature failures, "
              f"{len(obs.get('lifecycle_failures', []))} lifecycle failures")

    # ---- Research effectiveness report ----
    def test_research_effectiveness_report(self, tmp_path):
        """Export research_effectiveness.json and print audit summary."""
        out_path = _export_research_effectiveness(out_dir=tmp_path)

        # Verify the report landed in the temp dir, not the repo root.
        assert out_path.exists()
        assert out_path.parent == tmp_path
        loaded = json.loads(out_path.read_text())
        assert "generated" in loaded
        assert "games" in loaded
        assert "summary" in loaded
        assert not (_REPO_ROOT / "research_effectiveness.json").exists()

        print("\n\n" + "=" * 70)
        print("RESEARCH EFFECTIVENESS AUDIT")
        print("=" * 70)

        if not _OBS_DATA:
            print("No research data collected — run game-building tests first")
            return

        for game_id, obs in sorted(_OBS_DATA.items()):
            print(f"\n  {game_id}:")
            print(f"    SearX queries:    {obs.get('research_queries', 0)}")
            print(f"    Results returned: {obs.get('research_results_total', 0)}")
            print(f"    Keyword match:    {obs.get('match_rate', 0):.1%} "
                  f"({obs.get('keywords_matched', 0)}/{obs.get('keywords_total', 0)})")
            print(f"    Checks passed:    {obs.get('checks_passed', 0)}/{obs.get('checks_total', 0)}")
            lc = obs.get('lifecycle_failures', [])
            print(f"    Lifecycle:        {'PASS' if not lc else f'{len(lc)} failures'}")

        avg = sum(o.get("match_rate", 0) for o in _OBS_DATA.values()) / max(len(_OBS_DATA), 1)
        print(f"\n  Average match rate: {avg:.1%}")
        print("=" * 70)

    # ---- Gap report ----
    def test_gap_report(self):
        gaps = TestGameBuildingWithResearch._gaps
        if not gaps:
            print("\nNo research-augmented gaps detected")
            return

        print("\n\n" + "=" * 70)
        print("RESEARCH-AUGMENTED GAP REPORT")
        print("=" * 70)
        print(f"\nTotal gaps: {len(gaps)}")

        by_game: dict[str, list[dict]] = {}
        for g in gaps:
            by_game.setdefault(g["game"], []).append(g)

        print("\nBy game:")
        for game, items in sorted(by_game.items()):
            cats = sorted(set(i["category"] for i in items))
            print(f"  {game}: {len(items)} gaps ({', '.join(cats)})")

        print("\nDetailed gaps:")
        for g in gaps:
            print(f"  [{g['game']}] {g['category']}: {g['detail'][:200]}")
