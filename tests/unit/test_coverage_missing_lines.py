"""Tests for the focused coverage-gap reporter used by release work."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from scripts.coverage_missing_lines import (
    compress_ranges,
    main,
    summarize_classes,
    summarize_json,
)


def test_compress_ranges_groups_adjacent_lines() -> None:
    assert compress_ranges([3, 4, 5, 9, 11, 12]) == "3-5,9,11-12"


def test_summarize_classes_reports_only_files_below_threshold(
    tmp_path: Path,
) -> None:
    report = tmp_path / "coverage.xml"
    report.write_text(
        """\
<coverage>
  <packages>
    <package name="example">
      <classes>
        <class filename="src/example/low.py">
          <lines>
            <line number="1" hits="1"/>
            <line number="2" hits="0"/>
            <line number="3" hits="0"/>
            <line number="4" hits="1"/>
          </lines>
        </class>
        <class filename="src/example/green.py">
          <lines>
            <line number="1" hits="1"/>
            <line number="2" hits="1"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""
    )

    gaps = summarize_classes(report, threshold=75.0)

    assert len(gaps) == 1
    assert gaps[0].filename == "src/example/low.py"
    assert gaps[0].percentage == 50.0
    assert gaps[0].missing_lines == (2, 3)


def test_summarize_json_prioritizes_both_floors_and_filters_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src" / "general_ludd"
    source.mkdir(parents=True)
    report = tmp_path / "coverage.json"
    report.write_text(
        json.dumps(
            {
                "files": {
                    str(source / "line_gap.py"): {
                        "summary": {
                            "num_statements": 4,
                            "covered_lines": 1,
                            "num_branches": 2,
                            "covered_branches": 2,
                        },
                        "missing_lines": [2, 3, 4],
                        "missing_branches": [],
                    },
                    str(source / "branch_gap.py"): {
                        "summary": {
                            "num_statements": 4,
                            "covered_lines": 4,
                            "num_branches": 4,
                            "covered_branches": 2,
                        },
                        "missing_lines": [],
                        "missing_branches": [[10, 11], [10, 12]],
                    },
                    str(tmp_path / "collections" / "outside.py"): {
                        "summary": {
                            "num_statements": 4,
                            "covered_lines": 0,
                            "num_branches": 0,
                            "covered_branches": 0,
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    gaps = summarize_json(report, threshold=75.0, source=source)

    assert [gap.filename for gap in gaps] == ["line_gap.py", "branch_gap.py"]
    assert gaps[0].percentage == 25.0
    assert gaps[0].missing_lines == (2, 3, 4)
    assert gaps[1].branch_percentage == 50.0
    assert gaps[1].missing_branches == ((10, 11), (10, 12))


def test_main_prints_bounded_json_branch_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "src" / "general_ludd"
    source.mkdir(parents=True)
    report = tmp_path / "coverage.json"
    report.write_text(
        json.dumps(
            {
                "files": {
                    str(source / "branch_gap.py"): {
                        "summary": {
                            "num_statements": 4,
                            "covered_lines": 4,
                            "num_branches": 4,
                            "covered_branches": 2,
                        },
                        "missing_lines": [],
                        "missing_branches": [[10, 11], [10, 12]],
                    },
                    str(source / "line_gap.py"): {
                        "summary": {
                            "num_statements": 4,
                            "covered_lines": 1,
                            "num_branches": 0,
                            "covered_branches": 0,
                        },
                        "missing_lines": [2, 3, 4],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "coverage_missing_lines.py",
            "--json",
            str(report),
            "--source",
            str(source),
            "--threshold",
            "75",
            "--limit",
            "2",
        ],
    )

    assert main() == 0
    output = capsys.readouterr().out
    assert "files_below=2 displayed=2" in output
    assert "line_gap.py missing=2-4" in output
    assert "branches=50.0% 2/4 missing-arcs=10->11,10->12" in output


def test_main_defaults_to_xml_and_rejects_invalid_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    Path("coverage.xml").write_text(
        """\
<coverage><packages><package><classes>
<class filename="src/example/low.py"><lines>
<line number="1" hits="1"/><line number="2" hits="0"/>
</lines></class>
</classes></package></packages></coverage>
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["coverage_missing_lines.py"])
    assert main() == 0
    assert "files_below=1 displayed=1" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["coverage_missing_lines.py", "--limit", "-1"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 2
