"""Finite diagnostic controls for completed work across current branch heads."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
from scripts import branch_reconciliation_inventory as inventory
from scripts.makefile_layout import compose_makefile

from tests.unit import test_branch_reconciliation_inventory as fixtures

TARGET = fixtures.TARGET_HEAD
SOURCE = fixtures.UNIQUE_HEAD
BASELINE = "9" * 40
SECOND = "8" * 40
STAGING_PATH = "config/v012_completed_backlog_reconciliation.json"
CLI = [
    *fixtures.CLI_ARGS, "--completed-backlog", "--baseline", "v0.1.1",
    "--staging-path", STAGING_PATH, "--report-limit", "1000", "--quiet-progress",
]


def _staging(
    *, completed: Sequence[str] = (), implemented: Sequence[str] = (),
) -> str:
    return json.dumps({
        "schema_version": 1, "release": "v0.1.2",
        "baseline": {"ref": "v0.1.1", "commit": BASELINE},
        "completed_items": [{"task_id": tid, "title": tid} for tid in completed],
        "implemented_items": [{"item_id": tid, "title": tid} for tid in implemented],
        "excluded_open_tasks": [],
    })


class CompletedGit(fixtures.FakeGit):
    """Extend the existing finite Git fixture with immutable blob evidence."""

    def __init__(
        self, *, source: str | None = "- [x] S83.201 Branch claim | status: completed\n",
        target: str | None = "# TASKS\n", baseline: str | None = "# TASKS\n",
        staging: str | None = None, refs: Sequence[tuple[str, str]] | None = None,
        ledgers: dict[str, str | None] | None = None,
    ) -> None:
        super().__init__(refs=(
            [("refs/heads/feature/current", SOURCE)] if refs is None else refs
        ))
        self.files: dict[tuple[str, str], str | None] = {
            (TARGET, "TASKS.md"): target,
            (BASELINE, "TASKS.md"): baseline,
            (SOURCE, "TASKS.md"): source,
            (TARGET, STAGING_PATH): _staging() if staging is None else staging,
        }
        for head, text in (ledgers or {}).items():
            self.files[head, "TASKS.md"] = text
        self.blobs: dict[str, str] = {}
        self.invalid_baseline = False
        self.move_after_blob = False
        self.size_override: str | None = None
        self.content_override: str | None = None

    def __call__(
        self, argv: Sequence[str], cwd: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if (
            args[1:4] == ["rev-parse", "--symbolic-full-name", "--verify"]
            and args[-1] == "v0.1.1"
        ):
            self.calls.append(args)
            return self._result(
                args, 1 if self.invalid_baseline else 0,
                "" if self.invalid_baseline else "refs/tags/v0.1.1\n",
            )
        if (
            args[1:4] == ["rev-parse", "--verify", "--quiet"]
            and args[-1] == "refs/tags/v0.1.1^{commit}"
        ):
            self.calls.append(args)
            return self._result(args, 0, f"{BASELINE}\n")
        if args[1] == "ls-tree":
            self.calls.append(args)
            head, path = args[4], args[-1]
            text = self.files.get((head, path))
            if text is None:
                return self._result(args, 0)
            oid = hashlib.sha256(text.encode()).hexdigest()
            self.blobs[oid] = text
            return self._result(args, 0, f"100644 blob {oid}\t{path}\0")
        if args[1] == "cat-file":
            self.calls.append(args)
            text = self.blobs[args[-1]]
            if args[2] == "-s":
                return self._result(
                    args, 0, self.size_override or f"{len(text.encode())}\n",
                )
            if self.move_after_blob:
                self.refs = [("refs/heads/feature/current", SECOND)]
            return self._result(args, 0, (
                text if self.content_override is None else self.content_override
            ))
        return super().__call__(argv, cwd)


def _report(
    capsys: pytest.CaptureFixture[str], fake: CompletedGit,
    *, args: Sequence[str] = CLI,
) -> tuple[int, dict[str, object]]:
    rc = inventory.main(args, run=fake)
    return rc, cast(dict[str, object], json.loads(capsys.readouterr().out))


def _rows(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    value = payload[key]
    assert isinstance(value, list)
    return cast(list[dict[str, object]], value)


def _mapping(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload[key]
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def test_branch_only_completion_is_diagnostic_and_staging_is_explicit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = CompletedGit(staging=_staging(implemented=["S83.201"]))
    rc, result = _report(capsys, fake)
    assert rc == 0
    assert result["diagnostic_only"] is True
    assert result["gate_evidence"] is False
    assert result["release_ready"] is False
    finding = _rows(result, "findings")[0]
    assert finding["kind"] == "branch-only-completion-claim"
    assert finding["task_id"] == "S83.201"
    assert finding["title"] == "Branch claim"
    assert finding["staged_for_v012"] == "implemented"
    assert finding["implementation_integration"] == "unknown"
    assert finding["target_head"] == TARGET
    source = _rows(finding, "source_claims")[0]
    assert source["head"] == SOURCE
    assert source["refs"] == ["refs/heads/feature/current"]
    assert source["classification"] == "unique"
    assert all(call[1] in {
        "rev-parse", "for-each-ref", "show-ref", "merge-base", "rev-list", "cherry",
        "check-ref-format", "ls-tree", "cat-file",
    } for call in fake.calls)


def test_same_id_complete_in_development_is_summarized_without_branch_finding(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc, result = _report(capsys, CompletedGit(
        target="- [x] S83.201 Integrated title | status: completed\n",
        staging=_staging(completed=["S83.201"]),
    ))
    assert rc == 0
    assert result["findings"] == []
    target = _mapping(result, "completed_in_target")
    assert target["count"] == 1
    assert target["since_baseline_count"] == 1
    item = _rows(target, "since_baseline_items")[0]
    assert item["task_id"] == "S83.201"
    assert item["title"] == "Integrated title"
    assert item["staged_for_v012"] == "completed"
    assert _rows(result, "current_heads")[0]["completed_in_target_count"] == 1


def test_old_v011_completion_is_history_and_baseline_only_is_visible(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc, result = _report(capsys, CompletedGit(
        source="- [x] S83.1 Old branch copy\n",
        baseline="- [x] S83.1 Old\n- [x] S83.2 Baseline only\n",
    ))
    assert rc == 0
    assert result["findings"] == []
    history = _mapping(result, "baseline_history")
    assert history["count"] == 2
    assert history["baseline_only_count"] == 1
    assert _rows(history, "baseline_only_examples")[0]["task_id"] == "S83.2"
    assert _rows(result, "current_heads")[0]["baseline_history_count"] == 1


def test_aliases_and_matching_target_head_read_each_ledger_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = CompletedGit(refs=[
        ("refs/heads/feature/a", SOURCE), ("refs/heads/feature/b", SOURCE),
        ("refs/heads/feature/same-target", TARGET),
    ])
    fake.ancestors = frozenset({TARGET})
    rc, result = _report(capsys, fake)
    assert rc == 0
    assert len(_rows(result, "findings")) == 1
    assert _rows(result, "current_heads")[0]["refs"] == [
        "refs/heads/feature/a", "refs/heads/feature/b",
    ]
    source_reads = [
        call for call in fake.calls
        if call[1] == "ls-tree" and call[4] == SOURCE and call[-1] == "TASKS.md"
    ]
    assert len(source_reads) == 1
    assert _mapping(result, "counts")["current_branches"] == 2


@pytest.mark.parametrize("text,status", [
    (None, "missing"),
    ("not a task ledger", "malformed"),
    ("- [X] S83.201 Noncanonical\n", "malformed"),
    ("- [x] No recognizable identity\n", "malformed"),
    ("- [x] S83.201 Contradiction | status: in_progress\n", "malformed"),
    ("- [x] S83.201 One\n- [x] S83.201 Two\n", "malformed"),
    ("- [x] S83.201 One\n- [ ] S83.201 Still open\n", "malformed"),
])
def test_absent_or_malformed_source_is_unknown_not_zero(
    capsys: pytest.CaptureFixture[str], text: str | None, status: str,
) -> None:
    rc, result = _report(capsys, CompletedGit(source=text))
    assert rc == 2
    assert result["ok"] is False
    assert result["evidence_complete"] is False
    head = _rows(result, "current_heads")[0]
    assert _mapping(head, "ledger")["status"] == status
    assert _mapping(head, "ledger")["completed_count"] is None
    assert head["branch_only_completion_count"] is None
    assert _mapping(result, "counts")["branch_only_completed_ids"] is None
    assert _rows(result, "findings")[0]["kind"] == "unknown-ledger"


@pytest.mark.parametrize("owner", ["target", "baseline"])
def test_unknown_comparison_ledger_cannot_assert_branch_only_completion(
    capsys: pytest.CaptureFixture[str], owner: str,
) -> None:
    fake = CompletedGit()
    fake.files[TARGET if owner == "target" else BASELINE, "TASKS.md"] = None
    rc, result = _report(capsys, fake)
    assert rc == 2
    assert not any(
        row["kind"] == "branch-only-completion-claim"
        for row in _rows(result, "findings")
    )
    assert _mapping(result, "counts")["branch_only_completed_ids"] is None


@pytest.mark.parametrize("staging", [
    "{}", "{", _staging(completed=["S83.201", "S83.201"]),
    _staging(completed=["S83.201"], implemented=["S83.201"]),
])
def test_malformed_staging_is_unknown_and_never_release_readiness(
    capsys: pytest.CaptureFixture[str], staging: str,
) -> None:
    rc, result = _report(capsys, CompletedGit(staging=staging))
    assert rc == 2
    assert _mapping(result, "staging")["status"] == "malformed"
    assert _rows(result, "findings")[0]["staged_for_v012"] == "unknown"
    assert result["release_ready"] is False


def test_missing_staging_is_unknown(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = CompletedGit()
    fake.files[TARGET, STAGING_PATH] = None
    rc, result = _report(capsys, fake)
    assert rc == 2
    assert _mapping(result, "staging")["status"] == "missing"


def test_no_current_heads_is_explicit_and_does_not_scan_historical_sources(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = CompletedGit(refs=[("refs/heads/feature/merged", SOURCE)])
    fake.ancestors = frozenset({SOURCE})
    rc, result = _report(capsys, fake)
    assert rc == 0
    assert result["current_heads"] == []
    assert result["branch_evidence"] == "no-current-heads"
    assert _mapping(result, "counts")["branch_only_completed_ids"] == 0
    assert not any(
        call[1] == "ls-tree" and call[4] == SOURCE for call in fake.calls
    )


def test_distinct_head_claims_are_deduplicated_by_task_id_with_all_sources(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc, result = _report(capsys, CompletedGit(
        refs=[("refs/heads/feature/a", SOURCE), ("refs/heads/feature/b", SECOND)],
        ledgers={SECOND: "- [x] S83.201 Other title\n"},
    ))
    assert rc == 0
    assert len(_rows(result, "findings")) == 1
    sources = _rows(_rows(result, "findings")[0], "source_claims")
    assert {row["head"] for row in sources} == {SOURCE, SECOND}
    assert {row["title"] for row in sources} == {"Branch claim", "Other title"}


def test_findings_bound_is_visible_and_fails_closed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc, result = _report(
        capsys, CompletedGit(source="- [x] S83.201 First change\n- [x] S83.202 Second change\n"),
        args=[*CLI[:-2], "1", "--quiet-progress"],
    )
    assert rc == 2
    assert result["truncated"] is True
    assert result["evidence_complete"] is False
    assert result["omitted_findings"] == 1
    assert len(_rows(result, "findings")) == 1
    assert _mapping(result, "counts")["observed_branch_only_completed_ids"] == 2


def test_oversize_object_and_inconsistent_size_are_unknown(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for size in ("8388609\n", "not-a-size\n", "-1\n", "1\n"):
        fake = CompletedGit()
        fake.size_override = size
        rc, result = _report(capsys, fake)
        assert rc == 2
        assert _mapping(_rows(result, "current_heads")[0], "ledger")["status"] in {
            "truncated", "malformed",
        }
        assert _mapping(result, "counts")["branch_only_completed_ids"] is None


def test_baseline_ref_and_terminal_branch_movement_fail_closed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    invalid = CompletedGit()
    invalid.invalid_baseline = True
    rc, result = _report(capsys, invalid)
    assert rc == 2
    assert "invalid target ref" in str(result["error"])
    moved = CompletedGit()
    moved.move_after_blob = True
    rc, result = _report(capsys, moved)
    assert rc == 2
    assert "changed" in str(result["error"])


@pytest.mark.parametrize("extra", [
    ["--report-limit", "0"], ["--report-limit", "1001"],
    ["--staging-path", "../TASKS.md"], ["--all-pages"],
    ["--after", "refs/heads/feature/a"], ["--target", "-bad"],
])
def test_invalid_report_options_do_not_start_git(
    capsys: pytest.CaptureFixture[str], extra: list[str],
) -> None:
    fake = CompletedGit()
    rc, result = _report(capsys, fake, args=[*CLI, *extra])
    assert rc == 2
    assert result["ok"] is False
    assert fake.calls == []


def test_human_report_is_bounded_sanitized_and_names_its_limits(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = inventory.main([*CLI, "--human"], run=CompletedGit(
        source="- [x] S83.201 Dangerous\x1b[31m title\n",
    ))
    output = capsys.readouterr().out
    assert rc == 0
    assert "S83.201" in output
    assert "feature/current" in output
    assert SOURCE in output and TARGET in output
    assert "diagnostic" in output.lower()
    assert "implementation" in output.lower()
    assert "\x1b" not in output


def test_existing_implemented_and_explicitly_open_staging_is_supported(
    capsys: pytest.CaptureFixture[str],
) -> None:
    staging = json.loads(_staging(implemented=["S83.201"]))
    staging["excluded_open_tasks"] = ["S83.201"]
    rc, result = _report(capsys, CompletedGit(staging=json.dumps(staging)))
    assert rc == 0
    assert _mapping(result, "staging")["status"] == "available"
    assert _rows(result, "findings")[0]["staged_for_v012"] == "implemented-open"
    assert _mapping(result, "staging")["excluded_open_ids"] == ["S83.201"]


def test_markdown_links_are_not_malformed_task_markers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc, result = _report(capsys, CompletedGit(
        source="- [feature docs](docs/FEATURE.md)\n- [x] S83.201 Branch claim\n",
    ))
    assert rc == 0
    assert _rows(result, "findings")[0]["task_id"] == "S83.201"


@pytest.mark.parametrize("bound,error", [
    ("REPORT_JSON_CHAR_LIMIT", "output exceeded bound"),
    ("CLAIM_SCAN_LIMIT", "claim scan bound exceeded"),
])
def test_complete_report_refuses_output_or_claim_scan_overflow(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
    bound: str, error: str,
) -> None:
    monkeypatch.setattr(f"scripts.branch_reconciliation_completed.{bound}", 0)
    rc, result = _report(capsys, CompletedGit())
    assert rc == 2
    assert error in str(result["error"])
    assert result["gate_evidence"] is False
    assert result["release_ready"] is False


def test_task_scan_bound_is_unknown_instead_of_a_partial_count(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.branch_reconciliation_completed.TASK_SCAN_LIMIT", 1)
    rc, result = _report(capsys, CompletedGit(
        source="- [x] S83.201 First change\n- [x] S83.202 Second change\n",
    ))
    assert rc == 2
    assert _mapping(_rows(result, "current_heads")[0], "ledger")["status"] == "truncated"
    assert _mapping(result, "counts")["branch_only_completed_ids"] is None


@pytest.mark.parametrize("operation", ["tree", "size", "content"])
def test_failed_object_reads_leave_completion_counts_unknown(
    capsys: pytest.CaptureFixture[str], operation: str,
) -> None:
    class FailedObjectGit(CompletedGit):
        def __call__(
            self, argv: Sequence[str], cwd: str | None = None,
        ) -> subprocess.CompletedProcess[str]:
            fail = (
                (operation == "tree" and argv[1] == "ls-tree")
                or (operation == "size" and list(argv[1:3]) == ["cat-file", "-s"])
                or (operation == "content" and list(argv[1:3]) == ["cat-file", "blob"])
            )
            if fail:
                self.calls.append(list(argv))
                return self._result(argv, 128, stderr="object unavailable")
            return super().__call__(argv, cwd)

    rc, result = _report(capsys, FailedObjectGit())
    assert rc == 2
    assert _mapping(_rows(result, "current_heads")[0], "ledger")["status"] == "unavailable"
    assert _mapping(result, "counts")["branch_only_completed_ids"] is None


def test_baseline_moved_during_object_reads_refuses_the_entire_report(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class MovedBaselineGit(CompletedGit):
        def __call__(
            self, argv: Sequence[str], cwd: str | None = None,
        ) -> subprocess.CompletedProcess[str]:
            if argv[-1] == "refs/tags/v0.1.1^{commit}" and self.blobs:
                self.calls.append(list(argv))
                return self._result(argv, 0, f"{SECOND}\n")
            return super().__call__(argv, cwd)

    rc, result = _report(capsys, MovedBaselineGit())
    assert rc == 2
    assert "baseline changed" in str(result["error"])
    assert result["ok"] is False


@pytest.mark.parametrize("owner", ["target", "baseline"])
def test_unknown_comparison_retains_valid_source_ids_titles_and_sha(
    capsys: pytest.CaptureFixture[str], owner: str,
) -> None:
    fake = CompletedGit()
    fake.files[TARGET if owner == "target" else BASELINE, "TASKS.md"] = None
    rc, result = _report(capsys, fake)
    assert rc == 2
    findings = [
        row for row in _rows(result, "findings")
        if row["kind"] == "completion-claim-with-unknown-comparison"
    ]
    assert len(findings) == 1
    assert findings[0]["task_id"] == "S83.201"
    assert findings[0]["title"] == "Branch claim"
    assert _rows(findings[0], "source_claims")[0]["head"] == SOURCE
    assert findings[0]["completed_in_target" if owner == "target" else "completed_in_baseline"] is None
    assert _mapping(result, "counts")["branch_only_completed_ids"] is None


def test_known_target_match_survives_unknown_baseline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc, result = _report(capsys, CompletedGit(
        target="- [x] S83.201 Integrated title\n", baseline=None,
    ))
    assert rc == 2
    assert _rows(result, "current_heads")[0]["completed_in_target_count"] == 1
    assert not any(
        row["kind"] in {"branch-only-completion-claim", "completion-claim-with-unknown-comparison"}
        for row in _rows(result, "findings")
    )


_FACADE_VARIABLES = {
    "RECONCILE_TARGET": "development",
    "RECONCILE_LIMIT": "100",
    "RECONCILE_BASELINE": "v0.1.1",
    "RECONCILE_STAGING_PATH": STAGING_PATH,
    "RECONCILE_REPORT_LIMIT": "1000",
    "RECONCILE_QUIET_PROGRESS": "0",
    "RECONCILE_HUMAN": "0",
}
_FACADE_TARGET = "branch-reconciliation-completed-backlog"


def _run_completed_facade(
    tmp_path: Path, variables: dict[str, str], *, full_makefile: bool = False,
    file_defaults: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Exercise the actual composed recipe or full Makefile with fake UV."""
    root = Path(__file__).resolve().parents[2]
    source = compose_makefile(root / "Makefile")
    marker = f"\n{_FACADE_TARGET}:\n"
    assert marker in source, "Missing completed-backlog Make facade"
    recipe = source.split(marker, 1)[1].split("\n\n", 1)[0]
    recorder = tmp_path / "bounded uv recorder.py"
    recorder.write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "if len(sys.argv) > 32: raise SystemExit(91)\n"
        "Path('facade-argv.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
        "print('finite facade route; diagnostic_only=true; gate_evidence=false')\n",
        encoding="utf-8",
    )
    selected_uv = f"{shlex.quote(sys.executable)} {shlex.quote(str(recorder))}"
    make_source = f"UV := {selected_uv}\n{_FACADE_TARGET}:\n{recipe}\n"
    if full_makefile:
        make_source = source + "".join(
            f"\n{key} := {value}\n" for key, value in (file_defaults or {}).items()
        )
        (tmp_path / "pyproject.toml").write_bytes((root / "pyproject.toml").read_bytes())
    (tmp_path / "Makefile").write_text(make_source, encoding="utf-8")
    selected_make = shutil.which("make")
    assert selected_make is not None
    environment = os.environ.copy()
    for key in (*_FACADE_VARIABLES, "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES"):
        environment.pop(key, None)
    return subprocess.run(
        [selected_make, "--no-print-directory", f"MAKE={selected_make}", _FACADE_TARGET,
         *(f"{key}={value}" for key, value in variables.items()), f"UV={selected_uv}"],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
        check=False, timeout=10,
    )


def _facade_argv(tmp_path: Path) -> list[str]:
    value: object = json.loads((tmp_path / "facade-argv.json").read_text(encoding="utf-8"))
    assert isinstance(value, list) and all(isinstance(part, str) for part in value)
    return cast(list[str], value)


@pytest.mark.parametrize(("quiet", "human"), [("0", "0"), ("0", "1"), ("1", "0"), ("1", "1")])
def test_make_facade_routes_exact_native_argv_and_boolean_toggles(
    tmp_path: Path, quiet: str, human: str,
) -> None:
    variables = {
        **_FACADE_VARIABLES, "RECONCILE_QUIET_PROGRESS": quiet, "RECONCILE_HUMAN": human,
    }
    result = _run_completed_facade(tmp_path, variables)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = [
        "run", "python", "scripts/branch_reconciliation_inventory.py",
        "--target", "development", "--limit", "100", "--after", "",
        "--completed-backlog", "--baseline", "v0.1.1",
        "--staging-path", STAGING_PATH, "--report-limit", "1000",
    ]
    if quiet == "1":
        expected.append("--quiet-progress")
    if human == "1":
        expected.append("--human")
    assert _facade_argv(tmp_path) == expected
    assert not (tmp_path / ".gate-status").exists()
    assert not (tmp_path / "attestation.json").exists()


@pytest.mark.parametrize("variable", tuple(_FACADE_VARIABLES))
def test_make_facade_requires_every_documented_variable(
    tmp_path: Path, variable: str,
) -> None:
    variables = {key: value for key, value in _FACADE_VARIABLES.items() if key != variable}
    result = _run_completed_facade(tmp_path, variables)
    assert result.returncode != 0
    assert variable in result.stdout + result.stderr
    assert not (tmp_path / "facade-argv.json").exists()


@pytest.mark.parametrize("variable", tuple(_FACADE_VARIABLES))
def test_make_facade_refuses_empty_required_values_before_uv(
    tmp_path: Path, variable: str,
) -> None:
    result = _run_completed_facade(tmp_path, {**_FACADE_VARIABLES, variable: ""})
    assert result.returncode != 0
    assert variable in result.stdout + result.stderr
    assert not (tmp_path / "facade-argv.json").exists()


@pytest.mark.parametrize("variable", ["RECONCILE_QUIET_PROGRESS", "RECONCILE_HUMAN"])
@pytest.mark.parametrize("value", ["yes", "2", "1 0"])
def test_make_facade_refuses_noncanonical_booleans_before_uv(
    tmp_path: Path, variable: str, value: str,
) -> None:
    result = _run_completed_facade(tmp_path, {**_FACADE_VARIABLES, variable: value})
    assert result.returncode != 0
    assert variable in result.stdout + result.stderr
    assert not (tmp_path / "facade-argv.json").exists()


def test_make_facade_preserves_quotes_spaces_and_backslashes_in_argv(
    tmp_path: Path,
) -> None:
    # This proves routing only. Native report validation still owns ref syntax.
    target = "feature/with 'single' and \"double\" quotes"
    baseline = "tag/with 'single' and \"double\" quotes"
    path = "config/completed 'single' \"double\" \\ notes.json"
    result = _run_completed_facade(tmp_path, {
        **_FACADE_VARIABLES, "RECONCILE_TARGET": target,
        "RECONCILE_BASELINE": baseline, "RECONCILE_STAGING_PATH": path,
    })
    assert result.returncode == 0, result.stdout + result.stderr
    arguments = _facade_argv(tmp_path)
    assert arguments[arguments.index("--target") + 1] == target
    assert arguments[arguments.index("--baseline") + 1] == baseline
    assert arguments[arguments.index("--staging-path") + 1] == path


_LITERAL_SYNTAXES = ("make-function", "shell-substitution", "escaped-dollar", "backticks", "dollar")
_LITERAL_ARGUMENTS = (
    ("RECONCILE_TARGET", "--target"),
    ("RECONCILE_LIMIT", "--limit"),
    ("RECONCILE_BASELINE", "--baseline"),
    ("RECONCILE_STAGING_PATH", "--staging-path"),
    ("RECONCILE_REPORT_LIMIT", "--report-limit"),
)


def _literal_carrier(syntax: str, tripwire: Path) -> str:
    values = {
        "make-function": f"$(shell touch {tripwire})",
        "shell-substitution": f"$(touch {tripwire})",
        "escaped-dollar": f"$$(touch {tripwire})",
        "backticks": f"`touch {tripwire}`",
        "dollar": "$HOME $$PATH $@ ${NOT_A_VAR}",
    }
    return values[syntax]


@pytest.mark.parametrize(("variable", "flag"), _LITERAL_ARGUMENTS)
@pytest.mark.parametrize("syntax", _LITERAL_SYNTAXES)
def test_full_make_facade_preserves_literal_carriers_without_evaluation(
    tmp_path: Path, variable: str, flag: str, syntax: str,
) -> None:
    # Fake UV proves literal routing; native report semantics remain separate.
    tripwire = tmp_path / "literal-carrier-executed"
    literal = _literal_carrier(syntax, tripwire)
    result = _run_completed_facade(
        tmp_path, {**_FACADE_VARIABLES, variable: literal}, full_makefile=True,
    )
    assert not tripwire.exists(), result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
    arguments = _facade_argv(tmp_path)
    assert arguments[arguments.index(flag) + 1] == literal


@pytest.mark.parametrize("variable", ["RECONCILE_QUIET_PROGRESS", "RECONCILE_HUMAN"])
@pytest.mark.parametrize("syntax", _LITERAL_SYNTAXES)
def test_full_make_facade_rejects_literal_boolean_carriers_without_evaluation(
    tmp_path: Path, variable: str, syntax: str,
) -> None:
    tripwire = tmp_path / "literal-boolean-executed"
    result = _run_completed_facade(
        tmp_path, {**_FACADE_VARIABLES, variable: _literal_carrier(syntax, tripwire)},
        full_makefile=True,
    )
    assert not tripwire.exists(), result.stdout + result.stderr
    assert result.returncode != 0
    assert variable in result.stdout + result.stderr
    assert not (tmp_path / "facade-argv.json").exists()


@pytest.mark.parametrize("variable", tuple(_FACADE_VARIABLES))
def test_full_make_facade_does_not_use_foundation_defaults_for_required_inputs(
    tmp_path: Path, variable: str,
) -> None:
    variables = {key: value for key, value in _FACADE_VARIABLES.items() if key != variable}
    result = _run_completed_facade(tmp_path, variables, full_makefile=True)
    assert result.returncode != 0
    assert variable in result.stdout + result.stderr
    assert not (tmp_path / "facade-argv.json").exists()


@pytest.mark.parametrize("variable", tuple(_FACADE_VARIABLES))
def test_full_make_facade_rejects_file_origin_for_every_required_input(
    tmp_path: Path, variable: str,
) -> None:
    variables = {key: value for key, value in _FACADE_VARIABLES.items() if key != variable}
    result = _run_completed_facade(
        tmp_path, variables, full_makefile=True,
        file_defaults={variable: _FACADE_VARIABLES[variable]},
    )
    assert result.returncode != 0
    assert variable in result.stdout + result.stderr
    assert not (tmp_path / "facade-argv.json").exists()


def test_full_make_facade_accepts_explicit_command_line_inputs(
    tmp_path: Path,
) -> None:
    result = _run_completed_facade(tmp_path, _FACADE_VARIABLES, full_makefile=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _facade_argv(tmp_path)[1:3] == [
        "python", "scripts/branch_reconciliation_inventory.py",
    ]


_LEGACY_DUPLICATES = (
    "- [x] S76.1a Historical alias one | status: completed\n"
    "- [x] S76.1b Historical alias two | status: completed\n"
)


def test_partial_ledger_preserves_valid_source_and_suppresses_valid_target_history(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc, result = _report(capsys, CompletedGit(
        source=(
            _LEGACY_DUPLICATES
            + "- [x] S83.201 Branch completion | status: completed\n"
            + "- [x] S83.202 Already integrated | status: completed\n"
            + "- [x] S83.1 Baseline history | status: completed\n"
            + "- [x] S83.203 Contradictory status | status: in_progress\n"
        ),
        target=_LEGACY_DUPLICATES + "- [x] S83.202 Already integrated\n",
        baseline=_LEGACY_DUPLICATES + "- [x] S83.1 Baseline history\n",
    ))
    assert rc == 2
    assert result["evidence_complete"] is False
    assert result["gate_evidence"] is False and result["release_ready"] is False
    claims = [row for row in _rows(result, "findings") if "task_id" in row]
    assert [row["task_id"] for row in claims] == ["S83.201"]
    finding = claims[0]
    assert finding["kind"] == "completion-claim-with-unknown-comparison"
    assert finding["title"] == "Branch completion"
    assert finding["completed_in_target"] is None
    assert finding["completed_in_baseline"] is None
    source = _rows(finding, "source_claims")[0]
    assert source["head"] == SOURCE
    assert source["refs"] == ["refs/heads/feature/current"]
    assert source["implementation_integration"] == "unknown"
    evidence = _mapping(source, "ledger")
    assert evidence["status"] == "malformed"
    assert evidence["task_count"] is None and evidence["completed_count"] is None
    assert "duplicate task ID: S76" in cast(list[str], evidence["issues"])
    assert any("S83.203" in issue for issue in cast(list[str], evidence["issues"]))
    assert _mapping(result, "counts")["branch_only_completed_ids"] is None
    assert _mapping(result, "completed_in_target")["count"] is None
    assert _mapping(result, "baseline_history")["count"] is None


@pytest.mark.parametrize("invalid_rows,excluded_id", [
    (
        "- [x] S83.201 Duplicate checked\n- [ ] S83.201 Duplicate pending\n",
        "S83.201",
    ),
    ("- [x] S83.201 Contradictory | status: in_progress\n", "S83.201"),
    ("- [x] Missing canonical identity\n", ""),
    (f"- [x] S{'1' * 129} Overlong identity\n", f"S{'1' * 129}"),
    (
        "- [X] S83.201 Noncanonical claim\n- [x] S83.201 Canonical sibling\n",
        "S83.201",
    ),
])
def test_partial_ledger_excludes_affected_ids_without_erasing_unrelated_claims(
    capsys: pytest.CaptureFixture[str], invalid_rows: str, excluded_id: str,
) -> None:
    rc, result = _report(capsys, CompletedGit(
        source=invalid_rows + "- [x] S83.202 Unrelated valid completion | status: completed\n",
    ))
    assert rc == 2
    claims = [row for row in _rows(result, "findings") if "task_id" in row]
    assert [row["task_id"] for row in claims] == ["S83.202"]
    assert claims[0]["kind"] == "completion-claim-with-unknown-comparison"
    assert all(row["task_id"] != excluded_id for row in claims)
    source = _rows(claims[0], "source_claims")[0]
    assert source["title"] == "Unrelated valid completion"
    assert _mapping(source, "ledger")["status"] == "malformed"
    assert _mapping(source, "ledger")["completed_count"] is None
    assert _mapping(result, "counts")["branch_only_completed_ids"] is None
