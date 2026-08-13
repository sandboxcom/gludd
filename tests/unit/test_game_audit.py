from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "game_audit.py"


def _load_game_audit() -> ModuleType:
    spec = importlib.util.spec_from_file_location("game_audit_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_model_call_bottleneck_prints_actionable_hint(capsys) -> None:
    module = _load_game_audit()
    report = {
        "games": {
            "demo": {
                "tokens_in": 10,
                "tokens_out": 5,
                "phases": {
                    "model_call": 100,
                    "extract_code": 1,
                    "ast_parse": 1,
                    "game_verify": 1,
                },
                "imported": True,
                "instantiated": True,
                "checks_passed": 1,
                "checks_total": 1,
                "checks": {},
                "errors": [],
                "gaps": [],
            },
        },
        "summary": {
            "total_games": 1,
            "total_tokens_in": 10,
            "total_tokens_out": 5,
            "games_imported": 1,
            "games_fully_verified": 1,
            "total_latency_ms": 103,
        },
    }

    module.analyze_report(report)

    assert "Consider caching or model selection changes." in capsys.readouterr().out
