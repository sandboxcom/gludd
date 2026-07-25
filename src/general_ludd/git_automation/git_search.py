"""Git history search — searches indexed git history.

Wraps :class:`general_ludd.history.git_indexer.GitHistoryIndexer` for use
within the git_automation collection.
"""

from __future__ import annotations

from general_ludd.history.git_indexer import GitHistoryIndexer


def search_git_history(
    query: str = "",
    since: str = "",
    author: str = "",
    path_filter: str = "",
    limit: int = 100,
    offset: int = 0,
    db_path: str = ".gludd/git_history.db",
    repo_path: str = ".",
) -> list[dict[str, object]]:
    indexer = GitHistoryIndexer(repo_path=repo_path, db_path=db_path)
    results = indexer.search(
        query=query,
        since=since,
        author=author,
        path_filter=path_filter,
        limit=limit,
        offset=offset,
    )
    return [r.to_dict() for r in results]
