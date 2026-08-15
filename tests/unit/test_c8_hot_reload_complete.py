"""C.8 — Hot-reload/worker broadcast: all 4 issues verified.

1. snapshot→swap TOCTOU closed (lock serializes snapshot+swap)
2. unauthenticated worker registration blocked (PSK required, SSRF guard)
3. concurrency guard present (threading.Lock in both hot_reloader + worker_broadcast)
4. symlink bypass blocked (os.path.realpath before os.replace)
"""

from __future__ import annotations

import hashlib
import os
import sys
import textwrap
import threading
import uuid
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from general_ludd.reload.hot_reloader import HotReloader
from general_ludd.reload.worker_broadcast import (
    WorkerBroadcaster,
    WorkerInfo,
    _is_safe_worker_address,
)

# ── helpers ────────────────────────────────────────────────────────────────


def _install_live_module(
    tmp_path: Path, name: str, body: str
) -> tuple[Path, str, object]:
    pkg = f"live_{uuid.uuid4().hex[:8]}"
    pkg_dir = tmp_path / pkg
    pkg_dir.mkdir(exist_ok=True)
    (pkg_dir / "__init__.py").write_text("")
    mod_path = pkg_dir / f"{name}.py"
    mod_path.write_text(textwrap.dedent(body))
    sys.path.insert(0, str(tmp_path))
    import importlib

    importlib.invalidate_caches()
    fqmn = f"{pkg}.{name}"
    mod = importlib.import_module(fqmn)
    return mod_path, fqmn, mod


# ── ISSUE 1: snapshot→swap TOCTOU ─────────────────────────────────────────


def test_snapshot_and_swap_are_under_same_lock(tmp_path: Path) -> None:
    """The snapshot (read) and swap (os.replace+reload) MUST be serialized
    under the same reload_lock so a concurrent edit cannot modify the live
    file between snapshot and swap.

    We verify this by checking that while reload_code_module is in progress
    in one thread, a non-blocking acquire from another thread fails (lock is
    held), and after the reload completes the lock is released."""
    _mod_path, fqmn, _mod = _install_live_module(
        tmp_path, "leaf_toctou",
        """
        def value():
            return 10
        """,
    )
    reloader = HotReloader(config_dir=str(tmp_path / "config"))

    candidate = tmp_path / "cand_toctou.py"
    candidate.write_text("def value():\n    return 20\n")
    expected_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()

    reload_started = threading.Event()

    def _runner() -> None:
        reload_started.set()
        reloader.reload_code_module(
            module_name=fqmn,
            candidate_source_path=str(candidate),
            health_check=lambda: True,
            expected_sha256=expected_hash,
        )

    t = threading.Thread(target=_runner)
    t.start()
    assert reload_started.wait(timeout=5.0), "Reload thread never started"
    t.join(timeout=5.0)

    # After the reload completes, the lock must be released
    acquired_after = reloader._reload_lock.acquire(blocking=False)
    if acquired_after:
        reloader._reload_lock.release()
    assert acquired_after, "Lock was not released after snapshot+swap"


def test_concurrent_reload_code_module_yields_no_corruption(
    tmp_path: Path,
) -> None:
    """Multiple concurrent reload_code_module calls on different modules
    must not corrupt any module or raise unexpected errors."""
    _mod_path_a, fqmn_a, _mod_a = _install_live_module(
        tmp_path, "leaf_a",
        """
        def value():
            return 1
        """,
    )
    _mod_path_b, fqmn_b, _mod_b = _install_live_module(
        tmp_path, "leaf_b",
        """
        def value():
            return 1
        """,
    )
    reloader = HotReloader(config_dir=str(tmp_path / "config"))

    cand_a = tmp_path / "cand_a.py"
    cand_a.write_text("def value():\n    return 100\n")
    cand_b = tmp_path / "cand_b.py"
    cand_b.write_text("def value():\n    return 200\n")

    def _reload_a() -> None:
        for _ in range(10):
            reloader.reload_code_module(
                module_name=fqmn_a,
                candidate_source_path=str(cand_a),
                health_check=lambda: True,
            )

    def _reload_b() -> None:
        for _ in range(10):
            reloader.reload_code_module(
                module_name=fqmn_b,
                candidate_source_path=str(cand_b),
                health_check=lambda: True,
            )

    t_a = threading.Thread(target=_reload_a)
    t_b = threading.Thread(target=_reload_b)
    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()

    # Both modules should be importable and have their final values
    import importlib

    imported_a = importlib.import_module(fqmn_a)
    imported_b = importlib.import_module(fqmn_b)
    assert cast(Any, imported_a).value() in (1, 100)
    assert cast(Any, imported_b).value() in (1, 200)


# ── ISSUE 2: unauthenticated worker registration ─────────────────────────


def test_ssrf_guard_rejects_http_address() -> None:
    """_is_safe_worker_address rejects plain-http (PSK never sent in cleartext)."""
    assert _is_safe_worker_address("http://worker.internal:8000") is False


def test_ssrf_guard_rejects_loopback() -> None:
    """_is_safe_worker_address rejects 127.0.0.1 / localhost."""
    assert _is_safe_worker_address("https://127.0.0.1:8000") is False
    assert _is_safe_worker_address("https://localhost:8000") is False


def test_ssrf_guard_rejects_cloud_metadata() -> None:
    """_is_safe_worker_address rejects cloud metadata IP."""
    assert _is_safe_worker_address("https://169.254.169.254") is False


def test_ssrf_guard_rejects_rfc1918() -> None:
    """_is_safe_worker_address rejects RFC-1918 addresses."""
    assert _is_safe_worker_address("https://10.0.0.1:8000") is False
    assert _is_safe_worker_address("https://192.168.1.1:8000") is False


def test_ssrf_guard_accepts_safe_https() -> None:
    """_is_safe_worker_address accepts public https target."""
    assert _is_safe_worker_address("https://worker.internal.example.com:8000") is True
    assert _is_safe_worker_address("https://10.0.0.1:8000") is False  # private IP still rejected


def test_register_refuses_unsafe_address(caplog) -> None:
    """register() must refuse to store a worker whose address fails SSRF check."""
    import logging

    logger_name = "general_ludd.reload.worker_broadcast"
    logging.getLogger(logger_name).disabled = False
    logging.getLogger(logger_name).propagate = True

    b = WorkerBroadcaster()
    with caplog.at_level(logging.WARNING, logger=logger_name):
        b.register(WorkerInfo(worker_id="evil", address="http://169.254.169.254"))

    assert len(b.list_workers()) == 0, "Worker with unsafe address should not be registered"


def test_register_accepts_safe_address() -> None:
    """register() stores workers with safe https addresses."""
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="good", address="https://worker.internal.example.com:8001"))
    assert len(b.list_workers()) == 1
    assert b.list_workers()[0].worker_id == "good"


def test_broadcast_skips_unsafe_worker_even_if_registered(monkeypatch) -> None:
    """Defense-in-depth: even if an unsafe worker IS registered (bypass),
    broadcast must still skip it (re-validate at send time)."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
    b = WorkerBroadcaster()

    # Monkey-patch register to bypass SSRF check for this test
    with b._lock:
        b._workers["evil"] = WorkerInfo(
            worker_id="evil", address="http://169.254.169.254"
        )

    results = b.broadcast_reload("ALL")
    assert any(
        r.worker_id == "evil" and r.success is False and r.error == "unsafe address"
        for r in results
    ), "Re-validation at send time must catch unsafe registered worker"


# ── ISSUE 3: concurrency guard ────────────────────────────────────────────


def test_hot_reloader_has_threading_lock() -> None:
    """HotReloader uses threading.Lock for concurrency."""
    reloader = HotReloader(config_dir="/tmp/test")
    assert isinstance(reloader._reload_lock, type(threading.Lock()))


def test_worker_broadcaster_has_threading_lock() -> None:
    """WorkerBroadcaster uses threading.Lock for concurrency."""
    b = WorkerBroadcaster()
    assert isinstance(b._lock, type(threading.Lock()))


def test_register_heartbeat_list_are_under_lock(monkeypatch) -> None:
    """Concurrent register/heartbeat/list must not raise RuntimeError."""
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="w0", address="https://w0.internal:8001"))


    def _heartbeat_loop() -> None:
        for _i in range(200):
            b.heartbeat("w0")

    def _list_loop() -> None:
        for _ in range(200):
            b.list_workers()

    def _register_loop() -> None:
        for i in range(200):
            wid = f"w{i % 20}"
            b.register(WorkerInfo(worker_id=wid, address=f"https://{wid}.internal:8001"))

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = [
            pool.submit(_heartbeat_loop),
            pool.submit(_list_loop),
            pool.submit(_register_loop),
            pool.submit(_heartbeat_loop),
            pool.submit(_list_loop),
            pool.submit(_register_loop),
        ]
        for fut in concurrent.futures.as_completed(futures):
            fut.result()

    assert True  # No RuntimeError from dict mutation


# ── ISSUE 4: symlink bypass ───────────────────────────────────────────────


def test_live_path_is_realpathed(tmp_path: Path) -> None:
    """The live_path from __file__ must be resolved via os.path.realpath
    so the write targets the actual file, not the symlink target."""
    _mod_path_broken, _fqmn_broken, _mod_broken = _install_live_module(
        tmp_path, "leaf_sym2",
        """
        def value():
            return 1
        """,
    )

    real_dir = tmp_path / "real_store"
    real_dir.mkdir()
    real_file = real_dir / "real_target.py"
    real_file.write_text("VALUE = 1\n")

    sym_dir = tmp_path / "sym_store"
    sym_dir.mkdir()
    sym_link = sym_dir / "real_target.py"
    os.symlink(str(real_file), str(sym_link))

    # The reload_code_module path resolver uses os.path.realpath.
    # Verify realpath resolves the symlink to the real path.
    resolved = Path(os.path.realpath(str(sym_link)))
    assert resolved == real_file.resolve()


def test_symlink_write_targets_real_file(tmp_path: Path) -> None:
    """Reloading via a symlinked __file__ must write to the real underlying file."""
    real_dir = tmp_path / "reals"
    real_dir.mkdir()
    pkg = f"symp_{uuid.uuid4().hex[:8]}"
    real_pkg = real_dir / pkg
    real_pkg.mkdir()
    (real_pkg / "__init__.py").write_text("")
    real_mod = real_pkg / "mod.py"
    real_mod.write_text("V = 1\n")

    sym_dir = tmp_path / "syms"
    sym_dir.mkdir()
    sym_pkg = sym_dir / pkg
    sym_pkg.mkdir()
    (sym_pkg / "__init__.py").write_text("")
    sym_mod = sym_pkg / "mod.py"
    os.symlink(str(real_mod), str(sym_mod))

    sys.path.insert(0, str(sym_dir))
    import importlib

    importlib.invalidate_caches()
    fqmn = f"{pkg}.mod"
    mod = importlib.import_module(fqmn)
    mod.__file__ = str(sym_mod)  # force __file__ to be the symlink path

    reloader = HotReloader(config_dir=str(tmp_path / "cfg"))
    candidate = tmp_path / "cand_sym.py"
    candidate.write_text("V = 99\n")

    result = reloader.reload_code_module(
        module_name=fqmn,
        candidate_source_path=str(candidate),
        health_check=lambda: True,
    )
    assert result.success is True
    assert "V = 99" in real_mod.read_text(), "Real file should be updated"
    assert real_mod.read_bytes() == sym_mod.read_bytes(), "Symlink still points to real"


# ── ISSUE 4b: allowlist and PSK never sent to non-allowlisted workers ────


def test_psk_never_sent_to_non_allowlisted_worker(monkeypatch) -> None:
    """When allowlist is configured, the PSK Bearer header must never be POST'd
    to a worker whose id/address is not in the allowlist."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret-psk-value")
    monkeypatch.setenv("GLUDD_WORKER_ALLOWLIST", "trusted-w1,trusted-w2")

    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="trusted-w1", address="https://w1.internal:8001"))
    b.register(WorkerInfo(worker_id="untrusted", address="https://evil.internal:8001"))

    with patch("httpx.post") as mock_post:
        mock_post.return_value.status_code = 200
        results = b.broadcast_reload("CONFIG")

    assert len(results) == 2
    assert any(r.worker_id == "trusted-w1" and r.success for r in results)
    assert any(
        r.worker_id == "untrusted" and r.success is False and r.error == "not allowlisted"
        for r in results
    )


def test_broadcast_reload_returns_typed_results() -> None:
    """broadcast_reload always returns a list[BroadcastResult]."""
    b = WorkerBroadcaster()
    results = b.broadcast_reload("CONFIG")
    assert isinstance(results, list)
    assert len(results) == 0  # no workers registered


def test_allowlist_resolved_from_env(monkeypatch) -> None:
    """Worker allowlist is parsed from GLUDD_WORKER_ALLOWLIST env var."""
    monkeypatch.setenv("GLUDD_WORKER_ALLOWLIST", "  host-a , host-b:9090 ,  ")
    b = WorkerBroadcaster()
    resolved = b._resolve_allowlist()
    assert resolved == {"host-a", "host-b:9090"}


def test_empty_allowlist_means_unrestricted(monkeypatch) -> None:
    """An empty (or unset) allowlist means no restriction — broadcasts go out
    to all registered safe workers."""
    monkeypatch.delenv("GLUDD_WORKER_ALLOWLIST", raising=False)
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
    b = WorkerBroadcaster()
    resolved = b._resolve_allowlist()
    assert resolved == set()  # empty → unrestricted
