"""Behavioral contracts for the feature-spec quality scripts."""

from __future__ import annotations

from pathlib import Path

import audit_spec_effectiveness as effectiveness
import check_spec_enforcement_coverage as enforcement_coverage
import pytest
import spec_generator_loop as generator


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _spec_text(*, enforcement: str = "`make audit-specs`") -> str:
    return (
        "### A01 — Stop early\n"
        "The first behavior line.\n"
        f"**Enforcement:** {enforcement}\n"
        "continued enforcement\n"
        "**Behavior:** Keep working.\n"
        "continued behavior\n"
        "**Test:** `tests/unit/test_stop.py`\n"
    )


def test_effectiveness_parser_and_recurrence_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    specs_path = _write(
        tmp_path / "BEHAVIORAL_SPECS.md",
        "### AA001 — stale-branch\n"
        "**Enforcement:** make branch-check\n"
        "**Behavior:** Reject stale branches.\n"
        "### AA002 — clean-release\n"
        "**Enforcement:** make release-check\n"
        "**Behavior:** Require evidence.\n",
    )
    bugs_path = _write(tmp_path / "BUGS.md", "A stale branch recurred.\n")
    ratchet_path = _write(tmp_path / "ratchet.yml", "clean-release: open\n")
    monkeypatch.setattr(effectiveness, "SPECS_FILE", specs_path)
    monkeypatch.setattr(effectiveness, "BUGS_FILE", bugs_path)
    monkeypatch.setattr(effectiveness, "RATCHET_FILE", ratchet_path)

    specs = effectiveness.parse_specs()

    assert [spec["id"] for spec in specs] == ["AA001", "AA002"]
    assert specs[0]["behavior"] == "Reject stale branches."
    assert effectiveness.check_recurrences(specs[0]) is True
    assert effectiveness.check_recurrences(specs[1]) is True

    bugs_path.write_text("unrelated\n")
    ratchet_path.write_text("{}\n")
    assert effectiveness.check_recurrences(specs[0]) is False


def test_effectiveness_missing_input_and_main_thresholds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(effectiveness, "SPECS_FILE", tmp_path / "missing.md")
    assert effectiveness.parse_specs() == []
    assert effectiveness.main() == 0
    assert "no specs found" in capsys.readouterr().out

    specs = [
        {"id": "AA001", "title": "first-check", "enforcement": "make one", "behavior": "one"},
        {"id": "AA002", "title": "second-check", "enforcement": "make two", "behavior": "two"},
    ]
    monkeypatch.setattr(effectiveness, "parse_specs", lambda: specs)
    monkeypatch.setattr(effectiveness, "check_recurrences", lambda spec: spec["id"] == "AA001")
    monkeypatch.setattr(effectiveness, "MAX_INEFFECTIVE_PCT", 10)
    assert effectiveness.main() == 1
    assert "exceeds" in capsys.readouterr().out

    monkeypatch.setattr(effectiveness, "MAX_INEFFECTIVE_PCT", 50)
    assert effectiveness.main() == 0
    assert "within" in capsys.readouterr().out


def test_enforcement_coverage_parser_retains_multiline_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    specs_path = _write(tmp_path / "BEHAVIORAL_SPECS.md", _spec_text())
    monkeypatch.setattr(enforcement_coverage, "SPECS_FILE", specs_path)

    specs = enforcement_coverage._parse_specs()

    assert specs == [
        {
            "id": "A01",
            "title": "Stop early",
            "enforcement": "`make audit-specs` continued enforcement",
            "behavior": "Keep working. continued behavior",
        }
    ]


def test_enforcement_coverage_resolves_each_supported_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    makefile = _write(root / "Makefile", "audit-specs: check\nlegacy-check: check\n")
    agents = _write(root / "AGENTS.md", "# Rules\n")
    plugin_dir = root / ".opencode/plugin"
    scripts_dir = root / "scripts"
    _write(plugin_dir / "enforce-stop.ts", "export {};\n")
    _write(scripts_dir / "check_specs.py", "pass\n")
    monkeypatch.setattr(enforcement_coverage, "ROOT", root)
    monkeypatch.setattr(enforcement_coverage, "MAKEFILE", makefile)
    monkeypatch.setattr(enforcement_coverage, "AGENTS_FILE", agents)
    monkeypatch.setattr(enforcement_coverage, "PLUGIN_DIR", plugin_dir)
    monkeypatch.setattr(enforcement_coverage, "SCRIPTS_DIR", scripts_dir)

    assert enforcement_coverage._has_template_filler("TODO later") is True
    assert enforcement_coverage._has_template_filler("specific") is False
    assert enforcement_coverage._enforcement_exists("AGENTS.md section") is True
    assert enforcement_coverage._enforcement_exists("`make audit-specs`") is True
    assert enforcement_coverage._enforcement_exists("`scripts/check_specs.py`") is True
    assert enforcement_coverage._enforcement_exists("`enforce-stop.ts`") is True
    assert enforcement_coverage._enforcement_exists("Makefile `legacy-check`") is True
    assert enforcement_coverage._enforcement_exists("planned") is False
    assert enforcement_coverage._enforcement_exists("unknown mechanism") is False


def test_enforcement_coverage_main_fail_closed_and_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing.md"
    monkeypatch.setattr(enforcement_coverage, "SPECS_FILE", missing)
    assert enforcement_coverage.main() == 1
    assert "not found" in capsys.readouterr().out

    specs_path = _write(tmp_path / "specs.md", "placeholder\n")
    monkeypatch.setattr(enforcement_coverage, "SPECS_FILE", specs_path)
    monkeypatch.setattr(enforcement_coverage, "_parse_specs", lambda: [])
    assert enforcement_coverage.main() == 1
    assert "no specs parsed" in capsys.readouterr().out

    specs = [
        {"id": "A01", "title": "one", "enforcement": "covered", "behavior": ""},
        {"id": "A02", "title": "two", "enforcement": "missing", "behavior": ""},
    ]
    monkeypatch.setattr(enforcement_coverage, "_parse_specs", lambda: specs)
    monkeypatch.setattr(
        enforcement_coverage,
        "_enforcement_exists",
        lambda value: value == "covered",
    )
    assert enforcement_coverage.main() == 1
    assert "1 specs lack enforcement" in capsys.readouterr().out

    monkeypatch.setattr(enforcement_coverage, "COVERAGE_THRESHOLD", 0.5)
    assert enforcement_coverage.main() == 0
    assert "PASS" in capsys.readouterr().out


def test_generator_parses_and_classifies_specs(tmp_path: Path) -> None:
    specs_path = _write(
        tmp_path / "specs.md",
        _spec_text(enforcement="specific mechanism")
        + "\n### B02 - Branch safely\n**Enforcement:** "
        + ("generic " * 60)
        + "\n**Test:** test branch\n",
    )

    specs = generator.parse_specs_raw(specs_path)
    stats = generator.compute_stats(specs)

    assert [spec["spec_id"] for spec in specs] == ["A01", "B02"]
    assert specs[0]["group"] == "A"
    assert specs[0]["body"] == "The first behavior line. continued enforcement continued behavior"
    assert generator.is_template_enforcement(specs[0]) is False
    assert generator.is_template_enforcement(specs[1]) is True
    assert stats["total_specs"] == 2
    assert stats["real_enforcement"] == 1
    assert stats["template_enforcement"] == 1
    assert stats["real_pct"] == 50.0
    assert stats["by_group"]["A"] == {"total": 1, "real": 1, "template": 0}
    assert stats["by_group"]["B"] == {"total": 1, "real": 0, "template": 1}


def test_generator_enforcement_is_deterministic_and_preserves_mapping() -> None:
    spec = {
        "spec_id": "B02",
        "title": "Branch safely",
        "body": "body",
        "body_lines": ["body"],
        "enforcement": "",
        "enforcement_line_idx": 2,
        "test": "test branch",
        "test_line_idx": 3,
        "header_line": 0,
        "end_line": 4,
        "group": "B",
    }

    assert generator.generate_real_enforcement(spec) == (
        "AGENTS.md `enforce-branch-discipline.ts` tool.execute.before"
    )
    assert generator.group_name_map()["B"] == "branch_discipline"

    spec["group"] = "?"
    spec["spec_id"] = "X99"
    assert "enforce-make.ts" in generator.generate_real_enforcement(spec)


def test_generator_fix_supports_dry_run_and_write(tmp_path: Path) -> None:
    specs_path = _write(tmp_path / "specs.md", _spec_text(enforcement=""))
    specs = generator.parse_specs_raw(specs_path)
    before = specs_path.read_text()

    assert generator.fix_template_specs(specs, specs_path, dry_run=True) == 1
    assert specs_path.read_text() == before
    assert generator.fix_template_specs(specs, specs_path) == 1
    assert specs_path.read_text() != before

    fixed_specs = generator.parse_specs_raw(specs_path)
    assert generator.fix_template_specs(fixed_specs, specs_path) == 0


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["--stats"], "Total specs: 1"),
        (["--dry-run"], "Would fix 1 template specs"),
        (["--fix", "--target", "0"], "Fixed 1 template specs."),
        (["--target", "0"], "already met"),
        (["--target", "1", "--max-iterations", "2"], "Target of 1 real specs MET"),
    ],
)
def test_generator_main_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    expected: str,
) -> None:
    specs_path = _write(tmp_path / "specs.md", _spec_text(enforcement=""))
    monkeypatch.setattr(generator, "SPECS_PATH", specs_path)
    monkeypatch.setattr(generator.sys, "argv", ["spec-generator", *arguments])

    generator.main()

    assert expected in capsys.readouterr().out


def test_generator_main_missing_file_and_unreachable_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(generator, "SPECS_PATH", tmp_path / "missing.md")
    monkeypatch.setattr(generator.sys, "argv", ["spec-generator"])
    with pytest.raises(SystemExit, match="1"):
        generator.main()
    assert "not found" in capsys.readouterr().err

    specs_path = _write(tmp_path / "specs.md", "### A01 — Missing enforcement line\nbody\n")
    monkeypatch.setattr(generator, "SPECS_PATH", specs_path)
    monkeypatch.setattr(
        generator.sys,
        "argv",
        ["spec-generator", "--target", "1", "--max-iterations", "1"],
    )
    with pytest.raises(SystemExit, match="1"):
        generator.main()
    output = capsys.readouterr().out
    assert "No template specs left to fix" in output
    assert "not met after 1 iterations" in output
