"""Regression tests for source-scoped coverage audits."""

import importlib.util
import json
from pathlib import Path
from typing import Literal, Protocol, cast
from xml.etree import ElementTree

import pytest

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


# Raw threshold admission must stay separate from one-decimal report display.
_FloorCounts = tuple[str, int, int, int, int]
_FloorCase = tuple[float, float, bool, tuple[_FloorCounts, ...]]
_RAW_FLOOR_CASES: dict[str, _FloorCase] = {
    "aggregate-branch": (85.0, 75.0, False, (("measured.py", 100, 100, 846, 719),)),
    "aggregate-line": (85.0, 75.0, False, (("measured.py", 846, 719, 100, 100),)),
    "file-line": (
        85.0, 75.0, False,
        (("near.py", 999, 749, 100, 100), ("padding.py", 999, 999, 100, 100)),
    ),
    "aggregate-branch-exact": (85.0, 75.0, True, (("measured.py", 100, 100, 100, 85),)),
    "aggregate-line-exact": (85.0, 75.0, True, (("measured.py", 100, 85, 100, 100),)),
    "file-line-exact": (
        85.0, 75.0, True,
        (("near.py", 100, 75, 100, 100), ("padding.py", 100, 100, 100, 100)),
    ),
    "custom-exact": (
        70.0, 62.5, True,
        (("near.py", 8, 5, 8, 5), ("padding.py", 8, 8, 8, 8)),
    ),
}


def _write_raw_floor_input(
    tmp_path: Path, input_format: Literal["json", "xml"], case: str,
) -> tuple[Path, Path, float, float, bool]:
    """Write the same owned numeric measurements in each existing input format."""
    source = tmp_path / "owned_source"
    source.mkdir()
    path = tmp_path / ("raw-floor." + input_format)
    aggregate_min, per_file_min, should_pass, measured = _RAW_FLOOR_CASES[case]
    if input_format == "json":
        path.write_text(json.dumps({
            "files": {
                str(source / name): {"summary": {
                    "num_statements": statements, "covered_lines": covered,
                    "num_branches": branches, "covered_branches": taken,
                }}
                for name, statements, covered, branches, taken in measured
            },
        }), encoding="utf-8")
    else:
        root = ElementTree.Element("coverage")
        packages = ElementTree.SubElement(root, "packages")
        package = ElementTree.SubElement(packages, "package", name="owned")
        classes = ElementTree.SubElement(package, "classes")
        for name, statements, covered, branches, taken in measured:
            class_node = ElementTree.SubElement(classes, "class", filename=str(source / name))
            lines = ElementTree.SubElement(class_node, "lines")
            for index in range(statements):
                attributes = {"number": str(index + 1), "hits": "1" if index < covered else "0"}
                if index == 0 and branches:
                    attributes.update({
                        "branch": "true",
                        "condition-coverage": f"{100.0 * taken / branches}% ({taken}/{branches})",
                    })
                ElementTree.SubElement(lines, "line", attributes)
        ElementTree.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return source, path, aggregate_min, per_file_min, should_pass


@pytest.mark.parametrize("raw_floor_format", ["json", "xml"])
@pytest.mark.parametrize("raw_floor_case", list(_RAW_FLOOR_CASES))
def test_raw_floor_admission_precedes_display_rounding(
    tmp_path: Path, raw_floor_format: Literal["json", "xml"], raw_floor_case: str,
) -> None:
    source, path, aggregate_min, per_file_min, should_pass = _write_raw_floor_input(
        tmp_path, raw_floor_format, raw_floor_case,
    )
    before = path.read_bytes()
    auditor = _load_auditor()
    parse = auditor.parse_coverage_json if raw_floor_format == "json" else auditor.parse_coverage_xml
    report, under, passed = parse(str(path), aggregate_min, str(source), per_file_min)

    assert passed is should_pass
    assert report["passed"] is should_pass
    assert under == ([f"{source.name}/near.py"] if raw_floor_case == "file-line" else [])
    assert path.read_bytes() == before
    assert "e2e_branch_coverage" not in report and "e2e_branch_totals" not in report
    assert report["shards"] == [] and report["failed_shards"] == []
    if raw_floor_case == "aggregate-branch":
        assert report["covered_branches"] == 719 and report["total_branches"] == 846
        assert report["branch_coverage"] == 85.0
    if raw_floor_case == "aggregate-line":
        assert report["line_coverage"] == 85.0
    if raw_floor_case in {"file-line", "file-line-exact"}:
        values = cast(dict[str, float], report["per_file"])
        assert values[f"{source.name}/near.py"] == 75.0


def test_branchless_aggregate_uses_raw_unweighted_percentages_before_floor(
    tmp_path: Path,
) -> None:
    """Zero-branch fallback is a raw percentage mean, not weighted line coverage."""
    source = tmp_path / "owned_source"
    source.mkdir()
    measured = {
        "larger.py": (100000, 85010),
        "smaller.py": (10000, 8496),
    }
    coverage_json = tmp_path / "branchless.json"
    coverage_json.write_text(json.dumps({
        "files": {
            str(source / name): {"summary": {
                "num_statements": statements, "covered_lines": covered,
                "num_branches": 0, "covered_branches": 0,
            }}
            for name, (statements, covered) in measured.items()
        },
    }), encoding="utf-8")
    before = coverage_json.read_bytes()
    raw_line = 100.0 * (85010 + 8496) / (100000 + 10000)
    raw_branch_fallback = (100.0 * 85010 / 100000 + 100.0 * 8496 / 10000) / 2
    assert raw_line > 85.0
    assert raw_branch_fallback == pytest.approx(84.985)
    assert raw_branch_fallback < 85.0

    report, under, passed = _load_auditor().parse_coverage_json(
        str(coverage_json), 85.0, str(source), per_file_threshold=75.0,
    )

    assert coverage_json.read_bytes() == before
    assert report["total_files"] == 2
    assert report["total_branches"] == report["covered_branches"] == 0
    assert report["line_coverage"] == 85.0
    displayed_files = {
        "owned_source/larger.py": 85.0,
        "owned_source/smaller.py": 85.0,
    }
    assert report["per_file"] == displayed_files
    assert report["per_file_branch"] == displayed_files
    assert under == [] and report["files_below_threshold"] == 0
    assert "e2e_branch_coverage" not in report and "e2e_branch_totals" not in report
    assert report["shards"] == [] and report["failed_shards"] == []
    assert not passed, report
    assert report["passed"] is False
    branch_display = cast(float, report["branch_coverage"])
    assert 0.0 <= branch_display <= 100.0
    assert branch_display == 85.0
    failures = cast(list[dict[str, object]], report["threshold_failures"])
    assert len(failures) == 1
    assert failures[0]["scope"] == "aggregate"
    assert failures[0]["metric"] == "branch"
    assert failures[0]["coverage"] == pytest.approx(raw_branch_fallback)
    assert failures[0]["threshold"] == 85.0
