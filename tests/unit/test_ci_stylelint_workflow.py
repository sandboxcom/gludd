"""Regression coverage for the hosted CSS lint workflow."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
CONFIG = ROOT / "stylelint.config.mjs"
IGNORE = ROOT / ".stylelintignore"
MAKEFILE = ROOT / "make" / "10-observability-and-tests.mk"
HELP = ROOT / "make" / "00-foundation.mk"
PACKAGE = ROOT / ".opencode" / "package.json"
TARGET_CONTRACT = ROOT / "config" / "make_target_contract.json"


def test_css_lint_uses_pinned_stylelint_with_a_real_config_file() -> None:
    """Stylelint must not interpret inline JSON as a filesystem path."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    package = json.loads(PACKAGE.read_text(encoding="utf-8"))

    assert package["devDependencies"]["stylelint"] == "17.16.0"
    assert "make lint-css" in workflow
    assert "npx --no-install stylelint" not in workflow
    assert "--config '{" not in workflow
    assert CONFIG.is_file()


def test_stylelint_config_keeps_the_hosted_rules_enabled() -> None:
    """The tracked config must retain every rule enforced by CI."""
    config = CONFIG.read_text(encoding="utf-8")

    assert '"block-no-empty": true' in config
    assert '"color-no-invalid-hex": true' in config
    assert '"consecutive-duplicates-with-different-values"' in config
    assert "docs/presentation/deck/vendor/**" in IGNORE.read_text(encoding="utf-8")


def test_local_css_lint_target_matches_the_hosted_command() -> None:
    """Developers must be able to execute the same CSS gate before pushing."""
    makefile = MAKEFILE.read_text(encoding="utf-8")
    help_text = HELP.read_text(encoding="utf-8")
    contract = json.loads(TARGET_CONTRACT.read_text(encoding="utf-8"))
    targets = {item["name"]: item for item in contract["targets"]}

    assert "lint-css:" in makefile
    assert ".opencode/node_modules/.bin/stylelint" in makefile
    assert '"**/*.css" "**/*.scss" "**/*.less"' in makefile
    assert "--allow-empty-input --config stylelint.config.mjs" in makefile
    assert "lint-css" in help_text
    assert targets["lint-css"] == {
        "name": "lint-css",
        "make_variables": [],
        "behavior": "make lint-css",
    }
