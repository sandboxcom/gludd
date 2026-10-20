"""Keep HumanTodo requests on the existing Ansible module and literal Make inputs."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("native_status", [0, 7])
@pytest.mark.parametrize("environment_overrides", [False, True])
@pytest.mark.parametrize("policy", [None, "0", "1", "0 1", "$(shell touch TRIPWIRE)"])
def test_human_todo_target_delegates_with_literal_policy_and_owned_temps(
    tmp_path: Path, environment_overrides: bool, policy: str | None, native_status: int,
) -> None:
    recorder = tmp_path / "capture.py"
    recorder.write_text(
        "import json,os,sys\n"
        "if sys.argv[1:3] == ['run','python']:\n"
        ' print(json.dumps({"ansible_python_interpreter":sys.executable}))\n'
        "else:\n"
        " print(json.dumps({'argv':sys.argv[1:],'home':os.environ['ANSIBLE_HOME'],"
        "'local':os.environ['ANSIBLE_LOCAL_TEMP'],'remote':os.environ['ANSIBLE_REMOTE_TEMP']}))\n"
        " sys.exit(int(os.environ.get('FIXTURE_NATIVE_STATUS','0')))\n",
        encoding="utf-8",
    )
    probe = tmp_path / "probe.mk"
    probe.write_text(f"include {ROOT / 'make/95-collections-and-pipelines.mk'}\n", encoding="utf-8")
    payload = json.dumps({
        "title": "Literal 'request' `touch TRIPWIRE` $(shell touch TRIPWIRE)",
        "body": "keep every line\nincluding Unicode caf\u00e9",
    }, ensure_ascii=False)
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL"}
    }
    environment.update(
        GLUDD_HUMAN_TODO_ARGS_JSON="ambient wrong", GLUDD_HUMAN_TODO_VALIDATE_ONLY="opposite",
        FIXTURE_NATIVE_STATUS=str(native_status),
    )
    result = subprocess.run(
        ["make", "--no-print-directory", *(["-e"] if environment_overrides else []),
         "-f", str(probe), "human-todo-module", "HUMAN_TODO_ARGS_JSON=" + payload,
         *(["HUMAN_TODO_VALIDATE_ONLY=" + policy] if policy is not None else []),
         "UV=" + shlex.quote(sys.executable) + " " + shlex.quote(str(recorder))],
        cwd=tmp_path, env=environment, text=True, capture_output=True, check=False, timeout=15,
    )
    assert not (tmp_path / "TRIPWIRE").exists()
    if policy not in {None, "0", "1"}:
        assert result.returncode != 0
        assert not result.stdout
        return
    assert result.returncode == (2 if native_status else 0), result.stdout + result.stderr
    if native_status:
        assert "Error 7" in result.stderr
    captured = json.loads(result.stdout.splitlines()[-1])
    expected = [
        "run", "ansible", "localhost", "--inventory", "localhost,", "--connection", "local",
        "--forks", "1", "--module-name", "general_ludd.agent.gludd_human_todo", "--args", payload,
        "--extra-vars", json.dumps({"ansible_python_interpreter": sys.executable}),
    ]
    if policy != "0":
        expected.append("--check")
    assert captured["argv"] == expected
    temporary = Path(captured["home"])
    assert temporary.name.startswith("gludd-human-todo-") and temporary.parent == Path("/tmp")
    assert captured["local"] == str(temporary / "local")
    assert captured["remote"] == str(temporary / "remote")
    assert not temporary.exists()
