import general_ludd.git_automation.git_index as git_index


def test_index_git_history_delegates_paths(monkeypatch):
    calls = {}

    class Indexer:
        def __init__(self, **kwargs):
            calls.update(kwargs)

        def index(self):
            return 7

    monkeypatch.setattr(git_index, "GitHistoryIndexer", Indexer)
    assert git_index.index_git_history("/repo", "/db.sqlite") == 7
    assert calls == {"repo_path": "/repo", "db_path": "/db.sqlite"}
