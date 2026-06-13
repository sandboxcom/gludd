from __future__ import annotations

import logging
from typing import Any


class ProjectLogAdapter(logging.LoggerAdapter[Any]):
    def __init__(
        self, logger: logging.Logger, project_id: str | None = None
    ) -> None:
        super().__init__(logger, extra={"project_id": project_id})
        self.project_id = project_id

    def process(
        self, msg: str, kwargs: Any
    ) -> tuple[str, Any]:
        if self.project_id:
            return f"[{self.project_id}] {msg}", kwargs
        return msg, kwargs


class ProjectLogFilter(logging.Filter):
    def __init__(self, project_id: str | None = None) -> None:
        super().__init__()
        self.project_id = project_id

    def filter(self, record: logging.LogRecord) -> bool:
        # Don't clobber a project_id already set by a ProjectLogAdapter.
        if not hasattr(record, "project_id"):
            record.project_id = self.project_id
        return True


def install_project_log_filter(
    project_id: str | None = None,
    logger: logging.Logger | None = None,
) -> ProjectLogFilter:
    """Install a ProjectLogFilter on a logger (root by default), idempotently.

    Guarantees every emitted record carries a ``project_id`` attribute so log
    formatters can include it without each call site setting it. Returns the
    installed (or pre-existing) filter.
    """
    target = logger or logging.getLogger()
    for existing in target.filters:
        if isinstance(existing, ProjectLogFilter):
            return existing
    flt = ProjectLogFilter(project_id=project_id)
    target.addFilter(flt)
    return flt
