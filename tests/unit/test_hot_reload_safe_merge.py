"""Anti-clobber wiring for HotReloader.reload_code_module (issue #70).

The clobber-bug class: a candidate code/config change is generated against a
*base* snapshot of the live file. If, between generation and apply, the live
file DIVERGES (a concurrent edit), a whole-file ``os.replace`` of the candidate
over the live path silently REVERTS that concurrent edit — the same data-loss
bug fixed in the orchestrator's wt-sync via ``integration/safe_merge``.

The fix routes the file-application through ``safe_merge`` when the caller
supplies the ``base_source_path`` the candidate was diffed from:

* base == ours (no concurrent edit) -> candidate applied verbatim (unchanged
  legacy behaviour).
* ours diverged but DISJOINT from the candidate's hunks -> a clean 3-way merge
  keeps BOTH the concurrent edit and the candidate change.
* ours diverged and OVERLAPS the candidate's hunks -> REFUSE the reload
  (fail-closed), surface the conflict, write nothing, leave the live module
  byte-for-byte as it started.
"""

from __future__ import annotations

import importlib
import sys
import textwrap
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from general_ludd.reload.hot_reloader import HotReloader


def _install_live_module(tmp_path: Path, name: str, body: str) -> tuple[Path, str, ModuleType]:
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


def test_disjoint_divergence_is_three_way_merged_not_clobbered(
    tmp_path: Path, reloader: HotReloader
) -> None:
    """A live file that diverged from base in a DISJOINT region must be 3-way
    merged with the candidate — the concurrent edit survives, not clobbered."""
    base_body = textwrap.dedent(
        """\
        HEADER = "top"


        def value():
            return 1


        FOOTER = "bottom"
        """
    )
    mod_path, fqmn, _mod = _install_live_module(tmp_path, "leafm", base_body)

    # Base snapshot the candidate was generated against.
    base = tmp_path / "base_leafm.py"
    base.write_text(base_body)

    # Concurrent edit landed on the LIVE file: only the FOOTER line changed.
    live_diverged = base_body.replace('FOOTER = "bottom"', 'FOOTER = "CONCURRENT"')
    mod_path.write_text(live_diverged)

    # Candidate changes only value() — a region disjoint from FOOTER.
    candidate = tmp_path / "candidate_leafm.py"
    candidate.write_text(base_body.replace("return 1", "return 2"))

    result = reloader.reload_code_module(
        module_name=fqmn,
        candidate_source_path=str(candidate),
        base_source_path=str(base),
        health_check=lambda: True,
    )

    assert result.success is True, result.error
    merged = mod_path.read_text()
    # BOTH edits present — neither clobbered.
    assert "return 2" in merged
    assert 'FOOTER = "CONCURRENT"' in merged
    assert result.details.get("merged") is True

    reloaded = importlib.import_module(fqmn)
    assert cast(Any, reloaded).value() == 2


def test_overlapping_divergence_refuses_reload_fail_closed(
    tmp_path: Path, reloader: HotReloader
) -> None:
    """When the live file diverged from base on the SAME lines the candidate
    rewrites, the reload is REFUSED — the live module is untouched and the
    conflict is surfaced (no clobber, no silent pick)."""
    base_body = textwrap.dedent(
        """\
        def value():
            return 1
        """
    )
    mod_path, fqmn, live_mod = _install_live_module(tmp_path, "leafo", base_body)

    base = tmp_path / "base_leafo.py"
    base.write_text(base_body)

    # Concurrent edit rewrote the SAME return line, and the live module picked
    # it up (e.g. a separate reload) so the running state is genuinely v42.
    live_diverged = base_body.replace("return 1", "return 42")
    mod_path.write_text(live_diverged)
    importlib.invalidate_caches()
    importlib.reload(live_mod)
    assert cast(Any, live_mod).value() == 42
    original_bytes = mod_path.read_bytes()

    # Candidate ALSO rewrites that line, differently -> true conflict.
    candidate = tmp_path / "candidate_leafo.py"
    candidate.write_text(base_body.replace("return 1", "return 7"))

    result = reloader.reload_code_module(
        module_name=fqmn,
        candidate_source_path=str(candidate),
        base_source_path=str(base),
        health_check=lambda: True,
    )

    assert result.success is False
    assert result.details.get("conflict") is True
    # No clobber: live file untouched, no rollback theatre needed.
    assert mod_path.read_bytes() == original_bytes
    # The live module still behaves as the concurrent edit left it.
    reloaded = importlib.import_module(fqmn)
    assert cast(Any, reloaded).value() == 42


def test_no_divergence_applies_candidate_verbatim(
    tmp_path: Path, reloader: HotReloader
) -> None:
    """When the live file equals base (no concurrent edit), the candidate is
    applied as-is — merge wiring is a no-op on the happy path."""
    base_body = "def value():\n    return 1\n"
    mod_path, fqmn, _mod = _install_live_module(tmp_path, "leafn", base_body)

    base = tmp_path / "base_leafn.py"
    base.write_text(base_body)
    candidate = tmp_path / "candidate_leafn.py"
    candidate.write_text("def value():\n    return 9\n")

    result = reloader.reload_code_module(
        module_name=fqmn,
        candidate_source_path=str(candidate),
        base_source_path=str(base),
        health_check=lambda: True,
    )

    assert result.success is True, result.error
    assert mod_path.read_text() == "def value():\n    return 9\n"
    reloaded = importlib.import_module(fqmn)
    assert cast(Any, reloaded).value() == 9


def test_missing_base_preserves_legacy_blind_swap(
    tmp_path: Path, reloader: HotReloader
) -> None:
    """Without a base_source_path the API is unchanged: the candidate is swapped
    over the live file as before (backward compatibility)."""
    base_body = "def value():\n    return 1\n"
    mod_path, fqmn, _mod = _install_live_module(tmp_path, "leafl", base_body)
    candidate = tmp_path / "candidate_leafl.py"
    candidate.write_text("def value():\n    return 5\n")

    result = reloader.reload_code_module(
        module_name=fqmn,
        candidate_source_path=str(candidate),
        health_check=lambda: True,
    )

    assert result.success is True, result.error
    assert mod_path.read_text() == "def value():\n    return 5\n"
