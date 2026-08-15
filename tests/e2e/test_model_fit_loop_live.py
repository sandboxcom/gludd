"""Live E2E: model fit loop — serve → generate → score → record → reassess.

Proves the full loop the orchestrator relies on when local models do real
work: a small model is served, asked to do a real task, its output is
scored as an outcome, the outcome is recorded into the model/job weight DB
(``ModelPerformanceRepository``), and the fit reassessment (``ModelPerformanceRouter``)
reflects that recorded outcome for the next task of the same type.

Env-gated: skipped unless ``GLUDD_LIVE_MODEL_E2E=1`` so the default suite
stays offline.  Reuses the model and server patterns from
``test_local_model_server_live.py``.  Runtime is bounded to < 8 minutes by
the pytest-timeout marker.
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
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import ModelPerformanceRepository
from general_ludd.models.performance_router import ModelPerformanceRouter
from general_ludd.small_models.download import ModelDownloader

_MODEL_REPO = os.environ.get("GLUDD_LIVE_MODEL_REPO", "bartowski/Qwen2.5-0.5B-Instruct-GGUF")
_MODEL_FILE = os.environ.get("GLUDD_LIVE_MODEL_FILE", "Qwen2.5-0.5B-Instruct-Q5_K_M.gguf")

_GOOD_MODEL_PROFILE = "local/qwen2.5-0.5b"
_BAD_MODEL_PROFILE = "local/qwen2.5-0.5b-bad"

_HEALTH_TIMEOUT_SEC = 300.0
_HEALTH_POLL_INTERVAL_SEC = 2.0
_SHUTDOWN_TIMEOUT_SEC = 30.0

pytestmark = pytest.mark.skipif(
    os.environ.get("GLUDD_LIVE_MODEL_E2E") != "1",
    reason=(
        "Live model fit loop e2e disabled. Set GLUDD_LIVE_MODEL_E2E=1 "
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


def _score_completion(text: str) -> tuple[bool, float]:
    """Score a completion as an accept/reject outcome.

    A tiny model's answer to a factual prompt is usable when it is
    non-empty and mentions the expected subject — the same class of
    acceptance signal the game-gen verify steps use (structural checks
    rather than semantic perfection).
    """
    lowered = text.strip().lower()
    if not lowered:
        return False, 0.0
    if "paris" in lowered or "france" in lowered:
        return True, 0.05
    return False, 0.0


@pytest_asyncio.fixture
async def repo_session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _record(
    repo: ModelPerformanceRepository,
    model_profile_id: str,
    *,
    task_type: str,
    success: bool,
    cost_usd: float,
) -> None:
    service, _, model_name = model_profile_id.partition("/")
    await repo.record_call(
        service=service,
        model_name=model_name,
        model_profile_id=model_profile_id,
        task_type=task_type,
        success=success,
        duration_ms=100.0,
        cost_usd=cost_usd,
    )


@pytest.mark.timeout(480)
async def test_live_generate_score_record_reassess(tmp_path, repo_session: AsyncSession) -> None:
    """Serve the small model, score its real output, and prove the weight DB
    reassessment moves the next task's pick toward the better-scored model."""
    repo = ModelPerformanceRepository(session=repo_session)
    router = ModelPerformanceRouter(perf_repo=repo, config={"min_calls": 1})
    task_type = "local_factoid"
    stderr_path = tmp_path / "llama-server.stderr"
    proc: subprocess.Popen | None = None
    previous_hf_home = os.environ.get("HF_HOME")

    os.environ["HF_HOME"] = str(tmp_path / "hf_home")
    try:
        downloaded = ModelDownloader.download_gguf(_MODEL_REPO, _MODEL_FILE, tmp_path / "models")
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
            served_model = models_resp.json().get("data", [{}])[0].get("id", _MODEL_FILE)

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
            text = completion_resp.json().get("choices", [{}])[0].get("text", "")

            accepted, cost = _score_completion(text)
            await _record(repo, _GOOD_MODEL_PROFILE, task_type=task_type, success=accepted, cost_usd=cost)
            await _record(
                repo,
                _BAD_MODEL_PROFILE,
                task_type=task_type,
                success=not accepted,
                cost_usd=0.10,
            )

            picked = await router.select_model(task_type)
            assert picked["model_name"] == ("qwen2.5-0.5b" if accepted else "qwen2.5-0.5b-bad"), (
                f"fit reassessment must prefer the better-scored model, got {picked}"
            )

            ranking = await router.get_rankings(task_type, strategy="quality")
            assert ranking, "rankings must be non-empty after outcome recording"
            assert ranking[0]["model_name"] == picked["model_name"]

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
