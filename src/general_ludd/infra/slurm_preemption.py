"""Slurm preemption handling: auto-resubmit on PREEMPTED state with backoff."""

from __future__ import annotations

import logging
import time
from typing import Any

from general_ludd.infra.slurm import (
    SlurmAdapter,
    SlurmJobInfo,
    SlurmJobState,
)

logger = logging.getLogger(__name__)

_BACKOFF_SCHEDULE = [30, 60, 120]


class SlurmPreemptionError(Exception):
    """Raised when a preempted job cannot be re-submitted (max resubmits reached)."""


class SlurmPreemptionHandler:
    def __init__(self, adapter: SlurmAdapter | None = None) -> None:
        self._adapter = adapter if adapter is not None else SlurmAdapter()
        self._preemption_counts: dict[str, int] = {}

    def handle_preempted(
        self,
        job_info: SlurmJobInfo,
        *,
        max_resubmits: int = 3,
        submit_params: dict[str, Any] | None = None,
    ) -> SlurmJobInfo:
        original_id = job_info.job_id
        count = self._preemption_counts.get(original_id, 0)
        new_count = count + 1
        self._preemption_counts[original_id] = new_count

        logger.warning(
            "slurm job %s was preempted (resubmit attempt %d/%d)",
            original_id,
            new_count,
            max_resubmits,
        )

        if new_count > max_resubmits:
            raise SlurmPreemptionError(
                f"slurm job {original_id} exceeded max resubmits "
                f"({new_count}/{max_resubmits}) after preemption"
            )

        backoff = _BACKOFF_SCHEDULE[min(new_count - 1, len(_BACKOFF_SCHEDULE) - 1)]
        logger.info(
            "waiting %ds before resubmitting preempted job %s", backoff, original_id
        )
        time.sleep(backoff)

        return self.resubmit_job(job_info, submit_params=submit_params or {})

    def resubmit_job(
        self,
        original_job: SlurmJobInfo,
        submit_params: dict[str, Any] | None = None,
    ) -> SlurmJobInfo:
        params = submit_params or {}
        params.setdefault("job_name", f"gludd-resubmit-{original_job.job_id}")
        new_job_id = self._adapter.submit(**params)

        logger.info(
            "resubmitted preempted job %s as %s",
            original_job.job_id,
            new_job_id,
        )

        return SlurmJobInfo(
            job_id=new_job_id,
            state=SlurmJobState.PENDING,
            original_job_id=original_job.job_id,
            resubmit_count=original_job.resubmit_count + 1,
        )
