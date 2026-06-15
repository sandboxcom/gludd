"""Tests for hot-rotation of validated leaf code modules.

``reload_code_module`` snapshots the live file bytes (rollback buffer),
``os.replace``s the candidate file over the live path, ``importlib.reload``s the
module, and runs a health gate. On a failed health gate it restores the
original bytes, reloads again, and reports a rollback — the live module must end
up exactly as it started.
"""

from __future__ import annotations

import importlib
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

from general_ludd.reload.hot_reloader import HotReloader


def _install_live_module(tmp_path: Path, name: str, body: str) -> tuple[Path, str, object]:
    """Create an importable live module on a tmp sys.path and import it.

    Uses a per-call unique package name so tests (and xdist workers) never
    collide on a cached ``live_pkg.<name>`` from a different tmp dir.

    Returns (module_file_path, fully_qualified_module_name, module_object).
    """
    pkg = f"live_pkg_{uuid.uuid4().hex[:8]}"
    pkg_dir = tmp_path / pkg
    pkg_dir.mkdir(exist_ok=True)
    (pkg_dir / "__init__.py").write_text("")
    mod_path = pkg_dir / f"{name}.py"
    mod_path.write_text(textwrap.dedent(body))
    sys.path.insert(0, str(tmp_path))
    importlib.invalidate_caches()
    fqmn = f"{pkg}.{name}"
    mod = importlib.import_module(fqmn)
    return mod_path, fqmn, mod


@pytest.fixture
def reloader(tmp_path: Path) -> HotReloader:
    return HotReloader(config_dir=str(tmp_path / "config"))


def test_reload_swaps_in_candidate_when_healthy(tmp_path: Path, reloader: HotReloader) -> None:
    mod_path, fqmn, mod = _install_live_module(
        tmp_path,
        "leafa",
        """
        VERSION = "v1"

        def value():
            return 1
        """,
    )
    assert mod.value() == 1  # type: ignore[attr-defined]

    candidate = tmp_path / "candidate_leafa.py"
    candidate.write_text(
        textwrap.dedent(
            """
            VERSION = "v2"

            def value():
                return 2
            """
        )
    )

    result = reloader.reload_code_module(
        module_name=fqmn,
        candidate_source_path=str(candidate),
        health_check=lambda: True,
    )
    assert result.success is True
    reloaded = importlib.import_module(fqmn)
    assert reloaded.value() == 2  # type: ignore[attr-defined]
    assert mod_path.read_text().strip().endswith('return 2')


def test_reload_rolls_back_when_health_gate_fails(tmp_path: Path, reloader: HotReloader) -> None:
    mod_path, fqmn, _mod = _install_live_module(
        tmp_path,
        "leafb",
        """
        VERSION = "v1"

        def value():
            return 1
        """,
    )
    original_bytes = mod_path.read_bytes()

    candidate = tmp_path / "candidate_leafb.py"
    candidate.write_text(
        textwrap.dedent(
            """
            VERSION = "v2-broken"

            def value():
                return 999
            """
        )
    )

    result = reloader.reload_code_module(
        module_name=fqmn,
        candidate_source_path=str(candidate),
        health_check=lambda: False,  # health gate fails ⇒ rollback
    )
    assert result.success is False
    assert result.details.get("rolled_back") is True
    # File bytes restored exactly.
    assert mod_path.read_bytes() == original_bytes
    # Live module restored to v1 behaviour.
    reloaded = importlib.import_module(fqmn)
    assert reloaded.value() == 1  # type: ignore[attr-defined]


def test_reload_unknown_module_fails_closed(reloader: HotReloader, tmp_path: Path) -> None:
    candidate = tmp_path / "c.py"
    candidate.write_text("X = 1\n")
    result = reloader.reload_code_module(
        module_name="general_ludd.this_module_does_not_exist_xyz",
        candidate_source_path=str(candidate),
        health_check=lambda: True,
    )
    assert result.success is False


def test_reload_missing_candidate_fails_closed(tmp_path: Path, reloader: HotReloader) -> None:
    _mod_path, fqmn, _mod = _install_live_module(
        tmp_path,
        "leafc",
        """
        def value():
            return 1
        """,
    )
    result = reloader.reload_code_module(
        module_name=fqmn,
        candidate_source_path=str(tmp_path / "does_not_exist.py"),
        health_check=lambda: True,
    )
    assert result.success is False


def test_workflow_reload_if_needed_uses_real_hot_reloader(tmp_path: Path) -> None:
    """SelfImprovementWorkflow.reload_if_needed performs a REAL hot-rotation
    (os.replace + importlib.reload + health gate) when a code target is armed,
    and reports the real verdict — not the in-memory manager stub."""
    from general_ludd.reload.self_improve import ApplyResult, SelfImprovementWorkflow

    _mod_path, fqmn, mod = _install_live_module(
        tmp_path,
        "leafw",
        """
        def value():
            return 1
        """,
    )
    assert mod.value() == 1  # type: ignore[attr-defined]

    candidate = tmp_path / "candidate_leafw.py"
    candidate.write_text("def value():\n    return 7\n")

    wf = SelfImprovementWorkflow(config_dir=str(tmp_path / "config"))
    wf.set_code_target(fqmn, str(candidate), health_check=lambda: True)
    ar = ApplyResult(todo_id="SI-x", applied=True, reload_needed=True, validation_passed=True)
    result = wf.reload_if_needed(ar)

    assert result.status == "success"
    reloaded = importlib.import_module(fqmn)
    assert reloaded.value() == 7  # type: ignore[attr-defined]


def test_workflow_reload_rolls_back_on_degraded(tmp_path: Path) -> None:
    """When the post-promote health gate reports degraded, the workflow's real
    reload rolls back and reports failed."""
    from general_ludd.reload.self_improve import ApplyResult, SelfImprovementWorkflow

    mod_path, fqmn, _mod = _install_live_module(
        tmp_path,
        "leafd",
        """
        def value():
            return 1
        """,
    )
    original = mod_path.read_bytes()
    candidate = tmp_path / "candidate_leafd.py"
    candidate.write_text("def value():\n    return 999\n")

    wf = SelfImprovementWorkflow(config_dir=str(tmp_path / "config"))
    wf.set_code_target(fqmn, str(candidate), health_check=lambda: False)
    ar = ApplyResult(todo_id="SI-y", applied=True, reload_needed=True, validation_passed=True)
    result = wf.reload_if_needed(ar)

    assert result.status == "failed"
    assert "roll" in result.message.lower()
    assert mod_path.read_bytes() == original
    reloaded = importlib.import_module(fqmn)
    assert reloaded.value() == 1  # type: ignore[attr-defined]
