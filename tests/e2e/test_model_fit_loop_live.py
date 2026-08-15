"""Live E2E: model-fit loop — one real generation, recorded outcome, reassessed fit.

Exercises the full model-fit loop end to end:

1. Download the small GGUF and launch ``llama_cpp.server`` (same pattern as
   ``tests/e2e/test_local_model_server_live.py``).
2. Run ONE real generation task ("The capital of France is…") and run the
   acceptance check on its output ("paris" in the completion).
3. Record the accepted/rejected outcome into a real
   :class:`ModelPerformanceRepository` backed by in-memory SQLite, alongside
   a seeded cloud model with equal prior weight.
4. Assert the fit reassessment reflects the recorded outcome: the
   :class:`ModelPerformanceRouter` now selects the live model when accepted
   and the cloud model when rejected, and the live model's ranking row
   carries the new sample.  Also assert per-job-type scoping (the cloud
   model stays preferred for an untouched job type).

Env-gated: skipped unless ``GLUDD_LIVE_MODEL_E2E=1`` so the default suite
stays offline.  Override the model via ``GLUDD_LIVE_MODEL_REPO`` /
``GLUDD_LIVE_MODEL_FILE``.  Runtime bounded by the pytest-timeout marker.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import ModelPerformanceRepository
from general_ludd.models.performance_router import ModelPerformanceRouter
from general_ludd.small_models.download import ModelDownloader

_MODEL_REPO = os.environ.get("GLUDD_LIVE_MODEL_REPO", "bartowski/Qwen2.5-0.5B-Instruct-GGUF")
_MODEL_FILE = os.environ.get("GLUDD_LIVE_MODEL_FILE", "Qwen2.5-0.5B-Instruct-Q5_K_M.gguf")

_TASK_TYPE = "generation"
_OTHER_TASK_TYPE = "bug_fix"
_CLOUD = ("openai", "gpt-4o", "openai/gpt-4o")

_HEALTH_TIMEOUT_SEC = 300.0
_HEALTH_POLL_INTERVAL_SEC = 2.0
_SHUTDOWN_TIMEOUT_SEC = 30.0

pytestmark = pytest.mark.skipif(
    os.environ.get("GLUDD_LIVE_MODEL_E2E") != "1",
    reason=(
        "Live model-fit e2e disabled. Set GLUDD_LIVE_MODEL_E2E=1 to run it "
        "(downloads a GGUF and starts llama_cpp.server; requires network "
        "access and the llama-cpp-python[server] extra)."
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


async def _seed_call(
    repo: ModelPerformanceRepository,
    session: AsyncSession,
    model: tuple[str, str, str],
    task_type: str,
    successes: int,
    failures: int,
) -> None:
    service, model_name, profile_id = model
    for ok in [True] * successes + [False] * failures:
        await repo.record_call(
            service=service,
            model_name=model_name,
            model_profile_id=profile_id,
            task_type=task_type,
            success=ok,
            duration_ms=250.0,
            cost_usd=0.01,
            session=session,
        )


async def _record_outcome_and_reassess(
    accepted: bool,
    elapsed_ms: float,
    live_model_name: str,
) -> dict[str, object]:
    """Seed prior weights, record the real outcome, assert the reassessment."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            repo = ModelPerformanceRepository(session=session)
            router = ModelPerformanceRouter(perf_repo=repo, config={"min_calls": 1})
            live = ("local", live_model_name, live_model_name)

            await _seed_call(repo, session, _CLOUD, _TASK_TYPE, successes=3, failures=2)
            await _seed_call(repo, session, live, _TASK_TYPE, successes=3, failures=2)
            await _seed_call(repo, session, _CLOUD, _OTHER_TASK_TYPE, successes=4, failures=1)
            await session.commit()

            await repo.record_call(
                service="local",
                model_name=live_model_name,
                model_profile_id=live_model_name,
                task_type=_TASK_TYPE,
                success=accepted,
                duration_ms=elapsed_ms,
                cost_usd=0.0,
                session=session,
            )
            await session.commit()

            refreshed = await repo.refresh_recent_stats(session=session)
            assert refreshed >= 2, f"expected >=2 profiles refreshed, got {refreshed}"

            ranking = await router.get_rankings(_TASK_TYPE)
            live_row = next(r for r in ranking if r["model_name"] == live_model_name)
            assert live_row["sample_count"] == 6, f"live model samples: {live_row}"
            assert (live_row["success_rate"] > 3 / 6) is accepted, (
                f"live success_rate must reflect accepted={accepted}: {live_row}"
            )

            choice = await router.select_model(_TASK_TYPE)
            expected = live_model_name if accepted else _CLOUD[1]
            assert choice["model_name"] == expected, (
                f"fit reassessment must reflect the recorded outcome (accepted={accepted}); got {choice}"
            )

            other = await router.select_model(_OTHER_TASK_TYPE)
            assert other["model_name"] == _CLOUD[1], (
                f"per-job-type: {_OTHER_TASK_TYPE!r} untouched by the live model, got {other}"
            )

            perf_rows = await repo.get_stats_by_model(model_profile_id=live_model_name)
            assert len(perf_rows) == 1, f"expected 1 aggregated row for live model: {perf_rows}"
            perf = perf_rows[0]
            assert perf.total_calls == 6
            assert perf.successful_calls == (4 if accepted else 3)

            return {
                "accepted": accepted,
                "selected_model": choice["model_name"],
                "live_success_rate": live_row["success_rate"],
            }
    finally:
        await engine.dispose()


@pytest.mark.timeout(600)
def test_live_generation_outcome_recorded_and_fit_reassessed(tmp_path) -> None:
    """Run one real generation, record its outcome, verify fit reassessment."""
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
                    resp = httpx.get(f"{base_url}/health", timeout=5.0)
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

            started = time.time()
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
            elapsed_ms = (time.time() - started) * 1000.0
            assert completion_resp.status_code == 200, completion_resp.text
            body = completion_resp.json()
            choices = body.get("choices", [])
            assert choices, f"No choices in completion: {body}"
            text = choices[0].get("text", "")
            assert isinstance(text, str) and len(text) > 0, f"Empty completion text: {body}"

            accepted = "paris" in text.lower()

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

        result = asyncio.run(_record_outcome_and_reassess(accepted, elapsed_ms, served_model))
        assert result["selected_model"] == (served_model if accepted else _CLOUD[1])
    finally:
        if proc is not None and proc.poll() is None:
            _kill_process_group(proc)
        if previous_hf_home is None:
            os.environ.pop("HF_HOME", None)
        else:
            os.environ["HF_HOME"] = previous_hf_home
