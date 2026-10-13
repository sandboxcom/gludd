"""Exercise binary-smoke process identity through Ansible's real template API."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
import yaml
from ansible.errors import AnsibleError
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

START_TICKS = "847263"
EXECUTABLE = "/usr/local/bin/gludd"
COMM_VALUES = ("gludd", "worker pool", "work) (pool)", "work\npool", "work\n) pool")


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict) and all(isinstance(key, str) for key in value)
    return cast(dict[str, object], value)


@pytest.fixture(scope="module", autouse=True)
def ansible_process_state() -> Iterator[None]:
    from general_ludd.ansible.core_runner import _isolated_ansible_process_state

    with _isolated_ansible_process_state(None):
        yield


@pytest.fixture(scope="module")
def scenario_tasks(pytestconfig: pytest.Config) -> dict[str, dict[str, object]]:
    root = pytestconfig.rootpath
    relative = Path("molecule/playbooks/binary_smoke_linux/default")
    if not (root / relative).is_dir():
        root = Path.cwd()
    tasks: dict[str, dict[str, object]] = {}
    for filename in ("converge.yml", "stop_daemon.yml"):
        parsed: object = yaml.safe_load((root / relative / filename).read_text())
        assert isinstance(parsed, list)
        selected: object = _mapping(parsed[1])["tasks"] if filename == "converge.yml" else parsed
        assert isinstance(selected, list)
        for raw in selected:
            task = _mapping(raw)
            name = task["name"]
            assert isinstance(name, str)
            tasks[name] = task
    return tasks


def _fact(tasks: dict[str, dict[str, object]], task_name: str, key: str) -> str:
    expression = _mapping(tasks[task_name]["ansible.builtin.set_fact"])[key]
    assert isinstance(expression, str)
    return expression


@pytest.fixture(params=("startup", "teardown"))
def identity_template(
    request: pytest.FixtureRequest, scenario_tasks: dict[str, dict[str, object]]
) -> tuple[str, str]:
    if request.param == "startup":
        return (
            _fact(scenario_tasks, "Pin the packaged daemon process identity", "daemon_process_start_ticks"),
            "daemon_process_stat",
        )
    return (
        _fact(
            scenario_tasks,
            "Decode the current packaged daemon start identity",
            "_binary_daemon_current_start_ticks",
        ),
        "_binary_daemon_stat",
    )


def _stat(comm: str = "gludd", threads: int = 1, ticks: str = START_TICKS) -> str:
    fields = [str(field * 100) for field in range(3, 53)]
    fields[0] = "S"
    fields[20 - 3] = str(threads)
    fields[21 - 3] = "0"
    fields[22 - 3] = ticks
    return f"431 ({comm}) " + " ".join(fields)


def _render(expression: str, variables: dict[str, object]) -> object:
    result: object = Templar(loader=DataLoader(), variables=variables).template(
        trust_as_template(expression)
    )
    return result


def _condition(expression: str, variables: dict[str, object]) -> bool:
    result: object = Templar(loader=DataLoader(), variables=variables).evaluate_conditional(
        trust_as_template(expression)
    )
    assert isinstance(result, bool)
    return result


def _when(task: dict[str, object], variables: dict[str, object]) -> bool:
    conditions = task["when"]
    assert isinstance(conditions, list) and all(isinstance(item, str) for item in conditions)
    return all(_condition(expression, variables) for expression in cast(list[str], conditions))


@pytest.mark.parametrize("threads", (1, 2))
@pytest.mark.parametrize("comm", COMM_VALUES)
def test_actual_identity_templates_read_starttime_not_thread_count(
    identity_template: tuple[str, str], comm: str, threads: int
) -> None:
    expression, variable = identity_template
    assert str(_render(expression, {variable: {"stdout": _stat(comm, threads)}})) == START_TICKS


@pytest.mark.parametrize("threads", (1, 2))
def test_ansible_preprocessing_reproduces_legacy_thread_count_bug(threads: int) -> None:
    legacy = r"{{ (stat.stdout | regex_replace('^.*[)]\\s+', '')).split()[19] }}"
    assert str(_render(legacy, {"stat": {"stdout": _stat(threads=threads)}})) == str(threads)


@pytest.mark.parametrize("source", (None, "", "431 gludd S 1", "431 (gludd) S 1"))
def test_missing_or_truncated_stat_fails_closed(
    identity_template: tuple[str, str], source: str | None
) -> None:
    expression, variable = identity_template
    values = {} if source is None else {"stdout": source}
    with pytest.raises(AnsibleError):
        _render(expression, {variable: values})


def _ownership_variables(tasks: dict[str, dict[str, object]]) -> dict[str, object]:
    startup = _fact(tasks, "Pin the packaged daemon process identity", "daemon_process_start_ticks")
    teardown = _fact(tasks, "Decode the current packaged daemon start identity", "_binary_daemon_current_start_ticks")
    pinned = _render(startup, {"daemon_process_stat": {"stdout": _stat(threads=1)}})
    current_stat = {"rc": 0, "stdout": _stat(threads=2)}
    current = _render(teardown, {"_binary_daemon_stat": current_stat})
    return {
        "daemon_url": "http://127.0.0.1:9001",
        "gludd_bin": EXECUTABLE,
        "binary_daemon_manifest": {"pid": 431, "process_start_ticks": pinned, "executable": EXECUTABLE},
        "_binary_daemon_pid_record": {"pid": 431, "daemon_url": "http://127.0.0.1:9001"},
        "_binary_daemon_pidfile": {"stat": {"exists": True}},
        "_binary_daemon_ps": {"rc": 0},
        "_binary_daemon_final_ps": {"rc": 0, "stdout": "Sl"},
        "_binary_daemon_stat": current_stat,
        "_binary_daemon_current_start_ticks": current,
        "_binary_daemon_executable": {"rc": 0, "stdout": EXECUTABLE},
    }


@pytest.mark.parametrize(
    ("change", "owned", "refusal", "release"),
    (
        ("same", True, True, True),
        ("reused", False, False, False),
        ("foreign_executable", False, False, False),
        ("stat_unavailable", False, False, False),
        ("invalid_starttime", False, False, False),
        ("exited", False, True, True),
        ("zombie", False, True, True),
    ),
)
def test_actual_ownership_refusal_kill_and_release_guards(
    scenario_tasks: dict[str, dict[str, object]],
    change: str,
    owned: bool,
    refusal: bool,
    release: bool,
) -> None:
    variables = _ownership_variables(scenario_tasks)
    if change in {"reused", "invalid_starttime"}:
        ticks = "847264" if change == "reused" else "not-numeric"
        raw_stat = {"rc": 0, "stdout": _stat(threads=2, ticks=ticks)}
        variables["_binary_daemon_stat"] = raw_stat
        expression = _fact(
            scenario_tasks, "Decode the current packaged daemon start identity", "_binary_daemon_current_start_ticks"
        )
        variables["_binary_daemon_current_start_ticks"] = _render(expression, {"_binary_daemon_stat": raw_stat})
    elif change == "foreign_executable":
        variables["_binary_daemon_executable"] = {"rc": 0, "stdout": "/usr/bin/foreign"}
    elif change == "stat_unavailable":
        variables["_binary_daemon_stat"] = {"rc": 1}
    elif change == "exited":
        variables["_binary_daemon_final_ps"] = {"rc": 1, "stdout": ""}
    elif change == "zombie":
        variables["_binary_daemon_final_ps"] = {"rc": 0, "stdout": "Z"}
    facts = _mapping(scenario_tasks["Establish exact packaged daemon ownership"]["ansible.builtin.set_fact"])
    for key, fact_expression in facts.items():
        assert isinstance(fact_expression, str)
        variables[key] = _render(fact_expression, variables)
    assert variables["_binary_daemon_owned"] is owned
    assertions = _mapping(scenario_tasks["Refuse to signal a reused or foreign PID"]["ansible.builtin.assert"])
    conditions = assertions["that"]
    assert isinstance(conditions, list) and len(conditions) == 1 and isinstance(conditions[0], str)
    assert _condition(conditions[0], variables) is refusal
    assert _when(scenario_tasks["Stop only the proven packaged daemon"], variables) is owned
    assert _when(scenario_tasks["Prove the packaged daemon endpoint is closed"], variables) is release
    assert _when(scenario_tasks["Prove the daemon wrapper released its PID record"], variables) is release


def test_existing_pid_equality_guard_rejects_mismatched_record(
    scenario_tasks: dict[str, dict[str, object]],
) -> None:
    variables = _ownership_variables(scenario_tasks)
    variables["_binary_daemon_pid_record"] = {"pid": 999, "daemon_url": variables["daemon_url"]}
    assertions = _mapping(scenario_tasks["Validate the packaged daemon ownership record"]["ansible.builtin.assert"])
    conditions = assertions["that"]
    assert isinstance(conditions, list) and all(isinstance(item, str) for item in conditions)
    assert not all(_condition(expression, variables) for expression in cast(list[str], conditions))
