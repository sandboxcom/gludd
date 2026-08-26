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
