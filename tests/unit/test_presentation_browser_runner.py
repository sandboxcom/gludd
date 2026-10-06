"""Tests for the bounded Playwright presentation runner."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts import run_presentation_browser_tests as runner


def test_plan_is_serial_pinned_and_namespaced() -> None:
    """The mandatory browser proof cannot collide with another project run."""
    plan = runner.build_plan(
        browser="chromium",
        browser_root=Path("/tmp/gludd-browser-unit"),
        output_root=Path("/tmp/gludd-presentation-unit"),
        timeout_seconds=120,
    )

    assert plan.browser == "chromium"
    assert plan.timeout_seconds == 120
    assert plan.command[0] == runner.sys.executable
    assert plan.command[4:6] == ("-n", "0")
    assert ("--tracing", "retain-on-failure") in tuple(
        zip(plan.command, plan.command[1:], strict=False)
    )


@pytest.mark.parametrize(
    "path",
    (Path("/tmp"), Path("/tmp/presentation"), Path("/var/tmp/gludd-presentation")),
)
def test_plan_rejects_unowned_output_roots(path: Path) -> None:
    """Artifacts and browsers may only use an explicit Gludd namespace."""
    with pytest.raises(ValueError, match="/tmp/gludd"):
        runner.build_plan(
            browser="chromium",
            browser_root=Path("/tmp/gludd-browser-unit"),
            output_root=path,
            timeout_seconds=120,
        )


def test_validate_plan_is_side_effect_free(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Validate-only reads contracts but never creates output or launches pytest."""
    root = tmp_path / "repo"
    test_file = root / "tests" / "browser" / "test_presentation.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_placeholder(): pass\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        'presentation-test = ["playwright==1.63.0", "pytest-playwright==0.9.0"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "ROOT", root)
    monkeypatch.setattr(runner, "TEST_FILE", test_file)
    output = Path("/tmp/gludd-presentation-validate-only-test")
    plan = runner.build_plan(
        browser="chromium",
        browser_root=Path("/tmp/gludd-browser-validate-only-test"),
        output_root=output,
        timeout_seconds=120,
    )

    runner.validate_plan(plan)

    assert not output.exists()


def test_timeout_returns_observable_124(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A stuck browser is bounded and uses the conventional timeout status."""
    plan = runner.build_plan(
        browser="chromium",
        browser_root=Path("/tmp/gludd-browser-timeout-test"),
        output_root=Path("/tmp/gludd-presentation-timeout-test"),
        timeout_seconds=30,
    )
    monkeypatch.setattr(runner, "validate_plan", lambda _plan: None)
    monkeypatch.setattr(runner, "_require_browser_executable", lambda _plan: None)

    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        raise subprocess.TimeoutExpired(plan.command, plan.timeout_seconds)

    monkeypatch.setattr(runner.subprocess, "run", timeout)

    assert runner.run_plan(plan) == 124


def test_plan_rejects_unbounded_timeout() -> None:
    """The make target cannot accidentally disable the process deadline."""
    with pytest.raises(ValueError, match="between 30 and 900"):
        runner.build_plan(
            browser="chromium",
            browser_root=Path("/tmp/gludd-browser-unit"),
            output_root=Path("/tmp/gludd-presentation-unit"),
            timeout_seconds=901,
        )
