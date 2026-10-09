"""Regression coverage for the hosted CSS lint workflow."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
CONFIG = ROOT / "stylelint.config.mjs"


def test_css_lint_uses_pinned_stylelint_with_a_real_config_file() -> None:
    """Stylelint must not interpret inline JSON as a filesystem path."""
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "esbuild@0.28.1 stylelint@17.16.0" in workflow
    assert "npx --no-install stylelint" in workflow
    assert "--config stylelint.config.mjs" in workflow
    assert "--config '{" not in workflow
    assert CONFIG.is_file()


def test_stylelint_config_keeps_the_hosted_rules_enabled() -> None:
    """The tracked config must retain every rule enforced by CI."""
    config = CONFIG.read_text(encoding="utf-8")

    assert '"block-no-empty": true' in config
    assert '"color-no-invalid-hex": true' in config
    assert '"declaration-block-no-duplicate-properties": true' in config
