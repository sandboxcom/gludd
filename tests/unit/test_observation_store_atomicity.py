"""Regression tests for cross-instance ObservationStore atomic writes."""

from __future__ import annotations

import concurrent.futures
import json
import os
import threading
from pathlib import Path

import general_ludd.memory.observation_consolidator as observation_module
from general_ludd.memory.observation_consolidator import Observation, ObservationStore


def _observation(observation_id: str) -> Observation:
    return Observation(
        observation_id=observation_id,
        subject=observation_id,
        statement=f"statement-{observation_id}",
        confidence=0.5,
        created_at=1.0,
        updated_at=1.0,
    )


def test_concurrent_store_instances_use_unique_atomic_temp_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store_path = tmp_path / "observations.json"
    baseline = ObservationStore(str(store_path))
    baseline.put(_observation("baseline"))

    left_store = ObservationStore(str(store_path))
    right_store = ObservationStore(str(store_path))
    replace_barrier = threading.Barrier(2)
    replace_sources: list[str] = []
    source_lock = threading.Lock()
    real_replace = os.replace

    def synchronized_replace(source: str, destination: str) -> None:
        with source_lock:
            replace_sources.append(source)
        replace_barrier.wait(timeout=5)
        real_replace(source, destination)

    monkeypatch.setattr(observation_module.os, "replace", synchronized_replace)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(left_store.put, _observation("left")),
            executor.submit(right_store.put, _observation("right")),
        ]
        for future in futures:
            future.result(timeout=5)

    assert len(replace_sources) == 2
    assert len(set(replace_sources)) == 2
    final_snapshot = json.loads(store_path.read_text())
    assert set(final_snapshot) in (
        {"baseline", "left"},
        {"baseline", "right"},
    )

