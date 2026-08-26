"""Regression tests for source-scoped coverage audits."""

import importlib.util
import json
from pathlib import Path
from typing import Protocol, cast

ROOT = Path(__file__).parents[2]
AUDIT_SCRIPT = ROOT / "scripts" / "audit_coverage.py"


class AuditCoverageModule(Protocol):
    """Typed interface used by the dynamically loaded coverage auditor."""

    def parse_coverage_json(
        self,
        json_path: str,
        threshold: float,
        source_path: str,
        per_file_threshold: float = 75.0,
    ) -> tuple[dict[str, object], list[str], bool]:
        """Parse one coverage report within an explicit source boundary."""

    def parse_coverage_xml(
        self,
        xml_path: str,
        threshold: float,
        source_path: str,
        per_file_threshold: float = 75.0,
    ) -> tuple[dict[str, object], list[str], bool]:
        """Parse one Cobertura report within an explicit source boundary."""


def _load_auditor() -> AuditCoverageModule:
    spec = importlib.util.spec_from_file_location("audit_coverage_source_scope", AUDIT_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(AuditCoverageModule, module)


def test_explicit_source_excludes_coverage_files_outside_that_tree(tmp_path: Path) -> None:
    """A source-scoped audit must not grade unrelated measured packages."""
    source = tmp_path / "src" / "general_ludd"
    source.mkdir(parents=True)
    covered = source / "covered.py"
    unrelated = tmp_path / "collections" / "plugins" / "outside.py"
    coverage_json = tmp_path / "coverage.json"
    coverage_json.write_text(
        json.dumps(
            {
                "files": {
                    str(covered): {
                        "summary": {
                            "num_statements": 10,
                            "covered_lines": 10,
                            "num_branches": 4,
                            "covered_branches": 4,
                        }
                    },
                    str(unrelated): {
                        "summary": {
                            "num_statements": 10,
                            "covered_lines": 0,
                            "num_branches": 4,
                            "covered_branches": 0,
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    report, under, passed = _load_auditor().parse_coverage_json(
        str(coverage_json), 85, str(source), per_file_threshold=75
    )

    assert report["total_files"] == 1
    assert report["line_coverage"] == 100.0
    assert report["branch_coverage"] == 100.0
    assert under == []
    assert passed
    per_file_results = cast(dict[str, object], report["per_file_results"])
    assert list(per_file_results) == ["general_ludd/covered.py"]


def test_cobertura_artifact_preserves_independent_line_and_branch_floors(
    tmp_path: Path,
) -> None:
    """Hosted XML must expose the same source-scoped release decision as JSON."""
    source = tmp_path / "src" / "general_ludd"
    source.mkdir(parents=True)
    covered = source / "covered.py"
    branch_low = source / "branch_low.py"
    outside = tmp_path / "collections" / "outside.py"
    coverage_xml = tmp_path / "coverage.xml"
    coverage_xml.write_text(
        f"""<?xml version="1.0" ?>
<coverage>
  <packages><package name="general_ludd"><classes>
    <class filename="{covered}"><lines>
      <line number="1" hits="1" branch="true" condition-coverage="100% (2/2)"/>
      <line number="2" hits="1"/>
    </lines></class>
    <class filename="{branch_low}"><lines>
      <line number="1" hits="1" branch="true" condition-coverage="50% (1/2)" missing-branches="3"/>
      <line number="2" hits="1"/>
    </lines></class>
    <class filename="{outside}"><lines>
      <line number="1" hits="0"/>
    </lines></class>
  </classes></package></packages>
</coverage>
""",
        encoding="utf-8",
    )

    report, under, passed = _load_auditor().parse_coverage_xml(
        str(coverage_xml), 85, str(source), per_file_threshold=75
    )

    assert report["total_files"] == 2
    assert report["line_coverage"] == 100.0
    assert report["branch_coverage"] == 75.0
    assert under == ["general_ludd/branch_low.py"]
    assert not passed
    missing_arcs = cast(dict[str, list[list[int]]], report["missing_arcs"])
    assert missing_arcs == {"general_ludd/branch_low.py": [[1, 3]]}


def test_cobertura_relative_repo_paths_do_not_rebase_outside_source(
    tmp_path: Path,
) -> None:
    """Repo-relative collection files must not become application source files."""
    source = tmp_path / "src" / "general_ludd"
    source.mkdir(parents=True)
    (source / "inside.py").write_text("inside = True\n", encoding="utf-8")
    outside = tmp_path / "collections" / "outside.py"
    outside.parent.mkdir()
    outside.write_text("outside = True\n", encoding="utf-8")
    coverage_xml = tmp_path / "coverage.xml"
    coverage_xml.write_text(
        f"""<?xml version="1.0" ?>
<coverage>
  <sources><source>{tmp_path}</source></sources>
  <packages><package name="mixed"><classes>
    <class filename="src/general_ludd/inside.py"><lines>
      <line number="1" hits="1"/>
    </lines></class>
    <class filename="collections/outside.py"><lines>
      <line number="1" hits="0"/>
    </lines></class>
  </classes></package></packages>
</coverage>
""",
        encoding="utf-8",
    )

    report, under, passed = _load_auditor().parse_coverage_xml(
        str(coverage_xml), 85, str(source), per_file_threshold=75
    )

    assert report["total_files"] == 1
    assert under == []
    assert passed
