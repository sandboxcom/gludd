"""Read-only replay HTTP routes with a byte-compatible legacy listing."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import asdict
from typing import TypeVar, cast

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from general_ludd.replay.schema import validate_run_id
from general_ludd.replay.service import (
    ReplayAccessDeniedError,
    ReplayCursorError,
    ReplayExportError,
    ReplayNotFoundError,
    ReplayService,
    ReplayServiceError,
)

_MAX_PAGE_SIZE = 200
_MAX_CURSOR_LENGTH = 128
_MAX_PROJECT_ID_LENGTH = 128
_NOT_FOUND_DETAIL = "replay not found"
_INVALID_DETAIL = "invalid replay request"
_UNAVAILABLE_DETAIL = "replay unavailable"
_EXPORT_UNAVAILABLE_DETAIL = "replay export unavailable"
_T = TypeVar("_T")


def _service(
    app: FastAPI,
    daemon_state: dict[str, object],
) -> ReplayService:
    """Return the injected service without constructing storage in the router."""
    value = getattr(app.state, "_replay_service", None)
    if value is None:
        value = daemon_state.get("replay_service")
    if value is None:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
    return cast("ReplayService", value)


def _project_scope(request: Request, query_project_id: str | None) -> str | None:
    """Prefer the daemon-authenticated project claim over untrusted query input."""
    claimed = getattr(request.state, "project_id", None)
    if claimed is None:
        return query_project_id
    if not isinstance(claimed, str):
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
    return claimed


def _safe_run_id(run_id: str) -> str:
    """Validate a path identifier while keeping malformed and unknown IDs alike."""
    try:
        return validate_run_id(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL) from None


def _read_call(operation: Callable[[], _T]) -> _T:
    """Execute an individual read without leaking existence or backend details."""
    try:
        return operation()
    except (ReplayAccessDeniedError, ReplayNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL) from None
    except ReplayServiceError:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL) from None
    except Exception:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL) from None


def _export_call(operation: Callable[[], Iterator[bytes]]) -> Iterator[bytes]:
    """Start one export while translating pre-stream failures safely."""
    try:
        return operation()
    except (ReplayAccessDeniedError, ReplayNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL) from None
    except ReplayExportError:
        raise HTTPException(status_code=409, detail=_EXPORT_UNAVAILABLE_DETAIL) from None
    except ReplayServiceError:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL) from None
    except Exception:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL) from None


def _safe_stream(chunks: Iterator[bytes]) -> Iterator[bytes]:
    """Strip storage details from any failure that occurs after streaming starts."""
    try:
        yield from chunks
    except Exception:
        raise ReplayExportError(_EXPORT_UNAVAILABLE_DETAIL) from None


def register(app: FastAPI, daemon_state: dict[str, object]) -> None:
    """Register legacy and versioned replay routes on *app*."""

    @app.get("/api/replays")
    async def api_list_replays() -> list[str]:
        """Return the unchanged legacy list of recorder run identifiers."""
        recorder = getattr(app.state, "_run_recorder", None)
        if recorder is None:
            return []
        return cast("list[str]", recorder.list_runs())

    @app.get("/api/v1/replays")
    async def api_v1_list_replays(
        request: Request,
        project_id: str | None = Query(
            default=None,
            min_length=1,
            max_length=_MAX_PROJECT_ID_LENGTH,
        ),
        limit: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
        cursor: str | None = Query(
            default=None,
            min_length=1,
            max_length=_MAX_CURSOR_LENGTH,
        ),
    ) -> dict[str, object]:
        """Return one bounded, project-scoped page of safe replay summaries."""
        replay_service = _service(app, daemon_state)
        scope = _project_scope(request, project_id)
        try:
            page = replay_service.list_runs(
                project_id=scope,
                limit=limit,
                cursor=cursor,
            )
        except ReplayCursorError:
            raise HTTPException(status_code=400, detail=_INVALID_DETAIL) from None
        except ReplayAccessDeniedError:
            raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL) from None
        except ValueError:
            raise HTTPException(status_code=400, detail=_INVALID_DETAIL) from None
        except ReplayServiceError:
            raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL) from None
        except Exception:
            raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL) from None
        return cast("dict[str, object]", asdict(page))

    @app.post("/api/v1/replays/{run_id}/verify")
    async def api_v1_verify_replay(
        run_id: str,
        request: Request,
        project_id: str | None = Query(
            default=None,
            min_length=1,
            max_length=_MAX_PROJECT_ID_LENGTH,
        ),
    ) -> dict[str, object]:
        """Return a sanitized schema, completeness, digest, and HMAC verdict."""
        safe_run_id = _safe_run_id(run_id)
        scope = _project_scope(request, project_id)
        replay_service = _service(app, daemon_state)
        verdict = _read_call(
            lambda: replay_service.verify(safe_run_id, project_id=scope)
        )
        return cast("dict[str, object]", asdict(verdict))

    @app.get("/api/v1/replays/{run_id}/export")
    async def api_v1_export_replay(
        run_id: str,
        request: Request,
        project_id: str | None = Query(
            default=None,
            min_length=1,
            max_length=_MAX_PROJECT_ID_LENGTH,
        ),
    ) -> StreamingResponse:
        """Stream a verified ZIP and never accept or overwrite an output path."""
        safe_run_id = _safe_run_id(run_id)
        scope = _project_scope(request, project_id)
        replay_service = _service(app, daemon_state)
        chunks = _export_call(
            lambda: replay_service.stream_export(
                safe_run_id,
                project_id=scope,
            )
        )
        return StreamingResponse(
            _safe_stream(chunks),
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="gludd-replay-{safe_run_id}.zip"'
                ),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/v1/replays/{run_id}")
    async def api_v1_show_replay(
        run_id: str,
        request: Request,
        project_id: str | None = Query(
            default=None,
            min_length=1,
            max_length=_MAX_PROJECT_ID_LENGTH,
        ),
    ) -> dict[str, object]:
        """Return verified safe metadata without payload or attachment content."""
        safe_run_id = _safe_run_id(run_id)
        scope = _project_scope(request, project_id)
        replay_service = _service(app, daemon_state)
        detail = _read_call(
            lambda: replay_service.show(safe_run_id, project_id=scope)
        )
        return cast("dict[str, object]", asdict(detail))


__all__ = ["register"]
