"""Make contracts for canonical detect-secrets baseline management."""

from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
FRAGMENT = ROOT / "make" / "40-cross-version-and-worktrees.mk"
MANAGER = "scripts/manage_secrets_baseline.py"
FLAGS = (
    '--baseline "$(SECRETS_BASELINE_FILE)" '
    '--policy "$(POLICY)" '
    '--repo-root "$(REPO_ROOT)" '
    '--executable "$(EXECUTABLE)"'
)


def _target_recipe(name: str) -> str:
    source = FRAGMENT.read_text(encoding="utf-8")
    match = re.search(
        rf"^{re.escape(name)}:\s*\n((?:\t[^\n]*\n)+)",
        source,
        re.MULTILINE,
    )
    assert match, f"target {name!r} recipe not found"
    return " ".join(match.group(1).split())


def _contract(name: str) -> dict[str, object]:
    payload = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    return next(item for item in payload["targets"] if item["name"] == name)


def test_secrets_baseline_refreshes_then_checks_with_explicit_inputs() -> None:
    """The mutating entry point refreshes atomically, then validates its output."""
    fragment = FRAGMENT.read_text(encoding="utf-8")
    recipe = _target_recipe("secrets-baseline")
    refresh = f"$(UV) run python {MANAGER} refresh {FLAGS}"
    check = f"$(UV) run python {MANAGER} check {FLAGS}"

    assert "SECRETS_BASELINE_FILE ?= .secrets.baseline" in fragment
    assert "POLICY ?= config/detect_secrets_baseline_policy.json" in fragment
    assert "REPO_ROOT ?= $(CURDIR)" in fragment
    assert "EXECUTABLE ?= detect-secrets" in fragment
    assert refresh in recipe
    assert check in recipe
    assert recipe.index(refresh) < recipe.index(check)


def test_secrets_baseline_check_is_read_only_and_contracted() -> None:
    """The agent-facing behavioral example only checks canonical metadata."""
    makefile = compose_makefile(ROOT / "Makefile")
    recipe = _target_recipe("secrets-baseline-check")

    assert f"$(UV) run python {MANAGER} check {FLAGS}" in recipe
    assert " refresh " not in recipe
    assert "secrets-baseline-check" in makefile
    assert _contract("secrets-baseline-check") == {
        "name": "secrets-baseline-check",
        "make_variables": [
            "SECRETS_BASELINE_FILE",
            "POLICY",
            "REPO_ROOT",
            "EXECUTABLE",
        ],
        "behavior": (
            "make secrets-baseline-check "
            "SECRETS_BASELINE_FILE=.secrets.baseline "
            "POLICY=config/detect_secrets_baseline_policy.json "
            "REPO_ROOT=. EXECUTABLE=detect-secrets"
        ),
    }

