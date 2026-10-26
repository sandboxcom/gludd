"""Native observer initialization must be atomic, retryable and source-bound."""

from __future__ import annotations

import importlib.abc
import importlib.util
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from threading import Event
from types import FunctionType, ModuleType

import pytest

from general_ludd.git_automation import locking

ROOT = Path(__file__).resolve().parents[2]


def _fresh_observer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore both originally absent and originally cached modules after a control."""
    monkeypatch.setitem(sys.modules, "stream_command", ModuleType("stream_command"))
    monkeypatch.delitem(sys.modules, "stream_command")


def test_concurrent_observer_initialization_waits_for_complete_native_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fresh_observer(monkeypatch)
    entered, release, second_started = Event(), Event(), Event()
    real_spec = importlib.util.spec_from_file_location
    spec = real_spec("stream_command", ROOT / "scripts" / "stream_command.py")
    assert spec is not None and spec.loader is not None
    native_loader = spec.loader

    class BlockingLoader(importlib.abc.Loader):
        def exec_module(self, module: ModuleType) -> None:
            entered.set()
            assert release.wait(timeout=10), "observer loader was not released"
            native_loader.exec_module(module)

    spec.loader = BlockingLoader()
    monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *_args: spec)

    def second_call() -> tuple[object, object]:
        second_started.set()
        return locking._default_observer()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(locking._default_observer)
        try:
            assert entered.wait(timeout=5)
            second = pool.submit(second_call)
            assert second_started.wait(timeout=5)
            with pytest.raises(TimeoutError):
                second.result(timeout=0.1)
        finally:
            release.set()
        left, right = first.result(timeout=5), second.result(timeout=5)
    assert left == right
    native_observer = left[0]
    assert isinstance(native_observer, FunctionType)
    assert Path(native_observer.__code__.co_filename).resolve() == ROOT / "scripts" / "stream_command.py"
    assert sys.modules["stream_command"].stream_command is left[0]


@pytest.mark.parametrize("observer_load_failure", [RuntimeError, KeyboardInterrupt])
def test_failed_native_observer_initialization_removes_partial_module_and_can_retry(
    monkeypatch: pytest.MonkeyPatch, observer_load_failure: type[BaseException],
) -> None:
    _fresh_observer(monkeypatch)
    real_spec = importlib.util.spec_from_file_location
    spec = real_spec("stream_command", ROOT / "scripts" / "stream_command.py")
    assert spec is not None

    class FailingLoader(importlib.abc.Loader):
        def exec_module(self, module: ModuleType) -> None:
            raise observer_load_failure("observer initialization failed")

    spec.loader = FailingLoader()
    with monkeypatch.context() as patch:
        patch.setattr(importlib.util, "spec_from_file_location", lambda *_args: spec)
        with pytest.raises(observer_load_failure, match="observer initialization failed"):
            locking._default_observer()
    assert "stream_command" not in sys.modules
    observer_fn, identity_fn = locking._default_observer()
    assert isinstance(observer_fn, FunctionType)
    assert Path(observer_fn.__code__.co_filename).resolve() == ROOT / "scripts" / "stream_command.py"
    assert identity_fn()


@pytest.mark.parametrize("observer_cache_replaced", [False, True])
def test_fork_reset_discards_only_owned_incomplete_observer(
    monkeypatch: pytest.MonkeyPatch, observer_cache_replaced: bool,
) -> None:
    _fresh_observer(monkeypatch)
    incomplete = ModuleType("stream_command")
    replacement = ModuleType("stream_command") if observer_cache_replaced else incomplete
    for name in ("_file_lock_fds", "_file_lock_depth", "_file_lock_owner", "_repo_locks", "_async_workers"):
        monkeypatch.setattr(locking, name, {})
    for name in ("_registry_guard", "_async_workers_guard", "_observer_import_guard"):
        monkeypatch.setattr(locking, name, getattr(locking, name))
    monkeypatch.setattr(locking, "_observer_loading_module", incomplete)
    monkeypatch.setitem(sys.modules, "stream_command", replacement)
    parent_guard = locking._observer_import_guard
    locking._reset_after_fork()
    assert locking._observer_import_guard is not parent_guard
    assert locking._observer_loading_module is None
    if observer_cache_replaced:
        assert sys.modules["stream_command"] is replacement
    else:
        assert "stream_command" not in sys.modules


def test_failed_observer_loader_preserves_a_replaced_module_cache_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fresh_observer(monkeypatch)
    replacement = ModuleType("stream_command")
    spec = importlib.util.spec_from_file_location("stream_command", ROOT / "scripts" / "stream_command.py")
    assert spec is not None

    class ReplacingLoader(importlib.abc.Loader):
        def exec_module(self, module: ModuleType) -> None:
            sys.modules["stream_command"] = replacement
            raise RuntimeError("observer module was replaced")

    spec.loader = ReplacingLoader()
    monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *_args: spec)
    with pytest.raises(RuntimeError, match="observer module was replaced"):
        locking._default_observer()
    assert sys.modules["stream_command"] is replacement
    assert locking._observer_loading_module is None


@pytest.mark.parametrize("observer_initially_cached", [False, True])
def test_native_default_observer_probe_restores_prior_cache_state(
    monkeypatch: pytest.MonkeyPatch, observer_initially_cached: bool,
) -> None:
    from tests.unit import test_make_commit_transaction as controls

    _fresh_observer(monkeypatch)
    if observer_initially_cached:
        monkeypatch.setitem(sys.modules, "stream_command", ModuleType("stream_command"))
    absent = object()
    previous = sys.modules.get("stream_command", absent)
    with pytest.MonkeyPatch.context() as probe_patch:
        controls.test_default_observer_loads_real_stream_command_without_sys_path_mutation(probe_patch)
    assert sys.modules.get("stream_command", absent) is previous


@pytest.fixture
def frozen_observer_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Import actual current observer bytecode without a physical source-script path."""
    import py_compile
    import zipfile

    _fresh_observer(monkeypatch)
    compiled = tmp_path / "stream_command.pyc"
    py_compile.compile(
        str(ROOT / "scripts" / "stream_command.py"), cfile=str(compiled), doraise=True,
    )
    archive = tmp_path / "native-observer.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(compiled, "stream_command.pyc")
    frozen_root = tmp_path / "bundle"
    frozen_root.mkdir()
    monkeypatch.setattr(sys, "path", [str(archive), *sys.path])
    importlib.invalidate_caches()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen_root), raising=False)
    monkeypatch.setattr(
        locking, "__file__", str(frozen_root / "general_ludd" / "git_automation" / "locking.py"),
    )
    assert not (tmp_path / "scripts" / "stream_command.py").exists()
    return archive


def test_frozen_observer_imports_real_archived_bytecode_without_source_script(
    frozen_observer_archive: Path, tmp_path: Path,
) -> None:
    """Run the real observer resolved by archive machinery and preserve search paths."""
    import json
    import os
    import zipimport

    initial_path = tuple(sys.path)
    observer_fn, identity_fn = locking._default_observer()
    native_module = sys.modules["stream_command"]
    assert isinstance(native_module.__loader__, zipimport.zipimporter)
    assert native_module.__file__ is not None
    assert str(frozen_observer_archive) in native_module.__file__
    assert native_module.stream_command is observer_fn
    assert locking._default_observer() == (observer_fn, identity_fn)
    assert tuple(sys.path) == initial_path
    run_id = identity_fn()
    log = tmp_path / "native-observer.log"
    observed_root = tmp_path / "observed"
    result = observer_fn(
        [sys.executable, "-c", "print('archived native observer child')"], log,
        observed_root=observed_root, label="packaged-observer", run_id=run_id,
        heartbeat_seconds=1, quiet_seconds=0, max_seconds=10, retain_runs=2,
        environment=dict(os.environ), cwd=tmp_path, pass_fds=(),
    )
    assert result == 0
    receipt = json.loads((observed_root / "packaged-observer" / (run_id + ".json")).read_text())
    assert receipt["state"] == "passed" and receipt["exit_code"] == 0
    assert "archived native observer child" in log.read_text()
    assert tuple(sys.path) == initial_path


def test_frozen_observer_import_failure_is_not_cached_and_can_retry(
    frozen_observer_archive: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep normal archive import failure and rollback before a real-source retry."""
    import zipfile

    broken_archive = tmp_path / "broken-observer.zip"
    with zipfile.ZipFile(broken_archive, "w") as bundle:
        bundle.writestr("stream_command.py", "raise RuntimeError('archived observer initialization failed')\n")
    with monkeypatch.context() as failed:
        failed.setattr(sys, "path", [str(broken_archive), *sys.path])
        importlib.invalidate_caches()
        with pytest.raises(RuntimeError, match="archived observer initialization failed"):
            locking._default_observer()
    assert "stream_command" not in sys.modules
    assert locking._observer_loading_module is None
    observer_fn, identity_fn = locking._default_observer()
    retried_module = sys.modules["stream_command"]
    assert retried_module.stream_command is observer_fn
    assert retried_module.__file__ is not None
    assert str(frozen_observer_archive) in retried_module.__file__
    assert identity_fn()
