"""Input validation shared by atomic execution-lease operations."""

from __future__ import annotations

from collections.abc import Mapping


def validate_lease_input(
    bucket_keys: list[str],
    holder_id: str,
    ttl_seconds: int,
    todo_versions: Mapping[str, int] | None,
) -> None:
    """Reject malformed or ambiguous holder, bucket, TTL, and fence values."""
    if not isinstance(holder_id, str) or not holder_id or len(holder_id) > 128:
        raise ValueError("holder_id must be non-empty text no longer than 128 characters")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
        raise ValueError("ttl_seconds must be a positive integer")
    if ttl_seconds <= 0 or ttl_seconds > 86_400:
        raise ValueError("ttl_seconds must be between 1 and 86400")
    if len(bucket_keys) != len(set(bucket_keys)):
        raise ValueError("bucket_keys must not contain duplicates")
    for key in bucket_keys:
        if not isinstance(key, str) or not key or len(key) > 256:
            raise ValueError(
                "each bucket key must be non-empty text no longer than 256 characters"
            )
    if todo_versions is None:
        return
    if set(todo_versions) - set(bucket_keys):
        raise ValueError("todo_versions contains an unknown bucket key")
    for version in todo_versions.values():
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ValueError("todo versions must be positive integers")


__all__ = ("validate_lease_input",)
