"""System monitor for load average and CPU capacity checks."""

from __future__ import annotations

import os
import time


def get_load_average() -> tuple[float, float, float]:
    """Return the 1, 5, and 15 minute load averages.

    Returns:
        Tuple of (load_1min, load_5min, load_15min) as floats.
        Returns (0.0, 0.0, 0.0) if unavailable.
    """
    try:
        load1, load5, load15 = os.getloadavg()
        return float(load1), float(load5), float(load15)
    except (OSError, AttributeError):
        # getloadavg not available on all platforms (e.g., Windows)
        return 0.0, 0.0, 0.0


def get_cpu_count() -> int:
    """Return the number of CPU cores.

    Returns:
        Number of CPU cores as an int. Returns 1 if unavailable.
    """
    count = os.cpu_count()
    return count if count and count > 0 else 1


def can_start_process(
    threshold_multiplier: float = 2.5,
    max_load_5min: float = 10.0,
) -> bool:
    """Check if system has capacity to start a new process.

    A process can start if BOTH conditions are met:
    1. The 5-minute load average is below threshold_multiplier * cpu_count
    2. The 5-minute load average is below max_load_5min

    Args:
        threshold_multiplier: Maximum load per CPU core (default 2.5)
        max_load_5min: Absolute maximum 5-minute load (default 10.0)

    Returns:
        True if capacity is available, False otherwise.
    """
    _, load5, _ = get_load_average()
    cores = get_cpu_count()

    # Check threshold multiplier
    if load5 > threshold_multiplier * cores:
        return False

    # Check absolute max
    return not load5 > max_load_5min


def wait_for_capacity(
    threshold_multiplier: float = 2.5,
    max_load_5min: float = 10.0,
    check_interval: float = 5.0,
    timeout: float | None = None,
) -> None:
    """Block until system has capacity to start a new process.

    Args:
        threshold_multiplier: Maximum load per CPU core (default 2.5)
        max_load_5min: Absolute maximum 5-minute load (default 10.0)
        check_interval: Seconds between checks (default 5.0)
        timeout: Maximum seconds to wait. None = wait forever.

    Raises:
        TimeoutError: If timeout expires before capacity becomes available.
    """
    start_time = time.time()

    while True:
        if can_start_process(threshold_multiplier, max_load_5min):
            return

        if timeout is not None:
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                raise TimeoutError(
                    f"Timed out after {timeout}s waiting for system capacity. "
                    f"Load5: {get_load_average()[1]:.2f}, "
                    f"threshold: {threshold_multiplier * get_cpu_count():.2f}, "
                    f"max: {max_load_5min}"
                )

        time.sleep(check_interval)
