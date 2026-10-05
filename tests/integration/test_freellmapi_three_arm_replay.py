"""End-to-end proof for the exact, non-promoting three-arm replay."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.replay_freellmapi_three_arm import main

from general_ludd.models.freellmapi.three_arm_contracts import build_frozen_documents

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "config/freellmapi/upstream_candidate.json"
PLAN = ROOT / "config/freellmapi/three_arm_plan.json"
CORPUS = ROOT / "config/freellmapi/three_arm_corpus.json"
ACTIVE_BUNDLE = "d3078364c02f482909681e21895c4e86dc11cc66c1da7ae2007ad35b096ddf7d"
CANDIDATE_BUNDLE = "e9e5d87a0d1e9697e0681b52afb719b7fd1fcdd601bd7fcbaf430cc502114845"
CANDIDATE_ID = "sha256:69d63b09199c37f38c02c711559b15e0fd5dc5ecc0e64e2d94c597dc5e5d3998"


def _arguments(mode: str, report: Path, *, candidate: Path = CANDIDATE) -> list[str]:
    return [
        "--mode",
        mode,
        "--candidate",
        str(candidate),
        "--plan",
        str(PLAN),
        "--corpus",
        str(CORPUS),
        "--report",
        str(report),
        "--repository-root",
        str(ROOT),
    ]


def test_checked_in_documents_equal_the_deterministic_freeze() -> None:
    expected_plan, expected_corpus = build_frozen_documents(
        candidate_id=CANDIDATE_ID,
        candidate_bundle_sha256=CANDIDATE_BUNDLE,
        admitted_bundle_sha256=ACTIVE_BUNDLE,
    )

    assert json.loads(PLAN.read_text(encoding="utf-8")) == expected_plan
    assert json.loads(CORPUS.read_text(encoding="utf-8")) == expected_corpus


def test_validate_mode_is_read_only_and_non_promoting(
    tmp_path: Path,
    capsys: object,
) -> None:
    report = tmp_path / "must-not-exist.json"

    assert main(_arguments("validate", report)) == 0
    assert not report.exists()
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output == {
        "candidate_bundle_sha256": CANDIDATE_BUNDLE,
        "decision": "HOLD",
        "exclusion_count": 8,
        "group_count": 32,
        "mode": "validate",
        "runtime_admitted": False,
        "serving_bundle_sha256": ACTIVE_BUNDLE,
    }


def test_real_quickjs_and_node_three_arm_replay_preserves_v099(
    tmp_path: Path,
    capsys: object,
) -> None:
    report_path = tmp_path / "evidence.json"

    assert main(_arguments("replay", report_path)) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]

    assert output["decision"] == "HOLD"
    assert output["runtime_admitted"] is False
    assert report["decision"] == "HOLD"
    assert report["promotion_permitted"] is False
    assert report["runtime_admitted"] is False
    assert report["serving_bundle_sha256"] == ACTIVE_BUNDLE
    assert report["group_count"] == 32
    assert report["exclusion_count"] == 8
    assert report["network_calls"] == 0
    assert report["cost_usd"] == 0.0
    assert report["bridge_fault_count"] == 0
    assert report["node_crosscheck_passed"] is True
    assert report["comparisons"]["gludd_native"]["quality_lcb"] >= 0.02
    assert report["comparisons"]["gludd_native"]["mcnemar_pvalue"] < 0.05
    admitted = report["comparisons"]["freellmapi_v0_9_9"]
    assert admitted["quality_lcb"] == 0.0
    assert admitted["mcnemar_pvalue"] == 1.0
    assert "quality_lcb:freellmapi_v0_9_9" in report["failed_gates"]
    assert "mcnemar:freellmapi_v0_9_9" in report["failed_gates"]
    assert report["candidate_max_latency_ms"] <= 25.0
    assert report["rss_delta_mib"] <= 32.0
    assert "rss_monotonic_growth" not in report["failed_gates"]


def test_any_input_failure_writes_content_free_hold_and_keeps_v099(
    tmp_path: Path,
    capsys: object,
) -> None:
    candidate = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    candidate["candidate_id"] = "sha256:" + "0" * 64
    broken_candidate = tmp_path / "candidate.json"
    broken_candidate.write_text(json.dumps(candidate), encoding="utf-8")
    report_path = tmp_path / "failure.json"

    assert main(_arguments("replay", report_path, candidate=broken_candidate)) == 2
    report = json.loads(report_path.read_text(encoding="utf-8"))
    error = json.loads(capsys.readouterr().err)  # type: ignore[attr-defined]

    assert report == {
        "all_gates_passed": False,
        "decision": "HOLD",
        "fault": "candidate_invalid",
        "promotion_permitted": False,
        "runtime_admitted": False,
        "serving_bundle_sha256": ACTIVE_BUNDLE,
    }
    assert error == {"fault": "candidate_invalid", "ok": False}
