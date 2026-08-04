"""Messaging primitives: retry queue, dead letter queue, backoff.

Public surface:
  - :class:`RetryQueue` — enqueue, dequeue with exponential backoff,
    dead letter queue on max-retries exceeded.
  - :class:`RetryItem`  — wrapped payload with item_id, attempt, errors.
"""

from __future__ import annotations

from general_ludd.messaging.retry_queue import RetryItem, RetryQueue

__all__ = ["RetryItem", "RetryQueue"]
