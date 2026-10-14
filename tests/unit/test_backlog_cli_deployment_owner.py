"""Trusted deployment-owner propagation through the native backlog CLI."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
from scripts import backlog_audit as cli

from general_ludd.validation.backlog_audit import EvidenceResultPlugin
from general_ludd.validation.backlog_auditor import DeploymentValidator

EntryPoint = Literal["renderer", "report", "main"]


@dataclass
class _PhaseReport:
    nodeid: str
    when: str
    passed: bool = True
    skipped: bool = False


class _TruthyResult:
    def __bool__(self) -> bool:
        raise AssertionError("deployment results must not be coerced to bool")


@pytest.fixture
def claimed_repo(tmp_path: Path) -> Path:
    """Supply two canonical claims sharing one executable evidence node."""
    source = tmp_path / "src/feature.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")
    test = tmp_path / "tests/unit/test_feature.py"
    test.parent.mkdir(parents=True)
    test.write_text("def test_feature():\n    assert True\n", encoding="utf-8")
    rows = [
        f"- [x] T-{number} — add `src/feature.py` | evidence: "
        "`tests/unit/test_feature.py::test_feature` | status: completed | "
        "deployment_ready: true | signature_state: verified"
        for number in (401, 402)
    ]
    (tmp_path / "TASKS.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def evidence_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Exercise the real task auditor without nesting a pytest session."""
    calls: list[list[str]] = []

    def execute(args: list[str], *, plugins: list[object]) -> int:
        calls.append(args)
        assert len(plugins) == 1
        plugin = plugins[0]
        assert isinstance(plugin, EvidenceResultPlugin)
        for node in args[args.index("--") + 1:]:
            for phase in ("setup", "call", "teardown"):
                plugin.pytest_runtest_logreport(_PhaseReport(node, phase))
        return 0

    monkeypatch.setattr(pytest, "main", execute)
    monkeypatch.setattr(cli, "DEFAULT_BUG_CLASSES", [])
    return calls


def _invoke(
    entrypoint: EntryPoint,
    root: Path,
    owner: DeploymentValidator | None,
) -> int:
    if entrypoint == "renderer":
        return int(not cli._print_backlog_verdicts(
            root, enabled=True, deployment_validator=owner,
        ))
    if entrypoint == "report":
        return cli.run_report(
            root, collect=False, deployment_validator=owner,
        )
    return cli.main(
        ["--repo-root", str(root), "--no-collect"],
        deployment_validator=owner,
    )


@pytest.mark.parametrize("entrypoint", ["renderer", "report", "main"])
@pytest.mark.parametrize("result", [True, False, None, 1, "true", {"passed": True}, _TruthyResult()])
def test_cli_forwards_owner_and_preserves_exact_boolean_acceptance(
    entrypoint: EntryPoint,
    result: object,
    claimed_repo: Path,
    evidence_calls: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[str] = []

    def owner(task: Mapping[str, object]) -> object:
        seen.append(str(task["id"]))
        return result

    rc = _invoke(entrypoint, claimed_repo, owner)

    output = capsys.readouterr().out
    accepted = result is True
    assert rc == (0 if accepted else 1)
    assert seen == ["T-401", "T-402"]
    assert len(evidence_calls) == 1
    assert evidence_calls[0].count("tests/unit/test_feature.py::test_feature") == 1
    for task_id in seen:
        assert f"[{task_id}] {'VERIFIED_COMPLETE' if accepted else 'INCOMPLETE'}" in output
    assert f"verified={2 if accepted else 0}" in output
    assert f"incomplete={0 if accepted else 2}" in output


@pytest.mark.parametrize("entrypoint", ["renderer", "report", "main"])
def test_cli_missing_owner_rejects_self_reported_deployment_flags(
    entrypoint: EntryPoint,
    claimed_repo: Path,
    evidence_calls: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _invoke(entrypoint, claimed_repo, None) == 1
    output = capsys.readouterr().out
    assert len(evidence_calls) == 1
    assert "verified=0" in output
    assert "incomplete=2" in output
    assert "item-scoped deployment acceptance evidence is missing" in output


@pytest.mark.parametrize("entrypoint", ["renderer", "report", "main"])
def test_cli_keeps_each_items_acceptance_independent(
    entrypoint: EntryPoint,
    claimed_repo: Path,
    evidence_calls: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    def owner(task: Mapping[str, object]) -> object:
        return task["id"] == "T-401"

    assert _invoke(entrypoint, claimed_repo, owner) == 1
    output = capsys.readouterr().out
    assert len(evidence_calls) == 1
    assert "[T-401] VERIFIED_COMPLETE" in output
    assert "[T-402] INCOMPLETE" in output
    assert "verified=1 false_claim=0 incomplete=1" in output


@pytest.mark.parametrize("entrypoint", ["renderer", "report", "main"])
def test_cli_owner_exception_fails_closed_without_hiding_other_reports(
    entrypoint: EntryPoint,
    claimed_repo: Path,
    evidence_calls: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    def owner(_task: Mapping[str, object]) -> object:
        raise RuntimeError("independent signer rejected the artifact")

    assert _invoke(entrypoint, claimed_repo, owner) == 1
    output = capsys.readouterr().out
    assert len(evidence_calls) == 1
    assert "verified=0" in output
    assert "incomplete=2" in output
    assert "independent signer rejected the artifact" in output
    if entrypoint != "renderer":
        assert "BUG-CLASS SWEEP" in output
        assert "GUARD COVERAGE" in output


@pytest.mark.parametrize("entrypoint", ["renderer", "report", "main"])
def test_cli_does_not_call_owner_when_test_evidence_is_unavailable(
    entrypoint: EntryPoint,
    claimed_repo: Path,
    evidence_calls: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def missing(_args: list[str], *, plugins: list[object]) -> int:
        assert len(plugins) == 1
        return 5

    def owner(_task: Mapping[str, object]) -> object:
        raise AssertionError("failed source evidence cannot reach deployment acceptance")

    monkeypatch.setattr(pytest, "main", missing)
    assert _invoke(entrypoint, claimed_repo, owner) == 1
    output = capsys.readouterr().out
    assert evidence_calls == []
    assert "[T-401] FALSE_CLAIM" in output
    assert "[T-402] FALSE_CLAIM" in output
    assert "verified=0 false_claim=2" in output
