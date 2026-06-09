"""Security sanitization utilities for path and input validation."""

from __future__ import annotations

import re

_PATH_TRAVERSAL = re.compile(r"(?:\.\./|\.\.\\)")
_ABSOLUTE_PATH = re.compile(r"^/|^[A-Za-z]:\\")
_JOB_ID_PATTERN = re.compile(r"^[A-Z0-9_\-]+$")


def sanitize_path(path: str) -> str | None:
    cleaned = path.strip()
    if not cleaned:
        return None
    if _PATH_TRAVERSAL.search(cleaned):
        return None
    if _ABSOLUTE_PATH.match(cleaned):
        return None
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def sanitize_job_id(job_id: str) -> str | None:
    if not job_id:
        return None
    if "/" in job_id or "\\" in job_id:
        return None
    if _PATH_TRAVERSAL.search(job_id):
        return None
    if not _JOB_ID_PATTERN.match(job_id):
        return None
    return job_id
