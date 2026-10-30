"""Native Ansible credential controls; the loopback response is never filing evidence."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TypedDict, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PSK = "fixture-$-psk-{{ literal }}-\'quoted\'"
WRONG_PSK = "wrong-$-fixture-psk"
FIXTURE_ID = "HTODO-auth-fixture-not-a-filed-request"
BINDING = "HUMAN_TODO_FIXTURE_PSK"
EXPLICIT_LOOKUP = (
    '{{ lookup("ansible.builtin.env", "HUMAN_TODO_FIXTURE_PSK", default=undef())'
    ' | default(undef(), true) }}'
)


class _Request(TypedDict):
    path: str
    authorization: str
    x_psk: str
    payload: dict[str, object]


class _AuthFixture(HTTPServer):
    """Own a loopback-only ephemeral test socket, without a daemon or store."""

    allow_reuse_address = False

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _AuthHandler)
        self.requests: list[_Request] = []


class _AuthHandler(BaseHTTPRequestHandler):
    server: _AuthFixture

    def log_message(self, format: str, *args: object) -> None:
        """Retain requests in the fixture instead of emitting access-log noise."""

    def do_POST(self) -> None:
        """Record the actual module request and return only a marked fixture ID."""
        length = int(self.headers.get("Content-Length", "0"))
        assert 0 < length <= 16384
        payload = cast("dict[str, object]", json.loads(self.rfile.read(length)))
        authorization = self.headers.get("Authorization", "")
        x_psk = self.headers.get("X-PSK", "")
        self.server.requests.append({
            "path": self.path, "authorization": authorization, "x_psk": x_psk, "payload": payload,
        })
        authorized = authorization == "Bearer " + FIXTURE_PSK and x_psk == FIXTURE_PSK
        response: dict[str, object] = (
            {"id": FIXTURE_ID, "status": "pending"} if authorized
            else {"detail": "fixture authorization refused"}
        )
        encoded = json.dumps(response).encode("utf-8")
        self.send_response(201 if authorized else 401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@pytest.fixture
def auth_endpoint() -> Iterator[_AuthFixture]:
    with _AuthFixture() as server:
        thread = threading.Thread(
            target=server.serve_forever, name="gludd-human-todo-auth-fixture", daemon=False,
        )
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=3)
            assert not thread.is_alive()


def _invoke(
    tmp_path: Path, server: _AuthFixture, case: str,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]], str]:
    uv = shutil.which("uv")
    assert uv is not None, "native UV must already be installed; do not download at first use"
    transcript = tmp_path / "native-argv.jsonl"
    recorder = tmp_path / "exec_uv.py"
    recorder.write_text(
        "import json,os,sys\n"
        f"with open({str(transcript)!r}, 'a', encoding='utf-8') as output:\n"
        " output.write(json.dumps({'argv':sys.argv[1:],'home':os.environ.get('ANSIBLE_HOME'),"
        "'local':os.environ.get('ANSIBLE_LOCAL_TEMP'),'remote':os.environ.get('ANSIBLE_REMOTE_TEMP')})+'\\n')\n"
        f"os.execv({uv!r}, [{uv!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    host, port = cast("tuple[str, int]", server.server_address)
    payload: dict[str, object] = {
        "title": "Native credential fixture", "body": "Test fixture only; no daemon store.",
        "agent_id": "gludd-human-todo-auth-fixture", "category": "blocker",
        "priority": "high", "tags": ["auth-fixture"], "timeout": 5,
        "daemon_url": f"http://{host}:{port}",
    }
    if case != "omitted":
        payload["psk"] = EXPLICIT_LOOKUP
    literal_payload = json.dumps(payload)
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", BINDING}
    }
    environment["ANSIBLE_VERBOSITY"] = "4"
    environment["GLUDD_AUTH_PSK"] = FIXTURE_PSK
    if case not in {"missing", "omitted"}:
        environment[BINDING] = "" if case == "empty" else WRONG_PSK if case == "wrong" else FIXTURE_PSK
    result = subprocess.run(
        ["make", "--no-print-directory", "human-todo-module",
         "HUMAN_TODO_ARGS_JSON=" + literal_payload,
         "HUMAN_TODO_VALIDATE_ONLY=" + ("1" if case == "check" else "0"),
         "UV=" + shlex.quote(sys.executable) + " " + shlex.quote(str(recorder)),
         "UV_PROJECT_ENVIRONMENT=" + sys.prefix, "VIRTUAL_ENV=" + sys.prefix,
         "UV_NO_SYNC=1", "UV_PYTHON=" + ".".join(str(part) for part in sys.version_info[:3]),
         "UV_PYTHON_DOWNLOADS=never"],
        cwd=ROOT, env=environment, text=True, capture_output=True, check=False, timeout=30,
    )
    rows = [
        cast("dict[str, object]", json.loads(line))
        for line in transcript.read_text(encoding="utf-8").splitlines()
    ]
    return result, rows, literal_payload


@pytest.mark.parametrize("case", ["named", "missing", "empty", "omitted", "wrong", "check"])
def test_native_explicit_credential_binding_keeps_argv_and_output_secret_free(
    tmp_path: Path, auth_endpoint: _AuthFixture, case: str,
) -> None:
    result, rows, literal_payload = _invoke(tmp_path, auth_endpoint, case)
    evidence = result.stdout + result.stderr
    assert FIXTURE_PSK not in evidence and WRONG_PSK not in evidence
    assert FIXTURE_PSK not in json.dumps(result.args)
    assert FIXTURE_PSK not in json.dumps(rows) and WRONG_PSK not in json.dumps(rows)
    assert len(rows) == 2
    for secret in (FIXTURE_PSK, WRONG_PSK):
        assert all(secret not in argument for argument in cast("list[str]", result.args))
        for row in rows:
            assert all(secret not in argument for argument in cast("list[str]", row["argv"]))
    assert cast("list[str]", rows[0]["argv"])[:2] == ["run", "python"]
    argv = cast("list[str]", rows[-1]["argv"])
    assert argv[:2] == ["run", "ansible"]
    assert argv[argv.index("--args") + 1] == literal_payload
    assert argv[argv.index("--forks") + 1] == "1"
    specification = cast("dict[str, object]", json.loads(argv[argv.index("--extra-vars") + 1]))
    interpreter = specification["ansible_python_interpreter"]
    assert isinstance(interpreter, str) and Path(interpreter).parent.parent == Path(sys.prefix)
    assert ("--check" in argv) is (case == "check")
    temporary = Path(str(rows[-1]["home"]))
    assert temporary.name.startswith("gludd-human-todo-") and temporary.parent == Path("/tmp")
    assert rows[-1]["local"] == str(temporary / "local")
    assert rows[-1]["remote"] == str(temporary / "remote")
    assert not temporary.exists()
    assert "[WARNING]" not in evidence and "[DEPRECATION WARNING]" not in evidence
    if case in {"missing", "empty"}:
        assert result.returncode == 2, evidence
        assert auth_endpoint.requests == []
        assert BINDING in evidence and FIXTURE_ID not in evidence
        assert "outcome=failed" in result.stderr and "outcome=returned" not in result.stderr
        return
    module_commands = [
        line.split(" EXEC ", 1)[1] for line in evidence.splitlines()
        if " EXEC " in line and "AnsiballZ_gludd_human_todo.py" in line
    ]
    assert module_commands, evidence
    executions: list[list[str]] = []
    for command in module_commands:
        shell_arguments = shlex.split(command)
        assert shell_arguments[:2] == ["/bin/sh", "-c"]
        actual_module_argv = shlex.split(shell_arguments[2])
        for secret in (FIXTURE_PSK, WRONG_PSK):
            assert all(secret not in argument for argument in actual_module_argv)
        # Native verbosity also reports chmod staging the same module path.
        if len(actual_module_argv) > 1 and (
            Path(actual_module_argv[1]).name == "AnsiballZ_gludd_human_todo.py"
        ):
            executions.append(actual_module_argv)
    assert len(executions) == 1, evidence
    assert executions[0][0] == interpreter, evidence
    if case == "check":
        assert result.returncode == 0, evidence
        assert auth_endpoint.requests == []
        returned = json.loads(result.stdout.split(" => ", 1)[1])
        assert returned["changed"] is True and returned["id"] is None
        assert "outcome=planned" in result.stderr and "http_request=false" in result.stderr
        return
    assert len(auth_endpoint.requests) == 1
    request = auth_endpoint.requests[0]
    assert request["path"] == "/api/human-todos"
    assert request["payload"] == {
        "title": "Native credential fixture", "body": "Test fixture only; no daemon store.",
        "agent_id": "gludd-human-todo-auth-fixture", "category": "blocker",
        "priority": "high", "tags": ["auth-fixture"],
    }
    expected_psk = "" if case == "omitted" else WRONG_PSK if case == "wrong" else FIXTURE_PSK
    assert request["authorization"] == ("Bearer " + expected_psk if expected_psk else "")
    assert request["x_psk"] == expected_psk
    returned = json.loads(result.stdout.split(" => ", 1)[1])
    if case in {"omitted", "wrong"}:
        assert result.returncode == 2, evidence
        assert returned["status"] == 401 and FIXTURE_ID not in evidence
        assert "outcome=failed" in result.stderr and "outcome=returned" not in result.stderr
    else:
        assert result.returncode == 0, evidence
        assert returned["id"] == FIXTURE_ID and returned["human_todo"]["status"] == "pending"
        assert "outcome=returned" in result.stderr
