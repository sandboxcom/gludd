from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from pytest import MonkeyPatch


def _module() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "normalize_task_integrity.py"
    spec = importlib.util.spec_from_file_location("normalize_task_integrity", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_reopens_checked_item_without_measurable_evidence(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    module = _module()
    tasks = tmp_path / "TASKS.md"
    tasks.write_text("- [x] LEGACY-1 — old claim\n")
    monkeypatch.setattr(module, "TASKS", tasks)

    changed, reopened, renamed = module.normalize()

    assert (changed, reopened, renamed) == (1, 1, 0)
    result = tasks.read_text()
    assert result.startswith("- [ ] LEGACY-1")
    assert "priority: medium" in result
    assert "effort: M" in result
    assert "status: pending" in result
    assert "evidence:" not in result


def test_normalize_preserves_prior_wave_evidence_as_pending(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    module = _module()
    tasks = tmp_path / "TASKS.md"
    tasks.write_text("- [x] LEGACY-2 — old claim | evidence: Wave 34\n")
    monkeypatch.setattr(module, "TASKS", tasks)

    module.normalize()

    result = tasks.read_text()
    assert "- [ ] LEGACY-2" in result
    assert "status: pending" in result
    assert "prior-evidence: Wave 34" in result


def test_normalize_canonicalizes_fields_and_duplicate_ids(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    module = _module()
    tasks = tmp_path / "TASKS.md"
    tasks.write_text(
        "- [x] DUP.1 — first | priority: critical | effort: large | status: done | evidence: 12 tests\n"
        "- [x] DUP.1 — second | priority: low | effort: small | status: completed | evidence: commit abcdef1\n"
    )
    monkeypatch.setattr(module, "TASKS", tasks)

    changed, reopened, renamed = module.normalize()

    assert changed == 2
    assert reopened == 0
    assert renamed == 1
    result = tasks.read_text()
    assert "DUP.1 — first" in result
    assert "DUP.1-legacy-2 — second" in result
    assert "priority: high" in result
    assert "effort: L" in result
    assert "effort: S" in result
    assert "status: completed" in result


def test_normalize_reopens_checked_completion_with_pending_evidence(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    module = _module()
    tasks = tmp_path / "TASKS.md"
    tasks.write_text(
        "- [x] S1 — claim | evidence: 12 tests GREEN; full gate pending "
        "| priority: high | effort: S | status: completed\n"
    )
    monkeypatch.setattr(module, "TASKS", tasks)

    changed, reopened, renamed = module.normalize()

    assert (changed, reopened, renamed) == (1, 1, 0)
    result = tasks.read_text()
    assert result.startswith("- [ ] S1")
    assert "status: pending" in result
    assert "prior-evidence: 12 tests GREEN; full gate pending" in result
