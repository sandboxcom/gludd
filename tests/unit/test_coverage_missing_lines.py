"""Tests for the focused coverage-gap reporter used by release work."""

from __future__ import annotations

from pathlib import Path

from scripts.coverage_missing_lines import compress_ranges, summarize_classes


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
