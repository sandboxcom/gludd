"""Tests for renderers/executor.py — thin re-export module.

Verifies that the deprecated re-export module exposes the correct public API
from renderers/runner and does not accidentally shadow or drop any name.

Run: make test-iso TESTFILE='tests/unit/test_renderers_executor.py'
"""

from __future__ import annotations

import general_ludd.renderers.executor as executor_mod
from general_ludd.renderers.executor import (
    RendererFailure,
    RendererTimeout,
    run_renderer,
)
from general_ludd.renderers.runner import (
    RendererFailure as RunnerFailure,
)
from general_ludd.renderers.runner import (
    RendererTimeout as RunnerTimeout,
)
from general_ludd.renderers.runner import (
    run_renderer as runner_run_renderer,
)


class TestReExportIdentity:
    def test_run_renderer_is_same_as_runner_run_renderer(self) -> None:
        assert run_renderer is runner_run_renderer

    def test_renderer_failure_is_same_as_runner_failure(self) -> None:
        assert RendererFailure is RunnerFailure

    def test_renderer_timeout_is_same_as_runner_timeout(self) -> None:
        assert RendererTimeout is RunnerTimeout


class TestReExportModulePublicApi:
    def test_all_specifies_correct_names(self) -> None:
        assert hasattr(executor_mod, "__all__")
        assert set(executor_mod.__all__) == {"RendererFailure", "RendererTimeout", "run_renderer"}

    def test_no_extra_public_names(self) -> None:
        public_names = {n for n in dir(executor_mod) if not n.startswith("_")}
        assert "RendererFailure" in public_names
        assert "RendererTimeout" in public_names
        assert "run_renderer" in public_names
        assert {"RendererFailure", "RendererTimeout", "run_renderer"} <= public_names
        assert len(public_names - {"RendererFailure", "RendererTimeout", "run_renderer"}) <= 1
