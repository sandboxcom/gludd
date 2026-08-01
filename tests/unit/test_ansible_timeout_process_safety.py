"""Regression tests for thread-safe, killable Ansible timeout workers."""

from __future__ import annotations

import multiprocessing
import threading
import warnings
from typing import Any
from unittest.mock import patch

from general_ludd.ansible.core_runner import AnsibleResult, CoreAnsibleRunner


def _quick_core_execution(**_kwargs: Any) -> AnsibleResult:
    """Picklable stand-in for the in-process Ansible executor."""

    return AnsibleResult(status="successful", rc=0)


def test_timeout_worker_is_safe_with_live_threads_and_warnings_as_errors() -> None:
    """Python 3.14 must not report a multithreaded-fork deprecation warning."""

    release = threading.Event()
    background_thread = threading.Thread(target=release.wait, daemon=True)
    background_thread.start()
    runner = CoreAnsibleRunner()
    get_context = multiprocessing.get_context

    def warning_context(method: str | None = None) -> Any:
        if method == "fork":
            warnings.warn(
                "forking a multithreaded process is unsafe",
                DeprecationWarning,
                stacklevel=2,
            )
        return get_context(method)

    try:
        with (
            patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", True),
            patch.object(runner, "_execute_with_core", _quick_core_execution),
            patch(
                "general_ludd.ansible.core_runner.multiprocessing.get_context",
                side_effect=warning_context,
            ),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("error", DeprecationWarning)
            result = runner.run_playbook("/tmp/thread-safe.yml", timeout=5.0)
    finally:
        release.set()
        background_thread.join(timeout=2.0)

    assert result.status == "successful"
    assert result.rc == 0
    assert not background_thread.is_alive()
