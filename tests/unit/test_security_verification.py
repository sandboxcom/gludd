"""Structural verification of Phase SEC security features.

Phase SEC (TASKS.md:1097) defines 20 security specs. SEC.14-SEC.17 cover the
security tooling surface (detect-secrets baseline, Bandit SAST, CycloneDX SBOM,
pip-audit). The features themselves have shipped; this test verifies the
SEC-phase surface is structurally present so a future regression that removes
a target/config is caught at gate time.

Verified surface:
  - Make targets: secrets-scan, secrets-scrub, secrets-baseline, sast, sbom,
    pip-audit, security, security-audit, clean-artifacts.
  - Config: .secrets.baseline present, pre-commit detect-secrets hook wired.
  - Guardrails: enforce-no-suppressions.ts plugin exists, opencode.json
    enforces workspace-scoped path permissions.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE_PATH = ROOT / "Makefile"
OPENCODE_JSON_PATH = ROOT / "opencode.json"
PRECOMMIT_PATH = ROOT / ".pre-commit-config.yaml"
SECRETS_BASELINE_PATH = ROOT / ".secrets.baseline"
NO_SUPPRESSIONS_PLUGIN_PATH = ROOT / ".opencode/plugin/enforce-no-suppressions.ts"
NO_SUPPRESSIONS_LIB_PATH = ROOT / ".opencode/lib/plugin_test_exports.ts"


def _makefile_targets() -> set[str]:
    """Extract top-level Makefile target names (lines with `name:` at column 0)."""
    assert MAKEFILE_PATH.exists(), "Makefile missing at repo root"
    targets: set[str] = set()
    for line in MAKEFILE_PATH.read_text().splitlines():
        # Top-level target: starts at col 0, contains ':', not a recipe/comment.
        if not line or line[0] in ("#", "\t", " "):
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*):", line)
        if m:
            targets.add(m.group(1))
    return targets


def _assert_target_recipe_runs(target: str, tool: str) -> None:
    """Confirm the target's recipe actually invokes the named tool."""
    src = MAKEFILE_PATH.read_text()
    pattern = re.compile(
        rf"^{re.escape(target)}:\s*\n((?:\t[^\n]*\n)+)", re.MULTILINE
    )
    m = pattern.search(src)
    assert m, f"target {target!r} recipe not found"
    recipe = m.group(1)
    assert tool in recipe, (
        f"target {target!r} recipe does not invoke {tool!r}; recipe was:\n{recipe}"
    )


# --- Make target existence ------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "secrets-scan",
        "secrets-scrub",
        "secrets-baseline",
        "sast",
        "sbom",
        "pip-audit",
        "security",
        "security-audit",
        "clean-artifacts",
    ],
)
def test_security_make_target_exists(target: str) -> None:
    """Each SEC-phase security target is declared at top level of the Makefile."""
    targets = _makefile_targets()
    assert target in targets, (
        f"make target {target!r} missing — Phase SEC surface regressed. "
        f"Present targets sample: {sorted(t for t in targets if 'sec' in t or 'sast' in t or 'sbom' in t) [:10]}"
    )


# --- Make target tool wiring ----------------------------------------------


def test_secrets_scan_uses_detect_secrets() -> None:
    """SEC.14: secrets-scan must invoke detect-secrets against the baseline."""
    _assert_target_recipe_runs("secrets-scan", "detect-secrets")


def test_secrets_scrub_invokes_audit() -> None:
    """SEC.14 follow-up: secrets-scrub exposes the interactive audit workflow."""
    _assert_target_recipe_runs("secrets-scrub", "detect-secrets audit")


def test_secrets_baseline_writes_baseline() -> None:
    """SEC.14: secrets-baseline rebuilds .secrets.baseline via detect-secrets."""
    _assert_target_recipe_runs("secrets-baseline", ".secrets.baseline")


def test_sast_uses_bandit() -> None:
    """SEC.15: sast target runs the bandit SAST scanner."""
    _assert_target_recipe_runs("sast", "bandit")


def test_sbom_uses_cyclonedx() -> None:
    """SEC.16: sbom target generates a CycloneDX SBOM."""
    _assert_target_recipe_runs("sbom", "cyclonedx-py")


def test_pip_audit_runs_pip_audit() -> None:
    """SEC.17: pip-audit target runs the pip-audit tool."""
    _assert_target_recipe_runs("pip-audit", "pip-audit")


def test_security_target_aggregates_pipeline() -> None:
    """`make security` is the aggregate entry point — must chain the SEC subsites."""
    src = MAKEFILE_PATH.read_text()
    m = re.search(r"^security:\s*(.+)$", src, re.MULTILINE)
    assert m, "no `security:` aggregate line found"
    deps = m.group(1).strip()
    for expected in ("sast", "sbom", "pip-audit"):
        assert expected in deps, (
            f"`security` target does not depend on {expected!r}; deps were: {deps}"
        )


# --- .secrets.baseline ----------------------------------------------------


def test_secrets_baseline_file_exists() -> None:
    """SEC.14: .secrets.baseline must ship at repo root."""
    assert SECRETS_BASELINE_PATH.exists(), (
        f".secrets.baseline missing at {SECRETS_BASELINE_PATH}"
    )


def test_secrets_baseline_is_valid_json() -> None:
    """The baseline must be parseable JSON (consumed by detect-secrets --baseline)."""
    assert SECRETS_BASELINE_PATH.exists(), ".secrets.baseline missing"
    try:
        data = json.loads(SECRETS_BASELINE_PATH.read_text())
    except json.JSONDecodeError as exc:
        pytest.fail(f".secrets.baseline is not valid JSON: {exc}")
    assert isinstance(data, dict), ".secrets.baseline root must be a JSON object"
    assert "results" in data, (
        ".secrets.baseline missing `results` key (detect-secrets shape)"
    )


# --- pre-commit hook wiring -----------------------------------------------


def test_pre_commit_detect_secrets_hook() -> None:
    """SEC.14: detect-secrets must be wired into .pre-commit-config.yaml."""
    assert PRECOMMIT_PATH.exists(), ".pre-commit-config.yaml missing"
    cfg = PRECOMMIT_PATH.read_text()
    assert "detect-secrets" in cfg, (
        "detect-secrets pre-commit hook not configured in .pre-commit-config.yaml"
    )
    assert ".secrets.baseline" in cfg, (
        "detect-secrets hook must reference .secrets.baseline (--baseline arg)"
    )


def test_pre_commit_detect_private_key_hook() -> None:
    """Defense-in-depth: pre-commit hooks include detect-private-key guard."""
    cfg = PRECOMMIT_PATH.read_text()
    assert "detect-private-key" in cfg, (
        "detect-private-key hook missing from .pre-commit-config.yaml"
    )


# --- No-suppression guardrail --------------------------------------------


def test_enforce_no_suppressions_plugin_exists() -> None:
    """AGENTS.md no-suppression policy: the editor gate plugin must be on disk."""
    assert NO_SUPPRESSIONS_PLUGIN_PATH.exists(), (
        f"enforce-no-suppressions.ts missing at {NO_SUPPRESSIONS_PLUGIN_PATH}"
    )


def test_enforce_no_suppressions_registered_in_opencode() -> None:
    """The plugin must be wired into opencode.json's plugin array."""
    cfg = json.loads(OPENCODE_JSON_PATH.read_text())
    plugins = cfg.get("plugin", [])
    assert any(
        "enforce-no-suppressions" in p for p in plugins
    ), f"enforce-no-suppressions not registered in opencode.json plugin list: {plugins}"


def test_enforce_no_suppressions_patterns_present() -> None:
    """The plugin/lib must enumerate the AGENTS.md forbidden suppression patterns.

    The regex list lives in plugin_test_exports.ts (the proxy import path); the
    plugin file delegates to `shouldAllowEdit` from that lib. Both are scanned so
    a refactor that moves the list is still caught.
    """
    candidates = [
        NO_SUPPRESSIONS_PLUGIN_PATH.read_text(),
        NO_SUPPRESSIONS_LIB_PATH.read_text()
        if NO_SUPPRESSIONS_LIB_PATH.exists()
        else "",
    ]
    combined = "\n".join(candidates)
    # Each pattern AGENTS.md "No Lint-Suppression Comments" names.
    for needle in ("noqa", "type: ignore", "pylint", "fmt:", "isort"):
        assert needle in combined, (
            f"suppression pattern {needle!r} not referenced in plugin or lib source"
        )


# --- opencode.json path restriction --------------------------------------


def test_opencode_permission_block_exists() -> None:
    """opencode.json must carry a `permission` block gating file tools."""
    cfg = json.loads(OPENCODE_JSON_PATH.read_text())
    assert "permission" in cfg, "opencode.json missing top-level `permission` block"


def test_workspace_tools_use_supported_permission_schema() -> None:
    """Workspace tools are allowed while external paths remain deny-first."""
    cfg = json.loads(OPENCODE_JSON_PATH.read_text())
    permission = cfg.get("permission", {})
    read = permission.get("read", {})
    assert read.get("*") == "allow"
    assert read.get("*.env") == "deny"
    assert read.get("*.env.*") == "deny"
    assert read.get("*.env.example") == "allow"
    for tool in ("edit", "glob", "grep"):
        assert permission.get(tool) == "allow"
    assert "write" not in permission
    external = permission.get("external_directory", {})
    assert next(iter(external.items())) == ("*", "deny")


def test_bash_restricted_to_make() -> None:
    """The bash tool must be locked to `make *` (AGENTS.md Bash Command Policy)."""
    cfg = json.loads(OPENCODE_JSON_PATH.read_text())
    bash = cfg.get("permission", {}).get("bash", {})
    assert bash.get("*") == "deny", (
        f"bash must deny `*` by default; was: {bash.get('*')}"
    )
    assert bash.get("make *") == "allow", (
        "bash must allow `make *` (the only sanctioned command shape)"
    )
