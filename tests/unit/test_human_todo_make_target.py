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


def _capture_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str | None, native_status: int,
) -> subprocess.CompletedProcess[str]:
    """Retain every original literal-policy, argv, status and cleanup assertion."""
    native_run = subprocess.run
    results: list[subprocess.CompletedProcess[str]] = []

    def execute(
        arguments: list[str], *, cwd: Path, env: dict[str, str], text: bool,
        capture_output: bool, check: bool, timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        assert text and capture_output and not check and timeout == 15
        result = native_run(
            arguments, cwd=cwd, env=env, text=True, capture_output=True, check=False, timeout=15,
        )
        results.append(result)
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(subprocess, "run", execute)
        test_human_todo_target_delegates_with_literal_policy_and_owned_temps(
            tmp_path, environment_overrides=True, policy=policy, native_status=native_status,
        )
    assert subprocess.run is native_run
    assert len(results) == 1
    return results[0]


@pytest.mark.parametrize("policy", [None, "0", "1"])
@pytest.mark.parametrize("native_status", [0, 7])
def test_human_todo_guidance_uses_actual_native_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str | None, native_status: int,
) -> None:
    result = _capture_original(tmp_path, monkeypatch, policy, native_status)
    guidance = [line for line in result.stderr.splitlines() if line.startswith("AGENT_NEXT ")]
    assert len(guidance) == 1
    prompt = guidance[0]
    assert "target=human-todo-module" in prompt
    assert "gate_evidence=false" in prompt and "release_ready=false" in prompt
    assert "AGENT_NEXT" not in result.stdout
    if native_status:
        assert "outcome=failed" in prompt and "native_status=7" in prompt
        assert "write_outcome=unverified" in prompt
        assert "reconcile" in prompt and "before retry" in prompt
        assert "service/config/auth" in prompt and "changed input" in prompt
        assert "outcome=planned" not in prompt and "outcome=returned" not in prompt
    elif policy != "0":
        assert "outcome=planned" in prompt and "http_request=false" in prompt
        assert "changed=true" in prompt and "id=null" in prompt
        assert "no HumanTodo was filed or resolved" in prompt
    else:
        assert "outcome=returned" in prompt
        assert "returned id" in prompt and "verify requested state" in prompt
        assert "outcome=planned" not in prompt


@pytest.mark.parametrize("policy", ["0 1", "$(shell touch TRIPWIRE)"])
def test_human_todo_policy_refusal_guides_without_a_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str,
) -> None:
    result = _capture_original(tmp_path, monkeypatch, policy, 0)
    assert result.returncode == 2 and not result.stdout
    assert "HUMAN_TODO_VALIDATE_ONLY must be literal 0 or 1" in result.stderr
    assert "AGENT_NEXT target=human-todo-module outcome=refused" in result.stderr
    assert "input=HUMAN_TODO_VALIDATE_ONLY" in result.stderr
    assert "http_request=false" in result.stderr


def test_human_todo_missing_arguments_guides_before_runtime(tmp_path: Path) -> None:
    probe = tmp_path / "probe.mk"
    probe.write_text(f"include {ROOT / 'make/95-collections-and-pipelines.mk'}\n", encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL"}
    }
    result = subprocess.run(
        ["make", "--no-print-directory", "-f", str(probe), "human-todo-module",
         "HUMAN_TODO_ARGS_JSON=", "HUMAN_TODO_VALIDATE_ONLY=1", "UV=false"],
        cwd=tmp_path, env=environment, text=True, capture_output=True, check=False, timeout=15,
    )
    assert result.returncode == 2 and not result.stdout
    assert "HUMAN_TODO_ARGS_JSON must contain the existing module arguments" in result.stderr
    assert "AGENT_NEXT target=human-todo-module outcome=refused" in result.stderr
    assert "input=HUMAN_TODO_ARGS_JSON" in result.stderr
    assert "http_request=false" in result.stderr


@pytest.mark.parametrize("phase", ["owned-temporary-directory", "selected-runtime"])
def test_human_todo_startup_failure_guides_and_preserves_native_status(
    tmp_path: Path, phase: str,
) -> None:
    probe = tmp_path / "probe.mk"
    probe.write_text(f"include {ROOT / 'make/95-collections-and-pipelines.mk'}\n", encoding="utf-8")
    recorder = tmp_path / "refuse.py"
    recorder.write_text(
        "import sys\nprint('native prerequisite refused', file=sys.stderr)\nraise SystemExit(9)\n",
        encoding="utf-8",
    )
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL"}
    }
    if phase == "owned-temporary-directory":
        binary_dir = tmp_path / "bin"
        binary_dir.mkdir()
        refusal = binary_dir / "mktemp"
        refusal.write_text(
            "#!" + sys.executable + "\n" + recorder.read_text(encoding="utf-8"), encoding="utf-8",
        )
        refusal.chmod(0o700)
        environment["PATH"] = str(binary_dir) + os.pathsep + environment.get("PATH", "")
    result = subprocess.run(
        ["make", "--no-print-directory", "-f", str(probe), "human-todo-module",
         'HUMAN_TODO_ARGS_JSON={"title":"fixture","body":"No real request"}',
         "HUMAN_TODO_VALIDATE_ONLY=1",
         "UV=" + shlex.quote(sys.executable) + " " + shlex.quote(str(recorder))],
        cwd=tmp_path, env=environment, text=True, capture_output=True, check=False, timeout=15,
    )
    assert result.returncode == 2 and not result.stdout
    assert "native prerequisite refused" in result.stderr and "Error 9" in result.stderr
    guidance = [line for line in result.stderr.splitlines() if line.startswith("AGENT_NEXT ")]
    assert len(guidance) == 1
    assert "outcome=failed" in guidance[0] and f"phase={phase}" in guidance[0]
    assert "native_status=9" in guidance[0] and "http_request=false" in guidance[0]
    assert "gate_evidence=false" in guidance[0] and "release_ready=false" in guidance[0]
    assert "before retry" in guidance[0]
