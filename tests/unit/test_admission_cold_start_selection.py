"""Native controls for the fixed admission's initial-path sibling filter."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from tests.fixtures.admission_cold_start import snapshot_plugin
from tests.unit import test_runtime_admission_scheduling as scheduling
from tests.unit.test_runtime_admission_scheduling import (
    APPROVED_PROMOTION_SELECTORS,
    HELP,
    ROOT,
    ROSTER,
    _capture_owner,
    _stanza,
)

FIXTURE_PLUGIN = ROOT / "tests/fixtures/admission_cold_start/snapshot_plugin.py"
STRUCTURAL_NODE = (
    "tests/unit/test_admission_cold_start_selection.py::test_real_admission_filters_only_never_selected_main_siblings"
)


def test_real_admission_filters_only_never_selected_main_siblings(tmp_path: Path) -> None:
    """Reuse the existing exact roster and actual Make argument-capture owner."""
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [row["files"] for row in rows] == [list(ROSTER), [HELP]]
    assert ROSTER.count(STRUCTURAL_NODE) == 1
    assert not any(
        node.split("::", 1)[0] == STRUCTURAL_NODE.split("::", 1)[0] and node != STRUCTURAL_NODE for node in ROSTER
    )
    assert all((ROOT / node.split("::", 1)[0]).is_file() for node in (*ROSTER, HELP))
    flags = [str(row["flags"]) for row in rows]
    assert "--ignore-glob=tests/*" in flags[0]
    assert "--ignore" not in flags[1]
    assert "-W error" in flags[0] and "-n 2 --maxprocesses 2" in flags[0]
    assert "-W error" in flags[1] and "-n 0 --maxprocesses 1" in flags[1]
    assert "--ignore-glob" not in _stanza("test-files")
    for flag in flags:
        assert not any(
            token in flag
            for token in (
                "-k ",
                "--deselect",
                "--noconftest",
                "--disable-plugin-autoload",
                "--assert=plain",
                "-W ignore",
                "--collect-only",
            )
        )
    manifest = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    assert manifest["full_gate_target"] == "gate" and manifest["full_gate_required"] is True
    assert {
        "id": "runtime-admission-initial-path-sibling-collection",
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node",
        "node": STRUCTURAL_NODE,
        "full_gate_phase": "test",
    } in manifest["families"]
    phase = next(
        row
        for row in manifest["runtime_contract"]["phases"]
        if row["target"] == "_pytest-runtime-compatibility-admission"
    )
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)


@pytest.mark.parametrize("promoted_selector", APPROVED_PROMOTION_SELECTORS)
@pytest.mark.parametrize("mutation", ("omission", "duplication", "widening"))
@pytest.mark.parametrize(
    "guard",
    (
        scheduling.test_runtime_admission_retains_entire_roster_and_fresh_help,
        test_real_admission_filters_only_never_selected_main_siblings,
    ),
    ids=("scheduling", "cold-start"),
)
def test_admission_contracts_reject_mutated_promoted_selectors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    promoted_selector: str,
    mutation: str,
    guard: Callable[[Path], None],
) -> None:
    """Exercise both unchanged guards through the real Make argument-capture harness."""
    original_stanza = scheduling._stanza
    owner_stanza = original_stanza(scheduling.OWNER)
    selected = promoted_selector + " "
    assert owner_stanza.count(selected) == 1
    if mutation == "omission":
        replacement = ""
    elif mutation == "duplication":
        replacement = selected * 2
    else:
        assert mutation == "widening"
        replacement = promoted_selector.split("::", 1)[0] + " "
    mutated_stanza = owner_stanza.replace(selected, replacement, 1)
    assert mutated_stanza != owner_stanza
    monkeypatch.setattr(
        scheduling,
        "_stanza",
        lambda target: mutated_stanza if target == scheduling.OWNER else original_stanza(target),
    )
    with pytest.raises(AssertionError):
        guard(tmp_path)


def _fixture_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    package = tmp_path / "admission_native_package"
    package.mkdir()
    (package / "__init__.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "def setup_module():\n"
        "    with Path(os.environ['COLD_START_LIFECYCLE']).open('a') as stream:\n"
        "        stream.write('package-setup\\n')\n"
        "def teardown_module():\n"
        "    with Path(os.environ['COLD_START_LIFECYCLE']).open('a') as stream:\n"
        "        stream.write('package-teardown\\n')\n",
        encoding="utf-8",
    )
    (tmp_path / "conftest.py").write_text(
        "import pytest\n@pytest.fixture\ndef parent_control(): return 'parent-loaded'\n",
        encoding="utf-8",
    )
    (package / "conftest.py").write_text(
        "import pytest\n@pytest.fixture\ndef package_control(): return 'package-loaded'\n",
        encoding="utf-8",
    )
    first = package / "test_selected_a.py"
    first.write_text(
        "import pytest\n@pytest.mark.parametrize('value', [0, 1])\n"
        "def test_selected_a(value, parent_control, package_control, plugin_control):\n"
        "    assert value in (0, 1)\n"
        "    assert parent_control == 'parent-loaded'\n"
        "    assert package_control == 'package-loaded'\n"
        "    assert plugin_control == 'plugin-loaded'\n",
        encoding="utf-8",
    )
    second = package / "test_selected_b.py"
    second.write_text(
        "import pytest\nclass TestExplicit:\n"
        "    @pytest.mark.parametrize('value', ['x', 'y'])\n"
        "    def test_selected_b(self, value, parent_control, package_control, plugin_control):\n"
        "        assert value in ('x', 'y')\n"
        "        assert parent_control == 'parent-loaded'\n"
        "        assert package_control == 'package-loaded'\n"
        "        assert plugin_control == 'plugin-loaded'\n"
        "    def test_never_selected(self):\n        assert False, 'node selector must stay exact'\n",
        encoding="utf-8",
    )
    outside = tmp_path / "outside_control.py"
    outside.write_text(
        "def test_outside(parent_control, plugin_control):\n"
        "    assert parent_control == 'parent-loaded'\n    assert plugin_control == 'plugin-loaded'\n",
        encoding="utf-8",
    )
    (tmp_path / "admission_fixture_plugin.py").write_text(
        "import pytest\n@pytest.fixture\ndef plugin_control(): return 'plugin-loaded'\n",
        encoding="utf-8",
    )
    for index in range(48):
        (package / f"test_unused_{index:03d}.py").write_text(
            "raise AssertionError('unselected sibling must not be imported')\n",
            encoding="utf-8",
        )
    return first, second, outside


def test_diagnostic_fixture_is_opt_in_and_accepts_absent_fixture_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COLD_START_PROBE_ROOT", raising=False)
    monkeypatch.delenv("COLD_START_SNAPSHOT_ROOT", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    manager = SimpleNamespace(
        get_plugin=lambda _name: None,
        list_plugin_distinfo=lambda: [],
        _conftest_plugins=set(),
    )
    session = cast(pytest.Session, SimpleNamespace(config=SimpleNamespace(pluginmanager=manager), items=[]))
    collector: Callable[[Path], object] = snapshot_plugin.pytest_collect_file
    finish_snapshot: Callable[[pytest.Session], object] = snapshot_plugin.pytest_collection_finish
    assert collector(tmp_path / "unselected.py") is None
    assert finish_snapshot(session) is None
    assert list(tmp_path.iterdir()) == []
    destination = tmp_path / "explicit-diagnostics"
    monkeypatch.setenv("COLD_START_SNAPSHOT_ROOT", str(destination))
    assert finish_snapshot(session) is None
    receipts = list(destination.glob("controller-*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["nodes"] == receipt["plugins"] == receipt["conftests"] == []
    assert receipt["fixtures"] == {}
    assert receipt["gate_evidence"] is False and receipt["release_ready"] is False


def _run(
    tmp_path: Path,
    files: list[str],
    *,
    candidate: bool,
    run_id: str,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]], list[dict[str, str]]]:
    assert FIXTURE_PLUGIN.is_file(), "the native snapshot fixture must be tracked under ROOT"
    events = tmp_path / f"collector-{run_id}.jsonl"
    snapshots = tmp_path / f"snapshots-{run_id}"
    lifecycle = tmp_path / f"lifecycle-{run_id}.log"
    flags = "-W error -q -n 2 --maxprocesses 2"
    if candidate:
        flags += " --ignore-glob=" + str(tmp_path / "admission_native_package" / "*")
    environment = {
        **os.environ,
        "UV_NO_SYNC": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join([str(tmp_path), str(ROOT), str(ROOT / "src")]),
        "PYTEST_PLUGINS": ",".join(
            filter(
                None,
                (
                    os.environ.get("PYTEST_PLUGINS", ""),
                    "tests.fixtures.admission_cold_start.snapshot_plugin",
                    "admission_fixture_plugin",
                ),
            )
        ),
        "COLD_START_PROBE_ROOT": str(tmp_path / "admission_native_package"),
        "COLD_START_PROBE_LOG": str(events),
        "COLD_START_SNAPSHOT_ROOT": str(snapshots),
        "COLD_START_LIFECYCLE": str(lifecycle),
    }
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "test-files",
            "TESTFILES=" + " ".join(files),
            "PYTEST_ARGS=" + flags,
            "NPROC=2",
            "GLUDD_XDIST_WORKERS=2",
            "UV_PROJECT_ENVIRONMENT=" + os.environ.get("VIRTUAL_ENV", str(ROOT / ".venv")),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=35,
    )
    captured = [
        cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(snapshots.glob("gw*.json"))
    ]
    calls = (
        [cast(dict[str, str], json.loads(line)) for line in events.read_text(encoding="utf-8").splitlines()]
        if events.exists()
        else []
    )
    return result, captured, calls


def test_native_initial_files_nodes_marks_plugins_and_all_parent_fixtures_are_identical(tmp_path: Path) -> None:
    first, second, outside = _fixture_tree(tmp_path)
    files = [str(first), str(second) + "::TestExplicit::test_selected_b", str(outside)]
    original, original_snapshots, original_calls = _run(tmp_path, files, candidate=False, run_id="original")
    proposed, proposed_snapshots, proposed_calls = _run(tmp_path, files, candidate=True, run_id="proposed")
    assert original.returncode == proposed.returncode == 0, (
        original.stdout + original.stderr + proposed.stdout + proposed.stderr
    )
    assert "5 passed" in original.stdout and "5 passed" in proposed.stdout
    assert len(original_snapshots) == len(proposed_snapshots) == 2
    for before, after in zip(original_snapshots, proposed_snapshots, strict=True):
        for field in ("nodes", "plugins", "conftests", "fixtures"):
            assert before[field] == after[field], field
        assert len(cast(list[object], before["nodes"])) == 5
        assert {str((tmp_path / "conftest.py").resolve()), str((first.parent / "conftest.py").resolve())} <= set(
            cast(list[str], before["conftests"]),
        )
        fixtures = cast(dict[str, list[dict[str, object]]], before["fixtures"])
        assert fixtures["parent_control"] and fixtures["package_control"]
        assert fixtures["plugin_control"][0]["module"] == "admission_fixture_plugin"
    assert any("test_unused_" in row["file"] for row in original_calls)
    assert not any("test_unused_" in row["file"] for row in proposed_calls)
    for run_id in ("original", "proposed"):
        lifecycle = (tmp_path / f"lifecycle-{run_id}.log").read_text(encoding="utf-8").splitlines()
        assert lifecycle.count("package-setup") == lifecycle.count("package-teardown")
        assert 1 <= lifecycle.count("package-setup") <= 2


@pytest.mark.parametrize("failure", ["warning", "assertion", "missing"])
def test_native_selected_failures_cannot_be_hidden_by_sibling_filter(tmp_path: Path, failure: str) -> None:
    first, _second, _outside = _fixture_tree(tmp_path)
    selected = first.parent / "test_selected_failure.py"
    if failure == "warning":
        selected.write_text(
            "import warnings\ndef test_selected_failure():\n    warnings.warn('owned-warning-control', UserWarning)\n",
            encoding="utf-8",
        )
        expected = "UserWarning: owned-warning-control"
    elif failure == "assertion":
        selected.write_text(
            "def test_selected_failure():\n    assert 1 == 2, 'owned-assertion-control'\n",
            encoding="utf-8",
        )
        expected = "owned-assertion-control"
    else:
        expected = "no tests ran"
    original, _original_snapshots, _original_calls = _run(
        tmp_path,
        [str(selected)],
        candidate=False,
        run_id="original-" + failure,
    )
    result, _snapshots, _calls = _run(tmp_path, [str(selected)], candidate=True, run_id="proposed-" + failure)
    assert result.returncode == original.returncode != 0
    assert expected in result.stdout + result.stderr
    assert expected in original.stdout + original.stderr
    assert re.findall(r"Error (\d+)", result.stderr) == re.findall(r"Error (\d+)", original.stderr)
