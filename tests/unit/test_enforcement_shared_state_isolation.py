"""Regression tests for shared enforcement-state isolation under xdist."""

from __future__ import annotations

from pathlib import Path

import pytest

import tests.conftest as ct
import tests.unit._hook_fixtures as hook_fixtures


class _FakeItem:
    def __init__(
        self,
        path: Path,
        *,
        fixtures: tuple[str, ...] = (),
        group: str | None = None,
    ) -> None:
        self.path = path
        self.nodeid = f"{path}::test_example"
        self.fixturenames = fixtures
        self.own_markers: list[pytest.Mark] = []
        if group is not None:
            self.own_markers.append(pytest.mark.xdist_group(group).mark)

    def add_marker(self, marker: pytest.MarkDecorator | pytest.Mark) -> None:
        self.own_markers.append(getattr(marker, "mark", marker))

    def iter_markers(self, name: str | None = None):
        for marker in self.own_markers:
            if name is None or marker.name == name:
                yield marker

    def listchain(self) -> list[_FakeItem]:
        return [self]


def _group_names(item: _FakeItem) -> list[str]:
    names: list[str] = []
    for marker in item.iter_markers("xdist_group"):
        name = marker.kwargs.get("name")
        if name is None and marker.args:
            name = marker.args[0]
        names.append(str(name))
    return names


def _collect(item: _FakeItem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ct, "_REPO_ROOT", tmp_path)
    ct.pytest_collection_modifyitems([item])


def test_hardcoded_gludd_tmp_source_gets_canonical_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "test_shared_state.py"
    source.write_text('STATE = Path("/tmp/gludd-shared-state.json")\n')
    item = _FakeItem(source)

    _collect(item, tmp_path, monkeypatch)

    assert _group_names(item) == [ct.ENFORCEMENT_SHARED_STATE_GROUP]


def test_hook_fixture_user_gets_canonical_group_without_literal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "test_hook.py"
    source.write_text("def test_hook(hook_plugin_env): pass\n")
    item = _FakeItem(source, fixtures=("hook_plugin_env",))

    _collect(item, tmp_path, monkeypatch)

    assert _group_names(item) == [ct.ENFORCEMENT_SHARED_STATE_GROUP]


@pytest.mark.parametrize(
    "legacy_group",
    [
        "hook-hardcoded-tmp",
        "gludd-watchdog-ci-cache",
        "enforcement_plugin_state_files",
        "enforcement_state_files",
        "deadline_e2e_state",
    ],
)
def test_legacy_enforcement_groups_normalize_to_one_canonical_group(
    legacy_group: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / f"test_{legacy_group}.py"
    source.write_text('STATE = "/tmp/gludd-state.json"\n')
    item = _FakeItem(source, group=legacy_group)

    _collect(item, tmp_path, monkeypatch)

    assert _group_names(item) == [ct.ENFORCEMENT_SHARED_STATE_GROUP]


@pytest.mark.parametrize("group", ["port_8000", "hot_reload_proxy"])
def test_unrelated_resource_group_is_preserved(
    group: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / f"test_{group}.py"
    source.write_text('SCRATCH = "/tmp/gludd-namespaced-scratch"\n')
    item = _FakeItem(source, group=group)

    _collect(item, tmp_path, monkeypatch)

    assert _group_names(item) == [group]


def test_read_optional_bytes_returns_bytes_or_none(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    assert hook_fixtures.read_optional_bytes(state) is None
    state.write_bytes(b'{"count": 1}')
    assert hook_fixtures.read_optional_bytes(state) == b'{"count": 1}'


def test_read_optional_bytes_treats_disappearance_as_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state.json"
    state.write_bytes(b"transient")
    original_read_bytes = Path.read_bytes

    def _disappear_before_read(path: Path) -> bytes:
        if path == state:
            state.unlink(missing_ok=True)
            raise FileNotFoundError(state)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", _disappear_before_read)

    assert hook_fixtures.read_optional_bytes(state) is None
