"""Live E2E: capability matrix for small local GGUF models.

Proves which gludd workloads a small local model can actually do by serving
the same tiny GGUF the molecule ``local_game_gen`` scenario uses
(``bartowski/Qwen2.5-0.5B-Instruct-GGUF`` / ``Qwen2.5-0.5B-Instruct-Q5_K_M.gguf``)
through ``python -m llama_cpp.server`` and probing four capability surfaces:

1. ``text_completion`` — short completion via ``/v1/completions`` (core).
2. ``chat_completion`` — chat-template completion via ``/v1/chat/completions``
   (extended; documented skip when the server has no chat template).
3. ``json_mode`` — JSON-mode generation with ``response_format`` grammar
   (extended; documented skip when the server rejects the request).
4. ``embeddings`` — embedding vectors via ``/v1/embeddings`` on a server
   started with ``--embedding`` (core).

The four results are collected into a capability matrix dict which is printed
as a summary and asserted: core checks must pass, extended checks must pass or
skip with a recorded reason, and total wall-clock must stay under 8 minutes.

Env-gated: the module skips cleanly unless ``GLUDD_LIVE_MODEL_E2E=1`` so the
default suite stays offline.  Override the model via ``GLUDD_LIVE_MODEL_REPO``
/ ``GLUDD_LIVE_MODEL_FILE``.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import pytest

from general_ludd.small_models.download import ModelDownloader

_MODEL_REPO = os.environ.get("GLUDD_LIVE_MODEL_REPO", "bartowski/Qwen2.5-0.5B-Instruct-GGUF")
_MODEL_FILE = os.environ.get("GLUDD_LIVE_MODEL_FILE", "Qwen2.5-0.5B-Instruct-Q5_K_M.gguf")

_HEALTH_TIMEOUT_SEC = 300.0
_HEALTH_POLL_INTERVAL_SEC = 2.0
_SHUTDOWN_TIMEOUT_SEC = 30.0
_RUNTIME_BUDGET_SEC = 480.0
_JSON_PROMPT = 'Return a JSON object with keys "name" and "ok". Output only JSON, no prose.'

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("GLUDD_LIVE_MODEL_E2E") != "1",
        reason=(
            "Live small-model capability matrix disabled. Set GLUDD_LIVE_MODEL_E2E=1 "
            "to run it (downloads a GGUF and starts llama_cpp.server; requires "
            "network access and the llama-cpp-python[server] extra)."
        ),
    ),
]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _stderr_tail(path: str, limit: int = 4000) -> str:
    try:
        with open(path, "rb") as f:
            return f.read()[-limit:].decode(errors="replace")
    except OSError:
        return "(stderr not captured)"


def _kill_process_group(proc: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10.0)


@pytest.fixture(scope="session")
def live_model_path() -> str:
    """Download the small GGUF once for the whole session."""
    session_dir = tempfile.mkdtemp(prefix="gludd-live-caps-")
    previous_hf_home = os.environ.get("HF_HOME")
    os.environ["HF_HOME"] = str(os.path.join(session_dir, "hf_home"))
    try:
        downloader = ModelDownloader(cache_dir=str(session_dir))
        downloaded = downloader.download_gguf(model_id=_MODEL_REPO, filename=_MODEL_FILE)
        if not os.path.isfile(downloaded.local_path):
            pytest.fail(f"Download produced no file at {downloaded.local_path}")
        if os.path.getsize(downloaded.local_path) == 0:
            pytest.fail(f"Downloaded GGUF is empty: {downloaded.local_path}")
        yield downloaded.local_path
    finally:
        if previous_hf_home is None:
            os.environ.pop("HF_HOME", None)
        else:
            os.environ["HF_HOME"] = previous_hf_home
        shutil.rmtree(session_dir, ignore_errors=True)


def _start_server(
    model_path: str,
    extra_args: list[str],
    stderr_file,
    stderr_path,
) -> tuple[subprocess.Popen, str, str]:
    """Launch llama_cpp.server on a free port and poll /health until ready.

    The caller owns *stderr_file* (context-managed) and keeps it open for the
    server's lifetime so stderr capture survives this function's return.
    """
    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "llama_cpp.server",
            "--model",
            model_path,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            *extra_args,
        ],
        stdout=subprocess.DEVNULL,
        stderr=stderr_file,
        start_new_session=True,
    )

    deadline = time.time() + _HEALTH_TIMEOUT_SEC
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(
                f"llama_cpp.server exited early (rc={proc.returncode}). stderr tail:\n{_stderr_tail(str(stderr_path))}"
            )
        try:
            resp = httpx.get(f"{base_url}/v1/models", timeout=5.0)
            if resp.status_code == 200:
                return proc, base_url, str(stderr_path)
        except httpx.HTTPError:
            pass
        time.sleep(_HEALTH_POLL_INTERVAL_SEC)
    _kill_process_group(proc)
    pytest.fail(
        f"Server /health did not return 200 within {_HEALTH_TIMEOUT_SEC:.0f}s. "
        f"stderr tail:\n{_stderr_tail(str(stderr_path))}"
    )
    raise AssertionError("unreachable")


def _stop_server(proc: subprocess.Popen, stderr_path: str) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    try:
        proc.wait(timeout=_SHUTDOWN_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        pytest.fail(
            f"Server did not exit within {_SHUTDOWN_TIMEOUT_SEC:.0f}s after SIGTERM (killed). "
            f"stderr tail:\n{_stderr_tail(stderr_path)}"
        )
    if proc.returncode != 0:
        pytest.fail(f"Server exited uncleanly (rc={proc.returncode}). stderr tail:\n{_stderr_tail(stderr_path)}")


def _check_text_completion(base_url: str, model_id: str) -> tuple[str, str]:
    resp = httpx.post(
        f"{base_url}/v1/completions",
        json={
            "model": model_id,
            "prompt": "The capital of France is",
            "max_tokens": 8,
            "temperature": 0.0,
            "seed": 42,
        },
        timeout=120.0,
    )
    if resp.status_code != 200:
        return "fail", f"HTTP {resp.status_code}: {resp.text[:300]}"
    body = resp.json()
    choices = body.get("choices", [])
    if not choices:
        return "fail", f"No choices in completion: {body}"
    text = choices[0].get("text", "")
    if not isinstance(text, str) or len(text.strip()) == 0:
        return "fail", f"Empty completion text: {body}"
    return "pass", repr(text.strip()[:80])


def _check_chat_completion(base_url: str, model_id: str) -> tuple[str, str]:
    resp = httpx.post(
        f"{base_url}/v1/chat/completions",
        json={
            "model": model_id,
            "messages": [{"role": "user", "content": "Reply with exactly one word: hello"}],
            "max_tokens": 8,
            "temperature": 0.0,
            "seed": 42,
        },
        timeout=120.0,
    )
    if resp.status_code in (400, 404, 422, 501):
        return "skip", f"chat template unsupported (HTTP {resp.status_code}): {resp.text[:200]}"
    if resp.status_code != 200:
        return "fail", f"HTTP {resp.status_code}: {resp.text[:300]}"
    content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    if not isinstance(content, str) or len(content.strip()) == 0:
        return "fail", f"Empty chat content: {resp.text[:300]}"
    return "pass", repr(content.strip()[:80])


def _check_json_mode(base_url: str, model_id: str) -> tuple[str, str]:
    resp = httpx.post(
        f"{base_url}/v1/completions",
        json={
            "model": model_id,
            "prompt": _JSON_PROMPT,
            "max_tokens": 64,
            "temperature": 0.0,
            "seed": 42,
            "response_format": {"type": "json_object"},
        },
        timeout=120.0,
    )
    if resp.status_code in (400, 422, 501):
        return "skip", f"response_format unsupported (HTTP {resp.status_code}): {resp.text[:200]}"
    if resp.status_code != 200:
        return "fail", f"HTTP {resp.status_code}: {resp.text[:300]}"
    body = resp.json()
    choices = body.get("choices", [])
    if not choices:
        return "fail", f"No choices in completion: {body}"
    text = choices[0].get("text", "")
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        return "fail", f"Unparseable JSON: {exc}; text={text[:200]!r}"
    if not isinstance(parsed, dict):
        return "fail", f"Parsed JSON is not an object: {parsed!r}"
    return "pass", repr(parsed)


def _check_embeddings(base_url: str, model_id: str) -> tuple[str, str]:
    resp = httpx.post(
        f"{base_url}/v1/embeddings",
        json={"model": model_id, "input": "gludd small model embedding probe"},
        timeout=120.0,
    )
    if resp.status_code != 200:
        return "fail", f"HTTP {resp.status_code}: {resp.text[:300]}"
    data = resp.json().get("data", [])
    if not data:
        return "fail", f"No embedding data returned: {resp.text[:300]}"
    vector = data[0].get("embedding")
    if not isinstance(vector, list) or len(vector) == 0:
        return "fail", f"Empty embedding vector: {data[0]}"
    if not all(isinstance(v, (int, float)) for v in vector[:16]):
        return "fail", f"Embedding vector is not numeric: {vector[:16]!r}"
    return "pass", f"dim={len(vector)}"


def _print_matrix(model_path: str, matrix: dict[str, str], details: dict[str, str], elapsed: float) -> None:
    lines = [
        "=== SMALL MODEL CAPABILITY MATRIX ===",
        f"model: {_MODEL_REPO}/{_MODEL_FILE}",
        f"path: {model_path}",
        f"elapsed: {elapsed:.1f}s (budget {_RUNTIME_BUDGET_SEC:.0f}s)",
    ]
    for check in ("text_completion", "chat_completion", "json_mode", "embeddings"):
        lines.append(f"  {check:16s} -> {matrix[check]:10s} {details[check]}")
    print("\n".join(lines))


@pytest.mark.timeout(480)
def test_small_model_capability_matrix(live_model_path: str, tmp_path) -> None:
    """Probe all four capability surfaces and assert the capability matrix."""
    started = time.monotonic()
    matrix: dict[str, str] = {}
    details: dict[str, str] = {}

    gen_stderr_path = tmp_path / "llama-gen-server.stderr"
    with open(gen_stderr_path, "wb") as gen_stderr_file:
        gen_proc, base_url, gen_stderr = _start_server(
            live_model_path, extra_args=[], stderr_file=gen_stderr_file, stderr_path=gen_stderr_path
        )
        try:
            models_resp = httpx.get(f"{base_url}/v1/models", timeout=30.0)
            assert models_resp.status_code == 200, models_resp.text
            models = models_resp.json().get("data", [])
            assert models, f"No models served: {models_resp.text}"
            model_id = models[0].get("id", _MODEL_FILE)

            matrix["text_completion"], details["text_completion"] = _check_text_completion(base_url, model_id)
            matrix["chat_completion"], details["chat_completion"] = _check_chat_completion(base_url, model_id)
            matrix["json_mode"], details["json_mode"] = _check_json_mode(base_url, model_id)
        finally:
            _stop_server(gen_proc, gen_stderr)

    emb_stderr_path = tmp_path / "llama-emb-server.stderr"
    with open(emb_stderr_path, "wb") as emb_stderr_file:
        emb_proc, emb_base_url, emb_stderr = _start_server(
            live_model_path, extra_args=["--embedding"], stderr_file=emb_stderr_file, stderr_path=emb_stderr_path
        )
        try:
            matrix["embeddings"], details["embeddings"] = _check_embeddings(emb_base_url, model_id)
        finally:
            _stop_server(emb_proc, emb_stderr)

    elapsed = time.monotonic() - started
    _print_matrix(live_model_path, matrix, details, elapsed)

    assert set(matrix) == {"text_completion", "chat_completion", "json_mode", "embeddings"}
    assert matrix["text_completion"] == "pass", f"core check failed: {details['text_completion']}"
    assert matrix["embeddings"] == "pass", f"core check failed: {details['embeddings']}"
    for check in ("chat_completion", "json_mode"):
        assert matrix[check] == "pass" or matrix[check].startswith("skip:"), f"extended check failed: {details[check]}"
    assert elapsed < _RUNTIME_BUDGET_SEC, (
        f"Capability probe took {elapsed:.0f}s, exceeding the {_RUNTIME_BUDGET_SEC:.0f}s budget"
    )
