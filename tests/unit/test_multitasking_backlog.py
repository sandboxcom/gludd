"""
tests/unit/test_multitasking_backlog.py

Tests for scripts/multitasking_backlog_check.py

Coverage:
- open-count arithmetic
- --assert-done exit codes (0 = all done, 1 = open/in_progress remain)
- "done without evidence" is treated as still-open (anti-rubber-stamp rule)
- malformed/missing file exits 2 (fail-safe, not silently done)
- env-var GLUDD_MT_BACKLOG path override (temp file)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from multitasking_backlog_check import (
    _item_is_effectively_done,
    all_done,
    load_backlog,
    main,
    open_count,
    open_items,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_item(
    id: str = "mt-x",
    title: str = "test item",
    status: str = "open",
    evidence: str = "",
    notes: str = "",
) -> dict:
    return {"id": id, "title": title, "status": status, "evidence": evidence, "notes": notes}


def _write_backlog(tmp_path: Path, items: list[dict], version: str = "1") -> Path:
    p = tmp_path / "backlog.json"
    p.write_text(json.dumps({"version": version, "items": items}), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Unit tests: _item_is_effectively_done
# ---------------------------------------------------------------------------

class TestItemIsEffectivelyDone:
    def test_done_with_evidence_is_done(self) -> None:
        item = _make_item(status="done", evidence="abc123")
        assert _item_is_effectively_done(item) is True

    def test_done_without_evidence_is_not_done(self) -> None:
        """Anti-rubber-stamp rule: done + empty evidence => still open."""
        item = _make_item(status="done", evidence="")
        assert _item_is_effectively_done(item) is False

    def test_done_with_whitespace_only_evidence_is_not_done(self) -> None:
        item = _make_item(status="done", evidence="   ")
        assert _item_is_effectively_done(item) is False

    def test_open_is_not_done(self) -> None:
        item = _make_item(status="open", evidence="irrelevant")
        assert _item_is_effectively_done(item) is False

    def test_in_progress_is_not_done(self) -> None:
        item = _make_item(status="in_progress", evidence="sha")
        assert _item_is_effectively_done(item) is False


# ---------------------------------------------------------------------------
# Unit tests: open_count / open_items / all_done
# ---------------------------------------------------------------------------

class TestOpenCount:
    def test_all_open(self) -> None:
        items = [_make_item(status="open"), _make_item(status="open")]
        assert open_count(items) == 2

    def test_all_done_with_evidence(self) -> None:
        items = [
            _make_item(status="done", evidence="abc"),
            _make_item(status="done", evidence="def"),
        ]
        assert open_count(items) == 0

    def test_mixed(self) -> None:
        items = [
            _make_item(id="a", status="done", evidence="sha1"),
            _make_item(id="b", status="open"),
            _make_item(id="c", status="in_progress"),
            _make_item(id="d", status="done", evidence=""),  # rubber-stamp
        ]
        assert open_count(items) == 3

    def test_empty_list(self) -> None:
        assert open_count([]) == 0

    def test_all_done_is_true_when_no_open(self) -> None:
        items = [_make_item(status="done", evidence="proof")]
        assert all_done(items) is True

    def test_all_done_is_false_when_open_remain(self) -> None:
        items = [_make_item(status="open")]
        assert all_done(items) is False

    def test_open_items_returns_correct_subset(self) -> None:
        done_item = _make_item(id="d", status="done", evidence="sha")
        open_item = _make_item(id="o", status="open")
        result = open_items([done_item, open_item])
        assert result == [open_item]


# ---------------------------------------------------------------------------
# Integration tests: load_backlog
# ---------------------------------------------------------------------------

class TestLoadBacklog:
    def test_loads_valid_file(self, tmp_path: Path) -> None:
        items_data = [
            _make_item(id="mt-1", status="done", evidence="abc"),
            _make_item(id="mt-2", status="open"),
        ]
        p = _write_backlog(tmp_path, items_data)
        items, errors = load_backlog(p)
        assert errors == []
        assert len(items) == 2

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_backlog(tmp_path / "nonexistent.json")

    def test_malformed_json_returns_error(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        items, errors = load_backlog(p)
        assert items == []
        assert any("JSON parse" in e for e in errors)

    def test_missing_items_key_returns_error(self, tmp_path: Path) -> None:
        p = tmp_path / "noitems.json"
        p.write_text(json.dumps({"version": "1"}), encoding="utf-8")
        _items, errors = load_backlog(p)
        assert any("items" in e for e in errors)

    def test_item_with_invalid_status_flagged(self, tmp_path: Path) -> None:
        bad_item = {"id": "mt-x", "title": "t", "status": "bogus", "evidence": "", "notes": ""}
        p = _write_backlog(tmp_path, [bad_item])
        _items, errors = load_backlog(p)
        assert any("invalid status" in e for e in errors)

    def test_item_missing_required_field_flagged(self, tmp_path: Path) -> None:
        bad_item = {"id": "mt-x", "title": "no status field"}
        p = _write_backlog(tmp_path, [bad_item])
        _items, errors = load_backlog(p)
        assert any("status" in e for e in errors)

    def test_top_level_not_object_returns_error(self, tmp_path: Path) -> None:
        p = tmp_path / "arr.json"
        p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        _items, errors = load_backlog(p)
        assert any("object" in e for e in errors)


# ---------------------------------------------------------------------------
# CLI tests: main()
# ---------------------------------------------------------------------------

class TestMainCLI:
    """Tests call main() with argv and check exit code / stdout."""

    def _run(self, argv: list[str], backlog_path: Path) -> tuple[int, str]:
        """Run main with env override; capture stdout."""
        import io
        old_env = os.environ.get("GLUDD_MT_BACKLOG")
        os.environ["GLUDD_MT_BACKLOG"] = str(backlog_path)
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            rc = main(argv)
        finally:
            sys.stdout = old_stdout
            if old_env is None:
                os.environ.pop("GLUDD_MT_BACKLOG", None)
            else:
                os.environ["GLUDD_MT_BACKLOG"] = old_env
        return rc, captured.getvalue()

    # --open-count
    def test_open_count_all_open(self, tmp_path: Path) -> None:
        p = _write_backlog(tmp_path, [_make_item(status="open"), _make_item(status="open")])
        rc, out = self._run(["--open-count"], p)
        assert rc == 0
        assert out.strip() == "2"

    def test_open_count_all_done(self, tmp_path: Path) -> None:
        p = _write_backlog(tmp_path, [_make_item(status="done", evidence="sha")])
        rc, out = self._run(["--open-count"], p)
        assert rc == 0
        assert out.strip() == "0"

    def test_open_count_done_without_evidence_counted_as_open(self, tmp_path: Path) -> None:
        p = _write_backlog(tmp_path, [_make_item(status="done", evidence="")])
        rc, out = self._run(["--open-count"], p)
        assert rc == 0
        assert out.strip() == "1"

    # --list-open
    def test_list_open_shows_non_done(self, tmp_path: Path) -> None:
        items = [
            _make_item(id="mt-a", title="alpha", status="open"),
            _make_item(id="mt-b", title="beta", status="done", evidence="proof"),
            _make_item(id="mt-c", title="gamma", status="in_progress"),
        ]
        p = _write_backlog(tmp_path, items)
        rc, out = self._run(["--list-open"], p)
        assert rc == 0
        assert "mt-a" in out
        assert "mt-c" in out
        assert "mt-b" not in out

    def test_list_open_empty_when_all_done(self, tmp_path: Path) -> None:
        p = _write_backlog(tmp_path, [_make_item(status="done", evidence="sha")])
        rc, out = self._run(["--list-open"], p)
        assert rc == 0
        assert out.strip() == ""

    # --assert-done
    def test_assert_done_exits_0_when_all_done(self, tmp_path: Path) -> None:
        p = _write_backlog(tmp_path, [_make_item(status="done", evidence="abc123")])
        rc, _ = self._run(["--assert-done"], p)
        assert rc == 0

    def test_assert_done_exits_1_when_open_remain(self, tmp_path: Path) -> None:
        p = _write_backlog(tmp_path, [_make_item(status="open")])
        rc, _ = self._run(["--assert-done"], p)
        assert rc == 1

    def test_assert_done_exits_1_when_in_progress(self, tmp_path: Path) -> None:
        p = _write_backlog(tmp_path, [_make_item(status="in_progress")])
        rc, _ = self._run(["--assert-done"], p)
        assert rc == 1

    def test_assert_done_exits_1_when_done_without_evidence(self, tmp_path: Path) -> None:
        """done + no evidence is treated as open -> exit 1."""
        p = _write_backlog(tmp_path, [_make_item(status="done", evidence="")])
        rc, _ = self._run(["--assert-done"], p)
        assert rc == 1

    def test_assert_done_exits_0_empty_backlog(self, tmp_path: Path) -> None:
        """Zero items = vacuously all done."""
        p = _write_backlog(tmp_path, [])
        rc, _ = self._run(["--assert-done"], p)
        assert rc == 0

    # malformed file -> exit 2
    def test_malformed_file_exits_2(self, tmp_path: Path) -> None:
        p = tmp_path / "broken.json"
        p.write_text("{oops", encoding="utf-8")
        rc, _ = self._run(["--assert-done"], p)
        assert rc == 2

    def test_missing_file_exits_2(self, tmp_path: Path) -> None:
        p = tmp_path / "ghost.json"
        rc, _ = self._run(["--assert-done"], p)
        assert rc == 2

    # env-var path override
    def test_env_var_path_override(self, tmp_path: Path) -> None:
        """GLUDD_MT_BACKLOG must resolve to the temp file."""
        custom_path = tmp_path / "custom_backlog.json"
        custom_path.write_text(
            json.dumps({"version": "1", "items": [_make_item(status="done", evidence="ev")]}),
            encoding="utf-8",
        )
        old = os.environ.get("GLUDD_MT_BACKLOG")
        os.environ["GLUDD_MT_BACKLOG"] = str(custom_path)
        try:
            items, errors = load_backlog()
            assert errors == []
            assert open_count(items) == 0
        finally:
            if old is None:
                os.environ.pop("GLUDD_MT_BACKLOG", None)
            else:
                os.environ["GLUDD_MT_BACKLOG"] = old

    # canonical repo backlog loads cleanly
    def test_repo_backlog_loads_without_errors(self) -> None:
        """The seeded scripts/multitasking_backlog.json must be schema-valid."""
        # Unset any env override to use the repo default
        old = os.environ.pop("GLUDD_MT_BACKLOG", None)
        try:
            items, errors = load_backlog()
            assert errors == [], f"Repo backlog has schema errors: {errors}"
            assert len(items) > 0, "Repo backlog must have at least one item"
        finally:
            if old is not None:
                os.environ["GLUDD_MT_BACKLOG"] = old
