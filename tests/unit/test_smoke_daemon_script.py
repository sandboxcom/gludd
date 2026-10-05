from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "smoke_daemon.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("smoke_daemon_script", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _target_recipe(makefile: str, target: str) -> str:
    lines = makefile.splitlines()
    recipe: list[str] = []
    active = False
    for line in lines:
        if line.startswith(target + ":"):
            active = True
            continue
        if active and line and not line.startswith(chr(9)) and not line.startswith(" "):
            break
        if active:
            recipe.append(line)
    return chr(10).join(recipe)


def test_request_json_rejects_empty_body_with_endpoint_context(monkeypatch: Any) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda _req, timeout: _Response(b""))

    try:
        mod._request_json("http://127.0.0.1:1", "/api/status")
    except RuntimeError as exc:
        assert "GET /api/status returned empty body" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_request_json_rejects_non_json_with_endpoint_context(monkeypatch: Any) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda _req, timeout: _Response(b"not json"))

    try:
        mod._request_json("http://127.0.0.1:1", "/api/status")
    except RuntimeError as exc:
        assert "GET /api/status returned non-JSON body" in str(exc)
        assert "not json" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_request_json_returns_decoded_object(monkeypatch: Any) -> None:
    mod = _load_module()

    def fake_urlopen(req: Any, timeout: float) -> _Response:
        return _Response(bytes.fromhex("7b226f6b223a20747275657d"))

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)

    assert mod._request_json("http://127.0.0.1:1", "/api/status") == {"ok": True}


def test_request_json_adds_bearer_auth_when_psk_given(monkeypatch: Any) -> None:
    mod = _load_module()
    seen: dict[str, str | None] = {}

    def fake_urlopen(req: Any, timeout: float) -> _Response:
        seen["auth"] = req.get_header("Authorization")
        return _Response(bytes.fromhex("7b226f6b223a20747275657d"))

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)

    assert mod._request_json("http://127.0.0.1:1", "/api/todos", psk=mod.SMOKE_AUTH_TOKEN) == {"ok": True}
    assert seen["auth"] == f"Bearer {mod.SMOKE_AUTH_TOKEN}"

def test_prepare_config_writes_isolated_sqlite_url() -> None:
    mod = _load_module()
    tmpdir, config_dir = mod._prepare_config()
    try:
        config_text = (config_dir / "general-ludd.yml").read_text(encoding="utf-8")
        assert "sqlite+aiosqlite:///" in config_text
        assert str(Path(tmpdir.name) / "smoke.db") in config_text
    finally:
        tmpdir.cleanup()


def test_start_daemon_launches_gunicorn_and_captures_child_logs(monkeypatch: Any, tmp_path: Path) -> None:
    mod = _load_module()
    seen: dict[str, Any] = {}

    class Proc:
        pid = 12345

    def fake_popen(cmd: list[str], stdout: Any, stderr: Any, env: dict[str, str], start_new_session: bool) -> Proc:
        seen["cmd"] = cmd
        seen["stdout"] = stdout
        seen["stderr"] = stderr
        seen["env"] = env
        seen["start_new_session"] = start_new_session
        return Proc()

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr(mod, "LOG_PATH", tmp_path / "smoke.log")
    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)

    _proc, handle = mod._start_daemon(1234, config_dir=config_dir)
    handle.close()

    assert seen["cmd"][0] == "gunicorn"
    assert "general_ludd.daemon:create_daemon_app()" in seen["cmd"]
    assert "--access-logfile" in seen["cmd"]
    assert seen["stderr"] == mod.subprocess.STDOUT
    assert seen["env"]["GLUDD_CONFIG_DIR"] == str(config_dir)
    assert seen["env"]["GLUDD_AUTH_PSK"] == mod.SMOKE_AUTH_TOKEN
    assert seen["start_new_session"] is True


def test_smoke_make_target_uses_python_runner_instead_of_json_tool_pipeline() -> None:
    recipe = _target_recipe((ROOT / "Makefile").read_text(encoding="utf-8"), "smoke")

    assert "scripts/smoke_daemon.py" in recipe
    assert "json.tool" not in recipe
    assert "curl" not in recipe
