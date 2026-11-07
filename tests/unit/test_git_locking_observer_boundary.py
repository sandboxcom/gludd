"""Native observer initialization must be atomic, retryable and source-bound."""

from __future__ import annotations

import importlib.abc
import importlib.util
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from threading import Event
from types import FunctionType, ModuleType

import pytest

from general_ludd.git_automation import locking

ROOT = Path(__file__).resolve().parents[2]


def test_default_observer_loads_real_stream_command_without_sys_path_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "stream_command", ModuleType("stream_command"))
    monkeypatch.delitem(sys.modules, "stream_command")
    initial_path = tuple(sys.path)
    observer_fn, identity_fn = locking._default_observer()
    assert isinstance(observer_fn, FunctionType)
    assert Path(observer_fn.__code__.co_filename).resolve() == ROOT / "scripts" / "stream_command.py"
    assert Path(identity_fn.__code__.co_filename).resolve() == ROOT / "scripts" / "stream_command.py"
    registered = sys.modules["stream_command"]
    assert registered.stream_command is observer_fn and registered._new_run_id is identity_fn
    assert tuple(sys.path) == initial_path
    run_id = identity_fn()
    assert isinstance(run_id, str) and run_id


def test_default_observer_refuses_missing_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    monkeypatch.setitem(sys.modules, "stream_command", ModuleType("stream_command"))
    monkeypatch.delitem(sys.modules, "stream_command")
    monkeypatch.setattr(
        importlib.util,
        "spec_from_file_location",
        lambda *_args: None,
    )
    try:
        locking._default_observer()
    except ValueError as error:
        assert "loadable scripts/stream_command.py" in str(error)
    else:
        raise AssertionError("default observer must refuse a missing spec")


def test_default_observer_refuses_spec_without_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    monkeypatch.setitem(sys.modules, "stream_command", ModuleType("stream_command"))
    monkeypatch.delitem(sys.modules, "stream_command")
    loaderless = importlib.util.spec_from_file_location(
        "stream_command",
        str(ROOT / "scripts" / "stream_command.py"),
    )
    assert loaderless is not None
    loaderless.loader = None
    monkeypatch.setattr(
        importlib.util,
        "spec_from_file_location",
        lambda *_args, **_kwargs: loaderless,
    )
    try:
        locking._default_observer()
    except ValueError as error:
        assert "loadable scripts/stream_command.py" in str(error)
    else:
        raise AssertionError("default observer must refuse a spec without a loader")


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
    controls = sys.modules[__name__]

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



@pytest.mark.parametrize("archive_child_status", [0, 7])
def test_archived_observer_needs_no_ci_source_or_coverage_modules(
    frozen_observer_archive: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, archive_child_status: int,
) -> None:
    """Execute real frozen observer bytecode with unavailable development modules."""
    import json
    import os

    with monkeypatch.context() as unavailable:
        blocked_roots = ("scripts", "coverage")
        for module_name in tuple(sys.modules):
            if any(module_name == root or module_name.startswith(root + ".") for root in blocked_roots):
                unavailable.setitem(sys.modules, module_name, None)
        for root in blocked_roots:
            unavailable.setitem(sys.modules, root, None)
            with pytest.raises(ModuleNotFoundError) as refused:
                importlib.import_module(root)
            assert refused.value.name == root
        original_path = tuple(sys.path)
        observer_fn, identity_fn = locking._default_observer()
        native_module = sys.modules["stream_command"]
        assert native_module.__file__ is not None
        assert str(frozen_observer_archive) in native_module.__file__
        run_id = identity_fn()
        observed_root = tmp_path / "without-development-modules"
        log = tmp_path / "without-development-modules.log"
        status = observer_fn(
            [sys.executable, "-c", f"print('archive child status'); raise SystemExit({archive_child_status})"],
            log, observed_root=observed_root, label="archive-dependencies", run_id=run_id,
            heartbeat_seconds=1, quiet_seconds=0, max_seconds=10, retain_runs=2,
            environment=dict(os.environ), cwd=tmp_path, pass_fds=(),
        )
        assert status == archive_child_status
        receipt = json.loads((observed_root / "archive-dependencies" / (run_id + ".json")).read_text())
        assert receipt["exit_code"] == archive_child_status
        assert receipt["state"] == ("passed" if archive_child_status == 0 else "failed")
        assert "archive child status" in log.read_text()
        assert tuple(sys.path) == original_path

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


_TRANSACTION_IMPORT_CHILD = r"""
import contextlib
import importlib
import os
import sys
from pathlib import Path

source = Path(sys.argv[1]).resolve()
repository = Path(sys.argv[2]).resolve()
marker = Path(sys.argv[3])
from general_ludd.git_automation import locking

assert Path(locking.__file__).resolve() == source / "src/general_ludd/git_automation/locking.py"
initial_path = tuple(sys.path)

# Prove the actual same import name and explicit PYTHONPATH origin can execute
# the owned decoy before asking the guarded producer to resolve its own file.
sys.modules.pop("stream_command", None)
decoy = importlib.import_module("stream_command")
assert marker.is_file(), "unguarded import did not execute the owned decoy"
marker.unlink()
assert sys.modules.get("stream_command") is decoy
del sys.modules["stream_command"]

class ImportBoundaryReached(Exception):
    pass

@contextlib.contextmanager
def stop_before_lock_acquisition(*_args, **_kwargs):
    raise ImportBoundaryReached
    yield

# This fixture owns only metadata admission. It stops before any native mutex
# acquisition/Make child, so this is not a platform-lock or commit proof.
locking._HAVE_FCNTL = True
locking.git_repo_lock = stop_before_lock_acquisition
try:
    locking._run_make_transaction(
        "ship-commit-files", "make", "Makefile",
        observed_root=".gate-logs/observed",
        heartbeat_seconds=1, quiet_seconds=0, max_seconds=10, retain_runs=2,
    )
except ImportBoundaryReached:
    pass
else:
    raise AssertionError("the actual transaction did not reach the import boundary")

assert tuple(sys.path) == initial_path, "transaction mutated interpreter search paths"
assert not marker.exists(), "guarded producer executed the untrusted observer decoy"
loaded = sys.modules["stream_command"]
assert Path(loaded.__file__).resolve() == source / "scripts/stream_command.py"
assert Path(loaded.stream_command.__code__.co_filename).resolve() == (
    source / "scripts/stream_command.py"
)
assert Path(loaded._new_run_id.__code__.co_filename).resolve() == (
    source / "scripts/stream_command.py"
)
assert os.getcwd() == str(repository)
print("NATIVE_TRANSACTION_IMPORT_BOUNDARY_PASS")
"""


def test_actual_transaction_default_import_preserves_paths_against_active_decoy(
    tmp_path: Path,
) -> None:
    """Reach the old mutating branch through its real existing transaction API."""
    repository = tmp_path / "owned-repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    (repository / "Makefile").write_text(
        ".PHONY: _commit-locked-ship-commit-files\n"
        "_commit-locked-ship-commit-files:\n\t@true\n",
        encoding="utf-8",
    )
    decoy_root = tmp_path / "owned-decoy"
    decoy_root.mkdir()
    marker = tmp_path / "owned import witness"
    (decoy_root / "stream_command.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('owned decoy ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    inherited = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(decoy_root), str(ROOT / "src"), str(ROOT), *([inherited] if inherited else [])],
    )
    for key in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "GNUMAKEFLAGS", "MAKEFILES"):
        environment.pop(key, None)
    result = subprocess.run(
        [sys.executable, "-P", "-c", _TRANSACTION_IMPORT_CHILD, str(ROOT), str(repository), str(marker)],
        cwd=repository, env=environment, text=True, capture_output=True, check=False, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NATIVE_TRANSACTION_IMPORT_BOUNDARY_PASS" in result.stdout
    assert not marker.exists()
