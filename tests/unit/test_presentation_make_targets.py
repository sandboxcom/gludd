"""Make contracts for the offline-first Reveal.js presentation tooling."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
import yaml
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]


def _contract(name: str) -> dict[str, object]:
    payload = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    return next(item for item in payload["targets"] if item["name"] == name)


def test_presentation_asset_target_is_offline_and_read_only_by_default() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    presentation_fragment = (
        ROOT / "make" / "90-infrastructure-and-services.mk"
    ).read_text(encoding="utf-8")

    assert "PRESENTATION_VENDOR_VALIDATE_ONLY ?= 1" in presentation_fragment
    assert "vendor-presentation-assets:" in presentation_fragment
    assert "scripts/vendor_presentation_assets.py" in presentation_fragment
    assert "--validate-only" in presentation_fragment
    assert "--refresh" in presentation_fragment
    assert "vendor-presentation-assets" in makefile
    assert _contract("vendor-presentation-assets") == {
        "name": "vendor-presentation-assets",
        "make_variables": ["PRESENTATION_VENDOR_VALIDATE_ONLY"],
        "behavior": (
            "make vendor-presentation-assets "
            "PRESENTATION_VENDOR_VALIDATE_ONLY=1"
        ),
    }


def test_presentation_browser_target_has_explicit_owned_bounds() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    presentation_fragment = (
        ROOT / "make" / "90-infrastructure-and-services.mk"
    ).read_text(encoding="utf-8")
    pages_workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(
        encoding="utf-8"
    )

    assert "PRESENTATION_BROWSER_VALIDATE_ONLY ?= 1" in presentation_fragment
    assert "PRESENTATION_BROWSER_ROOT ?= /tmp/gludd-playwright-browsers" in presentation_fragment
    assert "PRESENTATION_BROWSER_OUTPUT ?= /tmp/gludd-presentation-browser" in presentation_fragment
    assert "PRESENTATION_BROWSER_TIMEOUT ?= 600" in presentation_fragment
    assert "PRESENTATION_BROWSER_ENGINES ?= chromium webkit" in presentation_fragment
    assert "presentation-browser-test:" in presentation_fragment
    assert (
        "$(UV) run --extra presentation-test python "
        "scripts/run_presentation_browser_tests.py"
    ) in presentation_fragment
    for argument in ("--browser", "--browser-root", "--output-root", "--timeout-seconds"):
        assert argument in presentation_fragment
    assert "--validate-only" in presentation_fragment
    assert "--run" in presentation_fragment
    assert "presentation-browser-test" in makefile
    assert (
        "presentation-browser-test   Validate/run bounded Chromium + WebKit acceptance "
        "(PRESENTATION_BROWSER_VALIDATE_ONLY=0|1)"
    ) in makefile
    assert _contract("presentation-browser-test") == {
        "name": "presentation-browser-test",
        "make_variables": [
            "PRESENTATION_BROWSER_VALIDATE_ONLY",
            "PRESENTATION_BROWSER_ENGINES",
            "PRESENTATION_BROWSER_ROOT",
            "PRESENTATION_BROWSER_OUTPUT",
            "PRESENTATION_BROWSER_TIMEOUT",
            "PRESENTATION_BROWSER_JS_COVERAGE",
            "OBSERVED_ROOT", "OBSERVED_HEARTBEAT_SECS", "OBSERVED_QUIET_SECS",
            "OBSERVED_MAX_SECS", "OBSERVED_RETAIN_RUNS",
        ],
        "behavior": (
            "make presentation-browser-test "
            "PRESENTATION_BROWSER_VALIDATE_ONLY=1 "
            "PRESENTATION_BROWSER_ENGINES='chromium webkit' "
            "PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers "
            "PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-presentation-browser "
            "PRESENTATION_BROWSER_TIMEOUT=600 "
            "PRESENTATION_BROWSER_JS_COVERAGE=0 "
            "OBSERVED_ROOT=/tmp/gludd-presentation-observed "
            "OBSERVED_HEARTBEAT_SECS=15 OBSERVED_QUIET_SECS=60 "
            "OBSERVED_MAX_SECS=1200 OBSERVED_RETAIN_RUNS=20"
        ),
    }
    assert "PRESENTATION_BROWSER_TIMEOUT=600" in pages_workflow
    assert "PRESENTATION_BROWSER_TIMEOUT=300" not in pages_workflow


def test_presentation_browser_install_is_read_only_by_default() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    presentation_fragment = (
        ROOT / "make" / "90-infrastructure-and-services.mk"
    ).read_text(encoding="utf-8")

    assert "PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY ?= 1" in presentation_fragment
    assert "PRESENTATION_BROWSER_INSTALL_TIMEOUT ?= 600" in presentation_fragment
    assert "PRESENTATION_BROWSER_ENGINES ?= chromium webkit" in presentation_fragment
    assert "presentation-browser-install:" in presentation_fragment
    assert (
        "$(UV) run --extra presentation-test python "
        "scripts/run_presentation_browser_tests.py"
    ) in presentation_fragment
    for argument in (
        "--check-browser",
        "--install-browser",
        "--browser",
        "--browser-root",
        "--output-root",
        "--timeout-seconds",
    ):
        assert argument in presentation_fragment
    assert "presentation-browser-install" in makefile
    assert (
        "presentation-browser-install Check/install pinned Chromium + WebKit "
        "(PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY=0|1)"
    ) in makefile
    assert _contract("presentation-browser-install") == {
        "name": "presentation-browser-install",
        "make_variables": [
            "PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY",
            "PRESENTATION_BROWSER_ENGINES",
            "PRESENTATION_BROWSER_ROOT",
            "PRESENTATION_BROWSER_OUTPUT",
            "PRESENTATION_BROWSER_INSTALL_TIMEOUT",
        ],
        "behavior": (
            "make presentation-browser-install "
            "PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY=1 "
            "PRESENTATION_BROWSER_ENGINES='chromium webkit' "
            "PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers "
            "PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-presentation-browser "
            "PRESENTATION_BROWSER_INSTALL_TIMEOUT=600"
        ),
    }


def test_presentation_browser_dependency_install_is_bounded_and_runtime_verified() -> None:
    """The hosted Linux lane installs and launch-probes the exact pinned engine."""
    makefile = compose_makefile(ROOT / "Makefile")
    presentation_fragment = (
        ROOT / "make" / "90-infrastructure-and-services.mk"
    ).read_text(encoding="utf-8")

    assert "presentation-browser-install-deps:" in presentation_fragment
    assert "scripts/run_presentation_browser_tests.py" in presentation_fragment
    assert "--install-browser-with-deps" in presentation_fragment
    assert 'playwright install-deps "$$browser"' not in presentation_fragment
    assert "presentation-browser-install-deps" in makefile
    assert _contract("presentation-browser-install-deps") == {
        "name": "presentation-browser-install-deps",
        "make_variables": [
            "PRESENTATION_BROWSER_DEPS_VALIDATE_ONLY",
            "PRESENTATION_BROWSER_ENGINES",
            "PRESENTATION_BROWSER_ROOT",
            "PRESENTATION_BROWSER_OUTPUT",
            "PRESENTATION_BROWSER_INSTALL_TIMEOUT",
        ],
        "behavior": (
            "make presentation-browser-install-deps "
            "PRESENTATION_BROWSER_DEPS_VALIDATE_ONLY=1 "
            "PRESENTATION_BROWSER_ENGINES='webkit' "
            "PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers "
            "PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-presentation-browser "
            "PRESENTATION_BROWSER_INSTALL_TIMEOUT=600"
        ),
    }


def test_native_safari_target_is_bounded_and_read_only_by_default() -> None:
    """Native Safari remains explicit and never enables Remote Automation."""
    makefile = compose_makefile(ROOT / "Makefile")
    presentation_fragment = (
        ROOT / "make" / "90-infrastructure-and-services.mk"
    ).read_text(encoding="utf-8")

    assert "PRESENTATION_SAFARI_VALIDATE_ONLY ?= 1" in presentation_fragment
    assert "PRESENTATION_SAFARI_DRIVER ?= /usr/bin/safaridriver" in presentation_fragment
    assert "PRESENTATION_SAFARI_OUTPUT ?= /tmp/gludd-presentation-safari" in presentation_fragment
    assert "PRESENTATION_SAFARI_TIMEOUT ?= 60" in presentation_fragment
    assert "presentation-safari-test:" in presentation_fragment
    assert "scripts.run_presentation_safari_smoke" in presentation_fragment
    assert "--enable" not in presentation_fragment
    assert "presentation-safari-test" in makefile
    assert _contract("presentation-safari-test") == {
        "name": "presentation-safari-test",
        "make_variables": [
            "PRESENTATION_SAFARI_VALIDATE_ONLY",
            "PRESENTATION_SAFARI_DRIVER",
            "PRESENTATION_SAFARI_OUTPUT",
            "PRESENTATION_SAFARI_TIMEOUT",
        ],
        "behavior": (
            "make presentation-safari-test "
            "PRESENTATION_SAFARI_VALIDATE_ONLY=1 "
            "PRESENTATION_SAFARI_DRIVER=/usr/bin/safaridriver "
            "PRESENTATION_SAFARI_OUTPUT=/tmp/gludd-presentation-safari "
            "PRESENTATION_SAFARI_TIMEOUT=60"
        ),
    }


def test_presentation_pages_probe_is_bounded_and_read_only_by_default() -> None:
    """Public revision checks must be explicit, bounded, and content-free."""
    makefile = compose_makefile(ROOT / "Makefile")
    presentation_fragment = (
        ROOT / "make" / "90-infrastructure-and-services.mk"
    ).read_text(encoding="utf-8")

    assert "PRESENTATION_PAGES_PROBE_VALIDATE_ONLY ?= 1" in presentation_fragment
    assert "PRESENTATION_PAGES_PROBE_TIMEOUT ?= 20" in presentation_fragment
    assert "presentation-pages-probe:" in presentation_fragment
    assert "scripts/probe_presentation_pages.py" in presentation_fragment
    for argument in ("--url", "--expected-sha", "--timeout-seconds"):
        assert argument in presentation_fragment
    assert "presentation-pages-probe" in makefile
    assert _contract("presentation-pages-probe") == {
        "name": "presentation-pages-probe",
        "make_variables": [
            "PRESENTATION_PAGES_PROBE_VALIDATE_ONLY",
            "PRESENTATION_PAGES_URL",
            "PRESENTATION_PAGES_EXPECTED_SHA",
            "PRESENTATION_PAGES_PROBE_TIMEOUT",
        ],
        "behavior": (
            "make presentation-pages-probe "
            "PRESENTATION_PAGES_PROBE_VALIDATE_ONLY=1 "
            "PRESENTATION_PAGES_URL=https://sandboxcom.github.io/gludd/ "
            f"PRESENTATION_PAGES_EXPECTED_SHA={'a' * 40} "
            "PRESENTATION_PAGES_PROBE_TIMEOUT=20"
        ),
    }



def test_javascript_coverage_make_flag_is_optional_explicit_and_documented() -> None:
    """The existing browser owner keeps default timing uninstrumented."""
    fragment = (ROOT / "make" / "90-infrastructure-and-services.mk").read_text(encoding="utf-8")
    makefile = compose_makefile(ROOT / "Makefile")
    assert "PRESENTATION_BROWSER_JS_COVERAGE ?= 0" in fragment
    recipe = fragment.split("presentation-browser-test:", 1)[1].split("\n\n", 1)[0]
    for directive in (
        "override GLUDD_PRESENTATION_BROWSER_JS_COVERAGE := "
        "$(value PRESENTATION_BROWSER_JS_COVERAGE)",
        "export GLUDD_PRESENTATION_BROWSER_JS_COVERAGE := "
        "$(value PRESENTATION_BROWSER_JS_COVERAGE)",
        "unexport PRESENTATION_BROWSER_JS_COVERAGE",
    ):
        assert directive in fragment
    assert 'case "$$GLUDD_PRESENTATION_BROWSER_JS_COVERAGE" in 0|1)' in recipe
    assert '--js-coverage "$$GLUDD_PRESENTATION_BROWSER_JS_COVERAGE"' in recipe
    assert "$(PRESENTATION_BROWSER_JS_COVERAGE)" not in recipe
    assert "PRESENTATION_BROWSER_JS_COVERAGE" in makefile
    contract = _contract("presentation-browser-test")
    assert contract["make_variables"] == [
        "PRESENTATION_BROWSER_VALIDATE_ONLY",
        "PRESENTATION_BROWSER_ENGINES",
        "PRESENTATION_BROWSER_ROOT",
        "PRESENTATION_BROWSER_OUTPUT",
        "PRESENTATION_BROWSER_TIMEOUT",
        "PRESENTATION_BROWSER_JS_COVERAGE",
        "OBSERVED_ROOT", "OBSERVED_HEARTBEAT_SECS", "OBSERVED_QUIET_SECS",
        "OBSERVED_MAX_SECS", "OBSERVED_RETAIN_RUNS",
    ]
    assert "PRESENTATION_BROWSER_JS_COVERAGE=0" in str(contract["behavior"])


def test_javascript_coverage_config_selects_whole_source_and_locked_native_tool() -> None:
    """No function fragments or coverage-ignore rules can substitute for the file."""
    config = json.loads(
        (ROOT / "config" / "presentation_javascript_coverage.json").read_text(encoding="utf-8")
    )
    assert config["all"] is True
    assert config["include"] == ["docs/presentation/deck/presentation.js"]
    assert config["src"] == ["docs/presentation/deck"]
    assert config["exclude"] == []
    assert {"json", "json-summary", "lcov", "text"} <= set(config["reporter"])
    manifest = json.loads((ROOT / ".opencode" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / ".opencode" / "package-lock.json").read_text(encoding="utf-8"))
    version = manifest["devDependencies"]["c8"]
    assert version
    assert version[0].isdigit()
    assert not any(character in version for character in "^~*<>=| ")
    assert lock["packages"][""]["devDependencies"]["c8"] == version
    assert lock["packages"]["node_modules/c8"]["version"] == version
    assert lock["packages"]["node_modules/c8"]["integrity"]



def _pages_job(job_name: str) -> dict[str, object]:
    """Use the existing mature YAML parser rather than inventing a workflow parser."""
    payload: object = yaml.safe_load((ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    jobs = payload["jobs"]
    assert isinstance(jobs, dict)
    job = jobs[job_name]
    assert isinstance(job, dict)
    return cast(dict[str, object], job)


def _pages_job_steps(job_name: str) -> list[dict[str, object]]:
    steps = _pages_job(job_name)["steps"]
    assert isinstance(steps, list)
    assert all(isinstance(step, dict) for step in steps)
    return cast(list[dict[str, object]], steps)


def test_pages_preserves_uninstrumented_matrix_before_chromium_coverage() -> None:
    """A separate dependent job preserves every original pass and its deadline."""
    validation_steps = _pages_job_steps("validate")
    coverage_job = _pages_job("javascript-coverage")
    coverage_steps = _pages_job_steps("javascript-coverage")
    assert coverage_job["needs"] == "validate"
    assert not coverage_job.get("if")
    assert not coverage_job.get("continue-on-error")
    acceptance = [
        step for step in validation_steps
        if "make presentation-browser-test" in str(step.get("run", ""))
    ]
    coverage = [
        step for step in coverage_steps
        if "make presentation-browser-test" in str(step.get("run", ""))
    ]
    assert len(acceptance) == len(coverage) == 1
    original = acceptance[0]
    instrumented = coverage[0]
    assert "PRESENTATION_BROWSER_JS_COVERAGE=0" in str(original["run"])
    assert 'PRESENTATION_BROWSER_ENGINES="${{ matrix.browser }}"' in str(original["run"])
    assert "PRESENTATION_BROWSER_TIMEOUT=600" in str(original["run"])
    assert not original.get("if")
    assert not instrumented.get("if")
    assert "PRESENTATION_BROWSER_JS_COVERAGE=1" in str(instrumented["run"])
    assert "PRESENTATION_BROWSER_ENGINES=chromium" in str(instrumented["run"])
    assert "PRESENTATION_BROWSER_TIMEOUT=600" in str(instrumented["run"])
    assert (
        "PRESENTATION_BROWSER_OUTPUT="
        "/tmp/gludd-presentation-js-coverage"
    ) in str(instrumented["run"])
    assert not instrumented.get("continue-on-error")


def test_pages_coverage_prepares_pinned_existing_node_owner_before_use() -> None:
    """The coverage lane has no first-use download or floating Node action."""
    steps = _pages_job_steps("javascript-coverage")
    setup = [
        (index, step) for index, step in enumerate(steps)
        if step.get("uses") == "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020"
    ]
    assert len(setup) == 1
    node_index, node = setup[0]
    options = node.get("with")
    assert isinstance(options, dict)
    assert str(options["node-version"]) == "26"
    installs = [
        (index, step) for index, step in enumerate(steps)
        if "make node-deps-sync" in str(step.get("run", ""))
    ]
    assert len(installs) == 1
    install_index, install = installs[0]
    assert node_index < install_index
    command = str(install["run"])
    for value in (
        "NODE_DEPS_VALIDATE_ONLY=0", "NODE_DEPS_NPM_USERCONFIG=/dev/null",
        (
            "NODE_DEPS_NPM_CACHE="
            "/tmp/gludd-npm-cache-public-v1"
        ),
        "NODE_DEPS_NPM_REGISTRY=https://registry.npmjs.org",
        "NODE_DEPS_NPM_UPDATE_NOTIFIER=false",
    ):
        assert value in command
    instrumented_index = next(
        index for index, step in enumerate(steps)
        if "PRESENTATION_BROWSER_JS_COVERAGE=1" in str(step.get("run", ""))
    )
    assert install_index < instrumented_index
    assert "npx " not in command


def test_pages_coverage_artifact_is_retained_on_success_and_failure() -> None:
    """Passing percentages remain reviewable rather than disappearing on success."""
    steps = _pages_job_steps("javascript-coverage")
    retained = []
    for step in steps:
        options = step.get("with")
        if isinstance(options, dict) and "/tmp/gludd-presentation-js-coverage" in str(options.get("path", "")):
            retained.append(step)
    assert len(retained) == 1
    artifact = retained[0]
    assert str(artifact.get("uses", "")).startswith("actions/upload-artifact@")
    assert "always()" in str(artifact.get("if", ""))
    assert not _pages_job("javascript-coverage").get("strategy")
    assert artifact["if"] == "always()"
    options = artifact["with"]
    assert isinstance(options, dict)
    assert options["if-no-files-found"] == "error"
    assert int(str(options["retention-days"])) >= 1


def _run_native_presentation_flag(
    tmp_path: Path, *, coverage_flag: str, flag_origin: str, validate_flag: str = "1",
    browser_exit: int = 0,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Exercise the real target, replacing only its UV child with an argv recorder."""
    selected_make = shutil.which("make")
    assert selected_make is not None
    recorder = tmp_path / "record-uv.py"
    calls = tmp_path / "uv-calls.jsonl"
    carrier_makefile = tmp_path / "native-carriers.mk"
    canary = "keep  two spaces PRESENTATION_BROWSER_JS_COVERAGE=opaque remain  exact"
    carrier_makefile.write_text(
        ".PHONY: refuse native-carriers\n"
        "refuse:\n\t@exit 23\n"
        "native-carriers:\n"
        f"\t@{shlex.quote(sys.executable)} {shlex.quote(str(recorder))} native-carriers "
        "'$(subst ','\"'\"',$(PRESENTATION_FLAG_CANARY))'\n",
        encoding="utf-8",
    )
    inherited_names = (
        "CI", "NPROC", "GLUDD_XDIST_WORKERS", "UV_PYTHON", "UV_PROJECT_ENVIRONMENT",
        "UV_NO_SYNC", "UV_PYTHON_DOWNLOADS", "PYTHONPATH",
    )
    recorder.write_text(
        "import json, os, subprocess, sys\n"
        "from pathlib import Path\n"
        "if sys.argv[1:4] == ['run', 'python', 'scripts/stream_command.py']:\n"
        "    observed = subprocess.run([sys.executable, *sys.argv[3:]], check=False)\n"
        "    raise SystemExit(observed.returncode)\n"
        "if sys.argv[1:2] == ['native-carriers']:\n"
        "    print(json.dumps({'canary': sys.argv[2]}))\n"
        "    raise SystemExit(0)\n"
        f"native = subprocess.run([{selected_make!r}, '--no-print-directory', "
        f"'-f', {str(carrier_makefile)!r}, 'refuse', 'native-carriers'], "
        "check=False, capture_output=True, text=True, timeout=10)\n"
        "assert native.returncode == 2, native.stdout + native.stderr\n"
        "assert native.stdout, 'native Make lost inherited keep-going: ' + native.stderr\n"
        "report = {'argv': sys.argv[1:], 'native': json.loads(native.stdout), "
        "'flags': os.environ.get('MAKEFLAGS', ''), 'level': os.environ.get('MAKELEVEL', ''), "
        f"'inherited': {{name: os.environ.get(name) for name in {inherited_names!r}}}}}\n"
        f"with Path({str(calls)!r}).open('a', encoding='utf-8') as output:\n"
        "    output.write(json.dumps(report) + '\\n')\n"
        f"raise SystemExit({browser_exit})\n",
        encoding="utf-8",
    )
    uv = f"{shlex.quote(sys.executable)} {shlex.quote(str(recorder))}"
    engines = "chromium webkit" if coverage_flag == "0" else "chromium"
    command = [
        selected_make, "--no-print-directory", "--keep-going", "-f", str(ROOT / "Makefile"),
        "presentation-browser-test", f"UV={uv}", f"PRESENTATION_FLAG_CANARY={canary}",
        f"PRESENTATION_BROWSER_VALIDATE_ONLY={validate_flag}",
        f"PRESENTATION_BROWSER_ENGINES={engines}",
        f"PRESENTATION_BROWSER_ROOT={tmp_path / 'browser binaries'}",
        f"PRESENTATION_BROWSER_OUTPUT={tmp_path / 'browser output'}",
        "PRESENTATION_BROWSER_TIMEOUT=600",
        f"OBSERVED_ROOT={tmp_path / 'observed'}",
        "OBSERVED_HEARTBEAT_SECS=1", "OBSERVED_QUIET_SECS=10",
        "OBSERVED_MAX_SECS=20", "OBSERVED_RETAIN_RUNS=2",
    ]
    inherited = os.environ.copy()
    environment = inherited.copy()
    if flag_origin == "command-line":
        command.append(f"PRESENTATION_BROWSER_JS_COVERAGE={coverage_flag}")
    else:
        assert flag_origin == "environment"
        environment["PRESENTATION_BROWSER_JS_COVERAGE"] = coverage_flag
    result = subprocess.run(
        command, cwd=ROOT, env=environment, check=False, capture_output=True,
        text=True, timeout=30,
    )
    assert os.environ == inherited
    return result, calls


@pytest.mark.parametrize("validate_flag", ("0", "1"))
@pytest.mark.parametrize("coverage_flag", ("0", "1"))
def test_native_browser_observer_protection_is_automatic_and_plans_stay_read_only(
    tmp_path: Path, coverage_flag: str, validate_flag: str,
) -> None:
    """A real observer must own each executed engine before its native child."""
    result, calls = _run_native_presentation_flag(
        tmp_path, coverage_flag=coverage_flag, flag_origin="command-line",
        validate_flag=validate_flag,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.is_file()
    observed = tmp_path / "observed"
    if validate_flag == "1":
        assert not observed.exists()
        return
    engines = ("chromium", "webkit") if coverage_flag == "0" else ("chromium",)
    for engine in engines:
        current = observed / f"presentation-browser-{engine}" / "current.json"
        assert current.is_file(), result.stdout + result.stderr
        status = cast(dict[str, object], json.loads(current.read_text(encoding="utf-8")))
        assert status["state"] == "passed" and status["exit_code"] == 0
        assert type(status["owner_pid"]) is int and type(status["child_pid"]) is int
        assert status["owner_pid"] != status["child_pid"]
        assert status["termination_reason"] is None
        log_path = Path(cast(str, status["log_path"]))
        assert log_path.is_relative_to(observed) and log_path.is_file()


def test_native_browser_observer_keeps_child_failure_and_stops_matrix(tmp_path: Path) -> None:
    result, calls = _run_native_presentation_flag(
        tmp_path, coverage_flag="0", flag_origin="command-line", validate_flag="0",
        browser_exit=27,
    )
    assert result.returncode != 0
    samples = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    assert len(samples) == 1 and samples[0]["argv"][6:8] == ["--browser", "chromium"]
    observed = tmp_path / "observed"
    current = observed / "presentation-browser-chromium" / "current.json"
    assert current.is_file(), result.stdout + result.stderr
    status = cast(dict[str, object], json.loads(current.read_text(encoding="utf-8")))
    assert status["state"] == "failed" and status["exit_code"] == 27
    assert not (observed / "presentation-browser-webkit").exists()


@pytest.mark.parametrize("flag_origin", ("command-line", "environment"))
@pytest.mark.parametrize("validate_flag", ("0", "1"))
@pytest.mark.parametrize("coverage_flag", ("0", "1"))
def test_native_make_routes_literal_javascript_coverage_flag_without_losing_carriers(
    tmp_path: Path, coverage_flag: str, flag_origin: str, validate_flag: str,
) -> None:
    """Literal flags retain the original CLI, bounds, engines and recursive Make state."""
    result, calls = _run_native_presentation_flag(
        tmp_path, coverage_flag=coverage_flag, flag_origin=flag_origin,
        validate_flag=validate_flag,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    samples = [
        cast(dict[str, object], json.loads(line))
        for line in calls.read_text(encoding="utf-8").splitlines()
    ]
    engines = ("chromium", "webkit") if coverage_flag == "0" else ("chromium",)
    assert len(samples) == len(engines)
    for sample, engine in zip(samples, engines, strict=True):
        assert sample["argv"] == [
            "run", "--extra", "presentation-test", "python",
            "scripts/run_presentation_browser_tests.py",
            "--validate-only" if validate_flag == "1" else "--run",
            "--browser", engine,
            "--browser-root", str(tmp_path / "browser binaries"),
            "--output-root", str(tmp_path / "browser output" / engine),
            "--timeout-seconds", "600", "--js-coverage", coverage_flag,
        ]
        assert sample["native"] == {
            "canary": "keep  two spaces PRESENTATION_BROWSER_JS_COVERAGE=opaque remain  exact",
        }
        assert int(str(sample["level"])) >= 1
        assert sample["flags"], "native Make flags disappeared"
        assert sample["inherited"] == {
            name: os.environ.get(name)
            for name in (
                "CI", "NPROC", "GLUDD_XDIST_WORKERS", "UV_PYTHON",
                "UV_PROJECT_ENVIRONMENT", "UV_NO_SYNC", "UV_PYTHON_DOWNLOADS", "PYTHONPATH",
            )
        }


@pytest.mark.parametrize("flag_origin", ("command-line", "environment"))
@pytest.mark.parametrize("literal_form", (
    "make-shell", "make-file", "shell-substitution", "backticks", "quote-breakout",
))
def test_native_make_refuses_javascript_coverage_text_before_evaluation_or_child(
    tmp_path: Path, flag_origin: str, literal_form: str,
) -> None:
    """Invalid new-flag data must not execute while Make or its shell prepares validation."""
    tripwire = tmp_path / "literal-flag-executed"
    backtick = chr(96)
    hostile_values = {
        "make-shell": f"$(shell touch {tripwire})",
        "make-file": f"$(file >{tripwire},unexpected)",
        "shell-substitution": f"$$(touch {tripwire})",
        "backticks": f"{backtick}touch {tripwire}{backtick}",
        "quote-breakout": f'x"$$(touch {tripwire})"',
    }
    result, calls = _run_native_presentation_flag(
        tmp_path, coverage_flag=hostile_values[literal_form], flag_origin=flag_origin,
    )
    detail = result.stdout + result.stderr
    assert not tripwire.exists(), detail
    assert not calls.exists(), detail
    assert result.returncode != 0, detail
    assert "PRESENTATION_BROWSER_JS_COVERAGE must be 0 or 1" in detail
