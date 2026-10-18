"""Regression coverage for the hosted CSS lint workflow."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
CONFIG = ROOT / "config" / "eslint-css.config.mjs"
MAKEFILE = ROOT / "make" / "00-foundation.mk"
PACKAGE = ROOT / ".opencode" / "package.json"
TARGET_CONTRACT = ROOT / "config" / "make_target_contract.json"


def test_css_lint_uses_pinned_native_engine_with_a_real_config_file() -> None:
    """The hosted CSS linter must use locked tools and a tracked config path."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    package = json.loads(PACKAGE.read_text(encoding="utf-8"))

    assert package["devDependencies"]["eslint"] == "10.11.0"
    assert package["devDependencies"]["@eslint/css"] == "2.0.0"
    assert "make lint-css" in workflow
    assert "npx --no-install stylelint" not in workflow
    assert "npx --no-install eslint" not in workflow
    assert "--config '{" not in workflow
    assert CONFIG.is_file()


def test_css_lint_config_keeps_the_hosted_rules_enabled() -> None:
    """The active config must retain every CI rule and the vendor boundary."""
    config = CONFIG.read_text(encoding="utf-8")

    assert '"css/no-empty-blocks": "error"' in config
    assert re.search(
        r'"css/no-invalid-properties":\s*\[\s*"error",\s*'
        r'\{\s*allowUnknownVariables:\s*true\s*\},?\s*\]',
        config,
    )
    assert '"gludd-css/no-invalid-hex-colors": "error"' in config
    assert '"gludd-css/no-duplicate-properties": "error"' in config
    assert "docs/presentation/deck/vendor/**" in config


def test_local_css_lint_target_matches_the_hosted_command() -> None:
    """Developers must be able to execute the same CSS gate before pushing."""
    makefile = MAKEFILE.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    contract = json.loads(TARGET_CONTRACT.read_text(encoding="utf-8"))
    targets = {item["name"]: item for item in contract["targets"]}

    assert "lint-css:" in makefile
    assert ".opencode/node_modules/.bin/eslint" in makefile
    assert '"**/*.css" "**/*.scss" "**/*.less"' in workflow
    assert '--no-error-on-unmatched-pattern --config "$(ESLINT_CSS_CONFIG)" $(CSS_FILES)' in makefile
    assert '"  lint-css ' in makefile
    assert targets["lint-css"] == {
        "name": "lint-css",
        "make_variables": ["CSS_FILES", "ESLINT_CSS_CONFIG"],
        "behavior": (
            "make lint-css CSS_FILES=docs/presentation/deck/presentation.css "
            "ESLINT_CSS_CONFIG=config/eslint-css.config.mjs"
        ),
    }
