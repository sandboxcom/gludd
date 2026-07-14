from __future__ import annotations

from pathlib import Path

import pytest

import tests.conftest as ct


class TestRatchetHook:
    def test_parse_ratchet_entries_empty_when_no_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(ct, "_REPO_ROOT", tmp_path)
        assert ct._parse_ratchet_entries() == {}

    def test_parse_ratchet_entries_empty_when_no_entries(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / "config").mkdir(parents=True)
        (tmp_path / "config" / "ratchet.yml").write_text("# no entries\n")
        monkeypatch.setattr(ct, "_REPO_ROOT", tmp_path)
        assert ct._parse_ratchet_entries() == {}

    def test_parse_ratchet_entries_with_node_ids(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / "config").mkdir(parents=True)
        (tmp_path / "config" / "ratchet.yml").write_text(
            "# Known failures\n"
            "tests/unit/test_foo.py::test_bar: known flaky\n"
            "tests/unit/test_baz.py::test_qux: needs fixing\n"
        )
        monkeypatch.setattr(ct, "_REPO_ROOT", tmp_path)
        entries = ct._parse_ratchet_entries()
        assert entries == {
            "tests/unit/test_foo.py::test_bar": "known flaky",
            "tests/unit/test_baz.py::test_qux": "needs fixing",
        }

    def test_parse_ratchet_entries_skips_blank_and_comments(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / "config").mkdir(parents=True)
        (tmp_path / "config" / "ratchet.yml").write_text(
            "# Header comment\n"
            "\n"
            "tests/unit/test_a.py::test_one: reason A\n"
            "# Mid-file comment\n"
            "\n"
            "tests/unit/test_b.py::test_two: reason B\n"
        )
        monkeypatch.setattr(ct, "_REPO_ROOT", tmp_path)
        entries = ct._parse_ratchet_entries()
        assert len(entries) == 2
        assert "tests/unit/test_a.py::test_one" in entries
        assert "tests/unit/test_b.py::test_two" in entries

    def test_parse_ratchet_entries_yaml_style_key_value(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / "config").mkdir(parents=True)
        (tmp_path / "config" / "ratchet.yml").write_text(
            "refactor_floor_ts:\n  pending\n  src::foo\n"
        )
        monkeypatch.setattr(ct, "_REPO_ROOT", tmp_path)
        entries = ct._parse_ratchet_entries()
        assert "refactor_floor_ts" in entries
        assert entries["refactor_floor_ts"] == "pending src::foo"

    def test_collection_modifyitems_adds_xfail_to_matching_items(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        class FakeItem:
            def __init__(self, nodeid):
                self.nodeid = nodeid
                self.own_markers = []
                self._fixturenames = frozenset()

            def add_marker(self, marker):
                self.own_markers.append(marker)

            @property
            def fixturenames(self):
                return self._fixturenames

        (tmp_path / "config").mkdir(parents=True)
        (tmp_path / "config" / "ratchet.yml").write_text(
            "tests/unit/test_foo.py::test_bar: known flaky\n"
            "tests/unit/test_other.py::test_other: not in items\n"
        )
        monkeypatch.setattr(ct, "_REPO_ROOT", tmp_path)
        entries = ct._parse_ratchet_entries()
        assert "tests/unit/test_foo.py::test_bar" in entries

        item = FakeItem("tests/unit/test_foo.py::test_bar")
        ct.pytest_collection_modifyitems([item])
        assert len(item.own_markers) == 1
        marker = item.own_markers[0]
        assert marker.name == "xfail"
        assert marker.kwargs["strict"] is True
        assert marker.kwargs["reason"] == "known flaky"

    def test_collection_modifyitems_skips_non_matching_item(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        class FakeItem:
            def __init__(self, nodeid):
                self.nodeid = nodeid
                self.own_markers = []
                self._fixturenames = frozenset()

            def add_marker(self, marker):
                self.own_markers.append(marker)

            @property
            def fixturenames(self):
                return self._fixturenames

        (tmp_path / "config").mkdir(parents=True)
        (tmp_path / "config" / "ratchet.yml").write_text(
            "tests/unit/test_foo.py::test_bar: flaky\n"
        )
        monkeypatch.setattr(ct, "_REPO_ROOT", tmp_path)

        item = FakeItem("tests/unit/test_baz.py::test_qux")
        ct.pytest_collection_modifyitems([item])
        assert len(item.own_markers) == 0

    def test_collection_modifyitems_no_ratchet_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        class FakeItem:
            def __init__(self, nodeid):
                self.nodeid = nodeid
                self.own_markers = []
                self._fixturenames = frozenset()

            def add_marker(self, marker):
                self.own_markers.append(marker)

            @property
            def fixturenames(self):
                return self._fixturenames

        monkeypatch.setattr(ct, "_REPO_ROOT", tmp_path)

        item = FakeItem("tests/unit/test_foo.py::test_bar")
        ct.pytest_collection_modifyitems([item])
        assert len(item.own_markers) == 0


class TestRatchetGrowthGuard:
    RATCHET_MAX = 0

    def test_ratchet_max_live_count(self):
        ratchet_path = Path("config/ratchet.yml")
        content = ratchet_path.read_text()
        entries = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#") and "::" in line
        ]
        actual = len(entries)
        assert actual <= self.RATCHET_MAX, (
            f"config/ratchet.yml has {actual} entries, RATCHET_MAX={self.RATCHET_MAX}"
        )
