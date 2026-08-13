"""Integration tests for the ``general_ludd.agent.build_presentation`` role.

Validates that the role codifies the reveal.js deck build / validate / deploy
pipeline as a repeatable gludd-managed task:

1. ``test_role_structure`` — the 4-file role structure exists and parses.
2. ``test_role_uses_gludd_facts`` — ``tasks/main.yml`` gathers live project
   stats via ``gludd_facts`` (honest deck numbers, not hardcoded).
3. ``test_role_runs_honesty_check`` — ``tasks/main.yml`` runs the deck honesty
   lint (banned marketing tokens + %-must-match-README) via ``make deck-honesty``.
4. ``test_molecule_scenario_exists`` — the molecule scenario files exist so
   the role can be exercised end-to-end against the mock daemon.

These are pytest-level structural + behavioral tests (per the established
precedent: molecule infrastructure may not be present in every environment —
pytest structural validation is the accepted fallback). The molecule scenario
under ``molecule/playbooks/role_build_presentation/`` provides the end-to-end
coverage.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
COLLECTION_DIR = ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
ROLE_DIR = COLLECTION_DIR / "roles" / "build_presentation"
MOLECULE_SCENARIO = ROOT / "molecule" / "playbooks" / "role_build_presentation"


def _load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text())


def _load_role_tasks() -> list:
    main = _load_yaml(ROLE_DIR / "tasks" / "main.yml")
    assert isinstance(main, list)
    return list(main)


def test_role_structure() -> None:
    """The 4-file role structure exists and parses as valid YAML/markdown."""
    expected = [
        ROLE_DIR / "tasks" / "main.yml",
        ROLE_DIR / "defaults" / "main.yml",
        ROLE_DIR / "meta" / "main.yml",
        ROLE_DIR / "README.md",
    ]
    for path in expected:
        assert path.is_file(), f"missing role file: {path}"

    # All three YAML files must parse cleanly.
    for yml in expected[:3]:
        loaded = _load_yaml(yml)
        assert loaded is not None, f"{yml} parsed to None"

    # defaults must declare the four task-spec variables.
    defaults = _load_yaml(ROLE_DIR / "defaults" / "main.yml")
    assert isinstance(defaults, dict)
    for var in ("presentation_dir", "deck_data_source", "honesty_check_enabled", "deploy_target"):
        assert var in defaults, f"defaults/main.yml must define {var}"

    # meta must name the role.
    meta = _load_yaml(ROLE_DIR / "meta" / "main.yml")
    assert isinstance(meta, dict)
    galaxy = meta.get("galaxy_info", {})
    assert galaxy.get("role_name") == "build_presentation"


def test_role_uses_gludd_facts() -> None:
    """tasks/main.yml must gather live project stats via gludd_facts."""
    tasks = _load_role_tasks()
    joined = json.dumps(tasks, default=str)
    assert "general_ludd.agent.gludd_facts" in joined, (
        "build_presentation must invoke general_ludd.agent.gludd_facts for "
        "honest deck stats (not hardcoded numbers)"
    )


def test_role_runs_honesty_check() -> None:
    """tasks/main.yml must run the deck honesty lint via `make deck-honesty`.

    The honesty lint is the core anti-marketing-spin guarantee: banned tokens
    (production-ready, blazing, seamless, etc.) and every % must trace back
    to a machine-produced source. Without it the role would let fabricated
    stats ship to the deck.
    """
    tasks = _load_role_tasks()
    joined = json.dumps(tasks, default=str)
    # The role invokes the honesty make target.
    assert "deck-honesty" in joined, (
        "build_presentation must run `make deck-honesty` — the honesty lint "
        "(banned marketing tokens + %-must-match-README) is non-negotiable"
    )
    # And it gates the lint behind the honesty_check_enabled toggle.
    assert "honesty_check_enabled" in joined, (
        "the honesty lint step must be gated by the honesty_check_enabled var"
    )


def test_readme_documents_mermaid_preference() -> None:
    """README.md must document Mermaid as the preferred diagram format.

    Mermaid text diagrams are version-controlled, diff-friendly, and render in
    the reveal.js deck without binary artifacts — preferred over SVG so the
    deck stays editable in-tree.
    """
    readme = (ROLE_DIR / "README.md").read_text().lower()
    assert "mermaid" in readme, (
        "build_presentation README must document Mermaid as the preferred "
        "diagram format (over SVG)"
    )


def test_tasks_validate_mermaid_syntax() -> None:
    """tasks/main.yml must include a Mermaid syntax validation step.

    The step is safe-by-default: it runs only when a Mermaid CLI is available
    and skips otherwise (no hard dependency on the toolchain).
    """
    tasks = _load_role_tasks()
    joined = json.dumps(tasks, default=str).lower()
    assert "mermaid" in joined, (
        "build_presentation tasks must include a Mermaid syntax validation "
        "step (optional, skipped when the mermaid CLI is unavailable)"
    )


def test_mermaid_find_is_guarded_by_directory_stat() -> None:
    """Absent presentation directories must not be passed to ``find``.

    ``ansible.builtin.find`` succeeds but emits a warning for a missing or
    non-directory path.  The role supports build-only runs where the reveal.js
    directory is intentionally absent, so it must stat the directory and skip
    the search without producing that warning.
    """
    tasks = _load_role_tasks()
    stat_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("register") == "_bp_presentation_dir_stat"
    )
    find_index, find_task = next(
        (index, task)
        for index, task in enumerate(tasks)
        if task.get("register") == "_bp_mmd_files"
    )

    assert stat_index < find_index
    conditions = json.dumps(find_task.get("when", []))
    assert "_bp_presentation_dir_stat.stat.exists" in conditions
    assert "_bp_presentation_dir_stat.stat.isdir" in conditions


def test_defaults_declare_mermaid_toggle() -> None:
    """defaults/main.yml must declare the mermaid validation toggle."""
    defaults = _load_yaml(ROLE_DIR / "defaults" / "main.yml")
    assert isinstance(defaults, dict)
    assert "validate_mermaid_syntax" in defaults, (
        "defaults/main.yml must define validate_mermaid_syntax"
    )


def test_molecule_scenario_exists() -> None:
    """The molecule scenario files exist so the role runs end-to-end."""
    assert MOLECULE_SCENARIO.is_dir(), f"missing molecule scenario dir: {MOLECULE_SCENARIO}"
    expected = [
        MOLECULE_SCENARIO / "molecule.yml",
        MOLECULE_SCENARIO / "default" / "prepare.yml",
        MOLECULE_SCENARIO / "default" / "converge.yml",
        MOLECULE_SCENARIO / "default" / "verify.yml",
    ]
    for path in expected:
        assert path.is_file(), f"missing molecule scenario file: {path}"

    # molecule.yml must configure the collection path + a mock port.
    molecule_cfg = (MOLECULE_SCENARIO / "molecule.yml").read_text()
    assert "ANSIBLE_COLLECTIONS_PATH" in molecule_cfg
    assert "GLUDD_MOCK_PORT" in molecule_cfg

    # converge.yml must invoke the role.
    converge = (MOLECULE_SCENARIO / "default" / "converge.yml").read_text()
    assert "general_ludd.agent.build_presentation" in converge
