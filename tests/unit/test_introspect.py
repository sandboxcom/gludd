"""Tests for CodebaseIntrospector — composes genuinely-capturable signals.

The introspector reports best-effort signals (None where a source is missing)
over a SEEDED repo_root, so these tests build a tiny fake repo tree and assert
the snapshot's shape and that each facet reflects the seeded data.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from general_ludd.code_intelligence.introspect import CodebaseIntrospector


def _seed_repo(root: Path) -> None:
    src = root / "src" / "general_ludd"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    # A module with measurable complexity (functions, nesting, branches).
    (src / "thing.py").write_text(
        textwrap.dedent(
            """
            def alpha(x):
                if x:
                    for i in range(x):
                        if i % 2:
                            return i
                return 0

            def beta():
                return 1
            """
        )
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_thing.py").write_text("def test_x():\n    assert True\n")

    # Seed a coverage.xml so the coverage facet has data.
    (root / "coverage.xml").write_text(
        textwrap.dedent(
            """\
            <?xml version="1.0" ?>
            <coverage line-rate="0.5">
              <packages>
                <package name="general_ludd">
                  <classes>
                    <class filename="src/general_ludd/thing.py" line-rate="0.4"/>
                  </classes>
                </package>
              </packages>
            </coverage>
            """
        )
    )

    # Seed a .gate-status so mypy/lint debt parses.
    (root / ".gate-status").write_text(
        "=== GATE ===\nlint PASS 0\ntypecheck PASS 0\ncollect PASS 0\n"
        "test PASS 0\nsmoke PASS\n---\nepoch 1700000000\n"
    )


def test_snapshot_has_expected_keys(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    intro = CodebaseIntrospector(str(tmp_path))
    snap = intro.snapshot()
    for key in (
        "churn",
        "complexity",
        "coverage",
        "debt",
        "dead_code",
        "missing_tests",
        "perf_cost",
        "recent_failures",
    ):
        assert key in snap


def test_complexity_reflects_seeded_module(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    intro = CodebaseIntrospector(str(tmp_path))
    snap = intro.snapshot()
    complexity = snap["complexity"]
    assert complexity is not None
    modules = {m["module"]: m for m in complexity["modules"]}
    assert any("thing.py" in k for k in modules)
    thing = next(v for k, v in modules.items() if "thing.py" in k)
    # alpha + beta == 2 functions
    assert thing["func_count"] == 2
    # alpha nests if/for/if ⇒ max nesting >= 3
    assert thing["max_nesting"] >= 3
    assert thing["branch_count"] >= 2


def test_coverage_parsed_from_seeded_xml(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    intro = CodebaseIntrospector(str(tmp_path))
    snap = intro.snapshot()
    coverage = snap["coverage"]
    assert coverage is not None
    assert coverage["overall_line_rate"] == 0.5
    low = {c["file"]: c for c in coverage["low_coverage"]}
    assert any("thing.py" in f for f in low)


def test_debt_parsed_from_gate_status(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    intro = CodebaseIntrospector(str(tmp_path))
    snap = intro.snapshot()
    debt = snap["debt"]
    assert debt is not None
    assert debt["lint"] == 0
    assert debt["typecheck"] == 0


def test_missing_coverage_xml_yields_none(tmp_path: Path) -> None:
    # repo with src but no coverage.xml / .gate-status
    src = tmp_path / "src" / "general_ludd"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "m.py").write_text("VALUE = 1\n")
    intro = CodebaseIntrospector(str(tmp_path))
    snap = intro.snapshot()
    assert snap["coverage"] is None
    assert snap["debt"] is None
    # complexity still works (stdlib ast over the seeded module)
    assert snap["complexity"] is not None


def test_perf_cost_from_injected_buffer(tmp_path: Path) -> None:
    _seed_repo(tmp_path)

    class _FakeBuffer:
        def snapshot(self, **_kw: object) -> dict[str, object]:
            return {"by_phase": {"plan": {"span_count": 3, "total_cost_usd": 0.5}}}

    intro = CodebaseIntrospector(str(tmp_path), traces_buffer=_FakeBuffer())
    snap = intro.snapshot()
    assert snap["perf_cost"] is not None
    assert "plan" in snap["perf_cost"]["by_phase"]


def test_recent_failures_injected(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    history = {"failure_count": 2, "success_count": 8, "success_rate": 0.8}
    intro = CodebaseIntrospector(str(tmp_path), recent_failures=history)
    snap = intro.snapshot()
    assert snap["recent_failures"] == history
