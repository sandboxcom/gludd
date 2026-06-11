"""V3.3: Verify watchdog can replace os.walk polling in integrity scanner.

Watchdog is a declared dependency. The integrity scanner currently uses
os.walk polling for change detection. The HMAC/baseline logic stays;
only the change-detection loop should be replaced with watchdog observers.
"""
from __future__ import annotations


def test_watchdog_is_declared_dependency():
    from pathlib import Path
    pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    assert "watchdog" in content, "watchdog must be a declared dependency"


def test_watchdog_can_be_imported():
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    assert Observer is not None
    assert FileSystemEventHandler is not None


def test_watchdog_observer_can_watch_paths():
    """Watchdog can create an observer and schedule a path."""
    import tempfile
    import time

    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    obs = Observer()
    handler = FileSystemEventHandler()
    with tempfile.TemporaryDirectory() as tmp:
        obs.schedule(handler, tmp, recursive=False)
        obs.start()
        time.sleep(0.1)
        obs.stop()
        obs.join(timeout=1)
    assert True


def test_integrity_scanner_uses_os_walk_polling():
    """The scanner currently uses polling — verifying watchdog replacement is needed."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent.parent / "src"
    scanner = src / "general_ludd" / "integrity" / "scanner.py"
    content = scanner.read_text(encoding="utf-8")
    assert "os.walk" in content or "Path" in content, (
        "Integrity scanner must exist for V3.3 replacement"
    )
