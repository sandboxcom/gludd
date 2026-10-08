"""Collection-native, content-free JUnit pipeline triage contracts."""

from __future__ import annotations

import dataclasses
import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from ansible_collections.general_ludd.git_release.plugins.action import pipeline_triage
from ansible_collections.general_ludd.git_release.plugins.action.pipeline_triage import (
    ActionModule,
)
from ansible_collections.general_ludd.git_release.plugins.module_utils import (
    pipeline_triage as triage_runtime,
)
from ansible_collections.general_ludd.git_release.plugins.modules import (
    pipeline_triage as module_stub,
)
from scripts.check_ansible_executable_stubs import scan_collection_tree

ROOT = Path(__file__).resolve().parents[2]
COLLECTION = ROOT / "collections/ansible_collections/general_ludd/git_release"


def _write_report(root: Path, body: str, *, name: str = "junit.xml") -> Path:
    report = root / name
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(body, encoding="utf-8")
    return report


def _suite(cases: str, *, summary: str = "") -> str:
    return f'<testsuite {summary}>{cases}</testsuite>'


def _args(root: Path, report_path: str = "junit.xml") -> dict[str, str]:
    return {"root": str(root), "report_path": report_path}


def test_native_pipeline_triage_extracts_actionable_failure_without_daemon(
    tmp_path: Path,
) -> None:
    _write_report(
        tmp_path,
        """<?xml version="1.0" encoding="utf-8"?>
<testsuite tests="2" failures="1" errors="0" skipped="0">
  <testcase classname="tests.unit.test_widget" name="test_ok" file="tests/unit/test_widget.py" />
  <testcase classname="tests.unit.test_widget" name="test_bad[param-secret]" file="tests/unit/test_widget.py">
    <failure message="token=do-not-return">traceback with credential material</failure>
    <system-out>stdout must never be returned</system-out>
    <system-err>stderr must never be returned</system-err>
  </testcase>
</testsuite>
""",
    )

    result = pipeline_triage.execute_action(_args(tmp_path))

    assert result["changed"] is False
    assert result["triage"]["outcome"] == "failed"
    assert result["triage"]["counts"] == {
        "errors": 0,
        "failures": 1,
        "passed": 1,
        "skipped": 0,
        "tests": 2,
    }
    assert result["triage"]["actionable_failures"] == [
        {
            "identity_sha256": result["triage"]["actionable_failures"][0][
                "identity_sha256"
            ],
            "outcome": "failure",
        }
    ]
    rendered = repr(result)
    assert "do-not-return" not in rendered
    assert "traceback" not in rendered
    assert "stdout" not in rendered
    assert "stderr" not in rendered
    assert "daemon" not in pipeline_triage.__file__


def test_normal_and_check_mode_are_byte_identical_and_read_only(tmp_path: Path) -> None:
    report = _write_report(
        tmp_path,
        _suite(
            '<testcase classname="pkg.Case" name="test_ok" file="tests/test_ok.py" />',
            summary='tests="1" failures="0" errors="0" skipped="0"',
        ),
    )
    before = report.read_bytes()

    normal = pipeline_triage.execute_action(_args(tmp_path), check_mode=False)
    check = pipeline_triage.execute_action(_args(tmp_path), check_mode=True)

    assert normal == check
    assert normal["triage"]["outcome"] == "passed"
    assert normal["triage"]["actionable_failures"] == []
    assert len(normal["triage"]["report"]["sha256"]) == 64
    assert report.read_bytes() == before


def test_namespaced_all_skipped_report_has_stable_content_free_identity(tmp_path: Path) -> None:
    _write_report(
        tmp_path,
        """<j:testsuites xmlns:j="urn:junit" tests="1" failures="0" errors="0" skipped="1">
  <j:testsuite><j:testcase classname="secret.class" name="secret-name"><j:skipped /></j:testcase></j:testsuite>
</j:testsuites>""",
    )

    first = triage_runtime.triage_junit_report(**_args(tmp_path))
    second = triage_runtime.triage_junit_report(**_args(tmp_path))

    assert first == second
    assert first["outcome"] == "skipped"
    assert "secret.class" not in json.dumps(first)
    assert "secret-name" not in json.dumps(first)


@pytest.mark.parametrize(
    ("xml", "message"),
    [
        (
            '<!DOCTYPE testsuite [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            '<testsuite><testcase name="x">&xxe;</testcase></testsuite>',
            "DTD",
        ),
        ('<!ENTITY x "secret"><testsuite><testcase name="x" /></testsuite>', "entity"),
        ("<testsuite><testcase name='x'></testsuite>", "well-formed"),
        ("<coverage />", "root"),
        ("<testsuite />", "no testcases"),
    ],
)
def test_unsafe_malformed_and_empty_xml_fail_closed(
    tmp_path: Path,
    xml: str,
    message: str,
) -> None:
    _write_report(tmp_path, xml)
    with pytest.raises(triage_runtime.PipelineTriageError, match=message):
        triage_runtime.triage_junit_report(**_args(tmp_path))


@pytest.mark.parametrize(
    ("case", "message"),
    [
        (
            '<testcase name="x"><failure /><error /></testcase>',
            "ambiguous terminal",
        ),
        (
            '<testcase name="x" status="success"><failure /></testcase>',
            "ambiguous terminal",
        ),
        (
            '<testcase name="x" status="unknown" />',
            "unsupported outcome",
        ),
        (
            '<testcase name="x" result="failure" />',
            "ambiguous terminal",
        ),
    ],
)
def test_ambiguous_testcase_outcomes_are_rejected(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    _write_report(tmp_path, _suite(case))
    with pytest.raises(triage_runtime.PipelineTriageError, match=message):
        triage_runtime.triage_junit_report(**_args(tmp_path))


@pytest.mark.parametrize(
    "summary",
    [
        'tests="2"',
        'failures="1"',
        'errors="-1"',
        'skipped="1.0"',
        'tests="100001"',
    ],
)
def test_conflicting_or_invalid_suite_summaries_are_rejected(
    tmp_path: Path,
    summary: str,
) -> None:
    _write_report(tmp_path, _suite('<testcase name="ok" />', summary=summary))
    with pytest.raises(triage_runtime.PipelineTriageError, match="summary"):
        triage_runtime.triage_junit_report(**_args(tmp_path))


def test_duplicate_missing_and_oversized_testcase_identity_fail_closed(
    tmp_path: Path,
) -> None:
    duplicate = '<testcase classname="same" name="same" />' * 2
    _write_report(tmp_path, _suite(duplicate))
    with pytest.raises(triage_runtime.PipelineTriageError, match="duplicate"):
        triage_runtime.triage_junit_report(**_args(tmp_path))

    _write_report(tmp_path, _suite("<testcase />"))
    with pytest.raises(triage_runtime.PipelineTriageError, match="identity"):
        triage_runtime.triage_junit_report(**_args(tmp_path))

    _write_report(tmp_path, _suite(f'<testcase name="x" classname="{"c" * 4097}" />'))
    with pytest.raises(triage_runtime.PipelineTriageError, match="identity"):
        triage_runtime.triage_junit_report(**_args(tmp_path))

    _write_report(tmp_path, _suite(f'<testcase name="{"n" * 65537}" />'))
    with pytest.raises(triage_runtime.PipelineTriageError, match="name exceeds"):
        triage_runtime.triage_junit_report(**_args(tmp_path))


def test_case_failure_and_report_byte_caps_fail_closed(tmp_path: Path) -> None:
    _write_report(
        tmp_path,
        _suite('<testcase name="a" /><testcase name="b" />'),
    )
    with pytest.raises(triage_runtime.PipelineTriageError, match="100000-testcase"):
        triage_runtime.triage_junit_report(
            **_args(tmp_path),
            _limits=triage_runtime.TriageLimits(max_test_cases=1),
        )

    _write_report(
        tmp_path,
        _suite(
            '<testcase name="a"><failure /></testcase>'
            '<testcase name="b"><error /></testcase>'
        ),
    )
    with pytest.raises(triage_runtime.PipelineTriageError, match="64-failure"):
        triage_runtime.triage_junit_report(
            **_args(tmp_path),
            _limits=triage_runtime.TriageLimits(max_failures=1),
        )

    with pytest.raises(triage_runtime.PipelineTriageError, match="16 MiB"):
        triage_runtime.triage_junit_report(
            **_args(tmp_path),
            _limits=triage_runtime.TriageLimits(max_report_bytes=8),
        )

    with pytest.raises(triage_runtime.PipelineTriageError, match="lower hard ceilings"):
        triage_runtime.TriageLimits(max_failures=0).validate()


def test_root_confinement_symlinks_special_files_and_hardlinks_are_rejected(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    report = _write_report(real, _suite('<testcase name="ok" />'))

    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(real, target_is_directory=True)
    with pytest.raises(triage_runtime.PipelineTriageError, match="symlink"):
        triage_runtime.triage_junit_report(**_args(linked_root))

    linked_report = real / "linked.xml"
    linked_report.symlink_to(report)
    with pytest.raises(triage_runtime.PipelineTriageError, match="symlink"):
        triage_runtime.triage_junit_report(**_args(real, "linked.xml"))

    hardlink = real / "hard.xml"
    hardlink.hardlink_to(report)
    with pytest.raises(triage_runtime.PipelineTriageError, match="one link"):
        triage_runtime.triage_junit_report(**_args(real))

    with pytest.raises(triage_runtime.PipelineTriageError, match="relative"):
        triage_runtime.triage_junit_report(**_args(real, str(report)))
    with pytest.raises(triage_runtime.PipelineTriageError, match="parent traversal"):
        triage_runtime.triage_junit_report(**_args(real, "../outside.xml"))
    (real / "directory.xml").mkdir()
    with pytest.raises(triage_runtime.PipelineTriageError, match="regular file"):
        triage_runtime.triage_junit_report(**_args(real, "directory.xml"))


def test_stable_file_identity_is_rechecked_after_the_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_report(tmp_path, _suite('<testcase name="ok" />'))
    real_snapshot = triage_runtime._file_snapshot
    calls = 0

    def unstable(fd: int) -> Any:
        nonlocal calls
        snapshot = real_snapshot(fd)
        calls += 1
        if calls == 2:
            return dataclasses.replace(snapshot, modified_ns=snapshot.modified_ns + 1)
        return snapshot

    monkeypatch.setattr(triage_runtime, "_file_snapshot", unstable)
    with pytest.raises(triage_runtime.PipelineTriageError, match="changed"):
        triage_runtime.triage_junit_report(**_args(tmp_path))


def test_action_contract_and_module_bypass_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_report(tmp_path, _suite('<testcase name="ok" />'))
    with pytest.raises(triage_runtime.PipelineTriageError, match="unsupported argument"):
        pipeline_triage.execute_action({**_args(tmp_path), "daemon_url": "https://bad"})
    with pytest.raises(triage_runtime.PipelineTriageError, match="requires"):
        pipeline_triage.execute_action({"root": str(tmp_path)})

    called: list[dict[str, object]] = []

    class Module:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["supports_check_mode"] is True
            assert set(kwargs["argument_spec"]) == {"root", "report_path"}

        def fail_json(self, **kwargs: object) -> None:
            called.append(kwargs)

    monkeypatch.setattr(module_stub, "AnsibleModule", Module)
    module_stub.main()
    assert called == [
        {
            "changed": False,
            "msg": "pipeline_triage requires its controller-side action plugin",
        }
    ]


def test_action_run_returns_only_bounded_failure_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_report(tmp_path, "<not-junit />")
    monkeypatch.setattr(pipeline_triage._ActionBase, "run", lambda *_args, **_kwargs: {})
    action = object.__new__(ActionModule)
    action._task = SimpleNamespace(args=_args(tmp_path), check_mode=True)

    result = action.run()

    assert result == {
        "changed": False,
        "failed": True,
        "msg": "JUnit report root must be testsuite or testsuites",
    }


def test_module_entrypoint_remains_executable_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []

    class Module:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def fail_json(self, **kwargs: object) -> None:
            messages.append(str(kwargs["msg"]))

    monkeypatch.setattr(module_stub, "AnsibleModule", Module)
    runpy.run_path(str(Path(module_stub.__file__)), run_name="pipeline_triage_import_only")
    module_stub.main()
    monkeypatch.setattr("ansible.module_utils.basic.AnsibleModule", Module)
    runpy.run_path(str(Path(module_stub.__file__)), run_name="__main__")
    assert messages == [
        "pipeline_triage requires its controller-side action plugin",
        "pipeline_triage requires its controller-side action plugin",
    ]


def test_role_and_unique_molecule_scenario_use_the_native_action() -> None:
    role = COLLECTION / "roles/pipeline_triage"
    defaults = yaml.safe_load((role / "defaults/main.yml").read_text(encoding="utf-8"))
    tasks_text = (role / "tasks/main.yml").read_text(encoding="utf-8")
    tasks = yaml.safe_load(tasks_text)
    scenario = ROOT / "molecule/playbooks/git_release_pipeline_triage"
    converge = (scenario / "default/converge.yml").read_text(encoding="utf-8")
    verify = (scenario / "default/verify.yml").read_text(encoding="utf-8")

    assert defaults["git_release_pipeline_triage_enabled"] is False
    assert defaults["git_release_pipeline_triage_report_path"] == ""
    assert len(tasks) == 3
    assert "general_ludd.git_release.pipeline_triage" in tasks_text
    assert "service_request" not in tasks_text
    assert "ansible.builtin.uri" not in tasks_text
    assert "pipeline_triage" in converge
    assert "terminal_outcome_sha256" in verify
    assert "ignore_errors" not in converge + verify


def test_native_surface_has_no_transport_process_or_raw_failure_channel() -> None:
    files = (
        Path(pipeline_triage.__file__),
        Path(triage_runtime.__file__),
    )
    forbidden = (
        "GluddClient",
        "httpx",
        "requests",
        "socket",
        "subprocess",
        "urlopen",
    )
    for path in files:
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source
    assert "from defusedxml import ElementTree" in Path(triage_runtime.__file__).read_text(
        encoding="utf-8"
    )
    assert scan_collection_tree(COLLECTION) == []
