"""Structural contract for the no-exemption tracked-file remediation plan."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "quality" / "file-line-remediation-plan.md"

EXPECTED_PATHS = {
    ".opencode/skills/go-expert/SKILL.md",
    ".opencode/skills/java-expert/SKILL.md",
    ".secrets.baseline",
    "AGENTS.md",
    "docs/MCP_TOOLS_TOPICS.yml",
    "docs/design/specs/SPEC_ML_AI_EXPERT_AND_SAFE_SELF_IMPROVEMENT.md",
    "docs/features/BETA4_DUAL_TRACK_CI.md",
    "docs/internal/sprint0.md",
    "docs/specs/BEHAVIORAL_SPECS.md",
    "docs/specs/FEATURE_EXPERT_SYSTEM_INTEROPERABILITY.md",
    "scripts/agent_watchdog.py",
    "scripts/test_hook_runtime.py",
    "src/general_ludd/cli.py",
    "src/general_ludd/daemon.py",
    "src/general_ludd/db/repository.py",
    "src/general_ludd/event_loop/loop.py",
    "src/general_ludd/models/gateway.py",
    "src/general_ludd/pricing_intel/sources.py",
    "src/general_ludd/self_improve/codex_comparison.py",
    "src/general_ludd/self_improve/managed_runner.py",
    "src/general_ludd/self_improve/runtime.py",
    "tests/e2e/test_connectors_batch5_workflows.py",
    "tests/e2e/test_game_building_deepseek.py",
    "tests/unit/test_automatic_disk_cleanup.py",
    "tests/unit/test_behavioral_enforcement.py",
    "tests/unit/test_ci_named_shard_files.py",
    "tests/unit/test_probabilistic_deep.py",
    "tests/unit/test_routers_endpoints.py",
    "tests/unit/test_self_improve_codex_comparison.py",
    "tests/unit/test_self_improve_codex_runner.py",
    "uv.lock",
}


def _ledger_rows() -> list[list[str]]:
    """Return normalized data rows from the exact inventory ledger."""
    text = PLAN.read_text(encoding="utf-8")
    start = text.index("## Exact 31-file execution ledger")
    end = text.index("\n## ", start + 3)
    rows: list[list[str]] = []
    for line in text[start:end].splitlines():
        if not line.startswith("| `"):
            continue
        rows.append([cell.strip().strip("`") for cell in line.strip("|").split("|")])
    return rows


def test_plan_assigns_every_violation_one_complete_remediation_contract() -> None:
    """Every initial violation has an owner, implementation, proof, and rollback."""
    assert PLAN.is_file()
    rows = _ledger_rows()
    assert len(rows) == 31
    assert all(len(row) == 7 for row in rows)

    by_path = {row[0]: row for row in rows}
    assert len(by_path) == len(rows), "ledger paths must be unique"
    assert set(by_path) == EXPECTED_PATHS

    for path, row in by_path.items():
        owner, mode, destination, migrations, validation, rollback = row[1:]
        assert all((owner, mode, destination, migrations, validation, rollback))
        assert mode in {
            "canonical compaction",
            "facade split",
            "index fragmentation",
            "profile fragmentation",
            "skill routing split",
            "test split",
        }, path
        joined = " ".join(row[1:]).lower()
        assert "tbd" not in joined
        assert "exempt" not in joined
        assert "grandfather" not in joined
        assert "make " in validation.lower(), path
        assert "revert" in rollback.lower() or "restore" in rollback.lower(), path


def test_plan_pins_format_constraints_sources_and_wave_gates() -> None:
    """The plan records why generated/config files need distinct treatments."""
    text = PLAN.read_text(encoding="utf-8")
    required_evidence = {
        "https://github.com/Yelp/detect-secrets/blob/master/README.md",
        "https://github.com/Yelp/detect-secrets/issues/246",
        "https://docs.astral.sh/uv/concepts/projects/layout/",
        "https://github.com/astral-sh/uv/issues/9735",
        "https://github.com/commonmark/commonmark-spec/issues/630",
        "https://github.com/yaml/pyyaml/issues/632",
    }
    assert all(url in text for url in required_evidence)
    assert "No tracked text-file exception" in text
    assert "one oversized file at a time" in text
    assert "make check-file-line-limits" in text
    assert "make gate" in text

    rows = {row[0]: row for row in _ledger_rows()}
    assert rows[".secrets.baseline"][2] == "canonical compaction"
    assert rows["uv.lock"][2] == "profile fragmentation"
    assert rows["docs/specs/BEHAVIORAL_SPECS.md"][2] == "index fragmentation"
    assert rows["docs/MCP_TOOLS_TOPICS.yml"][2] == "index fragmentation"
    assert rows["AGENTS.md"][2] == "index fragmentation"
    assert rows[".opencode/skills/go-expert/SKILL.md"][2] == "skill routing split"
    assert rows[".opencode/skills/java-expert/SKILL.md"][2] == "skill routing split"
