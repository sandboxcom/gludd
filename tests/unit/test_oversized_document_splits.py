"""Structural contracts for navigable documentation shards."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ML_SPEC = ROOT / "docs/design/specs/SPEC_ML_AI_EXPERT_AND_SAFE_SELF_IMPROVEMENT.md"
ML_CONTINUAL = ML_SPEC.parent / "ml-ai-expert" / "continual-evolution.md"
BETA4_CI = ROOT / "docs/features/BETA4_DUAL_TRACK_CI.md"
BETA4_PROMOTION = BETA4_CI.parent / "beta4-dual-track-ci" / "exact-sha-promotion.md"
BETA4_MOLECULE = BETA4_CI.parent / "beta4-dual-track-ci" / "hosted-molecule-isolation.md"
INTEROP_SPEC = ROOT / "docs/specs/FEATURE_EXPERT_SYSTEM_INTEROPERABILITY.md"
INTEROP_EXTENSIONS = INTEROP_SPEC.parent / "expert-system-interoperability" / "domain-extensions.md"
SPRINT0 = ROOT / "docs/internal/sprint0.md"
SPRINT0_OPERATIONS = SPRINT0.parent / "sprint0" / "implementation-and-operations.md"
AGENT_POLICY = ROOT / "AGENTS.md"
AGENT_TARGETS = ROOT / ".agents" / "policy" / "key-make-targets.md"
AGENT_PLUGINS = ROOT / ".agents" / "policy" / "enforcement-plugins.md"


def test_ml_ai_spec_routes_continual_evolution_below_line_limit() -> None:
    """Keep the authoritative index concise and the full contract discoverable."""
    spec = ML_SPEC.read_text(encoding="utf-8")
    continual = ML_CONTINUAL.read_text(encoding="utf-8")

    assert len(spec.splitlines()) < 2_500
    assert "[continual-evolution contract](ml-ai-expert/continual-evolution.md)" in spec
    for marker in ("### MLCONT.1 ", "### MLCONT.31 "):
        assert marker not in spec
        assert marker in continual
    assert len(continual.splitlines()) < 2_500


def test_beta4_ci_routes_exact_sha_operations_below_line_limit() -> None:
    """Keep the dual-track overview concise without discarding release evidence."""
    overview = BETA4_CI.read_text(encoding="utf-8")
    promotion = BETA4_PROMOTION.read_text(encoding="utf-8")
    molecule = BETA4_MOLECULE.read_text(encoding="utf-8")

    assert len(overview.splitlines()) < 2_500
    assert "[exact-SHA promotion and release operations](beta4-dual-track-ci/exact-sha-promotion.md)" in overview
    assert "[hosted Molecule build and runtime isolation](beta4-dual-track-ci/hosted-molecule-isolation.md)" in overview
    for marker in (
        "## Exact-SHA promotion contract (2026-08-31)",
        "### Durable plugin state is explicit test input (2026-09-29)",
    ):
        assert marker not in overview
        assert marker in promotion
    molecule_marker = "Molecule run `37684090064` exposed two shared-environment assumptions."
    assert molecule_marker not in overview
    assert molecule_marker in molecule
    assert len(promotion.splitlines()) < 2_500
    assert len(molecule.splitlines()) < 2_500


def test_interoperability_spec_routes_domain_extensions_below_line_limit() -> None:
    """Keep the core contract readable while preserving advanced domain rules."""
    core = INTEROP_SPEC.read_text(encoding="utf-8")
    extensions = INTEROP_EXTENSIONS.read_text(encoding="utf-8")

    assert len(core.splitlines()) < 2_500
    assert "[domain extensions and evidence](expert-system-interoperability/domain-extensions.md)" in core
    for marker in (
        "## 24. Rights, privacy, regulated transfer, drift, language, and embodied time",
        "## 27. Practitioner evidence, ZDD, and rollback",
    ):
        assert marker not in core
        assert marker in extensions
    assert len(extensions.splitlines()) < 2_500


def test_sprint0_routes_implementation_operations_below_line_limit() -> None:
    """Keep the original architecture plan compact and its execution record linked."""
    architecture = SPRINT0.read_text(encoding="utf-8")
    operations = SPRINT0_OPERATIONS.read_text(encoding="utf-8")

    assert len(architecture.splitlines()) < 2_500
    assert "[implementation and operations](sprint0/implementation-and-operations.md)" in architecture
    for marker in ("## 15. Model Gateway And Model Profiles", "## 28. Living Notes"):
        assert marker not in architecture
        assert marker in operations
    assert len(operations.splitlines()) < 2_500


def test_agent_policy_is_compact_without_losing_core_contracts() -> None:
    """Keep the auto-loaded policy within the universal file-size budget."""
    policy = AGENT_POLICY.read_text(encoding="utf-8")

    assert len(policy.splitlines()) < 2_500
    for marker in (
        "## Make Target Selection Contract",
        "## CRITICAL: Single-Source Feature Development",
        "## CRITICAL: TDD Policy",
        "## CRITICAL: System-Load Gate Before Dispatch Waves",
        "## CRITICAL: Root-Cause-Only Fix Policy",
    ):
        assert marker in policy
    assert "[key Make targets](.agents/policy/key-make-targets.md)" in policy
    assert "[enforcement plugin reference](.agents/policy/enforcement-plugins.md)" in policy
    assert "## Key Make Targets" in AGENT_TARGETS.read_text(encoding="utf-8")
    assert "## CRITICAL: Enforcement Plugin Reference" in AGENT_PLUGINS.read_text(encoding="utf-8")
