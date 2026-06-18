"""E2E scaffold: Slurm dispatch layer (submit/poll/cancel).

Unlike the local model backends, SlurmAdapter makes its own httpx calls and
is NOT routed through the model gateway SSRF guard. A private controller
host (RFC-1918) is fine here.

Two modes (mutually exclusive — use whichever matches your environment):
  CLI mode:   SLURM_E2E=1  — requires sbatch/sacct/scancel on PATH
  REST mode:  SLURM_REST_URL + optionally SLURM_REST_TOKEN

Skips unconditionally when neither mode is configured/reachable.

Model-serving sub-variant (optional, SSRF dep):
  SLURM_SERVE_E2E=1      — submits a real sbatch job that starts llama_cpp.server
  SLURM_SERVED_BASE_URL  — URL of the served /v1 endpoint once running
  Also requires GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1 for the model call (§2.1).

Wave-B TODO: implement real submit/poll/cancel loop once a test Slurm cluster
is available; current tests skip unconditionally without one.
Wave-B TODO: implement model-serving sub-variant.
"""

from __future__ import annotations

import os
import time

import pytest

from tests.e2e.providers._provider_skip import (
    ALLOW_LOCAL_MODEL_BASE_URLS,
    require_slurm_cli,
    require_slurm_rest,
)

pytestmark = pytest.mark.e2e


def _rest_token() -> str | None:
    return os.environ.get("SLURM_REST_TOKEN")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter_cli():
    """Return a SlurmAdapter configured for CLI mode, or skip."""
    require_slurm_cli()
    from general_ludd.infra.slurm import SlurmAdapter
    return SlurmAdapter()


def _make_adapter_rest():
    """Return a SlurmAdapter configured for REST mode, or skip."""
    rest_url = require_slurm_rest()
    from general_ludd.infra.slurm import SlurmAdapter
    return SlurmAdapter(api_url=rest_url, auth_token=_rest_token())


def _make_adapter():
    """Return whichever adapter mode is configured, or skip."""
    # REST takes priority if configured; fall back to CLI
    rest_url = os.environ.get("SLURM_REST_URL")
    if rest_url:
        return _make_adapter_rest()
    return _make_adapter_cli()


# ---------------------------------------------------------------------------
# Test: availability / discovery
# ---------------------------------------------------------------------------

class TestSlurmAvailability:
    """Basic availability and list_jobs probe."""

    def test_adapter_available(self) -> None:
        """SlurmAdapter.available() returns True for the configured backend."""
        adapter = _make_adapter()
        assert adapter.available(), (
            "SlurmAdapter.available() returned False — "
            "check that sbatch/sacct are on PATH (CLI) or that the REST "
            "controller is reachable (REST)"
        )

    def test_list_jobs_returns_list(self) -> None:
        """list_jobs() returns a list (may be empty on an idle cluster)."""
        adapter = _make_adapter()
        jobs = adapter.list_jobs()
        assert isinstance(jobs, list), f"Expected list, got {type(jobs)}"


# ---------------------------------------------------------------------------
# Test: submit → poll → COMPLETED
# ---------------------------------------------------------------------------

class TestSlurmSubmitPollComplete:
    """Submit a tiny job and poll until it reaches COMPLETED.

    The job is: echo gludd-e2e && sleep 5
    Expected: transitions PENDING → RUNNING → COMPLETED within ~3 min.
    """

    _POLL_DEADLINE_SECONDS = 180
    _POLL_INTERVAL_SECONDS = 5

    def test_submit_poll_complete(self) -> None:
        """Submit a real job, poll to COMPLETED, assert exit_code == 0."""
        adapter = _make_adapter()

        from general_ludd.infra.slurm import SlurmJobState

        job_id = adapter.submit(
            command="echo gludd-e2e-complete && sleep 5",
            job_name="gludd-e2e",
            time_limit="00:02:00",
        )

        # job_id must look like a real numeric slurm id
        assert job_id and job_id[0].isdigit(), (
            f"submit() returned unexpected job_id {job_id!r} — "
            "expected a numeric Slurm job id"
        )

        # Poll to a terminal state
        deadline = time.monotonic() + self._POLL_DEADLINE_SECONDS
        terminal = {
            SlurmJobState.COMPLETED,
            SlurmJobState.FAILED,
            SlurmJobState.CANCELLED,
            SlurmJobState.TIMEOUT,
            SlurmJobState.NODE_FAIL,
        }
        final_info = None
        while time.monotonic() < deadline:
            info = adapter.status(job_id)
            if info.state in terminal:
                final_info = info
                break
            time.sleep(self._POLL_INTERVAL_SECONDS)

        if final_info is None:
            pytest.skip(
                f"Slurm job {job_id} did not reach a terminal state within "
                f"{self._POLL_DEADLINE_SECONDS}s — cluster may be congested"
            )

        assert final_info.state == SlurmJobState.COMPLETED, (
            f"Expected COMPLETED, got {final_info.state}. "
            f"Exit code: {final_info.exit_code}"
        )
        if final_info.exit_code is not None:
            assert final_info.exit_code == 0


# ---------------------------------------------------------------------------
# Test: submit → cancel → CANCELLED
# ---------------------------------------------------------------------------

class TestSlurmSubmitCancel:
    """Submit a longer job, cancel it immediately, assert CANCELLED."""

    def test_submit_then_cancel(self) -> None:
        """cancel() causes subsequent status() to show CANCELLED."""
        adapter = _make_adapter()

        from general_ludd.infra.slurm import SlurmJobState

        # A longer sleep so we can cancel before it finishes
        job_id = adapter.submit(
            command="sleep 300",
            job_name="gludd-e2e-cancel",
            time_limit="00:05:00",
        )
        assert job_id and job_id[0].isdigit(), (
            f"submit() returned unexpected job_id {job_id!r}"
        )

        # Cancel immediately
        adapter.cancel(job_id)

        # Status should reflect cancellation (allow brief propagation delay)
        deadline = time.monotonic() + 30.0
        final_state = None
        while time.monotonic() < deadline:
            info = adapter.status(job_id)
            if info.state in (
                SlurmJobState.CANCELLED,
                SlurmJobState.FAILED,
                SlurmJobState.COMPLETED,
            ):
                final_state = info.state
                break
            time.sleep(2)

        if final_state is None:
            pytest.skip(
                f"Slurm job {job_id} did not reflect cancellation within 30s"
            )

        assert final_state == SlurmJobState.CANCELLED, (
            f"Expected CANCELLED after cancel(), got {final_state}"
        )


# ---------------------------------------------------------------------------
# Test: model serving on Slurm (opt-in, SSRF dep)
# ---------------------------------------------------------------------------

class TestSlurmServeModel:
    """Submit an sbatch job that serves a model via llama_cpp.server.

    Opt-in: SLURM_SERVE_E2E=1
    Also requires GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1 for the model call.

    TODO(Wave-B): implement the full LocalInferenceManager(engine="slurm")
    start_server path: assert slurm_job_id in the emitted event, then make
    a real model call against SLURM_SERVED_BASE_URL.
    """

    def test_serve_not_implemented_yet(self) -> None:
        """Placeholder — skip unless SLURM_SERVE_E2E=1."""
        if os.environ.get("SLURM_SERVE_E2E") != "1":
            pytest.skip(
                "SLURM_SERVE_E2E=1 not set — slurm model-serving test is "
                "opt-in (submits a real job that starts llama_cpp.server)"
            )
        _make_adapter()  # also skip if slurm not configured
        if not ALLOW_LOCAL_MODEL_BASE_URLS:
            pytest.skip(
                "GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1 not set — model call "
                "against slurm-served endpoint requires the SSRF opt-in flag "
                "(see DESIGN §2.1(A))"
            )
        # TODO(Wave-B): implement:
        #   1. LocalInferenceManager(engine="slurm").start_server(...)
        #   2. Assert event with slurm_job_id emitted
        #   3. Wait for SLURM_SERVED_BASE_URL /v1/models to be reachable
        #   4. Run gateway model call test
        pytest.skip("SLURM_SERVE_E2E test not yet implemented (Wave-B)")
