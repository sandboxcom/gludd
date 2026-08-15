"""Live E2E: local model server pipeline — download → serve → generate.

Exercises the real local-model server flow end to end:

1. ``ModelDownloader.download_gguf`` — fetch the small GGUF used by the
   molecule ``local_game_gen`` flow (``bartowski/Qwen2.5-0.5B-Instruct-GGUF``,
   ``Qwen2.5-0.5B-Instruct-Q5_K_M.gguf``) into the test tmp dir.
2. Launch ``python -m llama_cpp.server`` bound to 127.0.0.1 on a free port.
3. Poll ``/health`` until 200 (300s timeout).
4. POST ``/v1/completions`` with a short prompt; assert ``choices[0].text``.
5. SIGTERM the server; assert a clean exit.

Env-gated: skipped unless ``GLUDD_LIVE_MODEL_E2E=1`` so the default suite
stays offline.  Override the model via ``GLUDD_LIVE_MODEL_REPO`` /
``GLUDD_LIVE_MODEL_FILE``.  Runtime is bounded to < 8 minutes by the
pytest-timeout marker.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest

from general_ludd.small_models.download import ModelDownloader

_MODEL_REPO = os.environ.get("GLUDD_LIVE_MODEL_REPO", "bartowski/Qwen2.5-0.5B-Instruct-GGUF")
_MODEL_FILE = os.environ.get("GLUDD_LIVE_MODEL_FILE", "Qwen2.5-0.5B-Instruct-Q5_K_M.gguf")

_HEALTH_TIMEOUT_SEC = 300.0
_HEALTH_POLL_INTERVAL_SEC = 2.0
_SHUTDOWN_TIMEOUT_SEC = 30.0

pytestmark = pytest.mark.skipif(
    os.environ.get("GLUDD_LIVE_MODEL_E2E") != "1",
    reason=(
        "Live local model server e2e disabled. Set GLUDD_LIVE_MODEL_E2E=1 "
        "to run it (downloads a GGUF and starts llama_cpp.server; requires "
        "network access and the llama-cpp-python[server] extra)."
    ),
)


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


@pytest.mark.timeout(480)
def test_download_serve_generate_shutdown(tmp_path) -> None:
    """Download the small GGUF, serve it, generate text, shut down cleanly."""
    stderr_path = tmp_path / "llama-server.stderr"
    proc: subprocess.Popen | None = None
    previous_hf_home = os.environ.get("HF_HOME")

    os.environ["HF_HOME"] = str(tmp_path / "hf_home")
    try:
        downloader = ModelDownloader(cache_dir=str(tmp_path))
        downloaded = downloader.download_gguf(model_id=_MODEL_REPO, filename=_MODEL_FILE)
        assert os.path.isfile(downloaded.local_path), f"downloaded file missing: {downloaded.local_path}"
        assert os.path.getsize(downloaded.local_path) > 0, "downloaded GGUF is empty"

        port = _find_free_port()
        base_url = f"http://127.0.0.1:{port}"

        with open(stderr_path, "wb") as stderr_file:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "llama_cpp.server",
                    "--model",
                    downloaded.local_path,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                stdout=subprocess.DEVNULL,
                stderr=stderr_file,
                start_new_session=True,
            )

            deadline = time.time() + _HEALTH_TIMEOUT_SEC
            while time.time() < deadline:
                if proc.poll() is not None:
                    pytest.fail(
                        f"llama_cpp.server exited early (rc={proc.returncode}). "
                        f"stderr tail:\n{_stderr_tail(str(stderr_path))}"
                    )
                try:
                    resp = httpx.get(f"{base_url}/v1/models", timeout=5.0)
                    if resp.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(_HEALTH_POLL_INTERVAL_SEC)
            else:
                pytest.fail(
                    f"Server /health did not return 200 within {_HEALTH_TIMEOUT_SEC:.0f}s. "
                    f"stderr tail:\n{_stderr_tail(str(stderr_path))}"
                )

            models_resp = httpx.get(f"{base_url}/v1/models", timeout=30.0)
            assert models_resp.status_code == 200, models_resp.text
            models = models_resp.json().get("data", [])
            assert models, f"No models served: {models_resp.text}"
            served_model = models[0].get("id", _MODEL_FILE)

            completion_resp = httpx.post(
                f"{base_url}/v1/completions",
                json={
                    "model": served_model,
                    "prompt": "The capital of France is",
                    "max_tokens": 8,
                    "temperature": 0.0,
                },
                timeout=120.0,
            )
            assert completion_resp.status_code == 200, completion_resp.text
            body = completion_resp.json()
            choices = body.get("choices", [])
            assert choices, f"No choices in completion: {body}"
            text = choices[0].get("text", "")
            assert isinstance(text, str) and len(text) > 0, f"Empty completion text: {body}"

            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=_SHUTDOWN_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                _kill_process_group(proc)
                pytest.fail(f"Server did not exit within {_SHUTDOWN_TIMEOUT_SEC:.0f}s after SIGTERM (killed).")
            assert proc.returncode == 0, (
                f"Server exited uncleanly (rc={proc.returncode}). stderr tail:\n{_stderr_tail(str(stderr_path))}"
            )
            proc = None
    finally:
        if proc is not None and proc.poll() is None:
            _kill_process_group(proc)
        if previous_hf_home is None:
            os.environ.pop("HF_HOME", None)
        else:
            os.environ["HF_HOME"] = previous_hf_home
