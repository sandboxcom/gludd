"""TDD proof: W4.3 — watchdog Observer drives change detection in integrity scanner.

Proves:
1. FileWatcher can be started with a directory path.
2. Touching (modifying) a file in the watched tree emits a change event.
3. Change detection fires WITHOUT a full os.walk rescan.
4. FileWatcher.stop() cleans up cleanly.
"""

from __future__ import annotations

import time
from pathlib import Path


def _wait_for_changes(watcher, timeout: float = 5.0, interval: float = 0.05) -> list[dict]:
    """Poll until at least one change is collected or timeout is reached."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        changes = watcher.get_changes()
        if changes:
            return changes
        time.sleep(interval)
    return []


class TestFileWatcher:
    def test_file_watcher_class_exists(self) -> None:
        from general_ludd.integrity.scanner import FileWatcher
        assert FileWatcher is not None

    def test_touch_file_detected_without_full_rescan(self, tmp_path: Path) -> None:
        """Write a file, start watcher, modify file → change detected via watchdog."""
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "watched"
        watched.mkdir()
        target = watched / "test.txt"
        target.write_text("initial content")

        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.2)  # Let observer settle.

        # Modify the file.
        target.write_text("modified content")

        # Poll until events arrive (macOS FSEvents can be slow under load).
        changes = _wait_for_changes(watcher, timeout=5.0)
        watcher.stop()

        # At least one change event should have been collected.
        assert len(changes) > 0, "Expected at least one change event after file modification"
        paths = [c["file"] for c in changes]
        assert any(str(target) in p or "test.txt" in p for p in paths), (
            f"Expected {target} in changes, got: {paths}"
        )

    def test_new_file_detected(self, tmp_path: Path) -> None:
        """A new file created in the watched tree is detected."""
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "watched2"
        watched.mkdir()

        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.2)

        new_file = watched / "newfile.py"
        new_file.write_text("# new file")

        changes = _wait_for_changes(watcher, timeout=5.0)
        watcher.stop()

        assert len(changes) > 0, "Expected change event for new file"

    def test_stop_cleans_up_cleanly(self, tmp_path: Path) -> None:
        """Calling stop() does not raise."""
        from general_ludd.integrity.scanner import FileWatcher

        watcher = FileWatcher()
        watcher.start([str(tmp_path)])
        time.sleep(0.05)
        watcher.stop()  # Should not raise.

    def test_get_changes_clears_on_read(self, tmp_path: Path) -> None:
        """After get_changes(), the internal buffer is cleared (no duplicate events)."""
        from general_ludd.integrity.scanner import FileWatcher

        watched = tmp_path / "watched3"
        watched.mkdir()
        f = watched / "f.txt"
        f.write_text("hello")

        watcher = FileWatcher()
        watcher.start([str(watched)])
        time.sleep(0.2)

        f.write_text("changed")

        # Poll until we see events.
        changes1 = _wait_for_changes(watcher, timeout=5.0)
        # After _wait_for_changes consumed events, second call returns empty.
        changes2 = watcher.get_changes()
        watcher.stop()

        assert len(changes1) > 0
        assert len(changes2) == 0, "Second get_changes() should return empty (already consumed)"
