"""Tests for S.1: ProcessRegistry seal mechanism.

Verifies that once ``seal()`` is called the registry rejects mutating
operations (register, deregister, reap) while permitting read-only
operations (get, is_managed, list, is_alive, signal, resolve_signal,
allowed_signals).
"""

from __future__ import annotations

import signal
import subprocess
import sys

import pytest

from general_ludd.process.registry import (
    ManagedProcess,
    ProcessRegistry,
    ProcessRegistryError,
)


def _spawn_sleeper(seconds: int = 30) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"],
        start_new_session=True,
    )


class TestRegistrySeal:
    def test_not_sealed_by_default(self) -> None:
        reg = ProcessRegistry()
        assert reg.is_sealed is False

    def test_seal_is_idempotent(self) -> None:
        reg = ProcessRegistry()
        reg.seal()
        assert reg.is_sealed is True
        reg.seal()
        assert reg.is_sealed is True

    def test_register_rejected_when_sealed(self) -> None:
        reg = ProcessRegistry()
        reg.seal()
        proc = _spawn_sleeper()
        try:
            with pytest.raises(ProcessRegistryError, match="sealed"):
                reg.register(proc.pid, ["sleeper"], origin="test")
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def test_deregister_rejected_when_sealed(self) -> None:
        reg = ProcessRegistry()
        proc = _spawn_sleeper()
        try:
            reg.register(proc.pid, ["sleeper"], origin="test")
            reg.seal()
            with pytest.raises(ProcessRegistryError, match="sealed"):
                reg.deregister(proc.pid)
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def test_reap_rejected_when_sealed(self) -> None:
        reg = ProcessRegistry()
        proc = _spawn_sleeper()
        try:
            reg.register(proc.pid, ["sleeper"], origin="test")
            reg.seal()
            with pytest.raises(ProcessRegistryError, match="sealed"):
                reg.reap()
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def test_read_operations_work_after_seal(self) -> None:
        reg = ProcessRegistry()
        proc = _spawn_sleeper()
        try:
            reg.register(proc.pid, ["sleeper"], origin="test")
            reg.seal()
            assert reg.is_managed(proc.pid) is True
            assert reg.is_alive(proc.pid) is True
            rec = reg.get(proc.pid)
            assert rec is not None
            assert rec.pid == proc.pid
            records = reg.list()
            assert len(records) == 1
            active = reg.list(active_only=True)
            assert len(active) == 1
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def test_signal_works_after_seal(self) -> None:
        reg = ProcessRegistry()
        proc = _spawn_sleeper()
        reg.register(proc.pid, ["sleeper"], origin="test")
        reg.seal()
        reg.signal(proc.pid, "SIGTERM")
        rc = proc.wait(timeout=5)
        assert rc != 0

    def test_signal_still_refuses_unmanaged_after_seal(self) -> None:
        reg = ProcessRegistry()
        reg.seal()
        proc = _spawn_sleeper()
        try:
            with pytest.raises(ProcessRegistryError, match="not a gludd-managed process"):
                reg.signal(proc.pid, "SIGTERM")
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def test_resolve_signal_works_after_seal(self) -> None:
        reg = ProcessRegistry()
        reg.seal()
        assert reg.resolve_signal("SIGTERM") == int(signal.SIGTERM)

    def test_allowed_signals_works_after_seal(self) -> None:
        reg = ProcessRegistry()
        reg.seal()
        allowed = reg.allowed_signals()
        assert "SIGTERM" in allowed

    def test_register_before_seal_succeeds(self) -> None:
        reg = ProcessRegistry()
        proc = _spawn_sleeper()
        try:
            rec = reg.register(proc.pid, ["sleeper"], origin="test")
            assert isinstance(rec, ManagedProcess)
            assert reg.is_managed(proc.pid)
        finally:
            proc.kill()
            proc.wait(timeout=5)


class TestSetDefaultRegistry:
    def test_sets_and_seals_singleton(self) -> None:
        from general_ludd.process import registry as _mod
        from general_ludd.process.registry import _DEFAULT_REGISTRY_LOCK, set_default_registry

        saved = _mod._DEFAULT_REGISTRY
        try:
            reg = ProcessRegistry()
            with _DEFAULT_REGISTRY_LOCK:
                _mod._DEFAULT_REGISTRY = None

            set_default_registry(reg)
            assert _mod._DEFAULT_REGISTRY is reg
            assert reg.is_sealed is True
            assert _mod.default_registry() is reg
        finally:
            with _DEFAULT_REGISTRY_LOCK:
                _mod._DEFAULT_REGISTRY = saved

    def test_double_set_raises(self) -> None:
        from general_ludd.process import registry as _mod
        from general_ludd.process.registry import _DEFAULT_REGISTRY_LOCK, set_default_registry

        saved = _mod._DEFAULT_REGISTRY
        try:
            with _DEFAULT_REGISTRY_LOCK:
                _mod._DEFAULT_REGISTRY = None

            set_default_registry(ProcessRegistry())
            with pytest.raises(RuntimeError, match="already set"):
                set_default_registry(ProcessRegistry())
        finally:
            with _DEFAULT_REGISTRY_LOCK:
                _mod._DEFAULT_REGISTRY = saved
