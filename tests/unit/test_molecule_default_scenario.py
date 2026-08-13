"""Regression pins for Molecule's required canonical ``default`` scenario.

Molecule 26 loads ``molecule/default/molecule.yml`` even when ``-s`` selects a
different scenario.  Without that file it logs a misleading CRITICAL glob
failure before continuing, so release smoke output is not warning-free.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CONFIG = _ROOT / "molecule" / "default" / "molecule.yml"
_MAKEFILE = _ROOT / "Makefile"


def test_canonical_default_scenario_is_loadable() -> None:
    """The default scenario must exist and be a harmless no-op configuration."""
    assert _DEFAULT_CONFIG.is_file()
    data = yaml.safe_load(_DEFAULT_CONFIG.read_text())
    assert isinstance(data, dict)
    assert data.get("driver", {}).get("name") == "default"
    assert data.get("provisioner", {}).get("name") == "ansible"
    assert data.get("scenario", {}).get("test_sequence") == []


def test_molecule_clean_preserves_canonical_default_scenario() -> None:
    """Cleanup must not delete the config Molecule probes for every run."""
    makefile = _MAKEFILE.read_text()
    clean_recipe = re.search(
        r"^molecule-clean:\n(?P<body>(?:\t.*\n)+)",
        makefile,
        re.MULTILINE,
    )
    assert clean_recipe is not None
    assert re.search(
        r"playbooks\|roles\|internal_tools\|mock_daemon\|library\|default",
        clean_recipe.group("body"),
    )


def test_molecule_runner_keeps_named_scenarios_separate_from_default() -> None:
    """The requested scenario remains named while the default config is present."""
    makefile = _MAKEFILE.read_text()
    runner = re.search(
        r"^molecule-test:\n(?P<body>(?:\t.*\n)+)",
        makefile,
        re.MULTILINE,
    )
    assert runner is not None
    body = runner.group("body")
    assert 'MOLECULE_GLOB="molecule/playbooks/*/molecule.yml"' in body
    assert 'molecule test -s "$(SCENARIO)"' in body


def test_molecule_runner_uses_fresh_state_without_cross_runtime_reset() -> None:
    """Fresh namespaced state must isolate caches without probing another runtime."""
    makefile = _MAKEFILE.read_text()
    runner = re.search(
        r"^molecule-test:\n(?P<body>(?:\t.*\n)+)",
        makefile,
        re.MULTILINE,
    )
    assert runner is not None
    body = runner.group("body")
    state = body.index('mktemp -d "/tmp/gludd-molecule-$(SCENARIO).XXXXXX"')
    test = body.index('molecule test -s "$(SCENARIO)"')
    assert state < test
    assert 'molecule reset -s "$(SCENARIO)"' not in body


def test_molecule_runner_uses_isolated_ansible_home() -> None:
    """A named run must isolate default and named Molecule cache entries."""
    makefile = _MAKEFILE.read_text()
    runner = re.search(
        r"^molecule-test:\n(?P<body>(?:\t.*\n)+)",
        makefile,
        re.MULTILINE,
    )
    assert runner is not None
    body = runner.group("body")
    assert "mktemp -d" in body
    assert "/tmp/gludd-molecule-$(SCENARIO)" in body
    assert "ANSIBLE_HOME" in body
    assert "MOLECULE_EPHEMERAL_DIRECTORY" not in body
    assert 'rm -rf "$$ANSIBLE_STATE_DIR"' in body


__all__ = [
    "test_canonical_default_scenario_is_loadable",
    "test_molecule_clean_preserves_canonical_default_scenario",
    "test_molecule_runner_keeps_named_scenarios_separate_from_default",
    "test_molecule_runner_uses_fresh_state_without_cross_runtime_reset",
    "test_molecule_runner_uses_isolated_ansible_home",
]
