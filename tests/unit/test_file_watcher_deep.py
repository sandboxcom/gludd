"""Deep tests for FileWatcher: event-based file change detection.

Covers: create/modify/delete/move events, recursive watching, debounce
behaviour, polling fallback, symlink handling, cross-platform noop,
thread safety, lifecycle, and edge cases.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest


def _poll_changes(watcher, min_count: int = 1, timeout: float = 30.0, interval: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        changes = watcher.get_changes()
        if len(changes) >= min_count:
            return changes
        time.sleep(interval)
    return watcher.get_changes()


# ---------------------------------------------------------------------------
# Create / modify / delete events
# ---------------------------------------------------------------------------
class TestFileEvents:
    def test_file_created_detected(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "deep_create"
        watched.mkdir()
        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.3)

        (watched / "new.txt").write_text("fresh")
        changes = _poll_changes(watcher, timeout=10.0)
        watcher.stop()

        assert len(changes) > 0, "expected create event"
        assert any(c["type"] == "new" for c in changes)

    def test_file_modified_detected(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "deep_mod"
        watched.mkdir()
        target = watched / "mod.txt"
        target.write_text("v1")

        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.3)

        target.write_text("v2")
        changes = _poll_changes(watcher, timeout=10.0)
        watcher.stop()

        assert len(changes) > 0, "expected modify event"
        assert any(c["type"] == "modified" for c in changes)

    def test_file_deleted_detected(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "deep_del"
        watched.mkdir()
        target = watched / "gone.txt"
        target.write_text("farewell")

        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.3)

        target.unlink()
        changes = _poll_changes(watcher, timeout=10.0)
        watcher.stop()

        assert len(changes) > 0, "expected delete event (may be 'modified' on macOS)"
        types = {c["type"] for c in changes}
        assert types & {"removed", "modified", "new"}, f"unexpected event types: {types}"

    def test_file_moved_detected(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "deep_mv"
        watched.mkdir()
        src = watched / "src.txt"
        src.write_text("move-me")
        dest = watched / "dest.txt"

        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.3)

        src.rename(dest)
        changes = _poll_changes(watcher, timeout=10.0)
        watcher.stop()

        assert len(changes) > 0, "expected move event (macOS may emit modified+new)"
        types = {c["type"] for c in changes}
        assert types & {"moved", "modified", "new", "removed"}, f"unexpected event types: {types}"

    def test_directory_events_are_filtered_out(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "deep_dir"
        watched.mkdir()
        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.3)

        (watched / "sub").mkdir()
        changes = _poll_changes(watcher, timeout=10.0)
        watcher.stop()

        dir_events = [c for c in changes if c.get("file") and str(c["file"]).endswith("sub")]
        assert len(dir_events) == 0, "directory events should be filtered out"


# ---------------------------------------------------------------------------
# Recursive watching
# ---------------------------------------------------------------------------
class TestRecursiveWatching:
    def test_subdirectory_file_created_detected(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        root = tmp_path / "deep_rec"
        root.mkdir()
        sub = root / "sub"
        sub.mkdir()

        watcher = FileWatcher()
        watcher.start([str(root)])
        time.sleep(0.3)

        (sub / "deep.txt").write_text("recursive")
        changes = _poll_changes(watcher, timeout=10.0)
        watcher.stop()

        assert len(changes) > 0, "expected event from subdirectory"
        assert any("deep.txt" in str(c.get("file", "")) for c in changes)

    def test_deeply_nested_directory_detected(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        root = tmp_path / "deep_nest"
        root.mkdir()
        deep = root / "a" / "b" / "c"
        deep.mkdir(parents=True)

        watcher = FileWatcher()
        watcher.start([str(root)])
        time.sleep(0.3)

        (deep / "nest.txt").write_text("deep")
        changes = _poll_changes(watcher, timeout=10.0)
        watcher.stop()

        assert len(changes) > 0, "expected event from deeply nested dir"
        assert any("nest.txt" in str(c.get("file", "")) for c in changes)

    def test_multiple_watched_paths(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        dir_a = tmp_path / "multi_a"
        dir_b = tmp_path / "multi_b"
        dir_a.mkdir()
        dir_b.mkdir()

        watcher = FileWatcher()
        watcher.start([str(dir_a), str(dir_b)])
        time.sleep(0.3)

        (dir_a / "a.txt").write_text("a")
        (dir_b / "b.txt").write_text("b")

        # get_changes() drains the buffer, and the two watch-trees deliver
        # their events independently — on CI filesystems one of the two events
        # can lag the other. Accumulate changes across a bounded 5s poll window
        # instead of expecting both events in a single drain.
        changes: list[dict] = []
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            changes.extend(watcher.get_changes())
            files = [str(c.get("file", "")) for c in changes]
            if any("a.txt" in f for f in files) and any("b.txt" in f for f in files):
                break
            time.sleep(0.05)
        watcher.stop()

        files = [str(c.get("file", "")) for c in changes]
        assert any("a.txt" in f for f in files), "missing event from dir_a"
        assert any("b.txt" in f for f in files), "missing event from dir_b"


# ---------------------------------------------------------------------------
# Debounce behaviour — rapid writes produce events, no lost events on burst
# ---------------------------------------------------------------------------
class TestDebounceBehaviour:
    def test_rapid_sequential_writes_collected(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "deep_bounce"
        watched.mkdir()
        target = watched / "burst.txt"
        target.write_text("init")

        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.3)

        for i in range(5):
            target.write_text(f"burst-{i}")
            time.sleep(0.01)

        changes = _poll_changes(watcher, timeout=10.0)
        watcher.stop()

        assert len(changes) > 0, "expected events from rapid writes"

    def test_multiple_files_burst(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "deep_burst"
        watched.mkdir()

        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.3)

        for i in range(3):
            (watched / f"f{i}.txt").write_text(f"burst-{i}")

        changes = _poll_changes(watcher, min_count=3, timeout=10.0)
        watcher.stop()

        assert len(changes) >= 3, f"expected ≥3 events, got {len(changes)}"


# ---------------------------------------------------------------------------
# Get changes clears buffer
# ---------------------------------------------------------------------------
class TestGetChangesClears:
    def test_second_get_changes_returns_empty(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "deep_clear"
        watched.mkdir()
        f = watched / "c.txt"
        f.write_text("v1")

        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.3)

        f.write_text("v2")
        first = _poll_changes(watcher, timeout=10.0)
        assert len(first) > 0

        second = watcher.get_changes()
        watcher.stop()

        assert len(second) == 0, "second get_changes() must return empty"

    def test_no_events_yields_empty_list(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "deep_empty"
        watched.mkdir()

        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.3)
        changes = watcher.get_changes()
        watcher.stop()

        assert changes == []


# ---------------------------------------------------------------------------
# Symlink handling
# ---------------------------------------------------------------------------
class TestSymlinkHandling:
    def test_symlink_does_not_crash(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "deep_sym"
        watched.mkdir()
        target = watched / "real.txt"
        target.write_text("real")
        link = watched / "link.txt"

        if hasattr(os, "symlink"):
            os.symlink(str(target), str(link))
        else:
            pytest.skip("symlink not available")

        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.3)

        target.write_text("changed-via-link")
        changes = _poll_changes(watcher, timeout=10.0)
        watcher.stop()

        assert len(changes) > 0, "events should be produced with symlinks present"

    def test_symlink_nonexistent_target(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "deep_dangle"
        watched.mkdir()
        link = watched / "dangle.txt"

        if hasattr(os, "symlink"):
            os.symlink(str(watched / "nope.txt"), str(link))
        else:
            pytest.skip("symlink not available")

        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.2)
        watcher.stop()


# ---------------------------------------------------------------------------
# Polling fallback when watchdog is unavailable
# ---------------------------------------------------------------------------
class TestPollingFallback:
    def test_observer_is_none_before_start(self):
        from general_ludd.integrity.scanner import FileWatcher

        watcher = FileWatcher()
        assert watcher._observer is None

    def test_no_observer_when_watchdog_import_fails(self, tmp_path: Path):
        import importlib.util

        if importlib.util.find_spec("watchdog"):
            pytest.skip("watchdog installed — cannot test import-fail path")

        from general_ludd.integrity.scanner import FileWatcher

        watcher = FileWatcher()
        assert watcher._observer is None
        changes = watcher.get_changes()
        assert changes == []

    def test_get_changes_noop_when_not_started(self):
        from general_ludd.integrity.scanner import FileWatcher

        watcher = FileWatcher()
        changes = watcher.get_changes()
        assert changes == []

    def test_stop_when_never_started_does_not_raise(self):
        from general_ludd.integrity.scanner import FileWatcher

        watcher = FileWatcher()
        watcher.stop()

    def test_stop_called_twice_does_not_raise(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "deep_stopx2"
        watched.mkdir()

        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.1)
        watcher.stop()
        watcher.stop()

    def test_start_with_nonexistent_path_does_not_crash(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        nonexistent = str(tmp_path / "no_such_dir")
        watcher = FileWatcher()
        watcher.start([nonexistent])
        time.sleep(0.1)
        watcher.stop()


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------
class TestThreadSafety:
    def test_concurrent_get_changes_and_events(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "deep_thread"
        watched.mkdir()
        f = watched / "ts.txt"
        f.write_text("init")

        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.3)

        errors = []

        def writer():
            try:
                for i in range(10):
                    f.write_text(f"ts-{i}")
                    time.sleep(0.01)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        changes = _poll_changes(watcher, timeout=10.0)
        watcher.stop()

        assert errors == [], f"thread errors: {errors}"
        assert len(changes) > 0, "expected events under concurrent writes"

    def test_get_changes_while_events_arrive(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "deep_getrace"
        watched.mkdir()
        f = watched / "race.txt"
        f.write_text("start")

        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.3)

        read_results = []

        def reader():
            for _ in range(20):
                read_results.append(len(watcher.get_changes()))
                time.sleep(0.01)

        reader_t = threading.Thread(target=reader)
        reader_t.start()

        for i in range(10):
            f.write_text(f"race-{i}")
            time.sleep(0.01)

        reader_t.join(timeout=5.0)
        watcher.stop()

        assert len(read_results) == 20

    def test_start_stop_start_lifecycle(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        watched_a = tmp_path / "life_a"
        watched_a.mkdir()
        (watched_a / "a1.txt").write_text("a1")

        watcher = FileWatcher()
        watcher.start([str(watched_a)])
        time.sleep(0.3)
        (watched_a / "a2.txt").write_text("a2")
        changes1 = _poll_changes(watcher, timeout=10.0)
        watcher.stop()

        assert len(changes1) > 0, "first lifecycle: expected events"

        watched_b = tmp_path / "life_b"
        watched_b.mkdir()
        (watched_b / "b1.txt").write_text("b1")

        watcher.start([str(watched_b)])
        time.sleep(0.3)
        (watched_b / "b2.txt").write_text("b2")
        changes2 = _poll_changes(watcher, timeout=10.0)
        watcher.stop()

        assert len(changes2) > 0, "second lifecycle: expected events"


# ---------------------------------------------------------------------------
# Cross-platform — no-op when watchdog unavailable
# ---------------------------------------------------------------------------
class TestCrossPlatformNoop:
    def test_win32_noop_when_platform_module_unavailable(self):
        import importlib.util

        if importlib.util.find_spec("watchdog"):
            pytest.skip("watchdog is available")

        from general_ludd.integrity.scanner import FileWatcher

        watcher = FileWatcher()
        watcher.start(["/tmp"])
        changes = watcher.get_changes()
        watcher.stop()
        assert changes == []


# ---------------------------------------------------------------------------
# Structural checks: _IntegrityEventHandler
# ---------------------------------------------------------------------------
class TestEventHandlerStructural:
    def test_handler_has_all_four_event_methods(self):
        from general_ludd.integrity.scanner import _IntegrityEventHandler

        assert hasattr(_IntegrityEventHandler, "on_created")
        assert hasattr(_IntegrityEventHandler, "on_modified")
        assert hasattr(_IntegrityEventHandler, "on_deleted")
        assert hasattr(_IntegrityEventHandler, "on_moved")

    def test_handler_ignores_directory_events(self):
        import threading as _threading
        from unittest.mock import MagicMock

        from general_ludd.integrity.scanner import _IntegrityEventHandler

        changes: list[dict[str, object]] = []
        lock = _threading.Lock()
        handler = _IntegrityEventHandler(changes, lock)
        mock_event = MagicMock()
        mock_event.is_directory = True
        mock_event.src_path = "/some/dir"

        handler.on_created(mock_event)
        handler.on_modified(mock_event)
        handler.on_deleted(mock_event)
        handler.on_moved(mock_event)

        with lock:
            assert len(changes) == 0, "directory events must not be recorded"

    def test_record_event_has_required_fields(self):
        import threading as _threading
        from unittest.mock import MagicMock

        from general_ludd.integrity.scanner import _IntegrityEventHandler

        changes: list[dict[str, object]] = []
        lock = _threading.Lock()
        handler = _IntegrityEventHandler(changes, lock)
        mock_event = MagicMock()
        mock_event.is_directory = False
        mock_event.src_path = "/f.txt"

        handler.on_created(mock_event)
        with lock:
            assert len(changes) == 1
            entry = changes[0]
            assert entry["type"] == "new"
            assert "file" in entry
            assert "detected_at" in entry

        handler.on_modified(mock_event)
        with lock:
            assert changes[-1]["type"] == "modified"

        handler.on_deleted(mock_event)
        with lock:
            assert changes[-1]["type"] == "removed"

    def test_move_event_includes_dest_path(self):
        import threading as _threading
        from unittest.mock import MagicMock

        from general_ludd.integrity.scanner import _IntegrityEventHandler

        changes: list[dict[str, object]] = []
        lock = _threading.Lock()
        handler = _IntegrityEventHandler(changes, lock)
        mock_event = MagicMock()
        mock_event.is_directory = False
        mock_event.src_path = "/a.txt"
        mock_event.dest_path = "/b.txt"

        handler.on_moved(mock_event)
        with lock:
            assert changes[0]["dest"] == "/b.txt"

    def test_file_path_in_change_event_is_str(self, tmp_path: Path):
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "deep_strpath"
        watched.mkdir()
        (watched / "s.txt").write_text("str")

        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.3)

        (watched / "s.txt").write_text("str-mod")
        changes = _poll_changes(watcher, timeout=10.0)
        watcher.stop()

        for c in changes:
            assert isinstance(c.get("file"), str)
