"""Git history indexer — indexes git log into SQLite.

Wraps :class:`general_ludd.history.git_indexer.GitHistoryIndexer` for use
within the git_automation collection.
"""

from __future__ import annotations

from general_ludd.history.git_indexer import GitHistoryIndexer


def index_git_history(repo_path: str = ".", db_path: str = ".gludd/git_history.db") -> int:
    indexer = GitHistoryIndexer(repo_path=repo_path, db_path=db_path)
    return indexer.index()
