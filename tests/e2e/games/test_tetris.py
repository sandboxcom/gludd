"""End-to-end Tetris test via DeepSeek."""
from __future__ import annotations

import time

import pytest

from tests.e2e.test_game_building_deepseek import (
    _SKIP_REASON,
    GAME_DEFINITIONS,
    _build_deepseek_gateway,
    _export_observability_report,
    _extract_python_module,
    _get_deepseek_key,
    _init_game_obs,
    _load_generated_module,
    _parse_ast,
    _run_game_tests,
    _run_persistence_tests,
    verify_features,
)


@pytest.mark.skipif(not _get_deepseek_key(), reason=_SKIP_REASON)
class TestTetris:
    @pytest.fixture(scope="class")
    def gateway(self):
        return _build_deepseek_gateway()

    def test_build_tetris(self, gateway, tmp_path):
        game_id = "tetris"
        game_def = GAME_DEFINITIONS[game_id]
        obs = _init_game_obs(game_id)
        print(f"\n\n{'='*70}")
        print(f"BUILDING: {game_id} ({game_def['class_name']})")
        print(f"{'='*70}")

        response = _call_model(gateway, game_def["prompt"])
        obs["tokens_in"] = response["tokens_in"]
        obs["tokens_out"] = response["tokens_out"]
        obs["tool_calls"] = response["tool_calls"]
        obs["content_len"] = response["content_len"]
        obs["model"] = response["model"]
        obs["phases"]["model_call"] = round(response["latency_ms"], 1)

        source = _extract_python_module(response["content"])
        if source is None:
            obs["errors"].append("Could not extract Python module")
            return
        ast_result = _parse_ast(source)
        obs["phases"]["ast_parse"] = 0

        game_dir = tmp_path / game_id
        game_dir.mkdir(exist_ok=True)
        test_results = _run_game_tests(source, game_def["class_name"], game_def["verifications"], game_id, game_dir)
        obs["imported"] = test_results["module_imported"]
        obs["instantiated"] = test_results["instantiated"]

        checks = test_results.get("checks", {})
        passed = sum(1 for c in checks.values() if c["passed"])
        obs["checks_passed"] = passed
        obs["checks_total"] = len(checks)
        print(f"  Checks: {passed} passed, {len(checks) - passed} failed out of {len(checks)}")

        feature_failures = []
        if source and ast_result["parseable"]:
            feature_dir = tmp_path / f"{game_id}_features"
            feature_dir.mkdir(exist_ok=True)
            feature_mod = _load_generated_module(source, f"{game_id}_feature_check", feature_dir)
            feature_failures = verify_features(game_id, feature_mod)

        obs["feature_failures"] = feature_failures
        assert not feature_failures, (
            f"{game_id}: required features not satisfied:\n  - " + "\n  - ".join(feature_failures)
        )

    def test_persistence_tetris(self, gateway, tmp_path):
        game_id = "tetris"
        game_def = GAME_DEFINITIONS[game_id]
        print(f"\n\n{'='*70}")
        print(f"PERSISTENCE TEST: {game_id} — 500 interactions")
        print(f"{'='*70}")

        response = _call_model(gateway, game_def["prompt"])
        source = _extract_python_module(response["content"])
        if source is None:
            return
        ast_result = _parse_ast(source)
        if not ast_result["parseable"]:
            return
        game_dir = tmp_path / f"{game_id}_persist"
        game_dir.mkdir(exist_ok=True)
        results = _run_persistence_tests(source, game_id, game_def["class_name"], 500, game_dir)
        stress = results.get("stress", {})
        print(f"  interactions_completed={stress.get('interactions_completed', 0)}")
        print(f"  crashed={stress.get('crashed')} ended_gracefully={stress.get('ended_gracefully')}")

    def test_gap_report(self):
        _export_observability_report()
        print("\nTetris test complete.")


def _call_model(gateway, prompt):
    t0 = time.time()
    response = gateway.call_model(
        "deepseek_coder",
        messages=[{"role": "user", "content": prompt}],
        estimated_cost=0.0,
        budget_remaining=5.0,
    )
    latency_ms = (time.time() - t0) * 1000
    usage = response.usage_metadata or {}
    return {
        "content": response.content,
        "tokens_in": int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
        "tokens_out": int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
        "tool_calls": len(response.tool_calls) if response.tool_calls else 0,
        "content_len": len(response.content),
        "model": getattr(response, "model_profile_id", "unknown"),
        "latency_ms": latency_ms,
    }
