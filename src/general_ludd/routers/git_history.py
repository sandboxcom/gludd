from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

from general_ludd.history.git_indexer import GitHistoryIndexer


def register(app: FastAPI, daemon_state: dict[str, object]) -> None:
    indexer = GitHistoryIndexer()

    @app.get("/api/git/history")
    async def search_history(
        q: str = Query(default="", description="Search commit messages and file paths"),
        since: str = Query(default="", description="Filter commits since date (YYYY-MM-DD or ISO)"),
        author: str = Query(default="", description="Filter by author (partial match)"),
        path: str = Query(default="", description="Filter by file path (partial match)"),
        limit: int = Query(default=100, ge=1, le=500, description="Max results"),
        offset: int = Query(default=0, ge=0, description="Pagination offset"),
    ) -> list[dict[str, object]]:
        try:
            results = indexer.search(
                query=q,
                since=since,
                author=author,
                path_filter=path,
                limit=limit,
                offset=offset,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Search failed: {exc}") from exc
        return [r.to_dict() for r in results]

    @app.get("/api/git/history/stats")
    async def history_stats() -> dict[str, object]:
        try:
            return indexer.stats()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Stats failed: {exc}") from exc

    @app.post("/api/git/history/reindex")
    async def reindex_history() -> dict[str, object]:
        try:
            count = indexer.index()
            return {"status": "ok", "indexed": count}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Reindex failed: {exc}") from exc
