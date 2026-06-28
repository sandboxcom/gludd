"""Unit tests for the osquery availability facet in routers/facts.py.

The facet is a FAST availability probe (osqueryi --version, 2s timeout) that
fails soft to {"available": false} when the binary is absent. All subprocess /
filesystem / shutil.which boundaries are mocked — no real osquery is run.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from general_ludd.routers import facts as facts_mod


def _app(**state: Any) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(**state))


def test_available_false_when_binary_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    # Filestore yields no path; nothing on PATH.
    class _Boot:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def get_binary_path(self, name: str) -> None:
            return None

    import general_ludd.filestore.bootstrap as boot_mod

    monkeypatch.setattr(boot_mod, "BinaryBootstrapper", _Boot)
    monkeypatch.setattr(facts_mod.shutil, "which", lambda name: None)

    def _no_run(*_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("subprocess.run must not run when binary is absent")

    monkeypatch.setattr(facts_mod.subprocess, "run", _no_run)

    facet = facts_mod._osquery_facet(_app(_filestore=None))

    assert facet["available"] is False
    assert facet["version"] is None
    assert facet["path"] is None
    assert facet["source"] is None


def test_available_true_when_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boot:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def get_binary_path(self, name: str) -> None:
            return None

    import general_ludd.filestore.bootstrap as boot_mod

    monkeypatch.setattr(boot_mod, "BinaryBootstrapper", _Boot)
    monkeypatch.setattr(
        facts_mod.shutil, "which", lambda name: "/usr/local/bin/osqueryi"
    )

    class _Completed:
        returncode = 0
        stdout = "osqueryi version 5.10.2\n"
        stderr = ""

    monkeypatch.setattr(facts_mod.subprocess, "run", lambda *_a, **_k: _Completed())

    facet = facts_mod._osquery_facet(_app(_filestore=None))

    assert facet["available"] is True
    assert facet["version"] == "osqueryi version 5.10.2"
    assert facet["path"] == "/usr/local/bin/osqueryi"
    assert facet["source"] == "path"


def test_filestore_binary_preferred(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boot:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def get_binary_path(self, name: str) -> str:
            return "/store/binaries/osquery"

    import general_ludd.filestore.bootstrap as boot_mod

    monkeypatch.setattr(boot_mod, "BinaryBootstrapper", _Boot)
    monkeypatch.setattr(facts_mod.os.path, "isfile", lambda p: True)
    monkeypatch.setattr(facts_mod.os, "access", lambda p, m: True)
    # PATH must NOT be consulted once the filestore binary is found.
    monkeypatch.setattr(
        facts_mod.shutil,
        "which",
        lambda name: (_ for _ in ()).throw(AssertionError("PATH consulted")),
    )

    class _Completed:
        returncode = 0
        stdout = "osqueryi version 5.10.2\n"
        stderr = ""

    monkeypatch.setattr(facts_mod.subprocess, "run", lambda *_a, **_k: _Completed())

    facet = facts_mod._osquery_facet(_app(_filestore=object()))

    assert facet["available"] is True
    assert facet["source"] == "filestore"
    assert facet["path"] == "/store/binaries/osquery"


def test_probe_failure_fails_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boot:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def get_binary_path(self, name: str) -> None:
            return None

    import general_ludd.filestore.bootstrap as boot_mod

    monkeypatch.setattr(boot_mod, "BinaryBootstrapper", _Boot)
    monkeypatch.setattr(facts_mod.shutil, "which", lambda name: "/usr/bin/osqueryi")

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("explode")

    monkeypatch.setattr(facts_mod.subprocess, "run", _boom)

    facet = facts_mod._osquery_facet(_app(_filestore=None))

    # Located the binary but the version probe failed -> available False, no raise.
    assert facet["available"] is False
    assert facet["path"] == "/usr/bin/osqueryi"
