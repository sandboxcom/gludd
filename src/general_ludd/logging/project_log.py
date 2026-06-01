from __future__ import annotations

import logging
from typing import Any


class ProjectLogAdapter(logging.LoggerAdapter):
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
        record.project_id = self.project_id  # type: ignore[attr-defined]
        return True
