"""Native coverage-owner source binding for copied-environment children."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from scripts.makefile_layout import makefile_sources

ROOT = Path(__file__).resolve().parents[1]
_SOURCE_BINDING = (
    '\t\tPYTHONPATH="$$GLUDD_COVERAGE_SOURCE_ROOT/src:$$GLUDD_COVERAGE_SOURCE_ROOT/scripts:'
    '$$GLUDD_COVERAGE_SOURCE_ROOT$$' '{PYTHONPATH:+:$$PYTHONPATH}"; \\\n'
)
_CHILD_PROBE = """\
import json
import os
import sys
from pathlib import Path

from general_ludd import coverage_source_probe
import owned_coverage_script_probe
import owned_coverage_root_probe
import retained_coverage_probe

print(json.dumps({
    "source": coverage_source_probe.SOURCE,
    "source_file": str(Path(coverage_source_probe.__file__).resolve()),
    "script_source": owned_coverage_script_probe.SOURCE,
    "root_source": owned_coverage_root_probe.SOURCE,
    "retained": retained_coverage_probe.SOURCE,
    "pythonpath": os.environ.get("PYTHONPATH", ""),
    "sentinel": os.environ.get("OWNED_COVERAGE_SENTINEL"),
    "curdir_origin": os.environ.get("OWNED_COVERAGE_CURDIR_ORIGIN"),
    "curdir_value": os.environ.get("OWNED_COVERAGE_CURDIR_VALUE"),
    "pythonpath_origin": os.environ.get("OWNED_COVERAGE_PYTHONPATH_ORIGIN"),
    "coverage_root": os.environ.get("GLUDD_COVERAGE_SOURCE_ROOT"),
    "cwd": str(Path.cwd().resolve()),
    "python": sys.executable,
    "prefix": sys.prefix,
}, sort_keys=True))
raise SystemExit(int(os.environ.get("OWNED_COVERAGE_NATIVE_EXIT", "0")))
"""
_OBSERVER_PROBE = f"""\
import json
import os
import subprocess
import sys

child = subprocess.run(
    [sys.executable, "-c", {_CHILD_PROBE!r}],
    cwd=os.environ["OWNED_COVERAGE_CHILD_CWD"],
    env=dict(os.environ),
    capture_output=True,
    text=True,
    check=False,
    timeout=15,
)
if child.stdout:
    payload = json.loads(child.stdout)
    payload["observer_argv"] = sys.argv[1:]
    print(json.dumps(payload, sort_keys=True))
sys.stderr.write(child.stderr)
raise SystemExit(child.returncode)
"""


@dataclass
class CoverageSourceFixture:
    """Own only a copied native Make graph, import probes and their carriers."""

    root: Path
    link: Path
    decoy: Path
    carrier: Path
    child_cwd: Path
    marker: Path
    uv: str
    make: str
    environment: dict[str, str] = field(repr=False)
    original_environment: dict[str, str] = field(repr=False)
    producer_source: str = field(repr=False)

    @property
    def producer(self) -> Path:
        """Locate the actual coverage-files provider in the copied graph."""
        return self.root / "make/10-observability-and-tests.mk"

    def invoke(self, origin: str) -> subprocess.CompletedProcess[str]:
        """Run the unchanged real UV/Python entrypoint with one explicit origin."""
        environment = dict(self.environment)
        environment["CURDIR"] = str(self.decoy)
        environment["GLUDD_COVERAGE_SOURCE_ROOT"] = str(self.decoy)
        command = [self.make, "--no-print-directory"]
        if origin == "early-environment":
            command.append("-e")
        command.extend((
            "-f", str(self.link / "Makefile"), "coverage-files",
            "UV=" + shlex.quote(self.uv),
            "PYTHON=" + shlex.quote(sys.executable),
            "SYSTEM_PYTHON=" + shlex.quote(sys.executable),
            "ID=", "COVERAGE_TESTFILES=tests/unit/owned_source_case.py",
            "COVERAGE_CONFIG=" + str(ROOT / "config/coverage_self_improve.ini"),
            "COVERAGE_REPORT=" + str(self.root / "owned-report.json"),
            "COVERAGE_AGGREGATE_MIN=85", "COVERAGE_PER_FILE_MIN=75",
            "OBSERVED_ROOT=.gate-logs/observed", "OBSERVED_HEARTBEAT_SECS=1",
            "OBSERVED_QUIET_SECS=30", "OBSERVED_MAX_SECS=60",
            "OBSERVED_RETAIN_RUNS=1",
        ))
        if origin == "command-line":
            command.extend((
                "CURDIR=" + str(self.decoy),
                "PYTHONPATH=" + environment["PYTHONPATH"],
                "GLUDD_COVERAGE_SOURCE_ROOT=" + str(self.decoy),
            ))
        return subprocess.run(
            command, cwd=self.link, env=environment, capture_output=True,
            text=True, check=False, timeout=30,
        )

    def unguard(self) -> None:
        """Remove only the source prefix to demonstrate an active outside import."""
        source = self.producer_source
        if _SOURCE_BINDING in source:
            assert source.count(_SOURCE_BINDING) == 1
            source = source.replace(_SOURCE_BINDING, "", 1)
        self.producer.write_text(source, encoding="utf-8")

    def restore(self) -> None:
        """Restore the exact captured native provider before its guarded control."""
        self.producer.write_text(self.producer_source, encoding="utf-8")


def create_coverage_source_fixture(tmp_path: Path) -> CoverageSourceFixture:
    """Copy every native include and retain its real provider and phony context."""
    root = (tmp_path / "physical coverage checkout").resolve()
    root.mkdir()
    for source in makefile_sources(ROOT / "Makefile"):
        destination = root / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    with (root / "Makefile").open("a", encoding="utf-8") as stream:
        stream.write(
            "\nexport OWNED_COVERAGE_CURDIR_ORIGIN = $(origin CURDIR)\n"
            "export OWNED_COVERAGE_CURDIR_VALUE = $(CURDIR)\n"
            "export OWNED_COVERAGE_PYTHONPATH_ORIGIN = $(origin PYTHONPATH)\n"
        )
    link = tmp_path / "symlink coverage checkout"
    link.symlink_to(root, target_is_directory=True)
    decoy = (tmp_path / "outside opposite checkout").resolve()
    carrier = (tmp_path / "retained unrelated carrier").resolve()
    child_cwd = (tmp_path / "unrelated child cwd").resolve()
    carrier.mkdir()
    child_cwd.mkdir()
    marker = tmp_path / "owned coverage import witness"
    files = {
        root / "pyproject.toml": (
            '[project]\nname="gludd-owned-coverage-source"\nversion="0.0.0"\n'
            'requires-python=">=3.11"\n'
        ),
        root / "owned_coverage_source_boundary_program.py": _OBSERVER_PROBE,
        root / "src/general_ludd/__init__.py": "",
        root / "src/general_ludd/coverage_source_probe.py": 'SOURCE = "current-src"\n',
        root / "scripts/owned_coverage_script_probe.py": 'SOURCE = "current-scripts"\n',
        root / "owned_coverage_root_probe.py": 'SOURCE = "current-root"\n',
        decoy / "src/general_ludd/__init__.py": "",
        decoy / "src/general_ludd/coverage_source_probe.py": (
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('active outside import\\n')\n"
            'SOURCE = "opposite-src"\n'
        ),
        decoy / "scripts/owned_coverage_script_probe.py": 'SOURCE = "opposite-scripts"\n',
        decoy / "owned_coverage_root_probe.py": 'SOURCE = "opposite-root"\n',
        carrier / "retained_coverage_probe.py": 'SOURCE = "retained-carrier"\n',
    }
    for destination, content in files.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    (root / "scripts/stream_command.py").symlink_to(
        root / "owned_coverage_source_boundary_program.py"
    )
    real_uv, real_make = shutil.which("uv"), shutil.which("make")
    assert real_uv is not None and real_make is not None
    environment = dict(os.environ)
    for name in (
        "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL",
        "GNUMAKEFLAGS", "MAKEFILES", "PYTEST_ARGS",
    ):
        environment.pop(name, None)
    environment.update({
        "PYTHONPATH": os.pathsep.join(str(path) for path in (
            decoy / "src", decoy / "scripts", decoy, carrier,
        )),
        "UV_PROJECT_ENVIRONMENT": sys.prefix, "VIRTUAL_ENV": sys.prefix,
        "UV_NO_SYNC": "1", "UV_PYTHON": sys.executable, "UV_PYTHON_DOWNLOADS": "never",
        "OWNED_COVERAGE_CHILD_CWD": str(child_cwd),
        "OWNED_COVERAGE_SENTINEL": "owned-unrelated-carrier",
        "OWNED_COVERAGE_NATIVE_EXIT": "0",
    })
    assert not decoy.is_relative_to(root) and not carrier.is_relative_to(root)
    return CoverageSourceFixture(
        root, link, decoy, carrier, child_cwd, marker, real_uv, real_make,
        environment, dict(environment),
        (root / "make/10-observability-and-tests.mk").read_text(encoding="utf-8"),
    )


def assert_native_coverage_source_binding(
    fixture: CoverageSourceFixture, coverage_origin: str,
) -> None:
    """A real outside import must execute before the native prefix defeats it."""
    fixture.unguard()
    witness = fixture.invoke(coverage_origin)
    assert witness.returncode == 0, witness.stdout + witness.stderr
    assert json.loads(witness.stdout)["source"] == "opposite-src"
    assert fixture.marker.read_text(encoding="utf-8") == "active outside import\n"
    fixture.restore()
    fixture.marker.unlink()
    result = fixture.invoke(coverage_origin)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["source"] == "current-src"
    assert payload["source_file"] == str(fixture.root / "src/general_ludd/coverage_source_probe.py")
    assert payload["script_source"] == "current-scripts"
    assert payload["root_source"] == "current-root"
    assert payload["retained"] == "retained-carrier"
    assert payload["coverage_root"] == str(fixture.root)
    assert payload["cwd"] == str(fixture.child_cwd)
    assert payload["sentinel"] == "owned-unrelated-carrier"
    assert Path(payload["python"]).resolve() == Path(sys.executable).resolve()
    assert payload["prefix"] == sys.prefix
    expected_origin = {
        "command-line": "command line", "environment": "file",
        "early-environment": "environment override",
    }[coverage_origin]
    expected_curdir = fixture.root if coverage_origin == "environment" else fixture.decoy
    assert payload["curdir_origin"] == expected_origin
    assert payload["curdir_value"] == str(expected_curdir)
    assert payload["pythonpath_origin"] == (
        "command line" if coverage_origin == "command-line" else "environment"
    )
    assert payload["pythonpath"].split(os.pathsep)[:3] == [
        str(fixture.root / "src"), str(fixture.root / "scripts"), str(fixture.root),
    ]
    assert payload["pythonpath"].endswith(str(fixture.carrier))
    arguments = payload["observer_argv"]
    assert "--pytest-trace" in arguments
    assert arguments[arguments.index("--quiet-secs") + 1] == "30"
    assert arguments[arguments.index("--max-secs") + 1] == "60"
    assert arguments[arguments.index("--") + 1:][:2] == ["/bin/sh", "-c"]
    assert "-v -W error" in arguments[-1]
    assert "coverage report" in arguments[-1] and "--per-file-threshold=" in arguments[-1]
    assert not fixture.marker.exists()
    assert fixture.environment == fixture.original_environment
